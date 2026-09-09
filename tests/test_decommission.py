"""Unit tests for modules/decommission.py.

Tests cover Decommission class for removing ACM from old primary hub.
"""

import inspect
import json
import logging
import sys
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest
from kubernetes.client.exceptions import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.decommission as decommission_module
from lib.constants import ACM_NAMESPACE, DELETE_REQUEST_TIMEOUT, LOCAL_CLUSTER_NAME, OBSERVABILITY_NAMESPACE
from lib.decommission_outcome import DecommissionResult, SubstepExecution, SubstepOutcome
from lib.exceptions import SwitchoverError, TargetDisappeared
from lib.kube_client import KubeClient
from lib.run_record import RunRecord
from lib.teardown_record import TeardownPhase, TeardownRecord, teardown_key
from lib.utils import StateManager
from lib.waiter import WaitConditionResult

Decommission = decommission_module.Decommission

# This module owns its own key constants, matching the repository convention that a
# test file declares the values it asserts against rather than importing them from a
# sibling test module.
MCO_KEY = teardown_key(
    "observability.open-cluster-management.io/v1beta2",
    "MultiClusterObservability",
    None,
    "observability",
)

# A string that only ever exists inside an injected HTTP response body or header, so
# any assertion that finds it has found a raw response reaching a log or the state file.
RESPONSE_CANARY = "RAW-RESPONSE-CANARY"


@pytest.fixture
def mock_primary_client():
    """Create a mock KubeClient for primary hub.

    Carries the strict-read seam the C3 phase machine reads through, defaulted to the
    ordinary success path: the MCO is present and is the proved object, its delete is
    accepted, and the drain namespace is positively absent (verified-empty under the
    fixed-namespace scope rule). Tests that need another shape override these.
    """
    from lib.strict_read import StrictReadOutcome, StrictReadStatus

    client = Mock()
    client.list_managed_clusters = Mock(return_value=[])
    client.list_custom_resources = Mock(return_value=[])

    mco = {
        "apiVersion": "observability.open-cluster-management.io/v1beta2",
        "kind": "MultiClusterObservability",
        "metadata": {"name": "observability", "uid": "uid-1", "resourceVersion": "7"},
    }
    client.get_custom_resource_strict = Mock(
        side_effect=lambda *a, **k: (
            StrictReadOutcome(status=StrictReadStatus.ITEMS, resource=mco)
            if client.get_custom_resource_strict.call_count == 1
            else StrictReadOutcome(status=StrictReadStatus.OBJECT_ABSENT)
        )
    )
    client.list_custom_resources_strict = Mock(
        return_value=StrictReadOutcome(status=StrictReadStatus.ITEMS, items=[mco], resource_version="1")
    )
    client.delete_custom_resource_preconditioned = Mock(return_value=None)
    client.get_namespace_strict = Mock(return_value=StrictReadOutcome(status=StrictReadStatus.NAMESPACE_ABSENT))
    client.list_pods_strict = Mock(
        return_value=StrictReadOutcome(status=StrictReadStatus.ITEMS, items=[], resource_version="pods-1")
    )
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

    def test_delete_observability_with_resources(self, decommission_with_obs, mock_primary_client):
        """Delete the observed MCO with its UID and report the accepted mutation."""
        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        mock_primary_client.delete_custom_resource_preconditioned.assert_called_once_with(
            "observability.open-cluster-management.io",
            "v1beta2",
            "multiclusterobservabilities",
            "observability",
            uid="uid-1",
            namespace=None,
            timeout_seconds=DELETE_REQUEST_TIMEOUT,
        )
        mock_primary_client.delete_custom_resource.assert_not_called()

    def test_delete_observability_not_found(self, decommission_with_obs, mock_primary_client):
        """Positive object and namespace absence skips every delete."""
        mock_primary_client.get_custom_resource_strict.side_effect = None
        mock_primary_client.get_custom_resource_strict.return_value = _strict("OBJECT_ABSENT")

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        mock_primary_client.delete_custom_resource_preconditioned.assert_not_called()
        mock_primary_client.delete_custom_resource.assert_not_called()

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
            "teardown_observability",
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
        """The live run performs EVERY one of its own reads, not merely some read.

        The two fixtures share one ``state_manager``, so a dry run that cached an
        observation where a live run could find it would be visible here. A bare
        ``assert mock_primary_client.method_calls`` was too weak: it passed for a run
        that reused a cached MultiClusterObservability decision and only read the
        remaining two families. The whole per-family read sequence is pinned instead.

        Kill condition: short-circuiting ANY of the live reads from a preview
        observation (or from anything else the dry run left behind).
        """
        decommission_dry_run.decommission(interactive=False)
        mock_primary_client.reset_mock()
        result = decommission_with_obs.decommission(interactive=False)
        assert result.succeeded is True
        assert result.changed is True, "live MCO presence must override the empty preview"

        calls = mock_primary_client.method_calls
        mco_read = call.get_custom_resource_strict(
            group="observability.open-cluster-management.io",
            version="v1beta2",
            plural="multiclusterobservabilities",
            name="observability",
            namespace=None,
        )
        assert calls == [
            mco_read,
            call.delete_custom_resource_preconditioned(
                "observability.open-cluster-management.io",
                "v1beta2",
                "multiclusterobservabilities",
                "observability",
                uid="uid-1",
                namespace=None,
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            ),
            mco_read,
            call.get_namespace_strict(OBSERVABILITY_NAMESPACE),
            mco_read,
            call.get_namespace_strict(OBSERVABILITY_NAMESPACE),
            call.list_managed_clusters(),
            call.list_custom_resources(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                namespace=ACM_NAMESPACE,
            ),
        ]

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

    def test_a_delete_accepted_then_a_failed_drain_still_reports_the_change(
        self, decommission_with_obs, mock_primary_client, caplog
    ):
        """The DELETE landed; a later stage failed. Both facts must survive.

        C3 changed the mechanism -- one named resource through the guarded primitive,
        not a list of them -- but not this contract: an operator whose MCO has already
        been destroyed must not be told nothing changed.
        """
        from lib.strict_read import StrictReadOutcome, StrictReadStatus

        # The drain namespace is present but unreadable, so the run fails AFTER the
        # accepted delete.
        mock_primary_client.get_namespace_strict = Mock(return_value=StrictReadOutcome(status=StrictReadStatus.ERROR))

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_observability()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True, "this invocation destroyed the MCO"
        mock_primary_client.delete_custom_resource_preconditioned.assert_called_once()

    def test_a_rejected_delete_reports_no_change(self, decommission_with_obs, mock_primary_client, caplog):
        """Nothing was accepted, so nothing changed."""
        from lib.exceptions import PreconditionConflict

        mock_primary_client.delete_custom_resource_preconditioned = Mock(
            side_effect=PreconditionConflict("the live object is not the proved one")
        )

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_observability()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is False

    def test_a_failure_message_never_carries_a_raw_response_body(
        self, decommission_with_obs, mock_primary_client, caplog
    ):
        """Status and reason only, from a REAL ApiException.

        Injecting a pre-converted SwitchoverError here would prove only that this
        module logs the message it was handed; the whole defect is that the raw
        exception was never converted at all. Scope note: the Mock client bypasses
        ``lib.kube_client.api_call``, so this proves what THIS module logs, not the
        shared decorator's own behaviour.
        """
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=self._api_error(403, "Forbidden"))

        with caplog.at_level(logging.ERROR):
            decommission_with_obs.teardown_observability()

        assert "403" in caplog.text and "Forbidden" in caplog.text
        assert "raw body must never be shown" not in caplog.text

    def test_a_forbidden_delete_stays_on_the_result_channel(self, decommission_with_obs, mock_primary_client, caplog):
        """403 is the RBAC-denial case: the operator gets a result, not a stack trace.

        An escaping ApiException is absorbed by a caller's ``except Exception`` arm,
        which stringifies it -- and ``str(ApiException)`` is the HTTP status line plus
        the response headers plus the response body.
        """
        exc = self._api_error(403, "Forbidden", body='{"message":"%s"}' % RESPONSE_CANARY)
        exc.headers = {"X-Canary": RESPONSE_CANARY}
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=exc)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        record = decommission_with_obs.run_record.teardown_record(MCO_KEY)
        assert record is not None and record.phase is TeardownPhase.DELETE_STARTED, (
            "the July phase table already covers 'delete may or may not have been accepted'; "
            "a rejected delete must not invent a further phase"
        )
        assert "403" in caplog.text and "Forbidden" in caplog.text
        assert RESPONSE_CANARY not in caplog.text

    @pytest.mark.parametrize("transport", ["read_timeout", "max_retry"])
    def test_a_transport_failure_stays_on_the_result_channel(
        self, transport, decommission_with_obs, mock_primary_client, caplog
    ):
        """The urllib3 errors ``lib.kube_client`` already classifies are operational too.

        They reach the caller from the same primitive as an ApiException and must not
        be the one class of delete rejection that unwinds ``decommission()``.
        """
        from urllib3.exceptions import MaxRetryError, ReadTimeoutError

        exc = (
            ReadTimeoutError(None, "https://api.example/apis", RESPONSE_CANARY)
            if transport == "read_timeout"
            else MaxRetryError(None, "https://api.example/apis", RESPONSE_CANARY)
        )
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=exc)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert decommission_with_obs.run_record.teardown_record(MCO_KEY).phase is TeardownPhase.DELETE_STARTED
        assert type(exc).__name__ in caplog.text
        assert RESPONSE_CANARY not in caplog.text

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
        mock_primary_client.list_custom_resources.return_value = []  # Hive ClusterDeployments
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.delete_custom_resource.side_effect = self._api_error(409, "Conflict")

        result = decommission_with_obs.decommission(interactive=False)

        assert result.succeeded is False
        assert result.changed is True, "the destroyed MCO must be reported despite the later rejection"
        assert result.substeps["observability"] is SubstepOutcome.COMPLETED
        assert result.substeps["managed_clusters"] is SubstepOutcome.FAILED
        assert result.not_attempted == ("multiclusterhub",)
        assert "observability" in "\n".join(result.summary_lines())
        mock_primary_client.delete_custom_resource_preconditioned.assert_called_once()
        mock_primary_client.delete_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
            name="cluster1",
            timeout_seconds=DELETE_REQUEST_TIMEOUT,
        )

    def test_a_404_at_delete_time_is_no_longer_reported_as_an_accepted_delete(
        self, decommission_with_obs, mock_primary_client
    ):
        """This replaces a test that pinned the ambiguity PR C removed.

        The old caller-side arm treated a 404 from the delete as success, so an
        accepted delete and an already-absent object were indistinguishable and both
        reported ``changed``. The guarded primitive now raises ``TargetDisappeared``,
        and this invocation must report that it changed nothing -- it did not. The
        run still proves absence live, so the outcome is decided by the proofs rather
        than by the 404; only ``changed`` is settled here.
        """
        mock_primary_client.delete_custom_resource_preconditioned = Mock(
            side_effect=TargetDisappeared("already absent at delete time")
        )

        execution = decommission_with_obs.teardown_observability()

        assert execution.changed is False, "an object that was already gone is not this run's change"


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

    def test_preview_reports_false_when_nothing_is_present(self, decommission_dry_run, mock_primary_client):
        # The MCO is proven absent, not merely unlisted: the observability branch now
        # predicts from a strict named read, which distinguishes the two.
        mock_primary_client.get_custom_resource_strict = Mock(side_effect=None, return_value=_strict("OBJECT_ABSENT"))
        for substep in ("observability", "managed_clusters", "multiclusterhub"):
            assert decommission_dry_run._preview_substep(substep) is False

    def test_an_unreadable_mco_read_refuses_to_predict_no_change(self, decommission_dry_run, mock_primary_client):
        """The read-as-absence failure mode the strict-read algebra exists to remove.

        Through the non-strict list, an unreachable or forbidden API server predicted
        "nothing to delete" and the operator planned the switchover around it. The
        preview must fail loudly instead, and it must agree with the strict branch the
        live teardown uses for the same resource.
        """
        mock_primary_client.get_custom_resource_strict = Mock(side_effect=None, return_value=_strict("ERROR"))

        with pytest.raises(SwitchoverError, match="MultiClusterObservability"):
            decommission_dry_run.decommission(interactive=False)

        mock_primary_client.delete_custom_resource_preconditioned.assert_not_called()

    @pytest.mark.parametrize(
        "read, predicted",
        [("ITEMS", True), ("OBJECT_ABSENT", False), ("CRD_ABSENT", False)],
    )
    def test_the_mco_preview_predicts_from_a_strict_named_read(
        self, read, predicted, decommission_dry_run, mock_primary_client
    ):
        mock_primary_client.get_custom_resource_strict = Mock(
            side_effect=None,
            return_value=_strict(read, resource=_mco() if read == "ITEMS" else None),
        )

        assert decommission_dry_run._preview_substep("observability") is predicted
        mock_primary_client.get_custom_resource_strict.assert_called_once_with(
            group=decommission_module.OBSERVABILITY_TEARDOWN.group,
            version=decommission_module.OBSERVABILITY_TEARDOWN.version,
            plural=decommission_module.OBSERVABILITY_TEARDOWN.plural,
            name=decommission_module.OBSERVABILITY_TEARDOWN.name,
            namespace=decommission_module.OBSERVABILITY_TEARDOWN.namespace,
        )

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
            [],  # Hive ClusterDeployments
            [{"metadata": {"name": "multiclusterhub"}}],  # MCH
        ]
        mock_primary_client.list_managed_clusters.return_value = [{"metadata": {"name": "cluster1"}}]
        mock_primary_client.delete_custom_resource.return_value = True

        result = decomm.decommission(interactive=False)

        assert result.succeeded is True
        assert result.changed is True
        assert result.substeps == {
            "observability": SubstepOutcome.COMPLETED,
            "managed_clusters": SubstepOutcome.COMPLETED,
            "multiclusterhub": SubstepOutcome.COMPLETED,
        }
        mock_primary_client.delete_custom_resource_preconditioned.assert_called_once_with(
            "observability.open-cluster-management.io",
            "v1beta2",
            "multiclusterobservabilities",
            "observability",
            uid="uid-1",
            namespace=None,
            timeout_seconds=DELETE_REQUEST_TIMEOUT,
        )
        assert mock_primary_client.delete_custom_resource.call_args_list == [
            call(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
                name="cluster1",
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            ),
            call(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                name="multiclusterhub",
                namespace=ACM_NAMESPACE,
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            ),
        ]


def _strict(status, items=None, resource=None, resource_version=None):
    from lib.strict_read import StrictReadOutcome, StrictReadStatus

    return StrictReadOutcome(
        status=getattr(StrictReadStatus, status),
        items=items or [],
        resource=resource,
        resource_version=resource_version,
    )


def _mco(uid="uid-1", resource_version="7"):
    return {
        "apiVersion": "observability.open-cluster-management.io/v1beta2",
        "kind": "MultiClusterObservability",
        "metadata": {"name": "observability", "uid": uid, "resourceVersion": resource_version},
    }


def _seed_record(decommission, *, key=MCO_KEY, phase, expected_uid="uid-1"):
    """Write one teardown record at ``phase`` through the real RunRecord.

    Resume cases must be seeded through the durable writer, not by stubbing the
    reader: a record that the validator would refuse is not a resumable state, and a
    stub would let the phase machine be tested against one that never could exist.
    """
    decommission.run_record.record_teardown_phase(TeardownRecord(key=key, expected_uid=expected_uid, phase=phase))


def _arrange(dec, *, cr=None, pods=None, namespace=None):
    """Arrange the whole seam the phase machine reads through.

    Every test below sets up the reads it depends on. A test that asserted an
    outcome without arranging these would pass or fail on the fixture's defaults
    rather than on the behaviour it names.

    The durable seam is deliberately NOT stubbed: the writer stays the real
    ``RunRecord.record_teardown_phase``, wrapped only so call order and arguments can
    be asserted. Replacing it would disable ``lib.teardown_record.validate`` for every
    test in the class, which is exactly how a malformed completion-evidence write can
    pass a full green run. Seed prior records with ``_seed_record`` BEFORE calling
    this, so the seed does not appear in the recorded calls.
    """
    client = dec.primary
    # Sequential: the initial guarded read, then the final verification pass. A single
    # return_value would make the final pass see the object still present, so a
    # successful teardown could never be arranged.
    initial = cr if cr is not None else _strict("ITEMS", resource=_mco())
    final = _strict("OBJECT_ABSENT") if initial.status.name == "ITEMS" else initial
    client.get_custom_resource_strict = Mock(side_effect=[initial, final, final, final])
    client.delete_custom_resource_preconditioned = Mock(return_value=None)
    client.list_pods_strict = Mock(
        return_value=pods if pods is not None else _strict("ITEMS", items=[], resource_version="pods-1")
    )
    client.get_namespace_strict = Mock(return_value=namespace if namespace is not None else _strict("NAMESPACE_ABSENT"))
    dec.run_record.record_teardown_phase = Mock(wraps=dec.run_record.record_teardown_phase)
    return client


@pytest.mark.unit
class TestSharedTeardownPhaseMachine:
    """C3: one phase machine in ``Decommission``, consumed by both callers.

    GLM-H6 is closed only when ``modules/finalization.py`` owns zero MCO deletion
    logic and calls ``Decommission.teardown_observability`` instead.
    """

    # ---------------------------------------------------------------- interface

    def test_teardown_observability_returns_the_one_execution_result_type(self, decommission_with_obs):
        """No tuple form and no side channel anywhere in C, D or E."""
        _arrange(decommission_with_obs)
        assert isinstance(decommission_with_obs.teardown_observability(), SubstepExecution)

    def test_the_gitops_marker_flag_is_keyword_only_and_defaults_off(self, decommission_with_obs):
        import inspect as _inspect

        marker = _inspect.signature(decommission_with_obs.teardown_observability).parameters["record_gitops_markers"]
        assert marker.kind is _inspect.Parameter.KEYWORD_ONLY
        assert marker.default is False, "markers are opt-in; only Finalization asks for them"

    # ---------------------------------------------------------------- ordering

    def test_expected_uid_and_delete_started_are_durable_before_the_delete(self, decommission_with_obs):
        """The identity map must be persisted BEFORE the first DELETE: a crash
        mid-delete must not leave the run with no record of what it was removing."""
        order = []
        client = _arrange(decommission_with_obs)
        decommission_with_obs.run_record.record_teardown_phase = Mock(
            side_effect=lambda record: order.append(("record", record.phase.value, record.expected_uid))
        )
        client.delete_custom_resource_preconditioned = Mock(
            side_effect=lambda *a, **k: order.append(("delete", k.get("uid"), None))
        )

        decommission_with_obs.teardown_observability()

        assert ("delete", "uid-1", None) in order, "the guarded delete must be issued"
        before = order[: order.index(("delete", "uid-1", None))]
        assert ("record", "delete_started", "uid-1") in before

    # ---------------------------------------------------------------- changed

    def test_changed_is_true_when_this_invocation_had_its_delete_accepted(self, decommission_with_obs):
        _arrange(decommission_with_obs)
        assert decommission_with_obs.teardown_observability().changed is True

    def test_a_resumed_record_whose_delete_landed_earlier_reports_changed_false(self, decommission_with_obs):
        """The path a fresh-run test cannot reach. Resuming a record already past the
        DELETE and completing only the drain must NOT report changed: this invocation
        mutated nothing, even though it writes ``completed``."""
        _seed_record(decommission_with_obs, phase=TeardownPhase.CR_ABSENT)
        client = _arrange(decommission_with_obs, cr=_strict("OBJECT_ABSENT"))

        execution = decommission_with_obs.teardown_observability()

        assert execution.changed is False, "an earlier invocation's delete is not this one's change"
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_a_resumed_drained_record_whose_final_proof_fails_reports_no_change(self, decommission_with_obs):
        """B3.2 case 5, through the real machine rather than a stubbed aggregator.

        Nothing was accepted in this invocation, so the failure carries no mutation --
        and a resumed record must still fail closed when its final proof is unreadable.
        """
        _seed_record(decommission_with_obs, phase=TeardownPhase.DRAINED)
        client = _arrange(decommission_with_obs, cr=_strict("OBJECT_ABSENT"))
        client.get_custom_resource_strict.side_effect = [
            _strict("OBJECT_ABSENT"),
            _strict("OBJECT_ABSENT"),
            _strict("ERROR"),
        ]

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()
        assert decommission_with_obs.run_record.teardown_record(MCO_KEY).phase is not TeardownPhase.COMPLETED

    # ---------------------------------------------------------------- preconditions

    def test_no_record_with_crd_and_namespace_both_proven_absent_is_a_precondition_noop(self, decommission_with_obs):
        _arrange(
            decommission_with_obs,
            cr=_strict("CRD_ABSENT"),
            namespace=_strict("NAMESPACE_ABSENT"),
        )
        execution = decommission_with_obs.teardown_observability()
        assert execution.outcome is SubstepOutcome.PRECONDITION_NOOP
        assert execution.changed is False

    def test_crd_absent_but_observability_namespace_present_is_fatal(self, decommission_with_obs):
        """Half-removed observability is not a clean skip: something is still there."""
        _arrange(
            decommission_with_obs,
            cr=_strict("CRD_ABSENT"),
            namespace=_strict("ITEMS", resource_version="ns-1"),
        )
        assert decommission_with_obs.teardown_observability().outcome is SubstepOutcome.FAILED

    def test_a_strict_read_error_is_fatal_never_treated_as_absent(self, decommission_with_obs):
        _arrange(decommission_with_obs, cr=_strict("ERROR"))
        assert decommission_with_obs.teardown_observability().outcome is SubstepOutcome.FAILED

    def test_a_same_name_different_uid_cr_is_fatal_and_left_intact(self, decommission_with_obs):
        _seed_record(decommission_with_obs, phase=TeardownPhase.DELETE_STARTED)
        client = _arrange(
            decommission_with_obs,
            cr=_strict("ITEMS", resource=_mco(uid="uid-REPLACEMENT")),
        )
        execution = decommission_with_obs.teardown_observability()
        assert execution.outcome is SubstepOutcome.FAILED
        client.delete_custom_resource_preconditioned.assert_not_called()

    # ---------------------------------------------------------------- exception boundary

    def test_an_expected_operational_failure_is_returned_not_raised(self, decommission_with_obs):
        """IV-R403-01: expected SwitchoverError-class failures arrive on the return
        channel so ``decommission()`` can aggregate them."""
        client = _arrange(decommission_with_obs)
        client.delete_custom_resource_preconditioned = Mock(side_effect=SwitchoverError("boom"))
        assert decommission_with_obs.teardown_observability().outcome is SubstepOutcome.FAILED

    def test_a_precondition_conflict_is_expected_and_is_returned(self, decommission_with_obs):
        """C1 raises PreconditionConflict when the live object is not the proved one:
        an expected operational outcome here, not a crash."""
        from lib.exceptions import PreconditionConflict

        client = _arrange(decommission_with_obs)
        client.delete_custom_resource_preconditioned = Mock(side_effect=PreconditionConflict("mismatch"))
        assert decommission_with_obs.teardown_observability().outcome is SubstepOutcome.FAILED

    def test_a_target_that_disappeared_at_the_delete_is_verified_then_completed(
        self, decommission_with_obs, mock_primary_client, caplog
    ):
        """Replaces the contract that pinned TargetDisappeared to FAILED.

        July step 3 runs the absence poll "GET until 404/absent", and C1 raises the 404
        precisely so the caller can verify live rather than assume. The object being
        gone before the DELETE landed is therefore not a failure: it is the beginning
        of the proof obligation, and every remaining proof still runs.
        """
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=TargetDisappeared("gone"))

        with caplog.at_level(logging.INFO):
            execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        record = decommission_with_obs.run_record.teardown_record(MCO_KEY)
        assert record.phase is TeardownPhase.COMPLETED
        assert record.resource_versions == {}
        assert set(record.absence_proofs) == {"target_cr", "drain_namespace"}
        assert "disappeared" in caplog.text

    def test_a_replacement_found_by_the_absence_poll_after_a_disappearance_is_fatal(
        self, decommission_with_obs, mock_primary_client
    ):
        """The name coming back with another UID is a recreation, not this teardown."""
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=TargetDisappeared("gone"))
        mock_primary_client.get_custom_resource_strict = Mock(
            side_effect=[
                _strict("ITEMS", resource=_mco()),
                _strict("ITEMS", resource=_mco(uid="uid-REPLACEMENT")),
            ]
        )

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        mock_primary_client.delete_custom_resource_preconditioned.assert_called_once()

    def test_a_failing_final_pass_after_a_disappearance_still_fails(self, decommission_with_obs, mock_primary_client):
        """A disappearance skips no proof: an unreadable final pass cannot complete."""
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=TargetDisappeared("gone"))
        mock_primary_client.get_custom_resource_strict = Mock(
            side_effect=[_strict("ITEMS", resource=_mco()), _strict("OBJECT_ABSENT"), _strict("ERROR")]
        )

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert decommission_with_obs.run_record.teardown_record(MCO_KEY).phase is not TeardownPhase.COMPLETED

    def test_a_programmer_error_from_the_delete_primitive_propagates(self, decommission_with_obs):
        """The primitive raises ValidationError on an empty uid, which means the
        caller failed to supply a proved identity. Converting that into a FAILED
        result would hide a bug behind an operational-looking outcome."""
        from lib.validation import ValidationError

        client = _arrange(decommission_with_obs)
        client.delete_custom_resource_preconditioned = Mock(side_effect=ValidationError("empty uid"))
        with pytest.raises(ValidationError):
            decommission_with_obs.teardown_observability()

    def test_an_unexpected_exception_propagates_uncaught(self, decommission_with_obs):
        client = _arrange(decommission_with_obs)
        client.delete_custom_resource_preconditioned = Mock(side_effect=RuntimeError("bug"))
        with pytest.raises(RuntimeError):
            decommission_with_obs.teardown_observability()

    @pytest.mark.parametrize("stage", ["cr", "pods"])
    def test_teardown_waits_for_asynchronous_removal(self, stage, decommission_with_obs, monkeypatch):
        """A pending first read must not fail an ordinary asynchronous deletion."""
        client = _arrange(decommission_with_obs, namespace=_strict("ITEMS", resource_version="ns-1"))
        if stage == "cr":
            client.get_custom_resource_strict.side_effect = [
                _strict("ITEMS", resource=_mco()),
                _strict("ITEMS", resource=_mco()),
                _strict("OBJECT_ABSENT"),
                _strict("OBJECT_ABSENT"),
            ]
        else:
            client.list_pods_strict.side_effect = [
                _strict("ITEMS", items=[{"metadata": {"name": "obs-pod"}}], resource_version="pods-1"),
                _strict("ITEMS", resource_version="pods-2"),
                _strict("ITEMS", resource_version="pods-final"),
            ]
        monkeypatch.setattr("lib.waiter.time.sleep", lambda _: None)

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        phases = [c.args[0].phase.value for c in decommission_with_obs.run_record.record_teardown_phase.call_args_list]
        assert phases == ["delete_started", "cr_absent", "drain_pending", "drained", "completed"]

    @pytest.mark.parametrize("stage", ["cr", "pods"])
    def test_timeout_blocks_without_losing_the_accepted_delete(self, stage, decommission_with_obs, monkeypatch, caplog):
        """A stuck CR or pod must leave durable unfinished work and changed=True."""
        client = _arrange(decommission_with_obs, namespace=_strict("ITEMS", resource_version="ns-1"))
        if stage == "cr":
            client.get_custom_resource_strict.side_effect = None
            client.get_custom_resource_strict.return_value = _strict("ITEMS", resource=_mco())
        else:
            client.list_pods_strict.return_value = _strict(
                "ITEMS", items=[{"metadata": {"name": "obs-pod"}}], resource_version="pods-1"
            )
        # Exercise the real waiter's deadline without spending the production window.
        monkeypatch.setattr(decommission_module, "OBSERVABILITY_TERMINATE_TIMEOUT", 0)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        client.delete_custom_resource_preconditioned.assert_called_once()
        phases = [c.args[0].phase.value for c in decommission_with_obs.run_record.record_teardown_phase.call_args_list]
        assert phases == (["delete_started"] if stage == "cr" else ["delete_started", "cr_absent", "drain_pending"])
        assert "timeout" in caplog.text.lower()

    def test_drain_timeout_rechecks_before_failing(self, decommission_with_obs, monkeypatch):
        """Pods gone at the deadline still complete, with a separate final proof."""
        client = _arrange(decommission_with_obs, namespace=_strict("ITEMS", resource_version="ns-1"))
        client.list_pods_strict.side_effect = [
            _strict("ITEMS", items=[{"metadata": {"name": "obs-pod"}}], resource_version="pods-1"),
            _strict("ITEMS", resource_version="pods-boundary"),
            _strict("ITEMS", resource_version="pods-final"),
        ]
        # CR wait completes at t=0; the pending drain read consumes its deadline.
        times = iter([0, 0, 0, 0, 300, 300])
        monkeypatch.setattr("lib.waiter.time", Mock(time=lambda: next(times, 300), sleep=Mock()))

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        assert client.list_pods_strict.call_count == 3
        completed = decommission_with_obs.run_record.record_teardown_phase.call_args.args[0]
        assert completed.resource_versions["drain_pods"] == "pods-final"

    @pytest.mark.parametrize("failure", ["cr_error", "replacement", "namespace_error", "pods_error", "final_pods"])
    def test_poll_and_final_proof_failures_never_complete(self, failure, decommission_with_obs):
        """Read errors, replacements, and pods reappearing cannot certify completion."""
        client = _arrange(decommission_with_obs, namespace=_strict("ITEMS", resource_version="ns-1"))
        if failure in ("cr_error", "replacement"):
            observed = _strict("ERROR") if failure == "cr_error" else _strict("ITEMS", resource=_mco(uid="replacement"))
            client.get_custom_resource_strict.side_effect = [_strict("ITEMS", resource=_mco()), observed]
        elif failure == "namespace_error":
            client.get_namespace_strict.return_value = _strict("ERROR")
        elif failure == "pods_error":
            client.list_pods_strict.return_value = _strict("ERROR")
        else:
            client.list_pods_strict.side_effect = [
                _strict("ITEMS", resource_version="pods-drained"),
                _strict("ITEMS", items=[{"metadata": {"name": "new-pod"}}], resource_version="pods-final"),
            ]

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        client.delete_custom_resource_preconditioned.assert_called_once()
        assert "completed" not in [
            c.args[0].phase.value for c in decommission_with_obs.run_record.record_teardown_phase.call_args_list
        ]

    # ---------------------------------------------------------------- evidence

    def test_completion_evidence_comes_only_from_the_final_verification_pass(self, decommission_with_obs):
        """Evidence copied from an earlier phase, a pre-DELETE read or a previous
        invocation would certify something this run never re-proved."""
        _arrange(
            decommission_with_obs,
            namespace=_strict("ITEMS", resource_version="ns-final"),
            pods=_strict("ITEMS", items=[], resource_version="pods-final"),
        )
        decommission_with_obs.teardown_observability()

        completed = [
            call.args[0]
            for call in decommission_with_obs.run_record.record_teardown_phase.call_args_list
            if call.args[0].phase.value == "completed"
        ]
        assert completed, "a successful teardown must write a completed record"
        record = completed[-1]
        assert record.resource_versions["drain_pods"] == "pods-final"
        assert record.resource_versions["drain_namespace"] == "ns-final"
        assert "cr" not in record.resource_versions, "no pre-DELETE revision is carried forward"

    def test_a_pre_delete_revision_is_never_persisted(self, decommission_with_obs):
        """Provenance, at the persisted bytes rather than at one spelling of the key.

        Asserting only ``"cr" not in resource_versions`` catches the field name the
        implementation happens to use today; the rule is that no revision observed
        before the DELETE is carried into the completion evidence at all.
        """
        _arrange(
            decommission_with_obs,
            cr=_strict("ITEMS", resource=_mco(resource_version="77310"), resource_version="77310"),
            namespace=_strict("ITEMS", resource_version="ns-final"),
            pods=_strict("ITEMS", items=[], resource_version="pods-final"),
        )

        assert decommission_with_obs.teardown_observability().outcome is SubstepOutcome.COMPLETED
        stored = json.dumps(decommission_with_obs.run_record.all_teardown_records(), default=str)
        assert "77310" not in stored

    def test_the_namespace_absent_drain_mode_records_a_proof_and_an_empty_revision_map(self, decommission_with_obs):
        """The two drain modes are mutually exclusive; resource_versions is present
        and empty in the namespace-absent mode, never omitted."""
        _arrange(decommission_with_obs, namespace=_strict("NAMESPACE_ABSENT"))
        decommission_with_obs.teardown_observability()

        completed = [
            call.args[0]
            for call in decommission_with_obs.run_record.record_teardown_phase.call_args_list
            if call.args[0].phase.value == "completed"
        ]
        record = completed[-1]
        assert record.resource_versions == {}
        assert record.absence_proofs["drain_namespace"].proof_type == "namespace_absent"

    # ---------------------------------------------------------------- dry run

    def test_dry_run_writes_no_record_and_issues_no_delete(self, decommission_with_obs):
        client = _arrange(decommission_with_obs)
        decommission_with_obs.dry_run = True
        decommission_with_obs.teardown_observability()
        decommission_with_obs.run_record.record_teardown_phase.assert_not_called()
        client.delete_custom_resource_preconditioned.assert_not_called()

    # ---------------------------------------------------------------- gitops markers

    @pytest.mark.parametrize("requested", [False, True])
    def test_markers_are_recorded_only_when_explicitly_requested(self, requested, decommission_with_obs):
        """Both directions. Asserting only the True case would let a True default slip
        in and make Finalization's opt-in meaningless.

        Each direction gets its own instance: a second teardown against the same
        durable record resumes a completed one and never reaches the marker branch.
        """
        with patch.object(decommission_module, "safe_record_gitops_markers") as recorder:
            _arrange(decommission_with_obs)
            decommission_with_obs.teardown_observability(record_gitops_markers=requested)
            assert recorder.called is requested

    # ---------------------------------------------------------------- GLM-H6

    def test_the_duplicate_decommission_copy_is_gone(self):
        """GLM-H6 kill condition, half one."""
        assert not hasattr(decommission_module.Decommission, "_delete_observability")

    def test_the_dead_caller_side_404_arm_is_gone(self):
        """Deleted with the call site it guarded; its behaviour is re-asserted against
        the real seam instead of decorator-bypassing mocks."""
        source = inspect.getsource(decommission_module.Decommission)
        assert "already gone (404), treating as success" not in source
