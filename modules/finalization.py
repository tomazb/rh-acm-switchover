"""
Finalization and rollback module for ACM switchover.
"""

# Runbook: Steps 11-12 (finalization) and Step 14 (old hub handling)

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kubernetes.client.rest import ApiException

from lib import argocd as argocd_lib
from lib.constants import (
    ACM_NAMESPACE,
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_INTEGRITY_MAX_AGE_SECONDS,
    BACKUP_NAMESPACE,
    BACKUP_POLL_INTERVAL,
    BACKUP_SCHEDULE_DEFAULT_NAME,
    BACKUP_SCHEDULE_DELETE_WAIT,
    BACKUP_VERIFY_TIMEOUT,
    DELETE_REQUEST_TIMEOUT,
    IMPORT_CONTROLLER_CONFIG_CM,
    LOCAL_CLUSTER_NAME,
    MCE_NAMESPACE,
    MCH_VERIFY_INTERVAL,
    MCH_VERIFY_TIMEOUT,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
    OBSERVATORIUM_API_DEPLOYMENT,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
    THANOS_COMPACTOR_STATEFULSET,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
)
from lib.exceptions import SwitchoverError
from lib.gitops_detector import record_gitops_markers
from lib.kube_client import KubeClient
from lib.utils import StateManager, dry_run_skip, is_acm_version_ge
from lib.waiter import wait_for_condition

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
        disable_observability_on_secondary: bool = False,
        argocd_resume_after_switchover: bool = False,
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.primary = primary_client
        self.primary_has_observability = primary_has_observability
        self.dry_run = dry_run
        self.old_hub_action = old_hub_action  # "secondary", "decommission", or "none"
        self.manage_auto_import_strategy = manage_auto_import_strategy
        self.disable_observability_on_secondary = disable_observability_on_secondary
        self.argocd_resume_after_switchover = argocd_resume_after_switchover
        self.backup_manager = BackupScheduleManager(
            secondary_client,
            state_manager,
            "secondary hub",
            dry_run=dry_run,
        )
        self._cached_schedules: Optional[List[Dict]] = None  # Cache for backup schedules

    def finalize(self) -> bool:  # noqa: C901
        """
        Execute finalization steps.

        Returns:
            True if finalization completed successfully
        """
        logger.info("Starting finalization...")

        try:
            # Optional: Disable Observability on old secondary hub before enabling backups
            if self.disable_observability_on_secondary:
                with self.state.step("disable_observability_on_secondary", logger) as should_run:
                    if should_run:
                        self._disable_observability_on_secondary()

            # Step 11: Enable BackupSchedule on new hub
            with self.state.step("enable_backup_schedule", logger) as should_run:
                if should_run:
                    self._enable_backup_schedule()

            with self.state.step("verify_backup_schedule_enabled", logger) as should_run:
                if should_run:
                    self._verify_backup_schedule_enabled()

            # Fix BackupSchedule collision if detected
            with self.state.step("fix_backup_collision", logger) as should_run:
                if should_run:
                    self._fix_backup_schedule_collision()

            # Verify new backups are being created
            with self.state.step("verify_new_backups", logger) as should_run:
                if should_run:
                    self._verify_new_backups(timeout=self._get_backup_verify_timeout())

            with self.state.step("verify_backup_integrity", logger) as should_run:
                if should_run:
                    self._verify_backup_integrity()

            with self.state.step("verify_mch_health", logger) as should_run:
                if should_run:
                    self._verify_multiclusterhub_health()

            # Ensure auto-import strategy reset to default (ACM 2.14+)
            self._ensure_auto_import_default()

            # Optional: Restore Argo CD auto-sync (only when explicitly requested)
            if self.argocd_resume_after_switchover:
                with self.state.step("resume_argocd_apps", logger) as should_run:
                    if should_run:
                        self._resume_argocd_apps()

            # Handle old primary hub based on --old-hub-action
            with self.state.step("handle_old_hub", logger) as should_run:
                if should_run:
                    self._handle_old_hub()

            if self.primary and self.old_hub_action not in ("decommission", "none"):
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
        self.state.set_config("backup_schedule_enabled_at", datetime.now(timezone.utc).isoformat())
        self.state.set_config("new_backup_detected", False)

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
                    timeout_seconds=DELETE_REQUEST_TIMEOUT,
                )
                logger.info("Deleted restore resource: %s", restore_name)
            except Exception as e:
                # Not found is OK, other errors should be logged
                if "not found" not in str(e).lower():
                    logger.warning("Error deleting restore %s: %s", restore_name, e)

        # Save archived restores to state for audit trail
        if archived_restores:
            self.state.set_config("archived_restores", archived_restores)
            logger.info("Saved %s restore record(s) to state file", len(archived_restores))

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
                "veleroManagedClustersBackupName": spec.get("veleroManagedClustersBackupName"),
                "veleroCredentialsBackupName": spec.get("veleroCredentialsBackupName"),
                "veleroResourcesBackupName": spec.get("veleroResourcesBackupName"),
            },
            "sync_restore_with_new_backups": spec.get("syncRestoreWithNewBackups"),
            "restore_sync_interval": spec.get("restoreSyncInterval"),
            "cleanup_before_restore": spec.get("cleanupBeforeRestore"),
            # Status details
            "phase": status.get("phase"),
            "last_message": status.get("lastMessage"),
            "velero_managed_clusters_restore_name": status.get("veleroManagedClustersRestoreName"),
            "velero_credentials_restore_name": status.get("veleroCredentialsRestoreName"),
            "velero_resources_restore_name": status.get("veleroResourcesRestoreName"),
        }

    @dry_run_skip(message="Skipping new backup verification")
    def _verify_new_backups(self, timeout: int = BACKUP_VERIFY_TIMEOUT):
        """
        Verify new backups are being created.

        Args:
            timeout: Maximum wait time in seconds
        """

        logger.info("Verifying new backups are being created...")

        # Get current backup list (Velero Backups use velero.io/v1)
        initial_backups = self.secondary.list_custom_resources(
            group="velero.io",
            version="v1",
            plural="backups",
            namespace=BACKUP_NAMESPACE,
        )

        initial_backup_names = {b.get("metadata", {}).get("name") for b in initial_backups}

        logger.info("Found %s existing backup(s)", len(initial_backups))
        logger.info("Waiting for new backup to appear (timeout: %ss)...", timeout)

        start_time = time.time()

        while time.time() - start_time < timeout:
            current_backups = self.secondary.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
            )

            current_backup_names = {b.get("metadata", {}).get("name") for b in current_backups}

            # Check for new backups
            new_backups = current_backup_names - initial_backup_names

            if new_backups:
                logger.info("New backup(s) detected: %s", ", ".join(new_backups))

                # Verify at least one is in progress or completed
                for backup_name in new_backups:
                    backup = next(
                        (b for b in current_backups if b.get("metadata", {}).get("name") == backup_name),
                        None,
                    )

                    if backup:
                        phase = backup.get("status", {}).get("phase", "unknown")
                        logger.info("Backup %s phase: %s", backup_name, phase)

                        # Velero uses "InProgress" and "Completed" phases
                        if phase in ("InProgress", "Completed", "New"):
                            self.state.set_config("new_backup_detected", True)
                            logger.info("New backup is being created successfully!")
                            return

            elapsed = int(time.time() - start_time)
            logger.debug("Waiting for new backup... (elapsed: %ss)", elapsed)
            time.sleep(BACKUP_POLL_INTERVAL)

        logger.warning(
            f"No new backups detected after {timeout}s. " "BackupSchedule may take time to create first backup."
        )

    def _get_backup_verify_timeout(self) -> int:
        """Derive backup verification timeout from BackupSchedule cadence."""
        schedule_interval = self._get_backup_schedule_interval_seconds()
        if schedule_interval:
            derived_timeout = max(BACKUP_VERIFY_TIMEOUT, schedule_interval)
            logger.info(
                "Derived backup verification timeout from schedule cadence: %ss (interval=%ss)",
                derived_timeout,
                schedule_interval,
            )
            return derived_timeout
        return BACKUP_VERIFY_TIMEOUT

    def _get_backup_max_age_seconds(self, default_max_age: int) -> int:
        schedule_interval = self._get_backup_schedule_interval_seconds()
        if schedule_interval and schedule_interval > default_max_age:
            derived_max_age = schedule_interval + default_max_age
            logger.info(
                "Derived backup age threshold from schedule cadence: %ss (interval=%ss)",
                derived_max_age,
                schedule_interval,
            )
            return derived_max_age
        return default_max_age

    def _get_backup_schedule_enabled_at(self) -> Optional[datetime]:
        enabled_at_raw = self.state.get_config("backup_schedule_enabled_at")
        enabled_at = self._parse_timestamp(enabled_at_raw)
        if enabled_at:
            return enabled_at

        steps = getattr(self.state, "state", {}).get("completed_steps", [])
        for step in steps:
            if step.get("name") == "enable_backup_schedule":
                return self._parse_timestamp(step.get("timestamp"))
        return None

    def _get_backup_schedule_interval_seconds(self) -> Optional[int]:
        schedules = self._get_backup_schedules()
        if not schedules:
            logger.warning("No BackupSchedule found; using default backup verification timeout")
            return None

        schedule = schedules[0]
        schedule_name = schedule.get("metadata", {}).get("name", BACKUP_SCHEDULE_DEFAULT_NAME)
        spec = schedule.get("spec", {}) or {}
        cron_expr = spec.get("veleroSchedule")

        if not cron_expr:
            logger.warning(
                "BackupSchedule %s missing spec.veleroSchedule; using default backup verification timeout",
                schedule_name,
            )
            return None

        interval = self._parse_cron_interval_seconds(cron_expr)
        if interval is None:
            logger.warning(
                "Unable to derive schedule cadence from BackupSchedule %s veleroSchedule=%s; "
                "using default backup verification timeout",
                schedule_name,
                cron_expr,
            )
        return interval

    @staticmethod
    def _parse_timestamp(timestamp: Optional[str]) -> Optional[datetime]:
        """Parse a Kubernetes timestamp into a timezone-aware datetime."""
        if not timestamp:
            return None
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_cron_interval_seconds(cron_expr: str) -> Optional[int]:
        """Estimate cron schedule interval in seconds for common Velero patterns.

        This is a simplified parser that supports typical Velero-style expressions
        such as '*/15 * * * *', '0 */4 * * *', or '0 0 * * *'. It does not handle
        all valid cron syntax (lists like '1,15,30', ranges like '1-5', or complex
        day-of-week/month combinations) and may return None or an approximate
        interval for those cases.
        """
        fields = cron_expr.split()
        if len(fields) != 5:
            return None

        minute, hour, dom, month, dow = fields

        def _parse_every(field: str) -> Optional[int]:
            if field.startswith("*/"):
                value = field[2:]
                if value.isdigit():
                    interval = int(value)
                    if interval > 0:
                        return interval
            return None

        def _is_number(field: str) -> bool:
            return field.isdigit()

        every_minute = _parse_every(minute)
        if every_minute and hour == dom == month == dow == "*":
            return every_minute * 60

        every_hour = _parse_every(hour)
        if every_hour and dom == month == dow == "*" and _is_number(minute):
            return every_hour * 3600

        every_day = _parse_every(dom)
        if every_day and month == dow == "*" and _is_number(minute) and _is_number(hour):
            return every_day * 86400

        if dom == "*" and month == "*" and dow == "*" and _is_number(minute) and _is_number(hour):
            return 86400

        if dom == "*" and month == "*" and _is_number(minute) and _is_number(hour) and dow.isdigit():
            return 7 * 86400

        if _is_number(minute) and _is_number(hour) and dom.isdigit() and month == "*" and dow == "*":
            return 30 * 86400

        return None

    def _check_velero_logs_for_backup(self, backup_name: str, tail_lines: int = 2000) -> None:
        """Scan recent Velero logs for errors related to a backup."""
        try:
            velero_pods = self.secondary.get_pods(
                namespace=BACKUP_NAMESPACE,
                label_selector="app.kubernetes.io/name=velero",
            )
        except Exception as e:
            logger.warning("Unable to list Velero pods for log inspection: %s", e)
            return

        if not velero_pods:
            logger.warning("No Velero pods found for log inspection")
            return

        error_hits = 0
        for pod in velero_pods:
            pod_name = pod.get("metadata", {}).get("name")
            if not pod_name:
                continue
            try:
                logs = self.secondary.get_pod_logs(
                    name=pod_name,
                    namespace=BACKUP_NAMESPACE,
                    container="velero",
                    tail_lines=tail_lines,
                )
            except Exception as e:
                logger.warning("Unable to read Velero logs from %s: %s", pod_name, e)
                continue

            if not logs:
                continue

            lines = [line for line in logs.splitlines() if backup_name in line]
            error_lines = [line for line in lines if "error" in line.lower() or "failed" in line.lower()]
            if error_lines:
                error_hits += len(error_lines)
                logger.warning(
                    "Velero logs from %s show %s error line(s) for backup %s",
                    pod_name,
                    len(error_lines),
                    backup_name,
                )

        if error_hits == 0:
            logger.info(
                "No Velero log errors found for backup %s (recent logs checked)",
                backup_name,
            )

    @dry_run_skip(message="Skipping backup integrity verification")
    def _verify_backup_integrity(self, max_age_seconds: int = BACKUP_INTEGRITY_MAX_AGE_SECONDS) -> None:  # noqa: C901
        """Verify latest backup status, logs, and recency.

        Backup age enforcement is skipped until a new backup is detected after enabling the schedule.
        """
        logger.info("Verifying backup integrity...")
        effective_max_age_seconds = self._get_backup_max_age_seconds(max_age_seconds)

        backups = self.secondary.list_custom_resources(
            group="velero.io",
            version="v1",
            plural="backups",
            namespace=BACKUP_NAMESPACE,
        )

        if not backups:
            raise SwitchoverError("No Velero backups found for integrity verification")

        def _backup_sort_key(backup: Dict) -> str:
            return backup.get("metadata", {}).get("creationTimestamp", "") or ""

        latest_backup = max(backups, key=_backup_sort_key)
        backup_name = latest_backup.get("metadata", {}).get("name", "unknown")
        status = latest_backup.get("status", {}) or {}

        phase = status.get("phase", "unknown")

        def _to_int(value: Optional[Any]) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        if phase != "Completed":
            if phase in ("New", "InProgress"):
                backup_verify_timeout = self._get_backup_verify_timeout()

                def _poll_backup_completion() -> Tuple[bool, str]:
                    backup = self.secondary.get_custom_resource(
                        group="velero.io",
                        version="v1",
                        plural="backups",
                        name=backup_name,
                        namespace=BACKUP_NAMESPACE,
                    )
                    if not backup:
                        raise SwitchoverError(f"Backup {backup_name} disappeared during integrity check")
                    poll_phase = backup.get("status", {}).get("phase", "unknown")
                    if poll_phase == "Completed":
                        return True, "completed"
                    if poll_phase in ("Failed", "PartiallyFailed"):
                        raise SwitchoverError(f"Latest backup {backup_name} failed (phase={poll_phase})")
                    return False, f"phase={poll_phase}"

                completed = wait_for_condition(
                    f"backup {backup_name} completion",
                    _poll_backup_completion,
                    timeout=backup_verify_timeout,
                    interval=BACKUP_POLL_INTERVAL,
                    logger=logger,
                )
                if not completed:
                    raise SwitchoverError(
                        f"Latest backup {backup_name} did not complete within {backup_verify_timeout}s"
                    )
                latest_backup = (
                    self.secondary.get_custom_resource(
                        group="velero.io",
                        version="v1",
                        plural="backups",
                        name=backup_name,
                        namespace=BACKUP_NAMESPACE,
                    )
                    or latest_backup
                )
                status = latest_backup.get("status", {}) or {}
            else:
                raise SwitchoverError(f"Latest backup {backup_name} not completed (phase={phase})")

        errors = _to_int(status.get("errors"))
        warnings = _to_int(status.get("warnings"))
        if errors > 0:
            raise SwitchoverError(f"Latest backup {backup_name} completed with {errors} error(s)")
        if warnings > 0:
            logger.warning("Latest backup %s completed with %s warning(s)", backup_name, warnings)

        enabled_at = self._get_backup_schedule_enabled_at()
        new_backup_detected = bool(self.state.get_config("new_backup_detected", False))

        completion_ts = status.get("completionTimestamp") or status.get("startTimestamp")
        creation_ts = latest_backup.get("metadata", {}).get("creationTimestamp")
        ts = completion_ts or creation_ts
        parsed_ts = self._parse_timestamp(ts)

        if not parsed_ts:
            logger.warning(
                "Unable to parse timestamp for backup %s (timestamp=%s)",
                backup_name,
                ts,
            )
        else:
            age_seconds = int((datetime.now(timezone.utc) - parsed_ts).total_seconds())
            backup_after_enable = False
            if enabled_at:
                backup_after_enable = parsed_ts >= enabled_at
            else:
                backup_after_enable = new_backup_detected

            if not backup_after_enable:
                logger.warning(
                    "No new backups detected since enabling BackupSchedule; "
                    "skipping backup age enforcement for %s (latest: %ss old)",
                    backup_name,
                    age_seconds,
                )
            else:
                if age_seconds > effective_max_age_seconds:
                    raise SwitchoverError(
                        f"Latest backup {backup_name} is too old ({age_seconds}s > {effective_max_age_seconds}s)"
                    )
                logger.info("Latest backup %s completed %ss ago", backup_name, age_seconds)

        self._check_velero_logs_for_backup(backup_name)

    def _get_backup_schedules(self, force_refresh: bool = False) -> List[Dict]:
        """Get backup schedules with caching.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            List of backup schedule resources
        """
        if self._cached_schedules is None or force_refresh:
            self._cached_schedules = self.secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                namespace=BACKUP_NAMESPACE,
                max_items=1,
            )
        return self._cached_schedules

    @dry_run_skip(message="Skipping BackupSchedule verification")
    def _verify_backup_schedule_enabled(self):
        """Ensure BackupSchedule is present and not paused."""

        schedules = self._get_backup_schedules()

        if not schedules:
            raise RuntimeError("No BackupSchedule found while verifying finalization")

        schedule = schedules[0]
        schedule_name = schedule.get("metadata", {}).get("name", "schedule-rhacm")
        paused = schedule.get("spec", {}).get("paused", False)

        if paused:
            raise RuntimeError(f"BackupSchedule {schedule_name} is still paused")

        logger.info("BackupSchedule %s is enabled", schedule_name)

    @dry_run_skip(message="Skipping MultiClusterHub health verification")
    def _verify_multiclusterhub_health(self, timeout: int = MCH_VERIFY_TIMEOUT, interval: int = MCH_VERIFY_INTERVAL):
        """Ensure MultiClusterHub reports healthy and pods are running, with wait."""

        logger.info("Verifying MultiClusterHub health...")
        start = time.time()

        while True:
            try:
                mch = self.secondary.get_custom_resource(
                    group="operator.open-cluster-management.io",
                    version="v1",
                    plural="multiclusterhubs",
                    name="multiclusterhub",
                    namespace=ACM_NAMESPACE,
                )
            except ApiException as e:
                if getattr(e, "status", None) == 404:
                    mch = None
                else:
                    raise

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

            pods = self.secondary.get_pods(namespace=ACM_NAMESPACE)
            non_running = [
                pod.get("metadata", {}).get("name", "unknown")
                for pod in pods
                if pod.get("status", {}).get("phase") != "Running"
            ]

            if phase == "Running" and not non_running:
                logger.info("MultiClusterHub %s is Running and all pods are healthy", mch_name)
                return

            elapsed = time.time() - start
            if elapsed >= timeout:
                details = ", non-running pods=" + (", ".join(non_running) if non_running else "none")
                raise RuntimeError(f"MultiClusterHub {mch_name} not healthy after {timeout}s (phase={phase}{details})")

            logger.info(
                "Waiting for MultiClusterHub %s to become healthy (phase=%s, non-running pods=%s)...",
                mch_name,
                phase,
                ", ".join(non_running) if non_running else "none",
            )
            time.sleep(interval)

    def _disable_observability_on_secondary(self) -> None:
        """Delete MultiClusterObservability on old hub (optional)."""
        if not self.primary:
            logger.info("No primary client available, skipping observability disablement")
            return
        if self.old_hub_action != "secondary":
            logger.info(
                "Skipping observability disablement (old hub action is '%s')",
                self.old_hub_action,
            )
            return

        logger.info("Disabling observability on old hub by deleting MultiClusterObservability...")

        mcos = self.primary.list_custom_resources(
            group="observability.open-cluster-management.io",
            version="v1beta2",
            plural="multiclusterobservabilities",
        )

        if not mcos:
            logger.info("No MultiClusterObservability resources found on old hub")
            return

        for mco in mcos:
            metadata = mco.get("metadata", {})
            mco_name = metadata.get("name", "unknown")
            record_gitops_markers(
                context="primary",
                namespace="",  # MCO is cluster-scoped
                kind="MultiClusterObservability",
                name=mco_name,
                metadata=metadata,
            )
            if self.dry_run:
                logger.info("[DRY-RUN] Would delete MultiClusterObservability: %s", mco_name)
                continue

            logger.info("Deleting MultiClusterObservability: %s", mco_name)
            try:
                self.primary.delete_custom_resource(
                    group="observability.open-cluster-management.io",
                    version="v1beta2",
                    plural="multiclusterobservabilities",
                    name=mco_name,
                    timeout_seconds=DELETE_REQUEST_TIMEOUT,
                )
            except ApiException as e:
                if getattr(e, "status", None) == 404:
                    logger.info("MultiClusterObservability %s already deleted", mco_name)
                else:
                    raise

        if self.dry_run:
            logger.info("[DRY-RUN] Skipping observability termination check")
            return

        def _observability_terminated():
            pods = self.primary.get_pods(namespace=OBSERVABILITY_NAMESPACE)
            if not pods:
                return True, "no observability pods remaining"
            return False, f"{len(pods)} pod(s) remaining"

        success = wait_for_condition(
            "observability pod termination on old hub",
            _observability_terminated,
            timeout=OBSERVABILITY_TERMINATE_TIMEOUT,
            interval=OBSERVABILITY_TERMINATE_INTERVAL,
            logger=logger,
        )

        if not success:
            remaining = self.primary.get_pods(namespace=OBSERVABILITY_NAMESPACE)
            if remaining:
                logger.warning(
                    "Observability pods still running after MCO deletion (%s pods). "
                    "If GitOps is not recreating MCO, this may indicate a product bug.",
                    len(remaining),
                )

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
            logger.info("Setting up old primary hub as new secondary (for failback capability)...")
            self._setup_old_hub_as_secondary()
            return

        if self.old_hub_action == "decommission":
            logger.info("Decommissioning old primary hub...")
            self._decommission_old_hub()
            return

        logger.warning("Unknown old_hub_action: %s, skipping", self.old_hub_action)

    @dry_run_skip(message="Would decommission old primary hub")
    def _decommission_old_hub(self):
        """
        Decommission the old primary hub by removing ACM components.

        This is run non-interactively as part of the switchover finalization.
        """
        if not self.primary:
            logger.debug("No primary client available, skipping decommission")
            return

        logger.warning("=" * 60)
        logger.warning("DECOMMISSIONING OLD PRIMARY HUB")
        logger.warning("This will remove ACM components from the old hub!")
        logger.warning("=" * 60)

        decom = Decommission(self.primary, self.primary_has_observability, dry_run=self.dry_run)

        # Run decommission non-interactively since we're in automated mode
        if decom.decommission(interactive=False):
            logger.info("Old hub decommissioned successfully")
        else:
            logger.warning("Old hub decommission completed with warnings")
            logger.warning("You may need to manually clean up remaining resources")

    @dry_run_skip(message="Would set up old primary as secondary with passive sync")
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
            logger.warning("Failed to create passive sync restore on old primary: %s", e)
            logger.warning("You may need to manually create it for failback capability")

    @dry_run_skip(message="Would recreate BackupSchedule to prevent collision")
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
        # Check current BackupSchedule status (force refresh to get latest state)
        schedules = self._get_backup_schedules(force_refresh=True)

        if not schedules:
            logger.warning("No BackupSchedule found on new primary")
            return

        schedule = schedules[0]
        schedule_name = schedule.get("metadata", {}).get("name", BACKUP_SCHEDULE_DEFAULT_NAME)
        phase = schedule.get("status", {}).get("phase", "")

        # Proactively recreate to prevent collision, or fix if already in collision
        # The collision may not appear immediately - it only shows after Velero
        # schedules run and detect backups from a different cluster ID
        if phase == "BackupCollision":
            logger.warning("BackupSchedule %s has collision, recreating...", schedule_name)
        else:
            logger.info(
                "Proactively recreating BackupSchedule %s to prevent future collision " "(current phase: %s)",
                schedule_name,
                phase or "Unknown",
            )

        schedule_uid = schedule.get("metadata", {}).get("uid")
        # Save the spec for recreation
        schedule_spec = schedule.get("spec", {})

        # Re-verify schedule still exists before deletion (handles race conditions)
        current_schedule = self.secondary.get_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            name=schedule_name,
            namespace=BACKUP_NAMESPACE,
        )

        if not current_schedule:
            logger.warning(
                "BackupSchedule %s no longer exists (may have been deleted by another process), creating new one",
                schedule_name,
            )
            # Skip deletion, just create new schedule below
        else:
            current_uid = current_schedule.get("metadata", {}).get("uid")
            if schedule_uid and current_uid and current_uid != schedule_uid:
                logger.warning(
                    "BackupSchedule %s changed (uid %s -> %s); skipping recreation to avoid deleting unexpected resource",
                    schedule_name,
                    schedule_uid,
                    current_uid,
                )
                return

            # Use latest spec to avoid recreating from stale data
            schedule_spec = current_schedule.get("spec", {})

            # Delete the old schedule
            try:
                self.secondary.delete_custom_resource(
                    group="cluster.open-cluster-management.io",
                    version="v1beta1",
                    plural="backupschedules",
                    name=schedule_name,
                    namespace=BACKUP_NAMESPACE,
                    timeout_seconds=DELETE_REQUEST_TIMEOUT,
                )
                logger.info("Deleted old BackupSchedule %s", schedule_name)
            except ApiException as e:
                # If deletion fails with 404, schedule was already deleted
                if e.status == 404:
                    logger.info("BackupSchedule %s already deleted", schedule_name)
                else:
                    raise

        # Wait a moment for deletion to complete (even if already deleted, wait for API sync)
        time.sleep(BACKUP_SCHEDULE_DELETE_WAIT)

        # Recreate the schedule
        try:
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
            logger.info("Recreated BackupSchedule %s to prevent collision", schedule_name)
            # Invalidate cache since we recreated the schedule
            self._cached_schedules = None

        except Exception as e:
            logger.warning("Failed to recreate BackupSchedule: %s", e)
            logger.warning("You may need to manually delete and recreate the BackupSchedule")

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
            if name == LOCAL_CLUSTER_NAME:
                continue

            conditions = cluster.get("status", {}).get("conditions", [])
            available = any(
                c.get("type") == "ManagedClusterConditionAvailable" and c.get("status") == "True" for c in conditions
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
            self._scale_down_old_hub_observability()

    def _scale_down_old_hub_observability(self) -> None:
        """
        Scale down observability components on the old primary hub.

        Scales thanos-compact and observatorium-api to 0 replicas, then waits
        for pods to terminate with polling. Reports status of scale-down operation.
        """
        # Check both thanos-compact and observatorium-api pods
        compactor_pods = self.primary.get_pods(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector="app.kubernetes.io/name=thanos-compact",
        )
        api_pods = self.primary.get_pods(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector="app.kubernetes.io/name=observatorium-api",
        )

        # Issue scale-down commands (dry-run aware)
        if not self.dry_run:
            if compactor_pods:
                logger.info("Scaling down thanos-compact on old hub")
                self.primary.scale_statefulset(THANOS_COMPACTOR_STATEFULSET, OBSERVABILITY_NAMESPACE, 0)

            if api_pods:
                logger.info("Scaling down observatorium-api on old hub")
                self.primary.scale_deployment(OBSERVATORIUM_API_DEPLOYMENT, OBSERVABILITY_NAMESPACE, 0)

        # Wait for pods to terminate with polling
        compactor_pods_after, api_pods_after = self._wait_for_observability_scale_down(compactor_pods, api_pods)

        # Report status
        self._report_observability_scale_down_status(compactor_pods, api_pods, compactor_pods_after, api_pods_after)

    def _wait_for_observability_scale_down(
        self,
        compactor_pods: List[Dict],
        api_pods: List[Dict],
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Wait for observability pods to scale down with polling.

        Args:
            compactor_pods: Initial thanos-compact pods
            api_pods: Initial observatorium-api pods

        Returns:
            Tuple of (compactor_pods_after, api_pods_after) after waiting
        """
        compactor_pods_after = []
        api_pods_after = []

        if not self.dry_run and (compactor_pods or api_pods):
            logger.debug(
                "Waiting for observability pods to scale down (timeout=%ds, interval=%ds)",
                OBSERVABILITY_TERMINATE_TIMEOUT,
                OBSERVABILITY_TERMINATE_INTERVAL,
            )
            start_time = time.time()

            while time.time() - start_time < OBSERVABILITY_TERMINATE_TIMEOUT:
                if compactor_pods:
                    compactor_pods_after = self.primary.get_pods(
                        namespace=OBSERVABILITY_NAMESPACE,
                        label_selector="app.kubernetes.io/name=thanos-compact",
                    )
                if api_pods:
                    api_pods_after = self.primary.get_pods(
                        namespace=OBSERVABILITY_NAMESPACE,
                        label_selector="app.kubernetes.io/name=observatorium-api",
                    )

                # Check if both are scaled down
                compactor_done = not compactor_pods or not compactor_pods_after
                api_done = not api_pods or not api_pods_after

                if compactor_done and api_done:
                    break

                time.sleep(OBSERVABILITY_TERMINATE_INTERVAL)

        return compactor_pods_after, api_pods_after

    def _report_observability_scale_down_status(
        self,
        compactor_pods: List[Dict],
        api_pods: List[Dict],
        compactor_pods_after: List[Dict],
        api_pods_after: List[Dict],
    ) -> None:
        """
        Report the status of observability scale-down on old hub.

        Args:
            compactor_pods: Initial thanos-compact pods
            api_pods: Initial observatorium-api pods
            compactor_pods_after: Remaining thanos-compact pods after wait
            api_pods_after: Remaining observatorium-api pods after wait
        """
        if self.dry_run:
            if compactor_pods:
                logger.info("[DRY-RUN] Would scale down thanos-compact on old hub")
            if api_pods:
                logger.info("[DRY-RUN] Would scale down observatorium-api on old hub")
            return

        # Report individual component status
        self._log_component_scale_status("Thanos compactor", compactor_pods, compactor_pods_after)
        self._log_component_scale_status("Observatorium API", api_pods, api_pods_after)

        # Report overall status
        if compactor_pods_after or api_pods_after:
            logger.warning(
                "Old hub: MultiClusterObservability is still active (%s). Scale both to 0 or remove MCO.",
                f"thanos-compact={len(compactor_pods_after)}, observatorium-api={len(api_pods_after)}",
            )
        else:
            logger.info("All observability components scaled down on old hub")

    def _log_component_scale_status(
        self,
        component_name: str,
        initial_pods: List[Dict],
        remaining_pods: List[Dict],
    ) -> None:
        """Log scale-down status for a single observability component."""
        if not initial_pods:
            return

        if remaining_pods:
            logger.warning(
                "%s still running on old hub (%s pod(s)) after waiting",
                component_name,
                len(remaining_pods),
            )
        else:
            logger.info("%s is scaled down on old hub", component_name)

    @dry_run_skip(message="Would resume Argo CD auto-sync for paused apps")
    def _resume_argocd_apps(self) -> None:
        """Restore auto-sync for Argo CD Applications recorded in state (only when --argocd-resume-after-switchover)."""
        if self.state.get_config("argocd_pause_dry_run", False):
            raise SwitchoverError(
                "Argo CD auto-sync resume requested, but the pause step was run in dry-run mode. "
                "Re-run pause without --dry-run to generate resumable state."
            )
        run_id = self.state.get_config("argocd_run_id")
        paused_apps = self.state.get_config("argocd_paused_apps") or []
        if not run_id or not paused_apps:
            logger.info("No Argo CD paused apps in state; skipping resume")
            return
        logger.info(
            "Resuming Argo CD auto-sync for %d Application(s) (run_id=%s)",
            len(paused_apps),
            run_id,
        )
        failures = 0
        for entry in paused_apps:
            if not isinstance(entry, dict):
                failures += 1
                logger.warning("  Skip entry with unexpected format in Argo CD pause state")
                continue
            hub = entry.get("hub")
            ns = entry.get("namespace")
            name = entry.get("name")
            orig = entry.get("original_sync_policy")
            if entry.get("dry_run"):
                failures += 1
                logger.warning("  Skip %s/%s (pause was dry-run only)", ns, name)
                continue
            if not all([hub, ns, name, orig is not None]):
                failures += 1
                logger.warning("  Skip entry missing required fields (hub=%s, namespace=%s, name=%s)", hub, ns, name)
                continue
            client = self.primary if hub == "primary" else self.secondary
            if not client:
                failures += 1
                logger.warning("  Skip %s/%s (no client for hub=%s)", ns, name, hub)
                continue
            result = argocd_lib.resume_autosync(client, ns, name, orig, run_id)
            if result.restored:
                logger.info("  Resumed %s/%s on %s", ns, name, hub)
            else:
                failures += 1
                logger.warning("  Failed %s/%s: %s", ns, name, result.skip_reason or "not restored")

        if failures:
            raise SwitchoverError(f"Argo CD auto-sync restore failed for {failures} Application(s)")

    def _ensure_auto_import_default(self) -> None:
        """Reset autoImportStrategy to default ImportOnly when applicable."""
        try:
            if not is_acm_version_ge(self.acm_version, "2.14.0"):
                return
            cm = self.secondary.get_configmap(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)
            if not cm:
                return
            strategy = (cm.get("data") or {}).get(AUTO_IMPORT_STRATEGY_KEY, "default")
            if strategy != AUTO_IMPORT_STRATEGY_SYNC:
                return
            if self.state.get_config("auto_import_strategy_set", False):
                logger.info(
                    "Removing %s/%s to restore default autoImportStrategy (%s)",
                    MCE_NAMESPACE,
                    IMPORT_CONTROLLER_CONFIG_CM,
                    AUTO_IMPORT_STRATEGY_DEFAULT,
                )
                self.secondary.delete_configmap(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)
                self.state.set_config("auto_import_strategy_set", False)
                # Mark step completed (idempotent - no-op if already completed)
                self.state.mark_step_completed("reset_auto_import_strategy")
            else:
                logger.warning(
                    "autoImportStrategy is %s; remove %s/%s to reset to default (%s)",
                    AUTO_IMPORT_STRATEGY_SYNC,
                    MCE_NAMESPACE,
                    IMPORT_CONTROLLER_CONFIG_CM,
                    AUTO_IMPORT_STRATEGY_DEFAULT,
                )
        except Exception as e:
            logger.warning("Unable to verify/reset auto-import strategy: %s", e)
