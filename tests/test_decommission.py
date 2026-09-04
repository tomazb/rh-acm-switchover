"""Unit tests for modules/decommission.py.

Tests cover Decommission class for removing ACM from old primary hub.
"""

import inspect
import json
import logging
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from kubernetes.client.exceptions import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.decommission as decommission_module
from lib.constants import ACM_NAMESPACE, LOCAL_CLUSTER_NAME, OBSERVABILITY_NAMESPACE
from lib.decommission_outcome import DecommissionResult, SubstepExecution, SubstepOutcome
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.run_record import RunRecord
from lib.utils import StateManager
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
def state_manager(tmp_path):
    """Real StateManager backing the RunRecord the Decommission fixtures share."""
    return StateManager(str(tmp_path / "state.json"))


@pytest.fixture
def decommission_with_obs(mock_primary_client, state_manager):
    """Create Decommission instance with observability."""
    return Decommission(
        primary_client=mock_primary_client,
        has_observability=True,
        run_record=RunRecord(state_manager),
    )


@pytest.fixture
def decommission_no_obs(mock_primary_client, state_manager):
    """Create Decommission instance without observability."""
    return Decommission(
        primary_client=mock_primary_client,
        has_observability=False,
        run_record=RunRecord(state_manager),
    )


@pytest.fixture
def primary_with_all_resources(mock_primary_client):
    """Primary hub mock where every decommission target still exists."""

    def _list_custom_resources(*args, **kwargs):
        if kwargs.get("plural") == "multiclusterobservabilities":
            return [{"metadata": {"name": "observability"}}]
        if kwargs.get("plural") == "multiclusterhubs":
            return [{"metadata": {"name": "multiclusterhub", "namespace": ACM_NAMESPACE}}]
        return []

    mock_primary_client.list_custom_resources.side_effect = _list_custom_resources
    mock_primary_client.list_managed_clusters.return_value = [
        {"metadata": {"name": "cluster1"}},
        {"metadata": {"name": LOCAL_CLUSTER_NAME}},
    ]
    return mock_primary_client


@pytest.fixture
def decommission_dry_run(mock_primary_client, state_manager):
    """Create a dry-run Decommission instance with observability."""
    return Decommission(
        primary_client=mock_primary_client,
        has_observability=True,
        run_record=RunRecord(state_manager),
        dry_run=True,
    )


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

        assert result.succeeded is True
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

        assert result.succeeded is True

    @patch("modules.decommission.wait_for_condition")
    def test_decommission_dry_run_non_interactive_is_full_no_op(
        self, mock_wait, decommission_dry_run, mock_primary_client
    ):
        """Dry-run top-level decommission must not issue delete or wait calls anywhere."""
        dry_run_decommission = decommission_dry_run
        mock_primary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "observability"}}],
            [{"metadata": {"name": "multiclusterhub"}}],
        ]
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
        ]

        result = dry_run_decommission.decommission(interactive=False)

        assert result.succeeded is True
        mock_primary_client.delete_custom_resource.assert_not_called()
        mock_primary_client.get_pods.assert_not_called()
        mock_wait.assert_not_called()

    @patch("modules.decommission.confirm_action")
    @patch("modules.decommission.wait_for_condition")
    def test_decommission_interactive_user_cancels(self, mock_wait, mock_confirm, decommission_with_obs):
        """Test interactive decommission when user cancels."""
        mock_confirm.return_value = False  # User cancels

        result = decommission_with_obs.decommission(interactive=True)

        assert result.succeeded is False

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

        assert result.succeeded is True

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
    def test_delete_observability_timeout_blocks(self, mock_wait, decommission_with_obs, mock_primary_client, caplog):
        """Observability pods remaining after MCO deletion should block decommission."""
        mock_wait.return_value = False
        mock_primary_client.list_custom_resources.return_value = [{"metadata": {"name": "observability"}}]
        mock_primary_client.get_pods.return_value = [{"metadata": {"name": "obs-pod"}}]

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_observability()

        # IV-R403-01: the MCO delete was accepted before the proof failed, so the
        # failing execution must still report its own mutation.
        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True
        assert "Observability pods still running" in caplog.text
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
        self,
        mock_wait,
        decommission_with_obs,
        mock_primary_client,
        caplog,
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

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is False
        assert "preserveOnDelete=true" in caplog.text
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_blocks_metadata_name_clusterdeployment_match(
        self,
        decommission_with_obs,
        mock_primary_client,
        caplog,
    ):
        """metadata.name is a conventional ManagedCluster match and blocks when unsafe."""
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "cluster1", "namespace": "cluster1"},
                "spec": {"preserveOnDelete": False},
            }
        ]

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (cluster1/cluster1)" in caplog.text
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_blocks_spec_cluster_name_clusterdeployment_match(
        self,
        decommission_with_obs,
        mock_primary_client,
        caplog,
    ):
        """spec.clusterName is a conventional ManagedCluster match and blocks when unsafe."""
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "hive-cluster", "namespace": "hive-cluster"},
                "spec": {"clusterName": "cluster1", "preserveOnDelete": False},
            }
        ]

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (hive-cluster/hive-cluster)" in caplog.text
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_blocks_cluster_metadata_cluster_name_match(
        self,
        mock_wait,
        decommission_with_obs,
        mock_primary_client,
        caplog,
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

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (hive-cluster/hive-cluster)" in caplog.text
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_blocks_cross_checked_cluster_install_ref_match(
        self,
        mock_wait,
        decommission_with_obs,
        mock_primary_client,
        caplog,
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

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (cluster1/agent-install)" in caplog.text
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
        self,
        mock_wait,
        decommission_with_obs,
        mock_primary_client,
        caplog,
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

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "Cannot verify ManagedCluster relationship" in caplog.text
        assert "cluster1/agent-install" in caplog.text
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_fails_closed_for_conflicting_plausible_clusterdeployment(
        self,
        mock_wait,
        decommission_with_obs,
        mock_primary_client,
        caplog,
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

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "conflicting ManagedCluster identifiers" in caplog.text
        assert "cluster2/cluster1" in caplog.text
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
        self,
        decommission_with_obs,
        mock_primary_client,
        caplog,
    ):
        """Hive API errors fail closed before destructive ManagedCluster deletion."""
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
        ]
        mock_primary_client.list_custom_resources.side_effect = ApiException(status=403, reason="Forbidden")

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in caplog.text
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_missing_hive_api_blocks_destructive_deletion(
        self,
        decommission_with_obs,
        mock_primary_client,
        caplog,
    ):
        """Missing Hive API fails closed before destructive ManagedCluster deletion."""
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
        ]
        mock_primary_client.list_custom_resources.side_effect = ApiException(status=404, reason="Not Found")

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in caplog.text
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_reports_all_unsafe_clusterdeployments(
        self,
        decommission_with_obs,
        mock_primary_client,
        caplog,
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

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED

        message = caplog.text
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
    def test_delete_managed_clusters_timeout(self, mock_wait, decommission_with_obs, mock_primary_client, caplog):
        """Test that deletion fails when ManagedClusters are not removed in time."""
        mock_wait.return_value = False  # Simulate timeout

        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
        ]
        mock_primary_client.list_custom_resources.return_value = []

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        # IV-R403-01: the ManagedCluster delete was accepted before the proof failed.
        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True
        assert "ManagedClusters not fully removed" in caplog.text

    @patch("modules.decommission.wait_for_condition")
    def test_delete_managed_clusters_wait_detail_is_bounded(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """Large ManagedCluster sets should not produce oversized wait-detail log lines."""
        mock_wait.return_value = True
        delete_targets = [{"metadata": {"name": "cluster-delete-target"}}]
        remaining_clusters = [{"metadata": {"name": f"cluster-{idx:03d}"}} for idx in range(60)]
        mock_primary_client.list_managed_clusters.side_effect = [delete_targets, remaining_clusters]
        mock_primary_client.list_custom_resources.return_value = []

        decommission_with_obs._delete_managed_clusters()

        condition_fn = mock_wait.call_args.args[1]
        result = condition_fn()
        assert result.done is False
        assert "60 ManagedCluster(s) remaining" in result.public_detail
        assert "cluster-000" in result.public_detail
        assert "cluster-020" not in result.public_detail
        assert "40 more" in result.public_detail
        assert len(result.public_detail) <= 560

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

    def test_decommission_unexpected_exception_propagates(self, decommission_with_obs, mock_primary_client):
        """An unexpected exception is never laundered into a handled result."""
        mock_primary_client.list_custom_resources.side_effect = Exception("API error")

        with pytest.raises(Exception, match="API error"):
            decommission_with_obs.decommission(interactive=False)

    @pytest.mark.parametrize("has_obs", [True, False])
    def test_decommission_observability_conditional(self, mock_primary_client, state_manager, has_obs):
        """Test that observability deletion is conditional."""
        decomm = Decommission(
            primary_client=mock_primary_client,
            has_observability=has_obs,
            run_record=RunRecord(state_manager),
        )

        mock_primary_client.list_custom_resources.return_value = []

        with patch.object(
            decomm,
            "_delete_observability",
            return_value=SubstepExecution(SubstepOutcome.PRECONDITION_NOOP),
        ) as mock_delete_obs:
            decomm.decommission(interactive=False)

            if has_obs:
                mock_delete_obs.assert_called_once()
            else:
                mock_delete_obs.assert_not_called()


@pytest.mark.unit
class TestDecommissionOutcomes:
    """R4-C2: a refused substep can never produce a successful decommission."""

    @patch("modules.decommission.confirm_action")
    def test_top_level_cancel_returns_an_unsuccessful_result(self, confirm, decommission_with_obs):
        confirm.return_value = False
        result = decommission_with_obs.decommission(interactive=True)
        assert result.cancelled is True
        assert result.succeeded is False
        assert result.substeps == {}
        assert result.not_attempted == ("observability", "managed_clusters", "multiclusterhub")
        assert result.changed is False and result.would_change is False

    @patch("modules.decommission.confirm_action")
    def test_top_level_cancel_invokes_no_substep(self, confirm, decommission_with_obs, monkeypatch):
        confirm.return_value = False
        invoked = Mock()
        monkeypatch.setattr(decommission_with_obs, "_run_substep", invoked)
        decommission_with_obs.decommission(interactive=True)
        invoked.assert_not_called()

    @patch("modules.decommission.confirm_action")
    def test_refusing_the_first_substep_aborts_and_fails(self, confirm, decommission_with_obs):
        confirm.side_effect = [True, False]  # proceed, then decline observability
        result = decommission_with_obs.decommission(interactive=True)
        assert result.succeeded is False
        assert result.substeps["observability"] is SubstepOutcome.REFUSED
        assert result.not_attempted == ("managed_clusters", "multiclusterhub")
        assert result.changed is False

    @patch("modules.decommission.confirm_action")
    def test_refusal_stops_remaining_substeps(self, confirm, decommission_with_obs, mock_primary_client):
        confirm.side_effect = [True, False]
        decommission_with_obs.decommission(interactive=True)
        mock_primary_client.delete_custom_resource.assert_not_called()

    @patch("modules.decommission.confirm_action")
    def test_refusing_a_later_substep_still_fails_overall(self, confirm, decommission_no_obs):
        confirm.side_effect = [True, True, False]
        result = decommission_no_obs.decommission(interactive=True)
        assert result.succeeded is False
        assert result.substeps["multiclusterhub"] is SubstepOutcome.REFUSED

    def test_disabled_observability_is_not_requested_not_a_failure(self, decommission_no_obs):
        result = decommission_no_obs.decommission(interactive=False)
        assert result.substeps["observability"] is SubstepOutcome.NOT_REQUESTED
        assert result.succeeded is True

    @patch("modules.decommission.confirm_action")
    def test_non_interactive_never_prompts(self, confirm, decommission_with_obs):
        decommission_with_obs.decommission(interactive=False)
        confirm.assert_not_called()

    def test_summary_names_completed_refused_and_not_attempted(self):
        result = DecommissionResult(
            substeps={
                "observability": SubstepOutcome.COMPLETED,
                "managed_clusters": SubstepOutcome.REFUSED,
            },
            not_attempted=("multiclusterhub",),
        )
        text = "\n".join(result.summary_lines())
        assert "observability" in text and "completed" in text
        assert "managed_clusters" in text and "refused" in text
        assert "multiclusterhub" in text and "not attempted" in text

    def test_result_has_no_boolean_shortcut(self):
        assert "__bool__" not in vars(DecommissionResult)


# The complete producer list for the one execution-result channel (B3.1). PR B declares
# `_run_substep`; C, D, and E each append their family method in the same PR that adds it, so a
# producer can never be introduced without being covered by the interface guardrail below.
# C adds "teardown_observability" and "_teardown_resource"; D adds "teardown_managed_clusters";
# E adds "teardown_multiclusterhub".
SUBSTEP_EXECUTORS = ("_run_substep",)


@pytest.mark.unit
class TestActualChangeTruth:
    """`changed` is accepted mutation in this invocation, never intent or prediction."""

    def _executing(self, decommission, executions):
        """Drive the substep loop with declared per-substep executions.

        Every expected outcome, including FAILED, is a returned `SubstepExecution`. A member
        that is an exception instance is used only by the unexpected-exception test below, and
        models a programming error escaping a family method, never an expected failure.
        """
        calls = []

        def fake_run_substep(substep):
            calls.append(substep)
            execution = executions[substep]
            if isinstance(execution, BaseException):
                raise execution
            return execution

        decommission._run_substep = fake_run_substep
        return calls

    def test_a_noop_substep_reports_no_change(self, decommission_with_obs):
        self._executing(
            decommission_with_obs,
            {
                step: SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
                for step in ("observability", "managed_clusters", "multiclusterhub")
            },
        )
        result = decommission_with_obs.decommission(interactive=False)
        assert result.succeeded is True
        assert result.changed is False

    def test_a_resumed_substep_without_a_new_mutation_reports_no_change(self, decommission_with_obs):
        self._executing(
            decommission_with_obs,
            {
                "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=False),
                "managed_clusters": SubstepExecution(SubstepOutcome.NOT_REQUESTED, changed=False),
                "multiclusterhub": SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False),
            },
        )
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is False, "a completed record proved live is not a new mutation"

    def test_an_accepted_delete_with_a_completion_proof_reports_change(self, decommission_with_obs):
        self._executing(
            decommission_with_obs,
            {
                "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
                "managed_clusters": SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False),
                "multiclusterhub": SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False),
            },
        )
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is True
        assert result.succeeded is True

    def test_a_later_failure_does_not_erase_an_earlier_actual_change(self, decommission_with_obs):
        calls = self._executing(
            decommission_with_obs,
            {
                "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
                "managed_clusters": SubstepExecution(SubstepOutcome.FAILED, changed=False),
                "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            },
        )
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is True
        assert result.succeeded is False
        assert result.substeps["managed_clusters"] is SubstepOutcome.FAILED
        assert result.not_attempted == ("multiclusterhub",)
        assert calls == ["observability", "managed_clusters"], "a failure aborts later substeps"

    def test_a_failing_substeps_own_mutation_reaches_the_result(self, decommission_with_obs):
        """IV-R403-01: the SAME substep accepted a DELETE, then failed its proof.

        No earlier substep mutated anything, so the only way `changed` can be true is by
        aggregating the failing execution's own flag before the early return.
        """
        self._executing(
            decommission_with_obs,
            {
                "observability": SubstepExecution(SubstepOutcome.FAILED, changed=True),
                "managed_clusters": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
                "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            },
        )
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is True, "the accepted DELETE in the failing substep must be reported"
        assert result.succeeded is False
        assert result.substeps["observability"] is SubstepOutcome.FAILED
        assert result.not_attempted == ("managed_clusters", "multiclusterhub")

    def test_a_failing_substep_that_mutated_nothing_reports_no_change(self, decommission_with_obs):
        self._executing(
            decommission_with_obs,
            {
                "observability": SubstepExecution(SubstepOutcome.FAILED, changed=False),
                "managed_clusters": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
                "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            },
        )
        result = decommission_with_obs.decommission(interactive=False)
        assert result.changed is False
        assert result.succeeded is False

    def test_a_refused_substep_aborts_the_remaining_requested_substeps(self, decommission_with_obs):
        calls = self._executing(
            decommission_with_obs,
            {
                "observability": SubstepExecution(SubstepOutcome.REFUSED, changed=False),
                "managed_clusters": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
                "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            },
        )
        result = decommission_with_obs.decommission(interactive=False)
        assert result.succeeded is False
        assert calls == ["observability"]
        assert result.not_attempted == ("managed_clusters", "multiclusterhub")

    def test_an_unexpected_exception_propagates_and_never_becomes_a_result(self, decommission_with_obs):
        """A programming error must not be laundered into FAILED or into a successful result."""
        self._executing(
            decommission_with_obs,
            {
                "observability": AttributeError("'NoneType' object has no attribute 'metadata'"),
                "managed_clusters": SubstepExecution(SubstepOutcome.COMPLETED, changed=False),
                "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=False),
            },
        )
        with pytest.raises(AttributeError):
            decommission_with_obs.decommission(interactive=False)

    def test_the_aggregator_has_no_switchover_error_handler(self):
        """One channel: an expected failure is a return value, so no handler may swallow it."""
        source = inspect.getsource(Decommission.decommission)
        assert "except SwitchoverError" not in source
        assert "except Exception" not in source

    @patch("modules.decommission.confirm_action")
    def test_a_later_refusal_does_not_erase_an_earlier_actual_change(self, confirm, decommission_with_obs):
        confirm.side_effect = [True, True, False]  # proceed, run observability, decline the next
        self._executing(
            decommission_with_obs,
            {
                "observability": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
                "managed_clusters": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
                "multiclusterhub": SubstepExecution(SubstepOutcome.COMPLETED, changed=True),
            },
        )
        result = decommission_with_obs.decommission(interactive=True)
        assert result.changed is True
        assert result.succeeded is False

    def test_dry_run_records_no_outcome_and_never_reports_actual_change(
        self, decommission_dry_run, mock_primary_client
    ):
        result = decommission_dry_run.decommission(interactive=False)
        assert result.substeps == {}
        assert result.not_attempted == ("observability", "managed_clusters", "multiclusterhub")
        assert result.changed is False
        assert isinstance(result.would_change, bool)
        assert decommission_dry_run.run_record.all_teardown_records() == {}
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_decommission_requires_a_run_record(self, mock_primary_client):
        """``run_record`` is keyword-only and required.

        A default would let a caller silently opt out of the durable channel, and a
        decommission without durable state cannot satisfy the deletion boundary.
        Kill condition: giving ``run_record`` a default, or making it positional.
        """
        with pytest.raises(TypeError):
            Decommission(mock_primary_client, True)

    def test_dry_run_writes_nothing_to_state(self, decommission_dry_run, state_manager):
        """A preview writes NOTHING durable -- not only no teardown record.

        Broader than ``test_dry_run_records_no_outcome_and_never_reports_actual_change``,
        which only inspects the teardown-record channel. Kill condition: any state write
        on the dry-run path, through ``RunRecord`` or around it.
        """
        before = json.dumps(state_manager.capture_state_snapshot(), sort_keys=True)
        decommission_dry_run.decommission(interactive=False)
        assert json.dumps(state_manager.capture_state_snapshot(), sort_keys=True) == before

    def test_a_live_run_after_a_dry_run_reads_fresh_and_trusts_nothing(
        self, decommission_dry_run, decommission_with_obs, state_manager, mock_primary_client
    ):
        """The live run performs its own reads instead of reusing preview observations.

        Kill condition: caching a preview observation (on the shared state, or on the
        RunRecord) and short-circuiting the live run's reads from it.
        """
        decommission_dry_run.decommission(interactive=False)
        mock_primary_client.reset_mock()
        decommission_with_obs.decommission(interactive=False)
        assert mock_primary_client.method_calls, "the live run must perform its own reads"

    def test_dry_run_prediction_is_separate_from_actual_change(self, decommission_dry_run, monkeypatch):
        monkeypatch.setattr(decommission_dry_run, "_preview_substep", lambda substep: True)
        result = decommission_dry_run.decommission(interactive=False)
        assert result.would_change is True
        assert result.changed is False

    @pytest.mark.parametrize("name", SUBSTEP_EXECUTORS)
    def test_every_substep_executor_returns_the_one_execution_type(self, name):
        """One execution-result channel, asserted per producer rather than assumed."""
        annotation = inspect.signature(getattr(Decommission, name)).return_annotation
        assert annotation in (SubstepExecution, "SubstepExecution")

    def test_no_executor_returns_a_bare_outcome_or_a_tuple(self):
        for name in SUBSTEP_EXECUTORS:
            annotation = inspect.signature(getattr(Decommission, name)).return_annotation
            assert annotation not in (SubstepOutcome, "SubstepOutcome")
            assert "tuple" not in str(annotation).lower()


@pytest.mark.unit
class TestDeleteApiErrorsReachTheResult:
    """A rejected DELETE is an expected operational failure, not an escaping exception.

    Before this conversion a non-404 ApiException escaped ``decommission()``, so the
    DecommissionResult was never constructed and the aggregated ``changed`` flag died
    with the stack frame: an operator whose MCO had already been destroyed was told
    only "Unexpected error: (409)". Converting at the raise site keeps the failure on
    the one execution-result channel.

    **Scope of the sanitization assertions below.** These drive a Mock client, which
    bypasses ``lib.kube_client.api_call`` entirely. They therefore prove exactly one
    thing: the failure message *this module constructs* carries status and reason only
    and never the HTTP response body. They say nothing about the shared API decorator's
    own logging, which is a separate layer with its own (out-of-scope) behaviour.
    """

    @staticmethod
    def _api_error(status, reason, body='{"message":"raw body must never be shown"}'):
        exc = ApiException(status=status, reason=reason)
        exc.body = body
        return exc

    @patch("modules.decommission.wait_for_condition")
    def test_observability_delete_rejection_fails_with_the_earlier_delete_reported(
        self, mock_wait, decommission_with_obs, mock_primary_client, caplog
    ):
        """The first MCO was destroyed; the second is rejected. Both facts must survive.

        The body assertion covers the message this module builds, not the shared
        ``api_call`` decorator's logging -- the Mock client never reaches it.
        """
        mock_wait.return_value = True
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "obs-one"}},
            {"metadata": {"name": "obs-two"}},
        ]
        mock_primary_client.delete_custom_resource.side_effect = [True, self._api_error(403, "Forbidden")]

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_observability()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True
        assert "403" in caplog.text and "Forbidden" in caplog.text
        assert "raw body must never be shown" not in caplog.text

    def test_observability_delete_rejection_with_no_prior_delete_reports_no_change(
        self, decommission_with_obs, mock_primary_client, caplog
    ):
        mock_primary_client.list_custom_resources.return_value = [{"metadata": {"name": "obs-one"}}]
        mock_primary_client.delete_custom_resource.side_effect = self._api_error(409, "Conflict")

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_observability()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is False
        assert "409" in caplog.text and "Conflict" in caplog.text

    @patch("modules.decommission.wait_for_condition")
    def test_managed_cluster_delete_rejection_reports_the_earlier_delete(
        self, mock_wait, decommission_with_obs, mock_primary_client, caplog
    ):
        """The first ManagedCluster was deleted; the second is rejected.

        The body assertion covers the message this module builds, not the shared
        ``api_call`` decorator's logging -- the Mock client never reaches it.
        """
        mock_wait.return_value = True
        mock_primary_client.list_managed_clusters.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "cluster2"}},
        ]
        mock_primary_client.list_custom_resources.return_value = []  # no Hive ClusterDeployments
        mock_primary_client.delete_custom_resource.side_effect = [True, self._api_error(403, "Forbidden")]

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True
        assert "403" in caplog.text and "Forbidden" in caplog.text
        assert "raw body must never be shown" not in caplog.text

    def test_managed_cluster_delete_rejection_with_no_prior_delete_reports_no_change(
        self, decommission_with_obs, mock_primary_client, caplog
    ):
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.list_custom_resources.return_value = []
        mock_primary_client.delete_custom_resource.side_effect = self._api_error(409, "Conflict")

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is False

    @patch("modules.decommission.wait_for_condition")
    def test_multiclusterhub_delete_rejection_reports_the_earlier_delete(
        self, mock_wait, decommission_with_obs, mock_primary_client, caplog
    ):
        """The first MultiClusterHub was deleted; the second is rejected.

        The body assertion covers the message this module builds, not the shared
        ``api_call`` decorator's logging -- the Mock client never reaches it.
        """
        mock_wait.return_value = True
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "mch-one", "namespace": ACM_NAMESPACE}},
            {"metadata": {"name": "mch-two", "namespace": ACM_NAMESPACE}},
        ]
        mock_primary_client.delete_custom_resource.side_effect = [True, self._api_error(403, "Forbidden")]

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_multiclusterhub()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True
        assert "403" in caplog.text and "Forbidden" in caplog.text
        assert "raw body must never be shown" not in caplog.text

    def test_multiclusterhub_delete_rejection_with_no_prior_delete_reports_no_change(
        self, decommission_with_obs, mock_primary_client, caplog
    ):
        mock_primary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "mch-one", "namespace": ACM_NAMESPACE}}
        ]
        mock_primary_client.delete_custom_resource.side_effect = self._api_error(409, "Conflict")

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs._delete_multiclusterhub()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is False

    @patch("modules.decommission.wait_for_condition")
    def test_a_rejected_delete_never_escapes_the_aggregator(
        self, mock_wait, decommission_with_obs, mock_primary_client
    ):
        """The probed scenario: MCO destroyed, then a ManagedCluster DELETE is rejected.

        The operator must be told the MCO is gone, which is only possible if the
        DecommissionResult is constructed at all.
        """
        mock_wait.return_value = True
        mock_primary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "observability"}}],  # MCO
            [],  # Hive ClusterDeployments
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.get_pods.return_value = []
        mock_primary_client.delete_custom_resource.side_effect = [True, self._api_error(409, "Conflict")]

        result = decommission_with_obs.decommission(interactive=False)

        assert result.succeeded is False
        assert result.changed is True, "the destroyed MCO must be reported despite the later rejection"
        assert result.substeps["observability"] is SubstepOutcome.COMPLETED
        assert result.substeps["managed_clusters"] is SubstepOutcome.FAILED
        assert result.not_attempted == ("multiclusterhub",)
        assert "observability" in "\n".join(result.summary_lines())

    @patch("modules.decommission.wait_for_condition")
    def test_a_404_from_the_real_client_is_indistinguishable_and_reports_change(self, mock_wait, state_manager):
        """Pin the REAL client behaviour, which PR B cannot make exact.

        This drives an actual ``KubeClient`` -- not a Mock standing in for one -- so the
        ``@api_call(not_found_value=True)`` decorator on ``delete_custom_resource``
        really runs: the underlying ``custom_api`` raises ``ApiException(404)`` and the
        decorator converts it to ``True``, exactly what it returns for a delete the API
        performed. The call site cannot tell the two apart, so an already-absent object
        reports ``changed=True``. Flipping ``not_found_value`` to ``False`` fails this
        test, which is what makes it a pin rather than a restatement.

        ``tests/test_kube_client.py::test_delete_custom_resource_404_returns_true`` is
        the existing pin for the decorator behaviour itself; this one pins the
        consequence the decommission layer inherits from it, and PR C's
        UID-preconditioned guarded delete is what removes the ambiguity.

        Note that ``test_delete_observability_ignores_404_delete_errors`` models a 404
        as a RAISED ApiException. The production client never raises on 404 -- the
        decorator absorbs it -- so that path is reachable only through a Mock.
        """
        mock_wait.return_value = True

        client = object.__new__(KubeClient)  # bypass cluster config; this is a real KubeClient
        client.dry_run = False
        client.custom_api = Mock()
        client.custom_api.delete_cluster_custom_object.side_effect = ApiException(status=404)

        # 1. The real decorator path: a 404 from the API server becomes True.
        assert (
            client.delete_custom_resource(
                group="observability.open-cluster-management.io",
                version="v1beta2",
                plural="multiclusterobservabilities",
                name="observability",
            )
            is True
        )

        # 2. The B-stage consequence on that same client: an object that was already
        #    gone is still reported as an accepted mutation.
        client.list_custom_resources = Mock(return_value=[{"metadata": {"name": "observability"}}])
        client.get_pods = Mock(return_value=[])
        decommission = Decommission(
            primary_client=client,
            has_observability=True,
            run_record=RunRecord(state_manager),
        )

        execution = decommission._delete_observability()

        assert execution.outcome is SubstepOutcome.COMPLETED
        assert execution.changed is True
        assert client.custom_api.delete_cluster_custom_object.call_count == 2


@pytest.mark.unit
class TestDryRunPrediction:
    """The read-only predictor must actually predict, not merely return False."""

    @pytest.mark.parametrize("substep", ["observability", "managed_clusters", "multiclusterhub"])
    def test_preview_reports_true_when_the_resource_is_present(
        self, decommission_dry_run, primary_with_all_resources, substep
    ):
        assert decommission_dry_run._preview_substep(substep) is True

    def test_preview_ignores_local_cluster(self, decommission_dry_run, mock_primary_client):
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": LOCAL_CLUSTER_NAME}}]

        assert decommission_dry_run._preview_substep("managed_clusters") is False

    def test_preview_reports_false_when_nothing_is_present(self, decommission_dry_run):
        for substep in ("observability", "managed_clusters", "multiclusterhub"):
            assert decommission_dry_run._preview_substep(substep) is False

    def test_preview_rejects_an_unknown_substep(self, decommission_dry_run):
        with pytest.raises(KeyError):
            decommission_dry_run._preview_substep("not_a_substep")

    @patch("modules.decommission.wait_for_condition")
    def test_dry_run_predicts_change_without_making_any(
        self, mock_wait, decommission_dry_run, primary_with_all_resources
    ):
        result = decommission_dry_run.decommission(interactive=False)

        assert result.would_change is True
        assert result.changed is False
        assert result.succeeded is True
        assert result.substeps == {}
        assert result.not_attempted == ("observability", "managed_clusters", "multiclusterhub")
        primary_with_all_resources.delete_custom_resource.assert_not_called()
        primary_with_all_resources.get_pods.assert_not_called()
        mock_wait.assert_not_called()


@pytest.mark.integration
class TestDecommissionIntegration:
    """Integration tests for Decommission workflows."""

    def test_operator_pods_excluded_from_removal_check(self, mock_primary_client, state_manager):
        """Test that operator pods are excluded from removal check.

        When only operator pods remain (multiclusterhub-operator-*), the
        decommission should consider ACM removed successfully.
        """
        decomm = Decommission(
            primary_client=mock_primary_client,
            has_observability=False,
            run_record=RunRecord(state_manager),
        )

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
    def test_full_decommission_workflow(self, mock_wait, mock_primary_client, state_manager):
        """Test complete decommission workflow."""
        mock_wait.return_value = True

        decomm = Decommission(
            primary_client=mock_primary_client,
            has_observability=True,
            run_record=RunRecord(state_manager),
        )

        # Mock all resources
        mock_primary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "observability"}}],  # MCO
            [],  # Hive ClusterDeployments
            [{"metadata": {"name": "multiclusterhub"}}],  # MCH
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.delete_custom_resource.return_value = True

        result = decomm.decommission(interactive=False)

        assert result.succeeded is True
        # Verify resources were deleted
        assert mock_primary_client.delete_custom_resource.call_count >= 3
