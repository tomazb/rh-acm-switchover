"""Unit tests for modules/finalization.py.

Tests cover Finalization class for completing the switchover.
"""

import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.finalization as finalization_module

Finalization = finalization_module.Finalization


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

    def test_initialization(self, mock_secondary_client, mock_state_manager):
        """Test Finalization initialization."""
        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
        )

        assert fin.secondary == mock_secondary_client
        assert fin.state == mock_state_manager
        assert fin.backup_manager is not None

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
            [{"metadata": {"name": "backup-1"}, "status": {"phase": "InProgress"}}],  # Loop iteration 2 - new backup
            [
                {
                    "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                    "status": {"phase": "Completed", "completionTimestamp": backup_ts},
                }
            ],  # verify_backup_integrity
        ]

        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Running"},
        }
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
            [{"metadata": {"name": "new-backup"}, "status": {"phase": "Completed"}}],
        ]

        finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources.call_count == 3

    @patch("modules.finalization.time")
    def test_verify_new_backups_timeout(self, mock_time, finalization, mock_secondary_client):
        """Test backup verification timeout."""
        # Mock time to simulate timeout
        # Calls: start_time, check 1, check 2, final check after loop
        mock_time.time.side_effect = [0, 10, 45, 51]

        mock_secondary_client.list_custom_resources.return_value = []

        finalization._verify_new_backups(timeout=50)

        # Should log warning but not crash
        assert mock_secondary_client.list_custom_resources.called

    def test_verify_backup_integrity_success(self, finalization, mock_secondary_client):
        """Backup integrity should pass for a recent completed backup with no errors."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                "status": {"phase": "Completed", "completionTimestamp": backup_ts, "errors": 0, "warnings": 0},
            }
        ]
        mock_secondary_client.get_pods.return_value = []

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_skips_age_without_new_backup(self, finalization, mock_secondary_client):
        """Backup age enforcement should be skipped if no new backup was detected."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                "status": {"phase": "Completed", "completionTimestamp": backup_ts, "errors": 0, "warnings": 0},
            }
        ]
        mock_secondary_client.get_pods.return_value = []
        finalization.state.get_config.return_value = False

        finalization._verify_backup_integrity(max_age_seconds=600)

    def test_verify_backup_integrity_enforces_age_with_new_backup(self, finalization, mock_secondary_client):
        """Backup age enforcement should fail when a new backup was detected but is too old."""
        backup_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                "status": {"phase": "Completed", "completionTimestamp": backup_ts, "errors": 0, "warnings": 0},
            }
        ]
        mock_secondary_client.get_pods.return_value = []
        finalization.state.get_config.return_value = True

        with pytest.raises(RuntimeError):
            finalization._verify_backup_integrity(max_age_seconds=600)

    @patch("modules.finalization.wait_for_condition")
    def test_verify_backup_integrity_waits_for_completion(self, mock_wait, finalization, mock_secondary_client):
        """Backup integrity should wait for the latest backup to complete."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_wait.return_value = True
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                "status": {"phase": "InProgress"},
            }
        ]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
            "status": {"phase": "Completed", "completionTimestamp": backup_ts, "errors": 0, "warnings": 0},
        }
        mock_secondary_client.get_pods.return_value = []

        finalization._verify_backup_integrity(max_age_seconds=600)

        mock_wait.assert_called_once()

    def test_verify_backup_integrity_fails_on_errors(self, finalization, mock_secondary_client):
        """Backup integrity should fail when backup reports errors."""
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                "status": {"phase": "Completed", "completionTimestamp": backup_ts, "errors": 2},
            }
        ]
        mock_secondary_client.get_pods.return_value = []

        with pytest.raises(RuntimeError):
            finalization._verify_backup_integrity(max_age_seconds=600)

    @patch("modules.finalization.wait_for_condition")
    def test_disable_observability_on_secondary_deletes_mco(
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

        fin._disable_observability_on_secondary()

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

        with pytest.raises(RuntimeError):
            finalization._verify_backup_schedule_enabled()

    @patch("modules.finalization.time")
    def test_verify_multiclusterhub_health_failure(self, mock_time, finalization, mock_secondary_client):
        """MCH verification should fail when not running, without real-time waits."""
        # Simulate fast timeout without real sleeping:
        # start=0, then enough time has elapsed on next checks to exceed timeout.
        mock_time.time.side_effect = [0, 301, 302]
        mock_time.sleep.return_value = None

        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Degraded"},
        }
        mock_secondary_client.get_pods.return_value = [
            {"metadata": {"name": "acm-pod"}, "status": {"phase": "Running"}}
        ]

        with pytest.raises(RuntimeError):
            finalization._verify_multiclusterhub_health()

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
            [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}],  # verify_backup_schedule_enabled
            [{"metadata": {"name": "schedule"}, "spec": {}, "status": {"phase": "Enabled"}}],  # fix_backup_collision
            [],  # Initial backups
            [],  # Loop iteration 1
            [{"metadata": {"name": "backup-1"}, "status": {"phase": "InProgress"}}],  # Loop iteration 2 - new backup
            [
                {
                    "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                    "status": {"phase": "Completed", "completionTimestamp": backup_ts},
                }
            ],  # verify_backup_integrity
        ]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Running"},
        }
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
        self, mock_time_time, mock_time_sleep, mock_secondary_client, mock_state_manager, mock_backup_manager
    ):
        """Test that _verify_old_hub_state IS called when old_hub_action is 'secondary'."""
        # Mock time to avoid real waits
        mock_time_time.return_value = 0
        mock_time_sleep.return_value = None
        backup_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        primary = Mock()
        primary.list_custom_resources.return_value = []
        primary.get_pods.return_value = []

        fin = Finalization(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            primary_client=primary,
            primary_has_observability=False,
            old_hub_action="secondary",
        )

        # Mock all required responses with side_effect for sequential calls
        # Order: _cleanup_restore_resources, verify_backup_schedule_enabled, fix_backup_collision, verify_new_backups (2x)
        mock_secondary_client.list_custom_resources.side_effect = [
            [],  # _cleanup_restore_resources - no restores to clean up
            [{"metadata": {"name": "schedule"}, "spec": {"paused": False}}],  # verify_backup_schedule_enabled
            [{"metadata": {"name": "schedule"}, "spec": {}, "status": {"phase": "Enabled"}}],  # fix_backup_collision
            [],  # Initial backups
            [{"metadata": {"name": "backup-1"}, "status": {"phase": "InProgress"}}],  # New backup detected
            [
                {
                    "metadata": {"name": "backup-1", "creationTimestamp": backup_ts},
                    "status": {"phase": "Completed", "completionTimestamp": backup_ts},
                }
            ],  # verify_backup_integrity
        ]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "multiclusterhub"},
            "status": {"phase": "Running"},
        }
        mock_secondary_client.get_pods.return_value = []

        with patch.object(fin, "_verify_old_hub_state") as mock_verify:
            result = fin.finalize()

            assert result is True
            # _verify_old_hub_state SHOULD be called when old_hub_action is 'secondary'
            mock_verify.assert_called_once()

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
