"""Unit tests for modules/backup_schedule.py.

Tests cover BackupScheduleManager for enabling/restoring backup schedules.
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.backup_schedule as backup_module
from lib.constants import BACKUP_NAMESPACE

BackupScheduleManager = backup_module.BackupScheduleManager


@pytest.fixture
def mock_kube_client():
    """Create a mock KubeClient."""
    return Mock()


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager."""
    mock = Mock()
    mock.get_config.return_value = None
    return mock


@pytest.fixture
def schedule_manager(mock_kube_client, mock_state_manager):
    """Create BackupScheduleManager instance."""
    return BackupScheduleManager(
        kube_client=mock_kube_client,
        state_manager=mock_state_manager,
        hub_label="primary",
    )


@pytest.mark.unit
class TestBackupScheduleManager:
    """Tests for BackupScheduleManager class."""

    def test_schedule_already_enabled(self, schedule_manager, mock_kube_client):
        """Test when backup schedule is already enabled."""
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]

        schedule_manager.ensure_enabled("2.12.0")

        # Should not try to patch if already enabled
        mock_kube_client.patch_custom_resource.assert_not_called()

    def test_schedule_not_paused_field_missing(
        self, schedule_manager, mock_kube_client
    ):
        """Test when paused field is missing (implicitly enabled)."""
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {}}  # No paused field
        ]

        schedule_manager.ensure_enabled("2.12.0")

        # Should not patch if paused field doesn't exist (default enabled)
        mock_kube_client.patch_custom_resource.assert_not_called()

    def test_unpause_schedule_acm_212_and_above(
        self, schedule_manager, mock_kube_client
    ):
        """Test unpausing schedule for ACM 2.12.0+."""
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": True}}
        ]

        schedule_manager.ensure_enabled("2.12.0")

        # Should patch with paused: false for ACM 2.12+
        mock_kube_client.patch_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name="schedule-rhacm",
            patch={"spec": {"paused": False}},
            namespace=BACKUP_NAMESPACE,
        )

    def test_unpause_schedule_acm_211(self, schedule_manager, mock_kube_client):
        """Test handling paused schedule for ACM < 2.12.0."""
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": True}}
        ]

        schedule_manager.ensure_enabled("2.11.0")

        # Should not patch for older ACM versions (handled via restore)
        mock_kube_client.patch_custom_resource.assert_not_called()

    def test_restore_saved_schedule_when_missing(
        self, schedule_manager, mock_kube_client, mock_state_manager
    ):
        """Test restoring saved schedule when none exists."""
        mock_kube_client.list_custom_resources.return_value = []  # No schedules

        saved_schedule = {
            "metadata": {"name": "saved-schedule"},
            "spec": {"schedule": "0 */6 * * *"},
        }
        mock_state_manager.get_config.return_value = saved_schedule

        schedule_manager.ensure_enabled("2.12.0")

        # Should create the saved schedule
        mock_kube_client.create_custom_resource.assert_called_once()

    def test_no_schedule_and_none_saved(
        self, schedule_manager, mock_kube_client, mock_state_manager
    ):
        """Test warning when no schedule exists and none saved."""
        mock_kube_client.list_custom_resources.return_value = []
        mock_state_manager.get_config.return_value = None

        # Should handle gracefully (log warning)
        schedule_manager.ensure_enabled("2.12.0")

        mock_kube_client.create_custom_resource.assert_not_called()

    @pytest.mark.parametrize(
        "acm_version,should_patch",
        [
            ("2.12.0", True),
            ("2.12.5", True),
            ("2.13.0", True),
            ("2.11.5", False),
            ("2.10.0", False),
        ],
    )
    def test_version_based_pause_handling(
        self, schedule_manager, mock_kube_client, acm_version, should_patch
    ):
        """Test pause handling for different ACM versions."""
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": True}}
        ]

        schedule_manager.ensure_enabled(acm_version)

        if should_patch:
            mock_kube_client.patch_custom_resource.assert_called_once()
        else:
            mock_kube_client.patch_custom_resource.assert_not_called()

    def test_multiple_schedules_uses_first(self, schedule_manager, mock_kube_client):
        """Test that when multiple schedules exist, first one is used."""
        mock_kube_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-1"}, "spec": {"paused": True}},
            {"metadata": {"name": "schedule-2"}, "spec": {"paused": False}},
        ]

        schedule_manager.ensure_enabled("2.12.0")

        # Should patch the first schedule
        call_args = mock_kube_client.patch_custom_resource.call_args
        assert call_args[1]["name"] == "schedule-1"


@pytest.mark.integration
class TestBackupScheduleManagerIntegration:
    """Integration tests for BackupScheduleManager."""

    def test_full_workflow_with_state(self, mock_kube_client, tmp_path):
        """Test complete workflow with real StateManager."""
        from lib.utils import StateManager

        state = StateManager(str(tmp_path / "state.json"))
        state.set_config(
            "saved_backup_schedule",
            {"metadata": {"name": "saved"}, "spec": {"schedule": "0 */6 * * *"}},
        )

        manager = BackupScheduleManager(
            kube_client=mock_kube_client, state_manager=state, hub_label="secondary"
        )

        # Test restore from saved config
        mock_kube_client.list_custom_resources.return_value = []
        manager.ensure_enabled("2.12.0")

        mock_kube_client.create_custom_resource.assert_called_once()
