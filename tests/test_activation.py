"""Unit tests for modules/activation.py.

Tests cover SecondaryActivation class for activating the secondary hub.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.activation as activation_module
from lib.constants import BACKUP_NAMESPACE

SecondaryActivation = activation_module.SecondaryActivation


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
def activation_passive(mock_secondary_client, mock_state_manager):
    """Create SecondaryActivation instance (passive method)."""
    return SecondaryActivation(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        method="passive",
    )


@pytest.fixture
def activation_full(mock_secondary_client, mock_state_manager):
    """Create SecondaryActivation instance (full method)."""
    return SecondaryActivation(
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        method="full",
    )


@pytest.mark.unit
class TestSecondaryActivation:
    """Tests for SecondaryActivation class."""

    def test_initialization(self, mock_secondary_client, mock_state_manager):
        """Test SecondaryActivation initialization."""
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        assert act.secondary == mock_secondary_client
        assert act.state == mock_state_manager
        assert act.method == "passive"

    @patch("modules.activation.wait_for_condition")
    def test_activate_passive_success(
        self, mock_wait, activation_passive, mock_secondary_client, mock_state_manager
    ):
        """Test successful passive activation."""
        mock_wait.return_value = True

        # Mock verify_passive_sync
        mock_secondary_client.get_custom_resource.return_value = {
            "status": {"phase": "Enabled", "lastMessage": "Synced"}
        }

        # Mock patch for activation
        mock_secondary_client.patch_custom_resource.return_value = True

        result = activation_passive.activate()

        assert result is True

        # Verify steps
        # 1. Verify passive sync
        mock_secondary_client.get_custom_resource.assert_called_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            namespace=BACKUP_NAMESPACE,
        )

        # 2. Activate (patch)
        mock_secondary_client.patch_custom_resource.assert_called_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            patch={"spec": {"veleroManagedClustersBackupName": "latest"}},
            namespace=BACKUP_NAMESPACE,
        )

        # 3. Wait for completion
        mock_wait.assert_called_once()
        assert "restore-acm-passive-sync" in mock_wait.call_args[0][0]

    @patch("modules.activation.wait_for_condition")
    def test_activate_full_success(
        self, mock_wait, activation_full, mock_secondary_client
    ):
        """Test successful full activation."""
        mock_wait.return_value = True

        # Mock check for existing restore (returns None -> create new)
        # Then get_custom_resource is called inside wait loop (mocked by wait_for_condition, but we mock return for safety)
        mock_secondary_client.get_custom_resource.return_value = None

        result = activation_full.activate()

        assert result is True

        # Verify creation
        mock_secondary_client.create_custom_resource.assert_called_once()
        args = mock_secondary_client.create_custom_resource.call_args[1]
        assert args["body"]["metadata"]["name"] == "restore-acm-full"

        # Verify wait
        mock_wait.assert_called_once()
        assert "restore-acm-full" in mock_wait.call_args[0][0]

    def test_verify_passive_sync_failure(
        self, activation_passive, mock_secondary_client
    ):
        """Test failure when passive sync is not ready."""
        # Mock restore not found
        mock_secondary_client.get_custom_resource.return_value = None

        result = activation_passive.activate()

        assert result is False

    def test_verify_passive_sync_wrong_phase(
        self, activation_passive, mock_secondary_client
    ):
        """Test failure when passive sync is in wrong phase."""
        mock_secondary_client.get_custom_resource.return_value = {
            "status": {"phase": "Failed"}
        }

        result = activation_passive.activate()

        assert result is False

    @patch("modules.activation.wait_for_condition")
    def test_wait_for_restore_timeout(
        self, mock_wait, activation_passive, mock_secondary_client
    ):
        """Test timeout waiting for restore."""
        mock_wait.return_value = False  # Timeout

        # Mock verify success so we get to the wait step
        mock_secondary_client.get_custom_resource.return_value = {
            "status": {"phase": "Enabled"}
        }

        result = activation_passive.activate()

        assert result is False

    def test_poll_restore_logic(self, activation_passive, mock_secondary_client):
        """Test the internal _poll_restore logic via _wait_for_restore_completion."""
        # This is tricky because _poll_restore is internal closure.
        # We can test it by NOT mocking wait_for_condition but mocking time to avoid sleep,
        # OR we can trust wait_for_condition tests and just verify we pass the right callback.
        # Let's try to run it with a mocked wait_for_condition that executes the callback.

        mock_secondary_client.get_custom_resource.return_value = {
            "status": {"phase": "Finished"}
        }

        with patch("modules.activation.wait_for_condition") as mock_wait:
            # Define side effect to execute the callback passed to wait_for_condition
            def side_effect(desc, callback, **kwargs):
                return callback()[0]  # Execute callback and return done status

            mock_wait.side_effect = side_effect

            # We need to bypass the earlier steps to get to wait
            activation_passive.state.is_step_completed.side_effect = (
                lambda step: step != "wait_restore_completion"
            )

            activation_passive._wait_for_restore_completion()

            # Verify get_custom_resource was called by the callback
            assert mock_secondary_client.get_custom_resource.called
