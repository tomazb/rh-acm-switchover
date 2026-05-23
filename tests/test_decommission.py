"""Unit tests for modules/decommission.py.

Tests cover Decommission class for removing ACM from old primary hub.
"""

import logging
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from kubernetes.client.exceptions import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.decommission as decommission_module
from lib.constants import ACM_NAMESPACE, OBSERVABILITY_NAMESPACE
from lib.exceptions import SwitchoverError
from lib.waiter import WaitConditionResult

Decommission = decommission_module.Decommission


@pytest.fixture
def mock_primary_client():
    """Create a mock KubeClient for primary hub."""
    client = Mock()
    client.list_managed_clusters = Mock(return_value=[])
    client.list_custom_resources = Mock(return_value=[])
    return client


@pytest.fixture
def decommission_with_obs(mock_primary_client):
    """Create Decommission instance with observability."""
    return Decommission(primary_client=mock_primary_client, has_observability=True)


@pytest.fixture
def decommission_no_obs(mock_primary_client):
    """Create Decommission instance without observability."""
    return Decommission(primary_client=mock_primary_client, has_observability=False)


@pytest.mark.unit
class TestDecommission:
    """Tests for Decommission class."""

    @patch("modules.decommission.wait_for_condition")
    def test_decommission_non_interactive_with_observability(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """Test non-interactive decommission with observability."""
        mock_wait.return_value = True

        # Mock resources
        mock_primary_client.list_custom_resources.return_value = [{"metadata": {"name": "observability"}}]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.delete_custom_resource.return_value = True

        result = decommission_with_obs.decommission(interactive=False)

        assert result is True
        # Verify deletion calls
        assert mock_primary_client.delete_custom_resource.called

    @patch("modules.decommission.wait_for_condition")
    def test_decommission_non_interactive_without_observability(
        self, mock_wait, decommission_no_obs, mock_primary_client
    ):
        """Test non-interactive decommission without observability."""
        mock_wait.return_value = True

        # Mock resources
        mock_primary_client.list_custom_resources.side_effect = [
            [],
            [{"metadata": {"name": "multiclusterhub"}}],
        ]
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "cluster2"}},
        ]
        mock_primary_client.delete_custom_resource.return_value = True

        result = decommission_no_obs.decommission(interactive=False)

        assert result is True

    @patch("modules.decommission.wait_for_condition")
    def test_decommission_dry_run_non_interactive_is_full_no_op(self, mock_wait, mock_primary_client):
        """Dry-run top-level decommission must not issue delete or wait calls anywhere."""
        dry_run_decommission = Decommission(
            primary_client=mock_primary_client,
            has_observability=True,
            dry_run=True,
        )
        mock_primary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "observability"}}],
            [{"metadata": {"name": "multiclusterhub"}}],
        ]
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
        ]

        result = dry_run_decommission.decommission(interactive=False)

        assert result is True
        mock_primary_client.delete_custom_resource.assert_not_called()
        mock_primary_client.get_pods.assert_not_called()
        mock_wait.assert_not_called()

    @patch("modules.decommission.confirm_action")
    @patch("modules.decommission.wait_for_condition")
    def test_decommission_interactive_user_cancels(self, mock_wait, mock_confirm, decommission_with_obs):
        """Test interactive decommission when user cancels."""
        mock_confirm.return_value = False  # User cancels

        result = decommission_with_obs.decommission(interactive=True)

        assert result is False

    @patch("modules.decommission.confirm_action")
    @patch("modules.decommission.wait_for_condition")
    def test_decommission_interactive_user_confirms(
        self, mock_wait, mock_confirm, decommission_with_obs, mock_primary_client
    ):
        """Test interactive decommission when user confirms."""
        mock_confirm.return_value = True  # User confirms all prompts
        mock_wait.return_value = True

        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.list_managed_clusters.return_value = []
        mock_primary_client.delete_custom_resource.return_value = True

        result = decommission_with_obs.decommission(interactive=True)

        assert result is True

    @patch("modules.decommission.confirm_action")
    @patch("modules.decommission.wait_for_condition")
    def test_decommission_requires_extra_mch_confirmation_when_managed_clusters_skipped(
        self, mock_wait, mock_confirm, decommission_no_obs, mock_primary_client
    ):
        """Skipping ManagedCluster deletion requires a second MCH confirmation."""
        mock_wait.return_value = True
        mock_confirm.side_effect = [
            True,  # proceed with decommission
            False,  # skip ManagedCluster deletion
            False,  # decline extra unsafe MCH confirmation
        ]
        mock_primary_client.list_custom_resources.return_value = [{"metadata": {"name": "multiclusterhub"}}]

        result = decommission_no_obs.decommission(interactive=True)

        assert result is True
        mock_primary_client.delete_custom_resource.assert_not_called()
        assert mock_confirm.call_count == 3

    @patch("modules.decommission.wait_for_condition")
    def test_delete_observability_with_resources(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test deleting observability resources."""
        mock_wait.return_value = True

        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {
                    "name": "observability",
                    "namespace": OBSERVABILITY_NAMESPACE,
                }
            }
        ]

        decommission_with_obs._delete_observability()

        mock_primary_client.delete_custom_resource.assert_called_once()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_observability_not_found(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test when no observability resources exist."""
        mock_primary_client.list_custom_resources.return_value = []

        # Should handle gracefully
        decommission_with_obs._delete_observability()

        mock_primary_client.delete_custom_resource.assert_not_called()

    @pytest.mark.xfail(
        strict=True,
        reason="Current implementation does not treat delete 404 as an idempotent observability cleanup success",
    )
    @patch("modules.decommission.wait_for_condition")
    def test_delete_observability_ignores_404_delete_errors(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """Observability delete 404 should be treated as already-gone and still complete idempotently."""
        mock_wait.return_value = True
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "observability", "namespace": OBSERVABILITY_NAMESPACE}}
        ]
        mock_primary_client.delete_custom_resource.side_effect = ApiException(status=404, reason="Not Found")
        mock_primary_client.get_pods.return_value = []

        decommission_with_obs._delete_observability()

        mock_primary_client.delete_custom_resource.assert_called_once()
        mock_wait.assert_called_once()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_observability_timeout_blocks(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Observability pods remaining after MCO deletion should block decommission."""
        mock_wait.return_value = False
        mock_primary_client.list_custom_resources.return_value = [{"metadata": {"name": "observability"}}]
        mock_primary_client.get_pods.return_value = [{"metadata": {"name": "obs-pod"}}]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_observability()

        assert "Observability pods still running" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_called_once()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_observability_timeout_rechecks_before_failing(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """A boundary timeout should not fail decommission if pods are gone on the final read."""
        mock_wait.return_value = False
        mock_primary_client.list_custom_resources.return_value = [{"metadata": {"name": "observability"}}]
        mock_primary_client.get_pods.return_value = []

        decommission_with_obs._delete_observability()

        mock_primary_client.delete_custom_resource.assert_called_once()
        mock_primary_client.get_pods.assert_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_excludes_local(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test that local-cluster is excluded from deletion."""
        mock_wait.return_value = True  # Simulate successful wait for deletion

        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
            {"metadata": {"name": "cluster2"}},
        ]

        decommission_with_obs._delete_managed_clusters()

        # Should delete cluster1 and cluster2, but not local-cluster
        assert mock_primary_client.delete_custom_resource.call_count == 2
        # Should have waited for ManagedCluster removal
        mock_wait.assert_called_once()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_blocks_unsafe_matching_clusterdeployment(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """Unsafe matching Hive ClusterDeployment blocks ManagedCluster deletion."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
        ]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1", "namespace": "cluster1"},
                "spec": {"preserveOnDelete": False},
            }
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "preserveOnDelete=true" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_blocks_metadata_name_clusterdeployment_match(
        self, decommission_with_obs, mock_primary_client
    ):
        """metadata.name is a conventional ManagedCluster match and blocks when unsafe."""
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1", "namespace": "cluster1"},
                "spec": {"preserveOnDelete": False},
            }
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "cluster1 (cluster1/cluster1)" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_blocks_spec_cluster_name_clusterdeployment_match(
        self, decommission_with_obs, mock_primary_client
    ):
        """spec.clusterName is a conventional ManagedCluster match and blocks when unsafe."""
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "hive-cluster", "namespace": "hive-cluster"},
                "spec": {"clusterName": "cluster1", "preserveOnDelete": False},
            }
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "cluster1 (hive-cluster/hive-cluster)" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_blocks_cluster_metadata_cluster_name_match(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """spec.clusterMetadata.clusterName maps Hive resources restored with non-conventional names."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "hive-cluster", "namespace": "hive-cluster"},
                "spec": {
                    "clusterMetadata": {"clusterName": "cluster1"},
                    "preserveOnDelete": False,
                },
            }
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "cluster1 (hive-cluster/hive-cluster)" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_blocks_cross_checked_cluster_install_ref_match(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """clusterInstallRef is accepted only when cross-checked by the ClusterDeployment namespace."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "agent-install", "namespace": "cluster1"},
                "spec": {
                    "clusterInstallRef": {"name": "cluster1"},
                    "preserveOnDelete": False,
                },
            }
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "cluster1 (cluster1/agent-install)" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_allows_preserve_on_delete_true_match(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """Matched ClusterDeployments with preserveOnDelete=true allow ManagedCluster deletion."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "hive-cluster", "namespace": "hive-cluster"},
                "spec": {
                    "clusterMetadata": {"clusterName": "cluster1"},
                    "preserveOnDelete": True,
                },
            }
        ]

        decommission_with_obs._delete_managed_clusters()

        mock_primary_client.delete_custom_resource.assert_called_once()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_deletes_when_matching_clusterdeployment_is_preserved(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """A matching Hive ClusterDeployment with preserveOnDelete=true must not block ACM decommission."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1", "namespace": "cluster1"},
                "spec": {"preserveOnDelete": True},
            }
        ]

        decommission_with_obs._delete_managed_clusters()

        mock_primary_client.delete_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
            name="cluster1",
            timeout_seconds=decommission_module.DELETE_REQUEST_TIMEOUT,
        )
        mock_wait.assert_called_once()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_fails_closed_for_plausible_unverified_clusterdeployment(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """A plausible but unverified namespace relationship blocks ManagedCluster deletion."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "agent-install", "namespace": "cluster1"},
                "spec": {"clusterInstallRef": {"name": "install-config"}, "preserveOnDelete": True},
            }
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "Cannot verify ManagedCluster relationship" in str(exc_info.value)
        assert "cluster1/agent-install" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_fails_closed_for_conflicting_plausible_clusterdeployment(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """A confirmed identifier cannot override a different plausible target identifier."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "cluster2"}},
        ]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1", "namespace": "cluster2"},
                "spec": {"preserveOnDelete": True},
            }
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "conflicting ManagedCluster identifiers" in str(exc_info.value)
        assert "cluster2/cluster1" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_allows_verified_absent_hive_clusterdeployments(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """Verified absence of Hive ClusterDeployments remains acceptable."""
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
        ]
        mock_primary_client.list_custom_resources.return_value = []

        decommission_with_obs._delete_managed_clusters()

        mock_primary_client.delete_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
            name="cluster1",
            timeout_seconds=decommission_module.DELETE_REQUEST_TIMEOUT,
        )

    def test_delete_managed_clusters_api_error_blocks_destructive_deletion(
        self, decommission_with_obs, mock_primary_client
    ):
        """Hive API errors fail closed before destructive ManagedCluster deletion."""
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
        ]
        mock_primary_client.list_custom_resources.side_effect = ApiException(status=403, reason="Forbidden")

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_missing_hive_api_blocks_destructive_deletion(
        self, decommission_with_obs, mock_primary_client
    ):
        """Missing Hive API fails closed before destructive ManagedCluster deletion."""
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
        ]
        mock_primary_client.list_custom_resources.side_effect = ApiException(status=404, reason="Not Found")

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in str(exc_info.value)
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_reports_all_unsafe_clusterdeployments(
        self, decommission_with_obs, mock_primary_client
    ):
        """Unsafe report includes all matching ClusterDeployments deterministically."""
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "cluster2"}},
        ]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster2", "namespace": "ns2"},
                "spec": None,
            },
            {
                "metadata": {"name": "cluster1", "namespace": "ns1"},
                "spec": {"preserveOnDelete": False},
            },
        ]

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        message = str(exc_info.value)
        assert "cluster1 (ns1/cluster1)" in message
        assert "cluster2 (ns2/cluster2)" in message
        assert message.index("cluster1 (ns1/cluster1)") < message.index("cluster2 (ns2/cluster2)")
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_preserves_local_cluster_skip_without_hive_check(
        self, caplog, decommission_with_obs, mock_primary_client
    ):
        """local-cluster remains skipped and does not require Hive safety lookup."""
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "local-cluster"}},
        ]

        with caplog.at_level(logging.INFO, logger="acm_switchover"):
            decommission_with_obs._delete_managed_clusters()

        mock_primary_client.list_custom_resources.assert_not_called()
        mock_primary_client.delete_custom_resource.assert_not_called()
        assert "ClusterDeployment preserveOnDelete safety was verified" not in caplog.text

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_timeout(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test that deletion fails when ManagedClusters are not removed in time."""
        mock_wait.return_value = False  # Simulate timeout

        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
        ]
        mock_primary_client.list_custom_resources.return_value = []

        with pytest.raises(SwitchoverError) as exc_info:
            decommission_with_obs._delete_managed_clusters()

        assert "ManagedClusters not fully removed" in str(exc_info.value)

    def test_delete_managed_clusters_none_found(self, decommission_with_obs, mock_primary_client):
        """Test when no managed clusters exist."""
        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.list_managed_clusters.return_value = []

        decommission_with_obs._delete_managed_clusters()

        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_multiclusterhub(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test deleting MultiClusterHub resource."""
        mock_wait.return_value = True

        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "multiclusterhub", "namespace": ACM_NAMESPACE}}
        ]

        decommission_with_obs._delete_multiclusterhub()

        mock_primary_client.delete_custom_resource.assert_called_once()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_multiclusterhub_timeout(self, mock_wait, decommission_with_obs, mock_primary_client):
        """Test when MultiClusterHub deletion times out."""
        mock_wait.return_value = False  # Timeout

        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "multiclusterhub", "namespace": ACM_NAMESPACE}}
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
        decomm = Decommission(primary_client=mock_primary_client, has_observability=has_obs)

        mock_primary_client.list_custom_resources.return_value = []

        with patch.object(decomm, "_delete_observability") as mock_delete_obs:
            decomm.decommission(interactive=False)

            if has_obs:
                mock_delete_obs.assert_called_once()
            else:
                mock_delete_obs.assert_not_called()


@pytest.mark.integration
class TestDecommissionIntegration:
    """Integration tests for Decommission workflows."""

    def test_operator_pods_excluded_from_removal_check(self, mock_primary_client):
        """Test that operator pods are excluded from removal check.

        When only operator pods remain (multiclusterhub-operator-*), the
        decommission should consider ACM removed successfully.
        """
        decomm = Decommission(primary_client=mock_primary_client, has_observability=False)

        # Set up MCH to exist so deletion is attempted
        mch_listed = False

        def list_side_effect(*args, **kwargs):
            nonlocal mch_listed
            if kwargs.get("plural") == "multiclusterhubs":
                if not mch_listed:
                    mch_listed = True
                    return [
                        {
                            "metadata": {
                                "name": "multiclusterhub",
                                "namespace": ACM_NAMESPACE,
                            }
                        }
                    ]
                return []  # MCH deleted
            return []

        mock_primary_client.list_custom_resources.side_effect = list_side_effect
        mock_primary_client.list_managed_clusters.return_value = []
        mock_primary_client.delete_custom_resource.return_value = True

        # Only operator pods remain after MCH deletion
        mock_primary_client.get_pods.return_value = [
            {"metadata": {"name": "multiclusterhub-operator-597d5cfb4f-v8dl7"}},
            {"metadata": {"name": "multiclusterhub-operator-597d5cfb4f-wchrt"}},
        ]

        # The wait_for_condition will call the check function
        # We need to capture the actual check logic
        with patch("modules.decommission.wait_for_condition") as mock_wait:
            # Simulate calling the condition function
            def capture_condition_call(name, condition_fn, **kwargs):
                if "pod removal" in name.lower():
                    result = condition_fn()
                    assert isinstance(result, WaitConditionResult)
                    assert result.done is True, f"Expected success but got: {result.public_detail}"
                    assert (
                        "operator" in result.public_detail.lower()
                    ), f"Expected operator mention in: {result.public_detail}"
                return True

            mock_wait.side_effect = capture_condition_call

            decomm._delete_multiclusterhub()

            # Verify wait_for_condition was called for pod removal
            calls = [str(c) for c in mock_wait.call_args_list]
            assert any("pod removal" in c.lower() for c in calls), f"Expected pod removal call in: {calls}"

    @patch("modules.decommission.wait_for_condition")
    def test_full_decommission_workflow(self, mock_wait, mock_primary_client):
        """Test complete decommission workflow."""
        mock_wait.return_value = True

        decomm = Decommission(primary_client=mock_primary_client, has_observability=True)

        # Mock all resources
        mock_primary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "observability"}}],  # MCO
            [],  # Hive ClusterDeployments
            [{"metadata": {"name": "multiclusterhub"}}],  # MCH
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.delete_custom_resource.return_value = True

        result = decomm.decommission(interactive=False)

        assert result is True
        # Verify resources were deleted
        assert mock_primary_client.delete_custom_resource.call_count >= 3
