"""
Finalization and rollback module for ACM switchover.
"""

import logging
import time
from typing import Optional

from lib.constants import ACM_NAMESPACE, BACKUP_NAMESPACE, OBSERVABILITY_NAMESPACE
from lib.kube_client import KubeClient
from lib.utils import StateManager
from .backup_schedule import BackupScheduleManager

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
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.primary = primary_client
        self.primary_has_observability = primary_has_observability
        self.backup_manager = BackupScheduleManager(
            secondary_client,
            state_manager,
            "secondary hub",
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

            if self.primary:
                self._verify_old_hub_state()

            logger.info("Finalization completed successfully")
            return True

        except Exception as e:
            logger.error(f"Finalization failed: {e}")
            self.state.add_error(str(e), "finalization")
            return False

    def _enable_backup_schedule(self):
        """Enable BackupSchedule on new hub (version-aware)."""
        logger.info("Enabling BackupSchedule on new hub...")
        self.backup_manager.ensure_enabled(self.acm_version)

    def _verify_new_backups(self, timeout: int = 600):
        """
        Verify new backups are being created.

        Args:
            timeout: Maximum wait time in seconds (default 10 minutes)
        """
        logger.info("Verifying new backups are being created...")

        # Get current backup list
        initial_backups = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backups",
            namespace=BACKUP_NAMESPACE,
        )

        initial_backup_names = {
            b.get("metadata", {}).get("name") for b in initial_backups
        }

        logger.info(f"Found {len(initial_backups)} existing backup(s)")
        logger.info("Waiting for new backup to appear (this may take 5-10 minutes)...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            current_backups = self.secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
            )

            current_backup_names = {
                b.get("metadata", {}).get("name") for b in current_backups
            }

            # Check for new backups
            new_backups = current_backup_names - initial_backup_names

            if new_backups:
                logger.info(f"New backup(s) detected: {', '.join(new_backups)}")

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
                        logger.info(f"Backup {backup_name} phase: {phase}")

                        if phase in ("InProgress", "Finished"):
                            logger.info("New backup is being created successfully!")
                            return

            elapsed = int(time.time() - start_time)
            logger.debug(f"Waiting for new backup... (elapsed: {elapsed}s)")
            time.sleep(30)

        logger.warning(
            f"No new backups detected after {timeout}s. "
            "BackupSchedule may take time to create first backup."
        )

    def _verify_backup_schedule_enabled(self):
        """Ensure BackupSchedule is present and not paused."""
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
