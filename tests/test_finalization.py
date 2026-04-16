"""Unit tests for modules/finalization.py.

Tests cover Finalization class for completing the switchover.
"""

import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch

import pytest
from kubernetes.client.rest import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.finalization as finalization_module
from lib.exceptions import SwitchoverError

Finalization = finalization_module.Finalization
ACM_BACKUP_LABEL = {"cluster.open-cluster-management.io/backup-schedule-type": "managedClusters"}


def create_mock_step_context(is_step_completed_func, mark_step_completed_func):
    """Create a mock step context manager that mimics StepContext behavior."""

    @contextmanager
    def mock_step(step_name, logger=None):
        if is_step_completed_func(step_name):
            if logger:
                logger.info("Step already completed: %s", step_name)
            yield False
        else:
            yield True
            mark_step_completed_func(step_name)

    return mock_step


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub."""
    return Mock()


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager with step() context manager support."""
    mock = Mock()
    mock.is_step_completed.return_value = False
    # Set up step() to return a proper context manager
    mock.step.side_effect = create_mock_step_context(
        mock.is_step_completed,
        mock.mark_step_completed,
    )
    mock.state = {"completed_steps": []}
    # Return None for all config keys by default so tests are explicit.
    mock.get_config.return_value = None
    return mock


@pytest.fixture
def mock_backup_manager():
    """Create a mock BackupScheduleManager."""
    with patch("modules.finalization.BackupScheduleManager") as mock:
        yield mock.return_value


@pytest.fixture
def finalization(mock_secondary_client, mock_state_manager, mock_backup_manager):
    """Create Finalization instance."""
    return Finalization(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
    )


@pytest.fixture
def finalization_with_primary(mock_secondary_client, mock_state_manager, mock_backup_manager):
    """Create Finalization instance with primary client."""
    primary = Mock()
    fin = Finalization(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        primary_client=primary,
        primary_has_observability=True,
    )
    return fin, primary


@pytest.mark.unit
class TestFinalization:
    """Tests for Finalization class."""

    @patch("modules.finalization.time")
    def test_finalize_success(
        self,
        mock_time,
        finalization,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
    ):
        """Test successful finalization workflow."""
        # Mock time to avoid loops
        mock_time.time.side_effect = [0, 1, 2, 3]
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Mock list responses: schedule verification, collision check, initial backups, loop 1, loop 2
        mock_secondary_client.list_custom_resources.side_effect = [
            [],  # _cleanup_restore_resources
            [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}],  # verify_backup_schedule_enabled
            [
                {
                    "metadata": {"name": "schedule"},
                    "spec": {},
                    "status": {"phase": "Enabled"},
                }
            ],  # fix_backup_collision
            [],  # Initial backups
            [],  # Loop iteration 1
            [
                {
                    "metadata": {"name": "backup-1", "labels": ACM_BACKUP_LABEL},
                    "status": {"phase": "InProgress"},
                }
            ],  # Loop iteration 2 - new ACM backup
            [
                {
                    "metadata": {
                        "name": "backup-1",
                        "creationTimestamp": backup_ts,
                        "labels": ACM_BACKUP_LABEL,
                    },
                    "status": {"phase": "Completed", "completionTimestamp": backup_ts},
                }
            ],  # verify_backup_integrity
        ]

        mch_response = {"metadata": {"name": "multiclusterhub"}, "status": {"phase": "Running"}}
        # _fix_backup_schedule_collision calls get_custom_resource twice (before-delete read + post-delete
        # re-read), then _verify_mch_health calls it once for the MCH object.
        mock_secondary_client.get_custom_resource.side_effect = [
            {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}},  # before deletion
            None,  # after deletion — schedule gone, safe to create
            mch_response,  # MCH health check
        ]
        mock_secondary_client.get_pods.return_value = []

        result = finalization.finalize()

        assert result is True

        # Verify steps (now 7 steps with backup integrity and old hub handling)
        mock_backup_manager.ensure_enabled.assert_called_with("2.12.0")
        assert mock_state_manager.mark_step_completed.call_count == 7
        mock_state_manager.mark_step_completed.assert_has_calls(
            [
                call("enable_backup_schedule"),
                call("verify_backup_schedule_enabled"),
                call("fix_backup_collision"),
                call("verify_new_backups"),
                call("verify_backup_integrity"),
                call("verify_mch_health"),
                call("handle_old_hub"),
            ]
        )

    def test_finalize_skips_completed_steps(self, finalization, mock_state_manager, mock_backup_manager):
        """Test that completed steps are skipped."""
        mock_state_manager.is_step_completed.return_value = True

        result = finalization.finalize()

        assert result is True
        mock_backup_manager.ensure_enabled.assert_not_called()
        # verify_new_backups is internal method, hard to assert not called directly without mocking class method,
        # but we can infer from lack of client calls if we didn't mock list_custom_resources

    def test_decommission_old_hub_raises_when_decommission_returns_false(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Decommission failure must raise instead of being downgraded to a warning."""
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="decommission",
        )

        with patch("modules.finalization.Decommission") as decommission_class:
            decommission_class.return_value.decommission.return_value = False

            with pytest.raises(SwitchoverError, match="decommission failed"):
                fin._decommission_old_hub()

    @patch("modules.finalization.time")
    def test_verify_new_backups_success(self, mock_time, finalization, mock_secondary_client):
        """Test backup verification logic finding a new backup."""
        # Mock time.time() to increment, avoiding real sleep calls
        # Calls: start_time, check 1, check 2, check 3
        mock_time.time.side_effect = [0, 0, 1, 2]

        # Sequence of API calls:
        # 1. Initial list (empty)
        # 2. Loop 1 list (still empty)
        # 3. Loop 2 list (new backup found)
        # Velero uses "Completed" phase, not "Finished"
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            [],
            [
                {
                    "metadata": {"name": "new-backup", "labels": ACM_BACKUP_LABEL},
                    "status": {"phase": "Completed"},
                }
            ],
        ]

        finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources.call_count == 3

    @patch("modules.finalization.time")
    def test_verify_new_backups_timeout(self, mock_time, finalization, mock_secondary_client):
        """Backup verification timeout must raise SwitchoverError (fail closed)."""
        mock_time.time.side_effect = [0, 10, 45, 51]
        mock_secondary_client.list_custom_resources.return_value = []

        with pytest.raises(SwitchoverError, match="No new backup created"):
            finalization._verify_new_backups(timeout=50)

    @patch("modules.finalization.time")
    def test_verify_new_backups_stores_backup_name(self, mock_time, finalization, mock_secondary_client):
        """Successful backup detection must record the backup name in state."""
        mock_time.time.side_effect = [0, 1]
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            [
                {
                    "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
                    "status": {"phase": "Completed"},
                }
            ],
        ]

        finalization._verify_new_backups(timeout=10)

        finalization.state.set_config.assert_any_call("post_switchover_backup_name", "acm-backup-001")

    def test_verify_new_backups_reuses_recorded_backup_name(self, finalization, mock_secondary_client):
        """If a recorded post-switchover backup still exists, resume should succeed immediately."""
        recorded_backup = {
            "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
            "status": {"phase": "Completed"},
        }
        mock_secondary_client.list_custom_resources.return_value = [recorded_backup]
        mock_secondary_client.get_custom_resource.return_value = recorded_backup
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "acm-backup-001" if key == "post_switchover_backup_name" else None
        )

        finalization._verify_new_backups(timeout=10)

        mock_secondary_client.get_custom_resource.assert_called_once()
        finalization.state.set_config.assert_any_call("post_switchover_backup_name", "acm-backup-001")

    def test_verify_new_backups_accepts_existing_post_enable_backup_on_resume(
        self, finalization, mock_secondary_client
    ):
        """If the first post-enable ACM backup already exists on resume, do not require a second backup."""
        backup_ts = "2026-03-06T10:05:00Z"
        enabled_ts = "2026-03-06T10:00:00Z"
        existing_backup = {
            "metadata": {
                "name": "acm-backup-001",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {"phase": "Completed", "completionTimestamp": backup_ts},
        }
        mock_secondary_client.list_custom_resources.return_value = [existing_backup]
        mock_secondary_client.get_custom_resource.return_value = None
        finalization.state.get_config.side_effect = lambda key, default=None: {
            "post_switchover_backup_name": None,
            "backup_schedule_enabled_at": enabled_ts,
        }.get(key, default)

        finalization._verify_new_backups(timeout=10)

        finalization.state.set_config.assert_any_call("post_switchover_backup_name", "acm-backup-001")

    @patch("modules.finalization.time")
    def test_verify_new_backups_accepts_known_acm_name_without_label_and_logs_warning(
        self, mock_time, finalization, mock_secondary_client, caplog
    ):
        """Known ACM backup names are accepted with a warning when the ACM label is missing."""
        mock_time.time.side_effect = [0, 0, 1, 2]
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            [],
            [
                {
                    "metadata": {"name": "acm-managed-clusters-schedule-20260306100000"},
                    "status": {"phase": "Completed"},
                }
            ],
        ]

        with caplog.at_level(logging.WARNING):
            finalization._verify_new_backups(timeout=10)

        finalization.state.set_config.assert_any_call(
            "post_switchover_backup_name",
            "acm-managed-clusters-schedule-20260306100000",
        )
        assert finalization_module.ACM_BACKUP_SCHEDULE_TYPE_LABEL in caplog.text
        assert "name-pattern fallback" in caplog.text

    @patch("modules.finalization.time")
    def test_verify_new_backups_ignores_unrelated_velero_backups(self, mock_time, finalization, mock_secondary_client):
        """Only ACM-owned backups should count as post-switchover evidence."""
        mock_time.time.side_effect = [0, 0, 1, 2]
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            [{"metadata": {"name": "manual-backup"}, "status": {"phase": "Completed"}}],
            [
                {
                    "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
                    "status": {"phase": "Completed"},
                }
            ],
        ]

        finalization._verify_new_backups(timeout=10)

        finalization.state.set_config.assert_any_call("post_switchover_backup_name", "acm-backup-001")

    @patch("modules.finalization.time")
    def test_verify_new_backups_retries_after_transient_list_error(
        self, mock_time, finalization, mock_secondary_client
    ):
        """Transient backup list failures should be tolerated until a later poll succeeds."""
        mock_time.time.side_effect = [0, 0, 1]
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            ApiException(status=500, reason="temporary failure"),
            [
                {
                    "metadata": {"name": "acm-backup-001", "labels": ACM_BACKUP_LABEL},
                    "status": {"phase": "Completed"},
                }
            ],
        ]

        finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources.call_count == 3
        finalization.state.set_config.assert_any_call("post_switchover_backup_name", "acm-backup-001")

    def test_verify_new_backups_wraps_initial_transient_list_error(self, finalization, mock_secondary_client):
        """Initial backup discovery should not leak raw ApiException on retryable failures."""
        mock_secondary_client.list_custom_resources.side_effect = ApiException(status=500, reason="temporary failure")

        with pytest.raises(
            SwitchoverError,
            match="Failed to list Velero backups before waiting for a new ACM backup",
        ):
            finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources.call_count == 1

    def test_cleanup_restore_resources_raises_when_delete_fails(
        self, finalization, mock_secondary_client, mock_state_manager
    ):
        """Restore cleanup must fail closed when a delete request does not succeed."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "restore-1"}, "status": {"phase": "Running"}}
        ]
        mock_secondary_client.delete_custom_resource.side_effect = ApiException(status=403, reason="Forbidden")

        with pytest.raises(SwitchoverError, match="Failed to delete restore resource"):
            finalization._cleanup_restore_resources()

        mock_state_manager.set_config.assert_called_once_with("archived_restores", ANY)

    @patch("modules.finalization.time")
    def test_verify_new_backups_fails_fast_on_permanent_list_error(
        self, mock_time, finalization, mock_secondary_client
    ):
        """Permanent backup list failures should surface immediately instead of timing out."""
        mock_time.time.side_effect = [0, 0]
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            ApiException(status=403, reason="Forbidden"),
        ]

        with pytest.raises(SwitchoverError, match="Failed to list Velero backups"):
            finalization._verify_new_backups(timeout=10)

        mock_time.sleep.assert_not_called()
        assert mock_secondary_client.list_custom_resources.call_count == 2

    def test_verify_backup_integrity_success(self, finalization, mock_secondary_client):
        """Backup integrity should pass for a recent completed backup with no errors (recorded name path)."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_custom_resource.return_value = backup
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "backup-1" if key == "post_switchover_backup_name" else None
        )

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_schedule_enabled_raises_switchover_error_when_missing(self, finalization):
        finalization._cached_schedules = []

        with pytest.raises(
            SwitchoverError,
            match="No BackupSchedule found while verifying finalization",
        ):
            finalization._verify_backup_schedule_enabled()

    def test_verify_multiclusterhub_health_raises_switchover_error_when_missing(
        self, finalization, mock_secondary_client
    ):
        mock_secondary_client.get_custom_resource.return_value = None
        mock_secondary_client.list_custom_resources.return_value = []

        with pytest.raises(SwitchoverError, match="No MultiClusterHub resource found on secondary hub"):
            finalization._verify_multiclusterhub_health(timeout=1, interval=0)

    def test_ensure_auto_import_default_raises_when_delete_fails(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_SYNC}
        }
        mock_secondary_client.delete_configmap.side_effect = ApiException(status=403, reason="Forbidden")
        mock_state_manager.get_config.side_effect = lambda key, default=None: (
            True if key == "auto_import_strategy_set" else default
        )

        with pytest.raises(SwitchoverError, match="Failed to reset autoImportStrategy to default"):
            fin._ensure_auto_import_default()

    def test_auto_import_reset_missing_configmap_clears_state(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """A missing configmap means reset already completed and state should clear."""
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        state.set_config("auto_import_strategy_set", True)
        mock_secondary_client.get_configmap.return_value = None

        assert fin._ensure_auto_import_default() is True

        assert state.get_config("auto_import_strategy_set", False) is False
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_auto_import_reset_non_sync_strategy_clears_state_without_delete(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """A non-Sync strategy is already safe and must not trigger a delete."""
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        state.set_config("auto_import_strategy_set", True)
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_DEFAULT}
        }

        assert fin._ensure_auto_import_default() is True

        assert state.get_config("auto_import_strategy_set", False) is False
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_auto_import_reset_delete_404_is_treated_as_complete(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """Deleting an already-absent configmap should succeed idempotently."""
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        state.set_config("auto_import_strategy_set", True)
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_SYNC}
        }
        mock_secondary_client.delete_configmap.side_effect = ApiException(status=404, reason="Not Found")

        assert fin._ensure_auto_import_default() is True

        assert state.get_config("auto_import_strategy_set", False) is False
        mock_secondary_client.delete_configmap.assert_called_once_with(
            finalization_module.MCE_NAMESPACE,
            finalization_module.IMPORT_CONTROLLER_CONFIG_CM,
        )

    def test_auto_import_reset_delete_success_clears_persisted_state(
        self, mock_secondary_client, mock_backup_manager, tmp_path
    ):
        """Deleting the Sync override should restore default state persistently."""
        from lib.utils import StateManager

        state_path = tmp_path / "state.json"
        state = StateManager(str(state_path))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.14.0",
        )
        state.set_config("auto_import_strategy_set", True)
        mock_secondary_client.get_configmap.return_value = {
            "data": {finalization_module.AUTO_IMPORT_STRATEGY_KEY: finalization_module.AUTO_IMPORT_STRATEGY_SYNC}
        }

        assert fin._ensure_auto_import_default() is True

        assert StateManager(str(state_path)).get_config("auto_import_strategy_set", True) is False
        mock_secondary_client.delete_configmap.assert_called_once_with(
            finalization_module.MCE_NAMESPACE,
            finalization_module.IMPORT_CONTROLLER_CONFIG_CM,
        )

    def test_ensure_auto_import_default_skips_state_updates_in_dry_run(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Dry-run must not mutate state for the auto-import strategy reset step."""
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            dry_run=True,
        )

        assert fin._ensure_auto_import_default() is True

        mock_secondary_client.get_configmap.assert_not_called()
        mock_secondary_client.delete_configmap.assert_not_called()
        mock_state_manager.set_config.assert_not_called()
        mock_state_manager.mark_step_completed.assert_not_called()

    def test_ensure_auto_import_default_warns_and_skips_when_lookup_fails(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager, caplog
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )
        mock_secondary_client.get_configmap.side_effect = ApiException(status=403, reason="Forbidden")

        with caplog.at_level(logging.WARNING):
            assert fin._ensure_auto_import_default() is True

        assert "Unable to verify auto-import strategy" in caplog.text
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_ensure_auto_import_default_raises_when_owned_lookup_fails(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )
        mock_secondary_client.get_configmap.side_effect = ApiException(status=403, reason="Forbidden")
        mock_state_manager.get_config.side_effect = lambda key, default=None: (
            True if key == "auto_import_strategy_set" else default
        )

        with pytest.raises(SwitchoverError, match="Failed to verify autoImportStrategy"):
            fin._ensure_auto_import_default()

    def test_ensure_auto_import_default_is_noop_before_acm_2_14(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.13.9",
        )

        assert fin._ensure_auto_import_default() is True

        mock_secondary_client.get_configmap.assert_not_called()
        mock_secondary_client.delete_configmap.assert_not_called()

    def test_finalize_exposes_reset_auto_import_strategy_as_explicit_step(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Finalization should expose auto-import reset in the visible step sequence."""
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
        )

        with patch.object(fin, "_enable_backup_schedule"), patch.object(
            fin, "_verify_backup_schedule_enabled"
        ), patch.object(fin, "_fix_backup_schedule_collision"), patch.object(fin, "_verify_new_backups"), patch.object(
            fin, "_verify_backup_integrity"
        ), patch.object(
            fin, "_verify_multiclusterhub_health"
        ), patch.object(
            fin, "_ensure_auto_import_default", return_value=True
        ) as reset_auto_import, patch.object(
            fin, "_handle_old_hub"
        ), patch.object(
            fin, "_get_backup_verify_timeout", return_value=600
        ):
            assert fin.finalize() is True

        mock_state_manager.is_step_completed.assert_any_call("reset_auto_import_strategy")
        reset_auto_import.assert_called_once()
        mock_state_manager.mark_step_completed.assert_any_call("reset_auto_import_strategy")

    def test_backup_verify_timeout_derived_from_schedule(self, finalization):
        schedule = {
            "metadata": {"name": "schedule-rhacm"},
            "spec": {"veleroSchedule": "0 */4 * * *"},
        }
        finalization._cached_schedules = [schedule]

        timeout = finalization._get_backup_verify_timeout()

        assert timeout == 4 * 3600

    def test_backup_max_age_derived_from_schedule(self, finalization):
        schedule = {
            "metadata": {"name": "schedule-rhacm"},
            "spec": {"veleroSchedule": "0 */4 * * *"},
        }
        finalization._cached_schedules = [schedule]

        max_age = finalization._get_backup_max_age_seconds(600)

        assert max_age == (4 * 3600 + 600)

    @pytest.mark.parametrize(
        ("cron_expr", "expected_seconds"),
        [
            ("*/15 * * * *", 15 * 60),
            ("0 */2 * * *", 2 * 3600),
            ("0 0 * * *", 24 * 3600),
        ],
    )
    def test_parse_cron_interval_seconds(self, finalization, cron_expr, expected_seconds):
        assert finalization._parse_cron_interval_seconds(cron_expr) == expected_seconds

    def test_verify_backup_integrity_skips_age_without_new_backup(self, finalization, mock_secondary_client):
        """Backup age enforcement should be skipped if no post-switchover backup name is recorded."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.list_custom_resources.return_value = [backup]
        mock_secondary_client.get_pods.return_value = []
        # No recorded backup name → falls back to latest-by-timestamp, age check skipped
        finalization.state.get_config.return_value = None

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_fallback_ignores_unrelated_backups(self, finalization, mock_secondary_client):
        """Fallback integrity path should consider only ACM-owned backups."""
        acm_backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        manual_backup_ts = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {
                    "name": "manual-backup",
                    "creationTimestamp": manual_backup_ts,
                },
                "status": {
                    "phase": "Completed",
                    "completionTimestamp": manual_backup_ts,
                    "errors": 99,
                },
            },
            {
                "metadata": {
                    "name": "backup-1",
                    "creationTimestamp": acm_backup_ts,
                    "labels": ACM_BACKUP_LABEL,
                },
                "status": {
                    "phase": "Completed",
                    "completionTimestamp": acm_backup_ts,
                    "errors": 0,
                    "warnings": 0,
                },
            },
        ]
        mock_secondary_client.get_pods.return_value = []
        finalization.state.get_config.return_value = None

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_enforces_age_with_recorded_backup_name(self, finalization, mock_secondary_client):
        """Age enforcement fires when backup name is recorded and backup is too old."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_custom_resource.return_value = backup
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "backup-1" if key == "post_switchover_backup_name" else None
        )

        with pytest.raises(SwitchoverError):
            finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_skips_age_if_backup_before_enable(self, finalization, mock_secondary_client):
        """Backup age enforcement should be skipped if backup predates enable timestamp."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        enabled_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.list_custom_resources.return_value = [backup]
        mock_secondary_client.get_pods.return_value = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            enabled_ts if key == "backup_schedule_enabled_at" else None
        )

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_uses_recorded_name_not_latest(self, finalization, mock_secondary_client):
        """When backup name is recorded, that specific backup is verified — not the latest in the namespace."""
        switchover_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        switchover_backup = {
            "metadata": {
                "name": "acm-backup-switchover",
                "creationTimestamp": switchover_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": switchover_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_custom_resource.return_value = switchover_backup
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "acm-backup-switchover" if key == "post_switchover_backup_name" else None
        )

        finalization._verify_backup_integrity(max_age_seconds=600)

        # Must fetch by name, not list the namespace and pick the latest
        mock_secondary_client.get_custom_resource.assert_called_once()
        call_kwargs = mock_secondary_client.get_custom_resource.call_args
        assert call_kwargs.kwargs.get("name") == "acm-backup-switchover"
        mock_secondary_client.list_custom_resources.assert_not_called()

    def test_verify_backup_integrity_fails_when_recorded_backup_missing(self, finalization, mock_secondary_client):
        """If the recorded post-switchover backup is pruned, fall back to latest ACM-owned backup."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        fallback_backup = {
            "metadata": {
                "name": "acm-backup-newer",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        # get_custom_resource returns None for the recorded name (pruned)
        mock_secondary_client.get_custom_resource.return_value = None
        # list_custom_resources returns a fallback backup
        mock_secondary_client.list_custom_resources.return_value = [fallback_backup]
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "acm-backup-switchover" if key == "post_switchover_backup_name" else None
        )

        # Should NOT raise — should fall back to latest ACM-owned backup
        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_fails_when_recorded_backup_is_not_acm_owned(
        self, finalization, mock_secondary_client
    ):
        """Recorded backup names must still resolve to ACM-owned backups."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "manual-backup", "creationTimestamp": backup_ts},
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "manual-backup" if key == "post_switchover_backup_name" else None
        )

        with pytest.raises(SwitchoverError, match="not ACM-owned"):
            finalization._verify_backup_integrity(max_age_seconds=600)

    @patch("modules.finalization.wait_for_condition")
    def test_verify_backup_integrity_waits_for_completion(self, mock_wait, finalization, mock_secondary_client):
        """Backup integrity should wait for the recorded post-switchover backup to complete."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_wait.return_value = True
        in_progress_backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {"phase": "InProgress"},
        }
        completed_backup = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 0,
                "warnings": 0,
            },
        }
        # First call returns InProgress backup (initial fetch), second returns Completed (after wait)
        mock_secondary_client.get_custom_resource.side_effect = [
            in_progress_backup,
            completed_backup,
        ]
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "backup-1" if key == "post_switchover_backup_name" else None
        )

        finalization._verify_backup_integrity(max_age_seconds=600)

        mock_wait.assert_called_once()

    def test_verify_backup_integrity_fails_on_errors(self, finalization, mock_secondary_client):
        """Backup integrity should fail when the recorded backup reports errors."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {
                "name": "backup-1",
                "creationTimestamp": backup_ts,
                "labels": ACM_BACKUP_LABEL,
            },
            "status": {
                "phase": "Completed",
                "completionTimestamp": backup_ts,
                "errors": 2,
            },
        }
        mock_secondary_client.get_pods.return_value = []
        finalization._cached_schedules = []
        finalization.state.get_config.side_effect = lambda key, default=None: (
            "backup-1" if key == "post_switchover_backup_name" else None
        )

        with pytest.raises(SwitchoverError):
            finalization._verify_backup_integrity(max_age_seconds=600)

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_skips_create_on_uid_change_after_delete(
        self, mock_sleep, finalization, mock_secondary_client
    ):
        """If schedule UID changes after delete, fail the step so resume does not mark it complete."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
                "status": {"phase": "Enabled"},
            }
        ]
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            {
                "metadata": {"name": "schedule", "uid": "uid-new"},
                "status": {"phase": "Enabled"},
            },
        ]

        with pytest.raises(SwitchoverError, match="reappeared with a different uid"):
            finalization._fix_backup_schedule_collision()

        mock_secondary_client.delete_custom_resource.assert_called_once()
        mock_secondary_client.create_custom_resource.assert_not_called()

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_treats_409_with_healthy_schedule_as_success(
        self, mock_sleep, finalization, mock_secondary_client
    ):
        """A 409 create conflict should be acceptable when schedule exists in a non-collision phase."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
                "status": {"phase": "Enabled"},
            }
        ]
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            None,
            {
                "metadata": {"name": "schedule", "uid": "uid-new"},
                "status": {"phase": "Enabled"},
            },
        ]
        mock_secondary_client.create_custom_resource.side_effect = ApiException(status=409)

        finalization._cached_schedules = [{"metadata": {"name": "cached"}}]
        finalization._fix_backup_schedule_collision()

        mock_secondary_client.delete_custom_resource.assert_called_once()
        mock_secondary_client.create_custom_resource.assert_called_once()
        assert finalization._cached_schedules is None

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_raises_when_409_reuses_original_uid(
        self, mock_sleep, finalization, mock_secondary_client
    ):
        """A 409 create conflict must fail when the original schedule object still exists."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
                "status": {"phase": "Enabled"},
            }
        ]
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            None,
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "status": {"phase": "Enabled"},
            },
        ]
        mock_secondary_client.create_custom_resource.side_effect = ApiException(status=409)

        with pytest.raises(SwitchoverError, match="still has the original uid"):
            finalization._fix_backup_schedule_collision()

        assert finalization._cached_schedules is None

    @patch("modules.finalization.time.sleep")
    def test_fix_backup_schedule_collision_raises_when_409_schedule_is_in_collision(
        self, mock_sleep, finalization, mock_secondary_client, caplog
    ):
        """A 409 create conflict with BackupCollision phase must fail closed."""
        mock_sleep.return_value = None
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
                "status": {"phase": "Enabled"},
            }
        ]
        mock_secondary_client.get_custom_resource.side_effect = [
            {
                "metadata": {"name": "schedule", "uid": "uid-old"},
                "spec": {"veleroSchedule": "*/15 * * * *"},
            },
            None,
            {
                "metadata": {"name": "schedule", "uid": "uid-new"},
                "status": {"phase": "BackupCollision"},
            },
        ]
        mock_secondary_client.create_custom_resource.side_effect = ApiException(status=409)

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            with pytest.raises(SwitchoverError, match="remains in BackupCollision"):
                finalization._fix_backup_schedule_collision()

        assert "already exists during recreation" in caplog.text

    @patch("modules.finalization.wait_for_condition")
    def test_disable_observability_on_old_hub_deletes_mco(
        self, mock_wait, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Optional MCO deletion should remove observability on old hub."""
        mock_wait.return_value = True
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
            disable_observability_on_secondary=True,
        )
        primary.list_custom_resources.return_value = [{"metadata": {"name": "observability", "labels": {}}}]
        primary.get_pods.return_value = []

        fin._disable_observability_on_old_hub()

        primary.delete_custom_resource.assert_called_once()

    @patch("lib.gitops_detector.record_gitops_markers")
    @patch("modules.finalization.wait_for_condition")
    def test_disable_observability_on_old_hub_warns_for_gitops_managed_mco(
        self,
        mock_wait,
        mock_record_markers,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
        caplog,
    ):
        """MCO deletion should emit immediate warning when GitOps markers are detected."""
        mock_wait.return_value = True
        mock_record_markers.return_value = ["label:app.kubernetes.io/managed-by"]
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
            disable_observability_on_secondary=True,
        )
        primary.list_custom_resources.return_value = [{"metadata": {"name": "observability", "labels": {}}}]
        primary.get_pods.return_value = []

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            fin._disable_observability_on_old_hub()

        assert "appears GitOps-managed" in caplog.text
        assert "observability" in caplog.text
        primary.delete_custom_resource.assert_called_once()

    @patch("lib.gitops_detector.record_gitops_markers")
    @patch("modules.finalization.wait_for_condition")
    def test_disable_observability_on_old_hub_no_gitops_warning_without_markers(
        self,
        mock_wait,
        mock_record_markers,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
        caplog,
    ):
        """MCO deletion should not emit GitOps warning when no markers are detected."""
        mock_wait.return_value = True
        mock_record_markers.return_value = []
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
            disable_observability_on_secondary=True,
        )
        primary.list_custom_resources.return_value = [{"metadata": {"name": "observability", "labels": {}}}]
        primary.get_pods.return_value = []

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            fin._disable_observability_on_old_hub()

        assert "appears GitOps-managed" not in caplog.text
        primary.delete_custom_resource.assert_called_once()

    @patch("lib.gitops_detector.record_gitops_markers")
    @patch("modules.finalization.wait_for_condition")
    def test_disable_observability_on_old_hub_continues_when_marker_recording_fails(
        self,
        mock_wait,
        mock_record_markers,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
        caplog,
    ):
        """Marker recording failures must not abort optional MCO deletion flow."""
        mock_wait.return_value = True
        mock_record_markers.side_effect = RuntimeError("marker failure")
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.14.0",
            primary_client=primary,
            old_hub_action="secondary",
            disable_observability_on_secondary=True,
        )
        primary.list_custom_resources.return_value = [{"metadata": {"name": "observability", "labels": {}}}]
        primary.get_pods.return_value = []

        with caplog.at_level(logging.WARNING, logger="acm_switchover"):
            fin._disable_observability_on_old_hub()

        assert "marker recording failed" in caplog.text.lower()
        primary.delete_custom_resource.assert_called_once()

    def test_finalize_failure_handling(self, finalization, mock_backup_manager):
        """Test finalization failure handling."""
        mock_backup_manager.ensure_enabled.side_effect = Exception("Backup Error")

        result = finalization.finalize()

        assert result is False

    def test_verify_backup_schedule_enabled_failure(self, finalization, mock_secondary_client):
        """Backup schedule verification should fail when paused."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule"}, "spec": {"paused": True}}
        ]

        with pytest.raises(SwitchoverError):
            finalization._verify_backup_schedule_enabled()

    @patch("modules.finalization.time")
    def test_verify_multiclusterhub_health_failure(self, mock_time, finalization, mock_secondary_client):
        """MCH verification should fail when not running, without real-time waits."""
        # Simulate fast timeout without real sleeping:
        # start=0, then enough time has elapsed on next checks to exceed timeout.
        mock_time.time.side_effect = [0, 601, 602]
        mock_time.sleep.return_value = None

        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Degraded"},
        }
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "acm-pod"}, "status": {"phase": "Running"}}
        ]

        with pytest.raises(SwitchoverError):
            finalization._verify_multiclusterhub_health()

    @patch("modules.finalization.time")
    def test_verify_multiclusterhub_health_accepts_succeeded_pods(self, mock_time, finalization, mock_secondary_client):
        """Completed job pods should not block MultiClusterHub health verification."""
        mock_time.time.side_effect = [0]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Running"},
        }
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "acm-operator"}, "status": {"phase": "Running"}},
            {"metadata": {"name": "mch-hook-job"}, "status": {"phase": "Succeeded"}},
        ]

        finalization._verify_multiclusterhub_health(timeout=300)

    def test_verify_old_hub_state(self, finalization_with_primary, mock_secondary_client):
        """Old hub checks should inspect clusters, backups, and observability pods."""
        fin, primary = finalization_with_primary
        primary.list_custom_resources.side_effect = [
            [
                {
                    "metadata": {"name": "cluster1"},
                    "status": {
                        "conditions": [
                            {
                                "type": "ManagedClusterConditionAvailable",
                                "status": "False",
                            }
                        ]
                    },
                }
            ],
            [{"metadata": {"name": "schedule"}, "spec": {"paused": True}}],
        ]
        primary.get_pods.return_value = []

        fin._verify_old_hub_state()

        assert primary.list_custom_resources.call_count == 2
        # get_pods is called for both thanos-compact and observatorium-api checks
        assert primary.get_pods.call_count == 2

    @patch("modules.finalization.time")
    def test_old_hub_observability_reports_success_when_all_pods_gone(self, mock_time, finalization_with_primary):
        """Observability shutdown should report success when old-hub pods terminate."""
        fin, primary = finalization_with_primary
        compactor_pods = [{"metadata": {"name": "compact-0"}}]
        api_pods = [{"metadata": {"name": "api-0"}}]
        primary.get_pods.side_effect = [[], []]
        mock_time.time.side_effect = [0, 0]

        with patch.object(finalization_module, "logger") as logger:
            compactor_pods_after, api_pods_after = fin._wait_for_observability_scale_down(
                compactor_pods=compactor_pods,
                api_pods=api_pods,
            )
            fin._report_observability_scale_down_status(
                compactor_pods=compactor_pods,
                api_pods=api_pods,
                compactor_pods_after=compactor_pods_after,
                api_pods_after=api_pods_after,
            )

        assert compactor_pods_after == []
        assert api_pods_after == []
        assert primary.get_pods.call_args_list == [
            call(
                namespace=finalization_module.OBSERVABILITY_NAMESPACE,
                label_selector="app.kubernetes.io/name=thanos-compact",
            ),
            call(
                namespace=finalization_module.OBSERVABILITY_NAMESPACE,
                label_selector="app.kubernetes.io/name=observatorium-api",
            ),
        ]
        mock_time.sleep.assert_not_called()
        logger.info.assert_any_call("%s is scaled down on old hub", "Thanos compactor")
        logger.info.assert_any_call("%s is scaled down on old hub", "Observatorium API")
        logger.info.assert_any_call("All observability components scaled down on old hub")
        logger.warning.assert_not_called()

    @patch("modules.finalization.time")
    def test_old_hub_observability_warns_when_pods_remain(self, mock_time, finalization_with_primary):
        """Observability shutdown should warn when old-hub pods remain after waiting."""
        fin, primary = finalization_with_primary
        compactor_pods = [{"metadata": {"name": "compact-0"}}]
        primary.get_pods.side_effect = [[{"metadata": {"name": "compact-0"}}]]
        mock_time.time.side_effect = [0, 0, finalization_module.OBSERVABILITY_TERMINATE_TIMEOUT + 1]
        mock_time.sleep.return_value = None

        with patch.object(finalization_module, "logger") as logger:
            compactor_pods_after, api_pods_after = fin._wait_for_observability_scale_down(
                compactor_pods=compactor_pods,
                api_pods=[],
            )
            fin._report_observability_scale_down_status(
                compactor_pods=compactor_pods,
                api_pods=[],
                compactor_pods_after=compactor_pods_after,
                api_pods_after=api_pods_after,
            )

        assert compactor_pods_after == [{"metadata": {"name": "compact-0"}}]
        assert api_pods_after == []
        primary.get_pods.assert_called_once_with(
            namespace=finalization_module.OBSERVABILITY_NAMESPACE,
            label_selector="app.kubernetes.io/name=thanos-compact",
        )
        mock_time.sleep.assert_called_once_with(finalization_module.OBSERVABILITY_TERMINATE_INTERVAL)
        logger.warning.assert_any_call(
            "%s still running on old hub (%s pod(s)) after waiting",
            "Thanos compactor",
            1,
        )
        logger.warning.assert_any_call(
            "Old hub: MultiClusterObservability is still active (%s). Scale both to 0 or remove MCO.",
            "thanos-compact=1, observatorium-api=0",
        )
        assert call("All observability components scaled down on old hub") not in logger.info.call_args_list

    def test_old_hub_observability_dry_run_only_reports_intent(self, finalization_with_primary):
        """Dry-run observability shutdown should only log what would be scaled down."""
        fin, primary = finalization_with_primary
        fin.dry_run = True
        compactor_pods = [{"metadata": {"name": "compact-0"}}]
        api_pods = [{"metadata": {"name": "api-0"}}]

        with patch.object(finalization_module, "logger") as logger:
            compactor_pods_after, api_pods_after = fin._wait_for_observability_scale_down(
                compactor_pods=compactor_pods,
                api_pods=api_pods,
            )
            fin._report_observability_scale_down_status(
                compactor_pods=compactor_pods,
                api_pods=api_pods,
                compactor_pods_after=compactor_pods_after,
                api_pods_after=api_pods_after,
            )

        primary.get_pods.assert_not_called()
        assert compactor_pods_after == []
        assert api_pods_after == []
        assert logger.info.call_args_list == [
            call("[DRY-RUN] Would scale down thanos-compact on old hub"),
            call("[DRY-RUN] Would scale down observatorium-api on old hub"),
        ]
        logger.warning.assert_not_called()
        assert call("All observability components scaled down on old hub") not in logger.info.call_args_list

    @patch("modules.finalization.time")
    def test_finalize_skips_verify_old_hub_state_when_action_none(
        self, mock_time, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Test that _verify_old_hub_state is not called when old_hub_action is 'none'.

        This ensures the CLI contract is respected: --old-hub-action none should
        leave the old hub unchanged for manual handling.
        """
        # Mock time to avoid loops and sleep delays
        mock_time.time.side_effect = [0, 1, 2, 3]
        mock_time.sleep.return_value = None
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=True,
            old_hub_action="none",
        )

        # Mock all required responses with side_effect for sequential calls
        mock_secondary_client.list_custom_resources.side_effect = [
            [],  # _cleanup_restore_resources
            [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}],  # verify_backup_schedule_enabled
            [
                {
                    "metadata": {"name": "schedule"},
                    "spec": {},
                    "status": {"phase": "Enabled"},
                }
            ],  # fix_backup_collision
            [],  # Initial backups
            [],  # Loop iteration 1
            [
                {
                    "metadata": {"name": "backup-1", "labels": ACM_BACKUP_LABEL},
                    "status": {"phase": "InProgress"},
                }
            ],  # Loop iteration 2 - new ACM backup
            [
                {
                    "metadata": {
                        "name": "backup-1",
                        "creationTimestamp": backup_ts,
                        "labels": ACM_BACKUP_LABEL,
                    },
                    "status": {"phase": "Completed", "completionTimestamp": backup_ts},
                }
            ],  # verify_backup_integrity
        ]
        mock_secondary_client.get_custom_resource.side_effect = [
            {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}},  # before deletion
            None,  # after deletion
            {"metadata": {"name": "multiclusterhub"}, "status": {"phase": "Running"}},  # MCH health
        ]
        mock_secondary_client.get_pods.return_value = []

        # Ensure we track if _verify_old_hub_state was called
        with patch.object(fin, "_verify_old_hub_state") as mock_verify:
            result = fin.finalize()

            assert result is True
            # _verify_old_hub_state should NOT be called when old_hub_action is 'none'
            mock_verify.assert_not_called()
            # Primary client should not have scaling methods called
            primary.scale_statefulset.assert_not_called()
            primary.scale_deployment.assert_not_called()

    @patch("time.sleep")
    @patch("time.time")
    def test_finalize_calls_verify_old_hub_state_when_action_secondary(
        self,
        mock_time_time,
        mock_time_sleep,
        mock_secondary_client,
        mock_state_manager,
        mock_backup_manager,
    ):
        """Test that _verify_old_hub_state IS called when old_hub_action is 'secondary'."""
        # Mock time to avoid real waits
        mock_time_time.return_value = 0
        mock_time_sleep.return_value = None
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        primary = Mock()
        primary.list_custom_resources.return_value = []
        primary.get_pods.return_value = []
        # _setup_old_hub_as_secondary checks for an existing restore on the
        # old hub via primary.get_custom_resource.  Return None so it skips
        # the delete-wait cycle (which would hang because time.time is mocked
        # to always return 0, preventing the timeout from ever firing).
        primary.get_custom_resource.return_value = None

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=False,
            old_hub_action="secondary",
        )

        # Mock all required responses with side_effect for sequential calls
        # Order: _cleanup_restore_resources, verify_backup_schedule_enabled, fix_backup_collision,
        # _get_backup_verify_timeout, verify_new_backups (2x), verify_backup_integrity
        mock_secondary_client.list_custom_resources.side_effect = [
            [],  # _cleanup_restore_resources - no restores to clean up
            [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}],  # verify_backup_schedule_enabled
            [
                {
                    "metadata": {"name": "schedule"},
                    "spec": {},
                    "status": {"phase": "Enabled"},
                }
            ],  # fix_backup_collision
            [
                {
                    "metadata": {"name": "schedule"},
                    "spec": {"veleroSchedule": "*/15 * * * *"},
                }
            ],  # _get_backup_verify_timeout
            [],  # Initial backups
            [
                {
                    "metadata": {"name": "backup-1", "labels": ACM_BACKUP_LABEL},
                    "status": {"phase": "InProgress"},
                }
            ],  # New ACM backup detected
            [
                {
                    "metadata": {
                        "name": "backup-1",
                        "creationTimestamp": backup_ts,
                        "labels": ACM_BACKUP_LABEL,
                    },
                    "status": {"phase": "Completed", "completionTimestamp": backup_ts},
                }
            ],  # verify_backup_integrity
        ]
        mock_secondary_client.get_custom_resource.side_effect = [
            {"metadata": {"name": "schedule", "uid": "uid-1"}, "spec": {}},  # before deletion
            None,  # after deletion
            {"metadata": {"name": "multiclusterhub"}, "status": {"phase": "Running"}},  # MCH health
        ]
        mock_secondary_client.get_pods.return_value = []

        with patch.object(fin, "_verify_old_hub_state") as mock_verify:
            result = fin.finalize()

            assert result is True
            # _verify_old_hub_state SHOULD be called when old_hub_action is 'secondary'
            mock_verify.assert_called_once()

    def test_handle_old_hub_raises_on_unknown_old_hub_action(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Unknown old_hub_action values must fail closed."""
        primary = Mock()
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="bogus",
        )

        with patch.object(fin, "_setup_old_hub_as_secondary") as setup_old_hub, patch.object(
            fin, "_decommission_old_hub"
        ) as decommission_old_hub:
            with pytest.raises(SwitchoverError, match="Unknown old_hub_action 'bogus'"):
                fin._handle_old_hub()

        setup_old_hub.assert_not_called()
        decommission_old_hub.assert_not_called()

    def test_setup_old_hub_as_secondary_failure_propagates(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Failed passive restore creation on old hub must raise SwitchoverError with context."""

        primary = Mock()
        primary.get_custom_resource.return_value = None
        primary.create_custom_resource.side_effect = ApiException(status=500, reason="Internal Server Error")

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with pytest.raises(
            SwitchoverError,
            match="Failed to create passive sync restore on old primary hub",
        ):
            fin._setup_old_hub_as_secondary()

    def test_setup_old_hub_as_secondary_resets_stale_active_restore(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Existing restore with veleroManagedClustersBackupName=latest must be deleted, waited on, and recreated."""
        primary = Mock()
        primary.dry_run = False
        # First call: return stale restore for initial check.
        # Subsequent calls (during wait polling): return None to indicate deletion completed.
        primary.get_custom_resource.side_effect = [
            {"spec": {"veleroManagedClustersBackupName": "latest"}},
            None,
        ]

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )
        fin._setup_old_hub_as_secondary()

        primary.delete_custom_resource.assert_called_once()
        # Verify wait polling happened (at least 2 get_custom_resource calls:
        # initial check + at least one deletion poll)
        assert primary.get_custom_resource.call_count >= 2
        primary.create_custom_resource.assert_called_once()
        created_spec = primary.create_custom_resource.call_args.kwargs["body"]["spec"]
        assert created_spec["veleroManagedClustersBackupName"] == "skip"

    def test_setup_old_hub_as_secondary_skips_when_already_passive(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Existing restore already in passive mode must not be deleted or recreated."""
        primary = Mock()
        primary.get_custom_resource.return_value = {"spec": {"veleroManagedClustersBackupName": "skip"}}

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )
        fin._setup_old_hub_as_secondary()

        primary.delete_custom_resource.assert_not_called()
        primary.create_custom_resource.assert_not_called()

    def test_setup_old_hub_as_secondary_raises_on_delete_failure(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Delete failure for stale active restore must raise SwitchoverError."""
        primary = Mock()
        primary.get_custom_resource.return_value = {"spec": {"veleroManagedClustersBackupName": "latest"}}
        primary.delete_custom_resource.side_effect = ApiException(status=500, reason="Internal Server Error")

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with pytest.raises(
            SwitchoverError,
            match="Failed to delete stale passive sync restore on old primary hub",
        ):
            fin._setup_old_hub_as_secondary()

    def test_setup_old_hub_as_secondary_raises_on_deletion_timeout(
        self, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Timeout waiting for restore deletion must raise FatalError without attempting create."""
        from lib.exceptions import FatalError

        primary = Mock()
        primary.dry_run = False
        # First call returns stale restore; all subsequent calls also return it (never deleted)
        primary.get_custom_resource.return_value = {"spec": {"veleroManagedClustersBackupName": "latest"}}

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            old_hub_action="secondary",
        )

        with patch("modules.finalization.wait_for_condition", return_value=False):
            with pytest.raises(FatalError, match="Timeout waiting for restore"):
                fin._setup_old_hub_as_secondary()

        primary.delete_custom_resource.assert_called_once()
        primary.create_custom_resource.assert_not_called()

    def test_cleanup_restore_resources_archives_before_deletion(
        self, finalization, mock_secondary_client, mock_state_manager
    ):
        """Test that restore resources are archived before deletion."""
        # Mock a restore resource with full details
        mock_restore = {
            "metadata": {
                "name": "restore-acm-passive-sync",
                "namespace": "open-cluster-management-backup",
                "creationTimestamp": "2025-11-28T10:00:00Z",
            },
            "spec": {
                "veleroManagedClustersBackupName": "latest",
                "veleroCredentialsBackupName": "latest",
                "veleroResourcesBackupName": "latest",
                "syncRestoreWithNewBackups": True,
                "cleanupBeforeRestore": "CleanupRestored",
            },
            "status": {
                "phase": "Finished",
                "lastMessage": "Restore completed successfully",
                "veleroManagedClustersRestoreName": "acm-managed-clusters-12345",
                "veleroCredentialsRestoreName": "acm-credentials-12345",
                "veleroResourcesRestoreName": "acm-resources-12345",
            },
        }

        # Mock list_custom_resources to return the restore when listing
        mock_secondary_client.list_custom_resources.return_value = [mock_restore]

        finalization._cleanup_restore_resources()

        # Verify archive was saved to state
        mock_state_manager.set_config.assert_called_once()
        call_args = mock_state_manager.set_config.call_args
        assert call_args[0][0] == "archived_restores"

        archived = call_args[0][1]
        assert len(archived) == 1
        assert archived[0]["name"] == "restore-acm-passive-sync"
        assert archived[0]["phase"] == "Finished"
        assert archived[0]["velero_backups"]["veleroManagedClustersBackupName"] == "latest"
        assert archived[0]["archived_at"] is not None

        # Verify delete was called
        mock_secondary_client.delete_custom_resource.assert_called_once()

    @pytest.mark.parametrize(
        ("delete_error", "expect_warning"),
        [
            (ApiException(status=404, reason="Not Found"), False),
            (ApiException(status=500, reason="Internal Server Error"), True),
            (RuntimeError("boom"), True),
        ],
    )
    def test_cleanup_restore_resources_handles_delete_outcomes(
        self,
        finalization,
        mock_secondary_client,
        mock_state_manager,
        delete_error,
        expect_warning,
        caplog,
    ):
        """Cleanup should ignore 404s but fail closed for other delete errors."""
        mock_restore = {
            "metadata": {"name": "restore-acm-passive-sync"},
            "spec": {},
            "status": {"phase": "Finished"},
        }
        mock_secondary_client.list_custom_resources.return_value = [mock_restore]
        mock_secondary_client.delete_custom_resource.side_effect = delete_error

        with caplog.at_level(logging.WARNING):
            if expect_warning:
                with pytest.raises(SwitchoverError, match="Failed to delete restore resource"):
                    finalization._cleanup_restore_resources()
            else:
                finalization._cleanup_restore_resources()

        mock_state_manager.set_config.assert_called_once()
        if expect_warning:
            assert "Error deleting restore restore-acm-passive-sync" in caplog.text
        else:
            assert "Error deleting restore restore-acm-passive-sync" not in caplog.text

    def test_archive_restore_details_extracts_all_fields(self, finalization):
        """Test that _archive_restore_details extracts all important fields."""
        restore = {
            "metadata": {
                "name": "test-restore",
                "namespace": "test-ns",
                "uid": "abc-123-def-456",
                "resourceVersion": "12345",
                "generation": 2,
                "creationTimestamp": "2025-11-28T12:00:00Z",
                "labels": {"app": "acm-backup"},
                "annotations": {"note": "switchover test"},
                "ownerReferences": [{"name": "backup-operator", "kind": "Deployment"}],
            },
            "spec": {
                "veleroManagedClustersBackupName": "backup-mc",
                "veleroCredentialsBackupName": "backup-creds",
                "veleroResourcesBackupName": "backup-res",
                "syncRestoreWithNewBackups": False,
                "restoreSyncInterval": "10m",
                "cleanupBeforeRestore": "None",
            },
            "status": {
                "phase": "Enabled",
                "lastMessage": "Sync in progress",
                "veleroManagedClustersRestoreName": "restore-mc-123",
                "veleroCredentialsRestoreName": "restore-creds-123",
                "veleroResourcesRestoreName": "restore-res-123",
            },
        }

        result = finalization._archive_restore_details(restore)

        # Metadata fields
        assert result["name"] == "test-restore"
        assert result["namespace"] == "test-ns"
        assert result["uid"] == "abc-123-def-456"
        assert result["resource_version"] == "12345"
        assert result["generation"] == 2
        assert result["creation_timestamp"] == "2025-11-28T12:00:00Z"
        assert result["labels"] == {"app": "acm-backup"}
        assert result["annotations"] == {"note": "switchover test"}
        assert result["owner_references"] == [{"name": "backup-operator", "kind": "Deployment"}]
        assert result["archived_at"] is not None
        # Spec fields
        assert result["velero_backups"]["veleroManagedClustersBackupName"] == "backup-mc"
        assert result["restore_sync_interval"] == "10m"
        # Status fields
        assert result["phase"] == "Enabled"
        assert result["last_message"] == "Sync in progress"
        assert result["velero_managed_clusters_restore_name"] == "restore-mc-123"


@pytest.mark.integration
class TestFinalizationBackupOwnershipFallbackIntegration:
    """Integration-style checks for ACM backup ownership fallback with real state persistence."""

    @patch("modules.finalization.time")
    def test_verify_new_backups_persists_fallback_detected_backup(self, mock_time, mock_secondary_client, tmp_path):
        """A label-missing ACM-style backup should still be persisted via the fallback signal."""
        from lib.utils import StateManager

        mock_time.time.side_effect = [0, 0, 1, 2]
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            [],
            [
                {
                    "metadata": {"name": "acm-managed-clusters-schedule-20260306100000"},
                    "status": {"phase": "Completed"},
                }
            ],
        ]

        state = StateManager(str(tmp_path / "state.json"))
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=state,
            acm_version="2.12.0",
        )

        fin._verify_new_backups(timeout=10)

        assert state.get_config("post_switchover_backup_name") == "acm-managed-clusters-schedule-20260306100000"
