from pathlib import Path

from tests.release.reporting.render import render_release_report


def test_render_release_report_contains_required_sections() -> None:
    report = render_release_report(
        {
            "status": "failed",
            "release_mode": "certification",
            "certification_eligible": False,
            "required_scenarios": [{"scenario_id": "preflight", "status": "failed"}],
            "optional_scenarios": [],
            "mandatory_argocd": {"status": "passed"},
            "runtime_parity": {"status": "failed"},
            "artifact_redaction": {"status": "passed"},
            "final_baseline": {"status": "passed"},
            "recovery": {"status": "passed"},
            "release_metadata": {"status": "passed"},
            "failure_reasons": ["required scenario failed: preflight"],
            "warnings": [],
        },
        manifest={"run_id": "run-1", "profile": {"name": "lab"}},
    )

    assert "## Run Identity" in report
    assert "## Runtime Parity Summary" in report
    assert "required scenario failed: preflight" in report
    assert "NO-GO" in report


def test_release_validation_operator_doc_mentions_profile_and_modes() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    doc = (repo_root / "docs" / "development" / "release-validation-framework.md").read_text(encoding="utf-8")

    assert "--release-profile" in doc
    assert "certification" in doc
    assert "focused-rerun" in doc
    assert "debug" in doc
    assert "release-report.md" in doc
