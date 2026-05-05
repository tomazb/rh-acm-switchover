"""Release certification orchestration.

This module is intentionally test-owned but not fake-owned: default execution
uses live discovery and live stream adapters. Unit tests may inject fakes, and
those fakes make the run ineligible for certification.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tests.release.adapters.ansible import AnsibleAdapter
from tests.release.adapters.bash import BashAdapter
from tests.release.adapters.common import StreamAdapter
from tests.release.adapters.python_cli import PythonCliAdapter
from tests.release.baseline.assertions import assert_baseline
from tests.release.baseline.discovery import HubDiscoveryClient, discover_hub_facts
from tests.release.baseline.fingerprint import build_environment_fingerprint
from tests.release.checks.lab_readiness import assert_lab_readiness
from tests.release.checks.static_gates import (
    GateCommand,
    GateResult,
    build_default_gate_commands,
    run_gate_command,
)
from tests.release.conftest import ReleaseOptions
from tests.release.contracts.models import (
    LoadProfileResult,
    ScenarioProfile,
    StreamProfile,
)
from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.reporting.render import render_release_report
from tests.release.reporting.summary import build_summary
from tests.release.scenarios.catalog import ScenarioDefinition, select_release_matrix
from tests.release.scenarios.runtime_parity import (
    CAPABILITY_REQUIRED_FIELDS,
    compare_normalized_records,
    normalize_preflight,
    runtime_parity_not_applicable,
    write_runtime_parity_artifact,
)

GateRunner = Callable[[GateCommand, Path], GateResult]


class OcDiscoveryClient:
    """Minimal live Kubernetes discovery client backed by the current oc CLI."""

    test_only = False

    def __init__(self, *, kubeconfig: str, context: str) -> None:
        self.kubeconfig = kubeconfig
        self.context = context

    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]:
        command = [
            "oc",
            "--kubeconfig",
            self.kubeconfig,
            "--context",
            self.context,
            "get",
            resource,
            "-o",
            "json",
        ]
        if namespace:
            command.extend(["-n", namespace])
        try:
            completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return []
        items = payload.get("items", [])
        return items if isinstance(items, list) else []


def build_default_discovery_clients(
    release_profile: LoadProfileResult,
) -> dict[str, HubDiscoveryClient]:
    return {
        role: OcDiscoveryClient(kubeconfig=hub.kubeconfig, context=hub.context)
        for role, hub in release_profile.profile.hubs.items()
    }


def build_default_adapters(
    *, release_profile: LoadProfileResult, artifact_dir: Path, repo_root: Path
) -> dict[str, StreamAdapter]:
    primary = release_profile.profile.hubs["primary"]
    secondary = release_profile.profile.hubs["secondary"]
    return {
        "bash": BashAdapter(
            repo_root=repo_root,
            primary_context=primary.context,
            secondary_context=secondary.context,
            primary_kubeconfig=primary.kubeconfig,
            secondary_kubeconfig=secondary.kubeconfig,
            artifact_dir=artifact_dir,
        ),
        "python": PythonCliAdapter(
            repo_root=repo_root,
            primary_context=primary.context,
            secondary_context=secondary.context,
            primary_kubeconfig=primary.kubeconfig,
            secondary_kubeconfig=secondary.kubeconfig,
            artifact_dir=artifact_dir,
        ),
        "ansible": AnsibleAdapter(
            repo_root=repo_root,
            collection_root=repo_root / "ansible_collections/tomazb/acm_switchover",
            primary_context=primary.context,
            secondary_context=secondary.context,
            primary_kubeconfig=primary.kubeconfig,
            secondary_kubeconfig=secondary.kubeconfig,
            artifact_dir=artifact_dir,
        ),
    }


def _certification_eligible(
    *,
    release_options: ReleaseOptions,
    discovery_clients: Mapping[str, HubDiscoveryClient],
    adapters: Mapping[str, StreamAdapter],
) -> bool:
    if release_options.mode != "certification":
        return False
    participants: list[Any] = [*discovery_clients.values(), *adapters.values()]
    return not any(bool(getattr(item, "test_only", False)) for item in participants)


def _scenario_profiles(
    profile_scenarios: tuple[ScenarioProfile, ...],
) -> dict[str, ScenarioProfile]:
    return {scenario.id: scenario for scenario in profile_scenarios}


def _stream_profiles(
    profile_streams: tuple[StreamProfile, ...],
) -> dict[str, StreamProfile]:
    return {stream.id: stream for stream in profile_streams}


def _as_dict(value: Any) -> dict:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return dict(value)


def _local_result(scenario_id: str, status: str, assertions: list[dict], required: bool) -> dict:
    return {
        "stream": "local",
        "scenario_id": scenario_id,
        "status": status,
        "required": required,
        "assertions": assertions,
    }


def _aggregate_status(scenario: ScenarioDefinition, results: list[dict]) -> dict:
    scenario_results = [result for result in results if result["scenario_id"] == scenario.id]
    if not scenario_results:
        status = "not_applicable"
    elif all(result.get("status") in {"passed", "not_applicable"} for result in scenario_results):
        status = "passed"
    else:
        status = "failed"
    return {
        "scenario_id": scenario.id,
        "status": status,
        "required": scenario.required,
        "streams": list(scenario.streams),
    }


def _discover_fingerprint(
    *,
    release_profile: LoadProfileResult,
    discovery_clients: Mapping[str, HubDiscoveryClient],
    lab_readiness_status: str,
) -> dict:
    profile = release_profile.profile
    primary = discover_hub_facts(
        client=discovery_clients["primary"],
        context=profile.hubs["primary"].context,
        acm_namespace=profile.hubs["primary"].acm_namespace,
        argocd_namespaces=profile.argocd.namespaces,
    )
    secondary = discover_hub_facts(
        client=discovery_clients["secondary"],
        context=profile.hubs["secondary"].context,
        acm_namespace=profile.hubs["secondary"].acm_namespace,
        argocd_namespaces=profile.argocd.namespaces,
    )
    return build_environment_fingerprint(
        primary=primary,
        secondary=secondary,
        expected_names=profile.managed_clusters.expected_names,
        expected_count=profile.managed_clusters.expected_count,
        lab_readiness_status=lab_readiness_status,
    )


def _run_static_gates(
    *,
    release_profile: LoadProfileResult,
    repo_root: Path,
    artifacts: ReleaseArtifacts,
    selected_streams: tuple[str, ...],
    gate_runner: GateRunner,
) -> tuple[str, list[dict]]:
    commands = build_default_gate_commands(enabled_streams=selected_streams, repo_root=repo_root)
    optional = set(release_profile.profile.baseline.static_gates.optional_gate_ids)
    results = []
    for command in commands:
        gate = GateCommand(
            gate_id=command.gate_id,
            label=command.label,
            command=command.command,
            cwd=command.cwd,
            required=command.gate_id not in optional,
        )
        result = gate_runner(gate, artifacts.run_dir / "static-gates")
        results.append(asdict(result))
    status = "passed" if all(item["status"] == "passed" or not item["required"] for item in results) else "failed"
    artifacts.write_json("static-gates.json", {"schema_version": 1, "status": status, "results": results})
    return status, results


def _execute_stream_scenarios(
    *,
    scenarios: tuple[ScenarioDefinition, ...],
    scenario_profiles: Mapping[str, ScenarioProfile],
    stream_profiles: Mapping[str, StreamProfile],
    adapters: Mapping[str, StreamAdapter],
) -> list[dict]:
    results: list[dict] = []
    for scenario in scenarios:
        for stream in scenario.streams:
            if stream == "local":
                continue
            adapter = adapters.get(stream)
            if adapter is None:
                results.append(
                    {
                        "stream": stream,
                        "scenario_id": scenario.id,
                        "status": "failed",
                        "required": scenario.required,
                        "assertions": [
                            {
                                "capability": scenario.id,
                                "name": "adapter-present",
                                "status": "failed",
                                "message": f"{stream} adapter is not configured",
                            }
                        ],
                    }
                )
                continue
            scenario_profile = scenario_profiles.get(scenario.id)
            stream_profile = stream_profiles.get(stream)
            timeout = (
                scenario_profile.timeout_minutes * 60 if scenario_profile and scenario_profile.timeout_minutes else None
            )
            try:
                result = adapter.execute(
                    scenario.id,
                    timeout_seconds=timeout,
                    env=dict(stream_profile.env) if stream_profile else {},
                    extra_args=stream_profile.extra_args if stream_profile else (),
                )
            except Exception as exc:
                results.append(
                    {
                        "stream": stream,
                        "scenario_id": scenario.id,
                        "status": "failed",
                        "required": scenario.required,
                        "assertions": [
                            {
                                "capability": scenario.id,
                                "name": "adapter-execution",
                                "status": "failed",
                                "message": f"{stream} adapter raised {type(exc).__name__}: {exc}",
                            }
                        ],
                    }
                )
                continue
            payload = _as_dict(result)
            payload["required"] = scenario.required
            results.append(payload)
    return results


def _load_report(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _normalized_runtime_sources(results: list[dict]) -> dict[str, dict[str, dict]]:
    sources: dict[str, dict[str, dict]] = {}
    for result in results:
        for report in result.get("reports", []):
            report_type = report.get("type")
            payload = _load_report(report.get("path", ""))
            if not payload:
                continue
            if report_type == "preflight":
                sources.setdefault("preflight validation", {})[result["stream"]] = normalize_preflight(payload)
    return sources


def _runtime_parity(artifacts: ReleaseArtifacts, results: list[dict]) -> dict:
    comparisons = []
    sources = _normalized_runtime_sources(results)
    for capability, required_fields in CAPABILITY_REQUIRED_FIELDS.items():
        by_stream = sources.get(capability, {})
        if {"python", "ansible"}.issubset(by_stream):
            comparisons.append(
                compare_normalized_records(
                    capability=capability,
                    scenario_id="runtime-parity",
                    python=by_stream["python"],
                    ansible=by_stream["ansible"],
                    required_fields=required_fields,
                )
            )
        else:
            comparisons.append(runtime_parity_not_applicable(capability, "runtime-parity", "missing source reports"))
    return write_runtime_parity_artifact(artifacts=artifacts, comparisons=comparisons)


def _read_redaction_state(artifacts: ReleaseArtifacts) -> dict:
    try:
        return json.loads((artifacts.run_dir / "redaction.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": 1,
            "status": "failed",
            "rejected_artifacts": ["redaction.json"],
            "warnings": ["redaction audit state is missing or invalid"],
        }


def _finalize_run(
    *,
    artifacts: ReleaseArtifacts,
    release_options: ReleaseOptions,
    matrix,
    manifest: dict,
    certification_eligible: bool,
    results: list[dict],
    runtime_parity: dict,
    final_baseline: dict,
    mandatory_argocd: dict,
) -> dict:
    scenario_statuses = [_aggregate_status(scenario, results) for scenario in matrix.scenarios]
    artifacts.write_json(
        "scenario-results.json",
        {
            "schema_version": 1,
            "results": results,
            "scenario_statuses": scenario_statuses,
        },
    )
    required_scenarios = [item for item in scenario_statuses if item["required"]]
    optional_scenarios = [item for item in scenario_statuses if not item["required"]]
    redaction = _read_redaction_state(artifacts)
    artifact_redaction = {
        "status": "failed" if redaction.get("rejected_artifacts") else "passed",
        "rejected_artifacts": redaction.get("rejected_artifacts", []),
    }
    summary = build_summary(
        release_mode=release_options.mode or "certification",
        certification_eligible=certification_eligible,
        required_scenarios=required_scenarios,
        optional_scenarios=optional_scenarios,
        runtime_parity=runtime_parity,
        artifact_redaction=artifact_redaction,
        final_baseline=final_baseline,
        recovery={"status": "not_applicable", "hard_stops": []},
        mandatory_argocd=mandatory_argocd,
        release_metadata={"status": "passed"},
    )
    artifacts.write_json("summary.json", summary)
    final_manifest = {
        **manifest,
        "status": summary["status"],
        "certification_eligible": summary["certification_eligible"],
        "warnings": summary["warnings"],
        "failure_reasons": summary["failure_reasons"],
    }
    artifacts.write_json("manifest.json", final_manifest)
    (artifacts.run_dir / "release-report.md").write_text(
        render_release_report(summary, final_manifest),
        encoding="utf-8",
    )
    return summary


def _not_applicable_artifact(status: str = "not_applicable") -> dict:
    return {"schema_version": 1, "status": status, "comparisons": []}


def _run_release_certification(
    *,
    release_options: ReleaseOptions,
    release_profile: LoadProfileResult,
    artifacts: ReleaseArtifacts,
    repo_root: Path,
    discovery_clients: Mapping[str, HubDiscoveryClient] | None = None,
    adapters: Mapping[str, StreamAdapter] | None = None,
    gate_runner: GateRunner = run_gate_command,
) -> dict:
    profile = release_profile.profile
    enabled_streams = tuple(stream.id for stream in profile.streams if stream.enabled)
    matrix = select_release_matrix(
        enabled_streams=enabled_streams,
        scenario_filters=release_options.scenarios,
        stream_filters=release_options.streams,
        profile_scenarios=profile.scenarios,
    )
    discovery_clients = discovery_clients or build_default_discovery_clients(release_profile)
    adapters = adapters or build_default_adapters(
        release_profile=release_profile,
        artifact_dir=artifacts.run_dir,
        repo_root=repo_root,
    )
    certification_eligible = _certification_eligible(
        release_options=release_options,
        discovery_clients=discovery_clients,
        adapters=adapters,
    )

    manifest = {
        "schema_version": 1,
        "run_id": artifacts.run_id,
        "status": "running",
        "profile": {
            "name": profile.name,
            "path": str(release_profile.path),
            "sha256": release_profile.sha256,
        },
        "matrix": {
            "scenario_ids": list(matrix.scenario_ids),
            "hash": matrix.matrix_hash,
        },
        "certification_eligible": certification_eligible,
        "warnings": [],
        "failure_reasons": [],
    }
    artifacts.write_json("manifest.json", manifest)

    results: list[dict] = []
    scenario_profiles = _scenario_profiles(profile.scenarios)
    stream_profiles = _stream_profiles(profile.streams)
    scenarios_by_id = {scenario.id: scenario for scenario in matrix.scenarios}

    if "static-gates" in scenarios_by_id:
        status, gate_results = _run_static_gates(
            release_profile=release_profile,
            repo_root=repo_root,
            artifacts=artifacts,
            selected_streams=matrix.selected_streams,
            gate_runner=gate_runner,
        )
        results.append(
            _local_result(
                "static-gates",
                status,
                gate_results,
                scenarios_by_id["static-gates"].required,
            )
        )
        if status == "failed" and scenarios_by_id["static-gates"].required:
            runtime_parity = _not_applicable_artifact()
            artifacts.write_json("runtime-parity.json", runtime_parity)
            final_baseline = {"status": "not_applicable", "assertions": []}
            artifacts.write_json("final-baseline.json", {"schema_version": 1, **final_baseline})
            return _finalize_run(
                artifacts=artifacts,
                release_options=release_options,
                matrix=matrix,
                manifest=manifest,
                certification_eligible=certification_eligible,
                results=results,
                runtime_parity=runtime_parity,
                final_baseline=final_baseline,
                mandatory_argocd=({"status": "not_applicable"} if profile.argocd.mandatory else {"status": "passed"}),
            )

    initial_fingerprint = _discover_fingerprint(
        release_profile=release_profile,
        discovery_clients=discovery_clients,
        lab_readiness_status="unknown",
    )
    lab_readiness = assert_lab_readiness(
        fingerprint=initial_fingerprint,
        require_argocd=profile.baseline.lab_readiness.argocd_fixture.required,
        require_backup_storage=profile.baseline.lab_readiness.backup_storage_location.required,
    )
    initial_fingerprint = _discover_fingerprint(
        release_profile=release_profile,
        discovery_clients=discovery_clients,
        lab_readiness_status=lab_readiness.status,
    )
    artifacts.write_json("environment-fingerprint-initial.json", initial_fingerprint)

    if "lab-readiness" in scenarios_by_id:
        results.append(
            _local_result(
                "lab-readiness",
                lab_readiness.status,
                lab_readiness.assertions,
                scenarios_by_id["lab-readiness"].required,
            )
        )

    initial_baseline = assert_baseline(
        fingerprint=initial_fingerprint,
        initial_primary=profile.baseline.initial_primary,
    )
    if "baseline-check" in scenarios_by_id:
        results.append(
            _local_result(
                "baseline-check",
                initial_baseline.status,
                initial_baseline.assertions,
                scenarios_by_id["baseline-check"].required,
            )
        )

    results.extend(
        _execute_stream_scenarios(
            scenarios=matrix.scenarios,
            scenario_profiles=scenario_profiles,
            stream_profiles=stream_profiles,
            adapters=adapters,
        )
    )

    runtime_parity = (
        _runtime_parity(artifacts, results)
        if "runtime-parity" in scenarios_by_id
        else {"schema_version": 1, "status": "not_applicable", "comparisons": []}
    )

    final_fingerprint = _discover_fingerprint(
        release_profile=release_profile,
        discovery_clients=discovery_clients,
        lab_readiness_status=lab_readiness.status,
    )
    artifacts.write_json("environment-fingerprint-final.json", final_fingerprint)
    final_baseline_result = assert_baseline(
        fingerprint=final_fingerprint, initial_primary=profile.baseline.final_primary
    )
    final_baseline = {
        "status": final_baseline_result.status,
        "assertions": final_baseline_result.assertions,
    }
    artifacts.write_json("final-baseline.json", {"schema_version": 1, **final_baseline})

    if "final-baseline-check" in scenarios_by_id:
        results.append(
            _local_result(
                "final-baseline-check",
                final_baseline_result.status,
                final_baseline_result.assertions,
                scenarios_by_id["final-baseline-check"].required,
            )
        )

    return _finalize_run(
        artifacts=artifacts,
        release_options=release_options,
        matrix=matrix,
        manifest=manifest,
        certification_eligible=certification_eligible,
        results=results,
        runtime_parity=runtime_parity,
        final_baseline=final_baseline,
        mandatory_argocd={"status": "passed" if not profile.argocd.mandatory else lab_readiness.status},
    )


def run_release_certification(
    *,
    release_options: ReleaseOptions,
    release_profile: LoadProfileResult,
    artifacts: ReleaseArtifacts,
    repo_root: Path,
    discovery_clients: Mapping[str, HubDiscoveryClient] | None = None,
    adapters: Mapping[str, StreamAdapter] | None = None,
    gate_runner: GateRunner = run_gate_command,
) -> dict:
    try:
        return _run_release_certification(
            release_options=release_options,
            release_profile=release_profile,
            artifacts=artifacts,
            repo_root=repo_root,
            discovery_clients=discovery_clients,
            adapters=adapters,
            gate_runner=gate_runner,
        )
    except Exception as exc:
        reason = f"release certification failed: {type(exc).__name__}: {exc}"
        artifacts.write_failed_manifest(
            reason=reason,
            command=["pytest", "tests/release/test_release_certification.py"],
        )
        return json.loads((artifacts.run_dir / "summary.json").read_text(encoding="utf-8"))
