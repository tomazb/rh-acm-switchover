"""Read-only Backup continuity decisions, independent of lifecycle and storage."""

import logging
import time
from datetime import datetime, timezone
from typing import Mapping

from lib.constants import (
    ACM_BACKUP_NAME_RE,
    ACM_BACKUP_SCHEDULE_TYPE_LABEL,
    ACM_BACKUP_SCHEDULE_TYPES,
    BACKUP_NAMESPACE,
)
from lib.strict_read import StrictReadStatus


class BackupContinuityError(RuntimeError):
    """A sanitized refusal to treat historical or unavailable evidence as proof."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(f"Backup continuity refused: {reason}. Preserve the checkpoint for read-only assessment.")


def _timestamp(value, reason):
    if not isinstance(value, str) or not value:
        raise BackupContinuityError(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BackupContinuityError(reason) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BackupContinuityError(reason)
    return parsed.astimezone(timezone.utc)


def resolve_enable_boundary(inputs: Mapping, *, observed_at: datetime) -> datetime:
    """Use the raw saved boundary, or the single absent-only Python legacy step."""
    if not isinstance(inputs, Mapping) or inputs.get("invalid_reason"):
        raise BackupContinuityError("invalid_backup_evidence")
    present = inputs.get("enabled_at_present")
    if not isinstance(present, bool):
        raise BackupContinuityError("invalid_backup_evidence")
    if present:
        value = inputs.get("enabled_at")
    else:
        steps = inputs.get("legacy_enable_steps", [])
        if not isinstance(steps, list):
            raise BackupContinuityError("invalid_backup_evidence")
        if not steps:
            raise BackupContinuityError("missing_enable_boundary")
        if len(steps) != 1:
            raise BackupContinuityError("ambiguous_enable_boundary")
        if not isinstance(steps[0], dict):
            raise BackupContinuityError("invalid_enable_boundary")
        value = steps[0].get("timestamp")
    boundary = _timestamp(value, "invalid_enable_boundary")
    if boundary > observed_at:
        raise BackupContinuityError("invalid_enable_boundary")
    return boundary


def is_acm_owned(backup):
    metadata = backup.get("metadata") if isinstance(backup, dict) else None
    if not isinstance(metadata, dict):
        return False
    labels = metadata.get("labels") or {}
    name = metadata.get("name")
    if isinstance(labels, dict) and labels.get(ACM_BACKUP_SCHEDULE_TYPE_LABEL) in ACM_BACKUP_SCHEDULE_TYPES:
        return True
    if isinstance(name, str) and ACM_BACKUP_NAME_RE.match(name):
        logging.getLogger("acm_switchover").warning(
            "Backup %s is missing ACM ownership label %s; accepting ACM name-pattern fallback",
            name,
            ACM_BACKUP_SCHEDULE_TYPE_LABEL,
        )
        return True
    return False


def validate_backup(backup, *, enabled_at, observed_at, expected_name=None, expected_uid=None):
    """Validate one current object's creation, ownership, identity and health."""
    if not isinstance(backup, dict) or not isinstance(backup.get("metadata"), dict):
        raise BackupContinuityError("invalid_backup_identity")
    metadata = backup["metadata"]
    name, uid = metadata.get("name"), metadata.get("uid")
    if not isinstance(name, str) or not name or not isinstance(uid, str) or not uid:
        raise BackupContinuityError("invalid_backup_identity")
    if (expected_name is not None and name != expected_name) or (expected_uid is not None and uid != expected_uid):
        raise BackupContinuityError("backup_identity_changed")
    if not is_acm_owned(backup):
        raise BackupContinuityError("backup_not_acm_owned")
    created = _timestamp(metadata.get("creationTimestamp"), "invalid_backup_creation")
    if created > observed_at:
        raise BackupContinuityError("invalid_backup_creation")
    if created < enabled_at:
        raise BackupContinuityError("backup_predates_enable")
    status = backup.get("status")
    if not isinstance(status, dict):
        raise BackupContinuityError("invalid_backup_status")
    phase = status.get("phase")
    if phase not in ("New", "InProgress", "Finalizing", "Completed"):
        raise BackupContinuityError("backup_not_completed")
    errors = status.get("errors")
    if errors is not None:
        if isinstance(errors, bool) or not isinstance(errors, (int, str)):
            raise BackupContinuityError("invalid_backup_status")
        try:
            error_count = int(errors)
        except ValueError:
            raise BackupContinuityError("invalid_backup_status") from None
        if error_count < 0:
            raise BackupContinuityError("invalid_backup_status")
        if error_count:
            raise BackupContinuityError("backup_errors")
    return {"name": name, "uid": uid, "created_at": created.isoformat(), "phase": phase}


def verify_backup_continuity(client, *, enabled_at, recorded_name, timeout, poll_interval, max_age_seconds=None):
    """Fresh complete inventory and stable named observations; never persist proof."""
    deadline = time.time() + timeout
    if recorded_name is not None and (not isinstance(recorded_name, str) or not recorded_name):
        raise BackupContinuityError("invalid_backup_identity")

    def inventory():
        outcome = client.list_custom_resources_strict(
            group="velero.io", version="v1", plural="backups", namespace=BACKUP_NAMESPACE
        )
        if outcome.status is not StrictReadStatus.ITEMS:
            raise BackupContinuityError("backup_inventory_unavailable")
        return outcome.items

    def select(items):
        candidates = []
        for backup in items:
            if not is_acm_owned(backup):
                continue
            try:
                proof = validate_backup(backup, enabled_at=enabled_at, observed_at=datetime.now(timezone.utc))
            except BackupContinuityError as exc:
                if exc.reason == "backup_predates_enable":
                    continue
                raise
            candidates.append((datetime.fromisoformat(proof["created_at"]), backup))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    baseline = inventory()  # A failed initial read never becomes an empty baseline.
    if recorded_name is None:
        selected = select(baseline)
        if selected is not None:
            recorded_name = selected["metadata"]["name"]
    else:
        selected = next((b for b in baseline if b.get("metadata", {}).get("name") == recorded_name), None)
        if selected is not None:
            validate_backup(selected, enabled_at=enabled_at, observed_at=datetime.now(timezone.utc))
    while time.time() < deadline:
        if recorded_name is None:
            try:
                current = inventory()
            except BackupContinuityError:
                current = None  # No prefix or empty substitute, and baseline remains intact.
            if current is not None:
                selected = select(current)
                if selected is not None:
                    recorded_name = selected["metadata"]["name"]
        if recorded_name is not None:
            outcome = client.get_custom_resource_strict(
                group="velero.io", version="v1", plural="backups", name=recorded_name, namespace=BACKUP_NAMESPACE
            )
            if outcome.status is not StrictReadStatus.ITEMS or not isinstance(outcome.resource, dict):
                raise BackupContinuityError("recorded_backup_unavailable")
            backup = outcome.resource
            proof = validate_backup(
                backup,
                enabled_at=enabled_at,
                observed_at=datetime.now(timezone.utc),
                expected_name=recorded_name,
                expected_uid=selected["metadata"]["uid"] if selected is not None else None,
            )
            if selected is None:
                selected = backup
                # Establish UID from this first current observation, then read it again.
                continue
            if proof["phase"] == "Completed":
                if max_age_seconds is not None:
                    status = backup["status"]
                    age_value = (
                        status.get("completionTimestamp")
                        or status.get("startTimestamp")
                        or backup["metadata"].get("creationTimestamp")
                    )
                    try:
                        finished = _timestamp(age_value, "invalid_backup_status")
                    except BackupContinuityError:
                        finished = None  # Preserve existing warning-only malformed age policy.
                    if finished is not None and finished >= enabled_at:
                        if (datetime.now(timezone.utc) - finished).total_seconds() > max_age_seconds:
                            raise BackupContinuityError("backup_too_old")
                return proof
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
    raise BackupContinuityError("No qualifying new backup completed within the verification timeout")
