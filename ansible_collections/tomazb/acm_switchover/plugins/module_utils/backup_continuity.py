"""Read-only Backup continuity decisions, independent of lifecycle and storage."""

import re
from datetime import datetime, timezone
from typing import Mapping

ACM_BACKUP_NAME_RE = re.compile(r"^acm-(managed-clusters|credentials|resources)-")
ACM_BACKUP_SCHEDULE_TYPE_LABEL = "cluster.open-cluster-management.io/backup-schedule-type"
ACM_BACKUP_SCHEDULE_TYPES = frozenset({"managedClusters", "credentials", "resources"})


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
        raise BackupContinuityError("missing_enable_boundary")
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
    return (isinstance(labels, dict) and labels.get(ACM_BACKUP_SCHEDULE_TYPE_LABEL) in ACM_BACKUP_SCHEDULE_TYPES) or (
        isinstance(name, str) and ACM_BACKUP_NAME_RE.match(name) is not None
    )


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
