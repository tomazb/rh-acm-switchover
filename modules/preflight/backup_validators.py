"""Backup and restore validation checks."""

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

from lib.constants import (
    BACKUP_NAMESPACE,
    BACKUP_POLL_INTERVAL,
    BACKUP_SCHEDULE_DEFAULT_NAME,
    BACKUP_VERIFY_TIMEOUT,
    LOCAL_CLUSTER_NAME,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_USE_MANAGED_SERVICE_ACCOUNT,
)
from lib.gitops_detector import record_gitops_markers
from lib.kube_client import KubeClient
from lib.validation import InputValidator, ValidationError

from .base_validator import BaseValidator

logger = logging.getLogger("acm_switchover")


def _format_condition_details(conditions: Optional[List[dict]]) -> str:
    """Format condition details for human-readable output."""
    if not conditions:
        return ""

    details = []
    for condition in conditions:
        cond_type = condition.get("type", "unknown")
        status = condition.get("status", "unknown")
        reason = condition.get("reason") or "n/a"
        message = condition.get("message") or "n/a"
        details.append(f"{cond_type}={status} reason={reason} message={message}")

    return "; ".join(details)


def _describe_bsl_issue(bsl: dict) -> str:
    """Build a descriptive message for a problematic BackupStorageLocation."""
    name = bsl.get("metadata", {}).get("name", "unknown")
    phase = bsl.get("status", {}).get("phase", "unknown")
    conditions = bsl.get("status", {}).get("conditions") or []
    condition_summary = _format_condition_details(conditions)

    message = f"{name} phase={phase}"
    if condition_summary:
        message += f" conditions={condition_summary}"
    return message


def _collect_bsl_unavailable_details(kube_client: KubeClient) -> str:
    """Return a descriptive string for unavailable BSLs or missing BSLs."""
    try:
        bsls = kube_client.list_custom_resources(
            group="velero.io",
            version="v1",
            plural="backupstoragelocations",
            namespace=BACKUP_NAMESPACE,
        )
    except Exception as exc:
        logger.debug("Failed to list BackupStorageLocations: %s", exc)
        return ""

    if not bsls:
        return "no BackupStorageLocation found"

    unavailable = []
    for bsl in bsls:
        phase = bsl.get("status", {}).get("phase", "unknown")
        if phase != "Available":
            unavailable.append(_describe_bsl_issue(bsl))

    return "; ".join(unavailable)


class BackupValidator(BaseValidator):
    """Ensures backups exist and no job is stuck."""

    def _wait_for_backups_complete(
        self, primary: KubeClient, in_progress: List[str]
    ) -> List[str]:
        """Wait for in-progress backups to complete within a timeout.

        Args:
            primary: Primary hub KubeClient instance
            in_progress: List of backup names currently in progress

        Returns:
            List of backup names still in progress after waiting
        """
        if not in_progress:
            return []

        logger.info(
            "Backup(s) in progress: %s. Waiting up to %ds for completion...",
            ", ".join(in_progress),
            BACKUP_VERIFY_TIMEOUT,
        )

        start_time = time.time()
        remaining = in_progress

        while remaining and (time.time() - start_time) < BACKUP_VERIFY_TIMEOUT:
            time.sleep(BACKUP_POLL_INTERVAL)
            try:
                backups = primary.list_custom_resources(
                    group="velero.io",
                    version="v1",
                    plural="backups",
                    namespace=BACKUP_NAMESPACE,
                )
            except Exception as exc:
                logger.debug("Failed to list backups while waiting: %s", exc)
                return remaining

            remaining = [
                b.get("metadata", {}).get("name")
                for b in backups
                if b.get("status", {}).get("phase") == "InProgress"
            ]

        return remaining

    def _get_backup_age_info(self, completion_timestamp: Optional[str]) -> str:
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
            completion_dt = datetime.fromisoformat(
                completion_timestamp.replace("Z", "+00:00")
            )
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
            logger.debug(
                "Failed to parse backup timestamp %s: %s", completion_timestamp, e
            )
            return ""

    def run(self, primary: KubeClient) -> None:  # noqa: C901
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
                b.get("metadata", {}).get("name")
                for b in backups
                if b.get("status", {}).get("phase") == "InProgress"
            ]

            if in_progress:
                remaining = self._wait_for_backups_complete(primary, in_progress)
                if remaining:
                    self.add_result(
                        "Backup status",
                        False,
                        f"backup(s) in progress after waiting {BACKUP_VERIFY_TIMEOUT}s: {', '.join(remaining)}",
                        critical=True,
                    )
                    return

                # Refresh backup list after waiting to ensure we inspect the latest completion state
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
                        "no backups found after waiting for in-progress backups to complete (backups may have been deleted)",
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

            if phase == "Completed":
                # Get backup completion timestamp to calculate age
                completion_ts = latest_backup.get("status", {}).get(
                    "completionTimestamp"
                )
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
        except Exception as exc:
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

    def run(self, primary: KubeClient) -> None:  # noqa: C901
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
            metadata = schedule.get("metadata", {})
            schedule_name = metadata.get("name", BACKUP_SCHEDULE_DEFAULT_NAME)
            spec = schedule.get("spec", {})
            use_msa = spec.get(SPEC_USE_MANAGED_SERVICE_ACCOUNT, False)

            # Record GitOps markers if present (non-critical)
            try:
                record_gitops_markers(
                    context="primary",
                    namespace=BACKUP_NAMESPACE,
                    kind="BackupSchedule",
                    name=schedule_name,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning(
                    "GitOps marker recording failed for BackupSchedule %s: %s",
                    schedule_name,
                    exc,
                )

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

        except Exception as exc:
            self.add_result(
                "BackupSchedule configuration",
                False,
                f"error checking BackupSchedule: {exc}",
                critical=True,
            )


class BackupStorageLocationValidator(BaseValidator):
    """Validates BackupStorageLocation availability on each hub.

    A BackupStorageLocation must be Available, otherwise backups cannot be restored.
    """

    def run(self, kube_client: KubeClient, hub_label: str) -> None:
        """Check BackupStorageLocation availability for a hub.

        Args:
            kube_client: KubeClient instance for the hub
            hub_label: Label for the hub (primary/secondary)
        """
        try:
            InputValidator.validate_kubernetes_namespace(BACKUP_NAMESPACE)

            bsls = kube_client.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backupstoragelocations",
                namespace=BACKUP_NAMESPACE,
            )

            if not bsls:
                self.add_result(
                    f"BackupStorageLocation ({hub_label})",
                    False,
                    "no BackupStorageLocation found - restores cannot proceed",
                    critical=True,
                )
                return

            unavailable = []
            for bsl in bsls:
                phase = bsl.get("status", {}).get("phase", "unknown")
                if phase != "Available":
                    unavailable.append(_describe_bsl_issue(bsl))

            if unavailable:
                details = "; ".join(unavailable)
                self.add_result(
                    f"BackupStorageLocation ({hub_label})",
                    False,
                    f"unavailable BSL(s) block restore: {details}. "
                    "BackupStorageLocation must be Available to restore backups.",
                    critical=True,
                )
            else:
                self.add_result(
                    f"BackupStorageLocation ({hub_label})",
                    True,
                    f"all {len(bsls)} BackupStorageLocation(s) are Available",
                    critical=True,
                )

        except ValidationError as exc:
            self.add_result(
                f"BackupStorageLocation ({hub_label})",
                False,
                f"invalid backup namespace: {exc}",
                critical=True,
            )
        except Exception as exc:
            self.add_result(
                f"BackupStorageLocation ({hub_label})",
                False,
                f"error checking BackupStorageLocation: {exc}",
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
            InputValidator.validate_kubernetes_name(
                RESTORE_PASSIVE_SYNC_NAME, "restore"
            )

            context = secondary.context or "default"

            # Prefer discovery by spec.syncRestoreWithNewBackups=true (matches bash scripts)
            restores = secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                namespace=BACKUP_NAMESPACE,
            )

            def _creation_ts(item: dict) -> str:
                return item.get("metadata", {}).get("creationTimestamp", "")

            passive_candidates = [
                r
                for r in restores
                if r.get("spec", {}).get("syncRestoreWithNewBackups") is True
            ]
            passive_candidates.sort(key=_creation_ts, reverse=True)

            restore = passive_candidates[0] if passive_candidates else None
            if not restore:
                # Fallback to the conventional name
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
                    "No passive sync restore found on secondary hub (required for passive method). "
                    f"Expected a Restore with spec.syncRestoreWithNewBackups=true or named '{RESTORE_PASSIVE_SYNC_NAME}'. "
                    f"Debug: oc --context={context} -n {BACKUP_NAMESPACE} get restore.cluster.open-cluster-management.io -o wide",
                    critical=True,
                )
                return

            metadata = restore.get("metadata", {})
            restore_name = metadata.get("name", "") or RESTORE_PASSIVE_SYNC_NAME

            # Record GitOps markers if present (non-critical)
            try:
                record_gitops_markers(
                    context="secondary",
                    namespace=BACKUP_NAMESPACE,
                    kind="Restore",
                    name=restore_name,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning(
                    "GitOps marker recording failed for Restore %s: %s",
                    restore_name,
                    exc,
                )

            status = restore.get("status", {})
            phase = status.get("phase", "unknown")
            message = status.get("lastMessage", "")

            # "Enabled" = continuous sync running
            # "Finished"/"Completed" = initial sync completed successfully (valid for switchover)
            if phase in ("Enabled", "Finished", "Completed"):
                self.add_result(
                    "Passive sync restore",
                    True,
                    f"{restore_name} ready ({phase}): {message}",
                    critical=True,
                )
            else:
                # If the ACM restore controller referenced a Velero restore, surface its validation errors.
                velero_details = ""
                bsl_details = ""
                bsl_unavailable = _collect_bsl_unavailable_details(secondary)
                if bsl_unavailable:
                    bsl_details = (
                        " BackupStorageLocation issue(s): "
                        f"{bsl_unavailable}. Restore cannot proceed until BSL is Available."
                    )

                velero_match = re.search(r"Velero restore\s+(\S+)", message)
                if velero_match:
                    velero_restore_name = velero_match.group(1)
                    try:
                        velero_restore = secondary.get_custom_resource(
                            group="velero.io",
                            version="v1",
                            plural="restores",
                            name=velero_restore_name,
                            namespace=BACKUP_NAMESPACE,
                        )
                        if velero_restore:
                            velero_status = velero_restore.get("status", {})
                            velero_phase = velero_status.get("phase", "unknown")
                            validation_errors = (
                                velero_status.get("validationErrors") or []
                            )
                            if validation_errors:
                                joined = "; ".join(str(e) for e in validation_errors)
                                velero_details = f" Velero restore {velero_restore_name} phase={velero_phase} validationErrors={joined}."
                            else:
                                velero_details = f" Velero restore {velero_restore_name} phase={velero_phase}."
                    except Exception as exc:
                        logger.debug(
                            "Failed to fetch Velero restore %s details: %s",
                            velero_restore_name,
                            exc,
                        )

                error_message = (
                    f"{restore_name} in unexpected state: {phase} - {message}"
                )
                if velero_details:
                    error_message += f" {velero_details.strip()}"
                if bsl_details:
                    error_message += f"{bsl_details}"
                error_message += (
                    " (check ACM restore + Velero restore for details). "
                    f"Debug: oc --context={context} -n {BACKUP_NAMESPACE} get "
                    f"restore.cluster.open-cluster-management.io {restore_name} -o yaml; "
                    f"oc --context={context} -n {BACKUP_NAMESPACE} get restore.velero.io -o wide"
                )

                self.add_result(
                    "Passive sync restore",
                    False,
                    error_message,
                    critical=True,
                )
        except Exception as exc:
            self.add_result(
                "Passive sync restore",
                False,
                f"error checking passive sync: {exc}",
                critical=True,
            )


class ManagedClusterBackupValidator(BaseValidator):
    """Validates that all joined ManagedClusters are included in the latest backup."""

    def run(self, primary: KubeClient) -> None:  # noqa: C901
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
                if mc_name == LOCAL_CLUSTER_NAME:
                    continue

                # Check if cluster is joined (has Joined condition = True)
                conditions = mc.get("status", {}).get("conditions", [])
                is_joined = any(
                    c.get("type") == "ManagedClusterJoined"
                    and c.get("status") == "True"
                    for c in conditions
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
                b
                for b in backups
                if b.get("metadata", {})
                .get("labels", {})
                .get("cluster.open-cluster-management.io/backup-schedule-type")
                == "managedClusters"
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

            # Get backup completion timestamp for comparison
            backup_completion_time = latest_backup.get("status", {}).get(
                "completionTimestamp", ""
            )
            clusters_after_backup = []

            if backup_completion_time:
                try:
                    # Parse backup completion time (ISO 8601 format)
                    backup_time = datetime.fromisoformat(
                        backup_completion_time.replace("Z", "+00:00")
                    )

                    # Check each joined cluster's creation time against backup time
                    for cluster_name in joined_clusters:
                        cluster_info = primary.get_custom_resource(
                            group="cluster.open-cluster-management.io",
                            version="v1",
                            plural="managedclusters",
                            name=cluster_name,
                        )
                        if cluster_info:
                            cluster_creation = cluster_info.get("metadata", {}).get(
                                "creationTimestamp", ""
                            )
                            if cluster_creation:
                                cluster_time = datetime.fromisoformat(
                                    cluster_creation.replace("Z", "+00:00")
                                )
                                if cluster_time > backup_time:
                                    clusters_after_backup.append(cluster_name)
                except (ValueError, TypeError) as e:
                    # If timestamp parsing fails, log warning but continue
                    logger.warning("Could not compare cluster timestamps: %s", e)

            # Report failure if clusters were imported after the backup
            if clusters_after_backup:
                self.add_result(
                    "Clusters imported after backup",
                    False,
                    f"clusters imported after latest backup will be lost: {', '.join(clusters_after_backup)}",
                    critical=True,  # Critical failure - these clusters will be lost on switchover
                )
            else:
                # Backup is completed - report success with joined cluster count
                # Note: joined_clusters is guaranteed non-empty (validated earlier in this method)
                self.add_result(
                    "ManagedClusters in backup",
                    True,
                    f"found {len(joined_clusters)} joined cluster(s), latest backup completed successfully",
                    critical=False,
                )

        except Exception as exc:
            self.add_result(
                "ManagedClusters in backup",
                False,
                f"error checking ManagedClusters backup: {exc}",
                critical=True,
            )
