"""
Secondary hub activation module for ACM switchover.
"""

import logging

from lib.constants import BACKUP_NAMESPACE, RESTORE_POLL_INTERVAL, RESTORE_WAIT_TIMEOUT
from lib.kube_client import KubeClient
from lib.utils import StateManager
from lib.waiter import wait_for_condition

logger = logging.getLogger("acm_switchover")


class SecondaryActivation:
    """Handles activation steps on secondary hub."""

    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        method: str = "passive",
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.method = method

    def activate(self) -> bool:
        """
        Execute activation on secondary hub.

        Returns:
            True if activation completed successfully
        """
        logger.info(f"Starting secondary hub activation (method: {self.method})...")

        try:
            if self.method == "passive":
                # Method 1: Continuous Passive Restore
                if not self.state.is_step_completed("verify_passive_sync"):
                    self._verify_passive_sync()
                    self.state.mark_step_completed("verify_passive_sync")
                else:
                    logger.info("Step already completed: verify_passive_sync")

                if not self.state.is_step_completed("activate_managed_clusters"):
                    self._activate_via_passive_sync()
                    self.state.mark_step_completed("activate_managed_clusters")
                else:
                    logger.info("Step already completed: activate_managed_clusters")
            else:
                # Method 2: One-Time Full Restore
                if not self.state.is_step_completed("create_full_restore"):
                    self._create_full_restore()
                    self.state.mark_step_completed("create_full_restore")
                else:
                    logger.info("Step already completed: create_full_restore")

            # Wait for restore to complete
            if not self.state.is_step_completed("wait_restore_completion"):
                self._wait_for_restore_completion()
                self.state.mark_step_completed("wait_restore_completion")
            else:
                logger.info("Step already completed: wait_restore_completion")

            logger.info("Secondary hub activation completed successfully")
            return True

        except Exception as e:
            logger.error(f"Secondary hub activation failed: {e}")
            self.state.add_error(str(e), "activation")
            return False

    def _verify_passive_sync(self):
        """Verify passive sync restore is up-to-date."""
        logger.info("Verifying passive sync restore status...")

        restore = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            namespace=BACKUP_NAMESPACE,
        )

        if not restore:
            raise Exception("restore-acm-passive-sync not found on secondary hub")

        status = restore.get("status", {})
        phase = status.get("phase", "unknown")
        message = status.get("lastMessage", "")

        if phase != "Enabled":
            raise Exception(
                f"Passive sync restore not in Enabled state: {phase} - {message}"
            )

        logger.info(f"Passive sync verified: {message}")

    def _activate_via_passive_sync(self):
        """Activate managed clusters by patching passive sync restore."""
        logger.info("Activating managed clusters via passive sync...")

        # Patch existing restore-acm-passive-sync with veleroManagedClustersBackupName: latest
        patch = {"spec": {"veleroManagedClustersBackupName": "latest"}}

        self.secondary.patch_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            patch=patch,
            namespace=BACKUP_NAMESPACE,
        )

        logger.info("Patched restore-acm-passive-sync to activate managed clusters")

    def _create_full_restore(self):
        """Create full restore resource (Method 2)."""
        logger.info("Creating full restore resource...")

        # Check if restore already exists
        existing_restore = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-full",
            namespace=BACKUP_NAMESPACE,
        )

        if existing_restore:
            logger.info("restore-acm-full already exists")
            return

        # Create restore resource
        restore_body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {
                "name": "restore-acm-full",
                "namespace": BACKUP_NAMESPACE,
            },
            "spec": {
                "veleroManagedClustersBackupName": "latest",
                "veleroCredentialsBackupName": "latest",
                "veleroResourcesBackupName": "latest",
                "cleanupBeforeRestore": "CleanupRestored",
            },
        }

        self.secondary.create_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            body=restore_body,
            namespace=BACKUP_NAMESPACE,
        )

        logger.info("Created restore-acm-full resource")

    def _wait_for_restore_completion(self, timeout: int = RESTORE_WAIT_TIMEOUT):
        """Wait for restore to complete."""

        restore_name = (
            "restore-acm-passive-sync"
            if self.method == "passive"
            else "restore-acm-full"
        )

        def _poll_restore():
            restore = self.secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name=restore_name,
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                raise Exception(f"Restore {restore_name} disappeared during wait")

            status = restore.get("status", {})
            phase = status.get("phase", "unknown")
            message = status.get("lastMessage", "")

            if phase == "Finished":
                return True, message or "restore finished"
            if phase in ("Failed", "PartiallyFailed"):
                raise Exception(f"Restore failed: {phase} - {message}")

            return False, f"phase={phase} message={message}"

        completed = wait_for_condition(
            f"restore {restore_name}",
            _poll_restore,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            logger=logger,
        )

        if not completed:
            raise Exception(f"Timeout waiting for restore to complete after {timeout}s")
