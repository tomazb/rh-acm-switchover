"""Cross-module contract tests for switchover state handoff."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import argocd as argocd_lib
from lib.constants import (
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_NAMESPACE,
    IMPORT_CONTROLLER_CONFIG_CM,
    MCE_NAMESPACE,
)
from lib.utils import StateManager
from modules.activation import SecondaryActivation
from modules.backup_schedule import BackupScheduleManager
from modules.finalization import Finalization
from modules.primary_prep import PrimaryPreparation


@pytest.fixture
def state_file(tmp_path):
    """Path for a real StateManager state file."""
    return str(tmp_path / "state-handoff.json")


@pytest.fixture
def primary_client():
    """Create a mock primary hub client."""
    return Mock(name="primary-client")


@pytest.fixture
def secondary_client():
    """Create a mock secondary hub client."""
    return Mock(name="secondary-client")


@pytest.mark.integration
class TestStateHandoffContracts:
    """Verify real producers and consumers share state through StateManager."""

    def test_primary_prep_records_pause_state_consumed_by_finalization(
        self, state_file, primary_client, secondary_client
    ):
        """Primary prep pause state should be replayed by finalization resume."""
        producer_state = StateManager(state_file)
        prep = PrimaryPreparation(
            primary_client=primary_client,
            state_manager=producer_state,
            acm_version="2.12.0",
            has_observability=False,
            argocd_manage=True,
        )

        app = {
            "metadata": {"namespace": "openshift-gitops", "name": "acm-restore"},
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
            "status": {"resources": [{"kind": "Restore", "namespace": BACKUP_NAMESPACE}]},
        }
        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        impacts = [
            argocd_lib.AppImpact(
                namespace="openshift-gitops",
                name="acm-restore",
                resource_count=1,
                app=app,
            )
        ]

        with (
            patch("lib.argocd_coordinator.argocd_lib.detect_argocd_installation", return_value=discovery),
            patch("lib.argocd_coordinator.argocd_lib.list_argocd_applications", return_value=[app]),
            patch("lib.argocd_coordinator.argocd_lib.find_acm_touching_apps", return_value=impacts),
            patch("lib.argocd_coordinator.argocd_lib.run_id_or_new", return_value="run-123"),
            patch("lib.argocd_coordinator.argocd_lib.pause_autosync") as pause_autosync,
        ):
            pause_autosync.return_value = argocd_lib.PauseResult(
                namespace="openshift-gitops",
                name="acm-restore",
                original_sync_policy={"automated": {"prune": True}},
                patched=True,
            )

            prep._pause_argocd_acm_apps()

        consumer_state = StateManager(state_file)
        finalization = Finalization(
            secondary_client=secondary_client,
            state_manager=consumer_state,
            acm_version="2.12.0",
            primary_client=primary_client,
        )

        with patch("modules.finalization.argocd_lib.resume_recorded_applications") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=1, already_resumed=0, failed=0)

            finalization._resume_argocd_apps()

        paused_apps = consumer_state.get_config("argocd_paused_apps")
        assert consumer_state.get_config("argocd_run_id") == "run-123"
        assert consumer_state.get_config("argocd_pause_dry_run", None) is False
        assert paused_apps == [
            {
                "hub": "primary",
                "namespace": "openshift-gitops",
                "name": "acm-restore",
                "original_sync_policy": {"automated": {"prune": True}},
                "pause_applied": True,
            }
        ]

        resume_recorded.assert_called_once()
        recorded_apps, run_id, recorded_primary, recorded_secondary, _ = resume_recorded.call_args.args
        assert recorded_apps == paused_apps
        assert run_id == "run-123"
        assert recorded_primary is primary_client
        assert recorded_secondary is secondary_client

    def test_activation_flag_causes_finalization_to_delete_import_configmap(self, state_file, secondary_client):
        """Activation-owned auto-import state should trigger the finalization cleanup."""
        producer_state = StateManager(state_file)
        producer_state.set_config("secondary_version", "2.14.1")
        activation = SecondaryActivation(
            secondary_client=secondary_client,
            state_manager=producer_state,
            method="passive",
            manage_auto_import_strategy=True,
        )

        secondary_client.list_custom_resources.return_value = [{"metadata": {"name": "cluster-a"}}]
        secondary_client.get_configmap.side_effect = [
            None,
            {"data": {AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC}},
        ]

        activation._maybe_set_auto_import_strategy()

        consumer_state = StateManager(state_file)
        finalization = Finalization(
            secondary_client=secondary_client,
            state_manager=consumer_state,
            acm_version="2.14.0",
        )

        assert finalization._ensure_auto_import_default() is True

        secondary_client.create_or_patch_configmap.assert_called_once_with(
            namespace=MCE_NAMESPACE,
            name=IMPORT_CONTROLLER_CONFIG_CM,
            data={AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC},
        )
        secondary_client.delete_configmap.assert_called_once_with(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)
        assert consumer_state.get_config("auto_import_strategy_set", False) is False

    def test_saved_backup_schedule_is_restored_from_state(self, state_file, primary_client, secondary_client):
        """BackupScheduleManager should recreate the saved primary schedule from state."""
        schedule = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "BackupSchedule",
            "metadata": {
                "name": "schedule-acm",
                "namespace": BACKUP_NAMESPACE,
                "resourceVersion": "12345",
                "uid": "backup-schedule-uid",
            },
            "spec": {
                "veleroSchedule": "0 */6 * * *",
                "veleroTtl": "120h",
                "paused": False,
            },
            "status": {"phase": "Enabled"},
        }

        producer_state = StateManager(state_file)
        prep = PrimaryPreparation(
            primary_client=primary_client,
            state_manager=producer_state,
            acm_version="2.12.0",
            has_observability=False,
        )
        primary_client.list_custom_resources.return_value = [schedule]

        prep._pause_backup_schedule()

        consumer_state = StateManager(state_file)
        manager = BackupScheduleManager(
            kube_client=secondary_client,
            state_manager=consumer_state,
            hub_label="secondary",
        )

        manager._restore_saved_schedule()

        primary_client.patch_custom_resource.assert_called_once()
        secondary_client.create_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            body={
                "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                "kind": "BackupSchedule",
                "metadata": {
                    "name": "schedule-acm",
                    "namespace": BACKUP_NAMESPACE,
                },
                "spec": {
                    "veleroSchedule": "0 */6 * * *",
                    "veleroTtl": "120h",
                    "paused": False,
                },
            },
            namespace=BACKUP_NAMESPACE,
        )
