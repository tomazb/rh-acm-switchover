from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.release.adapters.common import AssertionRecord, StreamResult
from tests.release.checks.rbac_certification import CertificationResult
from tests.release.checks.static_gates import GateCommand, GateResult
from tests.release.conftest import ReleaseOptions
from tests.release.contracts.loader import load_profile
from tests.release.contracts.models import (
    RBACCertificationHubProfile,
    RBACCertificationProfile,
    ReleaseMetadataProfile,
    ScenarioProfile,
)
from tests.release.orchestrator import (
    OcDiscoveryClient,
    _normalized_runtime_sources,
    _runtime_parity,
    run_release_certification,
)
from tests.release.reporting.artifacts import ReleaseArtifacts


class FakeDiscoveryClient:
    test_only = True

    def __init__(self, *, primary: bool, applications_by_namespace: dict[str, list[dict]] | None = None) -> None:
        self.primary = primary
        self.applications_by_namespace = applications_by_namespace or {}
        self.calls: list[tuple[str, str | None]] = []

    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]:
        self.calls.append((resource, namespace))
        if resource == "multiclusterhubs":
            return [{"status": {"currentVersion": "2.12.0"}}]
        if resource == "backupschedules":
            return [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}] if self.primary else []
        if resource == "restores":
            return [] if self.primary else [{"metadata": {"name": "restore"}, "status": {"phase": "Enabled"}}]
        if resource == "managedclusters":
            return [
                {"metadata": {"name": "cluster-a"}},
                {"metadata": {"name": "cluster-b"}},
            ]
        if resource == "applications.argoproj.io":
            return list(self.applications_by_namespace.get(namespace or "", []))
        if resource == "backupstoragelocations":
            return [{"metadata": {"name": "default"}, "status": {"phase": "Available"}}]
        return []


class MissingBackupStorageDiscoveryClient(FakeDiscoveryClient):
    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]:
        if resource == "backupstoragelocations":
            return []
        return super().list_resources(resource, namespace)


class RealisticDiscoveryClient(FakeDiscoveryClient):
    test_only = False


class FakeAdapter:
    test_only = True

    def __init__(self, stream: str) -> None:
        self.stream = stream
        self.calls: list[dict] = []

    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None,
        env: dict | None,
        extra_args: tuple[str, ...],
    ):
        self.calls.append(
            {
                "scenario_id": scenario_id,
                "timeout_seconds": timeout_seconds,
                "env": env,
                "extra_args": extra_args,
            }
        )
        return StreamResult(
            stream=self.stream,
            scenario_id=scenario_id,
            status="passed",
            command=[self.stream, scenario_id],
            returncode=0,
            stdout_path=None,
            stderr_path=None,
            reports=[],
            assertions=[
                AssertionRecord(
                    capability=scenario_id,
                    name="exit-code",
                    status="passed",
                    expected="0",
                    actual="0",
                    evidence_path=None,
                    message="passed",
                )
            ],
            started_at="2026-05-05T00:00:00+00:00",
            ended_at="2026-05-05T00:00:01+00:00",
        )


class FailingAdapter(FakeAdapter):
    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None,
        env: dict | None,
        extra_args: tuple[str, ...],
    ):
        raise RuntimeError("adapter boom")


class RealisticAdapter(FakeAdapter):
    test_only = False


def _release_options(tmp_path: Path, *, mode: str = "certification") -> ReleaseOptions:
    return ReleaseOptions(
        profile_path=Path("tests/release/profiles/dev-minimal.example.yaml"),
        mode=mode,
        scenarios=(),
        streams=(),
        artifact_dir=tmp_path,
        allow_dirty=True,
    )


def _passing_gate(command: GateCommand, artifact_dir: Path) -> GateResult:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout = artifact_dir / f"{command.gate_id}-{command.label}.stdout"
    stderr = artifact_dir / f"{command.gate_id}-{command.label}.stderr"
    stdout.write_text("ok\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    return GateResult(
        command.gate_id,
        command.label,
        command.command,
        0,
        "passed",
        str(stdout),
        str(stderr),
        True,
    )


def test_orchestrator_writes_required_artifacts_with_fake_lab(tmp_path: Path) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    python = FakeAdapter("python")
    ansible = FakeAdapter("ansible")

    summary = run_release_certification(
        release_options=_release_options(tmp_path),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={"python": python, "ansible": ansible},
        gate_runner=_passing_gate,
    )

    assert summary["certification_eligible"] is False
    assert "run is not certification eligible" in summary["failure_reasons"]
    for filename in [
        "manifest.json",
        "scenario-results.json",
        "runtime-parity.json",
        "summary.json",
        "release-report.md",
        "environment-fingerprint-initial.json",
        "environment-fingerprint-final.json",
    ]:
        assert (artifacts.run_dir / filename).exists()
    assert [call["scenario_id"] for call in python.calls] == ["preflight"]
    assert [call["scenario_id"] for call in ansible.calls] == ["preflight"]
    manifest = json.loads((artifacts.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["profile"]["name"] == "dev-minimal-release"


def test_orchestrator_static_gate_secret_failure_reaches_summary(
    tmp_path: Path,
) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    primary = FakeDiscoveryClient(primary=True)
    secondary = FakeDiscoveryClient(primary=False)
    python = FakeAdapter("python")
    ansible = FakeAdapter("ansible")

    def failed_gate(command: GateCommand, artifact_dir: Path) -> GateResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout = artifact_dir / f"{command.gate_id}-{command.label}.stdout"
        stderr = artifact_dir / f"{command.gate_id}-{command.label}.stderr"
        stdout.write_text("", encoding="utf-8")
        stderr.write_text("Captured output was rejected by the sanitizer\n", encoding="utf-8")
        return GateResult(
            command.gate_id,
            command.label,
            command.command,
            0,
            "failed",
            str(stdout),
            str(stderr),
            True,
        )

    summary = run_release_certification(
        release_options=_release_options(tmp_path),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": primary,
            "secondary": secondary,
        },
        adapters={"python": python, "ansible": ansible},
        gate_runner=failed_gate,
    )

    scenario_results = json.loads((artifacts.run_dir / "scenario-results.json").read_text(encoding="utf-8"))
    static_gate = next(item for item in scenario_results["scenario_statuses"] if item["scenario_id"] == "static-gates")
    assert static_gate["status"] == "failed"
    assert "required scenario failed: static-gates" in summary["failure_reasons"]
    assert primary.calls == []
    assert secondary.calls == []
    assert python.calls == []
    assert ansible.calls == []


def test_orchestrator_records_adapter_exceptions_as_failed_results(
    tmp_path: Path,
) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    summary = run_release_certification(
        release_options=_release_options(tmp_path),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={
            "python": FailingAdapter("python"),
            "ansible": FakeAdapter("ansible"),
        },
        gate_runner=_passing_gate,
    )

    scenario_results = json.loads((artifacts.run_dir / "scenario-results.json").read_text(encoding="utf-8"))
    preflight = next(item for item in scenario_results["scenario_statuses"] if item["scenario_id"] == "preflight")
    python_result = next(
        item
        for item in scenario_results["results"]
        if item["scenario_id"] == "preflight" and item["stream"] == "python"
    )
    assert preflight["status"] == "failed"
    assert python_result["assertions"][0]["name"] == "adapter-execution"
    assert "required scenario failed: preflight" in summary["failure_reasons"]


def test_orchestrator_uses_profile_live_rbac_certification_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    loaded = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    certification_scope = RBACCertificationProfile(
        primary=RBACCertificationHubProfile(
            role="operator",
            namespace="custom-primary-ns",
            service_account="custom-primary-sa",
            include_decommission=False,
            include_old_hub_finalization=True,
        ),
        secondary=RBACCertificationHubProfile(
            role="validator",
            namespace="custom-secondary-ns",
            service_account="custom-secondary-sa",
            include_decommission=False,
            include_old_hub_finalization=False,
        ),
    )
    profile = replace(
        loaded.profile,
        scenarios=loaded.profile.scenarios
        + (
            ScenarioProfile(
                id="rbac-bootstrap-live",
                required=True,
                rbac_certification=certification_scope,
            ),
        ),
    )
    release_profile = replace(loaded, profile=profile)
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    calls = []

    def fake_certify(**kwargs):
        calls.append(kwargs)
        return CertificationResult(status="passed", assertions=[])

    monkeypatch.setattr(
        "tests.release.orchestrator.certify_rbac_permissions",
        fake_certify,
    )

    run_release_certification(
        release_options=replace(
            _release_options(tmp_path),
            scenarios=("rbac-bootstrap-live",),
        ),
        release_profile=release_profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={"python": FakeAdapter("python"), "ansible": FakeAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    assert [
        (
            call["hub_name"],
            call["role"],
            call["namespace"],
            call["service_account"],
            call["include_decommission"],
            call["include_old_hub_finalization"],
        )
        for call in calls
    ] == [
        (
            "primary",
            "operator",
            "custom-primary-ns",
            "custom-primary-sa",
            False,
            True,
        ),
        (
            "secondary",
            "validator",
            "custom-secondary-ns",
            "custom-secondary-sa",
            False,
            False,
        ),
    ]


def test_orchestrator_required_live_rbac_skip_fails_required_scenario(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ACM_ENABLE_LIVE_RBAC_CERTIFICATION", raising=False)
    loaded = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    profile = replace(
        loaded.profile,
        scenarios=loaded.profile.scenarios
        + (
            ScenarioProfile(
                id="rbac-bootstrap-live",
                required=True,
            ),
        ),
    )
    release_profile = replace(loaded, profile=profile)
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    summary = run_release_certification(
        release_options=replace(
            _release_options(tmp_path),
            scenarios=("rbac-bootstrap-live",),
        ),
        release_profile=release_profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={"python": FakeAdapter("python"), "ansible": FakeAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    scenario_results = json.loads((artifacts.run_dir / "scenario-results.json").read_text(encoding="utf-8"))
    rbac_live = next(
        item for item in scenario_results["scenario_statuses"] if item["scenario_id"] == "rbac-bootstrap-live"
    )
    assert rbac_live["status"] == "failed"
    assert "required scenario failed: rbac-bootstrap-live" in summary["failure_reasons"]


def test_orchestrator_stops_before_mutation_when_lab_readiness_fails(
    tmp_path: Path,
) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    primary = MissingBackupStorageDiscoveryClient(primary=True)
    secondary = MissingBackupStorageDiscoveryClient(primary=False)
    python = FakeAdapter("python")
    ansible = FakeAdapter("ansible")

    summary = run_release_certification(
        release_options=_release_options(tmp_path),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": primary,
            "secondary": secondary,
        },
        adapters={"python": python, "ansible": ansible},
        gate_runner=_passing_gate,
    )

    scenario_results = json.loads((artifacts.run_dir / "scenario-results.json").read_text(encoding="utf-8"))
    lab_readiness = next(
        item for item in scenario_results["scenario_statuses"] if item["scenario_id"] == "lab-readiness"
    )
    assert lab_readiness["status"] == "failed"
    assert "required scenario failed: lab-readiness" in summary["failure_reasons"]
    assert python.calls == []
    assert ansible.calls == []


def test_normalized_runtime_sources_populates_argocd_management_from_reports_and_pause_markers(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    ansible_dir = tmp_path / "ansible"
    python_dir.mkdir()
    ansible_dir.mkdir()
    (python_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "acm_switchover.py", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (ansible_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "tomazb.acm_switchover", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (python_dir / "state.json").write_text(
        json.dumps(
            {
                "config": {
                    "resume_summary": {"resume_start_phase": "activation"},
                    "argocd_run_id": "run-1",
                    "argocd_discovery_namespaces": {"secondary": ["argocd"]},
                }
            }
        ),
        encoding="utf-8",
    )
    (ansible_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "operational_data": {
                    "resume_summary": {"resume_start_phase": "activation"},
                    "argocd_run_id": "run-1",
                    "argocd_discovery_namespaces": {"secondary": ["argocd"]},
                }
            }
        ),
        encoding="utf-8",
    )
    results = [
        {
            "stream": "python",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(python_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(python_dir / "switchover-report.json")}],
        },
        {
            "stream": "ansible",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(ansible_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(ansible_dir / "switchover-report.json")}],
        },
    ]
    discovery_clients = {
        "primary": FakeDiscoveryClient(primary=True),
        "secondary": FakeDiscoveryClient(
            primary=False,
            applications_by_namespace={
                "argocd": [
                    {
                        "metadata": {
                            "namespace": "argocd",
                            "name": "app-a",
                            "annotations": {"acm-switchover.argoproj.io/paused-by": "run-1"},
                        }
                    }
                ]
            },
        ),
    }

    sources = _normalized_runtime_sources(results, discovery_clients=discovery_clients)

    assert sources["Argo CD management"]["python"] == {
        "run_id_present": True,
        "paused_application_names": ["secondary:argocd/app-a"],
        "paused_application_count": 1,
        "run_id_preserved_for_retry": "preserved",
    }
    assert sources["Argo CD management"]["python"] == sources["Argo CD management"]["ansible"]


def test_normalized_runtime_sources_ignores_argocd_items_missing_name(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    ansible_dir = tmp_path / "ansible"
    python_dir.mkdir()
    ansible_dir.mkdir()
    (python_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "acm_switchover.py", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (ansible_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "tomazb.acm_switchover", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (python_dir / "state.json").write_text(
        json.dumps(
            {
                "config": {
                    "resume_summary": {"resume_start_phase": "activation"},
                    "argocd_run_id": "run-1",
                    "argocd_discovery_namespaces": {"secondary": ["argocd"]},
                }
            }
        ),
        encoding="utf-8",
    )
    (ansible_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "operational_data": {
                    "resume_summary": {"resume_start_phase": "activation"},
                    "argocd_run_id": "run-1",
                    "argocd_discovery_namespaces": {"secondary": ["argocd"]},
                }
            }
        ),
        encoding="utf-8",
    )
    results = [
        {
            "stream": "python",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(python_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(python_dir / "switchover-report.json")}],
        },
        {
            "stream": "ansible",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(ansible_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(ansible_dir / "switchover-report.json")}],
        },
    ]
    discovery_clients = {
        "primary": FakeDiscoveryClient(primary=True),
        "secondary": FakeDiscoveryClient(
            primary=False,
            applications_by_namespace={
                "argocd": [
                    {
                        "metadata": {
                            "namespace": "argocd",
                            "annotations": {"acm-switchover.argoproj.io/paused-by": "run-1"},
                        }
                    },
                    {
                        "metadata": {
                            "namespace": "argocd",
                            "name": "app-a",
                            "annotations": {"acm-switchover.argoproj.io/paused-by": "run-1"},
                        }
                    },
                ]
            },
        ),
    }

    sources = _normalized_runtime_sources(results, discovery_clients=discovery_clients)

    assert sources["Argo CD management"]["python"]["paused_application_names"] == ["secondary:argocd/app-a"]
    assert sources["Argo CD management"]["python"] == sources["Argo CD management"]["ansible"]


def test_normalized_runtime_sources_ignores_non_mapping_state_payloads(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    ansible_dir = tmp_path / "ansible"
    python_dir.mkdir()
    ansible_dir.mkdir()
    (python_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "acm_switchover.py", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (ansible_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "tomazb.acm_switchover", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (python_dir / "state.json").write_text(json.dumps(["not-a-mapping"]), encoding="utf-8")
    (ansible_dir / "checkpoint.json").write_text(json.dumps(["not-a-mapping"]), encoding="utf-8")
    results = [
        {
            "stream": "python",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(python_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(python_dir / "switchover-report.json")}],
        },
        {
            "stream": "ansible",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(ansible_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(ansible_dir / "switchover-report.json")}],
        },
    ]

    sources = _normalized_runtime_sources(results, discovery_clients={"secondary": FakeDiscoveryClient(primary=False)})

    assert sources["Argo CD management"]["python"] == {
        "run_id_present": True,
        "paused_application_names": [],
        "paused_application_count": 0,
        "run_id_preserved_for_retry": "not_applicable",
    }
    assert sources["Argo CD management"]["python"] == sources["Argo CD management"]["ansible"]


def test_normalized_runtime_sources_ignores_malformed_argocd_entries(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    ansible_dir = tmp_path / "ansible"
    python_dir.mkdir()
    ansible_dir.mkdir()
    (python_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "acm_switchover.py", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (ansible_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "tomazb.acm_switchover", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    state_payload = {
        "config": {
            "resume_summary": {"resume_start_phase": "activation"},
            "argocd_run_id": "run-1",
            "argocd_discovery_namespaces": {"secondary": ["argocd"]},
        }
    }
    (python_dir / "state.json").write_text(json.dumps(state_payload), encoding="utf-8")
    (ansible_dir / "checkpoint.json").write_text(json.dumps(state_payload), encoding="utf-8")
    results = [
        {
            "stream": "python",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(python_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(python_dir / "switchover-report.json")}],
        },
        {
            "stream": "ansible",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(ansible_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(ansible_dir / "switchover-report.json")}],
        },
    ]
    discovery_clients = {
        "secondary": FakeDiscoveryClient(
            primary=False,
            applications_by_namespace={
                "argocd": [
                    "bad-entry",
                    {"metadata": "bad-metadata"},
                    {"metadata": {"annotations": "bad-annotations"}},
                    {
                        "metadata": {
                            "namespace": "argocd",
                            "name": "app-a",
                            "annotations": {"acm-switchover.argoproj.io/paused-by": "run-1"},
                        }
                    },
                ]
            },
        )
    }

    sources = _normalized_runtime_sources(results, discovery_clients=discovery_clients)

    assert sources["Argo CD management"]["python"]["paused_application_names"] == ["secondary:argocd/app-a"]
    assert sources["Argo CD management"]["python"] == sources["Argo CD management"]["ansible"]


def test_normalized_runtime_sources_skips_explicitly_empty_argocd_namespace_hints(tmp_path: Path) -> None:
    python_dir = tmp_path / "python"
    ansible_dir = tmp_path / "ansible"
    python_dir.mkdir()
    ansible_dir.mkdir()
    (python_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "acm_switchover.py", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    (ansible_dir / "switchover-report.json").write_text(
        json.dumps({"schema_version": "1.0", "source": "tomazb.acm_switchover", "argocd": {"run_id": "run-1"}}),
        encoding="utf-8",
    )
    state_payload = {
        "config": {
            "resume_summary": {"resume_start_phase": "activation"},
            "argocd_run_id": "run-1",
            "argocd_discovery_namespaces": {"secondary": []},
        }
    }
    (python_dir / "state.json").write_text(json.dumps(state_payload), encoding="utf-8")
    (ansible_dir / "checkpoint.json").write_text(json.dumps(state_payload), encoding="utf-8")
    results = [
        {
            "stream": "python",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(python_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(python_dir / "switchover-report.json")}],
        },
        {
            "stream": "ansible",
            "scenario_id": "argocd-managed-switchover",
            "stdout_path": str(ansible_dir / "stdout.txt"),
            "reports": [{"type": "switchover", "path": str(ansible_dir / "switchover-report.json")}],
        },
    ]
    secondary = FakeDiscoveryClient(primary=False)

    sources = _normalized_runtime_sources(results, discovery_clients={"secondary": secondary})

    assert ("applications.argoproj.io", None) not in secondary.calls
    assert sources["Argo CD management"]["python"]["paused_application_names"] == []
    assert sources["Argo CD management"]["python"] == sources["Argo CD management"]["ansible"]


def test_runtime_parity_records_rbac_live_consistency_failure(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    (tmp_path / "rbac-bootstrap-report.json").write_text(
        json.dumps({"status": "pass", "assets_applied": ["deploy/rbac/clusterrole.yaml"]}),
        encoding="utf-8",
    )
    results = [
        {
            "stream": "ansible",
            "scenario_id": "rbac-bootstrap",
            "status": "passed",
            "reports": [{"type": "rbac-bootstrap", "path": str(tmp_path / "rbac-bootstrap-report.json")}],
        },
        {
            "stream": "local",
            "scenario_id": "rbac-bootstrap-live",
            "status": "failed",
            "assertions": [{"status": "failed", "name": "core/pods:get@cluster"}],
            "reports": [],
        },
    ]

    payload = _runtime_parity(artifacts, results, discovery_clients={})

    assert payload["status"] == "failed"
    live_record = next(item for item in payload["comparisons"] if item["capability"] == "RBAC live consistency")
    assert live_record["status"] == "failed"


def test_runtime_parity_treats_optional_rbac_live_check_as_not_applicable(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    (tmp_path / "rbac-bootstrap-report.json").write_text(
        json.dumps({"status": "pass", "assets_applied": ["deploy/rbac/clusterrole.yaml"]}),
        encoding="utf-8",
    )
    results = [
        {
            "stream": "ansible",
            "scenario_id": "rbac-bootstrap",
            "status": "passed",
            "reports": [{"type": "rbac-bootstrap", "path": str(tmp_path / "rbac-bootstrap-report.json")}],
        },
        {
            "stream": "local",
            "scenario_id": "rbac-bootstrap-live",
            "status": "not_applicable",
            "assertions": [],
            "reports": [],
        },
    ]

    payload = _runtime_parity(artifacts, results, discovery_clients={})

    assert payload["status"] == "not_applicable"
    live_record = next(item for item in payload["comparisons"] if item["capability"] == "RBAC live consistency")
    assert live_record["status"] == "not_applicable"


def test_runtime_parity_flags_unknown_bootstrap_status_when_bootstrap_evidence_is_malformed(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    (tmp_path / "rbac-bootstrap-report.json").write_text(json.dumps(["bad-payload"]), encoding="utf-8")
    results = [
        {
            "stream": "ansible",
            "scenario_id": "rbac-bootstrap",
            "reports": [
                "bad-report-entry",
                {"type": "rbac-bootstrap", "path": str(tmp_path / "rbac-bootstrap-report.json")},
            ],
        },
        {
            "stream": "local",
            "scenario_id": "rbac-bootstrap-live",
            "status": "failed",
            "assertions": [{"status": "failed", "name": "core/pods:get@cluster"}],
            "reports": [],
        },
    ]

    payload = _runtime_parity(artifacts, results, discovery_clients={})

    live_record = next(item for item in payload["comparisons"] if item["capability"] == "RBAC live consistency")
    assert live_record["status"] == "failed"
    assert live_record["differences"] == [{"field": "live_status", "ansible": "unknown", "local": "failed"}]


def test_orchestrator_stops_before_mutation_when_baseline_check_fails(
    tmp_path: Path,
) -> None:
    loaded = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    profile = replace(
        loaded.profile,
        baseline=replace(loaded.profile.baseline, initial_primary="secondary"),
    )
    release_profile = replace(loaded, profile=profile)
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    python = FakeAdapter("python")
    ansible = FakeAdapter("ansible")

    summary = run_release_certification(
        release_options=_release_options(tmp_path),
        release_profile=release_profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={"python": python, "ansible": ansible},
        gate_runner=_passing_gate,
    )

    scenario_results = json.loads((artifacts.run_dir / "scenario-results.json").read_text(encoding="utf-8"))
    baseline_check = next(
        item for item in scenario_results["scenario_statuses"] if item["scenario_id"] == "baseline-check"
    )
    assert baseline_check["status"] == "failed"
    assert "required scenario failed: baseline-check" in summary["failure_reasons"]
    assert python.calls == []
    assert ansible.calls == []


def test_orchestrator_required_scenario_without_executable_streams_fails(
    tmp_path: Path,
) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    summary = run_release_certification(
        release_options=replace(_release_options(tmp_path), streams=("bash",)),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={"python": FakeAdapter("python"), "ansible": FakeAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    scenario_results = json.loads((artifacts.run_dir / "scenario-results.json").read_text(encoding="utf-8"))
    preflight = next(item for item in scenario_results["scenario_statuses"] if item["scenario_id"] == "preflight")
    assert preflight["status"] == "failed"
    assert "required scenario failed: preflight" in summary["failure_reasons"]


def test_orchestrator_blocks_dirty_checkout_without_allow_dirty(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    python = RealisticAdapter("python")
    ansible = RealisticAdapter("ansible")

    monkeypatch.setattr(
        "tests.release.orchestrator._git_checkout_state",
        lambda repo_root: {"available": True, "dirty": True, "allow_dirty": False, "commit": "abc123", "warnings": []},
    )

    summary = run_release_certification(
        release_options=replace(_release_options(tmp_path), allow_dirty=False),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": RealisticDiscoveryClient(primary=True),
            "secondary": RealisticDiscoveryClient(primary=False),
        },
        adapters={"python": python, "ansible": ansible},
        gate_runner=_passing_gate,
    )

    manifest = json.loads((artifacts.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert manifest["failure_reasons"] == [
        "release certification failed: RuntimeError: git checkout is dirty; rerun with --allow-dirty"
    ]
    assert python.calls == []
    assert ansible.calls == []


def test_orchestrator_dirty_allow_dirty_run_is_not_certification_eligible(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    monkeypatch.setattr(
        "tests.release.orchestrator._git_checkout_state",
        lambda repo_root: {"available": True, "dirty": True, "allow_dirty": True, "commit": "abc123", "warnings": []},
    )

    summary = run_release_certification(
        release_options=replace(_release_options(tmp_path), allow_dirty=True),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": RealisticDiscoveryClient(primary=True),
            "secondary": RealisticDiscoveryClient(primary=False),
        },
        adapters={"python": RealisticAdapter("python"), "ansible": RealisticAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    manifest = json.loads((artifacts.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert summary["certification_eligible"] is False
    assert "run is not certification eligible" in summary["failure_reasons"]
    assert manifest["git"] == {
        "available": True,
        "dirty": True,
        "allow_dirty": True,
        "commit": "abc123",
        "warnings": [],
    }


def test_orchestrator_records_release_metadata_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    loaded = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    profile = replace(
        loaded.profile,
        release=ReleaseMetadataProfile(expected_version="9.9.9", metadata_files=("README.md",)),
    )
    release_profile = replace(loaded, profile=profile)
    artifacts = ReleaseArtifacts.create(root=tmp_path / "artifacts", run_id="run-1")
    (tmp_path / "README.md").write_text("Version 1.0.0\n", encoding="utf-8")

    monkeypatch.setattr(
        "tests.release.orchestrator._git_checkout_state",
        lambda repo_root: {"available": True, "dirty": False, "allow_dirty": False, "commit": "abc123", "warnings": []},
    )

    summary = run_release_certification(
        release_options=replace(_release_options(tmp_path / "artifacts"), allow_dirty=False),
        release_profile=release_profile,
        artifacts=artifacts,
        repo_root=tmp_path,
        discovery_clients={
            "primary": RealisticDiscoveryClient(primary=True),
            "secondary": RealisticDiscoveryClient(primary=False),
        },
        adapters={"python": RealisticAdapter("python"), "ansible": RealisticAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    manifest = json.loads((artifacts.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert summary["release_metadata"]["status"] == "failed"
    assert "release metadata failed" in summary["failure_reasons"]
    assert manifest["release_metadata"]["status"] == "failed"
    assert manifest["release_metadata"]["hash"] is None


def test_orchestrator_writes_recovery_budget_from_profile(
    monkeypatch,
    tmp_path: Path,
) -> None:
    loaded = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    profile = replace(
        loaded.profile,
        recovery=replace(loaded.profile.recovery, total_budget_minutes=17),
    )
    release_profile = replace(loaded, profile=profile)
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    monkeypatch.setattr(
        "tests.release.orchestrator._git_checkout_state",
        lambda repo_root: {"available": True, "dirty": False, "allow_dirty": False, "commit": "abc123", "warnings": []},
    )

    run_release_certification(
        release_options=replace(_release_options(tmp_path), allow_dirty=False),
        release_profile=release_profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={"python": FakeAdapter("python"), "ansible": FakeAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    recovery = json.loads((artifacts.run_dir / "recovery.json").read_text(encoding="utf-8"))
    assert recovery["budget_minutes"] == 17
    assert recovery["hard_stops"] == []


def test_oc_discovery_client_raises_explicit_error_when_oc_is_missing(monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise FileNotFoundError("oc")

    monkeypatch.setattr("tests.release.orchestrator.subprocess.run", fail_run)

    client = OcDiscoveryClient(kubeconfig="/missing", context="ctx")

    with pytest.raises(RuntimeError, match="discovery failed for managedclusters.*FileNotFoundError"):
        client.list_resources("managedclusters")


def test_oc_discovery_client_raises_explicit_error_on_invalid_json(monkeypatch) -> None:
    class Completed:
        returncode = 0
        stdout = "{not-json"

    def bad_json(*args, **kwargs):
        return Completed()

    monkeypatch.setattr("tests.release.orchestrator.subprocess.run", bad_json)

    client = OcDiscoveryClient(kubeconfig="/missing", context="ctx")

    with pytest.raises(RuntimeError, match="discovery failed for managedclusters.*invalid JSON"):
        client.list_resources("managedclusters")


def test_orchestrator_records_discovery_failure_in_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_run(*args, **kwargs):
        raise FileNotFoundError("oc")

    monkeypatch.setattr("tests.release.orchestrator.subprocess.run", fail_run)
    monkeypatch.setattr(
        "tests.release.orchestrator._git_checkout_state",
        lambda repo_root: {"available": True, "dirty": False, "allow_dirty": False, "commit": "abc123", "warnings": []},
    )
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    summary = run_release_certification(
        release_options=replace(_release_options(tmp_path), allow_dirty=False),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": OcDiscoveryClient(kubeconfig="/missing", context="primary"),
            "secondary": OcDiscoveryClient(kubeconfig="/missing", context="secondary"),
        },
        adapters={"python": FakeAdapter("python"), "ansible": FakeAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    manifest = json.loads((artifacts.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert "discovery failed for multiclusterhubs" in manifest["failure_reasons"][0]


def test_orchestrator_marks_manifest_failed_on_unexpected_exception(
    tmp_path: Path,
) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    summary = run_release_certification(
        release_options=_release_options(tmp_path),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={"primary": FakeDiscoveryClient(primary=True)},
        adapters={"python": FakeAdapter("python"), "ansible": FakeAdapter("ansible")},
        gate_runner=_passing_gate,
    )

    manifest = json.loads((artifacts.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert summary["status"] == "failed"
    assert manifest["failure_reasons"][0].startswith("release certification failed: KeyError")
