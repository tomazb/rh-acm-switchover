"""
Secondary hub activation module for ACM switchover.
"""

import logging
import time
from typing import Dict, Optional

from lib.constants import (
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_NAMESPACE,
    IMPORT_CONTROLLER_CONFIGMAP,
    LOCAL_CLUSTER_NAME,
    MCE_NAMESPACE,
    PATCH_VERIFY_MAX_RETRIES,
    PATCH_VERIFY_RETRY_DELAY,
    RESTORE_FULL_NAME,
    RESTORE_PASSIVE_SYNC_NAME,
    RESTORE_POLL_INTERVAL,
    RESTORE_WAIT_TIMEOUT,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
    VELERO_BACKUP_LATEST,
)
from lib.exceptions import FatalError, SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import StateManager, is_acm_version_ge
from lib.waiter import wait_for_condition

logger = logging.getLogger("acm_switchover")

# Minimum number of ManagedClusters expected (excluding local-cluster)
# Set to 0 to allow switchover with only local-cluster
MIN_MANAGED_CLUSTERS = 0


def find_passive_sync_restore(client: KubeClient, namespace: str = BACKUP_NAMESPACE) -> Optional[Dict]:
    """
    Find an existing passive sync restore on the cluster.

    A passive sync restore is identified by spec.syncRestoreWithNewBackups = true.
    This works both before activation (when veleroManagedClustersBackupName is 'skip')
    and after activation (when it's 'latest').

    Args:
        client: KubeClient for the cluster
        namespace: Namespace to search in (default: open-cluster-management-backup)

    Returns:
        The restore resource dict if found, None otherwise
    """
    restores = client.list_custom_resources(
        group="cluster.open-cluster-management.io",
        version="v1beta1",
        plural="restores",
        namespace=namespace,
    )

    for restore in restores:
        spec = restore.get("spec", {})
        # Primary identifier: syncRestoreWithNewBackups = true
        if spec.get(SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS) is True:
            name = restore.get("metadata", {}).get("name", "unknown")
            logger.debug("Found passive sync restore: %s", name)
            return restore

    # Fallback: check for well-known name (backward compatibility)
    fallback = client.get_custom_resource(
        group="cluster.open-cluster-management.io",
        version="v1beta1",
        plural="restores",
        name=RESTORE_PASSIVE_SYNC_NAME,
        namespace=namespace,
    )
    if fallback:
        logger.debug("Found passive sync restore by fallback name: %s", RESTORE_PASSIVE_SYNC_NAME)
    return fallback


class SecondaryActivation:
    """Handles activation steps on secondary hub."""

    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        method: str = "passive",
        manage_auto_import_strategy: bool = False,
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.method = method
        self.manage_auto_import_strategy = manage_auto_import_strategy
        # Cache for discovered passive sync restore name
        self._passive_sync_restore_name: Optional[str] = None

    def _get_passive_sync_restore_name(self) -> str:
        """
        Get the name of the passive sync restore, discovering it if needed.

        This uses cached value if available, otherwise discovers the restore
        by looking for one with syncRestoreWithNewBackups = true.

        Returns:
            The name of the passive sync restore

        Raises:
            FatalError: If no passive sync restore is found
        """
        if self._passive_sync_restore_name:
            return self._passive_sync_restore_name

        restore = find_passive_sync_restore(self.secondary, BACKUP_NAMESPACE)
        if not restore:
            raise FatalError(
                f"No passive sync restore found on secondary hub. "
                f"Expected either a restore with spec.{SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS}=true "
                f"or a restore named '{RESTORE_PASSIVE_SYNC_NAME}'."
            )

        restore_name = restore.get("metadata", {}).get("name")
        if not restore_name:
            raise FatalError("Passive sync restore missing metadata.name")
        self._passive_sync_restore_name = restore_name
        logger.info("Discovered passive sync restore: %s", self._passive_sync_restore_name)
        return self._passive_sync_restore_name

    def activate(self) -> bool:
        """
        Execute activation on secondary hub.

        Returns:
            True if activation completed successfully
        """
        logger.info("Starting secondary hub activation (method: %s)...", self.method)

        try:
            if self.method == "passive":
                # Method 1: Continuous Passive Restore
                if not self.state.is_step_completed("verify_passive_sync"):
                    self._verify_passive_sync()
                    self.state.mark_step_completed("verify_passive_sync")
                else:
                    logger.info("Step already completed: verify_passive_sync")

                # Optional: set ImportAndSync before activation when applicable
                self._maybe_set_auto_import_strategy()

                if not self.state.is_step_completed("activate_managed_clusters"):
                    self._activate_via_passive_sync()
                    self.state.mark_step_completed("activate_managed_clusters")
                else:
                    logger.info("Step already completed: activate_managed_clusters")
            else:
                # Method 2: One-Time Full Restore
                # Optional: set ImportAndSync pre-restore when applicable
                self._maybe_set_auto_import_strategy()
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
            logger.error("Secondary hub activation failed: %s", e)
            self.state.add_error(str(e), "activation")
            return False
        except (RuntimeError, ValueError) as e:
            logger.error("Unexpected error during activation: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", "activation")
            return False
        except Exception as e:
            # Log programming errors but re-raise so they're not hidden
            logger.error("Programming error during activation: %s: %s", type(e).__name__, e)
            raise

    def _verify_passive_sync(self):
        """Verify passive sync restore is up-to-date."""
        logger.info("Verifying passive sync restore status...")

        restore_name = self._get_passive_sync_restore_name()
        restore = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name=restore_name,
            namespace=BACKUP_NAMESPACE,
        )

        if not restore:
            raise FatalError(f"{restore_name} not found on secondary hub")

        status = restore.get("status", {})
        phase = status.get("phase", "unknown")
        message = status.get("lastMessage", "")

        # "Enabled" = continuous sync running
        # "Finished" = initial sync completed successfully (also valid for activation)
        if phase not in ("Enabled", "Finished"):
            raise FatalError(f"Passive sync restore not ready: {phase} - {message}")

        logger.info("Passive sync verified (%s): %s", phase, message)

    def _activate_via_passive_sync(self):
        """Activate managed clusters by patching passive sync restore."""
        logger.info("Activating managed clusters via passive sync...")

        restore_name = self._get_passive_sync_restore_name()
        restore_before = self._get_restore_or_raise(restore_name)

        if self._activation_already_applied(restore_before):
            return

        patch = self._build_activation_patch()
        logger.info("PATCHING: Applying patch = %s", patch)

        result = self.secondary.patch_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name=restore_name,
            patch=patch,
            namespace=BACKUP_NAMESPACE,
        )

        self._log_patch_result(result)

        # Skip verification in dry-run mode since the patch wasn't actually applied
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Skipping patch verification (patch was not applied)")
            logger.info("Patched %s to activate managed clusters", restore_name)
            return

        # Verify patch was applied correctly
        self._verify_patch_applied(restore_name, restore_before)

    def _get_restore_or_raise(self, restore_name: str) -> Dict:
        """Fetch restore resource or raise a fatal error if missing."""
        restore = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name=restore_name,
            namespace=BACKUP_NAMESPACE,
        )

        if not restore:
            logger.error("BEFORE PATCH: %s not found!", restore_name)
            raise FatalError(f"{restore_name} not found before patching")

        return restore

    @staticmethod
    def _build_activation_patch() -> Dict[str, Dict[str, str]]:
        """Build patch payload for activating managed clusters."""
        return {"spec": {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST}}

    def _activation_already_applied(self, restore: Dict) -> bool:
        """Check if activation already applied and log current state."""
        current_mc_backup = restore.get("spec", {}).get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME, "<not set>")
        logger.info(
            "BEFORE PATCH: %s = %s",
            SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
            current_mc_backup,
        )
        logger.debug("BEFORE PATCH: Full spec = %s", restore.get("spec", {}))

        if current_mc_backup == VELERO_BACKUP_LATEST:
            logger.info(
                "%s is already set to '%s' - activation already applied (idempotent)",
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
                VELERO_BACKUP_LATEST,
            )
            return True
        return False

    def _log_patch_result(self, result: Optional[Dict]) -> None:
        """Log patch response details."""
        logger.info(
            "PATCH RESULT: patch_custom_resource returned type=%s",
            type(result).__name__,
        )
        if result:
            result_mc_backup = result.get("spec", {}).get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME, "<not set>")
            logger.info(
                "PATCH RESULT: %s in response = %s",
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
                result_mc_backup,
            )
            logger.debug("PATCH RESULT: Full spec in response = %s", result.get("spec", {}))
        else:
            logger.warning("PATCH RESULT: patch_custom_resource returned empty/None result")

    def _verify_patch_applied(self, restore_name: str, restore_before: Dict) -> None:
        """Verify that a patch was applied correctly by checking resourceVersion changes.

        Args:
            restore_name: Name of the restore resource
            restore_before: Restore resource state before patching (for resourceVersion comparison)
        """
        # Get resourceVersion from before patch for comparison
        before_resource_version = restore_before.get("metadata", {}).get("resourceVersion", "")

        # Track whether we've seen version changes
        seen_version_change = False

        # Verify patch with retry loop and resourceVersion comparison
        # This handles API sync delays more robustly than a single sleep
        for attempt in range(1, PATCH_VERIFY_MAX_RETRIES + 1):
            time.sleep(PATCH_VERIFY_RETRY_DELAY)

            restore_after = self.secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name=restore_name,
                namespace=BACKUP_NAMESPACE,
            )

            if not restore_after:
                logger.error("AFTER PATCH: %s not found after patching!", restore_name)
                raise FatalError(f"{restore_name} disappeared after patching")

            after_resource_version = restore_after.get("metadata", {}).get("resourceVersion", "")
            after_mc_backup = restore_after.get("spec", {}).get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME, "<not set>")

            logger.debug(
                "Patch verify attempt %d/%d: resourceVersion %s -> %s, %s = %s",
                attempt,
                PATCH_VERIFY_MAX_RETRIES,
                before_resource_version,
                after_resource_version,
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
                after_mc_backup,
            )

            # Check if resourceVersion changed (patch was processed)
            if after_resource_version != before_resource_version:
                seen_version_change = True
                if after_mc_backup == VELERO_BACKUP_LATEST:
                    logger.info(
                        "AFTER PATCH (re-read): %s = %s",
                        SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
                        after_mc_backup,
                    )
                    logger.info(
                        "PATCH VERIFICATION SUCCESS: %s is now '%s' (resourceVersion: %s -> %s)",
                        SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
                        VELERO_BACKUP_LATEST,
                        before_resource_version,
                        after_resource_version,
                    )
                    return  # Success!
                else:
                    # resourceVersion changed but value is wrong
                    logger.error(
                        "PATCH VERIFICATION FAILED: Expected '%s', got '%s'",
                        VELERO_BACKUP_LATEST,
                        after_mc_backup,
                    )
                    raise FatalError(
                        f"Patch verification failed: {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME} is '{after_mc_backup}', "
                        f"expected '{VELERO_BACKUP_LATEST}'. The patch may not have been applied correctly."
                    )

            # resourceVersion hasn't changed yet, continue retry loop
            if attempt < PATCH_VERIFY_MAX_RETRIES:
                logger.debug(
                    "resourceVersion unchanged, retrying (%d/%d)...",
                    attempt,
                    PATCH_VERIFY_MAX_RETRIES,
                )

        # Exhausted retries - check what happened
        if not seen_version_change:
            # Version never changed - likely API caching issue
            logger.error(
                "PATCH VERIFICATION FAILED: resourceVersion never changed after %d attempts (API may be returning cached responses)",
                PATCH_VERIFY_MAX_RETRIES,
            )
            raise FatalError(
                f"Patch verification failed: resourceVersion remained {before_resource_version} after "
                f"{PATCH_VERIFY_MAX_RETRIES} retries. The API may be returning cached responses and not processing the patch."
            )
        else:
            # Version changed but we didn't see correct value (shouldn't happen with current logic)
            logger.error(
                "PATCH VERIFICATION FAILED: resourceVersion changed but correct value not verified after %d attempts",
                PATCH_VERIFY_MAX_RETRIES,
            )
            raise FatalError(
                f"Patch verification failed: resourceVersion changed but {SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME} "
                f"was not verified as '{VELERO_BACKUP_LATEST}' after {PATCH_VERIFY_MAX_RETRIES} retries."
            )

    def _maybe_set_auto_import_strategy(self) -> None:
        """If requested, set ImportAndSync on secondary for ACM 2.14+ with existing clusters."""
        try:
            version = str(self.state.get_config("secondary_version", "unknown"))
            if not is_acm_version_ge(version, "2.14.0"):
                return
            # Count non-local clusters
            mcs = self.secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
            )
            has_non_local = any(mc.get("metadata", {}).get("name") != LOCAL_CLUSTER_NAME for mc in mcs)
            if not has_non_local:
                return
            # Determine current strategy
            cm = self.secondary.get_configmap(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIGMAP)
            current = (cm or {}).get("data", {}).get(AUTO_IMPORT_STRATEGY_KEY, "default")
            if current == AUTO_IMPORT_STRATEGY_SYNC:
                return
            if not self.manage_auto_import_strategy:
                logger.info(
                    "Detect-only: destination hub has existing clusters with default autoImportStrategy; "
                    "use --manage-auto-import-strategy to set %s temporarily.",
                    AUTO_IMPORT_STRATEGY_SYNC,
                )
                return
            logger.info(
                "Setting autoImportStrategy=%s on destination hub (temporary)",
                AUTO_IMPORT_STRATEGY_SYNC,
            )
            self.secondary.create_or_patch_configmap(
                namespace=MCE_NAMESPACE,
                name=IMPORT_CONTROLLER_CONFIGMAP,
                data={AUTO_IMPORT_STRATEGY_KEY: AUTO_IMPORT_STRATEGY_SYNC},
            )
            self.state.set_config("auto_import_strategy_set", True)
        except Exception as e:
            logger.warning("Unable to manage auto-import strategy: %s", e)

    def _create_full_restore(self):
        """Create full restore resource (Method 2)."""
        logger.info("Creating full restore resource...")

        # Check if restore already exists
        existing_restore = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name=RESTORE_FULL_NAME,
            namespace=BACKUP_NAMESPACE,
        )

        if existing_restore:
            logger.info("%s already exists", RESTORE_FULL_NAME)
            return

        # Create restore resource
        restore_body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {
                "name": RESTORE_FULL_NAME,
                "namespace": BACKUP_NAMESPACE,
            },
            "spec": {
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_LATEST,
                "veleroCredentialsBackupName": VELERO_BACKUP_LATEST,
                "veleroResourcesBackupName": VELERO_BACKUP_LATEST,
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

        logger.info("Created %s resource", RESTORE_FULL_NAME)

    def _wait_for_restore_completion(self, timeout: int = RESTORE_WAIT_TIMEOUT):
        """Wait for restore to complete and verify managed clusters are restored."""

        # Skip waiting in dry-run mode since no actual activation occurred
        if self.secondary.dry_run:
            logger.info("[DRY-RUN] Skipping wait for restore completion")
            return

        restore_name = self._get_passive_sync_restore_name() if self.method == "passive" else RESTORE_FULL_NAME

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

        restore_name = self._get_passive_sync_restore_name()

        def _poll_velero_restore():
            # Get the ACM restore to find the Velero restore name
            restore = self.secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name=restore_name,
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
                logger.info(
                    "Velero managed clusters restore completed: %s items restored",
                    items_restored,
                )
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
            if mc.get("metadata", {}).get("name") != LOCAL_CLUSTER_NAME
        ]

        if len(non_local_clusters) >= MIN_MANAGED_CLUSTERS:
            logger.info(
                "Found %s ManagedCluster(s) on secondary hub: %s",
                len(non_local_clusters),
                ", ".join(non_local_clusters) if non_local_clusters else "(none)",
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
