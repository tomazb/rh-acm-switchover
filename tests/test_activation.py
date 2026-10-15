"""Unit tests for modules/activation.py.

Tests cover SecondaryActivation class for activating the secondary hub.
"""

import copy
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from kubernetes.client.rest import ApiException

import modules.activation as activation_module
from lib.constants import (
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    IMMEDIATE_IMPORT_ANNOTATION,
    IMPORT_CONTROLLER_CONFIG_CM,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_PLURAL,
    MCE_NAMESPACE,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
)
from lib.exceptions import FatalError
from lib.run_record import HubFacts, RunRecord
from lib.strict_read import StrictReadOutcome

SecondaryActivation = activation_module.SecondaryActivation

AUTO_IMPORT_VERIFY_ERROR = "Unable to verify autoImportStrategy on the destination hub; verify API access and retry."


def create_mock_step_context(is_step_completed_func, mark_step_completed_func):
    """Create a mock step context manager that mimics StepContext behavior."""

    @contextmanager
    def mock_step(step_name, logger=None):
        if is_step_completed_func(step_name):
            if logger:
                logger.info("Step already completed: %s", step_name)
            yield False
        else:
            yield True
            mark_step_completed_func(step_name)

    return mock_step


def valid_auto_import_configmap(data):
    """Build verified identity evidence for the activation-time ConfigMap read."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": IMPORT_CONTROLLER_CONFIG_CM,
            "namespace": MCE_NAMESPACE,
        },
        "data": data,
    }


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub.

    The strict readers answer from the advisory mocks, so a test seeds one list/get
    result and every strict read of it is a positively complete outcome.
    """
    mock = Mock()
    mock.dry_run = False  # Ensure dry_run is False for tests

    def strict_list(**kwargs):
        items = mock.list_custom_resources(**kwargs)
        return StrictReadOutcome.from_items([_created(item) for item in items])

    def strict_get(**kwargs):
        resource = mock.get_custom_resource(**kwargs)
        if not resource:
            return StrictReadOutcome.object_absent("object_not_found")
        return StrictReadOutcome.from_resource(resource)

    def strict_managed_clusters():
        return StrictReadOutcome.from_items(
            mock.list_custom_resources(
                group=MANAGED_CLUSTER_API_GROUP, version=MANAGED_CLUSTER_API_VERSION, plural=MANAGED_CLUSTER_PLURAL
            )
        )

    mock.list_custom_resources_strict.side_effect = strict_list
    mock.get_custom_resource_strict.side_effect = strict_get
    mock.list_managed_clusters_strict.side_effect = strict_managed_clusters
    return mock


def _created(item):
    """A listed object with the creationTimestamp every live object carries."""
    item = copy.deepcopy(item)
    if isinstance(item.get("metadata"), dict):
        item["metadata"].setdefault("creationTimestamp", "2026-03-30T09:00:00Z")
    return item


@pytest.fixture
def mock_state_manager():
    """Create a mock StateManager with step() context manager support."""
    mock = Mock()
    mock.is_step_completed.return_value = False
    # Set up step() to return a proper context manager
    mock.step.side_effect = create_mock_step_context(
        mock.is_step_completed,
        mock.mark_step_completed,
    )
    # Back the config accessors with a real dict so tests can seed and read
    # cross-phase facts through RunRecord instead of raw key literals. The
    # deep copies mirror StateManager's ownership contract (stored and returned
    # values are isolated from the caller); unseeded keys resolve to the
    # caller-supplied default.
    config: dict = {}
    mock.config = config

    def _set_config(key, value):
        config[key] = copy.deepcopy(value)

    def _get_config(key, default=None):
        if key not in config:
            return default
        return copy.deepcopy(config[key])

    mock._set_config.side_effect = _set_config
    mock._get_config.side_effect = _get_config
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

    def test_verify_passive_sync_failure(self, activation_passive, mock_secondary_client):
        """Test failure when passive sync restore is not found."""
        # No restores discovered and fallback name lookup also fails
        mock_secondary_client.list_custom_resources.return_value = []
        mock_secondary_client.get_custom_resource.return_value = None

        result = activation_passive.activate()

        assert result is False

    def test_verify_passive_sync_wrong_phase(self, activation_passive, mock_secondary_client):
        """Test failure when passive sync is in wrong phase."""
        # Discover the restore, but report a failing status
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": "100"},
            "status": {"phase": "Failed"},
        }

        result = activation_passive.activate()

        assert result is False

    @patch("modules.activation.wait_for_condition")
    def test_full_restore_enforces_min_managed_clusters_after_restore(
        self, mock_wait, mock_secondary_client, mock_state_manager
    ):
        """Full restore must enforce --min-managed-clusters after the restore completes."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="full",
            min_managed_clusters=1,
        )

        def wait_side_effect(description, callback, **_kwargs):
            if description == "restore restore-acm-full":
                return True
            if description == "full-restore ManagedCluster resources":
                return False
            return callback().done

        mock_wait.side_effect = wait_side_effect
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "local-cluster"}},
        ]

        with pytest.raises(FatalError, match="Expected at least 1 ManagedCluster"):
            activation._wait_for_managed_clusters_restored(60)

        assert mock_wait.call_count == 1

    @patch("modules.activation.wait_for_condition")
    def test_full_restore_waits_for_min_managed_clusters_after_restore(
        self, mock_wait, mock_secondary_client, mock_state_manager
    ):
        """Full restore should wait for ManagedClusters before enforcing the threshold."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="full",
            min_managed_clusters=1,
        )
        mock_secondary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "local-cluster"}}],
            [{"metadata": {"name": "local-cluster"}}, {"metadata": {"name": "cluster-a"}}],
        ]

        def wait_side_effect(description, callback, **_kwargs):
            if description == "restore restore-acm-full":
                return True
            first = callback()
            assert first.done is False
            second = callback()
            assert second.done is True
            return second.done

        mock_wait.side_effect = wait_side_effect

        activation._wait_for_managed_clusters_restored(60)
        assert mock_wait.call_count == 1

    def test_apply_immediate_import_annotations(self, mock_secondary_client, mock_state_manager):
        """Test immediate-import annotation application under ImportOnly."""
        RunRecord(mock_state_manager).record_hub_facts(HubFacts(secondary_version="2.14.0"))
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap_advisory.return_value = None
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster-a", "annotations": {}}},
            {"metadata": {"name": "cluster-b", "annotations": {IMMEDIATE_IMPORT_ANNOTATION: ""}}},
            {"metadata": {"name": "local-cluster", "annotations": {}}},
        ]

        activation._apply_immediate_import_annotations()

        mock_secondary_client.patch_managed_cluster.assert_called_once()
        args, kwargs = mock_secondary_client.patch_managed_cluster.call_args
        assert kwargs["name"] == "cluster-a"
        assert kwargs["patch"]["metadata"]["annotations"][IMMEDIATE_IMPORT_ANNOTATION] == ""

    def test_get_auto_import_strategy_returns_configured_value(self, mock_secondary_client, mock_state_manager):
        """A valid present ConfigMap returns its configured strategy."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )
        mock_secondary_client.get_configmap_advisory.return_value = valid_auto_import_configmap(
            {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC}
        )

        assert activation._get_auto_import_strategy() == AUTO_IMPORT_STRATEGY_SYNC

    def test_apply_immediate_import_annotations_handles_completed_value(
        self, mock_secondary_client, mock_state_manager
    ):
        """Ensure Completed annotations are reset via delete-and-add."""
        RunRecord(mock_state_manager).record_hub_facts(HubFacts(secondary_version="2.14.1"))
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap_advisory.return_value = valid_auto_import_configmap(
            {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_DEFAULT}
        )
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster-a", "annotations": {IMMEDIATE_IMPORT_ANNOTATION: "Completed"}}},
            {"metadata": {"name": "local-cluster", "annotations": {}}},
        ]

        activation._apply_immediate_import_annotations()

        assert mock_secondary_client.patch_managed_cluster.call_count == 2
        first_call = mock_secondary_client.patch_managed_cluster.call_args_list[0]
        second_call = mock_secondary_client.patch_managed_cluster.call_args_list[1]

        assert first_call.kwargs["patch"]["metadata"]["annotations"][IMMEDIATE_IMPORT_ANNOTATION] is None
        assert second_call.kwargs["patch"]["metadata"]["annotations"][IMMEDIATE_IMPORT_ANNOTATION] == ""

    def test_apply_immediate_import_annotations_skips_for_sync_strategy(
        self, mock_secondary_client, mock_state_manager
    ):
        """Skip annotation when autoImportStrategy already ImportAndSync."""
        RunRecord(mock_state_manager).record_hub_facts(HubFacts(secondary_version="2.14.2"))
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap_advisory.return_value = valid_auto_import_configmap(
            {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC}
        )

        activation._apply_immediate_import_annotations()

        mock_secondary_client.list_custom_resources.assert_not_called()
        mock_secondary_client.patch_managed_cluster.assert_not_called()

    def test_apply_immediate_import_annotations_fails_closed_on_read_error(
        self, mock_secondary_client, mock_state_manager, caplog
    ):
        """API failures must raise the stable error before ManagedCluster work."""
        RunRecord(mock_state_manager).record_hub_facts(HubFacts(secondary_version="2.14.2"))
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )
        failure = RuntimeError("R302-ACTIVATION-READ-SENTINEL")
        mock_secondary_client.get_configmap_advisory.side_effect = failure

        with caplog.at_level("DEBUG", logger="acm_switchover"):
            with pytest.raises(FatalError) as exc_info:
                activation._apply_immediate_import_annotations()

        assert str(exc_info.value) == AUTO_IMPORT_VERIFY_ERROR
        assert exc_info.value.__cause__ is failure
        assert "R302-ACTIVATION-READ-SENTINEL" not in str(exc_info.value)
        assert "R302-ACTIVATION-READ-SENTINEL" not in caplog.text
        mock_secondary_client.list_custom_resources.assert_not_called()
        mock_secondary_client.patch_managed_cluster.assert_not_called()

    @pytest.mark.parametrize(
        "configmap",
        [
            [],
            valid_auto_import_configmap({AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_DEFAULT})
            | {"metadata": {"name": "wrong-name", "namespace": MCE_NAMESPACE}},
            valid_auto_import_configmap({AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_DEFAULT})
            | {"metadata": {"name": IMPORT_CONTROLLER_CONFIG_CM, "namespace": "wrong-namespace"}},
            valid_auto_import_configmap("not-a-mapping"),
        ],
        ids=["not-mapping", "wrong-name", "wrong-namespace", "invalid-data"],
    )
    def test_apply_immediate_import_annotations_fails_closed_on_malformed_configmap(
        self, mock_secondary_client, mock_state_manager, configmap
    ):
        """Malformed present evidence must fail before ManagedCluster discovery or patch."""
        RunRecord(mock_state_manager).record_hub_facts(HubFacts(secondary_version="2.14.2"))
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )
        mock_secondary_client.get_configmap_advisory.return_value = configmap

        with pytest.raises(FatalError) as exc_info:
            activation._apply_immediate_import_annotations()

        assert str(exc_info.value) == AUTO_IMPORT_VERIFY_ERROR
        assert str(exc_info.value) != "error"
        mock_secondary_client.list_custom_resources.assert_not_called()
        mock_secondary_client.patch_managed_cluster.assert_not_called()

    def test_apply_immediate_import_annotations_raises_on_failures(self, mock_secondary_client, mock_state_manager):
        """Ensure failures raise and do not mark the step completed."""
        RunRecord(mock_state_manager).record_hub_facts(HubFacts(secondary_version="2.14.0"))
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap_advisory.return_value = None
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster-a", "annotations": {}}},
            {"metadata": {"name": "local-cluster", "annotations": {}}},
        ]

        with patch.object(activation, "_reset_immediate_import_annotation", return_value=False):
            with pytest.raises(FatalError, match="cluster-a"):
                activation._apply_immediate_import_annotations()

    def test_reset_immediate_import_annotation_handles_api_exception(self, mock_secondary_client, mock_state_manager):
        """Verify ApiException returns False and logs warning."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.patch_managed_cluster.side_effect = ApiException(status=409)

        result = activation._reset_immediate_import_annotation("cluster-a", "Completed")

        assert result is False
        mock_secondary_client.patch_managed_cluster.assert_called_once()


@pytest.mark.unit
class TestMinManagedClusters:
    """Tests for the min_managed_clusters sanity check."""

    def _make_activation(self, mock_secondary_client, mock_state_manager, min_clusters=0):
        return SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=min_clusters,
        )

    def test_zero_clusters_with_min_zero_logs_warning_not_error(self, mock_secondary_client, mock_state_manager):
        """min_managed_clusters=0 should log a warning when zero clusters found, not raise."""
        mock_secondary_client.list_custom_resources.return_value = []
        act = self._make_activation(mock_secondary_client, mock_state_manager, min_clusters=0)
        act._verify_managed_clusters_restored()  # Must not raise

    def test_nonzero_min_raises_when_below_threshold(self, mock_secondary_client, mock_state_manager):
        """min_managed_clusters > 0 must raise FatalError when fewer clusters are found."""
        mock_secondary_client.list_custom_resources.return_value = []
        act = self._make_activation(mock_secondary_client, mock_state_manager, min_clusters=2)

        with pytest.raises(FatalError, match="Expected at least 2"):
            act._verify_managed_clusters_restored()

    def test_meets_min_cluster_count_passes(self, mock_secondary_client, mock_state_manager):
        """Activation succeeds when cluster count meets or exceeds min_managed_clusters."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster-a"}},
            {"metadata": {"name": "cluster-b"}},
        ]
        act = self._make_activation(mock_secondary_client, mock_state_manager, min_clusters=2)
        act._verify_managed_clusters_restored()  # Must not raise

    def test_local_cluster_excluded_from_count(self, mock_secondary_client, mock_state_manager):
        """local-cluster must not count toward the minimum managed cluster total."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "local-cluster"}},
        ]
        act = self._make_activation(mock_secondary_client, mock_state_manager, min_clusters=1)

        with pytest.raises(FatalError, match="Expected at least 1"):
            act._verify_managed_clusters_restored()

    def test_derived_expected_names_are_enforced(self, mock_secondary_client, mock_state_manager):
        """Derived preflight names must be present after activation."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster-a"}},
            {"metadata": {"name": "local-cluster"}},
        ]
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=2,
            expected_managed_cluster_names=["cluster-a", "cluster-b"],
            enforce_expected_managed_cluster_names=True,
        )

        with pytest.raises(FatalError, match="Missing expected ManagedCluster"):
            act._verify_managed_clusters_restored()

    def test_an_explicit_floor_above_the_present_names_reports_the_floor(
        self, mock_secondary_client, mock_state_manager
    ):
        """An explicit --min-managed-clusters N is not a preflight count, so the refusal names only the floor."""
        mock_secondary_client.list_custom_resources.return_value = [{"metadata": {"name": "cluster-a"}}]
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=3,
            expected_managed_cluster_names=["cluster-a"],
            enforce_expected_managed_cluster_names=True,
        )

        with pytest.raises(FatalError) as refused:
            act._verify_managed_clusters_restored()
        assert (
            str(refused.value) == "Expected at least 3 ManagedCluster(s) after restore, but found only 1: ['cluster-a']"
        )

    def test_unenforced_recorded_names_with_zero_floor_refuse_an_empty_hub(
        self, mock_secondary_client, mock_state_manager
    ):
        """#340 E2: recorded names that are not enforced (the waiver) never make an empty hub
        acceptable; that is the inventory_empty refusal post-activation also applies."""
        mock_secondary_client.list_custom_resources.return_value = []
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=0,
            expected_managed_cluster_names=["cluster-a"],
            enforce_expected_managed_cluster_names=False,
        )

        with pytest.raises(FatalError, match="inventory_empty"):
            act._verify_managed_clusters_restored()

    def test_unenforced_recorded_names_with_zero_floor_accept_a_local_cluster_only_hub(
        self, mock_secondary_client, mock_state_manager
    ):
        """With the name check lifted and a zero floor, a non-empty inventory is informational only."""
        mock_secondary_client.list_custom_resources.return_value = [{"metadata": {"name": "local-cluster"}}]
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=0,
            expected_managed_cluster_names=["cluster-a"],
            enforce_expected_managed_cluster_names=False,
        )

        act._verify_managed_clusters_restored()

    @pytest.mark.parametrize("member", [{}, {"metadata": {}}, {"metadata": {"name": ""}}, {"metadata": None}])
    def test_a_nameless_member_is_malformed_never_the_non_empty_proof(
        self, mock_secondary_client, mock_state_manager, member
    ):
        """#340 E2: a member without a name refuses like post-activation's malformed_inventory;
        it never stands in for a non-empty destination under a waiver and a zero floor."""
        mock_secondary_client.list_custom_resources.return_value = [member]
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=0,
            expected_managed_cluster_names=["cluster-a"],
            enforce_expected_managed_cluster_names=False,
        )

        with pytest.raises(FatalError, match="malformed_inventory"):
            act._verify_managed_clusters_restored()

    def test_zero_floor_without_recorded_names_allows_an_empty_hub(self, mock_secondary_client, mock_state_manager):
        """Unchanged: nothing recorded and a zero floor permit an empty destination."""
        mock_secondary_client.list_custom_resources.return_value = []
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=0,
            expected_managed_cluster_names=[],
            enforce_expected_managed_cluster_names=False,
        )

        act._verify_managed_clusters_restored()

    def test_zero_floor_with_enforced_names_waits_for_the_names(self, mock_secondary_client, mock_state_manager):
        """AC25: an explicit 0 keeps expected names enforced, so the restore wait still polls for them."""
        mock_secondary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "local-cluster"}}],
            [{"metadata": {"name": "local-cluster"}}, {"metadata": {"name": "cluster-a"}}],
        ]
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=0,
            expected_managed_cluster_names=["cluster-a"],
            enforce_expected_managed_cluster_names=True,
        )

        def wait_side_effect(_description, condition_fn, **_kwargs):
            assert condition_fn().done is False
            assert condition_fn().done is True
            return True

        with patch("modules.activation.wait_for_condition", side_effect=wait_side_effect) as wait:
            act._wait_for_managed_clusters_restored(timeout=10)

        wait.assert_called_once()

    def test_managed_cluster_wait_refreshes_between_poll_iterations(self, mock_secondary_client, mock_state_manager):
        """ManagedCluster wait polling must refresh so newly restored clusters can become visible."""
        mock_secondary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "local-cluster"}}],
            [
                {"metadata": {"name": "local-cluster"}},
                {"metadata": {"name": "cluster-a"}},
            ],
        ]
        act = self._make_activation(mock_secondary_client, mock_state_manager, min_clusters=1)

        def wait_side_effect(_description, condition_fn, **_kwargs):
            first = condition_fn()
            second = condition_fn()

            assert first.done is False
            assert second.done is True
            return True

        with patch("modules.activation.wait_for_condition", side_effect=wait_side_effect):
            act._wait_for_managed_clusters_restored(timeout=10)

        assert mock_secondary_client.list_custom_resources.call_count == 2


@pytest.mark.unit
class TestPassiveSyncRestoreMissingName:
    """Tests for FatalError when restore is missing metadata.name."""

    def test_get_passive_sync_restore_name_raises_when_name_missing(self, activation_passive, mock_secondary_client):
        """_get_passive_sync_restore_name raises FatalError when discovered restore has no name."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]

        with pytest.raises(FatalError, match="malformed Restore"):
            activation_passive._get_passive_sync_restore_name()


@pytest.mark.unit
class TestVerifyPassiveSyncFinishedWithErrors:
    """Tests for FinishedWithErrors handling in _verify_passive_sync."""

    def test_finished_with_errors_real_errors_raises(self, activation_passive, mock_secondary_client):
        """FinishedWithErrors with non-'already available' messages raises FatalError."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": "100"},
            "status": {
                "phase": "FinishedWithErrors",
                "lastMessage": "Velero restores failed",
                "messages": ["restore of secret xyz failed", "backup corrupted"],
            },
        }

        with pytest.raises(FatalError, match="Passive sync restore not ready: FinishedWithErrors"):
            activation_passive._verify_passive_sync()

    def test_finished_with_errors_mixed_messages_raises(self, activation_passive, mock_secondary_client):
        """FinishedWithErrors with a mix of exact already-available and real errors raises."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": "100"},
            "status": {
                "phase": "FinishedWithErrors",
                "lastMessage": "Some errors",
                "messages": [
                    "ManagedCluster prod1 already available",
                    "restore of configmap abc failed",
                ],
            },
        }

        with pytest.raises(FatalError, match="Passive sync restore not ready"):
            activation_passive._verify_passive_sync()


@pytest.mark.unit
class TestActivateResumeAndEdgeCases:
    """Tests for resume scenarios and edge cases in activation."""

    def test_activate_catches_switchover_error(self, mock_secondary_client, mock_state_manager):
        """SwitchoverError during activation returns False and records error."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        # No restores found → FatalError (subclass of SwitchoverError) from _get_passive_sync_restore_name
        mock_secondary_client.list_custom_resources.return_value = []
        mock_secondary_client.get_custom_resource.return_value = None

        result = activation.activate()

        assert result is False
        mock_state_manager.add_error.assert_called_once()

    def test_activate_catches_runtime_error(self, mock_secondary_client, mock_state_manager):
        """RuntimeError during activation returns False and records error."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.list_custom_resources.side_effect = RuntimeError("unexpected")

        result = activation.activate()

        assert result is False
        mock_state_manager.add_error.assert_called_once()
        assert "Unexpected" in mock_state_manager.add_error.call_args[0][0]

    def test_activate_reraises_programming_error(self, mock_secondary_client, mock_state_manager):
        """Non-SwitchoverError/RuntimeError/ValueError programming errors are re-raised."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.list_custom_resources.side_effect = TypeError("bad type")

        with pytest.raises(TypeError, match="bad type"):
            activation.activate()
