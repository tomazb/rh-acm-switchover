"""R4-04 migration journal checkpoint vocabulary (plan Task 3; amendment sections 2 and 10)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    CHECKPOINT_VALID_STATUSES,
    KEY_MIGRATION_BACKUPS,
    MIGRATION_JOURNAL_ABSENT,
    MIGRATION_JOURNAL_INVALID,
    MIGRATION_JOURNAL_VALID,
    CheckpointStructureError,
    MigrationRewindRefused,
    check_migration_rewind,
    checkpoint_structure_error,
    classify_migration_backups,
    migration_backups,
    record_migration_backups,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    MigrationEvidenceError,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_journal import (
    validate_journal_transition,
)

FIXTURE_PATH = Path(__file__).resolve().parents[7] / "tests" / "fixtures" / "r4_04_migration_evidence_vectors.json"


def _transition_input(case_id: str) -> dict:
    if not FIXTURE_PATH.is_file():
        pytest.skip("the shared R4-04 vectors live in the repository checkout")
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]
    return copy.deepcopy(next(case["input"] for case in cases if case["id"] == case_id))


@pytest.fixture
def first_write() -> dict:
    """A pre-mutation journal: the legal freeze write."""
    journal = _transition_input("transition-pre-to-completed")["previous"]
    validate_journal_transition(None, journal)
    return journal


@pytest.fixture
def next_write() -> dict:
    """A legal successor of first_write."""
    return _transition_input("transition-pre-to-completed")["candidate"]


@pytest.fixture
def rewrite() -> dict:
    """A valid journal that illegally rewrites first_write's frozen run_id."""
    return _transition_input("transition-run-id-changed-blocks")["candidate"]


def _checkpoint(operational_data=None) -> dict:
    checkpoint = {"schema_version": "2.0", "phase": "activation", "completed_phases": ["preflight", "primary_prep"]}
    if operational_data is not None:
        checkpoint["operational_data"] = operational_data
    return checkpoint


def test_update_is_a_valid_checkpoint_status():
    assert CHECKPOINT_VALID_STATUSES == {"enter", "pass", "fail", "reset", "update"}


def test_the_journal_key_is_the_documented_name():
    assert KEY_MIGRATION_BACKUPS == "migration_backups"


# --- store structure ------------------------------------------------------------


@pytest.mark.parametrize("decoded", [[], "text", 3, None, True])
def test_a_non_object_checkpoint_is_structurally_corrupt(decoded):
    assert checkpoint_structure_error(decoded) is not None


@pytest.mark.parametrize(
    "checkpoint",
    [
        {},
        {"operational_data": {}},
        _checkpoint({"argocd_run_id": "x"}),
        {"completed_phases": "not-validated"},
        # enter heals a malformed container on the ordinary path; only journal reads refuse it
        {"operational_data": None},
    ],
)
def test_the_structure_check_adds_no_required_keys(checkpoint):
    assert checkpoint_structure_error(checkpoint) is None


@pytest.mark.parametrize(
    "checkpoint", [[], {"operational_data": None}, {"operational_data": []}, {"operational_data": ""}]
)
def test_the_classifier_refuses_an_unreadable_store(checkpoint):
    with pytest.raises(CheckpointStructureError):
        classify_migration_backups(checkpoint)
    with pytest.raises(CheckpointStructureError):
        migration_backups(checkpoint)


# --- journal outcome inside a readable store --------------------------------------


@pytest.mark.parametrize("checkpoint", [_checkpoint(), _checkpoint({}), _checkpoint({"argocd_run_id": "x"})])
def test_a_missing_key_is_absent(checkpoint):
    assert classify_migration_backups(checkpoint) == (MIGRATION_JOURNAL_ABSENT, None)
    assert migration_backups(checkpoint) is None


def test_a_valid_journal_is_valid_and_returned_detached(first_write):
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: first_write})
    outcome, journal = classify_migration_backups(checkpoint)
    assert outcome == MIGRATION_JOURNAL_VALID
    assert journal == first_write
    journal["run_id"] = "edited"
    assert checkpoint["operational_data"][KEY_MIGRATION_BACKUPS]["run_id"] != "edited"
    assert migration_backups(checkpoint) == first_write


@pytest.mark.parametrize("stored", [None, {}, [], "", 0, False])
def test_a_present_malformed_key_is_invalid_never_absent(stored):
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: stored})
    outcome, error = classify_migration_backups(checkpoint)
    assert outcome == MIGRATION_JOURNAL_INVALID
    assert isinstance(error, MigrationEvidenceError)
    with pytest.raises(MigrationEvidenceError):
        migration_backups(checkpoint)


def test_an_unknown_schema_version_is_invalid(first_write):
    first_write["schema_version"] = 3
    outcome, error = classify_migration_backups(_checkpoint({KEY_MIGRATION_BACKUPS: first_write}))
    assert outcome == MIGRATION_JOURNAL_INVALID
    assert error.code == "unsupported_schema_version"


def test_a_partial_journal_is_invalid(first_write):
    del first_write["cleanup"]
    outcome, error = classify_migration_backups(_checkpoint({KEY_MIGRATION_BACKUPS: first_write}))
    assert outcome == MIGRATION_JOURNAL_INVALID
    assert error.code == "malformed_journal"


# --- the writer -----------------------------------------------------------------


def test_the_first_write_must_be_a_legal_freeze_write(next_write):
    checkpoint = _checkpoint({"argocd_run_id": "x"})
    with pytest.raises(MigrationEvidenceError) as exc_info:
        record_migration_backups(checkpoint, next_write)
    assert exc_info.value.code == "invalid_freeze_write"
    assert checkpoint["operational_data"] == {"argocd_run_id": "x"}


def test_a_first_write_stores_one_detached_top_level_value(first_write):
    checkpoint = _checkpoint({"argocd_run_id": "x"})
    record_migration_backups(checkpoint, first_write)
    assert checkpoint["operational_data"] == {"argocd_run_id": "x", KEY_MIGRATION_BACKUPS: first_write}
    first_write["run_id"] = "edited"
    assert checkpoint["operational_data"][KEY_MIGRATION_BACKUPS]["run_id"] != "edited"


def test_a_first_write_creates_missing_operational_data(first_write):
    checkpoint = _checkpoint()
    record_migration_backups(checkpoint, first_write)
    assert checkpoint["operational_data"] == {KEY_MIGRATION_BACKUPS: first_write}


def test_a_legal_transition_replaces_the_whole_value(first_write, next_write):
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: first_write})
    record_migration_backups(checkpoint, next_write)
    assert checkpoint["operational_data"][KEY_MIGRATION_BACKUPS] == next_write


def test_a_frozen_field_rewrite_is_refused_and_leaves_the_journal(first_write, rewrite):
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: first_write})
    with pytest.raises(MigrationEvidenceError) as exc_info:
        record_migration_backups(checkpoint, rewrite)
    assert exc_info.value.code == "frozen_field_changed"
    assert checkpoint["operational_data"][KEY_MIGRATION_BACKUPS] == first_write


def test_an_invalid_candidate_is_refused_before_the_previous_is_read(first_write):
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: {"broken": True}})
    candidate = copy.deepcopy(first_write)
    candidate["schema_version"] = 1
    with pytest.raises(MigrationEvidenceError) as exc_info:
        record_migration_backups(checkpoint, candidate)
    assert exc_info.value.code == "unsupported_schema_version"


def test_an_invalid_stored_journal_blocks_every_write(first_write):
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: None})
    with pytest.raises(MigrationEvidenceError):
        record_migration_backups(checkpoint, first_write)
    assert checkpoint["operational_data"] == {KEY_MIGRATION_BACKUPS: None}


def test_the_writer_refuses_an_unreadable_store(first_write):
    with pytest.raises(CheckpointStructureError):
        record_migration_backups({"operational_data": []}, first_write)


# --- reset / rewind (amendment section 10) --------------------------------------


@pytest.mark.parametrize("reset_from", ["activation", "post_activation", "finalization"])
@pytest.mark.parametrize("prunes", [True, False])
def test_a_rewind_at_or_after_activation_retains_a_valid_journal(first_write, reset_from, prunes):
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: first_write})
    assert check_migration_rewind(checkpoint, reset_from, prunes=prunes) == MIGRATION_JOURNAL_VALID
    assert checkpoint["operational_data"][KEY_MIGRATION_BACKUPS] == first_write


@pytest.mark.parametrize("reset_from", ["preflight", "primary_prep"])
def test_a_pre_freeze_rewind_with_a_valid_journal_is_refused(first_write, reset_from):
    with pytest.raises(MigrationRewindRefused, match="full checkpoint reset"):
        check_migration_rewind(_checkpoint({KEY_MIGRATION_BACKUPS: first_write}), reset_from, prunes=True)


@pytest.mark.parametrize("reset_from", ["preflight", "primary_prep"])
def test_a_stale_pre_freeze_reset_from_that_prunes_nothing_keeps_a_valid_journal(first_write, reset_from):
    """reset_from stays in the checkpoint config for every later task of the run; once
    it no longer names a completed phase, no rewind happens and the journal stands."""
    checkpoint = _checkpoint({KEY_MIGRATION_BACKUPS: first_write})
    assert check_migration_rewind(checkpoint, reset_from, prunes=False) == MIGRATION_JOURNAL_VALID


@pytest.mark.parametrize("reset_from", ["preflight", "primary_prep", "activation", "post_activation", "finalization"])
@pytest.mark.parametrize("prunes", [True, False])
def test_an_invalid_journal_blocks_every_rewind(reset_from, prunes):
    with pytest.raises(MigrationRewindRefused, match="invalid"):
        check_migration_rewind(_checkpoint({KEY_MIGRATION_BACKUPS: {}}), reset_from, prunes=prunes)


@pytest.mark.parametrize("reset_from", ["preflight", "primary_prep", "activation"])
def test_a_rewind_without_a_journal_is_unaffected(reset_from):
    assert check_migration_rewind(_checkpoint({}), reset_from, prunes=True) == MIGRATION_JOURNAL_ABSENT


def test_a_rewind_refuses_an_unreadable_store():
    with pytest.raises(CheckpointStructureError):
        check_migration_rewind({"operational_data": "x"}, "activation", prunes=True)
