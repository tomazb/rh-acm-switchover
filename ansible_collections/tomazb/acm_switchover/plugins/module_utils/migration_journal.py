# SPDX-License-Identifier: MIT
"""R4-04 migration evidence: the pure migration-journal validator (plan Task 2).

Collection mirror of lib/migration_journal.py. It validates the persisted
``migration_backups`` record (schema version 2), its Restore-cleanup state
machine, the expected-name waiver, the operator repair record and the canonical
Restore spec fingerprint. Normative sources: the July design (sections 1, 1a, 2, 4 and 4a), the August amendment
(sections 2-6 and 10) and the controller child-evidence amendment (sections
3-5) under docs/plans/.

Nothing here performs I/O or imports Ansible, so root tests load it directly.
Every rule fails closed by raising MigrationEvidenceError with a stable
``code``. The two form factors share no code;
tests/test_migration_evidence_parity.py holds them equal.

These validators decide only what the persisted record(s) alone can prove, so
an acceptance is necessary, never sufficient, for any completion, cleanup,
repair or teardown gate. Enforced by callers, never here: owner references,
live child namespaces and status locators, live locator absence, live Backup
UID/projection revalidation, the teardown barrier, provenance of an accepted
PATCH or DELETE response, uninterrupted absence polling, resumed-absence
classification, post-activation operation success and generated-name
collision prediction (PR C); store readability, full reset, rewind,
persistence ownership and check-mode non-persistence (Task 3).

validate_migration_journal checks in this fixed order, so each rejected record
reports one code: (1) root shape, malformed_journal or
unsupported_schema_version; (2) restore fields, malformed_restore, then
activation_method_mismatch and controller_contract_mismatch; (3) Backup
categories, invalid_category_set, then malformed_backup_projection; (4)
invalid_backup_fields; (5) invalid_precondition; (6) each child list in
VELERO_RESTORE_LISTS order: malformed_child_entry, child_namespace_mismatch,
child_list_not_permitted, child_backup_mismatch, child_list_unsorted,
duplicate_child_name; (7) malformed_waiver, then invalid_post_activation; (8)
the cleanup record: malformed_cleanup, malformed_recovery, malformed_repair,
invalid_cleanup_state; (9) the restore lifecycle: patch_identity_missing,
fingerprint_mismatch, incomplete_bundle, completion_child_count,
activation_names_unverified, teardown_revalidation_premature; (10)
post_activation_names_unverified; (11) repair_identity_mismatch; (12)
cleanup_copy_mismatch; (13) cleanup_prerequisite_missing. The other
validators check the previous record, then the candidate, then:
validate_journal_transition invalid_freeze_write, frozen_field_changed,
child_entry_rewritten, then the cleanup edge; validate_cleanup_transition
invalid_cleanup_transition, frozen_field_changed, then
repair_identity_mismatch; validate_waiver
malformed_waiver, waiver_scope_mismatch, malformed_expected_names,
waiver_expected_names_empty; validate_repair the journal, then
repair_not_permitted, malformed_repair, repair_identity_mismatch.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_child_evidence import (
    FROZEN_CATEGORIES,
    PASSIVE_PATCH_CATEGORIES,
    STATUS_NAME_FIELDS,
    VELERO_RESTORE_LISTS,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    ACM_MINOR_CONTRACTS,
    MigrationEvidenceError,
    _rfc3339_ns,
)

_LEGACY = "legacy_2_12_2_16"
_ACTIVE = "active_2_17"
_MC_FIELD = "veleroManagedClustersBackupName"
_CREDS_FIELD = "veleroCredentialsBackupName"
_RES_FIELD = "veleroResourcesBackupName"
_CLEANUP_RESTORED = "CleanupRestored"
# July section 1: the evidence-scope method each mutation kind records.
_METHODS = {"passive_patch": "passive", "passive_restore": "passive", "full_restore": "full"}
_CLEANUP_STATES = ("not_started", "intent_persisted", "delete_accepted", "recovery_required", "completed", "repaired")
_RECOVERY_REASONS = ("absent_without_completion", "replacement_uid", "replacement_during_poll")
_WAIVER_SCOPES = ("activation", "post_activation", "both")
_PROJECTION_KEYS = ("activation_method", "mutation_kind", "backup_fields", "cleanup_before_restore")
# August section 4: the upstream-normalized passive sync options a fresh passive_patch requires.
_PATCH_NORMALIZED = {_MC_FIELD: "skip", _CREDS_FIELD: "latest", _RES_FIELD: "latest"}
_BACKUP_ALIASES = ("latest", "skip")
# Go unicode.IsSpace, which strings.TrimSpace uses; Python's str.strip() also strips U+001C-U+001F.
_GO_SPACE = "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
_GO_SPACE += "\u2028\u2029\u202f\u205f\u3000"
_HEX64 = re.compile(r"[0-9a-f]{64}")

# Child-evidence amendment section 4.1 and August section 5: the minimum child count
# per list when restore.completed_at is set, applied only to a frozen category. 2.17
# full_restore adds one unpublished -active role to two lists, which then hold exactly two.
_COMPLETION_MINIMUMS = {
    "passive_patch": {"managed_clusters": 1, "activation_credentials": 1, "activation_resources_generic": 1},
    "passive_restore": {"managed_clusters": 1, "activation_credentials": 1, "activation_resources_generic": 1},
    "full_restore": {"managed_clusters": 1, "credentials": 1, "resources": 1, "resources_generic": 1},
}
_COMPLETION_EXACT = {("full_restore", _ACTIVE): {"credentials": 2, "resources_generic": 2}}

# July section 1 "State invariants": the cleanup fields each state requires set and
# requires null. The accepted-delete pair is always set together or not at all.
_INTENT = (
    "operation_id",
    "namespace",
    "name",
    "uid",
    "generation",
    "activation_method",
    "mutation_kind",
    "cleanup_before_restore",
    "spec_fingerprint",
    "intent_at",
)
_ACCEPTED = ("final_get_resource_version", "delete_accepted_at")
_DONE = ("absence_verified_at", "completed_at")
_STATE_FIELDS = {
    "not_started": ((), _INTENT + _ACCEPTED + _DONE + ("recovery", "repair")),
    "intent_persisted": (_INTENT, _ACCEPTED + _DONE + ("recovery", "repair")),
    "delete_accepted": (_INTENT + _ACCEPTED, _DONE + ("recovery", "repair")),
    "recovery_required": (_INTENT + ("recovery",), _DONE + ("repair",)),
    "completed": (_INTENT + _ACCEPTED + _DONE, ("recovery", "repair")),
    "repaired": (_INTENT + ("recovery", "repair"), _DONE),
}
_COPIED = _INTENT[1:-1] + ("backup_fields",)
_PREREQUISITES = (
    ("restore", "completed_at"),
    ("restore", "teardown_revalidated_at"),
    ("post_activation", "completed_at"),
)

_Spec = Dict[str, Tuple[Callable[[Any], bool], bool]]


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_timestamp(value: Any) -> bool:
    return _rfc3339_ns(value) is not None


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _is_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _is_string_map(value: Any) -> bool:
    return isinstance(value, dict) and all(_non_empty_str(k) and _non_empty_str(v) for k, v in value.items())


def _one_of(*allowed: str) -> Callable[[Any], bool]:
    return lambda value: isinstance(value, str) and value in allowed


def _is_velero_restores(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(VELERO_RESTORE_LISTS)
        and all(isinstance(entries, list) for entries in value.values())
    )


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _is_evidence_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_non_empty_str(item) for item in value)


_S = (_non_empty_str, False)
_S_OPT = (_non_empty_str, True)
_T = (_is_timestamp, False)
_T_OPT = (_is_timestamp, True)
_RESTORE_FIELDS: _Spec = {
    "namespace": _S,
    "name": _S,
    "uid": _S_OPT,
    "generation": (_is_int, True),
    "activation_method": (_one_of("passive", "full"), False),
    "mutation_kind": (_one_of(*_METHODS), False),
    "acm_minor": (_one_of(*ACM_MINOR_CONTRACTS), False),
    "controller_contract": (_one_of(_LEGACY, _ACTIVE), False),
    "backup_fields": (_is_dict, False),
    "cleanup_before_restore": (_one_of(_CLEANUP_RESTORED), False),
    "spec_fingerprint": (_is_fingerprint, True),
    "backup_names_verified_at": _T_OPT,
    "completed_at": _T_OPT,
    "names_verified_at": _T_OPT,
    "teardown_revalidated_at": _T_OPT,
    "velero_restores": (_is_velero_restores, False),
}
_BACKUP_FIELDS: _Spec = {
    "namespace": _S,
    "name": (lambda value: _non_empty_str(value) and _go_normalize(value) not in _BACKUP_ALIASES, False),
    "uid": _S,
    "phase": (_one_of("Completed"), False),
    "completed_at": _T,
    "errors": (lambda value: _is_int(value) and value == 0, False),
    "warnings": (lambda value: _is_int(value) and value >= 0, False),
}
_CHILD_FIELDS: _Spec = {
    "namespace": _S,
    "name": _S,
    "uid": _S,
    "backup_name": _S,
    "phase": (_one_of("Completed"), False),
}
_WAIVER_FIELDS: _Spec = {
    "flag": _S,
    "journaled_at": _T,
    "actor": _S,
    "reason": _S,
    "request_id": _S_OPT,
    "scope": (_one_of(*_WAIVER_SCOPES), False),
    "outcome": (_one_of("waived"), False),
}
_POST_ACTIVATION_FIELDS: _Spec = {"names_verified_at": _T_OPT, "completed_at": _T_OPT}
_CLEANUP_FIELDS: _Spec = {
    "operation_id": (_is_uuid, True),
    "state": (_one_of(*_CLEANUP_STATES), False),
    "namespace": _S_OPT,
    "name": _S_OPT,
    "uid": _S_OPT,
    "generation": (_is_int, True),
    "activation_method": (_one_of("passive", "full"), True),
    "mutation_kind": (_one_of(*_METHODS), True),
    "cleanup_before_restore": (_one_of(_CLEANUP_RESTORED), True),
    "spec_fingerprint": (_is_fingerprint, True),
    "backup_fields": (_is_string_map, False),
    "intent_at": _T_OPT,
    "final_get_resource_version": _S_OPT,
    "delete_accepted_at": _T_OPT,
    "absence_verified_at": _T_OPT,
    "completed_at": _T_OPT,
    "recovery": (_is_dict, True),
    "repair": (_is_dict, True),
}
_RECOVERY_FIELDS: _Spec = {
    "required_at": _T,
    "reason_code": (_one_of(*_RECOVERY_REASONS), False),
    "observed_uid": _S_OPT,
    "observed_resource_version": _S_OPT,
}
_REPAIR_FIELDS: _Spec = {
    "actor": _S,
    "acknowledged_at": _T,
    "reason": _S,
    "run_id": (_is_uuid, False),
    "operation_id": (_is_uuid, False),
    "inspected_evidence": (_is_evidence_list, False),
}
# July section 4a: every allowed cleanup edge and the fields it may introduce or
# change. Every other field, state included, is carried unchanged.
_ALL_CLEANUP_FIELDS = frozenset(_CLEANUP_FIELDS)
_CLEANUP_EDGES = {
    ("not_started", "intent_persisted"): _ALL_CLEANUP_FIELDS,
    ("intent_persisted", "delete_accepted"): frozenset(("state",) + _ACCEPTED),
    ("intent_persisted", "recovery_required"): frozenset(("state", "recovery")),
    ("delete_accepted", "delete_accepted"): frozenset(_ACCEPTED),
    ("delete_accepted", "completed"): frozenset(("state",) + _DONE),
    ("delete_accepted", "recovery_required"): frozenset(("state", "recovery")),
    ("recovery_required", "repaired"): frozenset(("state", "repair")),
}
_CLEANUP_EDGES.update({(state, state): frozenset() for state in _CLEANUP_STATES if state != "delete_accepted"})
# Fields a transition never changes, and fields that keep their value once set.
_FROZEN_RESTORE = (
    "namespace",
    "name",
    "activation_method",
    "mutation_kind",
    "acm_minor",
    "controller_contract",
    "backup_fields",
    "cleanup_before_restore",
    "passive_patch_precondition",
)
_SET_ONCE = (
    ("restore", "uid"),
    ("restore", "generation"),
    ("restore", "spec_fingerprint"),
    ("restore", "backup_names_verified_at"),
    ("restore", "completed_at"),
    ("restore", "names_verified_at"),
    ("restore", "teardown_revalidated_at"),
    ("post_activation", "names_verified_at"),
    ("post_activation", "completed_at"),
)
_ABSENT = object()


def canonical_restore_projection(journal: Any) -> Dict[str, Any]:
    """Return the four-key spec projection the fingerprint covers (August section 6)."""
    restore = journal.get("restore") if isinstance(journal, dict) else None
    if (
        not isinstance(restore, dict)
        or any(key not in restore for key in _PROJECTION_KEYS)
        or not all(_non_empty_str(restore[key]) for key in _PROJECTION_KEYS if key != "backup_fields")
        or not _is_string_map(restore["backup_fields"])
    ):
        raise MigrationEvidenceError("malformed_restore", "the restore spec projection is incomplete or malformed")
    return {key: copy.deepcopy(restore[key]) for key in _PROJECTION_KEYS}


def restore_spec_fingerprint(journal: Any) -> str:
    """Return the lowercase SHA-256 hex of the sorted-key, ASCII-escaped projection JSON."""
    text = json.dumps(canonical_restore_projection(journal), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def validate_migration_journal(candidate: Any) -> Dict[str, Any]:
    """Return a detached copy of a valid migration journal, or raise.

    Validates the complete schema and every state-dependent invariant in the
    order listed in the module docstring. A pre-mutation record and a partial
    bundle are valid; they are simply not terminal.
    """
    _require_root(candidate)
    restore = candidate["restore"]
    kind, contract = _require_restore(restore)
    backups = candidate["backups"]
    _require_backups(backups, kind, contract)
    _require_backup_fields(restore, backups, kind)
    if kind == "passive_patch":
        _require_precondition(restore["passive_patch_precondition"])
    _require_child_lists(restore, backups)
    if candidate["waiver"] is not None:
        _require_fields(candidate["waiver"], _WAIVER_FIELDS, "malformed_waiver", "waiver", exact=True)
    _require_fields(candidate["post_activation"], _POST_ACTIVATION_FIELDS, "invalid_post_activation", "post_activation")
    cleanup = candidate["cleanup"]
    _require_cleanup(cleanup)
    _require_restore_lifecycle(candidate, kind, contract)
    post = candidate["post_activation"]
    if post["completed_at"] is not None and post["names_verified_at"] is None:
        if not _waived(candidate["waiver"], "post_activation"):
            raise MigrationEvidenceError(
                "post_activation_names_unverified", "post-activation completed without a name check or waiver"
            )
    repair = cleanup["repair"]
    if repair is not None and (
        repair["run_id"] != candidate["run_id"] or repair["operation_id"] != cleanup["operation_id"]
    ):
        raise MigrationEvidenceError("repair_identity_mismatch", "the repair names another run or operation")
    if cleanup["state"] != "not_started":
        for field in _COPIED:
            if cleanup[field] != restore[field]:
                raise MigrationEvidenceError("cleanup_copy_mismatch", f"cleanup.{field} is not a copy of restore")
        for block, field in _PREREQUISITES:
            if candidate[block][field] is None:
                raise MigrationEvidenceError(
                    "cleanup_prerequisite_missing", f"cleanup started before {block}.{field} was recorded"
                )
    return copy.deepcopy(candidate)


def validate_cleanup_transition(previous_cleanup: Any, candidate_cleanup: Any) -> Dict[str, Any]:
    """Return the candidate cleanup record when it is an allowed July section 4a edge.

    Only the two cleanup records are seen: shape, per-state invariants, edge
    legality and carried-field equality. Restore copies and prerequisites need
    the whole journal and belong to validate_journal_transition.
    """
    _require_cleanup(previous_cleanup)
    _require_cleanup(candidate_cleanup)
    edge = (previous_cleanup["state"], candidate_cleanup["state"])
    if edge not in _CLEANUP_EDGES:
        raise MigrationEvidenceError("invalid_cleanup_transition", f"cleanup cannot move {edge[0]} -> {edge[1]}")
    for field in (set(previous_cleanup) | set(candidate_cleanup)) - _CLEANUP_EDGES[edge]:
        if previous_cleanup.get(field, _ABSENT) != candidate_cleanup.get(field, _ABSENT):
            raise MigrationEvidenceError("frozen_field_changed", f"cleanup.{field} changed on {edge[0]} -> {edge[1]}")
    repair = candidate_cleanup["repair"]
    if repair is not None and repair["operation_id"] != candidate_cleanup["operation_id"]:
        raise MigrationEvidenceError("repair_identity_mismatch", "the repair names another cleanup operation")
    return copy.deepcopy(candidate_cleanup)


def validate_journal_transition(previous: Any, candidate: Any) -> Dict[str, Any]:
    """Return the validated candidate when it may replace the previous journal.

    None as the previous journal makes the candidate the freeze write. An
    existing field is reconciled, never rewritten: frozen evidence stays
    equal, set-once fields keep their value, accepted child entries stay
    present and unchanged, and cleanup follows its state machine. The
    delete_accepted retry is the only permitted rewrite.
    """
    if previous is not None:
        validate_migration_journal(previous)
    result = validate_migration_journal(candidate)
    if previous is None:
        if candidate["cleanup"]["state"] != "not_started" or candidate["restore"]["completed_at"] is not None:
            raise MigrationEvidenceError("invalid_freeze_write", "the first journal write must precede completion")
        return result
    for key in ("schema_version", "run_id", "resolved_at", "backups"):
        if previous[key] != candidate[key]:
            raise MigrationEvidenceError("frozen_field_changed", f"{key} changed")
    for key in _FROZEN_RESTORE:
        if previous["restore"].get(key, _ABSENT) != candidate["restore"].get(key, _ABSENT):
            raise MigrationEvidenceError("frozen_field_changed", f"restore.{key} changed")
    for block, key in _SET_ONCE:
        if previous[block][key] is not None and previous[block][key] != candidate[block][key]:
            raise MigrationEvidenceError("frozen_field_changed", f"{block}.{key} changed once set")
    for key in VELERO_RESTORE_LISTS:
        accepted = {entry["name"]: entry for entry in candidate["restore"]["velero_restores"][key]}
        for entry in previous["restore"]["velero_restores"][key]:
            if accepted.get(entry["name"]) != entry:
                raise MigrationEvidenceError("child_entry_rewritten", f"child {entry['name']} was removed or changed")
    validate_cleanup_transition(previous["cleanup"], candidate["cleanup"])
    return result


def validate_waiver(candidate: Any, expected_names: Any, scope: Any) -> Dict[str, Any]:
    """Return a waiver that covers the requested name-check scope, or raise.

    A waiver substitutes only for the expected-ManagedCluster name checks
    (July section 2); it never stands in for counts, provenance, completion,
    cleanup, repair, revalidation or locator absence, and it never records a
    verification timestamp. There must be expected names to waive.
    """
    _require_fields(candidate, _WAIVER_FIELDS, "malformed_waiver", "waiver", exact=True)
    if scope not in _WAIVER_SCOPES or candidate["scope"] not in (scope, "both"):
        raise MigrationEvidenceError("waiver_scope_mismatch", f"the waiver does not cover scope {scope!r}")
    if not isinstance(expected_names, list) or not all(_non_empty_str(name) for name in expected_names):
        raise MigrationEvidenceError("malformed_expected_names", "expected names must be a list of non-empty strings")
    if not expected_names:
        raise MigrationEvidenceError("waiver_expected_names_empty", "there is no expected name to waive")
    return copy.deepcopy(candidate)


def validate_repair(candidate: Any, journal: Any) -> Dict[str, Any]:
    """Return the repaired cleanup record for an operator repair, or raise.

    The journal must be valid and in recovery_required, and the repair must
    name its run and cleanup operation. The result preserves the recovery and
    any accepted-delete evidence and never synthesizes absence or completion.
    Fresh strict live absence and the explicit operator action are caller
    obligations (PR C).
    """
    validate_migration_journal(journal)
    cleanup = journal["cleanup"]
    if cleanup["state"] != "recovery_required":
        raise MigrationEvidenceError("repair_not_permitted", f"cleanup state {cleanup['state']} cannot be repaired")
    _require_fields(candidate, _REPAIR_FIELDS, "malformed_repair", "repair", exact=True)
    if candidate["run_id"] != journal["run_id"] or candidate["operation_id"] != cleanup["operation_id"]:
        raise MigrationEvidenceError("repair_identity_mismatch", "the repair names another run or operation")
    return dict(copy.deepcopy(cleanup), state="repaired", repair=copy.deepcopy(candidate))


def _require_fields(value: Any, spec: _Spec, code: str, label: str, exact: bool = False) -> None:
    """Require every documented key (and, for a closed object, no other) with a valid value."""
    if not isinstance(value, dict) or any(key not in value for key in spec) or (exact and set(value) != set(spec)):
        raise MigrationEvidenceError(code, f"{label} does not carry exactly its documented fields")
    for key, (check, nullable) in spec.items():
        if not (value[key] is None and nullable) and not check(value[key]):
            raise MigrationEvidenceError(code, f"{label}.{key} is invalid")


def _require_root(candidate: Any) -> None:
    keys = ("schema_version", "run_id", "resolved_at", "backups", "restore", "cleanup", "post_activation", "waiver")
    if not isinstance(candidate, dict) or any(key not in candidate for key in keys):
        raise MigrationEvidenceError("malformed_journal", "the migration journal lacks a required field")
    if not _is_int(candidate["schema_version"]) or candidate["schema_version"] != 2:
        raise MigrationEvidenceError("unsupported_schema_version", "the migration journal is not schema version 2")
    if (
        not _is_uuid(candidate["run_id"])
        or not _is_timestamp(candidate["resolved_at"])
        or not all(isinstance(candidate[key], dict) for key in ("backups", "restore", "cleanup", "post_activation"))
        or not (candidate["waiver"] is None or isinstance(candidate["waiver"], dict))
    ):
        raise MigrationEvidenceError("malformed_journal", "the migration journal has a malformed top-level field")


def _require_restore(restore: Dict[str, Any]) -> Tuple[str, str]:
    _require_fields(restore, _RESTORE_FIELDS, "malformed_restore", "restore")
    kind = restore["mutation_kind"]
    if ("passive_patch_precondition" in restore) != (kind == "passive_patch"):
        raise MigrationEvidenceError("malformed_restore", "passive_patch_precondition is present iff passive_patch")
    if restore["activation_method"] != _METHODS[kind]:
        raise MigrationEvidenceError(
            "activation_method_mismatch", f"{kind} is not a {restore['activation_method']} method"
        )
    if restore["controller_contract"] != ACM_MINOR_CONTRACTS[restore["acm_minor"]]:
        raise MigrationEvidenceError(
            "controller_contract_mismatch", f"ACM {restore['acm_minor']} is not {restore['controller_contract']}"
        )
    return kind, restore["controller_contract"]


def _require_backups(backups: Dict[str, Any], kind: str, contract: str) -> None:
    required, optional = FROZEN_CATEGORIES.get((kind, contract), (PASSIVE_PATCH_CATEGORIES, ()))
    if not set(required) <= set(backups) <= set(required + optional):
        raise MigrationEvidenceError("invalid_category_set", f"the Backup categories do not fit {kind} on {contract}")
    for category, backup in backups.items():
        _require_fields(backup, _BACKUP_FIELDS, "malformed_backup_projection", f"backups.{category}", exact=True)


def _require_backup_fields(restore: Dict[str, Any], backups: Dict[str, Any], kind: str) -> None:
    if kind == "full_restore":
        expected = {
            _MC_FIELD: backups["managed_clusters"]["name"],
            _CREDS_FIELD: backups["credentials"]["name"],
            _RES_FIELD: backups["resources"]["name"],
        }
    else:
        expected = {_MC_FIELD: "latest"}
    if restore["backup_fields"] != expected:
        raise MigrationEvidenceError("invalid_backup_fields", f"restore.backup_fields is not the {kind} projection")


def _go_normalize(value: str) -> str:
    # strings.ToLower maps rune by rune; Python lowers U+0130 to two characters.
    return "".join("i" if char == "\u0130" else char.lower() for char in value.strip(_GO_SPACE))


def _require_precondition(precondition: Any) -> None:
    keys = {"generation", "resource_version", "backup_fields_raw", "backup_fields_normalized", "status_restore_names"}
    if not isinstance(precondition, dict) or set(precondition) != keys:
        raise MigrationEvidenceError("invalid_precondition", "the passive_patch precondition has the wrong fields")
    raw = precondition["backup_fields_raw"]
    names = precondition["status_restore_names"]
    if (
        not _is_int(precondition["generation"])
        or precondition["generation"] < 1
        or not _non_empty_str(precondition["resource_version"])
        or not isinstance(raw, dict)
        or set(raw) != set(_PATCH_NORMALIZED)
        or not all(_non_empty_str(value) for value in raw.values())
        or precondition["backup_fields_normalized"] != {key: _go_normalize(value) for key, value in raw.items()}
        or precondition["backup_fields_normalized"] != _PATCH_NORMALIZED
        or not isinstance(names, dict)
        or set(names) != set(STATUS_NAME_FIELDS)
        or not all(isinstance(value, str) for value in names.values())
        or names[STATUS_NAME_FIELDS[0]] != ""
    ):
        raise MigrationEvidenceError("invalid_precondition", "the passive_patch precondition is not a fresh intent")


def _require_child_lists(restore: Dict[str, Any], backups: Dict[str, Any]) -> None:
    for key in VELERO_RESTORE_LISTS:
        entries = restore["velero_restores"][key]
        for entry in entries:
            _require_fields(entry, _CHILD_FIELDS, "malformed_child_entry", f"velero_restores.{key}", exact=True)
        if any(entry["namespace"] != restore["namespace"] for entry in entries):
            raise MigrationEvidenceError("child_namespace_mismatch", f"a {key} child is outside the Restore namespace")
        if entries and key not in backups:
            raise MigrationEvidenceError("child_list_not_permitted", f"no {key} Backup is frozen for a child")
        if any(entry["backup_name"] != backups[key]["name"] for entry in entries):
            raise MigrationEvidenceError("child_backup_mismatch", f"a {key} child restores another Backup")
        names = [entry["name"] for entry in entries]
        if names != sorted(names):
            raise MigrationEvidenceError("child_list_unsorted", f"velero_restores.{key} is not sorted by name")
        if len(set(names)) != len(names):
            raise MigrationEvidenceError("duplicate_child_name", f"velero_restores.{key} repeats a name")


def _require_cleanup(cleanup: Any) -> None:
    _require_fields(cleanup, _CLEANUP_FIELDS, "malformed_cleanup", "cleanup")
    recovery = cleanup["recovery"]
    if recovery is not None:
        _require_fields(recovery, _RECOVERY_FIELDS, "malformed_recovery", "cleanup.recovery", exact=True)
        if (recovery["observed_uid"] is None) != (recovery["observed_resource_version"] is None):
            raise MigrationEvidenceError("malformed_recovery", "the observed uid/resourceVersion pair is partial")
    if cleanup["repair"] is not None:
        _require_fields(cleanup["repair"], _REPAIR_FIELDS, "malformed_repair", "cleanup.repair", exact=True)
    required, null = _STATE_FIELDS[cleanup["state"]]
    if (
        any(cleanup[field] is None for field in required)
        or any(cleanup[field] is not None for field in null)
        or bool(cleanup["backup_fields"]) != (cleanup["state"] != "not_started")
        or (cleanup["final_get_resource_version"] is None) != (cleanup["delete_accepted_at"] is None)
    ):
        raise MigrationEvidenceError("invalid_cleanup_state", f"the {cleanup['state']} cleanup record is inconsistent")


def _require_restore_lifecycle(journal: Dict[str, Any], kind: str, contract: str) -> None:
    restore = journal["restore"]
    if kind == "passive_patch" and restore["uid"] is None:
        raise MigrationEvidenceError("patch_identity_missing", "passive_patch persists restore.uid before the PATCH")
    if restore["spec_fingerprint"] is not None and restore["spec_fingerprint"] != restore_spec_fingerprint(journal):
        raise MigrationEvidenceError("fingerprint_mismatch", "restore.spec_fingerprint is not the spec projection")
    if restore["completed_at"] is not None:
        for field in ("uid", "generation", "spec_fingerprint", "backup_names_verified_at"):
            if restore[field] is None:
                raise MigrationEvidenceError("incomplete_bundle", f"restore completed without restore.{field}")
        _require_completion_counts(restore, journal["backups"], kind, contract)
        if (
            kind == "passive_patch"
            and restore["names_verified_at"] is None
            and not _waived(journal["waiver"], "activation")
        ):
            raise MigrationEvidenceError(
                "activation_names_unverified", "passive_patch completed without a name check or waiver"
            )
    if restore["teardown_revalidated_at"] is not None and (
        restore["completed_at"] is None or journal["post_activation"]["completed_at"] is None
    ):
        raise MigrationEvidenceError(
            "teardown_revalidation_premature", "teardown revalidation precedes restore or post-activation completion"
        )


def _require_completion_counts(restore: Dict[str, Any], backups: Dict[str, Any], kind: str, contract: str) -> None:
    exact = _COMPLETION_EXACT.get((kind, contract), {})
    for key, minimum in _COMPLETION_MINIMUMS[kind].items():
        if key not in backups:
            continue
        count = len(restore["velero_restores"][key])
        if count < minimum or count != exact.get(key, count):
            raise MigrationEvidenceError("completion_child_count", f"velero_restores.{key} holds {count} children")


def _waived(waiver: Optional[Dict[str, Any]], scope: str) -> bool:
    return waiver is not None and waiver["scope"] in (scope, "both")
