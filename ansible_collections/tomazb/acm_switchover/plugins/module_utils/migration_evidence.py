# SPDX-License-Identifier: MIT
"""R4-04 migration evidence: the pure Backup-evidence model (plan Task 2).

Collection mirror of lib/migration_evidence.py. It owns the controller contract
matrix, the schedule tokens, the seven-field Backup projection with its R4-04
eligibility rule, and the prediction of which Velero Backup a pinned
cluster-backup-operator selects. Normative sources: the July design, the August
amendment (sections 3 and 5) and the controller child-evidence amendment
(sections 1 and 2) under docs/plans/.

Nothing here performs I/O or imports Ansible, so root tests load it directly.
Every rule fails closed by raising MigrationEvidenceError with a stable
``code``. The two form factors share no code;
tests/test_migration_evidence_parity.py holds them equal.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


class MigrationEvidenceError(ValueError):
    """Migration evidence is malformed, ineligible or ambiguous. Fail closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# August amendment section 5: the immutable upstream contract matrix. A minor
# outside this table is never assigned to an existing contract.
PINNED_CONTROLLER_SHAS = {
    "2.12": "74b54988a5bd6712ea3fe3e9ceb770e06db91e8b",
    "2.13": "7a7b240b3df71105da3f15620e4116498f9e2a23",
    "2.14": "8b489db488739e7d9adca50cb3be0eae79293f22",
    "2.15": "25b28b762355a14b4fb7f145efe173f73659e740",
    "2.16": "9efe77eaec2139f106c957051e2297dafc84b482",
    "2.17": "c8578f94df09deab561e1aa5a7e9fc9b57f7d113",
}
ACM_MINOR_CONTRACTS = {
    "2.12": "legacy_2_12_2_16",
    "2.13": "legacy_2_12_2_16",
    "2.14": "legacy_2_12_2_16",
    "2.15": "legacy_2_12_2_16",
    "2.16": "legacy_2_12_2_16",
    "2.17": "active_2_17",
}

# Child-evidence amendment section 1: the pinned veleroBackupNames prefixes,
# keyed by upstream ResourceType, without a trailing hyphen. CredentialsHive and
# CredentialsCluster exist only on the legacy lanes; CredentialsActive and
# ResourcesGenericActive only on 2.17. Lane applicability is the caller's.
SCHEDULE_TOKENS = {
    "ManagedClusters": "acm-managed-clusters-schedule",
    "Credentials": "acm-credentials-schedule",
    "CredentialsHive": "acm-credentials-hive-schedule",
    "CredentialsCluster": "acm-credentials-cluster-schedule",
    "CredentialsActive": "acm-credentials-schedule",
    "Resources": "acm-resources-schedule",
    "ResourcesGeneric": "acm-resources-generic-schedule",
    "ResourcesGenericActive": "acm-resources-generic-schedule",
}
# Types resolved by direct `latest` selection, and types resolved by the
# correlated exact-name/+-30 s search from a concrete source Backup name.
LATEST_RESOURCE_TYPES = frozenset({"ManagedClusters", "Credentials", "Resources", "ResourcesGeneric"})
CORRELATED_RESOURCE_TYPES = frozenset(
    {"ResourcesGeneric", "ResourcesGenericActive", "CredentialsActive", "CredentialsHive", "CredentialsCluster"}
)

_LATEST_RAW_PHASES = frozenset({"Completed", "PartiallyFailed"})
_CORRELATION_WINDOW_NS = 30 * 10**9
_NS_PER_SECOND = 10**9
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Go's zero time.Time, which getBackupTimestamp returns for a name without '-'.
_GO_ZERO_TIME = datetime(1, 1, 1, tzinfo=timezone.utc)
# metav1.Time wire format: RFC 3339 with uppercase T/Z, optional fraction.
_RFC3339 = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(?:(Z)|([+-])([0-9]{2}):([0-9]{2}))"
)
# The Velero TimestampedName suffix, Go layout 20060102150405. Go's time.Parse also
# accepts a fractional second ('.' or ',' then digits) the layout does not declare.
_NAME_TIMESTAMP = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})([0-9]{2})([0-9]{2})([0-9]{2})(?:[.,]([0-9]+))?")


def controller_contract_for_acm_minor(minor: Any) -> str:
    """Return the pinned controller contract for an ACM minor such as "2.14"."""
    if not isinstance(minor, str) or minor not in ACM_MINOR_CONTRACTS:
        raise MigrationEvidenceError("unknown_acm_minor", f"ACM minor {minor!r} has no pinned controller contract")
    return ACM_MINOR_CONTRACTS[minor]


def normalize_backup_evidence(raw: Any, namespace: str) -> Dict[str, Any]:
    """Return the seven-field projection of an R4-04-eligible Velero Backup.

    This is the single R4-04 eligibility authority (August amendment section 3):
    it normalizes the optional counters, then requires Completed, a well-formed
    completionTimestamp, zero errors and the exact namespace. It raises on any
    Backup that is not eligible, so the projection only ever holds successful
    evidence; revalidation re-runs it and compares the result.
    """
    metadata, status = _backup_parts(raw)
    name = metadata.get("name")
    uid = metadata.get("uid")
    if not _non_empty_str(name) or not _non_empty_str(uid):
        raise MigrationEvidenceError("malformed_backup", "Backup metadata.name and metadata.uid must be non-empty")
    errors = _counter(status, "errors")
    warnings = _counter(status, "warnings")
    if not _non_empty_str(namespace) or metadata.get("namespace") != namespace:
        raise MigrationEvidenceError("backup_namespace_mismatch", f"Backup {name} is not in namespace {namespace!r}")
    if status.get("phase") != "Completed":
        raise MigrationEvidenceError("backup_not_completed", f"Backup {name} phase is not Completed")
    completed_at = status.get("completionTimestamp")
    if _rfc3339_ns(completed_at) is None:
        raise MigrationEvidenceError("malformed_completion_timestamp", f"Backup {name} completionTimestamp is invalid")
    if errors != 0:
        raise MigrationEvidenceError("backup_has_errors", f"Backup {name} reports {errors} errors")
    return {
        "namespace": namespace,
        "name": name,
        "uid": uid,
        "phase": "Completed",
        "completed_at": completed_at,
        "errors": errors,
        "warnings": warnings,
    }


def select_latest_evidence(inventory: Any, resource_type: str, namespace: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return the controller's direct `latest` choice as journalable evidence.

    ("selected", seven-field projection) or ("none", None); a selected Backup
    that fails R4-04 eligibility raises instead of yielding an older one.
    """
    decision, raw = predict_latest_backup(inventory, resource_type)
    return (decision, normalize_backup_evidence(raw, namespace) if raw is not None else None)


def select_correlated_evidence(
    inventory: Any, source_name: Any, resource_type: str, namespace: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return the controller's correlated choice as journalable evidence.

    ("selected", seven-field projection) or ("none", None); a selected Backup
    that fails R4-04 eligibility raises and never falls back to another one.
    """
    decision, raw = predict_correlated_backup(inventory, source_name, resource_type)
    return (decision, normalize_backup_evidence(raw, namespace) if raw is not None else None)


def predict_latest_backup(inventory: Any, resource_type: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Predict the controller's direct `latest` choice (child-evidence amendment section 1).

    An upstream-prediction primitive, not journalable evidence: it returns
    ("selected", raw) with the inventory object itself, or ("none", None) when
    no Backup passes the prefix and raw-phase filters, and applies no R4-04
    eligibility. Journal only what select_latest_evidence returns.
    """
    token = _token(resource_type, LATEST_RESOURCE_TYPES)
    candidates = [
        item for name, item in _inventory(inventory) if name.startswith(token) and _phase(item) in _LATEST_RAW_PHASES
    ]
    if not candidates:
        return ("none", None)
    starts = [_rfc3339_ns(_status(item).get("startTimestamp")) for item in candidates]
    if any(start is None for start in starts):
        raise MigrationEvidenceError(
            "latest_start_timestamp_invalid", f"a {token} candidate has a missing or malformed startTimestamp"
        )
    newest = max(starts)
    winners = [item for item, start in zip(candidates, starts) if start == newest]
    if len(winners) > 1:
        raise MigrationEvidenceError("latest_ambiguous", f"{len(winners)} {token} candidates share the newest start")
    return ("selected", winners[0])


def predict_correlated_backup(
    inventory: Any, source_name: Any, resource_type: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Predict the controller's correlated choice from a concrete Backup name.

    Child-evidence amendment section 2: an existing exact-name candidate wins
    with no pre-filter; otherwise exactly one raw +-30 s candidate is selected,
    none is ("none", None), and more than one raises. Like predict_latest_backup
    this returns the raw inventory object and applies no eligibility; journal
    only what select_correlated_evidence returns.
    """
    token = _token(resource_type, CORRELATED_RESOURCE_TYPES)
    if not _non_empty_str(source_name):
        raise MigrationEvidenceError("malformed_source_name", "the correlation source Backup name must be non-empty")
    entries = _inventory(inventory)
    hyphen = source_name.rfind("-")
    if hyphen != -1:
        exact_name = token + source_name[hyphen:]
        for name, item in entries:
            if name == exact_name:
                return ("selected", item)
    target = _name_timestamp_ns(source_name)
    if target is None:
        return ("none", None)
    matches = [item for name, item in entries if token in name and _within_window(item, target)]
    if len(matches) > 1:
        raise MigrationEvidenceError(
            "correlated_ambiguous", f"{len(matches)} {token} Backups start within 30s of {source_name}"
        )
    return ("selected", matches[0]) if matches else ("none", None)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _backup_parts(raw: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("metadata"), dict):
        raise MigrationEvidenceError("malformed_backup", "a Backup must be an object with metadata")
    status = raw.get("status", {})
    if not isinstance(status, dict):
        raise MigrationEvidenceError("malformed_backup", "Backup status must be an object")
    return raw["metadata"], status


def _counter(status: Dict[str, Any], key: str) -> int:
    # Velero omits a zero counter; bool is an int subclass and is rejected.
    if key not in status:
        return 0
    value = status[key]
    if type(value) is not int or value < 0:
        raise MigrationEvidenceError("malformed_backup_counter", f"Backup status.{key} must be a non-negative integer")
    return value


def _token(resource_type: Any, allowed: frozenset) -> str:
    if resource_type not in allowed:
        raise MigrationEvidenceError("unsupported_resource_type", f"resource type {resource_type!r} is not supported")
    return SCHEDULE_TOKENS[resource_type]


def _inventory(inventory: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Return (name, item) pairs of a strictly complete Backup list."""
    if not isinstance(inventory, list):
        raise MigrationEvidenceError("malformed_inventory", "the Backup inventory must be a list")
    entries = []
    for item in inventory:
        try:
            metadata, _ = _backup_parts(item)
        except MigrationEvidenceError as exc:
            raise MigrationEvidenceError("malformed_inventory", str(exc)) from exc
        name = metadata.get("name")
        if not _non_empty_str(name):
            raise MigrationEvidenceError("malformed_inventory", "an inventory Backup has no metadata.name")
        # A non-string phase fails the controller's typed decode of the whole LIST;
        # an absent or null phase decodes to "" and is merely not a candidate.
        if _status(item).get("phase") is not None and not isinstance(_status(item)["phase"], str):
            raise MigrationEvidenceError("malformed_inventory", f"Backup {name} status.phase is not a string")
        entries.append((name, item))
    if len({name for name, _ in entries}) != len(entries):
        raise MigrationEvidenceError("malformed_inventory", "the Backup inventory repeats a name")
    return entries


def _status(item: Dict[str, Any]) -> Dict[str, Any]:
    return item.get("status", {})


def _phase(item: Dict[str, Any]) -> Optional[str]:
    phase = _status(item).get("phase")
    return phase if isinstance(phase, str) else None


def _within_window(item: Dict[str, Any], target: int) -> bool:
    # A nil startTimestamp is excluded, as upstream; a malformed one blocks.
    raw_start = _status(item).get("startTimestamp")
    if raw_start is None:
        return False
    start = _rfc3339_ns(raw_start)
    if start is None:
        raise MigrationEvidenceError("malformed_inventory", "a correlated candidate has a malformed startTimestamp")
    return abs(start - target) <= _CORRELATION_WINDOW_NS


def _rfc3339_ns(value: Any) -> Optional[int]:
    """Return an RFC 3339 timestamp as integer nanoseconds since the epoch, or None."""
    match = _RFC3339.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction, zulu, sign, off_hour, off_minute = match.groups()
    try:
        if zulu:
            tz = timezone.utc
        else:
            if int(off_minute) > 59:
                return None
            offset = timedelta(hours=int(off_hour), minutes=int(off_minute))
            tz = timezone(-offset if sign == "-" else offset)
        instant = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=tz)
        seconds_ns = _to_ns(instant)
    except ValueError:
        return None
    return seconds_ns + int((fraction or "").ljust(9, "0")[:9])


def _name_timestamp_ns(source_name: str) -> Optional[int]:
    """Mirror getBackupTimestamp; a parse error or Go zero time is None."""
    hyphen = source_name.rfind("-")
    if hyphen == -1:
        return None
    match = _NAME_TIMESTAMP.fullmatch(source_name[hyphen:].strip("-"))
    if match is None:
        return None
    *fields, fraction = match.groups()
    try:
        instant = datetime(*(int(part) for part in fields), tzinfo=timezone.utc)
    except ValueError:
        return None
    # Go keeps only the first nine fractional digits.
    nanos = int((fraction or "").ljust(9, "0")[:9])
    if instant == _GO_ZERO_TIME and nanos == 0:
        return None
    return _to_ns(instant) + nanos


def _to_ns(instant: datetime) -> int:
    return ((instant - _EPOCH) // timedelta(seconds=1)) * _NS_PER_SECOND
