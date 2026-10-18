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


# The fixture retains real KubeClient discovery/strict producers, Finalization,
# RunRecord and StateManager; doubles exist only at transport and clock boundaries.
from tests.test_finalization_backup_strict import make_finalization, page


@pytest.mark.parametrize("recorded", [True, False])
def test_old_creation_cannot_be_reused_or_selected_from_later_completion(make_finalization, recorded):
    old = copy.deepcopy(make_finalization.old)
    old["status"]["completionTimestamp"] = make_finalization.new["metadata"]["creationTimestamp"]
    fin, transport = make_finalization([page([old])], [old])
    if recorded:
        fin.run_record.record_new_backup(old["metadata"]["name"])
    before = fin.state.capture_state_snapshot()
    with pytest.raises(Exception, match="backup_predates_enable|No qualifying"):
        fin._verify_new_backups(timeout=2)
    assert fin.state.capture_state_snapshot() == before
    assert any(r[0] == "LIST" for r in transport.requests)


@pytest.mark.parametrize("fault", [404, 403, "transport"])
def test_recorded_proof_named_transport_fault_never_substitutes_another_backup(make_finalization, fault):
    from kubernetes.client.rest import ApiException

    new = make_finalization.new
    error = RuntimeError("transport") if fault == "transport" else ApiException(status=fault)
    fin, transport = make_finalization([page([new])], [error])
    fin.run_record.record_new_backup(new["metadata"]["name"])
    before = fin.state.capture_state_snapshot()
    with pytest.raises(Exception, match="recorded_backup_unavailable"):
        fin._verify_new_backups(timeout=2)
    assert fin.state.capture_state_snapshot() == before
    assert any(r[0] == "GET" for r in transport.requests)


@pytest.mark.parametrize(
    "changed_field,changed_value",
    [
        ("uid", "replacement-uid"),
        ("creationTimestamp", "2020-01-01T00:00:00Z"),
        ("labels", {}),
    ],
)
def test_terminal_named_object_rechecks_uid_creation_and_ownership(make_finalization, changed_field, changed_value):
    new = copy.deepcopy(make_finalization.new)
    # A neutral name makes removal of the ownership label actually remove ownership.
    new["metadata"]["name"] = "proof-backup"
    terminal = copy.deepcopy(new)
    terminal["metadata"][changed_field] = changed_value
    fin, transport = make_finalization([page([new])], [terminal])
    with pytest.raises(Exception, match="backup_identity_changed|backup_predates_enable|backup_not_acm_owned"):
        fin._verify_new_backups(timeout=2)
    assert fin.run_record.new_backup() is None
    assert any(r[0] == "GET" for r in transport.requests)


def test_pending_backup_must_reach_clean_completion_with_same_uid(make_finalization):
    new = make_finalization.new
    pending = copy.deepcopy(new)
    pending["status"]["phase"] = "InProgress"
    fin, transport = make_finalization([page([pending])], [pending, new])
    fin._verify_new_backups(timeout=5)
    assert fin.run_record.new_backup() == new["metadata"]["name"]
    assert sum(r[0] == "GET" for r in transport.requests) == 2


def test_terminal_disappearance_never_falls_back_to_cached_pending_object(make_finalization):
    from kubernetes.client.rest import ApiException

    new = copy.deepcopy(make_finalization.new)
    new["status"]["phase"] = "InProgress"
    fin, _ = make_finalization([page([new])], [new, ApiException(status=404)])
    with pytest.raises(Exception, match="recorded_backup_unavailable"):
        fin._verify_new_backups(timeout=5)
    assert fin.run_record.new_backup() is None


@pytest.mark.parametrize("name", [None, "", False, 0, [], {}])
def test_present_malformed_recorded_name_never_selects_replacement(make_finalization, name):
    fin, _ = make_finalization([page([make_finalization.new])])
    fin.state._set_config("post_switchover_backup_name", name)
    before = fin.state.capture_state_snapshot()
    with pytest.raises(Exception, match="invalid_backup_identity"):
        fin._verify_new_backups(timeout=5)
    assert fin.state.capture_state_snapshot() == before


@pytest.mark.parametrize("phase,errors", [("Failed", 0), ("PartiallyFailed", 1), ("Completed", 1)])
@pytest.mark.parametrize("sibling_first", [False, True])
def test_unnamed_selection_proves_earliest_detectable_without_unrelated_health_veto(
    make_finalization, phase, errors, sibling_first
):
    selected = copy.deepcopy(make_finalization.new)
    sibling = copy.deepcopy(selected)
    sibling["metadata"].update(
        name="acm-resources-unrelated", uid="unrelated-uid", creationTimestamp=datetime.now(timezone.utc).isoformat()
    )
    sibling["status"].update(phase=phase, errors=errors)
    inventory = [sibling, selected] if sibling_first else [selected, sibling]
    fin, transport = make_finalization([page(inventory)], [selected])
    fin._verify_new_backups(timeout=2)
    assert fin.run_record.new_backup() == selected["metadata"]["name"]
    assert [r[1] for r in transport.requests if r[0] == "GET"] == [selected["metadata"]["name"]]


@pytest.mark.parametrize("recorded", [False, True])
def test_selected_unhealthy_candidate_never_falls_back_to_healthy_sibling(make_finalization, recorded):
    selected = copy.deepcopy(make_finalization.new)
    selected["status"]["errors"] = 1
    later = copy.deepcopy(selected)
    later["metadata"].update(
        name="acm-resources-later", uid="later-uid", creationTimestamp=datetime.now(timezone.utc).isoformat()
    )
    later["status"]["errors"] = 0
    fin, _ = make_finalization([page([later, selected])], [selected])
    if recorded:
        fin.run_record.record_new_backup(selected["metadata"]["name"])
    before = fin.state.capture_state_snapshot()
    with pytest.raises(Exception, match="backup_errors"):
        fin._verify_new_backups(timeout=2)
    assert fin.state.capture_state_snapshot() == before


def test_lone_failed_backup_cannot_be_selected_as_continuity(make_finalization):
    failed = copy.deepcopy(make_finalization.new)
    failed["status"]["phase"] = "Failed"
    fin, _ = make_finalization([page([failed])], [failed])
    with pytest.raises(Exception, match="No qualifying|backup_not_completed"):
        fin._verify_new_backups(timeout=2)
    assert fin.run_record.new_backup() is None
