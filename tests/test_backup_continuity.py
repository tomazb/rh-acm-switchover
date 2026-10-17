"""Raw Backup evidence and creation-boundary decisions, with no storage writes."""

import copy
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.run_record import RunRecord
from lib.utils import StateManager

VECTORS = Path(__file__).parent / "fixtures/backup_continuity_compatibility.json"


def domain():
    try:
        return importlib.import_module("modules.backup_continuity")
    except ModuleNotFoundError:
        pytest.fail("The approved read-only Backup boundary decision is not implemented")


@pytest.mark.parametrize("case", json.loads(VECTORS.read_text()), ids=lambda c: c["id"])
def test_boundary_and_creation_vectors(case):
    owner = domain()
    original = copy.deepcopy(case)
    try:
        boundary = owner.resolve_enable_boundary(
            case["inputs"], observed_at=datetime.fromisoformat(case["observed_at"].replace("Z", "+00:00"))
        )
        proof = owner.validate_backup(
            case["backup"],
            enabled_at=boundary,
            observed_at=datetime.fromisoformat(case["observed_at"].replace("Z", "+00:00")),
        )
    except owner.BackupContinuityError as exc:
        assert exc.reason == case["reason"]
    else:
        assert case["reason"] is None
        assert proof["name"] == "acm-managed-clusters-proof"
    assert case == original


def test_primary_boundary_absent_uses_single_valid_enable_marker_without_write(tmp_path):
    state = StateManager(str(tmp_path / "state.json"))
    state.mark_step_completed("enable_backup_schedule")
    before = state.capture_state_snapshot()
    raw = Path(state.state_file).read_bytes()
    with patch.object(state, "save_state", side_effect=AssertionError("write")):
        evidence = RunRecord(state).backup_continuity_inputs()
        boundary = domain().resolve_enable_boundary(evidence, observed_at=datetime.now(timezone.utc))
    assert boundary.tzinfo is not None
    assert evidence["enabled_at_present"] is False
    assert state.capture_state_snapshot() == before and Path(state.state_file).read_bytes() == raw


@pytest.mark.parametrize("value", [None, "", False, 0, 42, {}, [], "invalid", "2026-10-17T10:00:00"])
def test_present_invalid_boundary_never_falls_back(tmp_path, value):
    state = StateManager(str(tmp_path / "state.json"))
    state._set_config("backup_schedule_enabled_at", value)
    state.mark_step_completed("enable_backup_schedule")
    inputs = RunRecord(state).backup_continuity_inputs()
    assert inputs["enabled_at_present"] is True and inputs["enabled_at"] == value
    with pytest.raises(domain().BackupContinuityError, match="invalid_enable_boundary"):
        domain().resolve_enable_boundary(inputs, observed_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("steps", [[{"timestamp": "bad"}], [{"timestamp": "2026-10-17T10:00:00Z"}] * 2])
def test_duplicate_or_invalid_enable_markers_refuse(steps):
    with pytest.raises(domain().BackupContinuityError):
        domain().resolve_enable_boundary(
            {"enabled_at_present": False, "legacy_enable_steps": steps}, observed_at=datetime.now(timezone.utc)
        )


def test_current_flags_cannot_supply_boundary():
    with pytest.raises(domain().BackupContinuityError, match="missing_enable_boundary"):
        domain().resolve_enable_boundary(
            {"enabled_at_present": False, "restore_only": True, "now": "2026-10-17T10:00:00Z"},
            observed_at=datetime.now(timezone.utc),
        )


def test_creation_time_not_completion_time_controls_freshness():
    case = next(c for c in json.loads(VECTORS.read_text()) if c["id"] == "old-creation-new-completion")
    owner = domain()
    with pytest.raises(owner.BackupContinuityError, match="backup_predates_enable"):
        owner.validate_backup(
            case["backup"],
            enabled_at=datetime(2026, 10, 17, 10, tzinfo=timezone.utc),
            observed_at=datetime(2026, 10, 17, 11, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "config,steps",
    [(None, []), ({}, None), ({}, [None]), ({}, [{"name": "enable_backup_schedule", "timestamp": None}])],
)
def test_malformed_raw_evidence_never_becomes_absence(tmp_path, config, steps):
    state = StateManager(str(tmp_path / "state.json"))
    state.state["config"] = config
    state.state["completed_steps"] = steps
    inputs = RunRecord(state).backup_continuity_inputs()
    with pytest.raises(domain().BackupContinuityError):
        domain().resolve_enable_boundary(inputs, observed_at=datetime.now(timezone.utc))
