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
            "matrix_validation": {
                "status": "failed",
                "reasons": ["mutating scenario sequence requires reset/recovery between scenarios"],
                "issues": [
                    {
                        "scenario_id": "ansible-passive-switchover",
                        "stream": None,
                        "status": "failed",
                        "required": True,
                        "code": "matrix-lifecycle",
                        "reason": "mutating scenario sequence requires reset/recovery between scenarios",
                    },
                    {
                        "scenario_id": "full-restore",
                        "stream": "ansible",
                        "status": "not_applicable",
                        "required": False,
                        "code": "matrix-support",
                        "reason": "ansible stream does not implement this scenario in Phase 1",
                    },
                ],
            },
            "failure_reasons": ["required scenario failed: preflight"],
            "warnings": [],
        },
        manifest={"run_id": "run-1", "profile": {"name": "lab"}},
    )

    assert "## Run Identity" in report
    assert "## Runtime Parity Summary" in report
    assert "## Matrix Validation" in report
    assert "requires reset/recovery" in report
    assert "`full-restore` / `ansible`: `not_applicable`" in report
    assert "`matrix-support`, required=false" in report
    assert "ansible stream does not implement this scenario in Phase 1" in report
    assert "`ansible-passive-switchover` / `n/a`: `failed`" in report
    assert "`matrix-lifecycle`, required=true" in report
    assert "required scenario failed: preflight" in report
    assert "NO-GO" in report


def test_render_release_report_handles_missing_matrix_validation() -> None:
    report = render_release_report(
        {
            "status": "passed",
            "release_mode": "certification",
            "certification_eligible": True,
            "required_scenarios": [],
            "optional_scenarios": [],
            "mandatory_argocd": {"status": "passed"},
            "runtime_parity": {"status": "passed"},
            "artifact_redaction": {"status": "passed"},
            "final_baseline": {"status": "passed"},
            "recovery": {"status": "passed"},
            "release_metadata": {"status": "passed"},
            "matrix_validation": None,
            "failure_reasons": [],
            "warnings": [],
        },
        manifest={"run_id": "run-1", "profile": {"name": "lab"}},
    )

    assert "## Matrix Validation" in report
    assert "- Status: `passed`" in report
    assert "- Decision: **GO**" in report


def test_render_release_report_handles_malformed_matrix_validation_issues() -> None:
    report = render_release_report(
        {
            "status": "failed",
            "release_mode": "certification",
            "certification_eligible": False,
            "required_scenarios": [],
            "optional_scenarios": [],
            "mandatory_argocd": {"status": "passed"},
            "runtime_parity": {"status": "passed"},
            "artifact_redaction": {"status": "passed"},
            "final_baseline": {"status": "passed"},
            "recovery": {"status": "passed"},
            "release_metadata": {"status": "passed"},
            "matrix_validation": {
                "status": "failed",
                "issues": [
                    "bad",
                    {
                        "scenario_id": "full-restore",
                        "stream": "ansible",
                        "status": "not_applicable",
                        "required": False,
                        "code": "matrix-support",
                        "reason": "ansible stream does not implement this scenario in Phase 1",
                    },
                ],
            },
            "failure_reasons": [],
            "warnings": [],
        },
        manifest={"run_id": "run-1", "profile": {"name": "lab"}},
    )

    assert "malformed matrix validation issue" in report
    assert "`full-restore` / `ansible`: `not_applicable`" in report


def test_release_validation_operator_doc_mentions_profile_and_modes() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    doc = (repo_root / "docs" / "development" / "release-validation-framework.md").read_text(encoding="utf-8")

    assert "--release-profile" in doc
    assert "certification" in doc
    assert "focused-rerun" in doc
    assert "debug" in doc
    assert "release-report.md" in doc
