"""Tests for the RunRecord facade — the named, typed cross-phase interface.

All tests go through the public interface only: no raw key literals, no
reaching into StateManager internals beyond constructing it.
"""

import pytest

from lib.run_record import HubFacts, ManagedClusterExpectation, RunRecord
from lib.utils import StateManager


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
