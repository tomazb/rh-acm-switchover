"""Tests for the RunRecord facade — the named, typed cross-phase interface.

All tests go through the public interface only: no raw key literals, no
reaching into StateManager internals beyond constructing it.
"""

import argparse
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from acm_switchover import _prepare_runtime
from lib import run_record as run_record_module
from lib.cli_outcomes import CliOperationHooks, run_operation_mode
from lib.migration_evidence import MigrationEvidenceError
from lib.migration_journal import validate_journal_transition
from lib.run_record import ErrorRecord, HubFacts, ManagedClusterExpectation, RunRecord, RunSummary, StepRecord
from lib.utils import Phase, StateLoadError, StateManager
from lib.workflow import CompletedStateConfig, FailedStateConfig, handle_completed_state, handle_failed_state

VECTORS = Path(__file__).resolve().parent / "fixtures" / "r4_04_migration_evidence_vectors.json"


@pytest.fixture
def state(tmp_path):
    return StateManager(str(tmp_path / "switchover-test.json"))


@pytest.fixture
def record(state):
    return RunRecord(state)


class TestHubFacts:
    def test_defaults_before_recording(self, record):
        facts = record.hub_facts()
        assert facts == HubFacts()
        assert facts.primary_version == "unknown"
        assert facts.has_observability is False

    def test_round_trip(self, record):
        written = HubFacts(
            primary_version="2.13.2",
            primary_observability_detected=True,
            primary_has_observability=True,
            secondary_version="2.14.0",
            secondary_observability_detected=False,
            secondary_has_observability=False,
            has_observability=True,
        )
        record.record_hub_facts(written)
        assert record.hub_facts() == written

    def test_survives_reload(self, state, record):
        record.record_hub_facts(HubFacts(secondary_version="2.14.0"))
        reloaded = RunRecord(StateManager(state.state_file))
        assert reloaded.hub_facts().secondary_version == "2.14.0"


class TestManagedClusterExpectation:
    def test_default_before_recording(self, record):
        assert record.managed_cluster_expectation() == ManagedClusterExpectation()

    def test_round_trip_normalizes_to_tuple(self, record):
        record.record_managed_cluster_expectation(
            names=["cluster-a", "cluster-b"], count=2, mode="derived_from_preflight"
        )
        exp = record.managed_cluster_expectation()
        assert exp.names == ("cluster-a", "cluster-b")
        assert exp.count == 2
        assert exp.mode == "derived_from_preflight"


class TestPreflightResults:
    def test_record_writes_results_and_summary(self, state, record):
        results = [{"check": "versions", "status": "pass", "message": "ok"}]
        record.record_preflight_results(results, passed=True, critical_failures=0)
        # Interface-only persistence: the captured state snapshot keeps today's key names.
        snapshot = state.capture_state_snapshot()
        assert snapshot["config"]["preflight_results"] == results
        assert snapshot["config"]["preflight_summary"] == {
            "passed": True,
            "critical_failures": 0,
            "total": 1,
        }


class TestAutoImportOverride:
    def test_no_obligation_by_default(self, record):
        assert record.auto_import_override_pending() is False

    def test_record_then_clear(self, record):
        record.record_auto_import_override()
        assert record.auto_import_override_pending() is True
        record.clear_auto_import_override()
        assert record.auto_import_override_pending() is False


class TestSavedBackupSchedule:
    def test_none_by_default(self, record):
        assert record.saved_backup_schedule() is None

    def test_round_trip(self, record):
        bs = {"metadata": {"name": "schedule-acm"}, "spec": {"veleroSchedule": "0 */4 * * *"}}
        record.record_saved_backup_schedule(bs)
        assert record.saved_backup_schedule() == bs


class TestBackupWatch:
    def test_defaults(self, record):
        assert record.backup_watch_started_at() is None
        assert record.new_backup() is None

    def test_watch_start_resets_detection(self, state, record):
        record.record_new_backup("acm-backup-1")
        record.record_backup_watch_started("2026-08-02T18:00:00+00:00")
        assert record.backup_watch_started_at() == "2026-08-02T18:00:00+00:00"
        # A new watch window invalidates the previous detection flag but
        # keeps the last recorded name for the resume fast path.
        assert state.capture_state_snapshot()["config"]["new_backup_detected"] is False
        assert record.new_backup() == "acm-backup-1"

    def test_record_new_backup(self, state, record):
        record.record_new_backup("acm-backup-2")
        assert record.new_backup() == "acm-backup-2"
        snapshot = state.capture_state_snapshot()
        assert snapshot["config"]["new_backup_detected"] is True
        assert snapshot["config"]["post_switchover_backup_name"] == "acm-backup-2"


class TestArchivedRestores:
    def test_record(self, state, record):
        restores = [{"name": "restore-acm-passive-sync", "phase": "Finished"}]
        record.record_archived_restores(restores)
        assert state.capture_state_snapshot()["config"]["archived_restores"] == restores


class TestPreActivationVeleroRestore:
    def test_none_by_default(self, record):
        assert record.pre_activation_velero_restore() is None

    def test_round_trip_and_clear(self, record):
        record.record_pre_activation_velero_restore("velero-restore-1")
        assert record.pre_activation_velero_restore() == "velero-restore-1"
        record.record_pre_activation_velero_restore(None)
        assert record.pre_activation_velero_restore() is None


class TestResumeStartPhase:
    def test_record_writes_resume_summary_shape(self, state, record):
        record.record_resume_start_phase("activation")
        snapshot = state.capture_state_snapshot()
        assert snapshot["config"]["resume_summary"] == {"resume_start_phase": "activation"}


class TestRunSummary:
    def test_from_snapshot_happy_path(self):
        snapshot = {
            "current_phase": "finalization",
            "completed_steps": [
                {"name": "preflight_validation", "phase": "preflight", "timestamp": "t1"},
                {"name": "activate_managed_clusters", "phase": "activation", "timestamp": "t2"},
            ],
            "errors": [{"error": "boom", "phase": "activation", "timestamp": "t3"}],
            "config": {"preflight_results": [{"check": "versions", "status": "pass"}]},
        }
        summary = RunSummary.from_snapshot(snapshot)
        assert summary.current_phase == "finalization"
        assert summary.completed_steps == (
            StepRecord(name="preflight_validation", phase="preflight", timestamp="t1"),
            StepRecord(name="activate_managed_clusters", phase="activation", timestamp="t2"),
        )
        assert summary.errors == (ErrorRecord(error="boom", phase="activation", timestamp="t3"),)
        assert summary.preflight_results == ({"check": "versions", "status": "pass"},)

    @pytest.mark.parametrize(
        "snapshot",
        [
            None,
            "not a dict",
            {},
            {"completed_steps": "not a list", "errors": 7, "config": []},
            {"completed_steps": [None, "str", {"phase": 3}], "errors": [None, []]},
        ],
    )
    def test_from_snapshot_never_raises_on_malformed_input(self, snapshot):
        summary = RunSummary.from_snapshot(snapshot)
        assert isinstance(summary, RunSummary)
        for step in summary.completed_steps:
            assert isinstance(step, StepRecord)
        for err in summary.errors:
            assert isinstance(err, ErrorRecord)

    def test_from_snapshot_degrades_field_values(self):
        summary = RunSummary.from_snapshot(
            {
                "current_phase": 5,
                "completed_steps": [{"name": 3, "phase": 7, "timestamp": 9}],
                "errors": [{"error": [], "phase": 1, "timestamp": {}}],
                "config": {"preflight_results": ["junk", {"ok": 1}]},
            }
        )
        assert summary.current_phase is None
        assert summary.completed_steps == (StepRecord(name="", phase=None, timestamp=None),)
        assert summary.errors == (ErrorRecord(error="", phase=None, timestamp=None),)
        assert summary.preflight_results == ({"ok": 1},)

    def test_from_snapshot_non_list_preflight_results(self):
        assert RunSummary.from_snapshot({"config": {"preflight_results": "nope"}}).preflight_results == ()

    def test_live_summary_matches_snapshot_summary(self, state, record):
        state.mark_step_completed("preflight_validation")
        record.record_preflight_results([{"check": "versions", "status": "pass"}], passed=True, critical_failures=0)
        live = record.summary()
        offline = RunSummary.from_snapshot(state.capture_state_snapshot())
        assert live == offline
        assert live.completed_steps[0].name == "preflight_validation"


class TestInterfaceOnlyPersistence:
    """A state file written by the pre-RunRecord tool loads identically.

    The refactor is interface-only: the on-disk JSON schema and every config
    key name are unchanged, so a state file produced by the previous release
    stays resumable and every cross-phase fact is still readable — now through
    named RunRecord operations instead of raw key lookups.
    """

    def test_legacy_state_file_reads_through_run_record(self, tmp_path):
        # Shape produced by the previous release: raw keys in config.
        legacy = {
            "version": "1.0",
            "current_phase": "finalization",
            "completed_steps": [{"name": "activate_managed_clusters", "phase": "activation", "timestamp": "t"}],
            "errors": [],
            "config": {
                "primary_version": "2.13.2",
                "primary_has_observability": True,
                "secondary_version": "2.14.0",
                "auto_import_strategy_set": True,
                "saved_backup_schedule": {"metadata": {"name": "schedule-acm"}},
                "post_switchover_backup_name": "acm-backup-9",
                "new_backup_detected": True,
            },
        }
        state_file = tmp_path / "switchover-legacy.json"
        state_file.write_text(json.dumps(legacy))

        record = RunRecord(StateManager(str(state_file)))
        assert record.hub_facts().primary_version == "2.13.2"
        assert record.hub_facts().primary_has_observability is True
        assert record.auto_import_override_pending() is True
        assert record.saved_backup_schedule() == {"metadata": {"name": "schedule-acm"}}
        assert record.new_backup() == "acm-backup-9"


def _transition_input(case_id: str) -> dict:
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
    return copy.deepcopy(next(case["input"] for case in cases if case["id"] == case_id))


@pytest.fixture
def first_write():
    """A pre-mutation journal: the legal freeze write."""
    journal = _transition_input("transition-pre-to-completed")["previous"]
    validate_journal_transition(None, journal)
    return journal


@pytest.fixture
def next_write():
    """A legal successor of first_write."""
    return _transition_input("transition-pre-to-completed")["candidate"]


@pytest.fixture
def rewrite():
    """Valid on its own, but rewrites first_write's frozen run_id."""
    return _transition_input("transition-run-id-changed-blocks")["candidate"]


def _state_file_with_journal(tmp_path, stored):
    """A state file whose migration journal slot holds `stored` verbatim."""
    path = tmp_path / "switchover-journal.json"
    StateManager(str(path))  # a well-formed fresh state to edit
    raw = json.loads(path.read_text())
    raw["config"][run_record_module._KEY_MIGRATION_BACKUPS] = stored
    path.write_text(json.dumps(raw))
    return path


class TestMigrationBackups:
    """R4-04 plan Task 3: the strict journal facade (amendment sections 2 and 10)."""

    def test_absent_before_the_first_write(self, record):
        assert record.migration_backups() is None

    def test_first_write_round_trips_and_survives_reload(self, state, record, first_write):
        record.record_migration_backups(first_write)
        assert record.migration_backups() == first_write
        assert RunRecord(StateManager(state.state_file)).migration_backups() == first_write

    def test_the_write_is_durable_on_return(self, state, record, first_write):
        record.record_migration_backups(first_write)
        on_disk = json.loads(open(state.state_file, encoding="utf-8").read())
        assert on_disk["config"][run_record_module._KEY_MIGRATION_BACKUPS] == first_write

    def test_every_write_forces_a_critical_flush(self, state, record, first_write):
        record.record_migration_backups(first_write)
        with patch.object(state, "flush_state", wraps=state.flush_state) as flush:
            record.record_migration_backups(copy.deepcopy(first_write))
        flush.assert_called_once_with()

    def test_reads_and_writes_are_detached(self, record, first_write):
        record.record_migration_backups(first_write)
        first_write["run_id"] = "edited"
        read = record.migration_backups()
        assert read["run_id"] != "edited"
        read["run_id"] = "edited"
        assert record.migration_backups()["run_id"] != "edited"

    @pytest.mark.parametrize("stored", [None, {}, [], "", 0, False, {"schema_version": 2}])
    def test_a_present_invalid_key_raises_and_is_never_absent(self, tmp_path, stored):
        record = RunRecord(StateManager(str(_state_file_with_journal(tmp_path, stored))))
        with pytest.raises(MigrationEvidenceError):
            record.migration_backups()

    def test_an_unknown_schema_version_blocks(self, tmp_path, first_write):
        first_write["schema_version"] = 3
        record = RunRecord(StateManager(str(_state_file_with_journal(tmp_path, first_write))))
        with pytest.raises(MigrationEvidenceError) as exc_info:
            record.migration_backups()
        assert exc_info.value.code == "unsupported_schema_version"

    def test_the_first_write_must_be_a_freeze_write(self, record, next_write):
        with pytest.raises(MigrationEvidenceError) as exc_info:
            record.record_migration_backups(next_write)
        assert exc_info.value.code == "invalid_freeze_write"
        assert record.migration_backups() is None

    def test_a_legal_transition_replaces_the_whole_value(self, record, first_write, next_write):
        record.record_migration_backups(first_write)
        record.record_migration_backups(next_write)
        assert record.migration_backups() == next_write

    def test_retry_never_rewrites_a_frozen_field(self, state, record, first_write, rewrite):
        record.record_migration_backups(first_write)
        with pytest.raises(MigrationEvidenceError) as exc_info:
            record.record_migration_backups(rewrite)
        assert exc_info.value.code == "frozen_field_changed"
        assert RunRecord(StateManager(state.state_file)).migration_backups() == first_write

    def test_an_identical_write_is_idempotent(self, record, first_write):
        record.record_migration_backups(first_write)
        record.record_migration_backups(copy.deepcopy(first_write))
        assert record.migration_backups() == first_write

    def test_an_invalid_candidate_is_refused_before_the_stored_one_is_read(self, tmp_path, first_write):
        record = RunRecord(StateManager(str(_state_file_with_journal(tmp_path, {"broken": True}))))
        first_write["schema_version"] = 1
        with pytest.raises(MigrationEvidenceError) as exc_info:
            record.record_migration_backups(first_write)
        assert exc_info.value.code == "unsupported_schema_version"

    def test_an_invalid_stored_journal_blocks_every_write(self, tmp_path, first_write):
        path = _state_file_with_journal(tmp_path, None)
        record = RunRecord(StateManager(str(path)))
        with pytest.raises(MigrationEvidenceError):
            record.record_migration_backups(first_write)
        assert json.loads(path.read_text())["config"][run_record_module._KEY_MIGRATION_BACKUPS] is None

    def test_a_critical_write_failure_propagates(self, record, first_write):
        with patch("lib.utils._fsync_directory", side_effect=OSError("EIO")):
            with pytest.raises(OSError, match="EIO"):
                record.record_migration_backups(first_write)

    def test_a_failed_write_before_replace_propagates_and_keeps_the_old_file(self, state, record, first_write):
        with patch("lib.utils.os.replace", side_effect=OSError("read-only file system")):
            with pytest.raises(OSError, match="read-only"):
                record.record_migration_backups(first_write)
        assert RunRecord(StateManager(state.state_file)).migration_backups() is None


class TestMigrationStoreOutcomes:
    """Amendment section 2: corrupt and unreadable block and are never an absent journal."""

    @pytest.mark.parametrize("text", ["{not json", "[]", '"text"'])
    def test_a_corrupt_state_file_blocks_across_invocations_and_is_preserved(self, tmp_path, text):
        path = tmp_path / "switchover-corrupt.json"
        path.write_text(text)
        for _ in range(2):
            with pytest.raises(StateLoadError):
                StateManager(str(path))
        assert path.read_text() == text
        copies = list(tmp_path.glob("switchover-corrupt.json.corrupt.*"))
        assert copies and all(copy_path.read_text() == text for copy_path in copies)

    def test_a_failed_forensic_copy_still_blocks_and_keeps_the_original(self, tmp_path):
        path = tmp_path / "switchover-corrupt.json"
        path.write_text("{not json")
        with patch("lib.utils.shutil.copy2", side_effect=OSError("disk full")):
            with pytest.raises(StateLoadError):
                StateManager(str(path))
        assert path.read_text() == "{not json"
        assert not list(tmp_path.glob("switchover-corrupt.json.corrupt.*"))


def _cli_args(**overrides):
    defaults = {
        "min_managed_clusters": None,
        "dry_run": False,
        "argocd_resume_only": False,
        "decommission": False,
        "primary_context": "hub-a",
        "secondary_context": "hub-b",
        "reset_state": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestResetStateMakesTheJournalAbsent:
    """Amendment section 10: --reset-state is the explicit fresh-run boundary."""

    def test_reset_state_recovers_a_corrupt_state_file(self, tmp_path):
        path = tmp_path / "switchover-corrupt.json"
        path.write_text("{not json")
        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            runtime = _prepare_runtime(_cli_args(), logging.getLogger("test"), str(path))
        assert RunRecord(runtime.state).migration_backups() is None
        assert json.loads(path.read_text())["config"] == {}

    def test_reset_state_discards_a_recorded_journal(self, tmp_path, first_write):
        path = tmp_path / "switchover-journal.json"
        state = StateManager(str(path))
        state.ensure_contexts("hub-a", "hub-b")
        RunRecord(state).record_migration_backups(first_write)
        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            runtime = _prepare_runtime(_cli_args(), logging.getLogger("test"), str(path))
        assert RunRecord(runtime.state).migration_backups() is None

    def test_an_ordinary_retry_keeps_the_journal(self, tmp_path, first_write):
        path = tmp_path / "switchover-journal.json"
        state = StateManager(str(path))
        state.ensure_contexts("hub-a", "hub-b")
        RunRecord(state).record_migration_backups(first_write)
        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            runtime = _prepare_runtime(_cli_args(reset_state=False), logging.getLogger("test"), str(path))
        assert RunRecord(runtime.state).migration_backups() == first_write


# --- Task 3 review: malformed containers and implicit resets ------------------------


def _write_raw_state(tmp_path, **fields):
    """A fresh state file with top-level `fields` overwritten verbatim."""
    path = tmp_path / "switchover-raw.json"
    StateManager(str(path))
    raw = json.loads(path.read_text())
    raw.update(fields)
    path.write_text(json.dumps(raw))
    return path


class TestMalformedConfigContainer:
    """A config bag that is not a mapping is never read as "no journal"."""

    @pytest.mark.parametrize("container", [[], "", None])
    def test_the_read_blocks(self, tmp_path, container):
        record = RunRecord(StateManager(str(_write_raw_state(tmp_path, config=container))))
        with pytest.raises(run_record_module.StateStructureError):
            record.migration_backups()

    @pytest.mark.parametrize("container", [[], "", None])
    def test_the_write_blocks(self, tmp_path, container, first_write):
        path = _write_raw_state(tmp_path, config=container)
        record = RunRecord(StateManager(str(path)))
        with pytest.raises(run_record_module.StateStructureError):
            record.record_migration_backups(first_write)
        assert json.loads(path.read_text())["config"] == container

    @pytest.mark.parametrize(
        "snapshot, present",
        [({"config": {}}, False), ({"config": {"x": 1}}, False), ({"config": []}, False), ({}, False), ([], False)],
    )
    def test_presence_is_false_without_the_key(self, snapshot, present):
        assert run_record_module.migration_journal_present(snapshot) is present

    @pytest.mark.parametrize("stored", [None, {}, {"schema_version": 2}])
    def test_presence_counts_an_invalid_journal(self, stored):
        snapshot = {"config": {run_record_module._KEY_MIGRATION_BACKUPS: stored}}
        assert run_record_module.migration_journal_present(snapshot) is True


def _journal_state(tmp_path, first_write, **fields):
    """A state file bound to hub-a/hub-b that carries a migration journal."""
    path = tmp_path / "switchover-journal.json"
    state = StateManager(str(path))
    state.ensure_contexts("hub-a", "hub-b")
    RunRecord(state).record_migration_backups(first_write)
    if fields:
        raw = json.loads(path.read_text())
        raw.update(fields)
        path.write_text(json.dumps(raw))
    return path


class _Untouched:
    """The state file bytes and the live in-memory state, captured before a refusal."""

    def __init__(self, path, state):
        self.path, self.state = path, state
        self.bytes = path.read_bytes()
        self.snapshot = state.capture_state_snapshot()

    def assert_unchanged(self):
        assert self.path.read_bytes() == self.bytes
        assert self.state.capture_state_snapshot() == self.snapshot


class TestImplicitResetsKeepTheJournal:
    """Amendment section 10: only --reset-state may drop a recorded journal."""

    def test_a_context_mismatch_refuses_instead_of_resetting(self, tmp_path, first_write):
        path = _journal_state(tmp_path, first_write)
        state = StateManager(str(path))
        untouched = _Untouched(path, state)
        with pytest.raises(run_record_module.MigrationJournalResetRefused, match="--reset-state"):
            state.ensure_contexts("hub-a", "hub-c")
        untouched.assert_unchanged()
        assert RunRecord(StateManager(str(path))).migration_backups() == first_write

    def test_missing_contexts_on_progress_refuse_instead_of_resetting(self, tmp_path, first_write):
        path = _journal_state(
            tmp_path, first_write, contexts={"primary": None, "secondary": None}, current_phase="activation"
        )
        state = StateManager(str(path))
        untouched = _Untouched(path, state)
        with pytest.raises(run_record_module.MigrationJournalResetRefused, match="--reset-state"):
            state.ensure_contexts("hub-a", "hub-b")
        untouched.assert_unchanged()
        assert RunRecord(StateManager(str(path))).migration_backups() == first_write

    def test_an_invalid_journal_also_refuses_the_context_reset(self, tmp_path):
        path = _write_raw_state(
            tmp_path,
            contexts={"primary": "hub-a", "secondary": "hub-b"},
            config={run_record_module._KEY_MIGRATION_BACKUPS: None},
        )
        state = StateManager(str(path))
        untouched = _Untouched(path, state)
        with pytest.raises(run_record_module.MigrationJournalResetRefused, match="--reset-state"):
            state.ensure_contexts("hub-a", "hub-c")
        untouched.assert_unchanged()

    def test_a_journal_free_context_mismatch_still_resets(self, tmp_path):
        path = tmp_path / "switchover-plain.json"
        state = StateManager(str(path))
        state.ensure_contexts("hub-a", "hub-b")
        state.mark_step_completed("preflight_validation")
        state = StateManager(str(path))
        state.ensure_contexts("hub-a", "hub-c")
        assert state.state["completed_steps"] == []
        assert state.state["contexts"] == {"primary": "hub-a", "secondary": "hub-c"}

    def test_the_cli_reports_the_refusal_and_keeps_the_journal(self, tmp_path, first_write):
        path = _journal_state(tmp_path, first_write)
        before = path.read_bytes()
        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            with pytest.raises(SystemExit):
                _prepare_runtime(
                    _cli_args(reset_state=False, secondary_context="hub-c"), logging.getLogger("test"), str(path)
                )
        assert path.read_bytes() == before
        assert RunRecord(StateManager(str(path))).migration_backups() == first_write

    def test_force_on_an_unresumable_failed_state_refuses(self, tmp_path, first_write):
        path = _journal_state(tmp_path, first_write, current_phase="failed", errors=[])
        state = StateManager(str(path))
        config = FailedStateConfig(resumable_phases=(Phase.ACTIVATION,), operation_noun="switchover")
        untouched = _Untouched(path, state)
        with pytest.raises(run_record_module.MigrationJournalResetRefused, match="--reset-state"):
            handle_failed_state(argparse.Namespace(force=True), state, logging.getLogger("test"), config)
        untouched.assert_unchanged()
        assert RunRecord(StateManager(str(path))).migration_backups() == first_write

    def test_force_on_a_stale_completed_state_refuses(self, tmp_path, first_write):
        path = _journal_state(
            tmp_path, first_write, current_phase="completed", last_updated="2020-01-01T00:00:00+00:00"
        )
        state = StateManager(str(path))
        config = CompletedStateConfig(operation_label="Switchover", operation_noun="switchover")
        untouched = _Untouched(path, state)
        with pytest.raises(run_record_module.MigrationJournalResetRefused, match="--reset-state"):
            handle_completed_state(argparse.Namespace(force=True), state, logging.getLogger("test"), config)
        untouched.assert_unchanged()
        assert RunRecord(StateManager(str(path))).migration_backups() == first_write

    def test_journal_free_force_resets_are_unchanged(self, tmp_path):
        failed = _write_raw_state(tmp_path, current_phase="failed", errors=[])
        state = StateManager(str(failed))
        config = FailedStateConfig(resumable_phases=(Phase.ACTIVATION,), operation_noun="switchover")
        handle_failed_state(argparse.Namespace(force=True), state, logging.getLogger("test"), config)
        assert state.get_current_phase() == Phase.INIT

        completed_dir = tmp_path / "completed"
        completed_dir.mkdir()
        completed = _write_raw_state(completed_dir, current_phase="completed", last_updated="2020-01-01T00:00:00+00:00")
        state = StateManager(str(completed))
        config = CompletedStateConfig(operation_label="Switchover", operation_noun="switchover")
        assert handle_completed_state(argparse.Namespace(force=True), state, logging.getLogger("test"), config) is False
        assert state.get_current_phase() == Phase.INIT


class TestResetRefusalWritesNothing:
    """The refusal is not a run error: no path records it into the state file."""

    def test_the_operation_path_does_not_record_the_refusal(self, tmp_path, first_write):
        path = _journal_state(tmp_path, first_write)
        state = StateManager(str(path))
        untouched = _Untouched(path, state)

        def refuse(*_args, **_kwargs):
            raise run_record_module.MigrationJournalResetRefused(
                run_record_module.MIGRATION_JOURNAL_IMPLICIT_RESET_REFUSAL
            )

        hooks = CliOperationHooks(
            bind_runtime_hub_identities=lambda *a, **k: None,
            run_argocd_resume_only=refuse,
            execute_operation=refuse,
            write_python_report=lambda *a, **k: None,
            gitops_reporter_factory=lambda: SimpleNamespace(print_report=lambda: None),
        )
        exit_code = run_operation_mode(
            argparse.Namespace(argocd_resume_only=False),
            state,
            None,
            None,
            logging.getLogger("test"),
            should_bind_state=True,
            should_record_state_errors=True,
            hooks=hooks,
            exit_success=0,
            exit_failure=1,
            exit_interrupt=130,
        )
        assert exit_code == 1
        untouched.assert_unchanged()

    def test_a_dry_run_cli_refusal_leaves_the_file_untouched(self, tmp_path, first_write):
        path = _journal_state(tmp_path, first_write)
        before = path.read_bytes()
        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            with patch.object(StateManager, "restore_state_snapshot", side_effect=AssertionError("nothing to restore")):
                with pytest.raises(SystemExit) as exc_info:
                    _prepare_runtime(
                        _cli_args(reset_state=False, dry_run=True, secondary_context="hub-c"),
                        logging.getLogger("test"),
                        str(path),
                    )
        assert exc_info.value.code == 1
        assert path.read_bytes() == before
