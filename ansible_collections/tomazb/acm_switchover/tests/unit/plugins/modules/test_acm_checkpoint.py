"""Tests for the acm_checkpoint collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_checkpoint import (
    build_checkpoint_record,
    should_resume_phase,
)


def test_build_checkpoint_record_sets_schema_and_phase():
    record = build_checkpoint_record("activation", {"method": "passive"})
    assert record["schema_version"] == "1.0"
    assert record["phase"] == "activation"


def test_build_checkpoint_record_contains_all_schema_fields():
    record = build_checkpoint_record("preflight", {})
    for key in ("schema_version", "phase", "completed_phases", "operational_data", "errors", "report_refs", "created_at", "updated_at"):
        assert key in record, f"Missing field: {key}"
    assert record["completed_phases"] == []
    assert record["errors"] == []
    assert record["report_refs"] == []


def test_should_resume_phase_skips_completed_phase():
    assert should_resume_phase(
        checkpoint={"completed_phases": ["preflight", "primary_prep"]},
        phase="primary_prep",
    ) is False


def test_should_resume_phase_returns_true_for_new_phase():
    assert should_resume_phase(
        checkpoint={"completed_phases": ["preflight"]},
        phase="activation",
    ) is True
