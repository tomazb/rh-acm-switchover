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
    BACKUP_NAMESPACE,
    IMMEDIATE_IMPORT_ANNOTATION,
    IMPORT_CONTROLLER_CONFIG_CM,
    MANAGED_CLUSTER_RESTORE_NAME,
    MCE_NAMESPACE,
    PATCH_VERIFY_RETRY_DELAY,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
)
from lib.exceptions import FatalError
from lib.run_record import HubFacts, RunRecord
from lib.strict_read import StrictReadOutcome
from lib.waiter import WaitConditionResult

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

    mock.list_custom_resources_strict.side_effect = strict_list
    mock.get_custom_resource_strict.side_effect = strict_get
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
    def test_wait_for_restore_timeout(self, mock_wait, activation_passive, mock_secondary_client):
        """Test timeout waiting for restore."""
        mock_wait.return_value = False  # Timeout

        # Discover a passive sync restore
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]

        with pytest.raises(FatalError, match="Timeout waiting for restore"):
            activation_passive._wait_for_restore_completion()

    def test_poll_restore_logic(self, activation_passive, mock_secondary_client):
        """Test the internal _poll_restore logic via _wait_for_restore_completion."""
        # Mock get_custom_resource to return appropriate values for different resources
        call_count = [0]

        def get_custom_resource_side_effect(**kwargs):
            call_count[0] += 1
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "cluster.open-cluster-management.io":
                return {
                    "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": "100"},
                    "status": {
                        "phase": "Enabled",
                        "veleroManagedClustersRestoreName": "test-velero-mc-restore",
                    },
                }
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "velero.io":
                return {
                    "status": {
                        "phase": "Completed",
                        "progress": {"itemsRestored": 50},
                    }
                }
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect

        def list_custom_resources_side_effect(**kwargs):
            if kwargs.get("plural") == "restores":
                return [
                    {
                        "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                        "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
                    }
                ]
            return [
                {"metadata": {"name": "cluster1"}},
                {"metadata": {"name": "local-cluster"}},
            ]

        mock_secondary_client.list_custom_resources.side_effect = list_custom_resources_side_effect

        with patch("modules.activation.wait_for_condition") as mock_wait:
            # Define side effect to execute the callback passed to wait_for_condition
            def side_effect(desc, callback, **kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                return result.done  # Execute callback and return done status

            mock_wait.side_effect = side_effect

            # We need to bypass the earlier steps to get to wait
            activation_passive.state.is_step_completed.side_effect = lambda step: step != "wait_restore_completion"

            activation_passive._wait_for_restore_completion()

            # Verify get_custom_resource was called by the callback
            assert mock_secondary_client.get_custom_resource.called

    def test_poll_restore_finished_with_errors_clusters_already_available(
        self, activation_passive, mock_secondary_client
    ):
        """FinishedWithErrors with exact ManagedCluster already-available messages should succeed."""
        call_count = [0]

        def get_custom_resource_side_effect(**kwargs):
            call_count[0] += 1
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "cluster.open-cluster-management.io":
                return {
                    "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": "200"},
                    "status": {
                        "phase": "FinishedWithErrors",
                        "lastMessage": "Velero restores have run to completion but encountered 1+ errors",
                        "messages": [
                            "ManagedCluster prod1 already available",
                            "ManagedCluster prod2 already available",
                            "ManagedCluster prod3 already available",
                        ],
                        "veleroManagedClustersRestoreName": "test-velero-mc-restore",
                    },
                }
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "velero.io":
                return {"status": {"phase": "Completed", "progress": {"itemsRestored": 50}}}
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect

        def list_custom_resources_side_effect(**kwargs):
            if kwargs.get("plural") == "restores":
                return [
                    {
                        "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                        "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
                    }
                ]
            return [
                {"metadata": {"name": "prod1"}},
                {"metadata": {"name": "prod2"}},
                {"metadata": {"name": "prod3"}},
                {"metadata": {"name": "local-cluster"}},
            ]

        mock_secondary_client.list_custom_resources.side_effect = list_custom_resources_side_effect

        with patch("modules.activation.wait_for_condition") as mock_wait:

            def side_effect(desc, callback, **kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                return result.done

            mock_wait.side_effect = side_effect
            activation_passive.state.is_step_completed.side_effect = lambda step: step != "wait_restore_completion"

            # Should NOT raise - FinishedWithErrors with "already available" is treated as success
            activation_passive._wait_for_restore_completion()

    def test_poll_restore_finished_with_errors_loose_already_available_message_raises(
        self, activation_passive, mock_secondary_client
    ):
        """Loose already-available substrings are not benign FinishedWithErrors messages."""

        def get_custom_resource_side_effect(**kwargs):
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "cluster.open-cluster-management.io":
                return {
                    "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": "300"},
                    "status": {
                        "phase": "FinishedWithErrors",
                        "lastMessage": "Velero restores failed",
                        "messages": ["managed cluster prod1 already available"],
                    },
                }
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": RESTORE_PASSIVE_SYNC_NAME}, "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True}},
        ]

        with patch("modules.activation.wait_for_condition") as mock_wait:

            def side_effect(desc, callback, **kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                return result.done

            mock_wait.side_effect = side_effect
            activation_passive.state.is_step_completed.side_effect = lambda step: step != "wait_restore_completion"

            with pytest.raises(FatalError, match="Restore failed: FinishedWithErrors"):
                activation_passive._wait_for_restore_completion()

    def test_poll_restore_finished_with_errors_real_errors_raises(self, activation_passive, mock_secondary_client):
        """FinishedWithErrors with non-'already available' messages should still raise."""

        def get_custom_resource_side_effect(**kwargs):
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "cluster.open-cluster-management.io":
                return {
                    "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": "300"},
                    "status": {
                        "phase": "FinishedWithErrors",
                        "lastMessage": "Velero restores failed",
                        "messages": ["restore of secret xyz failed"],
                    },
                }
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": RESTORE_PASSIVE_SYNC_NAME}, "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True}},
        ]

        with patch("modules.activation.wait_for_condition") as mock_wait:

            def side_effect(desc, callback, **kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                return result.done

            mock_wait.side_effect = side_effect
            activation_passive.state.is_step_completed.side_effect = lambda step: step != "wait_restore_completion"

            with pytest.raises(FatalError, match="Restore failed: FinishedWithErrors"):
                activation_passive._wait_for_restore_completion()

    def test_poll_restore_error_phase_raises(self, activation_full, mock_secondary_client):
        """Restore phase Error must fail immediately instead of timing out."""

        def get_custom_resource_side_effect(**kwargs):
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "cluster.open-cluster-management.io":
                return {
                    "metadata": {"name": "restore-acm-full", "resourceVersion": "400"},
                    "status": {
                        "phase": "Error",
                        "lastMessage": "Velero restore restore-acm-full-acm-managed-clusters-schedule-123 failed validation",
                    },
                }
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect
        mock_secondary_client.list_custom_resources.return_value = []

        with patch("modules.activation.wait_for_condition") as mock_wait:

            def side_effect(desc, callback, **kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                return result.done

            mock_wait.side_effect = side_effect

            with pytest.raises(FatalError, match="Restore failed: Error"):
                activation_full._wait_for_restore_completion()

    def test_poll_restore_failed_with_errors_phase_keeps_waiting(self, activation_full, mock_secondary_client):
        """FailedWithErrors is not a real terminal ACM restore phase and should not fail immediately."""
        restore_states = iter(
            [
                {
                    "metadata": {"name": "restore-acm-full", "resourceVersion": "500"},
                    "status": {
                        "phase": "FailedWithErrors",
                        "lastMessage": "controller still reconciling",
                    },
                },
                {
                    "metadata": {"name": "restore-acm-full", "resourceVersion": "501"},
                    "status": {
                        "phase": "Completed",
                        "lastMessage": "restore completed",
                    },
                },
            ]
        )

        def get_custom_resource_side_effect(**kwargs):
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "cluster.open-cluster-management.io":
                return next(restore_states)
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect
        mock_secondary_client.list_custom_resources.return_value = []

        with patch("modules.activation.wait_for_condition") as mock_wait:

            def side_effect(_desc, callback, **_kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                assert result.done is False
                result = callback()
                assert isinstance(result, WaitConditionResult)
                return result.done

            mock_wait.side_effect = side_effect

            activation_full._wait_for_restore_completion()

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
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "restore-acm-full"},
            "status": {"phase": "Completed"},
        }
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "local-cluster"}},
        ]

        with pytest.raises(FatalError, match="Expected at least 1 ManagedCluster"):
            activation._wait_for_restore_completion()

        assert mock_wait.call_count == 2

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
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": "restore-acm-full"},
            "status": {"phase": "Completed"},
        }
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

        activation._wait_for_restore_completion()
        assert mock_wait.call_count == 2

    def test_poll_velero_restore_waits_when_acm_restore_missing(self, activation_passive, mock_secondary_client):
        """Velero restore wait must return structured pending status when ACM restore is absent."""
        mock_secondary_client.get_custom_resource.return_value = None

        with patch("modules.activation.wait_for_condition") as mock_wait:

            def side_effect(_desc, callback, **_kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                assert result.done is False
                assert result.public_detail == "ACM restore not found"
                return result.done

            mock_wait.side_effect = side_effect

            with pytest.raises(FatalError, match="Timeout waiting for Velero managed clusters restore"):
                activation_passive._wait_for_managed_clusters_velero_restore("restore-acm-passive-sync", timeout=1)

    def test_poll_velero_restore_waits_when_velero_restore_name_missing(
        self, activation_passive, mock_secondary_client
    ):
        """Velero restore wait must return structured pending status until the Velero restore is created."""
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
            "status": {"phase": "Enabled"},
        }

        with patch("modules.activation.wait_for_condition") as mock_wait:

            def side_effect(_desc, callback, **_kwargs):
                result = callback()
                assert isinstance(result, WaitConditionResult)
                assert result.done is False
                assert result.public_detail == "Velero managed clusters restore not yet created"
                return result.done

            mock_wait.side_effect = side_effect

            with pytest.raises(FatalError, match="Timeout waiting for Velero managed clusters restore"):
                activation_passive._wait_for_managed_clusters_velero_restore("restore-acm-passive-sync", timeout=1)

    def test_recreate_restore_from_snapshot_raises_on_conflict(self, mock_secondary_client, mock_state_manager):
        """Rollback recreation must surface 409 conflicts to the caller."""
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )
        restore_snapshot = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
            "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
        }
        mock_secondary_client.create_custom_resource.side_effect = ApiException(status=409)

        with pytest.raises(ApiException) as exc_info:
            activation._recreate_restore_from_snapshot(restore_snapshot)

        assert exc_info.value.status == 409

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

    def test_unenforced_names_with_zero_floor_allow_an_empty_hub(self, mock_secondary_client, mock_state_manager):
        """With a zero floor and no enforced names an empty hub is informational only."""
        mock_secondary_client.list_custom_resources.return_value = []
        act = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            min_managed_clusters=0,
            expected_managed_cluster_names=["cluster-a"],
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

    def test_managed_cluster_name_listing_can_reuse_single_generation_cache(
        self, mock_secondary_client, mock_state_manager
    ):
        """Repeated same-generation ManagedCluster checks should not re-list resources."""
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster-a"}},
            {"metadata": {"name": "local-cluster"}},
        ]
        act = self._make_activation(mock_secondary_client, mock_state_manager, min_clusters=1)
        generation_cache = {}

        first = act._list_restored_managed_cluster_names(name_cache=generation_cache)
        second = act._list_restored_managed_cluster_names(name_cache=generation_cache)

        assert first == ["cluster-a"]
        assert second == ["cluster-a"]
        assert mock_secondary_client.list_custom_resources.call_count == 1

    def test_managed_cluster_name_listing_force_refreshes_for_final_verification(
        self, mock_secondary_client, mock_state_manager
    ):
        """Final verification can bypass a same-generation cache and see fresh ManagedClusters."""
        mock_secondary_client.list_custom_resources.side_effect = [
            [{"metadata": {"name": "local-cluster"}}],
            [
                {"metadata": {"name": "local-cluster"}},
                {"metadata": {"name": "cluster-a"}},
            ],
        ]
        act = self._make_activation(mock_secondary_client, mock_state_manager, min_clusters=1)
        generation_cache = {}

        first = act._list_restored_managed_cluster_names(name_cache=generation_cache)
        refreshed = act._list_restored_managed_cluster_names(name_cache=generation_cache, force_refresh=True)

        assert first == []
        assert refreshed == ["cluster-a"]
        assert mock_secondary_client.list_custom_resources.call_count == 2

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

    def test_build_restore_snapshot_raises_when_name_missing(self):
        """_build_restore_snapshot raises FatalError when restore has no metadata.name."""
        restore = {"metadata": {}, "spec": {"someField": True}}

        with pytest.raises(FatalError, match="missing metadata.name"):
            SecondaryActivation._build_restore_snapshot(restore)


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
