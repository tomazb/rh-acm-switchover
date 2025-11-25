"""Unit tests for modules/rollback.py.

Tests cover Rollback class for reverting changes to primary hub.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.rollback as rollback_module
from lib.constants import BACKUP_NAMESPACE, OBSERVABILITY_NAMESPACE

Rollback = rollback_module.Rollback


@pytest.fixture
def mock_primary_client():
    """Create a mock KubeClient for primary hub."""
    return Mock()


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub."""
    return Mock()


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager."""
    return Mock()


@pytest.fixture
def mock_backup_manager():
    """Create a mock BackupScheduleManager."""
    with patch("modules.rollback.BackupScheduleManager") as mock:
        yield mock.return_value


@pytest.fixture
def rollback_with_obs(
    mock_primary_client, mock_secondary_client, mock_state_manager, mock_backup_manager
):
    """Create Rollback instance with observability."""
    return Rollback(
        primary_client=mock_primary_client,
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        has_observability=True,
    )


@pytest.fixture
def rollback_no_obs(
    mock_primary_client, mock_secondary_client, mock_state_manager, mock_backup_manager
):
    """Create Rollback instance without observability."""
    return Rollback(
        primary_client=mock_primary_client,
        secondary_client=mock_secondary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        has_observability=False,
    )


@pytest.mark.unit
class TestRollback:
    """Tests for Rollback class."""

    def test_initialization(
        self, mock_primary_client, mock_secondary_client, mock_state_manager
    ):
        """Test Rollback initialization."""
        rb = Rollback(
            primary_client=mock_primary_client,
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=True,
        )

        assert rb.primary == mock_primary_client
        assert rb.secondary == mock_secondary_client
        assert rb.has_observability is True
        assert rb.backup_manager is not None

    def test_rollback_success_with_observability(
        self,
        rollback_with_obs,
        mock_primary_client,
        mock_secondary_client,
        mock_backup_manager,
    ):
        """Test successful rollback with observability."""
        # Mock resources
        mock_secondary_client.delete_custom_resource.return_value = True

        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {
                    "name": "cluster1",
                    "annotations": {
                        "import.open-cluster-management.io/disable-auto-import": "true"
                    },
                }
            }
        ]

        result = rollback_with_obs.rollback()

        assert result is True

        # Verify secondary deactivation
        mock_secondary_client.delete_custom_resource.assert_called_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-full",
            namespace=BACKUP_NAMESPACE,
        )

        # Verify auto-import re-enabled
        mock_primary_client.patch_custom_resource.assert_called_once()

        # Verify Thanos restart
        mock_primary_client.scale_statefulset.assert_called_with(
            name="observability-thanos-compact",
            namespace=OBSERVABILITY_NAMESPACE,
            replicas=1,
        )

        # Verify backup schedule unpaused
        mock_backup_manager.ensure_enabled.assert_called_with("2.12.0")

    def test_rollback_success_without_observability(
        self, rollback_no_obs, mock_primary_client
    ):
        """Test successful rollback without observability."""
        mock_primary_client.list_custom_resources.return_value = []

        result = rollback_no_obs.rollback()

        assert result is True
        # Should not scale Thanos
        mock_primary_client.scale_statefulset.assert_not_called()

    def test_enable_auto_import_skips_local_cluster(
        self, rollback_no_obs, mock_primary_client
    ):
        """Test that auto-import enabling skips local-cluster."""
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {
                    "name": "local-cluster",
                    "annotations": {
                        "import.open-cluster-management.io/disable-auto-import": "true"
                    },
                }
            },
            {
                "metadata": {
                    "name": "remote-cluster",
                    "annotations": {
                        "import.open-cluster-management.io/disable-auto-import": "true"
                    },
                }
            },
        ]

        rollback_no_obs._enable_auto_import()

        # Should only patch remote-cluster
        assert mock_primary_client.patch_custom_resource.call_count == 1
        args = mock_primary_client.patch_custom_resource.call_args[1]
        assert args["name"] == "remote-cluster"

    def test_enable_auto_import_skips_clean_clusters(
        self, rollback_no_obs, mock_primary_client
    ):
        """Test that auto-import enabling skips clusters without the annotation."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "clean-cluster", "annotations": {}}}
        ]

        rollback_no_obs._enable_auto_import()

        mock_primary_client.patch_custom_resource.assert_not_called()

    def test_rollback_failure_handling(self, rollback_with_obs, mock_secondary_client):
        """Test rollback failure handling."""
        mock_secondary_client.delete_custom_resource.side_effect = Exception(
            "API Error"
        )

        result = rollback_with_obs.rollback()

        assert result is False
