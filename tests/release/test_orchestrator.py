from __future__ import annotations

import json
from pathlib import Path

from tests.release.adapters.common import AssertionRecord, StreamResult
from tests.release.checks.static_gates import GateCommand, GateResult
from tests.release.conftest import ReleaseOptions
from tests.release.contracts.loader import load_profile
from tests.release.orchestrator import run_release_certification
from tests.release.reporting.artifacts import ReleaseArtifacts


class FakeDiscoveryClient:
    test_only = True

    def __init__(self, *, primary: bool) -> None:
        self.primary = primary

    def list_resources(self, resource: str, namespace: str | None = None) -> list[dict]:
        if resource == "multiclusterhubs":
            return [{"status": {"currentVersion": "2.12.0"}}]
        if resource == "backupschedules":
            return [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}] if self.primary else []
        if resource == "restores":
            return [] if self.primary else [{"metadata": {"name": "restore"}, "status": {"phase": "Enabled"}}]
        if resource == "managedclusters":
            return [{"metadata": {"name": "cluster-a"}}, {"metadata": {"name": "cluster-b"}}]
        if resource == "backupstoragelocations":
            return [{"metadata": {"name": "default"}, "status": {"phase": "Available"}}]
        return []


class FakeAdapter:
    test_only = True

    def __init__(self, stream: str) -> None:
        self.stream = stream
        self.calls: list[dict] = []

    def execute(self, scenario_id: str, *, timeout_seconds: int | None, env: dict | None, extra_args: tuple[str, ...]):
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


def _release_options(tmp_path: Path, *, mode: str = "certification") -> ReleaseOptions:
    return ReleaseOptions(
        profile_path=Path("tests/release/profiles/dev-minimal.example.yaml"),
        mode=mode,
        scenarios=(),
        streams=(),
        resume_from_artifacts=None,
        rerun_from_artifacts=None,
        artifact_dir=tmp_path,
        allow_dirty=True,
    )


def _passing_gate(command: GateCommand, artifact_dir: Path) -> GateResult:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout = artifact_dir / f"{command.gate_id}-{command.label}.stdout"
    stderr = artifact_dir / f"{command.gate_id}-{command.label}.stderr"
    stdout.write_text("ok\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    return GateResult(command.gate_id, command.label, command.command, 0, "passed", str(stdout), str(stderr), True)


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


def test_orchestrator_static_gate_secret_failure_reaches_summary(tmp_path: Path) -> None:
    profile = load_profile("tests/release/profiles/dev-minimal.example.yaml")
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")

    def failed_gate(command: GateCommand, artifact_dir: Path) -> GateResult:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout = artifact_dir / f"{command.gate_id}-{command.label}.stdout"
        stderr = artifact_dir / f"{command.gate_id}-{command.label}.stderr"
        stdout.write_text("", encoding="utf-8")
        stderr.write_text("Captured output was rejected by the sanitizer\n", encoding="utf-8")
        return GateResult(command.gate_id, command.label, command.command, 0, "failed", str(stdout), str(stderr), True)

    summary = run_release_certification(
        release_options=_release_options(tmp_path),
        release_profile=profile,
        artifacts=artifacts,
        repo_root=Path.cwd(),
        discovery_clients={
            "primary": FakeDiscoveryClient(primary=True),
            "secondary": FakeDiscoveryClient(primary=False),
        },
        adapters={"python": FakeAdapter("python"), "ansible": FakeAdapter("ansible")},
        gate_runner=failed_gate,
    )

    scenario_results = json.loads((artifacts.run_dir / "scenario-results.json").read_text(encoding="utf-8"))
    static_gate = next(item for item in scenario_results["scenario_statuses"] if item["scenario_id"] == "static-gates")
    assert static_gate["status"] == "failed"
    assert "required scenario failed: static-gates" in summary["failure_reasons"]
