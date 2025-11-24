"""Unit tests for modules/decommission.py.

Tests cover Decommission class for removing ACM from old primary hub.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.decommission as decommission_module
from lib.constants import ACM_NAMESPACE, OBSERVABILITY_NAMESPACE

Decommission = decommission_module.Decommission


@pytest.fixture
def mock_primary_client():
    """Create a mock KubeClient for primary hub."""
    return Mock()


@pytest.fixture
def decommission_with_obs(mock_primary_client):
    """Create Decommission instance with observability."""
    return Decommission(
        primary_client=mock_primary_client,
        has_observability=True
    )


@pytest.fixture
def decommission_no_obs(mock_primary_client):
    """Create Decommission instance without observability."""
    return Decommission(
        primary_client=mock_primary_client,
        has_observability=False
    )


@pytest.mark.unit
class TestDecommission:
    """Tests for Decommission class."""

    def test_initialization(self, mock_primary_client):
        """Test Decommission initialization."""
        decomm = Decommission(
            primary_client=mock_primary_client,
            has_observability=True
        )
        
        assert decomm.primary == mock_primary_client
        assert decomm.has_observability is True

    @patch('modules.decommission.wait_for_condition')
    def test_decommission_non_interactive_with_observability(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test non-interactive decommission with observability."""
        mock_wait.return_value = True
        
        # Mock resources
        mock_primary_client.list_custom_resources.return_value = [
            {'metadata': {'name': 'observability'}}
        ]
        mock_primary_client.delete_custom_resource.return_value = True
        
        result = decommission_with_obs.decommission(interactive=False)
        
        assert result is True
        # Verify deletion calls
        assert mock_primary_client.delete_custom_resource.called

    @patch('modules.decommission.wait_for_condition')
    def test_decommission_non_interactive_without_observability(self, mock_wait, decommission_no_obs, mock_primary_client):
        """Test non-interactive decommission without observability."""
        mock_wait.return_value = True
        
        # Mock resources
        mock_primary_client.list_custom_resources.return_value = [
            {'metadata': {'name': 'cluster1'}},
            {'metadata': {'name': 'cluster2'}},
        ]
        mock_primary_client.delete_custom_resource.return_value = True
        
        result = decommission_no_obs.decommission(interactive=False)
        
        assert result is True

    @patch('modules.decommission.confirm_action')
    @patch('modules.decommission.wait_for_condition')
    def test_decommission_interactive_user_cancels(self, mock_wait, mock_confirm, decommission_with_obs):
        """Test interactive decommission when user cancels."""
        mock_confirm.return_value = False  # User cancels
        
        result = decommission_with_obs.decommission(interactive=True)
        
        assert result is False

    @patch('modules.decommission.confirm_action')
    @patch('modules.decommission.wait_for_condition')
    def test_decommission_interactive_user_confirms(self, mock_wait, mock_confirm, decommission_with_obs, mock_primary_client):
        """Test interactive decommission when user confirms."""
        mock_confirm.return_value = True  # User confirms all prompts
        mock_wait.return_value = True
        
        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.delete_custom_resource.return_value = True
        
        result = decommission_with_obs.decommission(interactive=True)
        
        assert result is True

    @patch('modules.decommission.wait_for_condition')
    def test_delete_observability_with_resources(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test deleting observability resources."""
        mock_wait.return_value = True
        
        mock_primary_client.list_custom_resources.return_value = [
            {'metadata': {'name': 'observability', 'namespace': OBSERVABILITY_NAMESPACE}}
        ]
        
        decommission_with_obs._delete_observability()
        
        mock_primary_client.delete_custom_resource.assert_called_once()

    @patch('modules.decommission.wait_for_condition')
    def test_delete_observability_not_found(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test when no observability resources exist."""
        mock_primary_client.list_custom_resources.return_value = []
        
        # Should handle gracefully
        decommission_with_obs._delete_observability()
        
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_excludes_local(self, decommission_with_obs, mock_primary_client):
        """Test that local-cluster is excluded from deletion."""
        mock_primary_client.list_custom_resources.return_value = [
            {'metadata': {'name': 'cluster1'}},
            {'metadata': {'name': 'local-cluster'}},
            {'metadata': {'name': 'cluster2'}},
        ]
        
        decommission_with_obs._delete_managed_clusters()
        
        # Should delete cluster1 and cluster2, but not local-cluster
        assert mock_primary_client.delete_custom_resource.call_count == 2

    def test_delete_managed_clusters_none_found(self, decommission_with_obs, mock_primary_client):
        """Test when no managed clusters exist."""
        mock_primary_client.list_custom_resources.return_value = []
        
        decommission_with_obs._delete_managed_clusters()
        
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch('modules.decommission.wait_for_condition')
    def test_delete_multiclusterhub(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test deleting MultiClusterHub resource."""
        mock_wait.return_value = True
        
        mock_primary_client.list_custom_resources.return_value = [
            {'metadata': {'name': 'multiclusterhub', 'namespace': ACM_NAMESPACE}}
        ]
        
        decommission_with_obs._delete_multiclusterhub()
        
        mock_primary_client.delete_custom_resource.assert_called_once()

    @patch('modules.decommission.wait_for_condition')
    def test_delete_multiclusterhub_timeout(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test when MultiClusterHub deletion times out."""
        mock_wait.return_value = False  # Timeout
        
        mock_primary_client.list_custom_resources.return_value = [
            {'metadata': {'name': 'multiclusterhub', 'namespace': ACM_NAMESPACE}}
        ]
        mock_primary_client.delete_custom_resource.return_value = True
        
        # Timeout is logged as warning but doesn't raise exception
        decommission_with_obs._delete_multiclusterhub()
        
        # Verify deletion was attempted
        mock_primary_client.delete_custom_resource.assert_called_once()

    def test_decommission_error_handling(self, decommission_with_obs, mock_primary_client):
        """Test error handling during decommission."""
        mock_primary_client.list_custom_resources.side_effect = Exception("API error")
        
        result = decommission_with_obs.decommission(interactive=False)
        
        assert result is False

    @pytest.mark.parametrize("has_obs", [True, False])
    def test_decommission_observability_conditional(self, mock_primary_client, has_obs):
        """Test that observability deletion is conditional."""
        decomm = Decommission(
            primary_client=mock_primary_client,
            has_observability=has_obs
        )
        
        mock_primary_client.list_custom_resources.return_value = []
        
        with patch.object(decomm, '_delete_observability') as mock_delete_obs:
            decomm.decommission(interactive=False)
            
            if has_obs:
                mock_delete_obs.assert_called_once()
            else:
                mock_delete_obs.assert_not_called()


@pytest.mark.integration
class TestDecommissionIntegration:
    """Integration tests for Decommission workflows."""

    @patch('modules.decommission.wait_for_condition')
    def test_full_decommission_workflow(self, mock_wait, mock_primary_client):
        """Test complete decommission workflow."""
        mock_wait.return_value = True
        
        decomm = Decommission(
            primary_client=mock_primary_client,
            has_observability=True
        )
        
        # Mock all resources
        mock_primary_client.list_custom_resources.side_effect = [
            [{'metadata': {'name': 'observability'}}],  # MCO
            [{'metadata': {'name': 'cluster1'}}],  # ManagedClusters
            [{'metadata': {'name': 'multiclusterhub'}}],  # MCH
        ]
        mock_primary_client.delete_custom_resource.return_value = True
        
        result = decomm.decommission(interactive=False)
        
        assert result is True
        # Verify resources were deleted
        assert mock_primary_client.delete_custom_resource.call_count >= 3
