"""
Finalization and rollback module for ACM switchover.
"""

import logging
import time
from typing import Optional

from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    BACKUP_SCHEDULE_DEFAULT_NAME,
    OBSERVABILITY_NAMESPACE,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
    MCE_NAMESPACE,
    IMPORT_CONTROLLER_CONFIGMAP,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_SYNC,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import StateManager, is_acm_version_ge

from .backup_schedule import BackupScheduleManager
from .decommission import Decommission

logger = logging.getLogger("acm_switchover")


class Finalization:
    """Handles finalization steps on secondary hub."""

    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str,
        primary_client: Optional[KubeClient] = None,
        primary_has_observability: bool = False,
        dry_run: bool = False,
        old_hub_action: str = "secondary",
        manage_auto_import_strategy: bool = False,
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.primary = primary_client
        self.primary_has_observability = primary_has_observability
        self.dry_run = dry_run
        self.old_hub_action = old_hub_action  # "secondary", "decommission", or "none"
        self.manage_auto_import_strategy = manage_auto_import_strategy
        self.backup_manager = BackupScheduleManager(
            secondary_client,
            state_manager,
            "secondary hub",
            dry_run=dry_run,
        )

    def finalize(self) -> bool:
        """
        Execute finalization steps.

        Returns:
            True if finalization completed successfully
        """
        logger.info("Starting finalization...")

        try:
            # Step 10: Enable BackupSchedule on new hub
            if not self.state.is_step_completed("enable_backup_schedule"):
                self._enable_backup_schedule()
                self.state.mark_step_completed("enable_backup_schedule")
            else:
                logger.info("Step already completed: enable_backup_schedule")

            if not self.state.is_step_completed("verify_backup_schedule_enabled"):
                self._verify_backup_schedule_enabled()
                self.state.mark_step_completed("verify_backup_schedule_enabled")
            else:
                logger.info("Step already completed: verify_backup_schedule_enabled")

            # Fix BackupSchedule collision if detected
            if not self.state.is_step_completed("fix_backup_collision"):
                self._fix_backup_schedule_collision()
                self.state.mark_step_completed("fix_backup_collision")
            else:
                logger.info("Step already completed: fix_backup_collision")

            # Verify new backups are being created
            if not self.state.is_step_completed("verify_new_backups"):
                self._verify_new_backups()
                self.state.mark_step_completed("verify_new_backups")
            else:
                logger.info("Step already completed: verify_new_backups")

            if not self.state.is_step_completed("verify_mch_health"):
                self._verify_multiclusterhub_health()
                self.state.mark_step_completed("verify_mch_health")
            else:
                logger.info("Step already completed: verify_mch_health")

            # Ensure auto-import strategy reset to default (ACM 2.14+)
            self._ensure_auto_import_default()

            # Handle old primary hub based on --old-hub-action
            if not self.state.is_step_completed("handle_old_hub"):
                self._handle_old_hub()
                self.state.mark_step_completed("handle_old_hub")
            else:
                logger.info("Step already completed: handle_old_hub")

            if self.primary and self.old_hub_action != "decommission":
                self._verify_old_hub_state()

            logger.info("Finalization completed successfully")
            return True

        except SwitchoverError as e:
            logger.error("Finalization failed: %s", e)
            self.state.add_error(str(e), "finalization")
            return False
        except Exception as e:
            logger.error("Unexpected error during finalization: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", "finalization")
            return False

    def _enable_backup_schedule(self):
        """Enable BackupSchedule on new hub (version-aware).

        Important: Before creating the BackupSchedule, we must delete any active
        restore resources. ACM will not allow a BackupSchedule to run while a
        restore is active (to prevent data corruption).
        """
        logger.info("Enabling BackupSchedule on new hub...")

        # First, delete any active restore resources
        self._cleanup_restore_resources()

        # Now create/enable the BackupSchedule
        self.backup_manager.ensure_enabled(self.acm_version)

    def _cleanup_restore_resources(self):
        """Delete restore resources before enabling BackupSchedule.

        ACM backup operator won't allow BackupSchedule to be active while
        a Restore resource exists. We need to clean them up first.

        Before deletion, we archive the restore details to the state file
        for audit trail and troubleshooting purposes.

        This method discovers all restores dynamically by listing all Restore
        resources in the backup namespace, rather than relying on hardcoded names.
        """
        archived_restores = []

        # List all restores in the namespace
        all_restores = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            namespace=BACKUP_NAMESPACE,
        )

        if not all_restores:
            logger.info("No restore resources found to clean up")
            return

        logger.info("Found %s restore resource(s) to clean up", len(all_restores))

        for restore in all_restores:
            restore_name = restore.get("metadata", {}).get("name", "unknown")
            try:
                # Archive restore details before deletion
                restore_archive = self._archive_restore_details(restore)
                archived_restores.append(restore_archive)
                logger.info(
                    "Archived restore '%s' details: phase=%s, veleroBackups=%s",
                    restore_name,
                    restore_archive.get("phase"),
                    restore_archive.get("velero_backups", {}),
                )

                logger.info("Deleting restore resource: %s", restore_name)
                self.secondary.delete_custom_resource(
                    group="cluster.open-cluster-management.io",
                    version="v1beta1",
                    plural="restores",
                    name=restore_name,
                    namespace=BACKUP_NAMESPACE,
                )
                logger.info("Deleted restore resource: %s", restore_name)
            except Exception as e:
                # Not found is OK, other errors should be logged
                if "not found" not in str(e).lower():
                    logger.warning("Error deleting restore %s: %s", restore_name, e)

        # Save archived restores to state for audit trail
        if archived_restores:
            self.state.set_config("archived_restores", archived_restores)
            logger.info(
                "Saved %s restore record(s) to state file", len(archived_restores)
            )

    def _archive_restore_details(self, restore: dict) -> dict:
        """Extract and return important details from a Restore resource for archiving.

        Args:
            restore: The full Restore CR dict

        Returns:
            A dict containing the essential restore information for audit trail
        """
        metadata = restore.get("metadata", {})
        spec = restore.get("spec", {})
        status = restore.get("status", {})

        return {
            # Metadata details
            "name": metadata.get("name"),
            "namespace": metadata.get("namespace"),
            "uid": metadata.get("uid"),
            "resource_version": metadata.get("resourceVersion"),
            "generation": metadata.get("generation"),
            "creation_timestamp": metadata.get("creationTimestamp"),
            "labels": metadata.get("labels", {}),
            "annotations": metadata.get("annotations", {}),
            "owner_references": metadata.get("ownerReferences", []),
            "archived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # Spec details
            "velero_backups": {
                "veleroManagedClustersBackupName": spec.get(
                    "veleroManagedClustersBackupName"
                ),
                "veleroCredentialsBackupName": spec.get("veleroCredentialsBackupName"),
                "veleroResourcesBackupName": spec.get("veleroResourcesBackupName"),
            },
            "sync_restore_with_new_backups": spec.get("syncRestoreWithNewBackups"),
            "restore_sync_interval": spec.get("restoreSyncInterval"),
            "cleanup_before_restore": spec.get("cleanupBeforeRestore"),
            # Status details
            "phase": status.get("phase"),
            "last_message": status.get("lastMessage"),
            "velero_managed_clusters_restore_name": status.get(
                "veleroManagedClustersRestoreName"
            ),
            "velero_credentials_restore_name": status.get(
                "veleroCredentialsRestoreName"
            ),
            "velero_resources_restore_name": status.get("veleroResourcesRestoreName"),
        }

    def _verify_new_backups(self, timeout: int = 600):
        """
        Verify new backups are being created.

        Args:
            timeout: Maximum wait time in seconds (default 10 minutes)
        """
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping new backup verification")
            return

        logger.info("Verifying new backups are being created...")

        # Get current backup list (Velero Backups use velero.io/v1)
        initial_backups = self.secondary.list_custom_resources(
            group="velero.io",
            version="v1",
            plural="backups",
            namespace=BACKUP_NAMESPACE,
        )

        initial_backup_names = {
            b.get("metadata", {}).get("name") for b in initial_backups
        }

        logger.info("Found %s existing backup(s)", len(initial_backups))
        logger.info("Waiting for new backup to appear (this may take 5-10 minutes)...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            current_backups = self.secondary.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
            )

            current_backup_names = {
                b.get("metadata", {}).get("name") for b in current_backups
            }

            # Check for new backups
            new_backups = current_backup_names - initial_backup_names

            if new_backups:
                logger.info("New backup(s) detected: %s", ", ".join(new_backups))

                # Verify at least one is in progress or completed
                for backup_name in new_backups:
                    backup = next(
                        (
                            b
                            for b in current_backups
                            if b.get("metadata", {}).get("name") == backup_name
                        ),
                        None,
                    )

                    if backup:
                        phase = backup.get("status", {}).get("phase", "unknown")
                        logger.info("Backup %s phase: %s", backup_name, phase)

                        # Velero uses "InProgress" and "Completed" phases
                        if phase in ("InProgress", "Completed", "New"):
                            logger.info("New backup is being created successfully!")
                            return

            elapsed = int(time.time() - start_time)
            logger.debug("Waiting for new backup... (elapsed: %ss)", elapsed)
            time.sleep(30)

        logger.warning(
            f"No new backups detected after {timeout}s. "
            "BackupSchedule may take time to create first backup."
        )

    def _verify_backup_schedule_enabled(self):
        """Ensure BackupSchedule is present and not paused."""
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping BackupSchedule verification")
            return

        schedules = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
        )

        if not schedules:
            raise RuntimeError("No BackupSchedule found while verifying finalization")

        schedule = schedules[0]
        schedule_name = schedule.get("metadata", {}).get("name", "schedule-rhacm")
        paused = schedule.get("spec", {}).get("paused", False)

        if paused:
            raise RuntimeError(f"BackupSchedule {schedule_name} is still paused")

        logger.info("BackupSchedule %s is enabled", schedule_name)

    def _verify_multiclusterhub_health(self):
        """Ensure MultiClusterHub reports healthy and pods are running."""
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping MultiClusterHub health verification")
            return

        logger.info("Verifying MultiClusterHub health...")
        mch = self.secondary.get_custom_resource(
            group="operator.open-cluster-management.io",
            version="v1",
            plural="multiclusterhubs",
            name="multiclusterhub",
            namespace=ACM_NAMESPACE,
        )

        if not mch:
            hubs = self.secondary.list_custom_resources(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                namespace=ACM_NAMESPACE,
            )
            if hubs:
                mch = hubs[0]

        if not mch:
            raise RuntimeError("No MultiClusterHub resource found on secondary hub")

        mch_name = mch.get("metadata", {}).get("name", "multiclusterhub")
        phase = mch.get("status", {}).get("phase", "unknown")

        if phase != "Running":
            raise RuntimeError(
                f"MultiClusterHub {mch_name} is in phase '{phase}', expected Running"
            )

        pods = self.secondary.get_pods(namespace=ACM_NAMESPACE)
        non_running = [
            pod.get("metadata", {}).get("name", "unknown")
            for pod in pods
            if pod.get("status", {}).get("phase") != "Running"
        ]

        if non_running:
            raise RuntimeError(
                "ACM namespace still has non-running pods: " + ", ".join(non_running)
            )

        logger.info("MultiClusterHub %s is Running and all pods are healthy", mch_name)

    def _handle_old_hub(self):
        """
        Handle the old primary hub based on --old-hub-action setting.

        Options:
        - 'secondary': Set up passive sync restore for failback capability (default)
        - 'decommission': Remove ACM components from old hub
        - 'none': Leave old hub unchanged (manual handling required)
        """
        if not self.primary:
            logger.debug("No primary client available, skipping old hub handling")
            return

        if self.old_hub_action == "none":
            logger.info("Old hub action is 'none' - leaving old primary hub unchanged")
            logger.info("NOTE: You will need to manually configure the old hub")
            return

        if self.old_hub_action == "secondary":
            logger.info(
                "Setting up old primary hub as new secondary (for failback capability)..."
            )
            self._setup_old_hub_as_secondary()
            return

        if self.old_hub_action == "decommission":
            logger.info("Decommissioning old primary hub...")
            self._decommission_old_hub()
            return

        logger.warning("Unknown old_hub_action: %s, skipping", self.old_hub_action)

    def _decommission_old_hub(self):
        """
        Decommission the old primary hub by removing ACM components.

        This is run non-interactively as part of the switchover finalization.
        """
        if not self.primary:
            logger.debug("No primary client available, skipping decommission")
            return

        if self.dry_run:
            logger.info("[DRY-RUN] Would decommission old primary hub")
            return

        logger.warning("=" * 60)
        logger.warning("DECOMMISSIONING OLD PRIMARY HUB")
        logger.warning("This will remove ACM components from the old hub!")
        logger.warning("=" * 60)

        decom = Decommission(
            self.primary, self.primary_has_observability, dry_run=self.dry_run
        )

        # Run decommission non-interactively since we're in automated mode
        if decom.decommission(interactive=False):
            logger.info("Old hub decommissioned successfully")
        else:
            logger.warning("Old hub decommission completed with warnings")
            logger.warning("You may need to manually clean up remaining resources")

    def _setup_old_hub_as_secondary(self):
        """
        Set up the old primary hub as a new secondary with passive sync restore.

        After switchover, the old primary should:
        1. NOT have a BackupSchedule (already handled by primary_prep)
        2. HAVE a passive sync restore to continuously receive backups from new primary

        This ensures the old hub is ready for a future failback if needed.
        """
        if not self.primary:
            logger.debug("No primary client available, skipping secondary setup")
            return

        if self.dry_run:
            logger.info(
                "[DRY-RUN] Would set up old primary as secondary with passive sync"
            )
            return

        logger.info("Setting up old primary hub as new secondary...")

        # Check if restore already exists
        existing_restore = self.primary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name=RESTORE_PASSIVE_SYNC_NAME,
            namespace=BACKUP_NAMESPACE,
        )

        if existing_restore:
            logger.info("Passive sync restore already exists on old primary")
            return

        # Create passive sync restore on old primary
        restore_body = {
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "Restore",
            "metadata": {
                "name": RESTORE_PASSIVE_SYNC_NAME,
                "namespace": BACKUP_NAMESPACE,
            },
            "spec": {
                SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True,
                "restoreSyncInterval": "10m",
                "cleanupBeforeRestore": "CleanupRestored",
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_SKIP,
                "veleroCredentialsBackupName": VELERO_BACKUP_LATEST,
                "veleroResourcesBackupName": VELERO_BACKUP_LATEST,
            },
        }

        try:
            self.primary.create_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                body=restore_body,
                namespace=BACKUP_NAMESPACE,
            )
            logger.info("Created passive sync restore on old primary hub")
        except Exception as e:
            logger.warning(
                "Failed to create passive sync restore on old primary: %s", e
            )
            logger.warning("You may need to manually create it for failback capability")

    def _fix_backup_schedule_collision(self):
        """
        Proactively fix BackupSchedule collision by recreating it.

        After switchover, the new primary's BackupSchedule will eventually show
        "BackupCollision" because it detects old backups from the previous primary
        in storage. The collision is only detected after Velero schedules run
        and attempt to write to the shared storage location.

        To avoid this race condition, we proactively recreate the BackupSchedule
        during switchover. This resets the cluster ID association and prevents
        the collision from occurring.
        """
        if self.dry_run:
            logger.info("[DRY-RUN] Would recreate BackupSchedule to prevent collision")
            return

        # Check current BackupSchedule status
        schedules = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
        )

        if not schedules:
            logger.warning("No BackupSchedule found on new primary")
            return

        schedule = schedules[0]
        schedule_name = schedule.get("metadata", {}).get(
            "name", BACKUP_SCHEDULE_DEFAULT_NAME
        )
        phase = schedule.get("status", {}).get("phase", "")

        # Proactively recreate to prevent collision, or fix if already in collision
        # The collision may not appear immediately - it only shows after Velero
        # schedules run and detect backups from a different cluster ID
        if phase == "BackupCollision":
            logger.warning(
                "BackupSchedule %s has collision, recreating...", schedule_name
            )
        else:
            logger.info(
                "Proactively recreating BackupSchedule %s to prevent future collision "
                "(current phase: %s)",
                schedule_name,
                phase or "Unknown",
            )

        # Save the spec for recreation
        schedule_spec = schedule.get("spec", {})

        # Delete the old schedule
        try:
            self.secondary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=schedule_name,
                namespace=BACKUP_NAMESPACE,
            )
            logger.info("Deleted old BackupSchedule %s", schedule_name)

            # Wait a moment for deletion to complete
            time.sleep(5)

            # Recreate with same spec
            new_schedule = {
                "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                "kind": "BackupSchedule",
                "metadata": {
                    "name": schedule_name,
                    "namespace": BACKUP_NAMESPACE,
                },
                "spec": schedule_spec,
            }

            self.secondary.create_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                body=new_schedule,
                namespace=BACKUP_NAMESPACE,
            )
            logger.info(
                "Recreated BackupSchedule %s to prevent collision", schedule_name
            )

        except Exception as e:
            logger.warning("Failed to recreate BackupSchedule: %s", e)
            logger.warning(
                "You may need to manually delete and recreate the BackupSchedule"
            )

    def _verify_old_hub_state(self):
        """Run regression checks on the old (primary) hub."""
        if not self.primary:
            return

        logger.info("Running regression checks on old primary hub...")

        clusters = self.primary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
        )

        still_available = []
        for cluster in clusters:
            name = cluster.get("metadata", {}).get("name")
            if name == "local-cluster":
                continue

            conditions = cluster.get("status", {}).get("conditions", [])
            available = any(
                c.get("type") == "ManagedClusterConditionAvailable"
                and c.get("status") == "True"
                for c in conditions
            )
            if available:
                still_available.append(name or "unknown")

        if still_available:
            logger.warning(
                "Old hub still reports the following ManagedClusters as Available: %s",
                ", ".join(still_available),
            )
        else:
            logger.info("All ManagedClusters show as disconnected from old hub")

        schedules = self.primary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
        )
        if schedules:
            paused = schedules[0].get("spec", {}).get("paused", False)
            if paused:
                logger.info("Old hub BackupSchedule remains paused as expected")
            else:
                logger.warning("Old hub BackupSchedule is not paused")

        if self.primary_has_observability:
            compactor_pods = self.primary.get_pods(
                namespace=OBSERVABILITY_NAMESPACE,
                label_selector="app.kubernetes.io/name=thanos-compact",
            )
            if compactor_pods:
                logger.warning(
                    "Thanos compactor still running on old hub (%s pod(s))",
                    len(compactor_pods),
                )
            else:
                logger.info("Thanos compactor is scaled down on old hub")

    def _ensure_auto_import_default(self) -> None:
        """Reset autoImportStrategy to default ImportOnly when applicable."""
        try:
            if not is_acm_version_ge(self.acm_version, "2.14.0"):
                return
            cm = self.secondary.get_configmap(
                MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIGMAP
            )
            if not cm:
                return
            strategy = (cm.get("data") or {}).get(AUTO_IMPORT_STRATEGY_KEY, "default")
            if strategy != AUTO_IMPORT_STRATEGY_SYNC:
                return
            if self.manage_auto_import_strategy or self.state.get_config(
                "auto_import_strategy_set", False
            ):
                logger.info(
                    "Removing %s/%s to restore default autoImportStrategy (%s)",
                    MCE_NAMESPACE,
                    IMPORT_CONTROLLER_CONFIGMAP,
                    AUTO_IMPORT_STRATEGY_DEFAULT,
                )
                self.secondary.delete_configmap(
                    MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIGMAP
                )
                if not self.state.is_step_completed("reset_auto_import_strategy"):
                    self.state.mark_step_completed("reset_auto_import_strategy")
            else:
                logger.warning(
                    "autoImportStrategy is %s; remove %s/%s to reset to default (%s)",
                    AUTO_IMPORT_STRATEGY_SYNC,
                    MCE_NAMESPACE,
                    IMPORT_CONTROLLER_CONFIGMAP,
                    AUTO_IMPORT_STRATEGY_DEFAULT,
                )
        except Exception as e:
            logger.warning("Unable to verify/reset auto-import strategy: %s", e)
