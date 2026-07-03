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

from lib.constants import (
    ARGOCD_APPLICATIONS_RESOURCE,
    ARGOCD_PAUSED_BY_ANNOTATION,
    RESUME_START_PHASE_KEY,
    STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES,
    STATE_KEY_ARGOCD_RUN_ID,
    STATE_KEY_RESUME_SUMMARY,
)
from tests.release.adapters.ansible import SUPPORTED_SCENARIO_IDS as ANSIBLE_SUPPORTED_SCENARIO_IDS
from tests.release.adapters.ansible import AnsibleAdapter
from tests.release.adapters.bash import SUPPORTED_SCENARIO_IDS as BASH_SUPPORTED_SCENARIO_IDS
from tests.release.adapters.bash import BashAdapter
from tests.release.adapters.common import StreamAdapter
from tests.release.adapters.python_cli import SUPPORTED_SCENARIO_IDS as PYTHON_SUPPORTED_SCENARIO_IDS
from tests.release.adapters.python_cli import PythonCliAdapter
from tests.release.baseline.assertions import assert_baseline
from tests.release.baseline.discovery import HubDiscoveryClient, discover_hub_facts
from tests.release.baseline.fingerprint import build_environment_fingerprint
from tests.release.checks.lab_readiness import assert_lab_readiness
from tests.release.checks.metadata import validate_release_metadata
from tests.release.checks.rbac_certification import certify_rbac_permissions
from tests.release.checks.static_gates import (
    GateCommand,
    GateResult,
    build_default_gate_commands,
    run_gate_command,
)
from tests.release.conftest import ReleaseOptions
from tests.release.contracts.models import (
    LoadProfileResult,
    RBACCertificationHubProfile,
    ScenarioProfile,
    StreamProfile,
)
from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.reporting.render import render_release_report
from tests.release.reporting.summary import build_summary
from tests.release.scenarios.catalog import (
    ScenarioDefinition,
    matrix_validation_results,
    select_release_matrix,
    validate_release_matrix,
)
from tests.release.scenarios.runtime_parity import (
    CAPABILITY_REQUIRED_FIELDS,
    ComparisonRecord,
    compare_normalized_records,
    normalize_argocd_management,
    normalize_checkpoint_artifact,
    normalize_decommission_artifact,
    normalize_operation_artifact,
    normalize_preflight,
    normalize_rbac_bootstrap_artifact,
    normalize_report_artifact,
    runtime_parity_not_applicable,
    write_runtime_parity_artifact,
)

GateRunner = Callable[[GateCommand, Path], GateResult]
DEFAULT_ADAPTER_SUPPORTED_SCENARIOS = {
    "bash": BASH_SUPPORTED_SCENARIO_IDS,
    "python": PYTHON_SUPPORTED_SCENARIO_IDS,
    "ansible": ANSIBLE_SUPPORTED_SCENARIO_IDS,
}


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
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                f"discovery failed for {resource} on {self.context}: {type(exc).__name__}: {exc}"
            ) from exc
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip() or "no stderr"
            raise RuntimeError(
                f"discovery failed for {resource} on {self.context}: oc exited {completed.returncode}: {stderr}"
            )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"discovery failed for {resource} on {self.context}: invalid JSON: {exc}") from exc
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError(f"discovery failed for {resource} on {self.context}: response missing items list")
        return items


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


def _adapter_supported_scenarios(adapters: Mapping[str, StreamAdapter]) -> dict[str, frozenset[str]]:
    supported = {}
    for stream, default_supported in DEFAULT_ADAPTER_SUPPORTED_SCENARIOS.items():
        adapter = adapters.get(stream)
        if adapter is None:
            supported[stream] = frozenset()
            continue
        supported[stream] = frozenset(getattr(adapter, "supported_scenario_ids", default_supported))
    return supported


def _certification_eligible(
    *,
    release_options: ReleaseOptions,
    discovery_clients: Mapping[str, HubDiscoveryClient],
    adapters: Mapping[str, StreamAdapter],
    git_checkout: Mapping[str, Any],
    release_metadata: Mapping[str, Any],
) -> bool:
    if release_options.mode != "certification":
        return False
    if not bool(git_checkout.get("available")) or bool(git_checkout.get("dirty")):
        return False
    if release_metadata.get("status") != "passed":
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


def _rbac_certification_scope(
    scenario_profiles: Mapping[str, ScenarioProfile],
    hub_name: str,
) -> RBACCertificationHubProfile:
    scenario_profile = scenario_profiles.get("rbac-bootstrap-live")
    if scenario_profile is None or scenario_profile.rbac_certification is None:
        return RBACCertificationHubProfile()
    if hub_name == "primary":
        return scenario_profile.rbac_certification.primary
    return scenario_profile.rbac_certification.secondary


def _certify_hub_rbac(
    *,
    hub,
    hub_name: str,
    scenario_profiles: Mapping[str, ScenarioProfile],
    rbac_cert_dir: Path,
) -> tuple[CertificationResult, list[dict]]:
    """Certify one hub's RBAC scope and return its result plus prefixed assertion dicts."""
    scope = _rbac_certification_scope(scenario_profiles, hub_name)
    result = certify_rbac_permissions(
        hub=hub,
        hub_name=hub_name,
        artifact_dir=rbac_cert_dir / hub_name,
        role=scope.role,
        namespace=scope.namespace,
        service_account=scope.service_account,
        include_decommission=scope.include_decommission,
        include_old_hub_finalization=scope.include_old_hub_finalization,
        include_forbidden_permissions=scope.include_forbidden_permissions,
    )
    assertions = [
        {
            "capability": a.capability,
            "name": f"{hub_name}:{a.name}",
            "status": a.status,
            "expected": a.expected,
            "actual": a.actual,
            "evidence_path": a.evidence_path,
            "message": a.message,
        }
        for a in result.assertions
    ]
    return result, assertions


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
        status = "failed" if scenario.required else "not_applicable"
    elif scenario.required:
        status = "passed" if all(result.get("status") == "passed" for result in scenario_results) else "failed"
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
    skipped_pairs: set[tuple[str, str]] | None = None,
) -> list[dict]:
    results: list[dict] = []
    skipped_pairs = skipped_pairs or set()
    for scenario in scenarios:
        for stream in scenario.streams:
            if stream == "local":
                continue
            if (scenario.id, stream) in skipped_pairs:
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


def _result_artifact_dir(result: dict) -> Path | None:
    for key in ("stdout_path", "stderr_path"):
        if result.get(key):
            return Path(result[key]).parent
    for report in _result_reports(result):
        if report.get("path"):
            return Path(report["path"]).parent
    return None


def _result_reports(result: dict) -> list[dict]:
    reports = result.get("reports")
    if not isinstance(reports, list):
        return []
    return [report for report in reports if isinstance(report, dict)]


def _report_metadata(result: dict, report_type: str) -> dict | None:
    for report in _result_reports(result):
        if report.get("type") == report_type:
            return report
    return None


def _state_or_checkpoint_payload(result: dict) -> dict | None:
    artifact_dir = _result_artifact_dir(result)
    if artifact_dir is None:
        return None
    for filename in ("state.json", "checkpoint.json"):
        payload = _load_report(str(artifact_dir / filename))
        if isinstance(payload, dict):
            return payload
    return None


def _namespace_hints_by_hub(config: dict, operational_data: dict) -> dict[str, list[str]]:
    namespace_hints = config.get(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES)
    if not isinstance(namespace_hints, dict):
        namespace_hints = operational_data.get(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES)
    if not isinstance(namespace_hints, dict):
        return {}
    return {
        str(hub_name): [str(namespace) for namespace in namespaces if namespace]
        for hub_name, namespaces in namespace_hints.items()
        if isinstance(namespaces, list)
    }


def _argocd_retry_state(*, report_run_id: str | None, persisted_run_id: str | None, resume_summary: dict) -> str:
    if not resume_summary.get(RESUME_START_PHASE_KEY):
        return "not_applicable"
    if not report_run_id or not persisted_run_id:
        return "missing"
    return "preserved" if report_run_id == persisted_run_id else "mismatch"


def _argocd_pause_names(
    *,
    discovery_clients: Mapping[str, HubDiscoveryClient],
    run_id: str | None,
    namespaces_by_hub: dict[str, list[str]],
) -> list[str]:
    if not run_id:
        return []
    names: list[str] = []
    for hub_name, client in discovery_clients.items():
        namespaces = namespaces_by_hub.get(hub_name)
        if namespaces is None:
            namespaces = [None]
        elif not namespaces:
            continue
        for namespace in namespaces:
            for item in client.list_resources(ARGOCD_APPLICATIONS_RESOURCE, namespace):
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                annotations = metadata.get("annotations")
                if not isinstance(annotations, dict):
                    annotations = {}
                if annotations.get(ARGOCD_PAUSED_BY_ANNOTATION) == run_id:
                    item_namespace = metadata.get("namespace", namespace)
                    item_name = metadata.get("name")
                    if item_name:
                        names.append(f"{hub_name}:{item_namespace}/{item_name}")
    return sorted(set(names))


def _add_argocd_source(
    sources: dict[str, dict[str, dict]],
    result: dict,
    payload: dict,
    *,
    discovery_clients: Mapping[str, HubDiscoveryClient],
) -> None:
    if result.get("scenario_id") != "argocd-managed-switchover" or result.get("stream") not in {"python", "ansible"}:
        return
    state_payload = _state_or_checkpoint_payload(result) or {}
    config = state_payload.get("config") if isinstance(state_payload.get("config"), dict) else {}
    operational_data = (
        state_payload.get("operational_data") if isinstance(state_payload.get("operational_data"), dict) else {}
    )
    resume_summary = config.get(STATE_KEY_RESUME_SUMMARY)
    if not isinstance(resume_summary, dict):
        resume_summary = operational_data.get(STATE_KEY_RESUME_SUMMARY)
    if not isinstance(resume_summary, dict):
        resume_summary = {}
    argocd = payload.get("argocd") if isinstance(payload.get("argocd"), dict) else {}
    report_run_id = argocd.get("run_id")
    persisted_run_id = config.get(STATE_KEY_ARGOCD_RUN_ID) or operational_data.get(STATE_KEY_ARGOCD_RUN_ID)
    namespaces_by_hub = _namespace_hints_by_hub(config, operational_data)
    sources.setdefault("Argo CD management", {})[result["stream"]] = normalize_argocd_management(
        {
            "run_id": report_run_id or persisted_run_id,
            "paused_application_names": _argocd_pause_names(
                discovery_clients=discovery_clients,
                run_id=report_run_id or persisted_run_id,
                namespaces_by_hub=namespaces_by_hub,
            ),
            "run_id_preserved_for_retry": _argocd_retry_state(
                report_run_id=report_run_id,
                persisted_run_id=persisted_run_id,
                resume_summary=resume_summary,
            ),
        }
    )


def _add_checkpoint_source(sources: dict[str, dict[str, dict]], result: dict) -> None:
    artifact_dir = _result_artifact_dir(result)
    if artifact_dir is None:
        return
    for filename in ("checkpoint.json", "state.json"):
        payload = _load_report(str(artifact_dir / filename))
        if isinstance(payload, dict):
            sources.setdefault("checkpoints", {})[result["stream"]] = normalize_checkpoint_artifact(
                payload,
                scenario_id=result.get("scenario_id"),
            )
            return


def _normalized_runtime_sources(
    results: list[dict],
    *,
    discovery_clients: Mapping[str, HubDiscoveryClient] | None = None,
) -> dict[str, dict[str, dict]]:
    sources: dict[str, dict[str, dict]] = {}
    discovery_clients = discovery_clients or {}
    for result in results:
        if result.get("stream") in {"python", "ansible"}:
            _add_checkpoint_source(sources, result)
        for report in _result_reports(result):
            report_type = report.get("type")
            report_path = report.get("path", "")
            payload = _load_report(report_path)
            if not isinstance(payload, dict):
                continue
            filename = Path(report_path).name
            if report_type == "preflight":
                sources.setdefault("preflight validation", {})[result["stream"]] = normalize_preflight(payload)
            if report_type == "switchover":
                _add_argocd_source(
                    sources,
                    result,
                    payload,
                    discovery_clients=discovery_clients,
                )
                sources.setdefault("switchover artifacts", {})[result["stream"]] = normalize_operation_artifact(
                    payload, filename
                )
            if report_type == "restore":
                sources.setdefault("restore-only artifacts", {})[result["stream"]] = normalize_operation_artifact(
                    payload, filename
                )
            if report_type == "decommission":
                sources.setdefault("decommission artifacts", {})[result["stream"]] = normalize_decommission_artifact(
                    payload, filename
                )
            if report_type == "rbac-bootstrap":
                sources.setdefault("RBAC/bootstrap artifacts", {})[result["stream"]] = (
                    normalize_rbac_bootstrap_artifact(payload, filename)
                )
            if report_type in {"preflight", "switchover", "restore", "decommission", "rbac-bootstrap"}:
                sources.setdefault("report artifacts", {}).setdefault(
                    result["stream"], normalize_report_artifact(payload, report_path)
                )
    return sources


def _rbac_live_consistency_record(results: list[dict]) -> ComparisonRecord | None:
    bootstrap = next(
        (item for item in results if item.get("scenario_id") == "rbac-bootstrap" and item.get("stream") == "ansible"),
        None,
    )
    live = next(
        (
            item
            for item in results
            if item.get("scenario_id") == "rbac-bootstrap-live" and item.get("stream") == "local"
        ),
        None,
    )
    if bootstrap is None or live is None:
        return None

    bootstrap_report = _report_metadata(bootstrap, "rbac-bootstrap") or {}
    bootstrap_payload = _load_report(bootstrap_report.get("path", "")) if bootstrap_report else None
    if not isinstance(bootstrap_payload, dict):
        bootstrap_payload = {}
    bootstrap_status = str(bootstrap_payload.get("status", bootstrap.get("status", "unknown")))
    live_status = str(live.get("status", "unknown"))

    if live_status in {"skipped", "not_applicable"}:
        record_status = "not_applicable"
        differences: list[dict[str, Any]] = []
    else:
        known_bootstrap_statuses = {"pass", "passed", "fail", "failed"}
        if bootstrap_status not in known_bootstrap_statuses:
            differences = [{"field": "live_status", "ansible": bootstrap_status, "local": live_status}]
        else:
            bootstrap_succeeded = bootstrap_status in {"pass", "passed"}
            live_succeeded = live_status == "passed"
            differences = (
                []
                if bootstrap_succeeded == live_succeeded
                else [{"field": "live_status", "ansible": bootstrap_status, "local": live_status}]
            )
        record_status = "passed" if not differences else "failed"

    evidence_paths = tuple(path for path in [bootstrap_report.get("path")] if path)
    return ComparisonRecord(
        capability="RBAC live consistency",
        scenario_id="rbac-bootstrap-live",
        streams=("ansible", "local"),
        status=record_status,
        required_fields=("bootstrap_status", "live_status"),
        differences=differences,
        evidence_paths=evidence_paths,
    )


def _runtime_parity(
    artifacts: ReleaseArtifacts,
    results: list[dict],
    *,
    discovery_clients: Mapping[str, HubDiscoveryClient] | None = None,
) -> dict:
    comparisons = []
    sources = _normalized_runtime_sources(results, discovery_clients=discovery_clients)
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
    live_consistency = _rbac_live_consistency_record(results)
    if live_consistency is not None:
        comparisons.append(live_consistency)
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


def _git_checkout_state(repo_root: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "available": False,
        "dirty": False,
        "allow_dirty": False,
        "commit": None,
        "warnings": [],
    }
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if commit.returncode != 0:
            state["warnings"].append("git commit metadata is unavailable")
            return state
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if status.returncode != 0:
            state["warnings"].append("git dirty-state metadata is unavailable")
            return state
    except (OSError, subprocess.SubprocessError) as exc:
        state["warnings"].append(f"git checkout metadata is unavailable: {type(exc).__name__}: {exc}")
        return state
    state["available"] = True
    state["commit"] = (commit.stdout or "").strip() or None
    state["dirty"] = bool((status.stdout or "").strip())
    return state


def _release_metadata_state(*, repo_root: Path, release_profile: LoadProfileResult, matrix_hash: str) -> dict[str, Any]:
    release = release_profile.profile.release
    if release is None or not release.metadata_files:
        return {
            "status": "passed",
            "hash": None,
            "expected_version": release.expected_version if release is not None else None,
            "files": [],
            "failure_reasons": [],
        }
    return validate_release_metadata(
        repo_root=repo_root,
        metadata_files=release.metadata_files,
        expected_version=release.expected_version,
        profile_hash=release_profile.sha256,
        matrix_hash=matrix_hash,
    )


def _initial_recovery_state(release_profile: LoadProfileResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "budget_minutes": release_profile.profile.recovery.total_budget_minutes,
        "budget_consumed_seconds": 0,
        "pre_run": [],
        "post_failure": [],
        "hard_stops": [],
        "status": "not_applicable",
    }


def _stop_before_mutation(
    *,
    scenarios_by_id: Mapping[str, ScenarioDefinition],
    lab_readiness_status: str,
    initial_baseline_status: str,
) -> bool:
    failed_required_lab = (
        "lab-readiness" in scenarios_by_id
        and scenarios_by_id["lab-readiness"].required
        and lab_readiness_status != "passed"
    )
    failed_required_baseline = (
        "baseline-check" in scenarios_by_id
        and scenarios_by_id["baseline-check"].required
        and initial_baseline_status != "passed"
    )
    return failed_required_lab or failed_required_baseline


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
    recovery: dict,
    mandatory_argocd: dict,
    release_metadata: dict,
    matrix_validation: dict | None = None,
) -> dict:
    if matrix_validation is None:
        matrix_validation = {"schema_version": 1, "status": "passed", "blocked": False, "reasons": []}
    scenario_statuses = [_aggregate_status(scenario, results) for scenario in matrix.scenarios]
    artifacts.write_json(
        "scenario-results.json",
        {
            "schema_version": 1,
            "results": results,
            "scenario_statuses": scenario_statuses,
            "matrix_validation": matrix_validation,
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
        recovery=recovery,
        mandatory_argocd=mandatory_argocd,
        release_metadata=release_metadata,
        matrix_validation=matrix_validation,
    )
    artifacts.write_json("summary.json", summary)
    final_manifest = {
        **manifest,
        "status": summary["status"],
        "certification_eligible": summary["certification_eligible"],
        "warnings": summary["warnings"],
        "failure_reasons": summary["failure_reasons"],
        "matrix": {
            **manifest.get("matrix", {}),
            "validation": matrix_validation,
        },
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
    git_checkout = _git_checkout_state(repo_root)
    git_checkout["allow_dirty"] = release_options.allow_dirty
    if git_checkout.get("dirty") and not release_options.allow_dirty:
        raise RuntimeError("git checkout is dirty; rerun with --allow-dirty")
    release_metadata = _release_metadata_state(
        repo_root=repo_root,
        release_profile=release_profile,
        matrix_hash=matrix.matrix_hash,
    )
    recovery = _initial_recovery_state(release_profile)
    artifacts.write_json("recovery.json", recovery)
    adapters = adapters or build_default_adapters(
        release_profile=release_profile,
        artifact_dir=artifacts.run_dir,
        repo_root=repo_root,
    )
    matrix_validation_result = validate_release_matrix(
        matrix=matrix,
        release_mode=release_options.mode or "certification",
        scenario_filters=release_options.scenarios,
        adapter_supported_scenarios=_adapter_supported_scenarios(adapters),
    )
    matrix_validation = matrix_validation_result.to_dict()

    def build_manifest(certification_eligible: bool) -> dict:
        return {
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
                "validation": matrix_validation,
            },
            "git": git_checkout,
            "release_metadata": release_metadata,
            "certification_eligible": certification_eligible,
            "warnings": [],
            "failure_reasons": [],
        }

    if matrix_validation_result.blocked:
        certification_eligible = False
        manifest = build_manifest(certification_eligible)
        artifacts.write_json("manifest.json", manifest)
        results = matrix_validation_results(matrix_validation_result)
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
            recovery=recovery,
            mandatory_argocd=({"status": "not_applicable"} if profile.argocd.mandatory else {"status": "passed"}),
            release_metadata=release_metadata,
            matrix_validation=matrix_validation,
        )

    discovery_clients = discovery_clients or build_default_discovery_clients(release_profile)
    certification_eligible = _certification_eligible(
        release_options=release_options,
        discovery_clients=discovery_clients,
        adapters=adapters,
        git_checkout=git_checkout,
        release_metadata=release_metadata,
    )

    manifest = build_manifest(certification_eligible)
    artifacts.write_json("manifest.json", manifest)

    results: list[dict] = matrix_validation_results(matrix_validation_result)
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
                recovery=recovery,
                mandatory_argocd=({"status": "not_applicable"} if profile.argocd.mandatory else {"status": "passed"}),
                release_metadata=release_metadata,
                matrix_validation=matrix_validation,
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

    if _stop_before_mutation(
        scenarios_by_id=scenarios_by_id,
        lab_readiness_status=lab_readiness.status,
        initial_baseline_status=initial_baseline.status,
    ):
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
            recovery=recovery,
            mandatory_argocd={"status": "passed" if not profile.argocd.mandatory else lab_readiness.status},
            release_metadata=release_metadata,
            matrix_validation=matrix_validation,
        )

    results.extend(
        _execute_stream_scenarios(
            scenarios=matrix.scenarios,
            scenario_profiles=scenario_profiles,
            stream_profiles=stream_profiles,
            adapters=adapters,
            skipped_pairs=set(matrix_validation_result.not_applicable_pairs),
        )
    )

    # Execute live RBAC certification if enabled
    if "rbac-bootstrap-live" in scenarios_by_id:
        rbac_cert_dir = artifacts.run_dir / "scenarios" / "rbac-bootstrap-live"
        rbac_cert_dir.mkdir(parents=True, exist_ok=True)
        rbac_cert_assertions: list[dict] = []
        hub_statuses: list[str] = []
        for hub_name in ("primary", "secondary"):
            hub_result, hub_assertions = _certify_hub_rbac(
                hub=profile.hubs[hub_name],
                hub_name=hub_name,
                scenario_profiles=scenario_profiles,
                rbac_cert_dir=rbac_cert_dir,
            )
            hub_statuses.append(hub_result.status)
            rbac_cert_assertions.extend(hub_assertions)

        if all(status == "skipped" for status in hub_statuses):
            rbac_cert_status = "not_applicable"
        elif any(status == "failed" for status in hub_statuses):
            rbac_cert_status = "failed"
        else:
            rbac_cert_status = "passed"

        results.append(
            _local_result(
                "rbac-bootstrap-live",
                rbac_cert_status,
                rbac_cert_assertions,
                scenarios_by_id["rbac-bootstrap-live"].required,
            )
        )

    runtime_parity = (
        _runtime_parity(artifacts, results, discovery_clients=discovery_clients)
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
        recovery=recovery,
        mandatory_argocd={"status": "passed" if not profile.argocd.mandatory else lab_readiness.status},
        release_metadata=release_metadata,
        matrix_validation=matrix_validation,
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
