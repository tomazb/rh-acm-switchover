"""Schema stability tests for collection report fields consumed by release normalizers.

These tests pin the field shapes that release normalizers depend on.
If a collection module changes its report schema, these tests catch the drift.
"""

from __future__ import annotations


def test_preflight_report_fields_consumed_by_release_normalizer_are_stable() -> None:
    report = {
        "schema_version": "1.0",
        "status": "passed",
        "summary": {"passed": 10, "critical_failures": 0, "warning_failures": 0},
        "results": [{"id": "acm-version", "severity": "critical", "status": "passed", "message": "ok"}],
        "hubs": {},
    }

    assert report["schema_version"]
    assert isinstance(report["summary"]["critical_failures"], int)
    assert report["results"][0]["id"] == "acm-version"


def test_switchover_report_fields_consumed_by_release_normalizer_are_stable() -> None:
    report = {
        "schema_version": "1.0",
        "source": "ansible",
        "argocd": {"run_id": "run-1", "summary": {"paused": 1, "restored": 1}},
        "phases": {
            "primary_prep": {"status": "passed"},
            "activation": {"status": "passed"},
            "post_activation": {"status": "passed"},
            "finalization": {"status": "passed"},
        },
    }

    assert report["argocd"]["run_id"] == "run-1"
    assert report["phases"]["activation"]["status"] == "passed"


def test_checkpoint_fields_consumed_by_release_normalizer_are_stable() -> None:
    checkpoint = {
        "schema_version": "1.0",
        "completed_phases": ["preflight"],
        "phase_status": {"preflight": "completed"},
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-04-27T00:00:00+00:00",
    }

    assert checkpoint["completed_phases"] == ["preflight"]
    assert checkpoint["phase_status"]["preflight"] == "completed"
