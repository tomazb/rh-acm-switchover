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
from lib.constants import (
    ACM_NAMESPACE,
    DECOMMISSION_POD_INTERVAL,
    DECOMMISSION_POD_TIMEOUT,
    DELETE_REQUEST_TIMEOUT,
    GATE_REASON_ACK_NOT_APPLICABLE,
    GATE_REASON_DESTINATION_ABSENT,
    GATE_REASON_DESTINATION_UNVERIFIABLE,
    GATE_REASON_SOURCE_AMBIGUOUS,
    GATE_REASON_SOURCE_UNVERIFIABLE,
    LOCAL_CLUSTER_NAME,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_POD_LABEL_SELECTOR,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
)
from lib.decommission_outcome import (
    DecommissionResult,
    ObservabilityGateDecision,
    ObservabilityGateResult,
    SubstepExecution,
    SubstepOutcome,
)
from lib.exceptions import FatalError, SwitchoverError, TargetDisappeared
from lib.kube_client import KubeClient
from lib.run_record import HubFacts, RunRecord
from lib.strict_read import StrictReadOutcome
from lib.teardown_record import (
    AbsenceProof,
    TeardownPhase,
    TeardownRecord,
    teardown_key,
)
from lib.utils import StateManager
from modules.decommission_identity import OperatorIdentity, classify_pods

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
    client.list_managed_clusters_strict = Mock(
        return_value=StrictReadOutcome(status=StrictReadStatus.ITEMS, items=[], resource_version="mc-1")
    )
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
        return_value=StrictReadOutcome(status=StrictReadStatus.ITEMS, items=[], resource_version="1")
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
        return []

    def _list_custom_resources_strict(group, version, plural, namespace=None, label_selector=None):
        if plural == "multiclusterhubs":
            return _mch_inventory(MCH_NAME)
        if plural == "clusterserviceversions":
            return _strict("ITEMS", items=[_csv()], resource_version="csv-list-1")
        return _strict("ITEMS", items=[], resource_version="1")

    def _get_custom_resource_strict(group, version, plural, name, namespace=None):
        if plural == "multiclusterhubs":
            return _mch_present(name)
        if plural == "clusterserviceversions":
            return _strict("ITEMS", resource=_csv(), resource_version="csv-1")
        return _strict("ITEMS", resource=_mco())

    mock_primary_client.list_custom_resources.side_effect = _list_custom_resources
    mock_primary_client.list_custom_resources_strict = Mock(side_effect=_list_custom_resources_strict)
    mock_primary_client.get_custom_resource_strict = Mock(side_effect=_get_custom_resource_strict)
    mock_primary_client.get_deployment_strict = Mock(return_value=_deployment())
    mc_items = [
        {"metadata": {"name": "cluster1", "uid": "uid-c1"}},
        {"metadata": {"name": LOCAL_CLUSTER_NAME, "uid": "uid-local"}},
    ]
    mock_primary_client.list_managed_clusters.return_value = mc_items
    from lib.strict_read import StrictReadOutcome, StrictReadStatus

    mock_primary_client.list_managed_clusters_strict.return_value = StrictReadOutcome(
        status=StrictReadStatus.ITEMS, items=mc_items, resource_version="mc-1"
    )
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
        # Every family deletes through the UID-guarded primitive; nothing uses the name-only delete.
        assert mock_primary_client.delete_custom_resource_preconditioned.called
        mock_primary_client.delete_custom_resource.assert_not_called()

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

    def test_delete_managed_clusters_excludes_local(self, decommission_with_obs):
        """local-cluster is excluded; other ManagedClusters are UID-preconditioned deleted."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[
                _mc("cluster1", uid="uid-c1"),
                _mc(LOCAL_CLUSTER_NAME, uid="uid-local"),
                _mc("cluster2", uid="uid-c2"),
            ],
        )
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        deleted = [c.args[3] for c in client.delete_custom_resource_preconditioned.call_args_list]
        assert deleted == ["cluster1", "cluster2"]
        client.delete_custom_resource.assert_not_called()

    def test_delete_managed_clusters_blocks_unsafe_matching_clusterdeployment(
        self,
        decommission_with_obs,
        caplog,
    ):
        """Unsafe matching Hive ClusterDeployment blocks ManagedCluster deletion."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "cluster1", "namespace": "cluster1"},
                    "spec": {"preserveOnDelete": False},
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is False
        assert "preserveOnDelete=true" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_blocks_metadata_name_clusterdeployment_match(
        self,
        decommission_with_obs,
        caplog,
    ):
        """metadata.name is a conventional ManagedCluster match and blocks when unsafe."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "cluster1", "namespace": "cluster1"},
                    "spec": {"preserveOnDelete": False},
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (cluster1/cluster1)" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_blocks_spec_cluster_name_clusterdeployment_match(
        self,
        decommission_with_obs,
        caplog,
    ):
        """spec.clusterName is a conventional ManagedCluster match and blocks when unsafe."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "hive-cluster", "namespace": "hive-cluster"},
                    "spec": {"clusterName": "cluster1", "preserveOnDelete": False},
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (hive-cluster/hive-cluster)" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_blocks_cluster_metadata_cluster_name_match(
        self,
        decommission_with_obs,
        caplog,
    ):
        """spec.clusterMetadata.clusterName maps Hive resources restored with non-conventional names."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "hive-cluster", "namespace": "hive-cluster"},
                    "spec": {
                        "clusterMetadata": {"clusterName": "cluster1"},
                        "preserveOnDelete": False,
                    },
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (hive-cluster/hive-cluster)" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_blocks_cross_checked_cluster_install_ref_match(
        self,
        decommission_with_obs,
        caplog,
    ):
        """clusterInstallRef is accepted only when cross-checked by the ClusterDeployment namespace."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "agent-install", "namespace": "cluster1"},
                    "spec": {
                        "clusterInstallRef": {"name": "cluster1"},
                        "preserveOnDelete": False,
                    },
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "cluster1 (cluster1/agent-install)" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_allows_preserve_on_delete_true_match(self, decommission_with_obs):
        """Matched ClusterDeployments with preserveOnDelete=true allow ManagedCluster deletion."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "hive-cluster", "namespace": "hive-cluster"},
                    "spec": {
                        "clusterMetadata": {"clusterName": "cluster1"},
                        "preserveOnDelete": True,
                    },
                }
            ],
        )
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.COMPLETED
        client.delete_custom_resource_preconditioned.assert_called_once()

    def test_delete_managed_clusters_deletes_when_matching_clusterdeployment_is_preserved(self, decommission_with_obs):
        """A matching Hive ClusterDeployment with preserveOnDelete=true must not block ACM decommission."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "cluster1", "namespace": "cluster1"},
                    "spec": {"preserveOnDelete": True},
                }
            ],
        )
        decommission_with_obs.teardown_managed_clusters()
        client.delete_custom_resource_preconditioned.assert_called_once()
        args = client.delete_custom_resource_preconditioned.call_args
        assert args.args[0] == "cluster.open-cluster-management.io"
        assert args.args[3] == "cluster1"
        assert args.kwargs["uid"] == "uid-mc-1"
        assert args.kwargs["timeout_seconds"] == decommission_module.DELETE_REQUEST_TIMEOUT

    def test_delete_managed_clusters_fails_closed_for_plausible_unverified_clusterdeployment(
        self,
        decommission_with_obs,
        caplog,
    ):
        """A plausible but unverified namespace relationship blocks ManagedCluster deletion."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "agent-install", "namespace": "cluster1"},
                    "spec": {
                        "clusterInstallRef": {"name": "install-config"},
                        "preserveOnDelete": True,
                    },
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "Cannot verify ManagedCluster relationship" in caplog.text
        assert "cluster1/agent-install" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_fails_closed_for_conflicting_plausible_clusterdeployment(
        self,
        decommission_with_obs,
        caplog,
    ):
        """A confirmed identifier cannot override a different plausible target identifier."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1"), _mc("cluster2", uid="uid-c2")],
            hive_items=[
                {
                    "metadata": {"name": "cluster1", "namespace": "cluster2"},
                    "spec": {"preserveOnDelete": True},
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "conflicting ManagedCluster identifiers" in caplog.text
        assert "cluster2/cluster1" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_allows_verified_absent_hive_clusterdeployments(self, decommission_with_obs):
        """Verified empty Hive ClusterDeployment inventory remains acceptable."""
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("cluster1")])
        decommission_with_obs.teardown_managed_clusters()
        client.delete_custom_resource_preconditioned.assert_called_once()
        assert client.delete_custom_resource_preconditioned.call_args.args[3] == "cluster1"

    def test_delete_managed_clusters_api_error_blocks_destructive_deletion(
        self,
        decommission_with_obs,
        caplog,
    ):
        """Hive list ERROR fails closed before destructive ManagedCluster deletion."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_status="ERROR",
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_missing_hive_api_blocks_destructive_deletion(
        self,
        decommission_with_obs,
        caplog,
    ):
        """Missing Hive API (CRD_ABSENT) fails closed before destructive ManagedCluster deletion."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_status="CRD_ABSENT",
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_reports_all_unsafe_clusterdeployments(
        self,
        decommission_with_obs,
        caplog,
    ):
        """Unsafe report includes all matching ClusterDeployments deterministically."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1"), _mc("cluster2", uid="uid-c2")],
            hive_items=[
                {
                    "metadata": {"name": "cluster2", "namespace": "ns2"},
                    "spec": None,
                },
                {
                    "metadata": {"name": "cluster1", "namespace": "ns1"},
                    "spec": {"preserveOnDelete": False},
                },
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        message = caplog.text
        assert "cluster1 (ns1/cluster1)" in message
        assert "cluster2 (ns2/cluster2)" in message
        assert message.index("cluster1 (ns1/cluster1)") < message.index("cluster2 (ns2/cluster2)")
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_delete_managed_clusters_preserves_local_cluster_skip_without_hive_check(
        self, caplog, decommission_with_obs
    ):
        """local-cluster remains skipped and does not require Hive safety lookup."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc(LOCAL_CLUSTER_NAME, uid="uid-local")],
        )
        with caplog.at_level(logging.INFO, logger="acm_switchover"):
            decommission_with_obs.teardown_managed_clusters()
        client.list_custom_resources_strict.assert_not_called()
        client.delete_custom_resource_preconditioned.assert_not_called()
        assert "ClusterDeployment preserveOnDelete safety was verified" not in caplog.text

    def test_delete_managed_clusters_timeout(self, decommission_with_obs, caplog, monkeypatch):
        """Per-cluster absence proof timeout after an accepted DELETE reports changed=True."""
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
        )
        client.get_custom_resource_strict = Mock(
            side_effect=None,
            return_value=_strict("ITEMS", resource=_mc("cluster1")),
        )
        client.delete_custom_resource_preconditioned = Mock(return_value=None)
        monkeypatch.setattr(decommission_module, "MANAGED_CLUSTER_DELETE_TIMEOUT", 0)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True
        assert "Timeout" in caplog.text or "timeout" in caplog.text.lower()

    def test_delete_managed_clusters_survivor_list_is_bounded(self, decommission_with_obs, caplog, monkeypatch):
        """Large survivor sets use format_public_list so the family log stays bounded."""
        names = [f"cluster-{idx:03d}" for idx in range(60)]
        items = [_mc(name, uid=f"uid-{name}") for name in names]
        _arrange_mc_family(decommission_with_obs, inventory_items=items)

        def always_fail(spec, *, record_gitops_markers):
            return SubstepExecution(SubstepOutcome.FAILED, changed=False)

        monkeypatch.setattr(decommission_with_obs, "_teardown_resource", always_fail)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert "survivors" in caplog.text.lower() or "incomplete" in caplog.text.lower()
        assert "cluster-000" in caplog.text
        assert "40 more" in caplog.text
        # One sanitized family log line, not 60 raw names without truncation.
        assert len(caplog.text) < 5000

    def test_delete_managed_clusters_none_found(self, decommission_with_obs):
        """Proven-empty ManagedCluster inventory is a clean skip."""
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_decommission_unexpected_exception_propagates(self, decommission_with_obs, mock_primary_client):
        """An unexpected exception is never laundered into a handled result.

        Injected at the strict MultiClusterHub inventory read, the MCH family's first read.
        """
        mock_primary_client.list_custom_resources_strict.side_effect = Exception("API error")

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
        assert result.not_attempted == (
            "observability",
            "managed_clusters",
            "multiclusterhub",
        )
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
SUBSTEP_EXECUTORS = ("_run_substep", "teardown_managed_clusters", "teardown_multiclusterhub")


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
        assert calls == [
            "observability",
            "managed_clusters",
        ], "a failure aborts later substeps"

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
        confirm.side_effect = [
            True,
            True,
            False,
        ]  # proceed, run observability, decline the next
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
        assert result.not_attempted == (
            "observability",
            "managed_clusters",
            "multiclusterhub",
        )
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
        self,
        decommission_dry_run,
        decommission_with_obs,
        state_manager,
        mock_primary_client,
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
            call.list_managed_clusters_strict(),
            call.list_custom_resources_strict(
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

    def test_managed_cluster_delete_rejection_reports_the_earlier_delete(self, decommission_with_obs, caplog):
        """The first ManagedCluster DELETE was accepted; the second is rejected.

        The body assertion covers the message this module builds, not the shared
        ``api_call`` decorator's logging -- the Mock client never reaches it.
        """
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[
                _mc("cluster1", uid="uid-c1"),
                _mc("cluster2", uid="uid-c2"),
            ],
        )

        def delete_side_effect(group, version, plural, name, uid, namespace=None, timeout_seconds=None):
            if name == "cluster2":
                raise self._api_error(403, "Forbidden")
            return None

        # Named GET: present until deleted for cluster1; cluster2 fails at DELETE.
        by_name = {
            "cluster1": _mc("cluster1", uid="uid-c1"),
            "cluster2": _mc("cluster2", uid="uid-c2"),
        }

        def named_get(group, version, plural, name, namespace=None):
            resource = by_name.get(name)
            return _strict("ITEMS", resource=resource) if resource else _strict("OBJECT_ABSENT")

        def delete(group, version, plural, name, uid, namespace=None, timeout_seconds=None):
            delete_side_effect(group, version, plural, name, uid, namespace, timeout_seconds)
            by_name.pop(name, None)

        client.get_custom_resource_strict = Mock(side_effect=named_get)
        client.delete_custom_resource_preconditioned = Mock(side_effect=delete)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is True
        assert "403" in caplog.text and "Forbidden" in caplog.text
        assert "raw body must never be shown" not in caplog.text

    def test_managed_cluster_delete_rejection_with_no_prior_delete_reports_no_change(
        self, decommission_with_obs, caplog
    ):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("cluster1")])
        client.delete_custom_resource_preconditioned = Mock(side_effect=self._api_error(409, "Conflict"))

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()

        assert execution.outcome is SubstepOutcome.FAILED
        assert execution.changed is False

    def test_multiclusterhub_delete_rejection_reports_no_change_and_no_raw_body(self, state_manager, caplog):
        """The guarded MultiClusterHub DELETE is rejected after identity was recorded.

        Replaces the two-MultiClusterHub variant: more than one live MultiClusterHub now
        fails closed before any mutation (see ``TestMultiClusterHubTargetResolution``), so
        a "first deleted, second rejected" sequence is no longer reachable. The body
        assertion covers the message this module builds, not the shared ``api_call``
        decorator's logging -- the fake client never reaches it.
        """
        hub = _AcmHub(delete=self._api_error(403, "Forbidden", body='{"message":"%s"}' % RESPONSE_CANARY))
        dec = _mch_decommission(state_manager, hub)

        with caplog.at_level(logging.ERROR):
            execution = dec.teardown_multiclusterhub()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert _phases(hub) == ["delete_started"]
        assert "403" in caplog.text and "Forbidden" in caplog.text
        assert RESPONSE_CANARY not in caplog.text

    def test_a_rejected_delete_never_escapes_the_aggregator(self, decommission_with_obs, mock_primary_client):
        """The probed scenario: MCO destroyed, then a ManagedCluster DELETE is rejected.

        The operator must be told the MCO is gone, which is only possible if the
        DecommissionResult is constructed at all.
        """
        # After MCO completes, ManagedCluster inventory has one target whose DELETE is rejected.
        mc = _mc("cluster1")
        mock_primary_client.list_managed_clusters_strict = Mock(
            return_value=_strict("ITEMS", items=[mc], resource_version="mc-1")
        )
        mock_primary_client.list_custom_resources_strict = Mock(
            return_value=_strict("ITEMS", items=[], resource_version="cd-1")
        )

        mco = _mco()
        mc_get_calls = {"n": 0}

        def named_get(group, version, plural, name, namespace=None):
            if plural == "multiclusterobservabilities":
                # First call ITEMS, subsequent OBJECT_ABSENT (MCO phase machine).
                if mock_primary_client.get_custom_resource_strict.call_count <= 1:
                    return _strict("ITEMS", resource=mco)
                return _strict("OBJECT_ABSENT")
            if plural == "managedclusters":
                return _strict("ITEMS", resource=mc)
            return _strict("OBJECT_ABSENT")

        def delete_preconditioned(group, version, plural, name, uid=None, namespace=None, timeout_seconds=None):
            if plural == "managedclusters":
                raise self._api_error(409, "Conflict")
            return None

        mock_primary_client.get_custom_resource_strict = Mock(side_effect=named_get)
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=delete_preconditioned)

        result = decommission_with_obs.decommission(interactive=False)

        assert result.succeeded is False
        assert result.changed is True, "the destroyed MCO must be reported despite the later rejection"
        assert result.substeps["observability"] is SubstepOutcome.COMPLETED
        assert result.substeps["managed_clusters"] is SubstepOutcome.FAILED
        assert result.not_attempted == ("multiclusterhub",)
        assert "observability" in "\n".join(result.summary_lines())
        assert mock_primary_client.delete_custom_resource_preconditioned.call_count >= 2

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
        mock_primary_client.list_managed_clusters_strict.return_value = _strict(
            "ITEMS",
            items=[_mc(LOCAL_CLUSTER_NAME, uid="uid-local")],
            resource_version="1",
        )

        assert decommission_dry_run._preview_substep("managed_clusters") is False

    def test_preview_reports_false_when_nothing_is_present(self, decommission_dry_run, mock_primary_client):
        # The MCO is proven absent, not merely unlisted: the observability branch now
        # predicts from a strict named read, which distinguishes the two.
        mock_primary_client.get_custom_resource_strict = Mock(side_effect=None, return_value=_strict("OBJECT_ABSENT"))
        mock_primary_client.list_managed_clusters_strict.return_value = _strict("ITEMS", items=[], resource_version="1")
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
        assert result.not_attempted == (
            "observability",
            "managed_clusters",
            "multiclusterhub",
        )
        primary_with_all_resources.delete_custom_resource.assert_not_called()
        primary_with_all_resources.get_pods.assert_not_called()
        mock_wait.assert_not_called()


@pytest.mark.integration
class TestDecommissionIntegration:
    """Integration tests for Decommission workflows."""

    @patch("modules.decommission.wait_for_condition")
    def test_full_decommission_workflow(self, mock_wait, mock_primary_client, state_manager):
        """Test complete decommission workflow."""
        mock_wait.return_value = True

        decomm = Decommission(
            primary_client=mock_primary_client,
            has_observability=True,
            run_record=RunRecord(state_manager),
        )

        mc = _mc("cluster1")
        mock_primary_client.list_managed_clusters_strict.return_value = _strict(
            "ITEMS", items=[mc], resource_version="mc-1"
        )

        def strict_list(group, version, plural, namespace=None, label_selector=None):
            if plural == "multiclusterhubs":
                return _mch_inventory(MCH_NAME)
            if plural == "clusterserviceversions":
                return _strict("ITEMS", items=[_csv()], resource_version="csv-list-1")
            return _strict("ITEMS", items=[], resource_version="cd-1")

        mock_primary_client.list_custom_resources_strict = Mock(side_effect=strict_list)
        mock_primary_client.get_deployment_strict = Mock(return_value=_deployment())

        mco = _mco()
        by_name = {"cluster1": mc}
        mch_present = {"present": True}

        def named_get(group, version, plural, name, namespace=None):
            if plural == "multiclusterobservabilities":
                # Phase machine: first present, then absent proofs.
                if named_get.mco_calls == 0:
                    named_get.mco_calls += 1
                    return _strict("ITEMS", resource=mco)
                return _strict("OBJECT_ABSENT")
            if plural == "managedclusters":
                resource = by_name.get(name)
                return _strict("ITEMS", resource=resource) if resource else _strict("OBJECT_ABSENT")
            if plural == "multiclusterhubs":
                return _mch_present() if mch_present["present"] else _strict("OBJECT_ABSENT")
            if plural == "clusterserviceversions":
                return _strict("ITEMS", resource=_csv(), resource_version="csv-1")
            return _strict("OBJECT_ABSENT")

        named_get.mco_calls = 0

        def delete_preconditioned(group, version, plural, name, uid=None, namespace=None, timeout_seconds=None):
            if plural == "managedclusters":
                by_name.pop(name, None)
            if plural == "multiclusterhubs":
                mch_present["present"] = False
            return None

        mock_primary_client.get_custom_resource_strict = Mock(side_effect=named_get)
        mock_primary_client.delete_custom_resource_preconditioned = Mock(side_effect=delete_preconditioned)

        result = decomm.decommission(interactive=False)

        assert result.succeeded is True
        assert result.changed is True
        assert result.substeps == {
            "observability": SubstepOutcome.COMPLETED,
            "managed_clusters": SubstepOutcome.COMPLETED,
            "multiclusterhub": SubstepOutcome.COMPLETED,
        }
        assert any(
            c.args[:4]
            == (
                "observability.open-cluster-management.io",
                "v1beta2",
                "multiclusterobservabilities",
                "observability",
            )
            for c in mock_primary_client.delete_custom_resource_preconditioned.call_args_list
        )
        assert any(
            c.args[:4]
            == (
                "cluster.open-cluster-management.io",
                "v1",
                "managedclusters",
                "cluster1",
            )
            for c in mock_primary_client.delete_custom_resource_preconditioned.call_args_list
        )
        assert (
            call(
                "operator.open-cluster-management.io",
                "v1",
                "multiclusterhubs",
                "multiclusterhub",
                uid=MCH_UID,
                namespace=ACM_NAMESPACE,
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            )
            in mock_primary_client.delete_custom_resource_preconditioned.call_args_list
        )
        mock_primary_client.delete_custom_resource.assert_not_called()
        mch_record = RunRecord(state_manager).teardown_record(_mch_key())
        assert mch_record.phase is TeardownPhase.COMPLETED
        assert mch_record.operator_deployment["uid"] == OPERATOR_DEPLOYMENT_UID


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
        "metadata": {
            "name": "observability",
            "uid": uid,
            "resourceVersion": resource_version,
        },
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
        return_value=(pods if pods is not None else _strict("ITEMS", items=[], resource_version="pods-1"))
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
            side_effect=[
                _strict("ITEMS", resource=_mco()),
                _strict("OBJECT_ABSENT"),
                _strict("ERROR"),
            ]
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
                _strict(
                    "ITEMS",
                    items=[{"metadata": {"name": "obs-pod"}}],
                    resource_version="pods-1",
                ),
                _strict("ITEMS", resource_version="pods-2"),
                _strict("ITEMS", resource_version="pods-final"),
            ]
        monkeypatch.setattr("lib.waiter.time.sleep", lambda _: None)

        execution = decommission_with_obs.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        phases = [c.args[0].phase.value for c in decommission_with_obs.run_record.record_teardown_phase.call_args_list]
        assert phases == [
            "delete_started",
            "cr_absent",
            "drain_pending",
            "drained",
            "completed",
        ]

    @pytest.mark.parametrize("stage", ["cr", "pods"])
    def test_timeout_blocks_without_losing_the_accepted_delete(self, stage, decommission_with_obs, monkeypatch, caplog):
        """A stuck CR or pod must leave durable unfinished work and changed=True."""
        client = _arrange(decommission_with_obs, namespace=_strict("ITEMS", resource_version="ns-1"))
        if stage == "cr":
            client.get_custom_resource_strict.side_effect = None
            client.get_custom_resource_strict.return_value = _strict("ITEMS", resource=_mco())
        else:
            client.list_pods_strict.return_value = _strict(
                "ITEMS",
                items=[{"metadata": {"name": "obs-pod"}}],
                resource_version="pods-1",
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
            _strict(
                "ITEMS",
                items=[{"metadata": {"name": "obs-pod"}}],
                resource_version="pods-1",
            ),
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

    @pytest.mark.parametrize(
        "failure",
        ["cr_error", "replacement", "namespace_error", "pods_error", "final_pods"],
    )
    def test_poll_and_final_proof_failures_never_complete(self, failure, decommission_with_obs):
        """Read errors, replacements, and pods reappearing cannot certify completion."""
        client = _arrange(decommission_with_obs, namespace=_strict("ITEMS", resource_version="ns-1"))
        if failure in ("cr_error", "replacement"):
            observed = _strict("ERROR") if failure == "cr_error" else _strict("ITEMS", resource=_mco(uid="replacement"))
            client.get_custom_resource_strict.side_effect = [
                _strict("ITEMS", resource=_mco()),
                observed,
            ]
        elif failure == "namespace_error":
            client.get_namespace_strict.return_value = _strict("ERROR")
        elif failure == "pods_error":
            client.list_pods_strict.return_value = _strict("ERROR")
        else:
            client.list_pods_strict.side_effect = [
                _strict("ITEMS", resource_version="pods-drained"),
                _strict(
                    "ITEMS",
                    items=[{"metadata": {"name": "new-pod"}}],
                    resource_version="pods-final",
                ),
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
            cr=_strict(
                "ITEMS",
                resource=_mco(resource_version="77310"),
                resource_version="77310",
            ),
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


# --------------------------------------------------------------------------- C5 gate


class _GateHarness(Decommission):
    """A ``Decommission`` whose two hubs' strict reads are programmable per test.

    The gate reads four things live; a test that did not program all four would
    assert against ``Mock`` sentinels rather than against a modeled cluster, so
    every case below arranges both hubs explicitly.
    """

    def source(self, *, mco, namespace):
        self.primary.get_custom_resource_strict = Mock(return_value=mco)
        self.primary.get_namespace_strict = Mock(return_value=namespace)

    def destination(self, *, mco, namespace):
        self.secondary.list_custom_resources_strict = Mock(return_value=mco)
        self.secondary.get_namespace_strict = Mock(return_value=namespace)

    @property
    def primary_client(self):
        return self.primary

    @property
    def secondary_client(self):
        return self.secondary


@pytest.fixture
def integrated(state_manager):
    """Integrated teardown: a real RunRecord over a tmp_path StateManager, both hubs."""
    return _GateHarness(
        primary_client=Mock(),
        has_observability=True,
        run_record=RunRecord(state_manager),
        secondary_client=Mock(),
        acknowledge_observability_not_migrated=False,
    )


@pytest.fixture
def standalone(state_manager):
    """Standalone decommission: no destination client at all."""
    return _GateHarness(
        primary_client=Mock(),
        has_observability=True,
        run_record=RunRecord(state_manager),
    )


def _present_source(dec):
    dec.source(
        mco=StrictReadOutcome.from_resource(_mco()),
        namespace=StrictReadOutcome.from_resource({"metadata": {"name": OBSERVABILITY_NAMESPACE}}),
    )


def _present_destination(dec):
    dec.destination(
        mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
        namespace=StrictReadOutcome.from_resource({"metadata": {"name": OBSERVABILITY_NAMESPACE}}),
    )


def _absent_destination(dec):
    dec.destination(
        mco=StrictReadOutcome.crd_absent("kind_not_served"),
        namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
    )


@pytest.mark.unit
class TestDestinationObservabilityGate:
    """July section 4 / plan C5: the fresh, stateless destination gate."""

    def test_destination_present_passes_without_the_flag(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        assert integrated.destination_observability_gate().decision is ObservabilityGateDecision.PROCEED

    def test_destination_positively_absent_blocks_without_the_flag(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.crd_absent("kind_not_served"),
            namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
        )
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_DESTINATION_ABSENT

    def test_destination_positively_absent_proceeds_with_the_flag(self, integrated):
        integrated.acknowledge_observability_not_migrated = True
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.crd_absent("kind_not_served"),
            namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
        )
        assert integrated.destination_observability_gate().decision is ObservabilityGateDecision.PROCEED

    def test_destination_unverifiable_blocks_even_with_the_flag(self, integrated):
        integrated.acknowledge_observability_not_migrated = True
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.error("read_failed"),
            namespace=StrictReadOutcome.error("read_failed"),
        )
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_DESTINATION_UNVERIFIABLE

    def test_the_two_blocking_reasons_are_distinguishable(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.crd_absent("kind_not_served"),
            namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
        )
        absent = integrated.destination_observability_gate().reason
        integrated.destination(
            mco=StrictReadOutcome.error("read_failed"),
            namespace=StrictReadOutcome.error("read_failed"),
        )
        unverifiable = integrated.destination_observability_gate().reason
        assert absent != unverifiable

    def test_flag_is_rejected_when_the_gate_would_pass_anyway(self, integrated):
        integrated.acknowledge_observability_not_migrated = True
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_ACK_NOT_APPLICABLE

    def test_source_is_re_read_fresh_and_the_preflight_boolean_is_not_consulted(self, integrated):
        integrated.run_record.record_hub_facts(HubFacts(primary_has_observability=False))
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        assert integrated.destination_observability_gate().decision is ObservabilityGateDecision.PROCEED
        assert integrated.primary_client.get_namespace_strict.called

    def test_mixed_source_state_absent_crd_present_namespace_blocks(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.crd_absent("kind_not_served"),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_SOURCE_AMBIGUOUS

    def test_source_error_never_reads_as_nothing_to_delete(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.error("read_failed"),
            namespace=StrictReadOutcome.error("read_failed"),
        )
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_SOURCE_UNVERIFIABLE

    def test_source_positively_absent_is_not_applicable(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.crd_absent("kind_not_served"),
            namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
        )
        assert integrated.destination_observability_gate().decision is ObservabilityGateDecision.NOT_APPLICABLE

    def test_gate_result_is_not_persisted(self, integrated, state_manager, tmp_path):
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination_observability_gate()

        snapshot = json.dumps(state_manager.capture_state_snapshot())
        assert "observability_gate" not in snapshot
        assert "destination_observability" not in snapshot
        # The snapshot is an in-memory view; the file is what a later run reads.
        state_manager.flush_state()
        persisted = (tmp_path / "state.json").read_text(encoding="utf-8")
        assert "observability_gate" not in persisted
        assert "destination_observability" not in persisted

    def test_resume_reruns_the_gate_against_fresh_reads(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination_observability_gate()
        calls_after_first = integrated.secondary_client.list_custom_resources_strict.call_count
        integrated.destination_observability_gate()
        assert integrated.secondary_client.list_custom_resources_strict.call_count > calls_after_first

    def test_calling_the_gate_without_a_destination_client_refuses_explicitly(self, standalone):
        """A caller that reaches the gate with no destination hub is refused before
        any read, rather than failing on ``None`` deep inside the destination step."""
        _present_source(standalone)

        with pytest.raises(SwitchoverError, match="requires a secondary client"):
            standalone.destination_observability_gate()

        standalone.primary.get_custom_resource_strict.assert_not_called()
        standalone.primary.get_namespace_strict.assert_not_called()
        assert standalone.secondary is None

    def test_standalone_decommission_has_no_destination_gate(self, standalone):
        """Request level, not just a null client: the gate is never entered at all."""
        standalone.source(
            mco=StrictReadOutcome.from_items([{"metadata": {"uid": "u"}}]),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        with patch.object(
            standalone,
            "destination_observability_gate",
            wraps=standalone.destination_observability_gate,
        ) as gate, patch.object(decommission_module, "wait_for_condition", return_value=True):
            standalone.teardown_observability()

        gate.assert_not_called()
        assert standalone.secondary_client is None

    # ------------------------------------------------------------ decision table

    def test_the_destination_list_uses_the_canonical_mco_plural(self, integrated):
        _present_source(integrated)
        _present_destination(integrated)
        integrated.destination_observability_gate()
        kwargs = integrated.secondary_client.list_custom_resources_strict.call_args.kwargs
        assert kwargs["plural"] == "multiclusterobservabilities"
        assert kwargs["group"] == "observability.open-cluster-management.io"
        assert kwargs["version"] == "v1beta2"

    def test_an_empty_destination_inventory_is_positive_absence(self, integrated):
        """An ITEMS list with no items is a complete inventory proving absence."""
        _present_source(integrated)
        integrated.destination(
            mco=StrictReadOutcome.from_items([], resource_version="1"),
            namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
        )
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_DESTINATION_ABSENT

    @pytest.mark.parametrize(
        ("dest_mco", "dest_namespace"),
        [
            (
                StrictReadOutcome.from_items([{"metadata": {"uid": "d"}}]),
                StrictReadOutcome.namespace_absent("namespace_not_found"),
            ),
            (
                StrictReadOutcome.crd_absent("kind_not_served"),
                StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
            ),
        ],
        ids=["cr-present-namespace-absent", "cr-absent-namespace-present"],
    )
    def test_a_readable_but_mixed_destination_blocks_as_unverifiable(self, integrated, dest_mco, dest_namespace):
        """Ruled: mixed proves neither coherent presence nor complete absence, and
        the acknowledgement has no fact to acknowledge."""
        integrated.acknowledge_observability_not_migrated = True
        _present_source(integrated)
        integrated.destination(mco=dest_mco, namespace=dest_namespace)
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_DESTINATION_UNVERIFIABLE

    def test_the_ack_never_converts_a_source_block(self, integrated):
        integrated.acknowledge_observability_not_migrated = True
        integrated.source(
            mco=StrictReadOutcome.error("read_failed"),
            namespace=StrictReadOutcome.error("read_failed"),
        )
        result = integrated.destination_observability_gate()
        assert result.decision is ObservabilityGateDecision.BLOCKED
        assert result.reason == GATE_REASON_SOURCE_UNVERIFIABLE
        integrated.secondary_client.list_custom_resources_strict.assert_not_called()

    def test_a_source_block_reads_no_destination(self, integrated):
        integrated.source(
            mco=StrictReadOutcome.crd_absent("kind_not_served"),
            namespace=StrictReadOutcome.from_resource({"metadata": {"name": "ns"}}),
        )
        integrated.destination_observability_gate()
        integrated.secondary_client.list_custom_resources_strict.assert_not_called()
        integrated.secondary_client.get_namespace_strict.assert_not_called()

    def test_a_blocked_result_always_carries_a_reason(self):
        with pytest.raises(ValueError):
            ObservabilityGateResult(decision=ObservabilityGateDecision.BLOCKED)

    def test_the_gate_takes_no_arguments(self):
        parameters = inspect.signature(Decommission.destination_observability_gate).parameters
        assert list(parameters) == ["self"]


@pytest.mark.unit
class TestGateCallSiteInThePhaseMachine:
    """Where the gate sits in ``_teardown_resource``: after the completed dispatch,
    before ``expected_uid`` and therefore before any write or DELETE."""

    def test_a_blocked_gate_fails_the_substep_before_any_write_or_delete(self, integrated):
        _present_source(integrated)
        _absent_destination(integrated)
        writer = Mock(wraps=integrated.run_record.record_teardown_phase)
        integrated.run_record.record_teardown_phase = writer
        integrated.primary.delete_custom_resource_preconditioned = Mock()

        execution = integrated.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        integrated.primary.delete_custom_resource_preconditioned.assert_not_called()
        writer.assert_not_called()

    def test_a_blocked_gate_logs_only_the_sanitized_reason_code(self, integrated, caplog):
        """The stable code reaches the operator; the read's own detail never does.

        The canary lives only inside the destination outcomes' reason text, so any
        assertion that finds it has found a raw read detail reaching the log.
        """
        _present_source(integrated)
        integrated.destination(
            mco=StrictReadOutcome.crd_absent(f"kind_not_served {RESPONSE_CANARY}"),
            namespace=StrictReadOutcome.namespace_absent(f"namespace_not_found {RESPONSE_CANARY}"),
        )
        with caplog.at_level(logging.DEBUG):
            integrated.teardown_observability()
        assert GATE_REASON_DESTINATION_ABSENT in caplog.text
        assert RESPONSE_CANARY not in caplog.text

    def test_a_passing_gate_lets_the_delete_proceed(self, integrated):
        integrated.primary.get_custom_resource_strict = Mock(
            side_effect=[
                _strict("ITEMS", resource=_mco()),  # phase machine's guarded read
                _strict("ITEMS", resource=_mco()),  # gate's own fresh source read
                _strict("OBJECT_ABSENT"),  # absence poll
                _strict("OBJECT_ABSENT"),  # final verification
            ]
        )
        integrated.primary.get_namespace_strict = Mock(
            side_effect=[
                _strict("ITEMS", resource={"metadata": {"name": OBSERVABILITY_NAMESPACE}}),  # gate
                _strict("NAMESPACE_ABSENT"),  # drain
                _strict("NAMESPACE_ABSENT"),  # final
            ]
        )
        integrated.primary.delete_custom_resource_preconditioned = Mock(return_value=None)
        _present_destination(integrated)

        execution = integrated.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        integrated.primary.delete_custom_resource_preconditioned.assert_called_once()

    def test_a_nonterminal_record_with_an_absent_source_keeps_its_obligations(self, integrated):
        """No DELETE is pending, so the gate is not invoked at all; the drain and the
        final proof still run, no destination is read, and nothing is deleted."""
        _seed_record(integrated, phase=TeardownPhase.DELETE_STARTED)
        integrated.source(
            mco=StrictReadOutcome.object_absent("object_not_found"),
            namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
        )
        integrated.primary.delete_custom_resource_preconditioned = Mock()
        writer = Mock(wraps=integrated.run_record.record_teardown_phase)
        integrated.run_record.record_teardown_phase = writer

        with patch.object(
            integrated,
            "destination_observability_gate",
            wraps=integrated.destination_observability_gate,
        ) as gate:
            execution = integrated.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        gate.assert_not_called()
        integrated.primary.delete_custom_resource_preconditioned.assert_not_called()
        integrated.secondary_client.list_custom_resources_strict.assert_not_called()
        integrated.secondary_client.get_namespace_strict.assert_not_called()
        phases = [written.args[0].phase for written in writer.call_args_list]
        assert TeardownPhase.CR_ABSENT in phases
        assert TeardownPhase.DRAINED in phases
        assert phases[-1] is TeardownPhase.COMPLETED

    @pytest.mark.parametrize("phase", [TeardownPhase.DELETE_STARTED, TeardownPhase.CR_ABSENT])
    def test_a_nonterminal_record_with_a_retained_namespace_resumes_the_drain(self, integrated, phase):
        """The mid-drain resume: the DELETE landed, the namespace is still draining.

        The gate's source step would read this half-removed hub as ambiguous and block
        a teardown that has nothing left to authorize, so no DELETE pending means no
        gate. The record's remaining obligations run to completion instead.
        """
        _seed_record(integrated, phase=phase)
        integrated.source(
            mco=StrictReadOutcome.object_absent("object_not_found"),
            namespace=StrictReadOutcome.from_resource(
                {"metadata": {"name": OBSERVABILITY_NAMESPACE}}, resource_version="ns-9"
            ),
        )
        integrated.primary.list_pods_strict = Mock(
            return_value=StrictReadOutcome.from_items([], resource_version="pods-9")
        )
        integrated.primary.delete_custom_resource_preconditioned = Mock()
        _absent_destination(integrated)
        writer = Mock(wraps=integrated.run_record.record_teardown_phase)
        integrated.run_record.record_teardown_phase = writer

        with patch.object(
            integrated,
            "destination_observability_gate",
            wraps=integrated.destination_observability_gate,
        ) as gate:
            execution = integrated.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        gate.assert_not_called()
        integrated.primary.delete_custom_resource_preconditioned.assert_not_called()
        integrated.secondary_client.list_custom_resources_strict.assert_not_called()
        integrated.secondary_client.get_namespace_strict.assert_not_called()
        phases = [written.args[0].phase for written in writer.call_args_list]
        assert phases[-1] is TeardownPhase.COMPLETED

    def test_a_completed_record_with_a_retained_namespace_reproves_without_the_gate(
        self, integrated, state_manager, tmp_path
    ):
        """The exact C1 probe: completed record, source CR gone, namespace retained,
        a destination that would block. The reproof runs and writes nothing at all."""
        integrated.run_record.record_teardown_phase(
            TeardownRecord(
                key=MCO_KEY,
                expected_uid="uid-1",
                phase=TeardownPhase.COMPLETED,
                observed_at="2026-09-09T00:00:00+00:00",
                resource_versions={"drain_namespace": "ns-1", "drain_pods": "pods-1"},
                absence_proofs={"target_cr": AbsenceProof(proof_type="object_absent", resource_key=MCO_KEY)},
            )
        )
        state_manager.flush_state()
        state_path = tmp_path / "state.json"
        before = state_path.read_bytes()

        integrated.source(
            mco=StrictReadOutcome.object_absent("object_not_found"),
            namespace=StrictReadOutcome.from_resource(
                {"metadata": {"name": OBSERVABILITY_NAMESPACE}}, resource_version="ns-2"
            ),
        )
        integrated.primary.list_pods_strict = Mock(
            return_value=StrictReadOutcome.from_items([], resource_version="pods-2")
        )
        integrated.primary.delete_custom_resource_preconditioned = Mock()
        _absent_destination(integrated)

        with patch.object(
            integrated,
            "destination_observability_gate",
            wraps=integrated.destination_observability_gate,
        ) as gate:
            execution = integrated.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        gate.assert_not_called()
        integrated.primary.delete_custom_resource_preconditioned.assert_not_called()
        integrated.secondary_client.list_custom_resources_strict.assert_not_called()
        integrated.secondary_client.get_namespace_strict.assert_not_called()
        # `save_state` writes only when the run made the state dirty, so unchanged
        # bytes here prove the completed record's evidence was never rewritten.
        state_manager.save_state()
        assert state_path.read_bytes() == before

    def test_a_completed_record_never_reaches_the_gate(self, integrated):
        """A completed record has no DELETE to authorize, so an unreadable or absent
        destination cannot change its reproof."""
        integrated.run_record.record_teardown_phase(
            TeardownRecord(
                key=MCO_KEY,
                expected_uid="uid-1",
                phase=TeardownPhase.COMPLETED,
                observed_at="2026-09-09T00:00:00+00:00",
                resource_versions={},
                absence_proofs={
                    "target_cr": AbsenceProof(proof_type="object_absent", resource_key=MCO_KEY),
                    "drain_namespace": AbsenceProof(
                        proof_type="namespace_absent",
                        resource_key=f"v1/Namespace//{OBSERVABILITY_NAMESPACE}",
                    ),
                },
            )
        )
        integrated.source(
            mco=StrictReadOutcome.object_absent("object_not_found"),
            namespace=StrictReadOutcome.namespace_absent("namespace_not_found"),
        )
        _absent_destination(integrated)

        # Request level: the gate must not run at all, not merely reach no
        # destination read. Moving the call above the completed dispatch is
        # exactly the edit this catches.
        with patch.object(
            integrated,
            "destination_observability_gate",
            wraps=integrated.destination_observability_gate,
        ) as gate:
            execution = integrated.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        gate.assert_not_called()
        integrated.secondary_client.list_custom_resources_strict.assert_not_called()
        integrated.secondary_client.get_namespace_strict.assert_not_called()

    def test_dry_run_blocked_reports_the_blocker_and_writes_nothing(self, integrated):
        integrated.dry_run = True
        _present_source(integrated)
        _absent_destination(integrated)
        writer = Mock(wraps=integrated.run_record.record_teardown_phase)
        integrated.run_record.record_teardown_phase = writer
        integrated.primary.delete_custom_resource_preconditioned = Mock()

        execution = integrated.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        writer.assert_not_called()
        integrated.primary.delete_custom_resource_preconditioned.assert_not_called()

    def test_the_gate_reruns_against_a_record_reloaded_from_disk(self, tmp_path):
        """A resume reads its obligations from the file, and re-proves the gate live."""
        state_path = tmp_path / "resume-state.json"
        state = StateManager(str(state_path))
        RunRecord(state).record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
        )
        state.flush_state()
        state._release_run_lock()

        reloaded = StateManager(str(state_path))
        run_record = RunRecord(reloaded)
        assert run_record.teardown_record(MCO_KEY).phase is TeardownPhase.DELETE_STARTED

        resumed = _GateHarness(
            primary_client=Mock(),
            has_observability=True,
            run_record=run_record,
            secondary_client=Mock(),
        )
        _present_source(resumed)
        _absent_destination(resumed)
        resumed.primary.delete_custom_resource_preconditioned = Mock()

        execution = resumed.teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        resumed.primary.delete_custom_resource_preconditioned.assert_not_called()
        assert run_record.teardown_record(MCO_KEY).phase is TeardownPhase.DELETE_STARTED

    def test_the_gate_is_selected_by_the_teardown_spec_not_a_kind_string(self):
        """PRs D and E reuse ``_teardown_resource``; only the MCO spec is gated."""
        source = inspect.getsource(decommission_module.Decommission._teardown_resource)
        assert "OBSERVABILITY_TEARDOWN" in source
        assert '"MultiClusterObservability"' not in source


@pytest.mark.unit
class TestARecordedObligationSurvivesStaleDetection:
    """F1: a persisted MCO teardown record requests the substep on its own.

    Standalone ``--decommission`` re-detects ``has_observability`` live on every run
    (``acm_switchover.run_decommission``: does the observability namespace exist),
    and deleting the MultiClusterObservability is exactly what makes that namespace
    go away. A resume whose DELETE already landed therefore arrives with
    ``has_observability=False`` and an outstanding drain and final-proof obligation.
    The collection already ORs the persisted record into ``_acm_mco_requested``
    (``roles/decommission/tasks/delete_observability.yml``); these pin the Python
    half of that parity contract, and the last one pins that live detection still
    governs when there is no record.
    """

    @staticmethod
    def _resumed(client, run_record):
        return Decommission(primary_client=client, has_observability=False, run_record=run_record)

    def test_a_delete_started_record_reloaded_from_disk_finishes_the_obligation(self, tmp_path, mock_primary_client):
        """The reported defect: run 1 deleted the CR, run 2 sees no observability namespace."""
        state_path = tmp_path / "resume-state.json"
        state = StateManager(str(state_path))
        RunRecord(state).record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
        )
        state.flush_state()
        state._release_run_lock()

        run_record = RunRecord(StateManager(str(state_path)))
        assert run_record.teardown_record(MCO_KEY).phase is TeardownPhase.DELETE_STARTED
        client = mock_primary_client
        client.get_custom_resource_strict = Mock(return_value=_strict("OBJECT_ABSENT"))
        client.get_namespace_strict = Mock(return_value=_strict("NAMESPACE_ABSENT"))

        result = self._resumed(client, run_record).decommission(interactive=False)

        assert result.substeps["observability"] is SubstepOutcome.COMPLETED
        assert result.changed is False, "an earlier invocation's delete is not this one's change"
        assert result.not_attempted == ()
        client.delete_custom_resource_preconditioned.assert_not_called()
        record = run_record.teardown_record(MCO_KEY)
        assert record.phase is TeardownPhase.COMPLETED
        assert set(record.absence_proofs) == {"target_cr", "drain_namespace"}

    def test_a_drain_pending_record_drains_a_still_present_namespace(self, mock_primary_client, state_manager):
        """The mid-drain resume: the CR is already gone, the namespace is still there."""
        run_record = RunRecord(state_manager)
        run_record.record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DRAIN_PENDING)
        )
        client = mock_primary_client
        client.get_custom_resource_strict = Mock(return_value=_strict("OBJECT_ABSENT"))
        client.get_namespace_strict = Mock(
            return_value=_strict(
                "ITEMS",
                resource={"metadata": {"name": OBSERVABILITY_NAMESPACE}},
                resource_version="ns-9",
            )
        )
        client.list_pods_strict = Mock(return_value=_strict("ITEMS", items=[], resource_version="pods-9"))

        result = self._resumed(client, run_record).decommission(interactive=False)

        assert result.substeps["observability"] is SubstepOutcome.COMPLETED
        assert result.changed is False
        assert result.not_attempted == ()
        client.delete_custom_resource_preconditioned.assert_not_called()
        record = run_record.teardown_record(MCO_KEY)
        assert record.phase is TeardownPhase.COMPLETED
        assert record.resource_versions == {
            "drain_namespace": "ns-9",
            "drain_pods": "pods-9",
        }

    def test_a_completed_record_is_reproved_live_and_never_rewritten(self, tmp_path, mock_primary_client):
        """A completed record still requests the substep, and its reproof reads live."""
        state_path = tmp_path / "completed-state.json"
        state = StateManager(str(state_path))
        completed = TeardownRecord(
            key=MCO_KEY,
            expected_uid="uid-1",
            phase=TeardownPhase.COMPLETED,
            observed_at="2026-09-09T00:00:00+00:00",
            resource_versions={},
            absence_proofs={
                "target_cr": AbsenceProof(proof_type="object_absent", resource_key=MCO_KEY),
                "drain_namespace": AbsenceProof(
                    proof_type="namespace_absent",
                    resource_key=f"v1/Namespace//{OBSERVABILITY_NAMESPACE}",
                ),
            },
        )
        RunRecord(state).record_teardown_phase(completed)
        state.flush_state()
        state._release_run_lock()

        reloaded_state = StateManager(str(state_path))
        run_record = RunRecord(reloaded_state)
        before = state_path.read_bytes()
        client = mock_primary_client
        client.get_custom_resource_strict = Mock(return_value=_strict("OBJECT_ABSENT"))
        client.get_namespace_strict = Mock(return_value=_strict("NAMESPACE_ABSENT"))
        writer = Mock(wraps=run_record.record_teardown_phase)
        run_record.record_teardown_phase = writer

        result = self._resumed(client, run_record).decommission(interactive=False)

        assert result.substeps["observability"] is SubstepOutcome.COMPLETED
        assert result.changed is False
        # `_reprove_completed` proves the absence live rather than trusting the record.
        assert client.get_custom_resource_strict.call_count == 1
        assert client.get_namespace_strict.call_count == 1
        client.delete_custom_resource_preconditioned.assert_not_called()
        writer.assert_not_called()
        assert run_record.teardown_record(MCO_KEY) == completed
        reloaded_state.save_state()
        assert state_path.read_bytes() == before

    def test_without_a_record_live_detection_still_governs_and_reads_nothing(self, decommission_no_obs):
        """The pre-existing behaviour must survive: no record means no obligation."""
        client = decommission_no_obs.primary

        result = decommission_no_obs.decommission(interactive=False)

        assert result.substeps["observability"] is SubstepOutcome.NOT_REQUESTED
        assert result.succeeded is True
        client.get_custom_resource_strict.assert_not_called()
        client.get_namespace_strict.assert_not_called()
        client.list_pods_strict.assert_not_called()
        client.delete_custom_resource_preconditioned.assert_not_called()


@pytest.mark.unit
class TestNoDrainTeardownPhaseMachine:
    """D2: shared phase machine supports families with no drain scope."""

    NO_DRAIN_KEY = teardown_key(
        "cluster.open-cluster-management.io/v1",
        "ManagedCluster",
        None,
        "spoke-a",
    )

    @staticmethod
    def _no_drain_spec():
        from lib.constants import (
            MANAGED_CLUSTER_API_GROUP,
            MANAGED_CLUSTER_API_VERSION,
            MANAGED_CLUSTER_DELETE_INTERVAL,
            MANAGED_CLUSTER_DELETE_TIMEOUT,
            MANAGED_CLUSTER_PLURAL,
        )

        return decommission_module.TeardownSpec(
            group=MANAGED_CLUSTER_API_GROUP,
            version=MANAGED_CLUSTER_API_VERSION,
            plural=MANAGED_CLUSTER_PLURAL,
            resource_name=MANAGED_CLUSTER_PLURAL,
            kind="ManagedCluster",
            namespace=None,
            name="spoke-a",
            drain_namespace=None,
            drain_label_selector=None,
            cr_absent_timeout=MANAGED_CLUSTER_DELETE_TIMEOUT,
            cr_absent_interval=MANAGED_CLUSTER_DELETE_INTERVAL,
        )

    @staticmethod
    def _mc(uid="uid-mc-1", resource_version="3"):
        return {
            "apiVersion": "cluster.open-cluster-management.io/v1",
            "kind": "ManagedCluster",
            "metadata": {
                "name": "spoke-a",
                "uid": uid,
                "resourceVersion": resource_version,
            },
        }

    def _arrange_no_drain(self, dec, *, cr=None):
        client = dec.primary
        initial = cr if cr is not None else _strict("ITEMS", resource=self._mc())
        final = _strict("OBJECT_ABSENT") if initial.status.name == "ITEMS" else initial
        client.get_custom_resource_strict = Mock(side_effect=[initial, final, final, final])
        client.delete_custom_resource_preconditioned = Mock(return_value=None)
        client.get_namespace_strict = Mock(side_effect=AssertionError("no-drain path must not read namespaces"))
        client.list_pods_strict = Mock(side_effect=AssertionError("no-drain path must not list pods"))
        dec.run_record.record_teardown_phase = Mock(wraps=dec.run_record.record_teardown_phase)
        return client

    def test_successful_no_drain_teardown_skips_namespace_and_pod_reads(self, decommission_with_obs):
        client = self._arrange_no_drain(decommission_with_obs)
        execution = decommission_with_obs._teardown_resource(self._no_drain_spec(), record_gitops_markers=False)
        assert execution.outcome is SubstepOutcome.COMPLETED
        assert execution.changed is True
        client.get_namespace_strict.assert_not_called()
        client.list_pods_strict.assert_not_called()

    def test_no_drain_phase_order_omits_drain_phases(self, decommission_with_obs):
        self._arrange_no_drain(decommission_with_obs)
        decommission_with_obs._teardown_resource(self._no_drain_spec(), record_gitops_markers=False)
        phases = [
            call.args[0].phase.value for call in decommission_with_obs.run_record.record_teardown_phase.call_args_list
        ]
        assert phases == ["delete_started", "cr_absent", "completed"]

    def test_no_drain_completed_evidence_is_empty_revisions_and_target_cr_only(self, decommission_with_obs):
        self._arrange_no_drain(decommission_with_obs)
        decommission_with_obs._teardown_resource(self._no_drain_spec(), record_gitops_markers=False)
        completed = decommission_with_obs.run_record.record_teardown_phase.call_args_list[-1].args[0]
        assert completed.phase is TeardownPhase.COMPLETED
        assert completed.resource_versions == {}
        assert set(completed.absence_proofs) == {"target_cr"}
        assert completed.absence_proofs["target_cr"].resource_key == self.NO_DRAIN_KEY
        assert completed.absence_proofs["target_cr"].proof_type == "object_absent"

    def test_no_drain_absent_without_record_is_precondition_noop_without_namespace_read(self, decommission_with_obs):
        client = self._arrange_no_drain(decommission_with_obs, cr=_strict("OBJECT_ABSENT"))
        execution = decommission_with_obs._teardown_resource(self._no_drain_spec(), record_gitops_markers=False)
        assert execution.outcome is SubstepOutcome.PRECONDITION_NOOP
        assert execution.changed is False
        client.get_namespace_strict.assert_not_called()
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_no_drain_completed_reproof_does_not_read_namespace_or_pods(self, decommission_with_obs):
        decommission_with_obs.run_record.record_teardown_phase(
            TeardownRecord(
                key=self.NO_DRAIN_KEY,
                expected_uid="uid-mc-1",
                phase=TeardownPhase.COMPLETED,
                observed_at="2026-09-15T00:00:00+00:00",
                resource_versions={},
                absence_proofs={"target_cr": AbsenceProof(proof_type="object_absent", resource_key=self.NO_DRAIN_KEY)},
            )
        )
        client = self._arrange_no_drain(decommission_with_obs, cr=_strict("OBJECT_ABSENT"))
        execution = decommission_with_obs._teardown_resource(self._no_drain_spec(), record_gitops_markers=False)
        assert execution.outcome is SubstepOutcome.COMPLETED
        assert execution.changed is False
        client.get_namespace_strict.assert_not_called()
        client.list_pods_strict.assert_not_called()
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_no_drain_absence_wait_uses_managed_cluster_timeouts(self, decommission_with_obs):
        from lib.constants import (
            MANAGED_CLUSTER_DELETE_INTERVAL,
            MANAGED_CLUSTER_DELETE_TIMEOUT,
        )

        self._arrange_no_drain(decommission_with_obs)
        with patch.object(decommission_module, "wait_for_condition", return_value=True) as wait:
            decommission_with_obs._teardown_resource(self._no_drain_spec(), record_gitops_markers=False)
        assert wait.call_count >= 1
        kwargs = wait.call_args.kwargs
        assert kwargs["timeout"] == MANAGED_CLUSTER_DELETE_TIMEOUT
        assert kwargs["interval"] == MANAGED_CLUSTER_DELETE_INTERVAL


MC_API_VERSION = "cluster.open-cluster-management.io/v1"


def _mc_key(name: str) -> str:
    return teardown_key(MC_API_VERSION, "ManagedCluster", None, name)


def _mc(name: str, uid: str = "uid-mc-1", resource_version: str = "3") -> dict:
    return {
        "apiVersion": MC_API_VERSION,
        "kind": "ManagedCluster",
        "metadata": {"name": name, "uid": uid, "resourceVersion": resource_version},
    }


def _seed_mc_record(decommission, name: str, *, phase, expected_uid: str = "uid-mc-1"):
    key = _mc_key(name)
    kwargs: dict = {"key": key, "expected_uid": expected_uid, "phase": phase}
    if phase is TeardownPhase.COMPLETED:
        kwargs["observed_at"] = "2026-09-15T00:00:00+00:00"
        kwargs["resource_versions"] = {}
        kwargs["absence_proofs"] = {
            "target_cr": AbsenceProof(proof_type="object_absent", resource_key=key),
        }
    decommission.run_record.record_teardown_phase(TeardownRecord(**kwargs))


def _arrange_mc_family(
    dec,
    *,
    inventory_items=None,
    inventory_status="ITEMS",
    hive_items=None,
    hive_status="ITEMS",
):
    """Arrange strict inventory + Hive + per-name GET/DELETE seams for family tests."""
    client = dec.primary
    items = inventory_items if inventory_items is not None else []

    def _list_outcome(status, listed, version):
        if status in ("ERROR", "CRD_ABSENT"):
            return _strict(status)
        return _strict(status, items=listed, resource_version=version)

    client.list_managed_clusters_strict = Mock(return_value=_list_outcome(inventory_status, items, "mc-inv"))
    client.list_custom_resources_strict = Mock(return_value=_list_outcome(hive_status, hive_items or [], "cd-inv"))
    client.delete_custom_resource_preconditioned = Mock(return_value=None)
    client.get_namespace_strict = Mock(side_effect=AssertionError("MC family must not drain"))
    client.list_pods_strict = Mock(side_effect=AssertionError("MC family must not drain"))
    dec.run_record.record_teardown_phase = Mock(wraps=dec.run_record.record_teardown_phase)

    by_name = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        name = metadata.get("name")
        if isinstance(name, str) and name:
            by_name[name] = item

    def _named_get(group, version, plural, name, namespace=None):
        if plural != "managedclusters":
            return _strict("OBJECT_ABSENT")
        if inventory_status == "CRD_ABSENT":
            return _strict("CRD_ABSENT")
        if inventory_status == "ERROR":
            return _strict("ERROR")
        resource = by_name.get(name)
        if resource is None:
            return _strict("OBJECT_ABSENT")
        # After a successful delete mock, subsequent GETs see absence. The delete
        # mock clears the name from by_name when accepted.
        return _strict("ITEMS", resource=resource)

    def _delete(group, version, plural, name, uid, namespace=None, timeout_seconds=None):
        by_name.pop(name, None)
        return None

    client.get_custom_resource_strict = Mock(side_effect=_named_get)
    client.delete_custom_resource_preconditioned = Mock(side_effect=_delete)
    return client


@pytest.mark.unit
class TestManagedClusterTeardownFamily:
    """D3: public teardown_managed_clusters family orchestrator."""

    def test_public_method_returns_substep_execution(self, decommission_with_obs):
        _arrange_mc_family(decommission_with_obs)
        execution = decommission_with_obs.teardown_managed_clusters()
        assert isinstance(execution, SubstepExecution)

    def test_dispatch_wires_managed_clusters_to_public_method(self):
        source = inspect.getsource(Decommission._run_substep)
        assert "teardown_managed_clusters" in source
        assert "_delete_managed_clusters" not in source

    def test_strict_empty_inventory_is_precondition_noop(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()
        client.delete_custom_resource.assert_not_called()

    def test_strict_inventory_error_is_family_failed_never_empty(self, decommission_with_obs, caplog):
        client = _arrange_mc_family(decommission_with_obs, inventory_status="ERROR")
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert "Cannot verify ManagedCluster inventory" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_crd_absent_without_records_is_unverifiable_failure(self, decommission_with_obs, caplog):
        """Binding ruling 3: positive CRD_ABSENT with no records is not empty-list blindness."""
        client = _arrange_mc_family(decommission_with_obs, inventory_status="CRD_ABSENT")
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert "Cannot verify ManagedCluster inventory" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_local_cluster_is_skipped_with_no_named_get_delete_or_record(self, decommission_with_obs, caplog):
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc(LOCAL_CLUSTER_NAME, uid="uid-local")],
        )
        with caplog.at_level(logging.INFO, logger="acm_switchover"):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        client.get_custom_resource_strict.assert_not_called()
        client.delete_custom_resource_preconditioned.assert_not_called()
        client.list_custom_resources_strict.assert_not_called()
        assert decommission_with_obs.run_record.all_teardown_records() == {}
        assert "local-cluster" not in [
            c.args[0].key for c in decommission_with_obs.run_record.record_teardown_phase.call_args_list
        ]

    def test_successful_teardown_uses_uid_preconditioned_delete(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        client.delete_custom_resource_preconditioned.assert_called_once()
        args = client.delete_custom_resource_preconditioned.call_args
        assert "spoke-a" in args.args or args.kwargs.get("name") == "spoke-a"
        assert (
            args.kwargs.get("uid") if "uid" in args.kwargs else args.args[4] if len(args.args) > 4 else None
        ) == "uid-mc-1"
        client.delete_custom_resource.assert_not_called()
        completed = decommission_with_obs.run_record.teardown_record(_mc_key("spoke-a"))
        assert completed.phase is TeardownPhase.COMPLETED
        assert completed.resource_versions == {}
        assert set(completed.absence_proofs) == {"target_cr"}

    def test_uid_is_forced_durable_before_delete(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        order = []

        real_record = decommission_with_obs.run_record.record_teardown_phase

        def track_record(record):
            order.append(("record", record.phase.value))
            return real_record(record)

        def track_delete(*a, **k):
            order.append(("delete", k.get("name") or (a[3] if len(a) > 3 else None)))
            return None

        decommission_with_obs.run_record.record_teardown_phase = Mock(side_effect=track_record)
        # Re-install named get that clears on delete via side effects below
        by_name = {"spoke-a": _mc("spoke-a")}

        def named_get(group, version, plural, name, namespace=None):
            resource = by_name.get(name)
            return _strict("ITEMS", resource=resource) if resource else _strict("OBJECT_ABSENT")

        def delete(group, version, plural, name, uid, namespace=None, timeout_seconds=None):
            track_delete(group, version, plural, name, uid=uid)
            by_name.pop(name, None)

        client.get_custom_resource_strict = Mock(side_effect=named_get)
        client.delete_custom_resource_preconditioned = Mock(side_effect=delete)

        decommission_with_obs.teardown_managed_clusters()
        assert order[0] == ("record", "delete_started")
        assert order[1][0] == "delete"

    def test_hive_crd_absent_blocks_before_mutation(self, decommission_with_obs, caplog):
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("spoke-a")],
            hive_status="CRD_ABSENT",
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_hive_list_error_blocks_before_mutation(self, decommission_with_obs, caplog):
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("spoke-a")],
            hive_status="ERROR",
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert "Unable to verify ClusterDeployment preserveOnDelete safety" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_preserve_on_delete_false_blocks_with_existing_message(self, decommission_with_obs, caplog):
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("cluster1")],
            hive_items=[
                {
                    "metadata": {"name": "cluster1", "namespace": "cluster1"},
                    "spec": {"preserveOnDelete": False},
                }
            ],
        )
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        assert "preserveOnDelete=true" in caplog.text
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_mixed_aggregation_fail_then_success_reports_changed(self, decommission_with_obs, caplog, monkeypatch):
        """First cluster fails after accepted DELETE; second completes. Survivors logged once."""
        items = [_mc("alpha", uid="uid-a"), _mc("beta", uid="uid-b")]
        client = _arrange_mc_family(decommission_with_obs, inventory_items=items)

        original = decommission_with_obs._teardown_resource
        calls = []

        def wrap(spec, *, record_gitops_markers):
            calls.append(spec.name)
            if spec.name == "alpha":
                # Accept DELETE then fail proof by forcing absence wait failure.
                result = original(spec, record_gitops_markers=record_gitops_markers)
                # Force FAILED with changed from a synthetic accepted delete path:
                return SubstepExecution(SubstepOutcome.FAILED, changed=True)
            return original(spec, record_gitops_markers=record_gitops_markers)

        monkeypatch.setattr(decommission_with_obs, "_teardown_resource", wrap)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()

        assert calls == [
            "alpha",
            "beta",
        ], "stable sorted order; later clusters continue"
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert "alpha" in caplog.text
        assert "survivors" in caplog.text.lower() or "incomplete" in caplog.text.lower()

    def test_mixed_aggregation_success_then_fail_reports_changed(self, decommission_with_obs, caplog, monkeypatch):
        items = [_mc("alpha", uid="uid-a"), _mc("beta", uid="uid-b")]
        _arrange_mc_family(decommission_with_obs, inventory_items=items)
        original = decommission_with_obs._teardown_resource
        calls = []

        def wrap(spec, *, record_gitops_markers):
            calls.append(spec.name)
            if spec.name == "beta":
                return SubstepExecution(SubstepOutcome.FAILED, changed=False)
            return original(spec, record_gitops_markers=record_gitops_markers)

        monkeypatch.setattr(decommission_with_obs, "_teardown_resource", wrap)

        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()

        assert calls == ["alpha", "beta"]
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert "beta" in caplog.text

    def test_resume_record_only_when_live_inventory_empty(self, decommission_with_obs):
        _seed_mc_record(decommission_with_obs, "spoke-a", phase=TeardownPhase.DELETE_STARTED)
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[])
        # Named GET sees absence → phase machine proves and completes without DELETE.
        client.get_custom_resource_strict = Mock(return_value=_strict("OBJECT_ABSENT"))

        execution = decommission_with_obs.teardown_managed_clusters()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()
        assert decommission_with_obs.run_record.teardown_record(_mc_key("spoke-a")).phase is TeardownPhase.COMPLETED

    def test_dry_run_present_inventory_does_not_mutate(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        decommission_with_obs.dry_run = True
        before = dict(decommission_with_obs.run_record.all_teardown_records())

        execution = decommission_with_obs.teardown_managed_clusters()

        assert execution.changed is False
        assert execution.outcome in (
            SubstepOutcome.COMPLETED,
            SubstepOutcome.PRECONDITION_NOOP,
        )
        client.delete_custom_resource_preconditioned.assert_not_called()
        client.delete_custom_resource.assert_not_called()
        assert decommission_with_obs.run_record.all_teardown_records() == before

    def test_dry_run_empty_inventory_is_noop(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[])
        decommission_with_obs.dry_run = True
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_dry_run_unverifiable_inventory_fails(self, decommission_with_obs, caplog):
        client = _arrange_mc_family(decommission_with_obs, inventory_status="ERROR")
        decommission_with_obs.dry_run = True
        with caplog.at_level(logging.ERROR):
            execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_per_target_precondition_noop_counts_as_satisfied(self, decommission_with_obs, monkeypatch):
        _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])

        def always_noop(spec, *, record_gitops_markers):
            return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)

        monkeypatch.setattr(decommission_with_obs, "_teardown_resource", always_noop)
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)

    def test_validation_error_propagates(self, decommission_with_obs):
        from lib.validation import ValidationError

        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        client.delete_custom_resource_preconditioned = Mock(side_effect=ValidationError("empty uid"))
        with pytest.raises(ValidationError):
            decommission_with_obs.teardown_managed_clusters()


@pytest.mark.unit
class TestManagedClusterTeardownIvR40301:
    """IV-R403-01 cases for teardown_managed_clusters. Case 3 (drain) is inapplicable."""

    def test_case1_no_mutation_then_proof_failure_changed_false(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        client.delete_custom_resource_preconditioned = Mock(side_effect=SwitchoverError("denied"))
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)

    def test_case2_accepted_delete_then_cr_absence_failure_changed_true(self, decommission_with_obs, monkeypatch):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        client.get_custom_resource_strict = Mock(
            side_effect=[
                _strict("ITEMS", resource=_mc("spoke-a")),
                _strict("ERROR"),
            ]
        )
        client.delete_custom_resource_preconditioned = Mock(return_value=None)
        monkeypatch.setattr(decommission_module, "MANAGED_CLUSTER_DELETE_TIMEOUT", 0)

        execution = decommission_with_obs.teardown_managed_clusters()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        client.delete_custom_resource_preconditioned.assert_called_once()

    def test_case3_drain_failure_is_inapplicable_no_drain_reads(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        decommission_with_obs.teardown_managed_clusters()
        client.get_namespace_strict.assert_not_called()
        client.list_pods_strict.assert_not_called()

    def test_case4_accepted_delete_then_final_verification_failure_changed_true(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        client.get_custom_resource_strict = Mock(
            side_effect=[
                _strict("ITEMS", resource=_mc("spoke-a")),
                _strict("OBJECT_ABSENT"),
                _strict("ERROR"),
            ]
        )
        client.delete_custom_resource_preconditioned = Mock(return_value=None)

        execution = decommission_with_obs.teardown_managed_clusters()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        record = decommission_with_obs.run_record.teardown_record(_mc_key("spoke-a"))
        assert record is None or record.phase is not TeardownPhase.COMPLETED

    def test_case5_resumed_record_final_proof_failure_changed_false(self, decommission_with_obs):
        _seed_mc_record(decommission_with_obs, "spoke-a", phase=TeardownPhase.CR_ABSENT)
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[])
        client.get_custom_resource_strict = Mock(return_value=_strict("ERROR"))

        execution = decommission_with_obs.teardown_managed_clusters()

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_case6_partial_batch_accepted_delete_then_later_failure_changed_true(
        self, decommission_with_obs, monkeypatch
    ):
        items = [_mc("alpha", uid="uid-a"), _mc("beta", uid="uid-b")]
        _arrange_mc_family(decommission_with_obs, inventory_items=items)
        original = decommission_with_obs._teardown_resource

        def wrap(spec, *, record_gitops_markers):
            if spec.name == "beta":
                return SubstepExecution(SubstepOutcome.FAILED, changed=False)
            return original(spec, record_gitops_markers=record_gitops_markers)

        monkeypatch.setattr(decommission_with_obs, "_teardown_resource", wrap)
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)

    def test_case7_expected_failure_does_not_escape_as_exception(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        client.delete_custom_resource_preconditioned = Mock(side_effect=SwitchoverError("boom"))
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED


@pytest.mark.unit
class TestManagedClusterPublicDryRunSafety:
    """Public dry-run must evaluate the same MC safety predicates as live execution."""

    def _dry(self, decommission_no_obs):
        decommission_no_obs.dry_run = True
        decommission_no_obs.has_observability = False
        return decommission_no_obs

    def test_public_dry_run_blocks_unsafe_hive_preserve_on_delete(self, decommission_no_obs):
        dec = self._dry(decommission_no_obs)
        client = _arrange_mc_family(
            dec,
            inventory_items=[_mc("spoke-a")],
            hive_items=[
                {
                    "metadata": {"namespace": "hive", "name": "spoke-a"},
                    "spec": {"preserveOnDelete": False},
                }
            ],
        )
        before = dict(dec.run_record.all_teardown_records())

        with pytest.raises(SwitchoverError, match="preserveOnDelete"):
            dec.decommission(interactive=False)

        client.delete_custom_resource_preconditioned.assert_not_called()
        client.delete_custom_resource.assert_not_called()
        assert dec.run_record.all_teardown_records() == before

    def test_public_dry_run_record_only_obligation_is_not_false_noop(self, decommission_no_obs):
        dec = self._dry(decommission_no_obs)
        _seed_mc_record(dec, "spoke-a", phase=TeardownPhase.DELETE_STARTED, expected_uid="uid-mc-1")
        client = _arrange_mc_family(dec, inventory_items=[])
        # Live inventory empty, but named GET still sees the recorded identity.
        client.get_custom_resource_strict = Mock(return_value=_strict("ITEMS", resource=_mc("spoke-a", uid="uid-mc-1")))
        before = dict(dec.run_record.all_teardown_records())

        result = dec.decommission(interactive=False)

        assert result.changed is False
        assert result.would_change is True
        client.get_custom_resource_strict.assert_called()
        client.delete_custom_resource_preconditioned.assert_not_called()
        assert dec.run_record.all_teardown_records() == before

    def test_public_dry_run_fails_closed_on_replacement_uid(self, decommission_no_obs):
        dec = self._dry(decommission_no_obs)
        _seed_mc_record(dec, "spoke-a", phase=TeardownPhase.DELETE_STARTED, expected_uid="uid-old")
        client = _arrange_mc_family(dec, inventory_items=[_mc("spoke-a", uid="uid-new")])
        before = dict(dec.run_record.all_teardown_records())

        with pytest.raises(SwitchoverError, match="not the object recorded"):
            dec.decommission(interactive=False)

        client.delete_custom_resource_preconditioned.assert_not_called()
        assert dec.run_record.all_teardown_records() == before

    def test_public_dry_run_clean_empty_inventory_is_noop(self, decommission_no_obs):
        dec = self._dry(decommission_no_obs)
        client = _arrange_mc_family(dec, inventory_items=[])
        before = dict(dec.run_record.all_teardown_records())

        result = dec.decommission(interactive=False)

        assert result.changed is False
        assert result.would_change is False
        client.delete_custom_resource_preconditioned.assert_not_called()
        assert dec.run_record.all_teardown_records() == before

    def test_public_dry_run_unverifiable_mc_inventory_fails_closed(self, decommission_no_obs):
        dec = self._dry(decommission_no_obs)
        client = _arrange_mc_family(dec, inventory_status="ERROR")
        before = dict(dec.run_record.all_teardown_records())

        with pytest.raises(SwitchoverError, match="ManagedCluster inventory"):
            dec.decommission(interactive=False)

        client.delete_custom_resource_preconditioned.assert_not_called()
        assert dec.run_record.all_teardown_records() == before

    def test_public_dry_run_unverifiable_hive_inventory_fails_closed(self, decommission_no_obs):
        dec = self._dry(decommission_no_obs)
        client = _arrange_mc_family(dec, inventory_items=[_mc("spoke-a")], hive_status="ERROR")
        before = dict(dec.run_record.all_teardown_records())

        with pytest.raises(SwitchoverError, match="ClusterDeployment"):
            dec.decommission(interactive=False)

        client.delete_custom_resource_preconditioned.assert_not_called()
        assert dec.run_record.all_teardown_records() == before

    def test_public_dry_run_fails_closed_when_present_target_uid_missing(self, decommission_no_obs):
        """Present targets without a usable UID cannot safely predict would_change."""
        dec = self._dry(decommission_no_obs)
        client = _arrange_mc_family(
            dec,
            inventory_items=[{"metadata": {"name": "spoke-a", "uid": ""}}],
        )
        # Named GET must also present the empty UID (list path already validated the name).
        client.get_custom_resource_strict = Mock(
            return_value=_strict("ITEMS", resource={"metadata": {"name": "spoke-a", "uid": ""}})
        )
        before = dict(dec.run_record.all_teardown_records())

        with pytest.raises(SwitchoverError, match="Cannot establish the identity"):
            dec.decommission(interactive=False)

        client.delete_custom_resource_preconditioned.assert_not_called()
        assert dec.run_record.all_teardown_records() == before


@pytest.mark.unit
class TestManagedClusterMalformedInventory:
    """Strict ManagedCluster inventory must fail closed on malformed items."""

    def test_malformed_only_empty_mapping_fails_not_noop(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[{}])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_missing_metadata_fails(self, decommission_with_obs):
        client = _arrange_mc_family(
            decommission_with_obs, inventory_items=[{"apiVersion": "v1", "kind": "ManagedCluster"}]
        )
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_non_mapping_metadata_fails(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[{"metadata": "spoke-a"}])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_empty_name_fails(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[{"metadata": {"name": ""}}])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_null_name_fails(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[{"metadata": {"name": None}}])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_non_string_name_fails(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[{"metadata": {"name": 123}}])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_mixed_valid_and_malformed_fails_entire_inventory(self, decommission_with_obs):
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("spoke-a"), {}],
        )
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.FAILED
        client.delete_custom_resource_preconditioned.assert_not_called()

    def test_valid_inventory_unchanged(self, decommission_with_obs):
        client = _arrange_mc_family(decommission_with_obs, inventory_items=[_mc("spoke-a")])
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.COMPLETED
        assert execution.changed is True
        client.delete_custom_resource_preconditioned.assert_called()

    def test_valid_local_cluster_excluded(self, decommission_with_obs):
        client = _arrange_mc_family(
            decommission_with_obs,
            inventory_items=[_mc("local-cluster"), _mc("spoke-a")],
        )
        execution = decommission_with_obs.teardown_managed_clusters()
        assert execution.outcome is SubstepOutcome.COMPLETED
        deleted = [
            c.kwargs.get("name") or (c.args[3] if len(c.args) > 3 else None)
            for c in client.delete_custom_resource_preconditioned.call_args_list
        ]
        assert "local-cluster" not in deleted
        assert "spoke-a" in deleted or any(
            "spoke-a" in str(c) for c in client.delete_custom_resource_preconditioned.call_args_list
        )


# --------------------------------------------------------------------------- E4: MultiClusterHub

MCH_GROUP = "operator.open-cluster-management.io"
MCH_API_VERSION = f"{MCH_GROUP}/v1"
MCH_NAME = "multiclusterhub"
MCH_UID = "uid-mch"
CSV_GROUP = "operators.coreos.com"
CSV_NAME = "advanced-cluster-management.v2.13.0"
CSV_UID = "uid-csv"
MCH_OWNED_CRD_NAME = "multiclusterhubs.operator.open-cluster-management.io"
OPERATOR_DEPLOYMENT_NAME = "multiclusterhub-operator"
OPERATOR_DEPLOYMENT_UID = "uid-operator-deployment"
OPERATOR_RS_NAME = "multiclusterhub-operator-7d9f"
OPERATOR_RS_UID = "uid-rs-7d9f"
DISCOVERY_METHOD = "olm_csv_owned_mch_crd_install_deployment_v1"
ACM_NAMESPACE_KEY = f"v1/Namespace//{ACM_NAMESPACE}"

# The three reads identity capture performs, in order, as (verb, group, resource, namespace, name).
CAPTURE_REQUESTS = (
    ("LIST", CSV_GROUP, "clusterserviceversions", ACM_NAMESPACE, None),
    ("GET", CSV_GROUP, "clusterserviceversions", ACM_NAMESPACE, CSV_NAME),
    ("GET", "apps", "deployments", ACM_NAMESPACE, OPERATOR_DEPLOYMENT_NAME),
)
MCH_DELETE = ("DELETE", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, MCH_NAME)


def _mch_key(name=MCH_NAME):
    return teardown_key(MCH_API_VERSION, "MultiClusterHub", ACM_NAMESPACE, name)


def _mch(name=MCH_NAME, uid=MCH_UID):
    return {
        "apiVersion": MCH_API_VERSION,
        "kind": "MultiClusterHub",
        "metadata": {
            "name": name,
            "namespace": ACM_NAMESPACE,
            "uid": uid,
            "resourceVersion": "mch-pre-delete",
        },
    }


def _mch_present(name=MCH_NAME, uid=MCH_UID):
    return _strict("ITEMS", resource=_mch(name, uid), resource_version="mch-pre-delete")


def _mch_inventory(*names):
    return _strict("ITEMS", items=[_mch(name) for name in names], resource_version="mch-list-1")


def _csv(*, deployment=OPERATOR_DEPLOYMENT_NAME):
    return {
        "metadata": {"name": CSV_NAME, "namespace": ACM_NAMESPACE, "uid": CSV_UID},
        "spec": {
            "customresourcedefinitions": {"owned": [{"name": MCH_OWNED_CRD_NAME}]},
            "install": {"strategy": "deployment", "spec": {"deployments": [{"name": deployment}]}},
        },
        "status": {"phase": "Succeeded"},
    }


def _deployment(uid=OPERATOR_DEPLOYMENT_UID, revision="deploy-1"):
    return _strict(
        "ITEMS",
        resource={"metadata": {"name": OPERATOR_DEPLOYMENT_NAME, "namespace": ACM_NAMESPACE, "uid": uid}},
        resource_version=revision,
    )


def _controller(kind, name, uid, api_version="apps/v1"):
    return {"api_version": api_version, "kind": kind, "name": name, "uid": uid, "controller": True}


def _acm_pod(name, *, controller=None):
    """A Pod in the CoreV1 ``to_dict()`` shape ``list_pods_strict`` yields."""
    return {
        "metadata": {
            "name": name,
            "namespace": ACM_NAMESPACE,
            "owner_references": [] if controller is None else [controller],
        }
    }


def _operator_pod(name="multiclusterhub-operator-7d9f-abcde", rs_name=OPERATOR_RS_NAME, rs_uid=OPERATOR_RS_UID):
    return _acm_pod(name, controller=_controller("ReplicaSet", rs_name, rs_uid))


def _replicaset(name=OPERATOR_RS_NAME, uid=OPERATOR_RS_UID, *, deployment_uid=OPERATOR_DEPLOYMENT_UID):
    return _strict(
        "ITEMS",
        resource={
            "metadata": {
                "name": name,
                "namespace": ACM_NAMESPACE,
                "uid": uid,
                "owner_references": [_controller("Deployment", OPERATOR_DEPLOYMENT_NAME, deployment_uid)],
            }
        },
        resource_version=f"{name}-rv",
    )


def _namespace_present(revision="ns-1"):
    return _strict("ITEMS", resource={"metadata": {"name": ACM_NAMESPACE}}, resource_version=revision)


def _pods(*pods, revision="pods-1"):
    return _strict("ITEMS", items=list(pods), resource_version=revision)


def _captured_identity(name=MCH_NAME, expected_uid=MCH_UID):
    return {
        "namespace": ACM_NAMESPACE,
        "name": OPERATOR_DEPLOYMENT_NAME,
        "uid": OPERATOR_DEPLOYMENT_UID,
        "discovery_method": DISCOVERY_METHOD,
        "captured_at": "2026-09-16T00:00:00+00:00",
        "csv": {"namespace": ACM_NAMESPACE, "name": CSV_NAME, "uid": CSV_UID, "owned_crd": MCH_OWNED_CRD_NAME},
        "mch_teardown_key": _mch_key(name),
        "mch_expected_uid": expected_uid,
    }


def _unavailable_identity(name=MCH_NAME, expected_uid=MCH_UID):
    return {
        "reason": "csv_absent",
        "discovery_method": DISCOVERY_METHOD,
        "captured_at": "2026-09-16T00:00:00+00:00",
        "evidence_summary": "No ClusterServiceVersion owning the MultiClusterHub CRD was found.",
        "mch_teardown_key": _mch_key(name),
        "mch_expected_uid": expected_uid,
    }


def _completed_evidence(key, mode):
    target = {"target_cr": AbsenceProof(proof_type="object_absent", resource_key=key)}
    if mode == "namespace_absent":
        return {
            "resource_versions": {},
            "absence_proofs": {
                **target,
                "drain_namespace": AbsenceProof(proof_type="namespace_absent", resource_key=ACM_NAMESPACE_KEY),
            },
        }
    return {
        "resource_versions": {
            "drain_namespace": "ns-old",
            "drain_pods": "pods-old",
            "operator_deployment": "deploy-old",
        },
        "absence_proofs": target,
    }


def _mch_record(phase, *, identity="captured", name=MCH_NAME, expected_uid=MCH_UID, completed_mode="namespace_absent"):
    """One valid MCH teardown record seeded through the real validator."""
    key = _mch_key(name)
    fields = {}
    if identity == "captured":
        fields["operator_deployment"] = _captured_identity(name, expected_uid)
    else:
        fields["operator_identity_unavailable"] = _unavailable_identity(name, expected_uid)
    if phase is TeardownPhase.COMPLETED:
        fields.update(observed_at="2026-09-16T00:00:00+00:00", **_completed_evidence(key, completed_mode))
    return TeardownRecord(key=key, expected_uid=expected_uid, phase=phase, **fields)


class _Script:
    """Scripted outcomes: each call consumes the head, and the last one repeats."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def next(self):
        return self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]


class _AcmHub:
    """Request-recording fake of the source hub for the MultiClusterHub family.

    Every read and the guarded DELETE append ``(verb, group, resource, namespace, name)``
    to ``requests``; ``_mch_decommission`` appends each durable write as
    ``("WRITE", phase)`` to the same log, so ordering is asserted over one sequence.
    The defaults describe the ordinary fresh path: one live MultiClusterHub that is
    gone after its DELETE, one Succeeded CSV owning the MCH CRD, a live operator
    Deployment, and a positively absent ACM namespace.
    """

    def __init__(
        self,
        *,
        mch_list=None,
        mch_gets=None,
        csv_list=None,
        csv_get=None,
        deployments=None,
        replicasets=None,
        namespaces=None,
        pods=None,
        delete=None,
    ):
        self.requests = []
        self.writes = []
        self.delete_uids = []
        self._mch_list = _Script(mch_list or [_mch_inventory(MCH_NAME)])
        self._mch_gets = _Script(mch_gets or [_mch_present(), _strict("OBJECT_ABSENT")])
        self._csv_list = _Script(csv_list or [_strict("ITEMS", items=[_csv()], resource_version="csv-list-1")])
        self._csv_get = _Script(csv_get or [_strict("ITEMS", resource=_csv(), resource_version="csv-1")])
        self._deployments = _Script(deployments or [_deployment()])
        self._replicasets = {name: _Script(outcomes) for name, outcomes in (replicasets or {}).items()}
        self._namespaces = _Script(namespaces or [_strict("NAMESPACE_ABSENT")])
        self._pods = _Script(pods or [_pods()])
        self._delete = delete

    def list_custom_resources_strict(self, group, version, plural, namespace=None, label_selector=None):
        self.requests.append(("LIST", group, plural, namespace, None))
        if plural == "multiclusterhubs":
            return self._mch_list.next()
        if plural == "clusterserviceversions":
            return self._csv_list.next()
        raise AssertionError(f"unexpected strict list of {plural}")

    def get_custom_resource_strict(self, group, version, plural, name, namespace=None):
        self.requests.append(("GET", group, plural, namespace, name))
        if plural == "multiclusterhubs":
            return self._mch_gets.next()
        if plural == "clusterserviceversions":
            return self._csv_get.next()
        raise AssertionError(f"unexpected strict get of {plural}")

    def get_deployment_strict(self, name, namespace):
        self.requests.append(("GET", "apps", "deployments", namespace, name))
        return self._deployments.next()

    def get_replicaset_strict(self, name, namespace):
        self.requests.append(("GET", "apps", "replicasets", namespace, name))
        script = self._replicasets.get(name)
        return script.next() if script is not None else _strict("OBJECT_ABSENT")

    def get_namespace_strict(self, name):
        self.requests.append(("GET", "", "namespaces", None, name))
        return self._namespaces.next()

    def list_pods_strict(self, namespace, label_selector=None):
        self.requests.append(("LIST", "", "pods", namespace, label_selector))
        return self._pods.next()

    def delete_custom_resource_preconditioned(
        self, group, version, plural, name, uid, namespace=None, timeout_seconds=None
    ):
        self.requests.append(("DELETE", group, plural, namespace, name))
        self.delete_uids.append(uid)
        if self._delete is not None:
            raise self._delete


def _mch_decommission(state_manager, hub, *, records=(), dry_run=False, has_observability=False):
    """A Decommission over ``hub`` whose durable writes are logged into ``hub.requests``.

    ``records`` are seeded through the real writer first, so they are valid resumable
    states and do not appear in the log. The logged writer still delegates to the real
    ``RunRecord.record_teardown_phase``, so ``lib.teardown_record.validate`` runs on
    every write under test.
    """
    run_record = RunRecord(state_manager)
    for record in records:
        run_record.record_teardown_phase(record)
    real_writer = run_record.record_teardown_phase

    def logged_writer(record):
        hub.requests.append(("WRITE", record.phase.value))
        hub.writes.append(record)
        return real_writer(record)

    run_record.record_teardown_phase = logged_writer
    return Decommission(
        primary_client=hub,
        has_observability=has_observability,
        run_record=run_record,
        dry_run=dry_run,
    )


def _phases(hub):
    return [record.phase.value for record in hub.writes]


def _requests_of(hub, verb, resource):
    return [request for request in hub.requests if request[0] == verb and request[2] == resource]


def _completed_write(hub):
    completed = [record for record in hub.writes if record.phase is TeardownPhase.COMPLETED]
    assert len(completed) == 1, f"expected exactly one completed write, got phases {_phases(hub)}"
    return completed[0]


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("lib.waiter.time.sleep", lambda _: None)


@pytest.fixture
def one_mch_poll(monkeypatch):
    """Every MCH wait performs exactly one post-deadline poll, so a blocker fails fast."""
    monkeypatch.setattr(decommission_module, "DECOMMISSION_POD_TIMEOUT", 0)


@pytest.mark.unit
class TestMultiClusterHubMandatoryContracts:
    """The six E4 assertion-level contracts, driven through the substep dispatch."""

    def test_a_prefixed_pod_without_an_owner_blocks_the_drain(self, state_manager, one_mch_poll):
        """July criterion 11: a name prefix is never an exclusion rule."""
        spoof = _acm_pod("multiclusterhub-operator-spoofed-x1")
        hub = _AcmHub(namespaces=[_namespace_present()], pods=[_pods(spoof)])
        dec = _mch_decommission(state_manager, hub)

        execution = dec._run_substep("multiclusterhub")

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert "completed" not in _phases(hub)
        assert _phases(hub)[-1] == "drain_pending"

    def test_identity_capture_is_durable_before_the_guarded_delete(self, state_manager):
        hub = _AcmHub()
        dec = _mch_decommission(state_manager, hub)

        dec._run_substep("multiclusterhub")

        assert MCH_DELETE in hub.requests, "the guarded delete must be issued"
        uid_proof = ("GET", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, MCH_NAME)
        positions = [hub.requests.index(uid_proof)]
        positions += [hub.requests.index(request) for request in CAPTURE_REQUESTS]
        positions += [hub.requests.index(("WRITE", "delete_started")), hub.requests.index(MCH_DELETE)]
        assert positions == sorted(positions), hub.requests
        assert hub.delete_uids == [MCH_UID]
        first = hub.writes[0]
        assert first.phase is TeardownPhase.DELETE_STARTED
        captured_at = first.operator_deployment["captured_at"]
        assert first.operator_deployment == {**_captured_identity(), "captured_at": captured_at}

    def test_zero_pods_with_an_inconsistent_recorded_deployment_never_drains(self, state_manager):
        """The pre-E4 advisor finding: zero Pods yield an empty blocking tuple even when the
        recorded operator Deployment is gone, so identity status must be checked first."""
        hub = _AcmHub(
            mch_list=[_mch_inventory()],
            mch_gets=[_strict("OBJECT_ABSENT")],
            deployments=[_strict("OBJECT_ABSENT")],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.DRAIN_PENDING)])

        execution = dec._run_substep("multiclusterhub")

        assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert "drained" not in _phases(hub) and "completed" not in _phases(hub)
        assert dec.run_record.teardown_record(_mch_key()).phase is TeardownPhase.RECOVERY_REQUIRED

    def test_captured_identity_completion_records_exactly_three_revisions(self, state_manager):
        hub = _AcmHub(
            deployments=[_deployment(revision="deploy-capture"), _deployment(revision="deploy-final")],
            namespaces=[_namespace_present("ns-final")],
            pods=[_pods(revision="pods-final")],
        )
        dec = _mch_decommission(state_manager, hub)

        execution = dec._run_substep("multiclusterhub")

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        completed = _completed_write(hub)
        assert completed.resource_versions == {
            "drain_namespace": "ns-final",
            "drain_pods": "pods-final",
            "operator_deployment": "deploy-final",
        }
        assert set(completed.absence_proofs) == {"target_cr"}

    def test_namespace_absent_completion_records_both_proofs_and_no_revisions(self, state_manager):
        hub = _AcmHub(namespaces=[_strict("NAMESPACE_ABSENT")])
        dec = _mch_decommission(state_manager, hub)

        execution = dec._run_substep("multiclusterhub")

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        completed = _completed_write(hub)
        assert completed.resource_versions == {}
        assert completed.absence_proofs == {
            "target_cr": AbsenceProof(proof_type="object_absent", resource_key=_mch_key()),
            "drain_namespace": AbsenceProof(proof_type="namespace_absent", resource_key=ACM_NAMESPACE_KEY),
        }
        assert _requests_of(hub, "LIST", "pods") == [], "namespace absence entails the pod-empty predicate"
        assert len(_requests_of(hub, "GET", "deployments")) == 1, "only the capture reads the Deployment"

    def test_only_final_pass_revisions_are_persisted(self, state_manager, no_sleep):
        blocker = _acm_pod("app-pod-still-terminating")
        hub = _AcmHub(
            deployments=[
                _deployment(revision="deploy-capture"),
                _deployment(revision="deploy-drain-1"),
                _deployment(revision="deploy-drain-2"),
                _deployment(revision="deploy-final"),
            ],
            namespaces=[
                _namespace_present("ns-drain-1"),
                _namespace_present("ns-drain-2"),
                _namespace_present("ns-final"),
            ],
            pods=[
                _pods(blocker, revision="pods-drain-1"),
                _pods(revision="pods-drain-2"),
                _pods(revision="pods-final"),
            ],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec._run_substep("multiclusterhub").outcome is SubstepOutcome.COMPLETED

        assert _completed_write(hub).resource_versions == {
            "drain_namespace": "ns-final",
            "drain_pods": "pods-final",
            "operator_deployment": "deploy-final",
        }
        stored = json.dumps(dec.run_record.all_teardown_records(), default=str)
        for earlier in ("deploy-capture", "deploy-drain", "ns-drain", "pods-drain", "mch-pre-delete"):
            assert earlier not in stored


def _mch_family_requests(hub, resource):
    return [request for request in hub.requests if request[0] != "WRITE" and request[2] == resource]


@pytest.mark.unit
class TestMultiClusterHubTargetResolution:
    """One strict resolver: zero is a clean skip, many fail closed, a record is never rebound."""

    def test_no_record_and_a_crd_absent_inventory_is_a_noop_without_a_namespace_read(self, state_manager):
        hub = _AcmHub(mch_list=[_strict("CRD_ABSENT")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        assert hub.requests == [("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None)]

    def test_no_record_and_an_empty_strict_inventory_is_a_noop(self, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory()])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        assert hub.requests == [("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None)]

    def test_an_unreadable_inventory_fails_without_change(self, state_manager):
        hub = _AcmHub(mch_list=[_strict("ERROR")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert hub.requests == [("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None)]

    def test_more_than_one_live_multiclusterhub_fails_before_any_mutation(self, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory("hub-a", "hub-b")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert hub.requests == [("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None)]

    @pytest.mark.parametrize(
        "item",
        [
            "not-a-mapping",
            {"metadata": "not-a-mapping"},
            {"metadata": {}},
            {"metadata": {"name": ""}},
            {"metadata": {"name": "Not_A_DNS_Name"}},
        ],
        ids=["item", "metadata", "missing-name", "empty-name", "invalid-name"],
    )
    def test_a_malformed_inventory_member_fails_the_proof_before_any_named_read(self, item, state_manager):
        hub = _AcmHub(mch_list=[_strict("ITEMS", items=[item], resource_version="mch-list-1")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert hub.requests == [("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None)]

    def test_one_live_multiclusterhub_is_bound_by_its_exact_name(self, state_manager):
        name = "acm-hub-custom"
        hub = _AcmHub(mch_list=[_mch_inventory(name)], mch_gets=[_mch_present(name), _strict("OBJECT_ABSENT")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        mch_reads = _mch_family_requests(hub, "multiclusterhubs")
        assert mch_reads[1] == ("GET", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, name)
        assert ("DELETE", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, name) in hub.requests
        assert {record.key for record in hub.writes} == {_mch_key(name)}
        assert hub.writes[0].operator_deployment["mch_teardown_key"] == _mch_key(name)

    def test_a_target_gone_between_the_list_and_the_named_get_is_a_noop(self, state_manager):
        hub = _AcmHub(mch_gets=[_strict("OBJECT_ABSENT")], namespaces=[_namespace_present()])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        assert hub.requests == [
            ("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None),
            ("GET", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, MCH_NAME),
        ], "no identity capture, no namespace read, no write and no delete"

    def test_the_observability_half_removed_rule_is_unchanged(self, decommission_with_obs):
        """MCH's clean skip ignores the surviving ACM namespace; MCO's must not."""
        client = _arrange(
            decommission_with_obs,
            cr=_strict("CRD_ABSENT"),
            namespace=_strict("ITEMS", resource_version="ns-1"),
        )

        assert decommission_with_obs.teardown_observability() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        client.get_namespace_strict.assert_called_once_with(OBSERVABILITY_NAMESPACE)

    def test_more_than_one_durable_multiclusterhub_record_fails_closed(self, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory()])
        records = [
            _mch_record(TeardownPhase.DRAIN_PENDING, name="hub-a"),
            _mch_record(TeardownPhase.DRAIN_PENDING, name="hub-b"),
        ]
        dec = _mch_decommission(state_manager, hub, records=records)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert [request for request in hub.requests if request[0] in ("GET", "DELETE", "WRITE")] == []

    @pytest.mark.parametrize("live", [("hub-b",), ("hub-a", "hub-b")], ids=["other", "other-alongside"])
    def test_a_record_never_rebinds_to_a_different_live_multiclusterhub(self, live, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory(*live)])
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.DELETE_STARTED, name="hub-a")])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert [request for request in hub.requests if request[0] in ("GET", "DELETE", "WRITE")] == []
        assert dec.run_record.teardown_record(_mch_key("hub-a")).phase is TeardownPhase.DELETE_STARTED
        assert dec.run_record.teardown_record(_mch_key("hub-b")) is None

    def test_a_recorded_teardown_with_an_unreadable_inventory_fails_without_resuming(self, state_manager):
        hub = _AcmHub(mch_list=[_strict("ERROR")])
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.DRAIN_PENDING)])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert hub.requests == [("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None)]

    def test_an_invalid_recorded_name_fails_closed_before_any_read(self, state_manager, caplog):
        hub = _AcmHub()
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.DRAIN_PENDING, name="Bad_Name")])

        with caplog.at_level(logging.ERROR):
            assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert hub.requests == []
        assert "recorded MultiClusterHub name is invalid" in caplog.text

    def test_records_of_other_families_are_not_multiclusterhub_targets(self, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory()])
        mco = TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DRAIN_PENDING)
        dec = _mch_decommission(state_manager, hub, records=[mco])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)

    def test_a_record_resumes_by_its_recorded_name_when_the_kind_is_gone(self, state_manager):
        hub = _AcmHub(mch_list=[_strict("CRD_ABSENT")], mch_gets=[_strict("CRD_ABSENT")])
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.CR_ABSENT, name="hub-a")])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        assert ("GET", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, "hub-a") in hub.requests
        assert _completed_write(hub).absence_proofs["target_cr"].proof_type == "crd_absent"


@pytest.mark.unit
class TestMultiClusterHubTeardownSpec:
    """The MCH spec, the drain-shape invariant, and the per-family wait bounds."""

    def test_the_factory_supplies_the_pass_level_classifier_and_the_mch_bounds(self):
        spec = decommission_module._multiclusterhub_teardown_spec("hub-a")

        assert spec.classifier is classify_pods
        assert (spec.group, spec.version, spec.plural, spec.kind, spec.namespace, spec.name) == (
            MCH_GROUP,
            "v1",
            "multiclusterhubs",
            "MultiClusterHub",
            ACM_NAMESPACE,
            "hub-a",
        )
        assert spec.require_drain() == (ACM_NAMESPACE, None), "no selector: every Pod is classified"
        assert (
            spec.absence_wait_timeout(),
            spec.absence_wait_interval(),
            spec.drain_wait_timeout(),
            spec.drain_wait_interval(),
        ) == (DECOMMISSION_POD_TIMEOUT, DECOMMISSION_POD_INTERVAL, DECOMMISSION_POD_TIMEOUT, DECOMMISSION_POD_INTERVAL)

    def test_observability_keeps_its_selector_and_no_classifier(self):
        spec = decommission_module.OBSERVABILITY_TEARDOWN

        assert spec.classifier is None
        assert spec.require_drain() == (OBSERVABILITY_NAMESPACE, OBSERVABILITY_POD_LABEL_SELECTOR)
        assert (spec.drain_wait_timeout(), spec.drain_wait_interval()) == (
            OBSERVABILITY_TERMINATE_TIMEOUT,
            OBSERVABILITY_TERMINATE_INTERVAL,
        )

    @pytest.mark.parametrize(
        "kind, drain",
        [
            ("MultiClusterObservability", {"drain_namespace": OBSERVABILITY_NAMESPACE, "drain_label_selector": None}),
            ("MultiClusterHub", {"drain_namespace": ACM_NAMESPACE, "drain_label_selector": None}),
            (
                "MultiClusterHub",
                {"drain_namespace": ACM_NAMESPACE, "drain_label_selector": "app=x", "classifier": classify_pods},
            ),
            (
                "MultiClusterObservability",
                {"drain_namespace": OBSERVABILITY_NAMESPACE, "drain_label_selector": None, "classifier": classify_pods},
            ),
            ("ManagedCluster", {"drain_namespace": None, "drain_label_selector": None, "classifier": classify_pods}),
            ("ManagedCluster", {"drain_namespace": None, "drain_label_selector": "app=x"}),
            ("MultiClusterHub", {"drain_namespace": " ", "drain_label_selector": None, "classifier": classify_pods}),
            ("MultiClusterObservability", {"drain_namespace": OBSERVABILITY_NAMESPACE, "drain_label_selector": " "}),
        ],
        ids=[
            "selector-drain-missing-selector",
            "mch-without-classifier",
            "selector-and-classifier",
            "classifier-on-non-identity-kind",
            "no-drain-with-classifier",
            "no-drain-with-selector",
            "blank-namespace",
            "blank-selector",
        ],
    )
    def test_an_invalid_drain_shape_is_rejected_at_construction(self, kind, drain):
        """An MCO spec missing its selector must never silently become an all-Pod scan."""
        with pytest.raises(ValueError):
            decommission_module.TeardownSpec(
                group="example.io",
                version="v1",
                plural="examples",
                resource_name="examples",
                kind=kind,
                namespace=None,
                name="example",
                **drain,
            )

    def test_the_dispatch_wires_multiclusterhub_to_the_public_method(self, state_manager):
        dec = _mch_decommission(state_manager, _AcmHub())
        sentinel = SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        dec.teardown_multiclusterhub = Mock(return_value=sentinel)

        assert dec._run_substep("multiclusterhub") is sentinel
        dec.teardown_multiclusterhub.assert_called_once_with()

    def test_the_legacy_name_only_path_is_gone(self):
        assert not hasattr(Decommission, "_delete_multiclusterhub")

    def test_multiclusterhub_waits_use_the_decommission_pod_bounds(self, state_manager):
        hub = _AcmHub(namespaces=[_namespace_present()])
        dec = _mch_decommission(state_manager, hub)

        with patch.object(decommission_module, "wait_for_condition", return_value=True) as wait:
            assert dec.teardown_multiclusterhub().outcome is SubstepOutcome.COMPLETED

        assert [(c.kwargs["timeout"], c.kwargs["interval"]) for c in wait.call_args_list] == [
            (DECOMMISSION_POD_TIMEOUT, DECOMMISSION_POD_INTERVAL)
        ] * 2

    def test_observability_waits_keep_the_terminate_bounds(self, decommission_with_obs):
        _arrange(decommission_with_obs, namespace=_strict("ITEMS", resource_version="ns-1"))

        with patch.object(decommission_module, "wait_for_condition", return_value=True) as wait:
            assert decommission_with_obs.teardown_observability().outcome is SubstepOutcome.COMPLETED

        assert [(c.kwargs["timeout"], c.kwargs["interval"]) for c in wait.call_args_list] == [
            (OBSERVABILITY_TERMINATE_TIMEOUT, OBSERVABILITY_TERMINATE_INTERVAL)
        ] * 2


@pytest.mark.unit
class TestMultiClusterHubMutationOrdering:
    """Identity is captured after the UID is proved and made durable before the DELETE."""

    @pytest.mark.parametrize("failure", ["csv_list", "csv_get"])
    def test_a_fatal_capture_read_writes_nothing_and_deletes_nothing(self, failure, state_manager):
        hub = _AcmHub(**{failure: [_strict("ERROR")]})
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert hub.writes == []
        assert MCH_DELETE not in hub.requests

    def test_a_failed_delete_started_write_issues_no_delete(self, state_manager):
        hub = _AcmHub()
        dec = _mch_decommission(state_manager, hub)
        dec.run_record.record_teardown_phase = Mock(side_effect=FatalError("state flush failed"))

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert all(request in hub.requests for request in CAPTURE_REQUESTS)
        assert MCH_DELETE not in hub.requests

    @pytest.mark.parametrize("identity", ["captured", "unavailable"])
    def test_every_phase_write_carries_the_identical_identity(self, identity, state_manager):
        csv_list = [_strict("ITEMS", items=[], resource_version="csv-list-1")] if identity == "unavailable" else None
        hub = _AcmHub(csv_list=csv_list, namespaces=[_namespace_present()], pods=[_pods()])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        assert _phases(hub) == ["delete_started", "cr_absent", "drain_pending", "drained", "completed"]
        first = hub.writes[0]
        assert (first.operator_deployment is not None) is (identity == "captured")
        assert (first.operator_identity_unavailable is not None) is (identity == "unavailable")
        for record in hub.writes:
            assert (record.operator_deployment, record.operator_identity_unavailable) == (
                first.operator_deployment,
                first.operator_identity_unavailable,
            )

    def test_a_programmer_error_from_capture_propagates(self, state_manager, monkeypatch):
        monkeypatch.setattr(decommission_module, "capture_operator_identity", Mock(side_effect=ValueError("binding")))
        hub = _AcmHub()
        dec = _mch_decommission(state_manager, hub)

        with pytest.raises(ValueError):
            dec.teardown_multiclusterhub()
        assert hub.writes == [] and MCH_DELETE not in hub.requests

    def test_the_writer_requires_an_explicit_identity_argument(self):
        parameter = inspect.signature(Decommission._record).parameters["identity"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    def test_a_missing_or_misplaced_identity_is_a_programmer_error_not_a_failure(self, state_manager):
        hub = _AcmHub()
        dec = _mch_decommission(state_manager, hub)
        mch_spec = decommission_module._multiclusterhub_teardown_spec(MCH_NAME)
        identity = OperatorIdentity(operator_deployment=_captured_identity())

        with pytest.raises(ValueError):
            dec._record(mch_spec, _mch_key(), MCH_UID, TeardownPhase.DELETE_STARTED, identity=None)
        with pytest.raises(ValueError):
            dec._record(
                decommission_module.OBSERVABILITY_TEARDOWN,
                MCO_KEY,
                "uid-1",
                TeardownPhase.DELETE_STARTED,
                identity=identity,
            )
        assert hub.writes == []


@pytest.mark.unit
class TestMultiClusterHubResume:
    """A durable record resumes its obligations by its own name, UID and identity."""

    def test_a_delete_started_record_retries_the_guarded_delete_without_recapture(self, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory("hub-a")], mch_gets=[_mch_present("hub-a"), _strict("OBJECT_ABSENT")])
        seeded = _mch_record(TeardownPhase.DELETE_STARTED, name="hub-a")
        dec = _mch_decommission(state_manager, hub, records=[seeded])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        assert ("DELETE", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, "hub-a") in hub.requests
        assert hub.delete_uids == [MCH_UID]
        assert _mch_family_requests(hub, "clusterserviceversions") == []
        assert all(record.operator_deployment == seeded.operator_deployment for record in hub.writes)

    def test_a_same_name_replacement_fails_and_is_left_intact(self, state_manager):
        hub = _AcmHub(mch_gets=[_mch_present(uid="uid-replacement")])
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.DELETE_STARTED)])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert MCH_DELETE not in hub.requests
        assert hub.writes == []

    @pytest.mark.parametrize(
        "phase",
        [
            TeardownPhase.DELETE_STARTED,
            TeardownPhase.CR_ABSENT,
            TeardownPhase.DRAIN_PENDING,
            TeardownPhase.DRAINED,
            TeardownPhase.RECOVERY_REQUIRED,
            TeardownPhase.COMPLETED,
        ],
        ids=lambda phase: phase.value,
    )
    def test_no_resume_recaptures_identity(self, phase, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory()], mch_gets=[_strict("OBJECT_ABSENT")])
        seeded = _mch_record(phase)
        dec = _mch_decommission(state_manager, hub, records=[seeded])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        assert _mch_family_requests(hub, "clusterserviceversions") == []
        assert MCH_DELETE not in hub.requests
        assert dec.run_record.teardown_record(_mch_key()).operator_deployment == seeded.operator_deployment
        if phase is TeardownPhase.COMPLETED:
            assert hub.writes == []

    def test_an_unavailable_durable_identity_is_never_upgraded(self, state_manager):
        hub = _AcmHub(
            mch_list=[_mch_inventory()],
            mch_gets=[_strict("OBJECT_ABSENT")],
            namespaces=[_namespace_present("ns-final")],
            pods=[_pods(revision="pods-final")],
        )
        dec = _mch_decommission(
            state_manager, hub, records=[_mch_record(TeardownPhase.DRAIN_PENDING, identity="unavailable")]
        )

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        completed = _completed_write(hub)
        assert completed.operator_identity_unavailable == _unavailable_identity()
        assert completed.operator_deployment is None
        assert completed.resource_versions == {"drain_namespace": "ns-final", "drain_pods": "pods-final"}
        assert _mch_family_requests(hub, "clusterserviceversions") == []
        assert _mch_family_requests(hub, "deployments") == []

    @pytest.mark.parametrize(
        "phase",
        [TeardownPhase.CR_ABSENT, TeardownPhase.DRAIN_PENDING, TeardownPhase.DRAINED],
        ids=lambda phase: phase.value,
    )
    def test_a_post_delete_phase_resumes_its_remaining_obligations(self, phase, state_manager):
        hub = _AcmHub(
            mch_list=[_mch_inventory()],
            mch_gets=[_strict("OBJECT_ABSENT")],
            namespaces=[_namespace_present("ns-final")],
            pods=[_pods(_operator_pod(), revision="pods-final")],
            replicasets={OPERATOR_RS_NAME: [_replicaset()]},
            deployments=[_deployment(revision="deploy-final")],
        )
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(phase)])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        assert _completed_write(hub).resource_versions == {
            "drain_namespace": "ns-final",
            "drain_pods": "pods-final",
            "operator_deployment": "deploy-final",
        }
        assert MCH_DELETE not in hub.requests

    def test_recovery_required_retries_the_proof_without_rebinding(self, state_manager):
        hub = _AcmHub(
            mch_list=[_mch_inventory()],
            mch_gets=[_strict("OBJECT_ABSENT")],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        seeded = _mch_record(TeardownPhase.RECOVERY_REQUIRED)
        dec = _mch_decommission(state_manager, hub, records=[seeded])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        completed = _completed_write(hub)
        assert (completed.key, completed.expected_uid, completed.operator_deployment) == (
            seeded.key,
            seeded.expected_uid,
            seeded.operator_deployment,
        )

    def test_recovery_required_stays_blocked_while_the_deployment_is_replaced(self, state_manager):
        hub = _AcmHub(
            mch_list=[_mch_inventory()],
            mch_gets=[_strict("OBJECT_ABSENT")],
            deployments=[_deployment(uid="uid-replacement")],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.RECOVERY_REQUIRED)])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert "completed" not in _phases(hub)
        assert dec.run_record.teardown_record(_mch_key()).phase is TeardownPhase.RECOVERY_REQUIRED


_LOST_RECORDED_DEPLOYMENT = {
    "absent": _strict("OBJECT_ABSENT"),
    "replaced": _deployment(uid="uid-replacement"),
    "error": _strict("ERROR"),
}


@pytest.mark.unit
class TestMultiClusterHubDrain:
    """Identity-bound drain: every unproven Pod blocks, and identity status is checked first."""

    @pytest.mark.parametrize("lost", sorted(_LOST_RECORDED_DEPLOYMENT))
    def test_zero_pods_with_a_lost_recorded_deployment_enters_recovery_required(self, lost, state_manager):
        hub = _AcmHub(
            deployments=[_deployment(), _LOST_RECORDED_DEPLOYMENT[lost]],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub) == ["delete_started", "cr_absent", "drain_pending", "recovery_required"]

    def test_an_operator_pod_owned_through_the_recorded_chain_is_excluded(self, state_manager):
        hub = _AcmHub(
            namespaces=[_namespace_present()],
            pods=[_pods(_operator_pod())],
            replicasets={OPERATOR_RS_NAME: [_replicaset()]},
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)

    def test_a_rolling_update_drains_when_every_chain_resolves_to_the_recorded_deployment(
        self, state_manager, no_sleep
    ):
        old = _operator_pod("multiclusterhub-operator-old-1", rs_name="mch-op-old", rs_uid="uid-rs-old")
        new = _acm_pod("renamed-operator-new-1", controller=_controller("ReplicaSet", "mch-op-new", "uid-rs-new"))
        app = _acm_pod("console-chart-abc", controller=_controller("ReplicaSet", "console-rs", "uid-console-rs"))
        console_rs = _strict(
            "ITEMS",
            resource={
                "metadata": {
                    "name": "console-rs",
                    "namespace": ACM_NAMESPACE,
                    "uid": "uid-console-rs",
                    "owner_references": [_controller("Deployment", "console-chart", "uid-console")],
                }
            },
            resource_version="console-rs-rv",
        )
        hub = _AcmHub(
            namespaces=[_namespace_present()],
            pods=[_pods(old, new, app), _pods(old, new), _pods(new, revision="pods-final")],
            replicasets={
                "mch-op-old": [_replicaset("mch-op-old", "uid-rs-old")],
                "mch-op-new": [_replicaset("mch-op-new", "uid-rs-new")],
                "console-rs": [console_rs],
            },
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        assert _completed_write(hub).resource_versions["drain_pods"] == "pods-final"

    def test_unavailable_identity_with_zero_pods_drains(self, state_manager):
        hub = _AcmHub(
            csv_list=[_strict("ITEMS", items=[], resource_version="csv-list-1")],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        assert _completed_write(hub).operator_identity_unavailable["reason"] == "csv_absent"

    def test_unavailable_identity_excludes_no_pod_even_a_genuine_operator_pod(self, state_manager, one_mch_poll):
        hub = _AcmHub(
            csv_list=[_strict("ITEMS", items=[], resource_version="csv-list-1")],
            namespaces=[_namespace_present()],
            pods=[_pods(_operator_pod())],
            replicasets={OPERATOR_RS_NAME: [_replicaset()]},
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "drain_pending", "a timeout alone is not recovery_required"
        assert _mch_family_requests(hub, "replicasets") == []

    @pytest.mark.parametrize(
        "pod, replicasets",
        [
            (
                _acm_pod(
                    "multiclusterhub-operator-job-x", controller=_controller("Job", "mch-job", "uid-job", "batch/v1")
                ),
                {},
            ),
            (
                _acm_pod("multiclusterhub-operator-sts-0", controller=_controller("StatefulSet", "mch-sts", "uid-sts")),
                {},
            ),
            (
                _operator_pod("multiclusterhub-operator-other-1", rs_name="other-rs", rs_uid="uid-other-rs"),
                {"other-rs": [_replicaset("other-rs", "uid-other-rs", deployment_uid="uid-other-deployment")]},
            ),
        ],
        ids=["job-owned", "statefulset-owned", "unrelated-replicaset"],
    )
    def test_a_prefixed_pod_with_the_wrong_owner_blocks(self, pod, replicasets, state_manager, one_mch_poll):
        hub = _AcmHub(namespaces=[_namespace_present()], pods=[_pods(pod)], replicasets=replicasets)
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "drain_pending"

    def test_a_deployment_replaced_between_passes_enters_recovery_required(self, state_manager, no_sleep):
        hub = _AcmHub(
            deployments=[_deployment(), _deployment(), _deployment(uid="uid-replacement")],
            namespaces=[_namespace_present()],
            pods=[_pods(_acm_pod("app-pod-terminating")), _pods()],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub) == ["delete_started", "cr_absent", "drain_pending", "recovery_required"]

    def test_an_unreadable_pod_list_fails_and_is_never_empty(self, state_manager):
        hub = _AcmHub(namespaces=[_namespace_present()], pods=[_strict("ERROR")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "drain_pending"

    def test_an_unreadable_namespace_enters_recovery_required(self, state_manager):
        hub = _AcmHub(namespaces=[_strict("ERROR")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "recovery_required"
        assert _requests_of(hub, "LIST", "pods") == []

    def test_a_replicaset_read_failure_leaves_the_pod_blocking(self, state_manager, one_mch_poll):
        hub = _AcmHub(
            namespaces=[_namespace_present()],
            pods=[_pods(_operator_pod())],
            replicasets={OPERATOR_RS_NAME: [_strict("ERROR")]},
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "drain_pending"

    def test_every_drain_pod_list_is_unscoped(self, state_manager):
        hub = _AcmHub(namespaces=[_namespace_present()], pods=[_pods()])
        dec = _mch_decommission(state_manager, hub)

        dec.teardown_multiclusterhub()

        pod_lists = _requests_of(hub, "LIST", "pods")
        assert pod_lists and all(request[4] is None for request in pod_lists)


@pytest.mark.unit
class TestMultiClusterHubFinalEvidence:
    """The completed write is gated by, and built only from, a fresh final pass."""

    def test_a_new_pod_at_final_verification_prevents_completion(self, state_manager):
        hub = _AcmHub(namespaces=[_namespace_present()], pods=[_pods(), _pods(_acm_pod("late-pod"))])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "drained"

    @pytest.mark.parametrize("lost", sorted(_LOST_RECORDED_DEPLOYMENT))
    def test_a_lost_recorded_deployment_at_final_verification_enters_recovery_required(self, lost, state_manager):
        hub = _AcmHub(
            deployments=[_deployment(), _deployment(), _LOST_RECORDED_DEPLOYMENT[lost]],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub) == ["delete_started", "cr_absent", "drain_pending", "drained", "recovery_required"]

    def test_an_unreadable_final_pod_list_prevents_completion(self, state_manager):
        hub = _AcmHub(namespaces=[_namespace_present()], pods=[_pods(), _strict("ERROR")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "drained"

    def test_an_ambiguous_final_namespace_enters_recovery_required(self, state_manager):
        hub = _AcmHub(namespaces=[_namespace_present(), _strict("ERROR")], pods=[_pods()])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "recovery_required"

    @pytest.mark.parametrize("final_cr", ["present", "error"])
    def test_a_final_target_read_that_is_not_positive_absence_prevents_completion(self, final_cr, state_manager):
        final = _mch_present() if final_cr == "present" else _strict("ERROR")
        hub = _AcmHub(mch_gets=[_mch_present(), _strict("OBJECT_ABSENT"), final])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert "completed" not in _phases(hub)

    def test_unavailable_identity_completion_records_exactly_two_revisions(self, state_manager):
        hub = _AcmHub(
            csv_list=[_strict("ITEMS", items=[], resource_version="csv-list-1")],
            namespaces=[_namespace_present("ns-final")],
            pods=[_pods(revision="pods-final")],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        completed = _completed_write(hub)
        assert completed.resource_versions == {"drain_namespace": "ns-final", "drain_pods": "pods-final"}
        assert set(completed.absence_proofs) == {"target_cr"}


@pytest.mark.unit
class TestMultiClusterHubCompletedReproof:
    """A completed record is re-proved live and never rewritten."""

    def test_namespace_absent_reproof_reads_no_deployment_and_no_pods(self, state_manager):
        hub = _AcmHub(mch_gets=[_strict("OBJECT_ABSENT")], namespaces=[_strict("NAMESPACE_ABSENT")])
        seeded = _mch_record(TeardownPhase.COMPLETED)
        dec = _mch_decommission(state_manager, hub, records=[seeded])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        assert hub.writes == []
        assert _mch_family_requests(hub, "deployments") == [] and _mch_family_requests(hub, "pods") == []
        assert dec.run_record.teardown_record(_mch_key()) == seeded

    def test_namespace_present_reproof_classifies_a_fresh_pod_list(self, state_manager):
        hub = _AcmHub(
            mch_gets=[_strict("OBJECT_ABSENT")],
            namespaces=[_namespace_present()],
            pods=[_pods(_operator_pod())],
            replicasets={OPERATOR_RS_NAME: [_replicaset()]},
        )
        seeded = _mch_record(TeardownPhase.COMPLETED, completed_mode="namespace_present")
        dec = _mch_decommission(state_manager, hub, records=[seeded])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        assert hub.writes == []
        assert len(_mch_family_requests(hub, "deployments")) == 1
        assert len(_requests_of(hub, "LIST", "pods")) == 1

    @pytest.mark.parametrize(
        "scripts",
        [
            {"mch_gets": [_mch_present()]},
            {"mch_gets": [_strict("ERROR")]},
            {"namespaces": [_strict("ERROR")]},
            {"namespaces": [_namespace_present()], "pods": [_strict("ERROR")]},
            {"namespaces": [_namespace_present()], "deployments": [_strict("OBJECT_ABSENT")]},
            {"namespaces": [_namespace_present()], "pods": [_pods(_acm_pod("late-pod"))]},
        ],
        ids=[
            "target-present",
            "target-unreadable",
            "namespace-unreadable",
            "pods-unreadable",
            "identity-inconsistent",
            "blocking-pod",
        ],
    )
    def test_a_failed_reproof_fails_without_rewriting_the_record(self, scripts, state_manager):
        hub = _AcmHub(**{"mch_gets": [_strict("OBJECT_ABSENT")], **scripts})
        seeded = _mch_record(TeardownPhase.COMPLETED)
        dec = _mch_decommission(state_manager, hub, records=[seeded])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert hub.writes == []
        assert MCH_DELETE not in hub.requests
        assert dec.run_record.teardown_record(_mch_key()) == seeded


@pytest.mark.unit
class TestMultiClusterHubTeardownIvR40301:
    """The seven IV-R403-01 execution-result cases for the MultiClusterHub family."""

    def test_case1_no_mutation_then_proof_failure_reports_no_change(self, state_manager):
        hub = _AcmHub(mch_gets=[_strict("ERROR")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert MCH_DELETE not in hub.requests

    def test_case2_accepted_delete_then_cr_absence_failure_reports_the_change(self, state_manager):
        hub = _AcmHub(mch_gets=[_mch_present(), _strict("ERROR")])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub) == ["delete_started"]

    @pytest.mark.parametrize("drain_failure", ["pod-read", "broken-chain"])
    def test_case3_accepted_delete_then_drain_failure_reports_the_change(
        self, drain_failure, state_manager, one_mch_poll
    ):
        if drain_failure == "pod-read":
            hub = _AcmHub(namespaces=[_namespace_present()], pods=[_strict("ERROR")])
        else:
            hub = _AcmHub(
                namespaces=[_namespace_present()],
                pods=[_pods(_operator_pod())],
                replicasets={OPERATOR_RS_NAME: [_replicaset(uid="uid-rs-mismatch")]},
            )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert "completed" not in _phases(hub)

    def test_case4_accepted_delete_then_a_replaced_deployment_at_final_verification(self, state_manager):
        hub = _AcmHub(
            deployments=[_deployment(), _deployment(), _deployment(uid="uid-replacement")],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=True)
        assert _phases(hub)[-1] == "recovery_required"
        assert "completed" not in _phases(hub)

    def test_case5_resumed_drained_record_whose_final_proof_fails_reports_no_change(self, state_manager):
        hub = _AcmHub(
            mch_gets=[_strict("OBJECT_ABSENT"), _strict("OBJECT_ABSENT"), _strict("ERROR")],
        )
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.DRAINED)])

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.FAILED, changed=False)
        assert "completed" not in _phases(hub)

    def test_case6_an_earlier_substeps_change_survives_a_multiclusterhub_failure(self, state_manager):
        hub = _AcmHub(mch_list=[_strict("ERROR")])
        dec = _mch_decommission(state_manager, hub, has_observability=True)
        dec.teardown_observability = Mock(return_value=SubstepExecution(SubstepOutcome.COMPLETED, changed=True))
        dec.teardown_managed_clusters = Mock(
            return_value=SubstepExecution(SubstepOutcome.PRECONDITION_NOOP, changed=False)
        )

        result = dec.decommission(interactive=False)

        assert result.succeeded is False
        assert result.changed is True
        assert result.substeps["observability"] is SubstepOutcome.COMPLETED
        assert result.substeps["multiclusterhub"] is SubstepOutcome.FAILED

    @pytest.mark.parametrize(
        "scripts",
        [
            {"mch_list": [_strict("ERROR")]},
            {"mch_list": [_mch_inventory("hub-a", "hub-b")]},
            {"csv_list": [_strict("ERROR")]},
            {"delete": ApiException(status=500, reason="Internal")},
            {"namespaces": [_strict("ERROR")]},
            {"namespaces": [_namespace_present()], "pods": [_strict("ERROR")]},
            {"namespaces": [_namespace_present()], "deployments": [_deployment(), _strict("ERROR")]},
        ],
        ids=[
            "inventory-error",
            "ambiguous-inventory",
            "capture-error",
            "delete-rejected",
            "namespace-error",
            "pods-error",
            "deployment-error",
        ],
    )
    def test_case7_no_expected_failure_escapes_as_an_exception(self, scripts, state_manager, one_mch_poll):
        hub = _AcmHub(**scripts)
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub().outcome is SubstepOutcome.FAILED

    def test_a_target_gone_at_the_delete_reports_no_change_and_is_still_proven(self, state_manager):
        hub = _AcmHub(delete=TargetDisappeared("already absent at delete time"))
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
        assert _phases(hub) == ["delete_started", "cr_absent", "drain_pending", "drained", "completed"]


@pytest.mark.unit
class TestMultiClusterHubDryRun:
    """The preview reads strictly, captures identity read-only, and persists nothing."""

    @staticmethod
    def _preview(state_manager, hub, records=()):
        dec = _mch_decommission(state_manager, hub, records=records, dry_run=True)
        return dec, dec._preview_substep("multiclusterhub")

    def test_an_available_identity_predicts_a_delete_and_writes_nothing(self, state_manager):
        hub = _AcmHub()
        dec, predicted = self._preview(state_manager, hub)

        assert predicted is True
        assert hub.writes == [] and MCH_DELETE not in hub.requests
        positions = [hub.requests.index(request) for request in CAPTURE_REQUESTS]
        assert positions == sorted(positions)
        for resource in ("namespaces", "pods", "replicasets"):
            assert _mch_family_requests(hub, resource) == [], "the preview does not classify Pods"
        assert dec.run_record.all_teardown_records() == {}

    def test_an_unavailable_identity_still_predicts_a_delete(self, state_manager):
        hub = _AcmHub(csv_list=[_strict("ITEMS", items=[], resource_version="csv-list-1")])
        _dec, predicted = self._preview(state_manager, hub)

        assert predicted is True
        assert hub.writes == []

    def test_no_target_predicts_no_change(self, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory()])
        _dec, predicted = self._preview(state_manager, hub)

        assert predicted is False
        assert hub.requests == [("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None)]

    def test_a_target_gone_between_list_and_get_predicts_no_change(self, state_manager):
        hub = _AcmHub(mch_gets=[_strict("OBJECT_ABSENT")])
        _dec, predicted = self._preview(state_manager, hub)

        assert predicted is False
        assert _mch_family_requests(hub, "clusterserviceversions") == []

    @pytest.mark.parametrize(
        "scripts",
        [
            {"mch_list": [_strict("ERROR")]},
            {"mch_list": [_mch_inventory("hub-a", "hub-b")]},
            {"mch_gets": [_strict("ERROR")]},
            {"csv_list": [_strict("ERROR")]},
            {"csv_get": [_strict("ERROR")]},
        ],
        ids=["inventory-error", "ambiguous-inventory", "target-error", "csv-list-error", "csv-get-error"],
    )
    def test_an_unverifiable_read_fails_the_prediction(self, scripts, state_manager):
        hub = _AcmHub(**scripts)

        with pytest.raises(SwitchoverError):
            self._preview(state_manager, hub)
        assert hub.writes == [] and MCH_DELETE not in hub.requests

    @pytest.mark.parametrize(
        "phase, live",
        [
            (TeardownPhase.DELETE_STARTED, _mch_present(uid="uid-replacement")),
            (TeardownPhase.COMPLETED, _mch_present()),
        ],
        ids=["replaced", "completed-but-present"],
    )
    def test_a_recorded_target_that_contradicts_its_record_fails_the_prediction(self, phase, live, state_manager):
        hub = _AcmHub(mch_gets=[live])

        with pytest.raises(SwitchoverError):
            self._preview(state_manager, hub, records=[_mch_record(phase)])
        assert hub.writes == []

    def test_a_recorded_non_completed_live_target_predicts_a_delete_without_capture(self, state_manager):
        hub = _AcmHub()
        _dec, predicted = self._preview(state_manager, hub, records=[_mch_record(TeardownPhase.DELETE_STARTED)])

        assert predicted is True
        assert _mch_family_requests(hub, "clusterserviceversions") == []

    def test_a_live_run_after_a_preview_repeats_every_authoritative_read(self, state_manager):
        hub = _AcmHub(mch_gets=[_mch_present(), _mch_present(), _strict("OBJECT_ABSENT")])
        before = json.dumps(state_manager.capture_state_snapshot(), sort_keys=True)

        self._preview(state_manager, hub)

        assert json.dumps(state_manager.capture_state_snapshot(), sort_keys=True) == before
        hub.requests.clear()
        live = _mch_decommission(state_manager, hub)
        assert live.teardown_multiclusterhub() == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        for request in [
            ("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, None),
            ("GET", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE, MCH_NAME),
            *CAPTURE_REQUESTS,
        ]:
            assert request in hub.requests


# Python E4 request-shape measurement -- NOT yet complete E7 RBAC evidence. Each shape is
# (verb, API group, resource, namespace); "" is the core group and None is cluster scope.
MCH_LIST_SHAPE = ("LIST", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE)
MCH_GET_SHAPE = ("GET", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE)
CSV_LIST_SHAPE = ("LIST", CSV_GROUP, "clusterserviceversions", ACM_NAMESPACE)
CSV_GET_SHAPE = ("GET", CSV_GROUP, "clusterserviceversions", ACM_NAMESPACE)
DEPLOYMENT_GET_SHAPE = ("GET", "apps", "deployments", ACM_NAMESPACE)
REPLICASET_GET_SHAPE = ("GET", "apps", "replicasets", ACM_NAMESPACE)
NAMESPACE_GET_SHAPE = ("GET", "", "namespaces", None)
POD_LIST_SHAPE = ("LIST", "", "pods", ACM_NAMESPACE)
MCH_GUARDED_DELETE_SHAPE = ("DELETE", MCH_GROUP, "multiclusterhubs", ACM_NAMESPACE)


def _request_shapes(hub):
    return {request[:4] for request in hub.requests if request[0] != "WRITE"}


@pytest.mark.unit
class TestMultiClusterHubRequestShapes:
    """The measured Python request surface for the later RBAC STOP; no RBAC file is implied."""

    def test_fresh_captured_identity_with_a_rolling_update(self, state_manager, no_sleep):
        old = _operator_pod("mch-op-old-1", rs_name="mch-op-old", rs_uid="uid-rs-old")
        new = _operator_pod("mch-op-new-1", rs_name="mch-op-new", rs_uid="uid-rs-new")
        hub = _AcmHub(
            namespaces=[_namespace_present()],
            pods=[_pods(old, new), _pods(new)],
            replicasets={
                "mch-op-old": [_replicaset("mch-op-old", "uid-rs-old")],
                "mch-op-new": [_replicaset("mch-op-new", "uid-rs-new")],
            },
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub().outcome is SubstepOutcome.COMPLETED
        assert _request_shapes(hub) == {
            MCH_LIST_SHAPE,
            MCH_GET_SHAPE,
            CSV_LIST_SHAPE,
            CSV_GET_SHAPE,
            DEPLOYMENT_GET_SHAPE,
            REPLICASET_GET_SHAPE,
            NAMESPACE_GET_SHAPE,
            POD_LIST_SHAPE,
            MCH_GUARDED_DELETE_SHAPE,
        }
        assert hub.delete_uids == [MCH_UID]

    def test_fresh_unavailable_identity(self, state_manager):
        hub = _AcmHub(
            csv_list=[_strict("ITEMS", items=[], resource_version="csv-list-1")],
            namespaces=[_namespace_present()],
            pods=[_pods()],
        )
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub().outcome is SubstepOutcome.COMPLETED
        assert _request_shapes(hub) == {
            MCH_LIST_SHAPE,
            MCH_GET_SHAPE,
            CSV_LIST_SHAPE,
            NAMESPACE_GET_SHAPE,
            POD_LIST_SHAPE,
            MCH_GUARDED_DELETE_SHAPE,
        }

    def test_resume_from_drain_pending(self, state_manager):
        hub = _AcmHub(
            mch_list=[_mch_inventory()],
            mch_gets=[_strict("OBJECT_ABSENT")],
            namespaces=[_namespace_present()],
            pods=[_pods(_operator_pod())],
            replicasets={OPERATOR_RS_NAME: [_replicaset()]},
        )
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.DRAIN_PENDING)])

        assert dec.teardown_multiclusterhub().outcome is SubstepOutcome.COMPLETED
        assert _request_shapes(hub) == {
            MCH_LIST_SHAPE,
            MCH_GET_SHAPE,
            DEPLOYMENT_GET_SHAPE,
            REPLICASET_GET_SHAPE,
            NAMESPACE_GET_SHAPE,
            POD_LIST_SHAPE,
        }

    def test_completed_reproof(self, state_manager):
        hub = _AcmHub(
            mch_gets=[_strict("OBJECT_ABSENT")],
            namespaces=[_namespace_present()],
            pods=[_pods(_operator_pod())],
            replicasets={OPERATOR_RS_NAME: [_replicaset()]},
        )
        dec = _mch_decommission(state_manager, hub, records=[_mch_record(TeardownPhase.COMPLETED)])

        assert dec.teardown_multiclusterhub().outcome is SubstepOutcome.COMPLETED
        assert _request_shapes(hub) == {
            MCH_LIST_SHAPE,
            MCH_GET_SHAPE,
            NAMESPACE_GET_SHAPE,
            POD_LIST_SHAPE,
            DEPLOYMENT_GET_SHAPE,
            REPLICASET_GET_SHAPE,
        }

    def test_dry_run_preview(self, state_manager):
        hub = _AcmHub()
        dec = _mch_decommission(state_manager, hub, dry_run=True)

        assert dec._preview_substep("multiclusterhub") is True
        assert _request_shapes(hub) == {
            MCH_LIST_SHAPE,
            MCH_GET_SHAPE,
            CSV_LIST_SHAPE,
            CSV_GET_SHAPE,
            DEPLOYMENT_GET_SHAPE,
        }

    def test_no_target_clean_skip(self, state_manager):
        hub = _AcmHub(mch_list=[_mch_inventory()])
        dec = _mch_decommission(state_manager, hub)

        assert dec.teardown_multiclusterhub().outcome is SubstepOutcome.PRECONDITION_NOOP
        assert _request_shapes(hub) == {MCH_LIST_SHAPE}
