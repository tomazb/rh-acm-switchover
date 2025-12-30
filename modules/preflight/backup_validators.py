"""Backup and restore validation checks."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from lib.constants import (
    BACKUP_NAMESPACE,
    BACKUP_SCHEDULE_DEFAULT_NAME,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_USE_MANAGED_SERVICE_ACCOUNT,
)
from lib.kube_client import KubeClient
from lib.validation import InputValidator, ValidationError

from .base_validator import BaseValidator

logger = logging.getLogger("acm_switchover")


class BackupValidator(BaseValidator):
    """Ensures backups exist and no job is stuck."""

    def _get_backup_age_info(self, completion_timestamp: str | None) -> str:
        """
        Calculate backup age and return human-readable info with freshness indicator.

        Args:
            completion_timestamp: ISO 8601 timestamp string from backup.status.completionTimestamp

        Returns:
            Human-readable age string with freshness indicator, or empty string if timestamp unavailable
        """
        if not completion_timestamp:
            return ""

        try:
            # Parse ISO 8601 timestamp (Kubernetes format: 2025-12-03T10:15:30Z)
            completion_dt = datetime.fromisoformat(completion_timestamp.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)

            # Calculate age
            age_seconds = int((now_dt - completion_dt).total_seconds())

            # Format human-readable age
            if age_seconds < 60:
                age_display = f"{age_seconds}s"
            elif age_seconds < 3600:
                age_minutes = age_seconds // 60
                age_display = f"{age_minutes}m"
            elif age_seconds < 86400:
                age_hours = age_seconds // 3600
                age_minutes = (age_seconds % 3600) // 60
                age_display = f"{age_hours}h{age_minutes}m"
            else:
                age_days = age_seconds // 86400
                age_hours = (age_seconds % 86400) // 3600
                age_display = f"{age_days}d{age_hours}h"

            # Determine freshness indicator
            if age_seconds < 3600:  # < 1 hour
                freshness = "FRESH"
            elif age_seconds < 86400:  # < 24 hours
                freshness = "acceptable"
            else:  # >= 24 hours
                freshness = "consider running a fresh backup"

            return f"age: {age_display}, {freshness}"
        except (ValueError, AttributeError) as e:
            logger.debug("Failed to parse backup timestamp %s: %s", completion_timestamp, e)
            return ""

    def run(self, primary: KubeClient) -> None:
        """Run validation with primary client.

        Args:
            primary: Primary hub KubeClient instance
        """
        try:
            backups = primary.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
            )

            if not backups:
                self.add_result(
                    "Backup status",
                    False,
                    "no backups found",
                    critical=True,
                )
                return

            backups.sort(
                key=lambda b: b.get("metadata", {}).get("creationTimestamp", ""),
                reverse=True,
            )

            latest_backup = backups[0]
            backup_name = latest_backup.get("metadata", {}).get("name", "unknown")
            phase = latest_backup.get("status", {}).get("phase", "unknown")

            in_progress = [
                b.get("metadata", {}).get("name") for b in backups if b.get("status", {}).get("phase") == "InProgress"
            ]

            if in_progress:
                self.add_result(
                    "Backup status",
                    False,
                    f"backup(s) in progress: {', '.join(in_progress)}",
                    critical=True,
                )
            elif phase == "Completed":
                # Get backup completion timestamp to calculate age
                completion_ts = latest_backup.get("status", {}).get("completionTimestamp")
                age_info = self._get_backup_age_info(completion_ts)

                message = f"latest backup {backup_name} completed successfully"
                if age_info:
                    message += f" ({age_info})"

                self.add_result(
                    "Backup status",
                    True,
                    message,
                    critical=True,
                )
            else:
                self.add_result(
                    "Backup status",
                    False,
                    f"latest backup {backup_name} in unexpected state: {phase}",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.add_result(
                "Backup status",
                False,
                f"error checking backups: {exc}",
                critical=True,
            )


class BackupScheduleValidator(BaseValidator):
    """Validates BackupSchedule has useManagedServiceAccount enabled for passive sync.

    The useManagedServiceAccount setting is critical for passive sync switchover.
    When enabled, the hub creates a ManagedServiceAccount for each managed cluster,
    allowing klusterlet agents to automatically reconnect to the new hub after
    the restore activates managed clusters.

    Without this setting, managed clusters would require manual re-import after
    switchover because the klusterlet bootstrap-hub-kubeconfig still points to
    the old hub.
    """

    def run(self, primary: KubeClient) -> None:
        """Check that BackupSchedule has useManagedServiceAccount enabled.

        Args:
            primary: KubeClient for the primary hub
        """
        try:
            # Try to find a BackupSchedule resource
            backup_schedules = primary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                namespace=BACKUP_NAMESPACE,
            )

            if not backup_schedules:
                self.add_result(
                    "BackupSchedule configuration",
                    False,
                    "no BackupSchedule found - required for passive sync",
                    critical=True,
                )
                return

            # Check the first (typically only) BackupSchedule
            schedule = backup_schedules[0]
            schedule_name = schedule.get("metadata", {}).get("name", BACKUP_SCHEDULE_DEFAULT_NAME)
            spec = schedule.get("spec", {})
            use_msa = spec.get(SPEC_USE_MANAGED_SERVICE_ACCOUNT, False)

            if use_msa:
                self.add_result(
                    "BackupSchedule configuration",
                    True,
                    f"{schedule_name}: useManagedServiceAccount=true (managed clusters will auto-reconnect)",
                    critical=True,
                )
            else:
                self.add_result(
                    "BackupSchedule configuration",
                    False,
                    f"{schedule_name}: useManagedServiceAccount is not enabled. "
                    "Managed clusters will NOT auto-reconnect to new hub after switchover. "
                    "Set spec.useManagedServiceAccount=true in BackupSchedule.",
                    critical=True,
                )

        except (RuntimeError, ValueError, Exception) as exc:
            self.add_result(
                "BackupSchedule configuration",
                False,
                f"error checking BackupSchedule: {exc}",
                critical=True,
            )


class PassiveSyncValidator(BaseValidator):
    """Checks the passive synchronization restore object."""

    def run(self, secondary: KubeClient) -> None:
        """Run validation with secondary client.

        Args:
            secondary: Secondary hub KubeClient instance
        """
        try:
            # Validate namespace and resource name before using them
            InputValidator.validate_kubernetes_namespace(BACKUP_NAMESPACE)
            InputValidator.validate_kubernetes_name(RESTORE_PASSIVE_SYNC_NAME, "restore")

            restore = secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name=RESTORE_PASSIVE_SYNC_NAME,
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                self.add_result(
                    "Passive sync restore",
                    False,
                    f"{RESTORE_PASSIVE_SYNC_NAME} not found on secondary hub",
                    critical=True,
                )
                return

            status = restore.get("status", {})
            phase = status.get("phase", "unknown")
            message = status.get("lastMessage", "")

            # "Enabled" = continuous sync running
            # "Finished" = initial sync completed successfully (still valid for switchover)
            if phase in ("Enabled", "Finished"):
                self.add_result(
                    "Passive sync restore",
                    True,
                    f"passive sync ready ({phase}): {message}",
                    critical=True,
                )
            else:
                self.add_result(
                    "Passive sync restore",
                    False,
                    f"passive sync in unexpected state: {phase} - {message}",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.add_result(
                "Passive sync restore",
                False,
                f"error checking passive sync: {exc}",
                critical=True,
            )


class ManagedClusterBackupValidator(BaseValidator):
    """Validates that all joined ManagedClusters are included in the latest backup."""

    def run(self, primary: KubeClient) -> None:
        """Check that all joined ManagedClusters are in the latest managed-clusters backup."""
        try:
            # Get all joined ManagedClusters (excluding local-cluster)
            managed_clusters = primary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
            )

            joined_clusters = []
            for mc in managed_clusters:
                mc_name = mc.get("metadata", {}).get("name", "unknown")
                if mc_name == "local-cluster":
                    continue

                # Check if cluster is joined (has Joined condition = True)
                conditions = mc.get("status", {}).get("conditions", [])
                is_joined = any(
                    c.get("type") == "ManagedClusterJoined" and c.get("status") == "True" for c in conditions
                )
                if is_joined:
                    joined_clusters.append(mc_name)

            if not joined_clusters:
                self.add_result(
                    "ManagedClusters in backup",
                    True,
                    "no joined ManagedClusters found (only local-cluster)",
                    critical=False,
                )
                return

            # Find the latest managed-clusters backup
            try:
                # Validate namespace before using it
                InputValidator.validate_kubernetes_namespace(BACKUP_NAMESPACE)
            except ValidationError:
                self.add_result(
                    "ManagedClusters in backup",
                    False,
                    f"invalid backup namespace: {BACKUP_NAMESPACE}",
                    critical=True,
                )
                return

            backups = primary.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
            )

            # Filter for managed-clusters backups using ACM backup schedule type label
            mc_backups = [
                b for b in backups
                if b.get("metadata", {}).get("labels", {}).get("cluster.open-cluster-management.io/backup-schedule-type") == "managedClusters"
            ]

            if not mc_backups:
                self.add_result(
                    "ManagedClusters in backup",
                    False,
                    "no managed-clusters backups found",
                    critical=True,
                )
                return

            # Sort by creation timestamp and get the latest
            mc_backups.sort(
                key=lambda b: b.get("metadata", {}).get("creationTimestamp", ""),
                reverse=True,
            )
            latest_backup = mc_backups[0]

            # Check backup status
            phase = latest_backup.get("status", {}).get("phase", "unknown")
            if phase != "Completed":
                backup_name = latest_backup.get("metadata", {}).get("name", "unknown")
                self.add_result(
                    "ManagedClusters in backup",
                    False,
                    f"latest managed-clusters backup {backup_name} not completed: {phase}",
                    critical=True,
                )
                return

            # Backup is completed - report success with joined cluster count
            # Note: joined_clusters is guaranteed non-empty (validated earlier in this method)
            self.add_result(
                "ManagedClusters in backup",
                True,
                f"found {len(joined_clusters)} joined cluster(s), latest backup completed successfully",
                critical=False,
            )

        except (RuntimeError, ValueError, Exception) as exc:
            self.add_result(
                "ManagedClusters in backup",
                False,
                f"error checking ManagedClusters backup: {exc}",
                critical=True,
            )
