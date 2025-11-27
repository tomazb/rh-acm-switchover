"""
Secondary hub activation module for ACM switchover.
"""

import logging
import time

from lib.constants import BACKUP_NAMESPACE, RESTORE_POLL_INTERVAL, RESTORE_WAIT_TIMEOUT
from lib.exceptions import FatalError, SwitchoverError, TransientError
from lib.kube_client import KubeClient
from lib.utils import StateManager
from lib.waiter import wait_for_condition

logger = logging.getLogger("acm_switchover")

# Minimum number of ManagedClusters expected (excluding local-cluster)
# Set to 0 to allow switchover with only local-cluster
MIN_MANAGED_CLUSTERS = 0


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

        except SwitchoverError as e:
            logger.error(f"Secondary hub activation failed: {e}")
            self.state.add_error(str(e), "activation")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during activation: {e}")
            self.state.add_error(f"Unexpected: {str(e)}", "activation")
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
            raise FatalError("restore-acm-passive-sync not found on secondary hub")

        status = restore.get("status", {})
        phase = status.get("phase", "unknown")
        message = status.get("lastMessage", "")

        # "Enabled" = continuous sync running
        # "Finished" = initial sync completed successfully (also valid for activation)
        if phase not in ("Enabled", "Finished"):
            raise FatalError(f"Passive sync restore not ready: {phase} - {message}")

        logger.info(f"Passive sync verified ({phase}): {message}")

    def _activate_via_passive_sync(self):
        """Activate managed clusters by patching passive sync restore."""
        logger.info("Activating managed clusters via passive sync...")

        # First, get current state of the restore
        restore_before = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            namespace=BACKUP_NAMESPACE,
        )
        
        if restore_before:
            current_mc_backup = restore_before.get("spec", {}).get("veleroManagedClustersBackupName", "<not set>")
            logger.info(f"BEFORE PATCH: veleroManagedClustersBackupName = {current_mc_backup}")
            logger.debug(f"BEFORE PATCH: Full spec = {restore_before.get('spec', {})}")
        else:
            logger.error("BEFORE PATCH: restore-acm-passive-sync not found!")
            raise FatalError("restore-acm-passive-sync not found before patching")

        # Patch existing restore-acm-passive-sync with veleroManagedClustersBackupName: latest
        patch = {"spec": {"veleroManagedClustersBackupName": "latest"}}
        logger.info(f"PATCHING: Applying patch = {patch}")

        result = self.secondary.patch_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            patch=patch,
            namespace=BACKUP_NAMESPACE,
        )
        
        logger.info(f"PATCH RESULT: patch_custom_resource returned type={type(result).__name__}")
        if result:
            result_mc_backup = result.get("spec", {}).get("veleroManagedClustersBackupName", "<not set>")
            logger.info(f"PATCH RESULT: veleroManagedClustersBackupName in response = {result_mc_backup}")
            logger.debug(f"PATCH RESULT: Full spec in response = {result.get('spec', {})}")
        else:
            logger.warning("PATCH RESULT: patch_custom_resource returned empty/None result")

        # Skip verification in dry-run mode since the patch wasn't actually applied
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Skipping patch verification (patch was not applied)")
            logger.info("Patched restore-acm-passive-sync to activate managed clusters")
            return

        # Verify the patch was actually applied by re-reading the resource
        time.sleep(1)  # Brief pause to allow API to sync
        
        restore_after = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-passive-sync",
            namespace=BACKUP_NAMESPACE,
        )
        
        if restore_after:
            after_mc_backup = restore_after.get("spec", {}).get("veleroManagedClustersBackupName", "<not set>")
            logger.info(f"AFTER PATCH (re-read): veleroManagedClustersBackupName = {after_mc_backup}")
            logger.debug(f"AFTER PATCH (re-read): Full spec = {restore_after.get('spec', {})}")
            
            if after_mc_backup != "latest":
                logger.error(f"PATCH VERIFICATION FAILED: Expected 'latest', got '{after_mc_backup}'")
                raise FatalError(
                    f"Patch verification failed: veleroManagedClustersBackupName is '{after_mc_backup}', "
                    f"expected 'latest'. The patch may not have been applied correctly."
                )
            else:
                logger.info("PATCH VERIFICATION SUCCESS: veleroManagedClustersBackupName is now 'latest'")
        else:
            logger.error("AFTER PATCH: restore-acm-passive-sync not found after patching!")
            raise FatalError("restore-acm-passive-sync disappeared after patching")

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
        """Wait for restore to complete and verify managed clusters are restored."""

        restore_name = "restore-acm-passive-sync" if self.method == "passive" else "restore-acm-full"

        def _poll_restore():
            restore = self.secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name=restore_name,
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                raise FatalError(f"Restore {restore_name} disappeared during wait")

            status = restore.get("status", {})
            phase = status.get("phase", "unknown")
            message = status.get("lastMessage", "")

            # For passive sync, "Enabled" means the restore is actively syncing - this is the success state
            # For full restore, "Finished" means the restore completed
            if self.method == "passive" and phase == "Enabled":
                return True, message or "passive sync enabled and running"
            if phase == "Finished":
                return True, message or "restore finished"
            if phase in ("Failed", "PartiallyFailed"):
                raise FatalError(f"Restore failed: {phase} - {message}")

            return False, f"phase={phase} message={message}"

        completed = wait_for_condition(
            f"restore {restore_name}",
            _poll_restore,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            logger=logger,
        )

        if not completed:
            raise FatalError(f"Timeout waiting for restore to complete after {timeout}s")

        # For passive sync, wait for the managed clusters Velero restore to actually complete
        if self.method == "passive":
            # Skip waiting for Velero restore in dry-run mode since the patch wasn't applied
            if self.secondary.dry_run:
                logger.info("[DRY-RUN] Skipping wait for Velero managed clusters restore")
            else:
                self._wait_for_managed_clusters_velero_restore(timeout)

    def _wait_for_managed_clusters_velero_restore(self, timeout: int = 300):
        """
        Wait for the Velero managed clusters restore to complete.
        
        This is critical because:
        1. The ACM restore controller patches the passive sync restore
        2. This triggers creation of a Velero restore for managed clusters
        3. The Velero restore needs to complete before ManagedCluster resources exist
        4. Only after this can we safely create a BackupSchedule
        
        If we create a BackupSchedule before the managed clusters restore completes,
        the new backups won't contain the managed clusters!
        """
        logger.info("Waiting for managed clusters Velero restore to complete...")

        def _poll_velero_restore():
            # Get the ACM restore to find the Velero restore name
            restore = self.secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name="restore-acm-passive-sync",
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                return False, "ACM restore not found"

            status = restore.get("status", {})
            velero_mc_restore_name = status.get("veleroManagedClustersRestoreName")

            if not velero_mc_restore_name:
                return False, "Velero managed clusters restore not yet created"

            # Check the Velero restore status
            velero_restore = self.secondary.get_custom_resource(
                group="velero.io",
                version="v1",
                plural="restores",
                name=velero_mc_restore_name,
                namespace=BACKUP_NAMESPACE,
            )

            if not velero_restore:
                return False, f"Velero restore {velero_mc_restore_name} not found"

            velero_phase = velero_restore.get("status", {}).get("phase", "unknown")

            if velero_phase == "Completed":
                items_restored = velero_restore.get("status", {}).get("progress", {}).get("itemsRestored", 0)
                logger.info(f"Velero managed clusters restore completed: {items_restored} items restored")
                return True, f"completed ({items_restored} items)"
            if velero_phase in ("Failed", "PartiallyFailed"):
                raise FatalError(f"Velero managed clusters restore failed: {velero_phase}")

            return False, f"Velero restore phase: {velero_phase}"

        completed = wait_for_condition(
            "Velero managed clusters restore",
            _poll_velero_restore,
            timeout=timeout,
            interval=RESTORE_POLL_INTERVAL,
            logger=logger,
        )

        if not completed:
            raise FatalError(f"Timeout waiting for Velero managed clusters restore after {timeout}s")

        # Verify ManagedCluster resources actually exist
        self._verify_managed_clusters_restored()

    def _verify_managed_clusters_restored(self):
        """
        Verify that ManagedCluster resources were actually restored.
        
        This is a sanity check to ensure the restore actually brought over
        the managed clusters before we proceed with creating a BackupSchedule.
        """
        logger.info("Verifying ManagedCluster resources were restored...")

        managed_clusters = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
        )

        # Count non-local clusters
        non_local_clusters = [
            mc.get("metadata", {}).get("name")
            for mc in managed_clusters
            if mc.get("metadata", {}).get("name") != "local-cluster"
        ]

        if len(non_local_clusters) >= MIN_MANAGED_CLUSTERS:
            logger.info(
                f"Found {len(non_local_clusters)} ManagedCluster(s) on secondary hub: "
                f"{', '.join(non_local_clusters) if non_local_clusters else '(none)'}"
            )
        else:
            if MIN_MANAGED_CLUSTERS > 0:
                raise FatalError(
                    f"Expected at least {MIN_MANAGED_CLUSTERS} ManagedCluster(s) after restore, "
                    f"but found only {len(non_local_clusters)}: {non_local_clusters}"
                )
            else:
                logger.warning(
                    "No non-local ManagedClusters found after restore. "
                    "This may be expected if no clusters were imported on the primary hub."
                )
