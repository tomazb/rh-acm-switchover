"""Unit tests for modules/primary_prep.py.

Tests cover PrimaryPreparation class for preparing the primary hub.
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.primary_prep as primary_prep_module
from lib.constants import OBSERVABILITY_NAMESPACE

PrimaryPreparation = primary_prep_module.PrimaryPreparation


@pytest.fixture
def mock_primary_client():
    """Create a mock KubeClient for primary hub."""
    client = Mock()
    client.list_managed_clusters = Mock(return_value=[])
    client.patch_managed_cluster = Mock()
    return client


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager."""
    mock = Mock()
    mock.is_step_completed.return_value = False
    return mock


@pytest.fixture
def primary_prep_with_obs(mock_primary_client, mock_state_manager):
    """Create PrimaryPreparation instance with observability."""
    return PrimaryPreparation(
        primary_client=mock_primary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        has_observability=True,
    )


@pytest.fixture
def primary_prep_no_obs(mock_primary_client, mock_state_manager):
    """Create PrimaryPreparation instance without observability."""
    return PrimaryPreparation(
        primary_client=mock_primary_client,
        state_manager=mock_state_manager,
        acm_version="2.12.0",
        has_observability=False,
    )


@pytest.mark.unit
class TestPrimaryPreparation:
    """Tests for PrimaryPreparation class."""

    def test_initialization(self, mock_primary_client, mock_state_manager):
        """Test PrimaryPreparation initialization."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version="2.12.0",
            has_observability=True,
        )

        assert prep.primary == mock_primary_client
        assert prep.state == mock_state_manager
        assert prep.acm_version == "2.12.0"
        assert prep.has_observability is True

    @patch("time.sleep")
    def test_prepare_success_with_observability(self, mock_sleep, primary_prep_with_obs, mock_primary_client, mock_state_manager):
        """Test successful preparation with observability."""

        # Mock all list_custom_resources calls
        def list_side_effect(*args, **kwargs):
            plural = kwargs.get("plural", "")
            if plural == "backupschedules":
                return [{"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}]
            elif plural == "managedclusters":
                return [
                    {"metadata": {"name": "cluster1", "labels": {}}},
                    {"metadata": {"name": "cluster2", "labels": {}}},
                ]
            return []

        mock_primary_client.list_custom_resources.side_effect = list_side_effect
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "cluster2"}},
        ]
        mock_primary_client.patch_custom_resource.return_value = True
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = []

        result = primary_prep_with_obs.prepare()

        assert result is True
        assert mock_state_manager.mark_step_completed.call_count >= 3

    def test_prepare_success_without_observability(self, primary_prep_no_obs, mock_primary_client, mock_state_manager):
        """Test successful preparation without observability."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.patch_custom_resource.return_value = True

        result = primary_prep_no_obs.prepare()

        assert result is True
        # Should not scale Thanos since no observability
        mock_primary_client.scale_statefulset.assert_not_called()

    def test_prepare_steps_already_completed(self, primary_prep_with_obs, mock_state_manager):
        """Test skipping already completed steps."""
        mock_state_manager.is_step_completed.return_value = True

        result = primary_prep_with_obs.prepare()

        assert result is True

    def test_pause_backup_schedule_acm_212(self, primary_prep_with_obs, mock_primary_client):
        """Test pausing backup schedule for ACM 2.12+."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]

        primary_prep_with_obs._pause_backup_schedule()

        mock_primary_client.patch_custom_resource.assert_called_once()
        call_kwargs = mock_primary_client.patch_custom_resource.call_args[1]
        assert call_kwargs["patch"] == {"spec": {"paused": True}}

    def test_pause_backup_schedule_already_paused(self, primary_prep_with_obs, mock_primary_client):
        """Test when backup schedule is already paused."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": True}}
        ]

        primary_prep_with_obs._pause_backup_schedule()

        # Should not patch if already paused
        mock_primary_client.patch_custom_resource.assert_not_called()

    def test_pause_backup_schedule_not_found(self, primary_prep_with_obs, mock_primary_client):
        """Test when no backup schedule exists."""
        mock_primary_client.list_custom_resources.return_value = []

        # Should handle gracefully
        primary_prep_with_obs._pause_backup_schedule()

        mock_primary_client.patch_custom_resource.assert_not_called()

    @pytest.mark.parametrize(
        "acm_version,should_patch",
        [
            ("2.12.0", True),
            ("2.13.0", True),
            ("2.11.5", False),
            ("2.10.0", False),
        ],
    )
    def test_pause_version_handling(self, mock_primary_client, mock_state_manager, acm_version, should_patch):
        """Test version-specific pause behavior."""
        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=mock_state_manager,
            acm_version=acm_version,
            has_observability=False,
        )

        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}
        ]

        prep._pause_backup_schedule()

        if should_patch:
            mock_primary_client.patch_custom_resource.assert_called_once()
        else:
            # For ACM < 2.12, use delete instead
            mock_primary_client.delete_custom_resource.assert_called_once()

    def test_disable_auto_import_with_clusters(self, primary_prep_with_obs, mock_primary_client):
        """Test disabling auto-import on managed clusters."""
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1", "labels": {}}},
            {"metadata": {"name": "local-cluster", "labels": {}}},
            {"metadata": {"name": "cluster2", "labels": {}}},
        ]
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
            {"metadata": {"name": "cluster2"}},
        ]

        primary_prep_with_obs._disable_auto_import()

        # Should patch all clusters except local-cluster
        assert mock_primary_client.patch_managed_cluster.call_count == 2

    def test_disable_auto_import_no_clusters(self, primary_prep_with_obs, mock_primary_client):
        """Test when no managed clusters exist."""
        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.list_managed_clusters.return_value = []

        primary_prep_with_obs._disable_auto_import()

        mock_primary_client.patch_managed_cluster.assert_not_called()

    @patch("time.sleep")
    def test_scale_down_thanos(self, mock_sleep, primary_prep_with_obs, mock_primary_client):
        """Test scaling down Thanos compactor."""
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = []  # No pods after scaling down

        primary_prep_with_obs._scale_down_thanos_compactor()

        mock_primary_client.scale_statefulset.assert_called_once_with(
            namespace=OBSERVABILITY_NAMESPACE,
            name="observability-thanos-compact",
            replicas=0,
        )
        mock_sleep.assert_called_once_with(5)  # Verify wait was called

    def test_prepare_error_handling(self, primary_prep_with_obs, mock_primary_client, mock_state_manager):
        """Test error handling during preparation."""
        mock_primary_client.list_custom_resources.side_effect = Exception("API error")

        result = primary_prep_with_obs.prepare()

        assert result is False
        mock_state_manager.add_error.assert_called_once()


@pytest.mark.integration
class TestPrimaryPreparationIntegration:
    """Integration tests for PrimaryPreparation."""

    @patch("time.sleep")
    def test_full_workflow_with_state(self, mock_sleep, mock_primary_client, tmp_path):
        """Test complete workflow with real StateManager."""
        from lib.utils import Phase, StateManager

        state = StateManager(str(tmp_path / "state.json"))
        state.set_phase(Phase.PRIMARY_PREP)

        prep = PrimaryPreparation(
            primary_client=mock_primary_client,
            state_manager=state,
            acm_version="2.12.0",
            has_observability=True,
        )

        # Mock successful flow
        def list_side_effect(*args, **kwargs):
            plural = kwargs.get("plural", "")
            if plural == "backupschedules":
                return [{"metadata": {"name": "schedule-rhacm"}, "spec": {"paused": False}}]
            elif plural == "managedclusters":
                return [{"metadata": {"name": "cluster1", "labels": {}}}]
            return []

        mock_primary_client.list_custom_resources.side_effect = list_side_effect
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.patch_custom_resource.return_value = True
        mock_primary_client.scale_statefulset.return_value = {"status": "scaled"}
        mock_primary_client.get_pods.return_value = []

        result = prep.prepare()

        assert result is True
        assert state.is_step_completed("pause_backup_schedule")
        assert state.is_step_completed("disable_auto_import")
        assert state.is_step_completed("scale_down_thanos")
