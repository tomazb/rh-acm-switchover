"""Unit tests for modules/activation.py.

Tests cover SecondaryActivation class for activating the secondary hub.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import modules.activation as activation_module
from kubernetes.client.rest import ApiException

from lib.constants import (
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_NAMESPACE,
    IMMEDIATE_IMPORT_ANNOTATION,
    MANAGED_CLUSTER_RESTORE_NAME,
    PATCH_VERIFY_RETRY_DELAY,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
)
from lib.exceptions import FatalError

SecondaryActivation = activation_module.SecondaryActivation


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


@pytest.fixture
def mock_secondary_client():
    """Create a mock KubeClient for secondary hub."""
    mock = Mock()
    mock.dry_run = False  # Ensure dry_run is False for tests
    return mock


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

    @patch("modules.activation.time.sleep")
    @patch("modules.activation.wait_for_condition")
    def test_activate_passive_success(
        self, mock_wait, mock_sleep, activation_passive, mock_secondary_client, mock_state_manager
    ):
        """Test successful passive activation."""
        mock_wait.return_value = True
        mock_sleep.return_value = None  # Skip real sleep in patch verification loop

        # Track if patch has been applied to simulate the patched state
        patch_applied = {"value": False}

        # Mock verify_passive_sync - return Enabled state
        # This will be called multiple times for different resources
        def get_custom_resource_side_effect(**kwargs):
            if kwargs.get("plural") == "restores" and kwargs.get("name") == RESTORE_PASSIVE_SYNC_NAME:
                result = {
                    "metadata": {
                        "name": RESTORE_PASSIVE_SYNC_NAME,
                        # resourceVersion changes after patch is applied
                        "resourceVersion": "200" if patch_applied["value"] else "100",
                    },
                    "status": {
                        "phase": "Enabled",
                        "lastMessage": "Synced",
                        "veleroManagedClustersRestoreName": "test-velero-restore",
                    },
                    "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
                }
                # After patch is applied, include the patched field
                if patch_applied["value"]:
                    result["spec"][SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME] = VELERO_BACKUP_LATEST
                return result
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "velero.io":
                return {
                    "status": {
                        "phase": "Completed",
                        "progress": {"itemsRestored": 100},
                    }
                }
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect

        # Mock list_custom_resources for both restore discovery and managed clusters verification
        def list_custom_resources_side_effect(**kwargs):
            if kwargs.get("plural") == "restores":
                # Return the passive sync restore for discovery
                return [
                    {
                        "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                        "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
                        "status": {"phase": "Enabled"},
                    }
                ]
            if kwargs.get("plural") == "managedclusters":
                return [
                    {"metadata": {"name": "cluster1"}},
                    {"metadata": {"name": "local-cluster"}},
                ]
            return []

        mock_secondary_client.list_custom_resources.side_effect = list_custom_resources_side_effect

        # Mock patch for activation - mark patch as applied and return patched resource
        def patch_side_effect(**kwargs):
            patch_applied["value"] = True
            return {"spec": {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST}}

        mock_secondary_client.patch_custom_resource.side_effect = patch_side_effect

        result = activation_passive.activate()

        assert result is True

        # 2. Activate (patch)
        mock_secondary_client.patch_custom_resource.assert_called_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name=RESTORE_PASSIVE_SYNC_NAME,
            patch={"spec": {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST}},
            namespace=BACKUP_NAMESPACE,
        )
        # Patch verification loop sleeps once before detecting the resourceVersion change
        mock_sleep.assert_called_once_with(PATCH_VERIFY_RETRY_DELAY)

    @patch("modules.activation.wait_for_condition")
    def test_activate_full_success(self, mock_wait, activation_full, mock_secondary_client):
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

    def test_verify_passive_sync_failure(self, activation_passive, mock_secondary_client):
        """Test failure when passive sync restore is not found."""
        # No restores discovered and fallback name lookup also fails
        mock_secondary_client.list_custom_resources.return_value = []
        mock_secondary_client.get_custom_resource.return_value = None

        result = activation_passive.activate()

        assert result is False

    @patch("modules.activation.time.sleep")
    @patch("modules.activation.wait_for_condition")
    def test_verify_passive_sync_completed_phase_is_valid(
        self, mock_wait, mock_sleep, activation_passive, mock_secondary_client
    ):
        """Test that phase=Completed is treated as a ready passive sync state.

        This test fully mocks the activate() flow so patch verification succeeds:
        - time.sleep is patched to avoid real sleeps
        - get_custom_resource returns increasing resourceVersion after patch
        - patch_custom_resource returns a dict including the patched spec
        - downstream Velero/ManagedCluster checks are mocked
        """
        mock_wait.return_value = True
        mock_sleep.return_value = None

        patch_applied = {"value": False}

        def list_custom_resources_side_effect(**kwargs):
            if kwargs.get("plural") == "restores":
                return [
                    {
                        "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                        "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
                    }
                ]
            if kwargs.get("plural") == "managedclusters":
                return [
                    {"metadata": {"name": "cluster1"}},
                    {"metadata": {"name": "local-cluster"}},
                ]
            return []

        mock_secondary_client.list_custom_resources.side_effect = list_custom_resources_side_effect

        def get_custom_resource_side_effect(**kwargs):
            # Passive sync Restore (used by _verify_passive_sync, _get_restore_or_raise, and _verify_patch_applied)
            if kwargs.get("plural") == "restores" and kwargs.get("name") == RESTORE_PASSIVE_SYNC_NAME:
                resource_version = "101" if patch_applied["value"] else "100"
                spec = {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True}
                if patch_applied["value"]:
                    spec[SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME] = VELERO_BACKUP_LATEST

                return {
                    "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME, "resourceVersion": resource_version},
                    "status": {
                        "phase": "Completed",
                        "lastMessage": "Initial sync complete",
                        "veleroManagedClustersRestoreName": "test-velero-restore",
                    },
                    "spec": spec,
                }

            # Velero Restore (defensive: only needed if wait_for_condition calls the poller)
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "velero.io":
                return {
                    "status": {
                        "phase": "Completed",
                        "progress": {"itemsRestored": 100},
                    }
                }

            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect

        def patch_custom_resource_side_effect(**kwargs):
            patch_applied["value"] = True
            return {
                "spec": {
                    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST,
                }
            }

        mock_secondary_client.patch_custom_resource.side_effect = patch_custom_resource_side_effect

        result = activation_passive.activate()

        assert result is True

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
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
            "status": {"phase": "Failed"},
        }

        result = activation_passive.activate()

        assert result is False

    @patch("modules.activation.wait_for_condition")
    def test_activate_already_activated_idempotent(
        self, mock_wait, activation_passive, mock_secondary_client, mock_state_manager
    ):
        """Test that activation is idempotent when already activated.

        If veleroManagedClustersBackupName is already 'latest', the tool should
        skip the patch and proceed without errors. This handles resume scenarios
        where activation was previously completed.
        """
        mock_wait.return_value = True

        # Mock restore already activated (veleroManagedClustersBackupName = latest)
        def get_custom_resource_side_effect(**kwargs):
            if kwargs.get("plural") == "restores" and kwargs.get("name") == RESTORE_PASSIVE_SYNC_NAME:
                return {
                    "metadata": {
                        "name": RESTORE_PASSIVE_SYNC_NAME,
                        "resourceVersion": "100",
                    },
                    "status": {
                        "phase": "Finished",  # Already completed
                        "lastMessage": "All Velero restores have run successfully",
                    },
                    "spec": {
                        SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True,
                        SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST,  # Already set!
                    },
                }
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "velero.io":
                return {"status": {"phase": "Completed"}}
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect

        # Mock list for restore discovery
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]

        result = activation_passive.activate()

        assert result is True

        # Patch should NOT have been called since value was already correct
        mock_secondary_client.patch_custom_resource.assert_not_called()

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

        # Make activation idempotent by indicating managed clusters backup is already activated
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
            "status": {"phase": "Enabled"},
            "spec": {
                SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True,
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST,
            },
        }

        result = activation_passive.activate()

        assert result is False

    def test_poll_restore_logic(self, activation_passive, mock_secondary_client):
        """Test the internal _poll_restore logic via _wait_for_restore_completion."""
        # Mock get_custom_resource to return appropriate values for different resources
        call_count = [0]

        def get_custom_resource_side_effect(**kwargs):
            call_count[0] += 1
            if kwargs.get("plural") == "restores" and kwargs.get("group") == "cluster.open-cluster-management.io":
                return {
                    "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
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

        # Mock list_custom_resources for managed clusters
        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": "cluster1"}},
            {"metadata": {"name": "local-cluster"}},
        ]

        with patch("modules.activation.wait_for_condition") as mock_wait:
            # Define side effect to execute the callback passed to wait_for_condition
            def side_effect(desc, callback, **kwargs):
                return callback()[0]  # Execute callback and return done status

            mock_wait.side_effect = side_effect

            # We need to bypass the earlier steps to get to wait
            activation_passive.state.is_step_completed.side_effect = lambda step: step != "wait_restore_completion"

            activation_passive._wait_for_restore_completion()

            # Verify get_custom_resource was called by the callback
            assert mock_secondary_client.get_custom_resource.called

    @patch("modules.activation.wait_for_condition")
    def test_activate_passive_restore_method(self, mock_wait, mock_secondary_client, mock_state_manager):
        """Test passive activation using restore-acm-activate (Option B)."""
        mock_wait.return_value = True
        mock_state_manager.get_config.return_value = "2.13.0"

        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
            activation_method="restore",
        )

        mock_secondary_client.list_custom_resources.return_value = [
            {"metadata": {"name": RESTORE_PASSIVE_SYNC_NAME}, "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True}}
        ]

        def get_custom_resource_side_effect(**kwargs):
            name = kwargs.get("name")
            if name == RESTORE_PASSIVE_SYNC_NAME:
                return {"metadata": {"name": name}, "status": {"phase": "Enabled"}}
            if name == MANAGED_CLUSTER_RESTORE_NAME:
                return None
            return None

        mock_secondary_client.get_custom_resource.side_effect = get_custom_resource_side_effect

        with patch.object(activation, "_wait_for_managed_clusters_velero_restore") as mock_wait_mc:
            result = activation.activate()

        assert result is True
        mock_secondary_client.delete_custom_resource.assert_called_once()
        mock_secondary_client.create_custom_resource.assert_called_once()
        body = mock_secondary_client.create_custom_resource.call_args.kwargs["body"]
        assert body["metadata"]["name"] == MANAGED_CLUSTER_RESTORE_NAME
        assert body["spec"][SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME] == VELERO_BACKUP_LATEST
        assert body["spec"]["veleroCredentialsBackupName"] == VELERO_BACKUP_SKIP
        assert body["spec"]["veleroResourcesBackupName"] == VELERO_BACKUP_SKIP
        mock_wait_mc.assert_called_once()

    def test_apply_immediate_import_annotations(self, mock_secondary_client, mock_state_manager):
        """Test immediate-import annotation application under ImportOnly."""
        mock_state_manager.get_config.return_value = "2.14.0"
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap.return_value = None
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

    def test_apply_immediate_import_annotations_handles_completed_value(
        self, mock_secondary_client, mock_state_manager
    ):
        """Ensure Completed annotations are reset via delete-and-add."""
        mock_state_manager.get_config.return_value = "2.14.1"
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap.return_value = {"data": {}}
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
        mock_state_manager.get_config.return_value = "2.14.2"
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap.return_value = {
            "data": {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC}
        }

        activation._apply_immediate_import_annotations()

        mock_secondary_client.list_custom_resources.assert_not_called()
        mock_secondary_client.patch_managed_cluster.assert_not_called()

    def test_apply_immediate_import_annotations_raises_on_failures(self, mock_secondary_client, mock_state_manager):
        """Ensure failures raise and do not mark the step completed."""
        mock_state_manager.get_config.return_value = "2.14.0"
        activation = SecondaryActivation(
            secondary_client=mock_secondary_client,
            state_manager=mock_state_manager,
            method="passive",
        )

        mock_secondary_client.get_configmap.return_value = None
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
class TestFindPassiveSyncRestore:
    """Tests for the find_passive_sync_restore discovery function."""

    def test_find_by_sync_restore_with_new_backups(self, mock_secondary_client):
        """Test discovery finds restore by syncRestoreWithNewBackups=true."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "my-custom-restore"},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            }
        ]
        mock_secondary_client.get_custom_resource.return_value = None

        result = activation_module.find_passive_sync_restore(mock_secondary_client)

        assert result is not None
        assert result["metadata"]["name"] == "my-custom-restore"
        # Should not have called get_custom_resource since found via list
        mock_secondary_client.get_custom_resource.assert_not_called()

    def test_find_fallback_to_well_known_name(self, mock_secondary_client):
        """Test discovery falls back to well-known name if no syncRestoreWithNewBackups."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "some-other-restore"},
                "spec": {"someOtherField": True},
            }
        ]
        mock_secondary_client.get_custom_resource.return_value = {
            "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
            "spec": {},
        }

        result = activation_module.find_passive_sync_restore(mock_secondary_client)

        assert result is not None
        assert result["metadata"]["name"] == RESTORE_PASSIVE_SYNC_NAME
        mock_secondary_client.get_custom_resource.assert_called_once()

    def test_find_no_restore_found(self, mock_secondary_client):
        """Test discovery returns None when no restore found."""
        mock_secondary_client.list_custom_resources.return_value = []
        mock_secondary_client.get_custom_resource.return_value = None

        result = activation_module.find_passive_sync_restore(mock_secondary_client)

        assert result is None

    def test_find_prefers_sync_restore_over_well_known(self, mock_secondary_client):
        """Test that syncRestoreWithNewBackups is preferred over well-known name."""
        mock_secondary_client.list_custom_resources.return_value = [
            {
                "metadata": {"name": "custom-passive-sync"},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True},
            },
            {
                "metadata": {"name": RESTORE_PASSIVE_SYNC_NAME},
                "spec": {SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: False},
            },
        ]

        result = activation_module.find_passive_sync_restore(mock_secondary_client)

        assert result is not None
        assert result["metadata"]["name"] == "custom-passive-sync"
        # Should not have tried fallback
        mock_secondary_client.get_custom_resource.assert_not_called()
