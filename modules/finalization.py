"""
Finalization and rollback module for ACM switchover.
"""

import logging
import time
from typing import Optional

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
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
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

            # Verify new backups are being created
            if not self.state.is_step_completed("verify_new_backups"):
                self._verify_new_backups()
                self.state.mark_step_completed("verify_new_backups")
            else:
                logger.info("Step already completed: verify_new_backups")

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
            namespace="open-cluster-management-backup",
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
                namespace="open-cluster-management-backup",
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
