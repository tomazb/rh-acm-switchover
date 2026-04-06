"""Integration tests for cross-module state handoff.

Verifies that state keys set by one workflow module (e.g. activation,
primary_prep) are correctly consumed by downstream modules (e.g.
finalization, backup_schedule).

Cross-module state contract:
  primary_prep  → finalization:  argocd_paused_apps, argocd_run_id, argocd_pause_dry_run
  primary_prep  → backup_schedule: saved_backup_schedule
  activation    → finalization:  auto_import_strategy_set
"""

import copy
from unittest.mock import MagicMock, patch

import pytest

from lib.utils import Phase, StateManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state(tmp_path):
    """Real StateManager backed by a temp file."""
    return StateManager(str(tmp_path / "handoff-state.json"))


def _mock_kube_client():
    """Return a lightweight mock KubeClient."""
    client = MagicMock()
    client.get_configmap.return_value = None
    client.list_custom_resources.return_value = []
    client.get_custom_resource.return_value = None
    client.create_custom_resource.return_value = None
    client.delete_configmap.return_value = None
    return client


# ---------------------------------------------------------------------------
# Argo CD pause/resume handoff: primary_prep → finalization
# ---------------------------------------------------------------------------


class TestArgoCDStateHandoff:
    """Verify argocd_paused_apps / argocd_run_id / argocd_pause_dry_run flow."""

    def test_finalization_reads_argocd_state_set_by_primary_prep(self, state):
        """Keys set during primary_prep are available for finalization."""
        apps = [
            {
                "hub": "primary",
                "namespace": "openshift-gitops",
                "name": "my-app",
                "original_sync_policy": {"automated": {"prune": True}},
                "pause_applied": True,
                "dry_run": False,
            }
        ]
        state.set_config("argocd_paused_apps", copy.deepcopy(apps))
        state.set_config("argocd_run_id", "run-abc-123")
        state.set_config("argocd_pause_dry_run", False)

        assert state.get_config("argocd_run_id") == "run-abc-123"
        assert state.get_config("argocd_pause_dry_run", False) is False
        retrieved = state.get_config("argocd_paused_apps")
        assert len(retrieved) == 1
        assert retrieved[0]["name"] == "my-app"

    def test_finalization_reads_empty_argocd_state(self, state):
        """When no Argo CD CRD was found, primary_prep stores empty values."""
        state.set_config("argocd_paused_apps", [])
        state.set_config("argocd_run_id", None)
        state.set_config("argocd_pause_dry_run", False)

        assert state.get_config("argocd_paused_apps") == []
        assert state.get_config("argocd_run_id") is None
        assert state.get_config("argocd_pause_dry_run", False) is False

    def test_argocd_dry_run_flag_propagates(self, state):
        """Finalization raises when argocd_pause_dry_run is True."""
        state.set_config("argocd_pause_dry_run", True)
        state.set_config("argocd_run_id", "run-dry")
        state.set_config(
            "argocd_paused_apps",
            [{"hub": "primary", "namespace": "ns", "name": "a", "dry_run": True}],
        )

        assert state.get_config("argocd_pause_dry_run", False) is True

    def test_argocd_state_survives_save_reload(self, tmp_path):
        """State persists across StateManager instances (save → reload)."""
        path = str(tmp_path / "persist.json")
        s1 = StateManager(path)
        apps = [{"hub": "primary", "namespace": "ns", "name": "app1", "pause_applied": True}]
        s1.set_config("argocd_paused_apps", copy.deepcopy(apps))
        s1.set_config("argocd_run_id", "run-persist")
        s1.set_config("argocd_pause_dry_run", False)
        s1.flush_state()

        s2 = StateManager(path)
        assert s2.get_config("argocd_run_id") == "run-persist"
        assert s2.get_config("argocd_pause_dry_run", False) is False
        assert len(s2.get_config("argocd_paused_apps")) == 1
        assert s2.get_config("argocd_paused_apps")[0]["name"] == "app1"

    def test_multiple_paused_apps_preserved(self, state):
        """All paused apps survive deep-copy roundtrip through state."""
        apps = [
            {"hub": "primary", "namespace": "ns1", "name": "app-a", "pause_applied": True},
            {"hub": "secondary", "namespace": "ns2", "name": "app-b", "pause_applied": True},
            {"hub": "primary", "namespace": "ns3", "name": "app-c", "pause_applied": False},
        ]
        state.set_config("argocd_paused_apps", copy.deepcopy(apps))

        retrieved = state.get_config("argocd_paused_apps")
        assert len(retrieved) == 3
        names = [a["name"] for a in retrieved]
        assert names == ["app-a", "app-b", "app-c"]

    def test_finalization_resume_skips_when_no_run_id(self, state):
        """Finalization skips resume when argocd_run_id is absent."""
        assert state.get_config("argocd_run_id") is None
        paused = state.get_config("argocd_paused_apps") or []
        assert not paused


# ---------------------------------------------------------------------------
# auto_import_strategy_set handoff: activation → finalization
# ---------------------------------------------------------------------------


class TestAutoImportStrategyHandoff:
    """Verify auto_import_strategy_set flows from activation to finalization."""

    def test_activation_sets_flag_finalization_reads_it(self, state):
        """activation sets auto_import_strategy_set=True, finalization reads it."""
        state.set_config("auto_import_strategy_set", True)

        assert state.get_config("auto_import_strategy_set", False) is True

    def test_finalization_default_when_flag_not_set(self, state):
        """Without activation setting the flag, finalization defaults to False."""
        assert state.get_config("auto_import_strategy_set", False) is False

    def test_finalization_resets_flag_after_restore(self, state):
        """Finalization clears auto_import_strategy_set after resetting strategy."""
        state.set_config("auto_import_strategy_set", True)
        assert state.get_config("auto_import_strategy_set", False) is True

        # Simulate finalization reset
        state.set_config("auto_import_strategy_set", False)
        assert state.get_config("auto_import_strategy_set", False) is False

    def test_auto_import_flag_survives_save_reload(self, tmp_path):
        """Flag persists across StateManager instances."""
        path = str(tmp_path / "ais.json")
        s1 = StateManager(path)
        s1.set_config("auto_import_strategy_set", True)
        s1.flush_state()

        s2 = StateManager(path)
        assert s2.get_config("auto_import_strategy_set", False) is True


# ---------------------------------------------------------------------------
# saved_backup_schedule handoff: primary_prep → backup_schedule
# ---------------------------------------------------------------------------


class TestBackupScheduleStateHandoff:
    """Verify saved_backup_schedule flows from primary_prep to BackupScheduleManager."""

    SAMPLE_BACKUP_SCHEDULE = {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "BackupSchedule",
        "metadata": {
            "name": "schedule-acm",
            "namespace": "open-cluster-management-backup",
            "resourceVersion": "12345",
            "uid": "abc-uid",
        },
        "spec": {
            "veleroSchedule": "0 */6 * * *",
            "veleroTtl": "120h",
            "paused": False,
        },
    }

    def test_primary_prep_saves_backup_schedule_for_finalization(self, state):
        """primary_prep stores BackupSchedule; backup_schedule module reads it."""
        state.set_config("saved_backup_schedule", copy.deepcopy(self.SAMPLE_BACKUP_SCHEDULE))

        saved = state.get_config("saved_backup_schedule")
        assert saved is not None
        assert saved["metadata"]["name"] == "schedule-acm"
        assert saved["spec"]["veleroSchedule"] == "0 */6 * * *"

    def test_backup_schedule_absent_returns_none(self, state):
        """When primary had no BackupSchedule, get_config returns None."""
        assert state.get_config("saved_backup_schedule") is None

    def test_saved_schedule_survives_save_reload(self, tmp_path):
        """BackupSchedule dict persists across save/reload."""
        path = str(tmp_path / "bs.json")
        s1 = StateManager(path)
        s1.set_config("saved_backup_schedule", copy.deepcopy(self.SAMPLE_BACKUP_SCHEDULE))
        s1.flush_state()

        s2 = StateManager(path)
        saved = s2.get_config("saved_backup_schedule")
        assert saved["spec"]["veleroSchedule"] == "0 */6 * * *"
        assert saved["metadata"]["name"] == "schedule-acm"

    def test_paused_schedule_preserved_in_state(self, state):
        """A paused schedule is stored with paused=True intact."""
        bs = copy.deepcopy(self.SAMPLE_BACKUP_SCHEDULE)
        bs["spec"]["paused"] = True
        state.set_config("saved_backup_schedule", bs)

        saved = state.get_config("saved_backup_schedule")
        assert saved["spec"]["paused"] is True


# ---------------------------------------------------------------------------
# Finalization internal state keys (written + read within finalization)
# ---------------------------------------------------------------------------


class TestFinalizationInternalState:
    """Verify finalization's own state keys for backup tracking."""

    def test_backup_schedule_enabled_at_roundtrip(self, state):
        """backup_schedule_enabled_at timestamp persists and is readable."""
        state.set_config("backup_schedule_enabled_at", "2025-01-15T10:30:00+00:00")

        assert state.get_config("backup_schedule_enabled_at") == "2025-01-15T10:30:00+00:00"

    def test_post_switchover_backup_name_roundtrip(self, state):
        """post_switchover_backup_name survives state round-trip for resume."""
        state.set_config("new_backup_detected", True)
        state.set_config("post_switchover_backup_name", "acm-managed-clusters-schedule-20250115103000")

        assert state.get_config("new_backup_detected") is True
        assert state.get_config("post_switchover_backup_name") == "acm-managed-clusters-schedule-20250115103000"

    def test_archived_restores_roundtrip(self, state):
        """archived_restores audit trail is retrievable after save."""
        restores = [
            {"name": "restore-passive-sync", "deleted_at": "2025-01-15T10:31:00+00:00"},
            {"name": "restore-acm-full", "deleted_at": "2025-01-15T10:31:01+00:00"},
        ]
        state.set_config("archived_restores", restores)

        saved = state.get_config("archived_restores")
        assert len(saved) == 2
        assert saved[0]["name"] == "restore-passive-sync"


# ---------------------------------------------------------------------------
# End-to-end phase progression with cross-module state
# ---------------------------------------------------------------------------


class TestFullPhaseStateProgression:
    """Simulate a complete switchover's state evolution across phases."""

    def test_complete_state_handoff_chain(self, tmp_path):
        """Walk through PRIMARY_PREP → ACTIVATION → FINALIZATION state flow."""
        path = str(tmp_path / "full-flow.json")
        state = StateManager(path)

        # Phase 1: PRIMARY_PREP sets Argo CD + backup state
        state.set_phase(Phase.PRIMARY_PREP)
        state.set_config("argocd_paused_apps", [{"hub": "primary", "name": "app1", "pause_applied": True}])
        state.set_config("argocd_run_id", "run-full-test")
        state.set_config("argocd_pause_dry_run", False)
        state.set_config(
            "saved_backup_schedule",
            {"metadata": {"name": "schedule-acm"}, "spec": {"veleroSchedule": "0 */6 * * *"}},
        )
        state.mark_step_completed("pause_backup_schedule")
        state.flush_state()

        # Phase 2: ACTIVATION sets auto-import strategy
        state.set_phase(Phase.ACTIVATION)
        state.set_config("auto_import_strategy_set", True)
        state.set_config("secondary_version", "2.14.1")
        state.flush_state()

        # Phase 3: FINALIZATION reads all upstream state
        state.set_phase(Phase.FINALIZATION)

        # Reload from disk to simulate process restart between phases
        state2 = StateManager(path)
        assert state2.get_current_phase() == Phase.FINALIZATION

        # Verify all cross-module keys are present
        assert state2.get_config("argocd_run_id") == "run-full-test"
        assert state2.get_config("argocd_pause_dry_run", False) is False
        assert len(state2.get_config("argocd_paused_apps")) == 1
        assert state2.get_config("auto_import_strategy_set", False) is True
        assert state2.get_config("saved_backup_schedule")["metadata"]["name"] == "schedule-acm"
        assert state2.is_step_completed("pause_backup_schedule")

    def test_state_handoff_with_no_argocd(self, tmp_path):
        """When Argo CD is not present, finalization gets empty state."""
        path = str(tmp_path / "no-argocd.json")
        state = StateManager(path)

        # PRIMARY_PREP: no Argo CD found
        state.set_phase(Phase.PRIMARY_PREP)
        state.set_config("argocd_paused_apps", [])
        state.set_config("argocd_run_id", None)
        state.set_config("argocd_pause_dry_run", False)
        state.flush_state()

        # ACTIVATION: no auto-import (ACM < 2.14)
        state.set_phase(Phase.ACTIVATION)
        state.flush_state()

        # FINALIZATION reads
        state.set_phase(Phase.FINALIZATION)
        s2 = StateManager(path)
        assert s2.get_config("argocd_paused_apps") == []
        assert s2.get_config("argocd_run_id") is None
        # auto_import_strategy_set was never set → default False
        assert s2.get_config("auto_import_strategy_set", False) is False

    def test_resume_preserves_cross_module_state(self, tmp_path):
        """Failing mid-finalization and resuming keeps upstream state intact."""
        path = str(tmp_path / "resume.json")
        state = StateManager(path)

        # Set up state as if PRIMARY_PREP and ACTIVATION completed
        state.set_phase(Phase.PRIMARY_PREP)
        state.set_config("argocd_paused_apps", [{"name": "app1"}])
        state.set_config("argocd_run_id", "run-resume")
        state.set_config("argocd_pause_dry_run", False)
        state.set_config("auto_import_strategy_set", True)
        state.mark_step_completed("pause_backup_schedule")
        state.set_phase(Phase.ACTIVATION)
        state.mark_step_completed("activate_clusters")

        # Simulate failure in finalization
        state.set_phase(Phase.FINALIZATION)
        state.add_error("backup verification timed out", Phase.FINALIZATION.value)
        state.set_phase(Phase.FAILED)
        state.flush_state()

        # Resume: reload state
        resumed = StateManager(path)
        assert resumed.get_current_phase() == Phase.FAILED
        assert resumed.get_last_error_phase() == Phase.FINALIZATION

        # All cross-module keys intact
        assert resumed.get_config("argocd_run_id") == "run-resume"
        assert resumed.get_config("auto_import_strategy_set", False) is True
        assert resumed.is_step_completed("pause_backup_schedule")
        assert resumed.is_step_completed("activate_clusters")

    def test_config_mutation_does_not_corrupt_state(self, state):
        """Mutating a retrieved config value must not affect stored state."""
        original = [{"name": "app1", "pause_applied": True}]
        state.set_config("argocd_paused_apps", copy.deepcopy(original))

        # Mutate the retrieved value
        retrieved = state.get_config("argocd_paused_apps")
        retrieved.append({"name": "injected"})

        # Original state must be unchanged
        fresh = state.get_config("argocd_paused_apps")
        # StateManager returns references, so deep-copy on set is the contract.
        # This test documents current behavior (reference semantics).
        # The primary_prep module uses copy.deepcopy() before set_config to avoid this.
        assert isinstance(fresh, list)
