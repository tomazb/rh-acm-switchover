"""Finalization module for ACM switchover."""

# Runbook: Steps 11-12 (finalization) and Step 14 (old hub handling)

import logging
import math
import time
import uuid
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes.client.rest import ApiException

from lib.constants import (
    ACM_BACKUP_NAME_RE,
    ACM_BACKUP_SCHEDULE_TYPE_LABEL,
    ACM_BACKUP_SCHEDULE_TYPES,
    ACM_NAMESPACE,
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_INTEGRITY_MAX_AGE_SECONDS,
    BACKUP_NAMESPACE,
    BACKUP_POLL_INTERVAL,
    BACKUP_SCHEDULE_DEFAULT_NAME,
    BACKUP_SCHEDULE_DELETE_INTERVAL,
    BACKUP_SCHEDULE_DELETE_TIMEOUT,
    BACKUP_SCHEDULE_PLURAL,
    BACKUP_VERIFY_TIMEOUT,
    CLEANUP_BEFORE_RESTORE_VALUE,
    CLUSTER_BACKUP_API_GROUP,
    CLUSTER_BACKUP_API_VERSION,
    CLUSTER_BACKUP_API_VERSION_FULL,
    DELETE_REQUEST_TIMEOUT,
    IMPORT_CONTROLLER_CONFIG_CM,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_PLURAL,
    MANAGED_CLUSTER_RESTORE_NAME,
    MCE_NAMESPACE,
    MCH_VERIFY_INTERVAL,
    MCH_VERIFY_TIMEOUT,
    RESTORE_FAST_POLL_INTERVAL,
    RESTORE_FAST_POLL_TIMEOUT,
    RESTORE_FULL_NAME,
    RESTORE_PASSIVE_SYNC_NAME,
    RESTORE_PLURAL,
    RESTORE_POLL_INTERVAL,
    RESTORE_WAIT_TIMEOUT,
    SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS,
    SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
)
from lib.decommission_outcome import UNSUCCESSFUL_OUTCOMES
from lib.exceptions import FatalError, SwitchoverError, TransientError
from lib.kube_client import KubeClient, is_retryable_error
from lib.migration_cleanup import (
    absence_poll,
    cleanup_intent,
    cleanup_schedule,
    cleanup_step,
    delete_outcome,
    final_absence,
    generic_cleanup_restores,
    pre_delete,
    repair_cleanup,
    require_locator_absent,
    teardown_revalidated,
)
from lib.migration_evidence import MigrationEvidenceError
from lib.run_record import RunRecord, StateStructureError
from lib.strict_read import StrictReadStatus
from lib.utils import Phase, StateManager, dry_run_skip, is_acm_version_ge
from lib.waiter import WaitConditionResult, wait_for_condition, wait_for_restore_deletion

from .activation import SecondaryActivation
from .backup_schedule import BackupScheduleManager, fail_on_multiple_backup_schedules
from .decommission import Decommission

logger = logging.getLogger("acm_switchover")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# A strict read that would end after the absence-poll deadline: never classified.
_DEADLINE_PASSED = object()


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
        disable_observability_on_secondary: bool = False,  # deprecated, no-op
        restore_only: bool = False,
        acknowledge_observability_not_migrated: bool = False,
        migration_cleanup_repair: Optional[Dict] = None,
    ):
        # `disable_observability_on_secondary` is deprecated and intentionally
        # unused: old-hub observability cleanup is now driven by detected
        # observability state, not this flag. Kept in the signature only for
        # CLI/back-compat. Explicitly discard it to make the intent clear.
        del disable_observability_on_secondary
        self.secondary = secondary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.primary = primary_client
        self.primary_has_observability = primary_has_observability
        self.dry_run = dry_run
        self.old_hub_action = old_hub_action  # "secondary", "decommission", or "none"
        self.manage_auto_import_strategy = manage_auto_import_strategy
        self.restore_only = restore_only
        # Operator acknowledgement that destination observability was never migrated.
        # CLI validation restricts it to old_hub_action=decommission; it converts one
        # proven-absent destination block and nothing else.
        self.acknowledge_observability_not_migrated = acknowledge_observability_not_migrated
        # A validated --repair-migration-cleanup request (lib.validation): an audited
        # acknowledgement checked against the journal and a fresh strict read, never evidence.
        self.migration_cleanup_repair = migration_cleanup_repair
        self.backup_manager = BackupScheduleManager(
            secondary_client,
            state_manager,
            "secondary hub",
            dry_run=dry_run,
        )
        self._cached_schedules: Optional[List[Dict]] = None  # Cache for backup schedules
        # The migration journal this pass reconciled; every enabled-BackupSchedule write is gated on it.
        self._cleanup_journal: Optional[Dict] = None
        self.run_record = RunRecord(self.state)

    def _step(self, step_name: str) -> AbstractContextManager:
        """Guard one step; a dry-run consults completion markers but never records one.

        A dry-run skips the step's work, so a marker it recorded would let a later
        executing run skip work that never ran.
        """
        if self.dry_run:
            return nullcontext(not self.state.is_step_completed(step_name))
        return self.state.step(step_name, logger)

    @staticmethod
    def _get_acm_backup_ownership_signal(backup: Dict) -> Optional[str]:
        """Return the ownership signal proving a Velero backup is ACM-owned.

        Preferred signal is the ACM backup-schedule-type label. As a hardened
        fallback, accept the well-known ACM backup naming convention if the
        label is missing.
        """
        labels = backup.get("metadata", {}).get("labels", {}) or {}
        label_value = labels.get(ACM_BACKUP_SCHEDULE_TYPE_LABEL)
        if label_value in ACM_BACKUP_SCHEDULE_TYPES:
            return "label"

        backup_name = backup.get("metadata", {}).get("name", "")
        if ACM_BACKUP_NAME_RE.match(backup_name):
            return "name-pattern"

        return None

    def _is_acm_owned_backup(self, backup: Dict) -> bool:
        """Return True when the backup has a recognized ACM ownership signal."""
        return self._get_acm_backup_ownership_signal(backup) is not None

    def _filter_acm_owned_backups(self, backups: List[Dict]) -> List[Dict]:
        """Filter Velero backups down to ACM-owned backups only.

        Warn when the label is missing but a known ACM backup name pattern is
        used as the fallback ownership signal.
        """
        acm_backups = []
        for backup in backups:
            signal = self._get_acm_backup_ownership_signal(backup)
            if signal == "name-pattern":
                backup_name = backup.get("metadata", {}).get("name", "unknown")
                logger.warning(
                    "Backup %s is missing ACM ownership label %s; accepting ACM name-pattern fallback",
                    backup_name,
                    ACM_BACKUP_SCHEDULE_TYPE_LABEL,
                )
            if signal:
                acm_backups.append(backup)
        return acm_backups

    @staticmethod
    def _backup_is_detectable(backup: Dict) -> bool:
        """Return True when a backup phase proves backup creation has started."""
        phase = backup.get("status", {}).get("phase", "unknown")
        return phase in ("InProgress", "Completed", "New")

    def _backup_effective_timestamp(self, backup: Dict) -> Optional[datetime]:
        """Return the most useful timestamp for backup chronology checks."""
        status = backup.get("status", {}) or {}
        metadata = backup.get("metadata", {}) or {}
        candidate_fields = (
            ("completionTimestamp", status.get("completionTimestamp")),
            ("startTimestamp", status.get("startTimestamp")),
            ("creationTimestamp", metadata.get("creationTimestamp")),
        )
        for _field_name, raw_value in candidate_fields:
            parsed = self._parse_timestamp(raw_value)
            if parsed:
                return parsed
        raw_candidates = [f"{field}={value}" for field, value in candidate_fields if value]
        if raw_candidates:
            backup_name = metadata.get("name", "unknown")
            logger.warning(
                "Backup %s has unparseable timestamp(s): %s",
                backup_name,
                ", ".join(raw_candidates),
            )
        return None

    def _find_post_enable_backup(self, backups: List[Dict], enabled_at: Optional[datetime]) -> Optional[Dict]:
        """Find an ACM-owned backup that proves continuity after enable/resume."""
        if enabled_at is None:
            return None

        candidates = []
        for backup in backups:
            backup_ts = self._backup_effective_timestamp(backup)
            if backup_ts and backup_ts >= enabled_at and self._backup_is_detectable(backup):
                candidates.append((backup_ts, backup))

        if not candidates:
            return None

        # Pick the earliest post-enable backup so resume reuses the first proof point
        # instead of requiring a later backup to appear.
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def finalize(self) -> bool:  # noqa: C901
        """
        Execute finalization steps.

        Returns:
            True if finalization completed successfully
        """
        logger.info("Starting finalization...")

        try:
            # R4-04: the journaled Restore cleanup runs on every pass, ahead of every old-hub
            # action and of BackupSchedule enablement; the journal, not a marker, decides it.
            journal = self._run_restore_cleanup_transaction()
            self._cleanup_journal = journal

            # When the old hub remains as a secondary, the runbook requires MCO deletion.
            if self.primary and self.old_hub_action == "secondary" and self.primary_has_observability:
                with self._step("disable_observability_on_secondary") as should_run:
                    if should_run:
                        self._disable_observability_on_old_hub()

            # Step 11: Enable BackupSchedule on new hub
            with self._step("enable_backup_schedule") as should_run:
                if should_run:
                    self._enable_backup_schedule(journal)

            with self._step("verify_backup_schedule_enabled") as should_run:
                if should_run:
                    self._verify_backup_schedule_enabled()

            # Fix BackupSchedule collision if detected
            with self._step("fix_backup_collision") as should_run:
                if should_run:
                    self._fix_backup_schedule_collision()

            # Verify new backups are being created
            with self._step("verify_new_backups") as should_run:
                if should_run:
                    self._verify_new_backups(timeout=self._get_backup_verify_timeout())

            with self._step("verify_backup_integrity") as should_run:
                if should_run:
                    self._verify_backup_integrity()

            with self._step("verify_mch_health") as should_run:
                if should_run:
                    self._verify_multiclusterhub_health()

            # Ensure auto-import strategy reset to default (ACM 2.14+)
            self._run_reset_auto_import_strategy_step()

            # Handle old primary hub based on --old-hub-action
            with self._step("handle_old_hub") as should_run:
                if should_run:
                    self._handle_old_hub()

            if self.primary and self.old_hub_action not in ("decommission", "none"):
                with self._step("verify_old_hub_state") as should_run:
                    if should_run:
                        self._verify_old_hub_state()

            logger.info("Finalization completed successfully")
            return True

        except SwitchoverError as e:
            logger.error("Finalization failed: %s", e)
            self.state.add_error(str(e), Phase.FINALIZATION.value)
            return False
        except Exception as e:
            logger.error("Unexpected error during finalization: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", Phase.FINALIZATION.value)
            return False

    def _enable_backup_schedule(self, journal: Optional[Dict] = None):
        """Enable BackupSchedule on new hub (version-aware).

        Important: Before creating the BackupSchedule, we must delete any active
        restore resources. ACM will not allow a BackupSchedule to run while a
        restore is active (to prevent data corruption). The journaled Restore is
        already gone through its own transaction; a fresh strict read must still
        prove its locator empty immediately before enablement.
        """
        logger.info("Enabling BackupSchedule on new hub...")

        # First, delete any other switchover restore resources
        self._cleanup_restore_resources(journal)
        if not self.dry_run:
            self._require_journaled_locator_absent(journal)

        # Now create/enable the BackupSchedule
        self.backup_manager.ensure_enabled(self.acm_version)
        self.run_record.record_backup_watch_started(datetime.now(timezone.utc).isoformat())

    # -- R4-04 durable Restore-cleanup transaction (July design section 4a) --

    def _run_restore_cleanup_transaction(self) -> Optional[Dict]:
        """Reconcile the journaled Restore cleanup to a terminal state, or fail before any other work.

        The strict journal is loaded and validated first; the journal alone decides
        the step. A fresh teardown barrier and durable intent precede the guarded
        DELETE; recovery and an operator repair are durable journal writes. A
        dry-run reads and writes nothing and reports the step it would take.
        Returns the journal the rest of finalization relies on (None only in a
        dry-run without one).
        """
        if self.dry_run:
            return self._preview_restore_cleanup()
        journal = self._load_cleanup_journal()
        step = self._evidence(
            cleanup_step, journal=journal, repair_requested=self.migration_cleanup_repair is not None
        )["step"]
        if step == "repair":
            return self._repair_restore_cleanup(journal)
        if step in ("revalidate", "delete"):
            # The journaled controller lane decides the barrier and the guarded delete alike.
            SecondaryActivation(self.secondary, self.state).require_journaled_controller_contract(journal)
        if step == "revalidate":
            journal = self._revalidate_for_teardown(journal)
            operation_id = str(uuid.uuid4())
            journal = self._persist_decision(
                self._evidence(cleanup_intent, journal=journal, operation_id=operation_id, now=_now_iso())
            )
            logger.info("Recorded cleanup intent %s for Restore %s", operation_id, journal["restore"]["name"])
            step = "delete"
        if step == "delete":
            journal = self._delete_journaled_restore(journal)
        return journal

    def _preview_restore_cleanup(self) -> Optional[Dict]:
        journal = self._load_cleanup_journal()
        if journal is None:
            logger.info("[DRY-RUN] Would revalidate and delete the journaled Restore under its UID and resourceVersion")
            return None
        step = self._evidence(
            cleanup_step, journal=journal, repair_requested=self.migration_cleanup_repair is not None
        )["step"]
        logger.info("[DRY-RUN] Journaled Restore %s cleanup step: %s", journal["restore"]["name"], step)
        return journal

    def _load_cleanup_journal(self) -> Optional[Dict]:
        """The validated migration journal; restore-only and full runs journal it at activation too."""
        try:
            return self.run_record.migration_backups()
        except (MigrationEvidenceError, StateStructureError) as exc:
            raise FatalError(f"The recorded migration journal is invalid and blocks finalization: {exc}") from exc

    def _revalidate_for_teardown(self, journal: Dict) -> Dict:
        """The July section 4a evidence barrier, on every pass that has not created intent.

        The completion proof activation recorded is re-run live against the journaled
        Restore (identity, generation, owned spec, cleanup policy, accepted child
        evidence and frozen Backups); only its success records
        restore.teardown_revalidated_at, which a later pass keeps but never trusts.
        """
        SecondaryActivation(self.secondary, self.state).revalidate_completion_evidence(journal)
        decision = self._evidence(teardown_revalidated, journal=journal, now=_now_iso())
        if decision["write"]:
            self._persist_journal(decision["candidate"])
        logger.info("Teardown revalidation passed for Restore %s", journal["restore"]["name"])
        return decision["candidate"]

    def _delete_journaled_restore(self, journal: Dict) -> Dict:
        """Delete the journaled Restore under UID and resourceVersion, then prove its absence.

        Every attempt re-reads and re-validates the locator in full; a conflict or a
        404 retries within the shared bound. Only this uninterrupted attempt, holding
        the most recent accepted DELETE, may write completion; a replacement or an
        absence it did not prove is recorded as recovery and blocks.
        """
        schedule = cleanup_schedule()
        name, namespace = journal["restore"]["name"], journal["restore"]["namespace"]
        for attempt in range(1, schedule["attempts"] + 1):
            decision = self._evidence(
                pre_delete, journal=journal, live=self._strict_get_journaled_restore(journal), now=_now_iso()
            )
            if decision["action"] == "recover":
                self._record_recovery(decision["candidate"])
            logger.info("Deleting journaled Restore %s under its UID and resourceVersion", name)
            result = self.secondary.delete_restore_guarded(
                name,
                namespace=namespace,
                uid=decision["uid"],
                resource_version=decision["resource_version"],
                timeout_seconds=schedule["request_timeout"],
            )
            outcome = self._evidence(
                delete_outcome,
                journal=journal,
                result=result,
                resource_version=decision["resource_version"],
                attempt=attempt,
                now=_now_iso(),
            )
            if outcome["outcome"] == "accepted":
                journal = self._persist_decision(outcome)
                break
            logger.warning("Guarded delete of %s removed nothing (%s); re-validating", name, result.get("reason"))
        # The poll timeout is elapsed time: waits, reads and the final verification all count.
        deadline = time.monotonic() + schedule["poll_timeout"]
        for delay in schedule["poll_delays"]:
            if delay:
                time.sleep(max(0.0, min(delay, deadline - time.monotonic())))
            live = self._strict_get_before(journal, deadline)
            if live is _DEADLINE_PASSED:
                break
            poll = self._evidence(absence_poll, journal=journal, live=live, now=_now_iso())
            if poll["state"] == "recover":
                self._record_recovery(poll["candidate"])
            if poll["state"] == "absent":
                live = self._strict_get_before(journal, deadline)
                if live is _DEADLINE_PASSED:
                    break
                final = self._evidence(final_absence, journal=journal, live=live, now=_now_iso())
                if final["state"] == "recover":
                    self._record_recovery(final["candidate"])
                logger.info("Journaled Restore %s deleted and its absence verified", name)
                return self._persist_decision(final)
        raise FatalError(
            f"Timeout waiting for the journaled Restore {name} to disappear after "
            f"{schedule['poll_timeout']}s; the cleanup stays resumable"
        )

    def _strict_get_before(self, journal: Dict, deadline: float):
        """One strict locator GET bounded by the remaining poll budget; _DEADLINE_PASSED once it is spent."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _DEADLINE_PASSED
        live = self._strict_get_journaled_restore(journal, request_timeout=max(1, math.ceil(remaining)))
        return _DEADLINE_PASSED if time.monotonic() > deadline else live

    def _repair_restore_cleanup(self, journal: Dict) -> Dict:
        """Record the operator's audited repair once a fresh strict read proves the locator absent."""
        decision = self._evidence(
            repair_cleanup,
            journal=journal,
            request=self.migration_cleanup_repair,
            live=self._strict_get_journaled_restore(journal),
            now=_now_iso(),
        )
        journal = self._persist_decision(decision)
        repair = journal["cleanup"]["repair"]
        logger.warning(
            "Restore cleanup %s repaired by %s (%s); no delete or absence is claimed for this run",
            journal["cleanup"]["operation_id"],
            repair["actor"],
            repair["reason"],
        )
        return journal

    def _require_journaled_locator_absent(self, journal: Optional[Dict]) -> None:
        """Immediately before enablement: terminal cleanup and a fresh strict read proving absence."""
        live = self._strict_get_journaled_restore(journal) if journal is not None else None
        self._evidence(require_locator_absent, journal=journal, live=live)

    def _strict_get_journaled_restore(self, journal: Dict, request_timeout: Optional[int] = None) -> Optional[Dict]:
        """One strict GET of the journaled locator: the object, None for a proven 404, or FatalError."""
        restore = journal["restore"]
        outcome = self.secondary.get_custom_resource_strict(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=RESTORE_PLURAL,
            name=restore["name"],
            namespace=restore["namespace"],
            **({} if request_timeout is None else {"request_timeout": request_timeout}),
        )
        if outcome.status is StrictReadStatus.OBJECT_ABSENT:
            return None
        if not outcome.is_success:
            raise FatalError(
                f"Restore {restore['name']} could not be read ({outcome.status.value}: {outcome.reason}); "
                "unreadable is never absent, so the cleanup state is unchanged"
            )
        return outcome.resource

    def _record_recovery(self, candidate: Dict) -> None:
        """Durably record recovery_required, then block with the operator's repair guidance."""
        self._persist_journal(candidate)
        cleanup = candidate["cleanup"]
        recovery = cleanup["recovery"]
        observed = f", observed UID {recovery['observed_uid']}" if recovery["observed_uid"] else ""
        raise FatalError(
            f"The journaled Restore {cleanup['name']} cleanup requires recovery ({recovery['reason_code']}{observed}). "
            "Nothing at the locator was deleted or adopted. Inspect the destination hub, then acknowledge with "
            "--repair-migration-cleanup and the --cleanup-repair-* options, naming run ID "
            f"{candidate['run_id']} and operation ID {cleanup['operation_id']}."
        )

    def _persist_decision(self, decision: Dict) -> Dict:
        self._persist_journal(decision["candidate"])
        return decision["candidate"]

    def _persist_journal(self, candidate: Dict) -> None:
        """Durably record a journal transition through RunRecord; a failure stops before the next mutation."""
        try:
            self.run_record.record_migration_backups(candidate)
        except MigrationEvidenceError as exc:
            raise FatalError(f"The migration journal transition was refused: {exc}") from exc
        except OSError as exc:
            raise FatalError(f"The migration journal could not be recorded durably: {exc}") from exc

    @staticmethod
    def _evidence(function, *args, **kwargs):
        """Call a pure evidence helper, turning its fail-closed refusal into a FatalError."""
        try:
            return function(*args, **kwargs)
        except MigrationEvidenceError as exc:
            raise FatalError(f"Migration evidence refused ({exc})") from exc

    @staticmethod
    def _is_cleanup_candidate_restore(restore: Dict) -> bool:
        """Return True when the restore is owned by the switchover workflow."""
        restore_name = (restore.get("metadata", {}) or {}).get("name", "")
        if restore_name in {RESTORE_PASSIVE_SYNC_NAME, RESTORE_FULL_NAME, MANAGED_CLUSTER_RESTORE_NAME}:
            return True
        return (restore.get("spec", {}) or {}).get(SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS) is True

    def _cleanup_restore_resources(self, journal: Optional[Dict] = None):
        """Delete restore resources before enabling BackupSchedule.

        ACM backup operator won't allow BackupSchedule to be active while
        a Restore resource exists. We need to clean them up first.

        Before deletion, we archive the restore details to the state file
        for audit trail and troubleshooting purposes.

        This method discovers all restores dynamically by strictly listing all
        Restore resources in the backup namespace, rather than relying on
        hardcoded names. The journaled Restore locator is reserved for its own
        transaction: only once that cleanup is terminal are the other
        switchover Restores classified here, and an object at the journaled
        locator blocks instead of being deleted.
        """
        archived_restores = []
        delete_failures = []

        # Strictly list all restores in the namespace
        outcome = self.secondary.list_custom_resources_strict(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=RESTORE_PLURAL,
            namespace=BACKUP_NAMESPACE,
        )
        if not outcome.is_success:
            raise SwitchoverError(
                f"Restore inventory read failed before BackupSchedule enablement ({outcome.status.value}: "
                f"{outcome.reason})"
            )
        all_restores = outcome.items
        if not self.dry_run:
            all_restores = self._evidence(generic_cleanup_restores, journal=journal, restores=all_restores)["restores"]
        elif journal is not None:
            # A preview predicts the journaled Restore's guarded transaction separately.
            all_restores = [
                restore
                for restore in all_restores
                if (restore.get("metadata", {}) or {}).get("name") != journal["restore"]["name"]
            ]

        if not all_restores:
            logger.info("No restore resources found to clean up")
            return

        unexpected_restores = sorted(
            (restore.get("metadata", {}) or {}).get("name", "unknown")
            for restore in all_restores
            if not self._is_cleanup_candidate_restore(restore)
        )
        if unexpected_restores:
            raise SwitchoverError(
                "Found unexpected Restore resource(s) blocking BackupSchedule enablement: "
                + ", ".join(unexpected_restores)
                + ". Refusing to delete non-switchover Restore resources automatically."
            )

        logger.info("Found %s switchover restore resource(s) to clean up", len(all_restores))

        for restore in all_restores:
            restore_name = (restore.get("metadata", {}) or {}).get("name", "unknown")
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
                    group=CLUSTER_BACKUP_API_GROUP,
                    version=CLUSTER_BACKUP_API_VERSION,
                    plural=RESTORE_PLURAL,
                    name=restore_name,
                    namespace=BACKUP_NAMESPACE,
                    timeout_seconds=DELETE_REQUEST_TIMEOUT,
                )
                logger.info("Deleted restore resource: %s", restore_name)
            except ApiException as e:
                if getattr(e, "status", None) != 404:
                    logger.warning("Error deleting restore %s: %s", restore_name, e)
                    delete_failures.append(f"{restore_name}: {e.status} {e.reason}")
            except Exception as e:
                logger.warning("Error deleting restore %s: %s", restore_name, e)
                delete_failures.append(f"{restore_name}: {e}")

        # Save archived restores to state for audit trail
        if archived_restores:
            self.run_record.record_archived_restores(archived_restores)
            logger.info("Saved %s restore record(s) to state file", len(archived_restores))

        if delete_failures:
            raise SwitchoverError(
                "Failed to delete restore resource(s) before enabling BackupSchedule: " + "; ".join(delete_failures)
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
        Verify new backups are being created after enabling BackupSchedule.

        Fails with SwitchoverError if no new backup appears within the timeout.
        Records the detected backup name in state for downstream integrity verification.

        Args:
            timeout: Maximum wait time in seconds
        """

        if self._restore_only_missing_backup_schedule():
            logger.warning(
                "Skipping backup continuity verification in restore-only mode because no BackupSchedule exists yet"
            )
            return

        logger.info("Verifying new backups are being created...")

        # Get current ACM-owned backup list (Velero Backups use velero.io/v1)
        try:
            current_backups = self._list_acm_owned_velero_backups()
        except TransientError as exc:
            raise SwitchoverError(
                "Failed to list Velero backups before waiting for a new ACM backup: " f"{exc}"
            ) from exc

        recorded_backup_name = self.run_record.new_backup()
        if recorded_backup_name:
            recorded_backup = self.secondary.get_custom_resource(
                group="velero.io",
                version="v1",
                plural="backups",
                name=recorded_backup_name,
                namespace=BACKUP_NAMESPACE,
            )
            if (
                recorded_backup
                and self._is_acm_owned_backup(recorded_backup)
                and self._backup_is_detectable(recorded_backup)
            ):
                logger.info(
                    "Reusing previously recorded post-switchover backup: %s",
                    recorded_backup_name,
                )
                self.run_record.record_new_backup(recorded_backup_name)
                return

        enabled_at = self._get_backup_schedule_enabled_at()
        existing_post_enable_backup = self._find_post_enable_backup(current_backups, enabled_at)
        if existing_post_enable_backup:
            existing_backup_name = existing_post_enable_backup.get("metadata", {}).get("name")
            logger.info(
                "Found existing post-enable ACM backup on resume: %s",
                existing_backup_name,
            )
            self.run_record.record_new_backup(existing_backup_name)
            return

        initial_backups = current_backups

        initial_backup_names = {b.get("metadata", {}).get("name") for b in initial_backups}

        logger.info("Found %s existing backup(s)", len(initial_backups))
        logger.info("Waiting for new backup to appear (timeout: %ss)...", timeout)

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                current_backups = self._list_acm_owned_velero_backups()
            except TransientError as exc:
                logger.warning("%s", exc)
                time.sleep(BACKUP_POLL_INTERVAL)
                continue

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
                            self.run_record.record_new_backup(backup_name)
                            logger.info("New backup is being created successfully!")
                            return

            elapsed = int(time.time() - start_time)
            logger.debug("Waiting for new backup... (elapsed: %ss)", elapsed)
            time.sleep(BACKUP_POLL_INTERVAL)

        raise SwitchoverError(
            f"No new backup created within {timeout}s after enabling BackupSchedule on new hub. "
            "Finalization cannot succeed without proof of backup continuity."
        )

    def _list_acm_owned_velero_backups(self) -> List[Dict]:
        """List Velero backups, filter to ACM-owned backups, and normalize API failures."""
        try:
            backups = self.secondary.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
            )
        except ApiException as exc:
            if is_retryable_error(exc):
                raise TransientError(f"Transient error listing Velero backups: {exc}") from exc
            raise SwitchoverError(
                "Failed to list Velero backups while waiting for a new ACM backup: " f"{exc.status} {exc.reason}"
            ) from exc

        return self._filter_acm_owned_backups(backups)

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
        enabled_at_raw = self.run_record.backup_watch_started_at()
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
        """Verify backup status, logs, and recency for the post-switchover backup.

        When a post-switchover backup name was recorded by _verify_new_backups(), that
        specific backup is fetched and verified. This prevents unrelated backups in the
        namespace from masking a missing or failed ACM backup.

        Falls back to the latest-by-timestamp backup (with a warning) only when no
        recorded name is available (e.g. during a dry-run).
        """
        if self._restore_only_missing_backup_schedule():
            logger.warning(
                "Skipping backup integrity verification in restore-only mode because no BackupSchedule exists yet"
            )
            return

        logger.info("Verifying backup integrity...")
        effective_max_age_seconds = self._get_backup_max_age_seconds(max_age_seconds)

        post_switchover_backup_name = self.run_record.new_backup()

        if post_switchover_backup_name:
            logger.info("Verifying post-switchover backup: %s", post_switchover_backup_name)
            latest_backup = self.secondary.get_custom_resource(
                group="velero.io",
                version="v1",
                plural="backups",
                name=post_switchover_backup_name,
                namespace=BACKUP_NAMESPACE,
            )
            if not latest_backup:
                logger.warning(
                    "Post-switchover backup '%s' no longer exists (likely pruned by retention); "
                    "falling back to latest ACM-owned backup",
                    post_switchover_backup_name,
                )
                post_switchover_backup_name = None
            elif not self._is_acm_owned_backup(latest_backup):
                raise SwitchoverError(
                    f"Post-switchover backup '{post_switchover_backup_name}' is not ACM-owned; "
                    "cannot verify integrity against an unrelated Velero backup"
                )
            else:
                backup_name = post_switchover_backup_name

        if not post_switchover_backup_name:
            if not self.run_record.new_backup():
                logger.warning("No post-switchover backup name recorded; falling back to latest backup in namespace")
            try:
                backups = self._list_acm_owned_velero_backups()
            except TransientError as exc:
                raise SwitchoverError("Failed to list Velero backups for integrity verification: " f"{exc}") from exc
            if not backups:
                raise SwitchoverError("No ACM-owned Velero backups found for integrity verification")

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
            if phase in ("New", "InProgress", "Finalizing"):
                backup_verify_timeout = self._get_backup_verify_timeout()

                def _poll_backup_completion() -> WaitConditionResult:
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
                        return WaitConditionResult.complete("phase=Completed")
                    if poll_phase in ("Failed", "PartiallyFailed"):
                        raise SwitchoverError(f"Latest backup {backup_name} failed (phase={poll_phase})")
                    return WaitConditionResult.pending(f"phase={poll_phase}")

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
        # A backup is considered "after enable" if its name was recorded by
        # _verify_new_backups (meaning it was detected after enabling the schedule),
        # or if its timestamp is >= the recorded enable time.
        is_post_switchover_backup = bool(post_switchover_backup_name)

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
                backup_after_enable = is_post_switchover_backup

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
            outcome = self.secondary.list_custom_resources_strict(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=BACKUP_SCHEDULE_PLURAL,
                namespace=BACKUP_NAMESPACE,
            )
            if not outcome.is_success:
                raise SwitchoverError(
                    f"BackupSchedule inventory on the secondary hub could not be read ({outcome.status.value}: "
                    f"{outcome.reason})"
                )
            self._cached_schedules = outcome.items
        fail_on_multiple_backup_schedules(self._cached_schedules, "secondary hub")
        return self._cached_schedules

    def _restore_only_missing_backup_schedule(self, schedules: Optional[List[Dict]] = None) -> bool:
        """Return True when restore-only finalization should tolerate no BackupSchedule."""
        if not self.restore_only:
            return False
        if schedules is None:
            schedules = self._get_backup_schedules()
        return not schedules

    @dry_run_skip(message="Skipping BackupSchedule verification")
    def _verify_backup_schedule_enabled(self):
        """Ensure BackupSchedule is present and not paused."""

        schedules = self._get_backup_schedules()

        if not schedules:
            if self._restore_only_missing_backup_schedule(schedules):
                logger.warning(
                    "No BackupSchedule found during restore-only finalization; manual backup setup is required"
                )
                return
            raise SwitchoverError("No BackupSchedule found while verifying finalization")

        schedule = schedules[0]
        schedule_name = schedule.get("metadata", {}).get("name", BACKUP_SCHEDULE_DEFAULT_NAME)
        paused = schedule.get("spec", {}).get("paused", False)

        if paused:
            raise SwitchoverError(f"BackupSchedule {schedule_name} is still paused")

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
                raise SwitchoverError("No MultiClusterHub resource found on secondary hub")

            mch_name = mch.get("metadata", {}).get("name", "multiclusterhub")
            phase = mch.get("status", {}).get("phase", "unknown")

            pods = self.secondary.get_pods(namespace=ACM_NAMESPACE)
            non_running = [
                pod.get("metadata", {}).get("name", "unknown")
                for pod in pods
                if pod.get("status", {}).get("phase") not in ("Running", "Succeeded")
            ]

            if phase == "Running" and not non_running:
                logger.info("MultiClusterHub %s is Running and all pods are healthy", mch_name)
                return

            elapsed = time.time() - start
            if elapsed >= timeout:
                details = ", non-running pods=" + (", ".join(non_running) if non_running else "none")
                raise SwitchoverError(
                    f"MultiClusterHub {mch_name} not healthy after {timeout}s (phase={phase}{details})"
                )

            logger.info(
                "Waiting for MultiClusterHub %s to become healthy (phase=%s, non-running pods=%s)...",
                mch_name,
                phase,
                ", ".join(non_running) if non_running else "none",
            )
            time.sleep(interval)

    def _disable_observability_on_old_hub(self) -> None:
        """Delete MultiClusterObservability on the old hub through the shared machine.

        GLM-H6: this method owns **no** MCO deletion logic. It keeps only the
        caller-specific concerns amendment section 11 item 2 assigns to Finalization --
        the gating below, the GitOps marker opt-in, and finalization-specific failure
        text -- and delegates the algorithm to ``Decommission.teardown_observability``.

        Expected failures arrive on the return channel, so this never catches an
        exception from the teardown; it inspects the outcome and raises its own
        ``SwitchoverError`` with finalization's message context. That is the
        documented adapter, not a second channel.
        """
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

        decommission = Decommission(
            self.primary,
            self.primary_has_observability,
            run_record=self.run_record,
            dry_run=self.dry_run,
            secondary_client=self.secondary,
            acknowledge_observability_not_migrated=self.acknowledge_observability_not_migrated,
        )
        execution = decommission.teardown_observability(record_gitops_markers=True)

        if execution.outcome in UNSUCCESSFUL_OUTCOMES:
            raise SwitchoverError(
                "Failed to disable observability on the old hub: the MultiClusterObservability "
                f"teardown reported {execution.outcome.value}. The old hub was not left in the "
                "expected secondary state."
            )

    def _handle_old_hub(self):
        """
        Handle the old primary hub based on --old-hub-action setting.

        Options:
        - 'secondary': Set up passive sync restore for failback capability
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

        raise SwitchoverError(
            f"Unknown old_hub_action '{self.old_hub_action}'. " "Expected one of: secondary, decommission, none."
        )

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

        decom = Decommission(
            self.primary,
            self.primary_has_observability,
            run_record=self.run_record,
            dry_run=self.dry_run,
            secondary_client=self.secondary,
            acknowledge_observability_not_migrated=self.acknowledge_observability_not_migrated,
        )

        # Run decommission non-interactively since we're in automated mode
        result = decom.decommission(interactive=False)
        for line in result.summary_lines():
            logger.info("Decommission summary - %s", line)

        # Explicit .succeeded, never dataclass truthiness: a partially mutated run
        # is both changed and unsuccessful, and truthiness cannot express that.
        if result.succeeded:
            logger.info("Old hub decommissioned successfully")
        else:
            raise SwitchoverError(
                "Old hub decommission failed or completed incompletely. "
                "Manual cleanup is required before considering switchover finalized. "
                f"Substep outcomes: {'; '.join(result.summary_lines())}"
            )

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

        # Always delete and recreate the passive-sync restore. Even when the spec
        # already says "skip", stale status/controller state can leave the old hub
        # without a usable passive-sync restore for the next failback.
        existing_restore = self.primary.get_custom_resource(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=RESTORE_PLURAL,
            name=RESTORE_PASSIVE_SYNC_NAME,
            namespace=BACKUP_NAMESPACE,
        )

        if existing_restore:
            mc_backup = existing_restore.get("spec", {}).get(SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME)
            logger.info(
                "Existing passive sync restore on old primary has veleroManagedClustersBackupName=%s; "
                "deleting it so finalization creates a fresh passive-sync restore",
                mc_backup or "unknown",
            )
            try:
                self.primary.delete_custom_resource(
                    group=CLUSTER_BACKUP_API_GROUP,
                    version=CLUSTER_BACKUP_API_VERSION,
                    plural=RESTORE_PLURAL,
                    name=RESTORE_PASSIVE_SYNC_NAME,
                    namespace=BACKUP_NAMESPACE,
                    timeout_seconds=DELETE_REQUEST_TIMEOUT,
                )
            except ApiException as e:
                raise SwitchoverError("Failed to delete stale passive sync restore on old primary hub") from e

            self._wait_for_primary_restore_deletion(RESTORE_PASSIVE_SYNC_NAME)

        # Create passive sync restore on old primary
        restore_body = {
            "apiVersion": CLUSTER_BACKUP_API_VERSION_FULL,
            "kind": "Restore",
            "metadata": {
                "name": RESTORE_PASSIVE_SYNC_NAME,
                "namespace": BACKUP_NAMESPACE,
            },
            "spec": {
                SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS: True,
                "restoreSyncInterval": "10m",
                "cleanupBeforeRestore": CLEANUP_BEFORE_RESTORE_VALUE,
                SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME: VELERO_BACKUP_SKIP,
                "veleroCredentialsBackupName": VELERO_BACKUP_LATEST,
                "veleroResourcesBackupName": VELERO_BACKUP_LATEST,
            },
        }

        try:
            self.primary.create_custom_resource(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=RESTORE_PLURAL,
                body=restore_body,
                namespace=BACKUP_NAMESPACE,
            )
        except ApiException as e:
            raise SwitchoverError("Failed to create passive sync restore on old primary hub") from e
        logger.info("Created passive sync restore on old primary hub")

    def _wait_for_primary_restore_deletion(self, restore_name: str, timeout: int = RESTORE_WAIT_TIMEOUT) -> None:
        """Wait until a restore resource is fully deleted from the old primary hub."""
        wait_for_restore_deletion(
            self.primary, restore_name, dry_run=self.dry_run, timeout=timeout, where=" on primary", logger=logger
        )

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
            if self._restore_only_missing_backup_schedule(schedules):
                logger.warning(
                    "Skipping BackupSchedule collision repair in restore-only mode because no BackupSchedule exists yet"
                )
                return
            raise SwitchoverError("No BackupSchedule found on new primary while repairing collision state")

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

        # R4-04: an enabled BackupSchedule is recreated only after terminal Restore cleanup and a
        # fresh strict read proving the journaled locator empty, enablement marker or not.
        self._require_journaled_locator_absent(self._cleanup_journal)

        schedule_uid = schedule.get("metadata", {}).get("uid")
        # Save the spec for recreation
        schedule_spec = schedule.get("spec", {})

        # Re-verify schedule still exists before deletion (handles race conditions)
        current_schedule = self.secondary.get_custom_resource(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=BACKUP_SCHEDULE_PLURAL,
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
                raise SwitchoverError(
                    "BackupSchedule %s changed identity (uid %s -> %s) before deletion; "
                    "manual verification is required before retrying collision repair"
                    % (schedule_name, schedule_uid, current_uid)
                )

            # Use latest spec to avoid recreating from stale data
            schedule_spec = current_schedule.get("spec", {})

            # Delete the old schedule
            try:
                self.secondary.delete_custom_resource(
                    group=CLUSTER_BACKUP_API_GROUP,
                    version=CLUSTER_BACKUP_API_VERSION,
                    plural=BACKUP_SCHEDULE_PLURAL,
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

            self._wait_for_backup_schedule_deletion(schedule_name, schedule_uid)

        # Recreate the schedule
        try:
            new_schedule = {
                "apiVersion": CLUSTER_BACKUP_API_VERSION_FULL,
                "kind": "BackupSchedule",
                "metadata": {
                    "name": schedule_name,
                    "namespace": BACKUP_NAMESPACE,
                },
                "spec": schedule_spec,
            }

            self._require_journaled_locator_absent(self._cleanup_journal)
            self.secondary.create_custom_resource(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=BACKUP_SCHEDULE_PLURAL,
                body=new_schedule,
                namespace=BACKUP_NAMESPACE,
            )
            logger.info("Recreated BackupSchedule %s to prevent collision", schedule_name)
            # Invalidate cache since we recreated the schedule
            self._cached_schedules = None

        except ApiException as e:
            if e.status != 409:
                raise SwitchoverError(f"Failed to recreate BackupSchedule {schedule_name}: {e}") from e

            logger.warning(
                "BackupSchedule %s already exists during recreation (409); checking current state",
                schedule_name,
            )
            current_schedule = self.secondary.get_custom_resource(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=BACKUP_SCHEDULE_PLURAL,
                name=schedule_name,
                namespace=BACKUP_NAMESPACE,
            )
            if not current_schedule:
                raise SwitchoverError(
                    "BackupSchedule %s returned 409 on recreate but could not be re-read afterward" % schedule_name
                )

            current_phase = current_schedule.get("status", {}).get("phase", "")
            current_uid = current_schedule.get("metadata", {}).get("uid")
            self._cached_schedules = None

            if schedule_uid and current_uid == schedule_uid:
                raise SwitchoverError(
                    "BackupSchedule %s returned 409 on recreate but still has the original uid (%s). "
                    "Manual verification is required before retrying collision repair." % (schedule_name, current_uid)
                )

            if current_phase == "BackupCollision":
                raise SwitchoverError(
                    "BackupSchedule %s exists after recreate conflict (uid=%s) but remains in BackupCollision"
                    % (schedule_name, current_uid or "unknown")
                )
            else:
                logger.info(
                    "BackupSchedule %s exists after conflict (uid=%s, phase=%s); continuing",
                    schedule_name,
                    current_uid or "unknown",
                    current_phase or "Unknown",
                )
        except Exception as e:
            raise SwitchoverError(f"Failed to recreate BackupSchedule {schedule_name}: {e}") from e

    def _wait_for_backup_schedule_deletion(
        self,
        schedule_name: str,
        schedule_uid: Optional[str],
        timeout: int = BACKUP_SCHEDULE_DELETE_TIMEOUT,
        interval: int = BACKUP_SCHEDULE_DELETE_INTERVAL,
    ) -> None:
        """Wait until a deleted BackupSchedule is absent before recreating it."""

        def _poll_schedule_absence() -> WaitConditionResult:
            schedule = self.secondary.get_custom_resource(
                group=CLUSTER_BACKUP_API_GROUP,
                version=CLUSTER_BACKUP_API_VERSION,
                plural=BACKUP_SCHEDULE_PLURAL,
                name=schedule_name,
                namespace=BACKUP_NAMESPACE,
            )
            if not schedule:
                return WaitConditionResult.complete("absent")

            current_uid = schedule.get("metadata", {}).get("uid")
            if schedule_uid and current_uid and current_uid != schedule_uid:
                raise SwitchoverError(
                    "BackupSchedule %s reappeared with a different uid (%s, previous=%s) after deletion. "
                    "Manual verification is required before retrying collision repair."
                    % (schedule_name, current_uid, schedule_uid)
                )
            return WaitConditionResult.pending("still present (uid=%s)" % (current_uid or "unknown"))

        deleted = wait_for_condition(
            f"BackupSchedule {schedule_name} deletion",
            _poll_schedule_absence,
            timeout=timeout,
            interval=interval,
            logger=logger,
        )
        if deleted:
            return

        self._cached_schedules = None
        schedule_after_delete = self.secondary.get_custom_resource(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=BACKUP_SCHEDULE_PLURAL,
            name=schedule_name,
            namespace=BACKUP_NAMESPACE,
        )
        if not schedule_after_delete:
            logger.info("BackupSchedule %s deleted after final re-check", schedule_name)
            return

        after_uid = (schedule_after_delete or {}).get("metadata", {}).get("uid")
        raise SwitchoverError(
            "BackupSchedule %s still present (uid=%s) after deletion attempt. "
            "Deletion has not completed; manual verification is required before retrying collision repair."
            % (schedule_name, after_uid or "unknown")
        )

    def _verify_old_hub_state(self):
        """Run regression checks on the old (primary) hub."""
        if not self.primary:
            return

        logger.info("Running regression checks on old primary hub...")

        clusters = self.primary.list_custom_resources(
            group=MANAGED_CLUSTER_API_GROUP,
            version=MANAGED_CLUSTER_API_VERSION,
            plural=MANAGED_CLUSTER_PLURAL,
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
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=BACKUP_SCHEDULE_PLURAL,
            namespace=BACKUP_NAMESPACE,
        )
        if schedules:
            paused = schedules[0].get("spec", {}).get("paused", False)
            if paused:
                logger.info("Old hub BackupSchedule remains paused as expected")
            else:
                logger.warning("Old hub BackupSchedule is not paused")

    def _run_reset_auto_import_strategy_step(self) -> None:
        """Execute the explicit reset_auto_import_strategy step when applicable."""
        if not is_acm_version_ge(self.acm_version, "2.14.0"):
            return

        step_name = "reset_auto_import_strategy"
        if self.state.is_step_completed(step_name):
            logger.info("Step already completed: %s", step_name)
            return

        if self._ensure_auto_import_default() and not self.dry_run:
            self.state.mark_step_completed(step_name)

    @dry_run_skip(message="Would reset autoImportStrategy to default ImportOnly", return_value=True)
    def _ensure_auto_import_default(self) -> bool:
        """Reset autoImportStrategy to default ImportOnly when applicable."""
        if not is_acm_version_ge(self.acm_version, "2.14.0"):
            return True

        auto_import_strategy_set = self.run_record.auto_import_override_pending()
        try:
            cm = self.secondary.get_configmap(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)
        except ApiException as e:
            if auto_import_strategy_set:
                raise SwitchoverError(
                    "Failed to verify autoImportStrategy before restoring default by checking "
                    f"{MCE_NAMESPACE}/{IMPORT_CONTROLLER_CONFIG_CM}: {e.status} {e.reason}"
                ) from e
            logger.warning("Unable to verify auto-import strategy: %s", e)
            return True

        if not cm:
            if auto_import_strategy_set:
                # ConfigMap is gone — reset is complete; clear flag so retries skip re-entering
                self.run_record.clear_auto_import_override()
            return True

        strategy = (cm.get("data") or {}).get(AUTO_IMPORT_STRATEGY_KEY, "default")
        if strategy != AUTO_IMPORT_STRATEGY_SYNC:
            if auto_import_strategy_set:
                # Strategy already at non-sync value — reset is complete; clear flag
                self.run_record.clear_auto_import_override()
            return True

        if auto_import_strategy_set:
            logger.info(
                "Removing %s/%s to restore default autoImportStrategy (%s)",
                MCE_NAMESPACE,
                IMPORT_CONTROLLER_CONFIG_CM,
                AUTO_IMPORT_STRATEGY_DEFAULT,
            )
            try:
                self.secondary.delete_configmap(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIG_CM)
            except ApiException as e:
                if getattr(e, "status", None) == 404:
                    logger.debug(
                        "ConfigMap %s/%s already absent",
                        MCE_NAMESPACE,
                        IMPORT_CONTROLLER_CONFIG_CM,
                    )
                else:
                    raise SwitchoverError(
                        "Failed to reset autoImportStrategy to default by deleting "
                        f"{MCE_NAMESPACE}/{IMPORT_CONTROLLER_CONFIG_CM}: {e.status} {e.reason}"
                    ) from e
            self.run_record.clear_auto_import_override()
            return True

        logger.warning(
            "autoImportStrategy is %s; remove %s/%s to reset to default (%s)",
            AUTO_IMPORT_STRATEGY_SYNC,
            MCE_NAMESPACE,
            IMPORT_CONTROLLER_CONFIG_CM,
            AUTO_IMPORT_STRATEGY_DEFAULT,
        )
        return False
