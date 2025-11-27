"""Rollback workflow for ACM switchover."""

from __future__ import annotations

import logging

from lib.constants import BACKUP_NAMESPACE, OBSERVABILITY_NAMESPACE
from lib.kube_client import KubeClient
from lib.utils import StateManager

from .backup_schedule import BackupScheduleManager

logger = logging.getLogger("acm_switchover")


class Rollback:
    """Handles rollback to primary hub."""

    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str,
        has_observability: bool,
        dry_run: bool = False,
    ) -> None:
        self.primary = primary_client
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.has_observability = has_observability
        self.dry_run = dry_run
        self.backup_manager = BackupScheduleManager(
            primary_client, state_manager, "primary hub", dry_run=dry_run
        )

    def rollback(self) -> bool:
        """Execute rollback to primary hub."""
        if self.dry_run:
            logger.info("[DRY-RUN] Starting rollback to primary hub (no changes will be made)...")
        else:
            logger.info("Starting rollback to primary hub...")

        try:
            self._deactivate_secondary()
            self._enable_auto_import()

            if self.has_observability:
                self._restart_thanos_compactor()

            self._unpause_backup_schedule()

            logger.info("Rollback completed. Waiting for clusters to reconnect to primary...")
            logger.info("Allow 5-10 minutes for ManagedClusters to reconnect.")

            return True
        except Exception as exc:
            logger.error(f"Rollback failed: {exc}")
            return False

    def _deactivate_secondary(self) -> None:
        if self.dry_run:
            logger.info("[DRY-RUN] Would deactivate secondary hub...")
        else:
            logger.info("Deactivating secondary hub...")

        deleted = self.secondary.delete_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-acm-full",
            namespace=BACKUP_NAMESPACE,
        )

        if not self.dry_run:
            if deleted:
                logger.info("Deleted restore-acm-full")
            else:
                logger.debug("restore-acm-full not found")
            logger.info("Secondary hub deactivated")

    def _enable_auto_import(self) -> None:
        if self.dry_run:
            logger.info("[DRY-RUN] Would re-enable auto-import on ManagedClusters...")
        else:
            logger.info("Re-enabling auto-import on ManagedClusters...")

        managed_clusters = self.primary.list_managed_clusters()

        count = 0
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")

            if mc_name == "local-cluster":
                continue

            annotations = mc.get("metadata", {}).get("annotations", {})
            if "import.open-cluster-management.io/disable-auto-import" in annotations:
                patch = {"metadata": {"annotations": {"import.open-cluster-management.io/disable-auto-import": None}}}

                self.primary.patch_managed_cluster(name=mc_name, patch=patch)

                count += 1

        if self.dry_run:
            logger.info("[DRY-RUN] Would remove disable-auto-import annotation from %s ManagedCluster(s)", count)
        else:
            logger.info("Removed disable-auto-import annotation from %s ManagedCluster(s)", count)

    def _restart_thanos_compactor(self) -> None:
        if self.dry_run:
            logger.info("[DRY-RUN] Would restart Thanos compactor...")
        else:
            logger.info("Restarting Thanos compactor...")

        try:
            self.primary.scale_statefulset(
                name="observability-thanos-compact",
                namespace=OBSERVABILITY_NAMESPACE,
                replicas=1,
            )

            if not self.dry_run:
                logger.info("Thanos compactor scaled back to 1 replica")
        except Exception as exc:
            logger.error(f"Failed to restart Thanos compactor: {exc}")

    def _unpause_backup_schedule(self) -> None:
        if self.dry_run:
            logger.info("[DRY-RUN] Would unpause BackupSchedule on primary hub...")
        else:
            logger.info("Unpausing BackupSchedule on primary hub...")
        self.backup_manager.ensure_enabled(self.acm_version)
