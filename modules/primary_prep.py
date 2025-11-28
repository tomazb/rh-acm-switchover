"""
Primary hub preparation module for ACM switchover.
"""

import logging
from typing import List

from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import StateManager, is_acm_version_ge

logger = logging.getLogger("acm_switchover")


class PrimaryPreparation:
    """Handles preparation steps on primary hub."""

    def __init__(
        self,
        primary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str,
        has_observability: bool,
        dry_run: bool = False,
    ):
        self.primary = primary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.has_observability = has_observability
        self.dry_run = dry_run

    def prepare(self) -> bool:
        """
        Execute all primary hub preparation steps.

        Returns:
            True if all steps completed successfully
        """
        logger.info("Starting primary hub preparation...")

        try:
            # Step 1: Pause BackupSchedule
            if not self.state.is_step_completed("pause_backup_schedule"):
                self._pause_backup_schedule()
                self.state.mark_step_completed("pause_backup_schedule")
            else:
                logger.info("Step already completed: pause_backup_schedule")

            # Step 2: Add disable-auto-import annotations
            if not self.state.is_step_completed("disable_auto_import"):
                self._disable_auto_import()
                self.state.mark_step_completed("disable_auto_import")
            else:
                logger.info("Step already completed: disable_auto_import")

            # Step 3: Scale down Thanos compactor (if Observability present)
            if self.has_observability:
                if not self.state.is_step_completed("scale_down_thanos"):
                    self._scale_down_thanos_compactor()
                    self.state.mark_step_completed("scale_down_thanos")
                else:
                    logger.info("Step already completed: scale_down_thanos")
            else:
                logger.info("Skipping Thanos compactor scaling (Observability not detected)")

            logger.info("Primary hub preparation completed successfully")
            return True

        except SwitchoverError as e:
            logger.error("Primary hub preparation failed: %s", e)
            self.state.add_error(str(e), "primary_preparation")
            return False
        except Exception as e:
            logger.error("Unexpected error during primary preparation: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", "primary_preparation")
            return False

    def _pause_backup_schedule(self):
        """Pause BackupSchedule (version-aware)."""
        logger.info("Pausing BackupSchedule...")

        # Get BackupSchedule
        backup_schedules = self.primary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace="open-cluster-management-backup",
        )

        if not backup_schedules:
            logger.warning("No BackupSchedule found to pause")
            return

        # Assume first BackupSchedule (typically only one exists)
        bs = backup_schedules[0]
        bs_name = bs.get("metadata", {}).get("name", "schedule-rhacm")

        # Check if already paused
        if bs.get("spec", {}).get("paused") is True:
            logger.info("BackupSchedule %s is already paused", bs_name)
            return

        # ACM 2.12+ supports pausing via spec.paused
        if is_acm_version_ge(self.acm_version, "2.12.0"):
            logger.info("Using spec.paused for ACM %s", self.acm_version)

            patch = {"spec": {"paused": True}}
            self.primary.patch_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=bs_name,
                patch=patch,
                namespace="open-cluster-management-backup",
            )

            logger.info("BackupSchedule %s paused successfully", bs_name)
        else:
            # ACM 2.11: Need to delete BackupSchedule
            logger.info("ACM %s requires deleting BackupSchedule", self.acm_version)

            # Save the BackupSchedule YAML to state for later restoration
            self.state.set_config("saved_backup_schedule", bs)

            self.primary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=bs_name,
                namespace="open-cluster-management-backup",
            )

            logger.info("BackupSchedule %s deleted (saved to state)", bs_name)

    def _disable_auto_import(self):
        """Add disable-auto-import annotation to all ManagedClusters."""
        logger.info("Disabling auto-import on ManagedClusters...")

        managed_clusters = self.primary.list_managed_clusters()

        if not managed_clusters:
            logger.warning("No ManagedClusters found")
            return

        count = 0
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")

            # Skip local-cluster
            if mc_name == "local-cluster":
                logger.debug("Skipping local-cluster")
                continue

            # Check if annotation already exists
            annotations = mc.get("metadata", {}).get("annotations", {})
            if "import.open-cluster-management.io/disable-auto-import" in annotations:
                logger.debug("ManagedCluster %s already has disable-auto-import annotation", mc_name)
                continue

            # Add annotation
            patch = {"metadata": {"annotations": {"import.open-cluster-management.io/disable-auto-import": ""}}}

            self.primary.patch_managed_cluster(name=mc_name, patch=patch)

            count += 1
            logger.debug("Added disable-auto-import annotation to %s", mc_name)

        logger.info("Disabled auto-import on %s ManagedCluster(s)", count)

    def _scale_down_thanos_compactor(self):
        """Scale down Thanos compactor StatefulSet."""
        logger.info("Scaling down Thanos compactor...")

        try:
            self.primary.scale_statefulset(
                name="observability-thanos-compact",
                namespace="open-cluster-management-observability",
                replicas=0,
            )

            # Skip verification in dry-run mode
            if self.dry_run:
                logger.info("[DRY-RUN] Skipping Thanos compactor pod verification")
                return

            # Wait a moment and verify no pods running
            import time

            time.sleep(5)

            pods = self.primary.get_pods(
                namespace="open-cluster-management-observability",
                label_selector="app=thanos-compact",
            )

            if pods:
                logger.warning("Thanos compactor still has %s pod(s) running", len(pods))
            else:
                logger.info("Thanos compactor scaled down successfully")

        except Exception as e:
            logger.error("Failed to scale down Thanos compactor: %s", e)
            # Don't fail the whole preparation if this is optional
            if "not found" in str(e).lower():
                logger.warning("Thanos compactor StatefulSet not found (may not exist)")
            else:
                raise
