"""Unit tests for modules/finalization.py.

Tests cover Finalization class for completing the switchover.
"""

import pytest
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch, call

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.finalization as finalization_module

Finalization = finalization_module.Finalization


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub."""
    return Mock()


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager."""
    mock = Mock()
    mock.is_step_completed.return_value = False
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

        # Mock backup verification (immediate success)
        mock_secondary_client.list_custom_resources.side_effect = [
            [],  # Initial check: no backups
            [
                {"metadata": {"name": "backup-1"}, "status": {"phase": "InProgress"}}
            ],  # Second check: new backup
        ]

        result = finalization.finalize()

        assert result is True

        # Verify steps
        mock_backup_manager.ensure_enabled.assert_called_with("2.12.0")
        assert mock_state_manager.mark_step_completed.call_count == 2
        mock_state_manager.mark_step_completed.assert_has_calls(
            [call("enable_backup_schedule"), call("verify_new_backups")]
        )

    def test_finalize_skips_completed_steps(
        self, finalization, mock_state_manager, mock_backup_manager
    ):
        """Test that completed steps are skipped."""
        mock_state_manager.is_step_completed.return_value = True

        result = finalization.finalize()

        assert result is True
        mock_backup_manager.ensure_enabled.assert_not_called()
        # verify_new_backups is internal method, hard to assert not called directly without mocking class method,
        # but we can infer from lack of client calls if we didn't mock list_custom_resources

    @patch("modules.finalization.time")
    def test_verify_new_backups_success(
        self, mock_time, finalization, mock_secondary_client
    ):
        """Test backup verification logic finding a new backup."""
        mock_time.time.return_value = 0

        # Sequence of API calls:
        # 1. Initial list (empty)
        # 2. Loop 1 list (still empty)
        # 3. Loop 2 list (new backup found)
        mock_secondary_client.list_custom_resources.side_effect = [
            [],
            [],
            [{"metadata": {"name": "new-backup"}, "status": {"phase": "Finished"}}],
        ]

        finalization._verify_new_backups(timeout=10)

        assert mock_secondary_client.list_custom_resources.call_count == 3

    @patch("modules.finalization.time")
    def test_verify_new_backups_timeout(
        self, mock_time, finalization, mock_secondary_client
    ):
        """Test backup verification timeout."""
        # Mock time to simulate timeout
        # Start at 0, then check > timeout
        mock_time.time.side_effect = [0, 100, 200]

        mock_secondary_client.list_custom_resources.return_value = []

        finalization._verify_new_backups(timeout=50)

        # Should log warning but not crash
        assert mock_secondary_client.list_custom_resources.called

    def test_finalize_failure_handling(self, finalization, mock_backup_manager):
        """Test finalization failure handling."""
        mock_backup_manager.ensure_enabled.side_effect = Exception("Backup Error")

        result = finalization.finalize()

        assert result is False
