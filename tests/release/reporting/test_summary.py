from pathlib import Path

from tests.release.reporting.summary import build_summary
from tests.release.test_release_certification import finalize_release_artifacts


def test_summary_passes_only_when_required_gates_pass() -> None:
    summary = build_summary(
        release_mode="certification",
        certification_eligible=True,
        required_scenarios=[{"scenario_id": "preflight", "status": "passed"}],
        optional_scenarios=[],
        runtime_parity={"status": "passed"},
        artifact_redaction={"status": "passed"},
        final_baseline={"status": "passed"},
        recovery={"status": "passed"},
        mandatory_argocd={"status": "passed"},
        release_metadata={"status": "passed"},
    )

    assert summary["status"] == "passed"
    assert summary["certification_eligible"] is True


def test_summary_fails_dirty_or_non_certification_run() -> None:
    summary = build_summary(
        release_mode="debug",
        certification_eligible=False,
        required_scenarios=[{"scenario_id": "preflight", "status": "passed"}],
        optional_scenarios=[],
        runtime_parity={"status": "passed"},
        artifact_redaction={"status": "passed"},
        final_baseline={"status": "passed"},
        recovery={"status": "passed"},
        mandatory_argocd={"status": "passed"},
        release_metadata={"status": "passed"},
    )

    assert summary["status"] == "failed"
    assert "release mode is not certification" in summary["failure_reasons"]


class FakeArtifacts:
    def __init__(self) -> None:
        self.writes = {}

    def write_json(self, relative_path, payload):
        self.writes[relative_path] = payload

    @property
    def run_dir(self):
        return Path("/tmp/run")


def test_finalize_release_artifacts_writes_summary_and_report() -> None:
    artifacts = FakeArtifacts()

    finalize_release_artifacts(
        artifacts=artifacts,
        manifest={"run_id": "run-1", "profile": {"name": "lab"}},
        summary_inputs={
            "release_mode": "certification",
            "certification_eligible": True,
            "required_scenarios": [{"scenario_id": "preflight", "status": "passed"}],
            "optional_scenarios": [],
            "runtime_parity": {"status": "passed"},
            "artifact_redaction": {"status": "passed"},
            "final_baseline": {"status": "passed"},
            "recovery": {"status": "passed"},
            "mandatory_argocd": {"status": "passed"},
            "release_metadata": {"status": "passed"},
        },
    )

    assert artifacts.writes["summary.json"]["status"] == "passed"
