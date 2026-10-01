# SPDX-License-Identifier: MIT
"""R4-04 migration evidence: the pure Restore-cleanup transaction decisions (plan Task 10).

Collection mirror of lib/migration_cleanup.py. It decides every step of the
durable cleanup of the journaled ACM Restore: which step a finalization pass
owes, the teardown-revalidation and cleanup-intent writes, the final pre-delete
boundary, the guarded DELETE outcome, the bounded absence poll and its final
read, recovery, operator repair, the terminal locator-absence gate before
BackupSchedule enablement, and the reservation of the journaled locator from
generic Restore cleanup. Normative sources: the July design (sections 1 and
4a), the August amendment (section 6) and the controller child-evidence
amendment (section 3.1) under docs/plans/.

Nothing here performs I/O or imports Ansible, so root tests load it directly.
Every rule fails closed by raising MigrationEvidenceError with a stable
``code``. The two form factors share no code;
tests/test_migration_evidence_parity.py holds them equal.

Callers own the strict reads, the guarded DELETE, the ordering and the writes.
A ``live`` input is the object of one complete strict GET of the journaled
locator, or None for a proven 404; a read failure never reaches this module.
The live completion proof of the teardown barrier is the activation proof the
caller re-runs before teardown_revalidated. Each returned candidate is already
validated as a whole-journal transition from the journal it was built from,
which also re-proves the restore/cleanup copy and the fingerprint.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    RESTORE_CLEANUP_CONFLICT_RETRY_LIMIT,
    RESTORE_CLEANUP_POLL_INTERVAL,
    RESTORE_CLEANUP_POLL_TIMEOUT,
    RESTORE_CLEANUP_REQUEST_TIMEOUT,
    VELERO_BACKUP_LATEST,
    VELERO_BACKUP_SKIP,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import MigrationEvidenceError
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_journal import (
    validate_journal_transition,
    validate_repair,
    validate_repair_request,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_post_activation import (
    require_post_activation_completed,
)

_MANAGED_CLUSTERS_FIELD = "veleroManagedClustersBackupName"
_CREDENTIALS_FIELD = "veleroCredentialsBackupName"
_RESOURCES_FIELD = "veleroResourcesBackupName"
_CLEANUP_POLICY_FIELD = "cleanupBeforeRestore"
_DELETING = ("intent_persisted", "delete_accepted")
_TERMINAL = ("completed", "repaired")
# A DELETE that removed nothing: re-read the locator and decide again, within the bound.
_REREAD_REASONS = ("precondition_failed", "not_found")


def cleanup_schedule() -> Dict[str, Any]:
    """Return the parity-shared bounds of one cleanup attempt.

    attempts counts the guarded DELETEs (the first plus the conflict retries);
    poll_delays are the waits before each strict absence read after an accepted
    DELETE; request_timeout bounds each DELETE request.
    """
    polls = RESTORE_CLEANUP_POLL_TIMEOUT // RESTORE_CLEANUP_POLL_INTERVAL
    return {
        "attempts": RESTORE_CLEANUP_CONFLICT_RETRY_LIMIT + 1,
        "poll_delays": [0] + [RESTORE_CLEANUP_POLL_INTERVAL] * polls,
        "request_timeout": RESTORE_CLEANUP_REQUEST_TIMEOUT,
    }


def cleanup_step(*, journal: Any, repair_requested: Any) -> Dict[str, str]:
    """Return the cleanup step a finalization pass owes, from the validated journal alone.

    not_started revalidates (a recorded teardown_revalidated_at is never fresh
    success); intent_persisted and delete_accepted delete; completed and repaired
    are terminal; recovery_required blocks unless the operator requested repair,
    which is accepted in no other state.
    """
    journal = require_post_activation_completed(journal)
    if not isinstance(repair_requested, bool):
        raise MigrationEvidenceError("malformed_cleanup_input", "the repair request flag is not a boolean")
    state = journal["cleanup"]["state"]
    if repair_requested and state != "recovery_required":
        raise MigrationEvidenceError("repair_not_permitted", f"cleanup state {state} cannot be repaired")
    if state == "recovery_required":
        if not repair_requested:
            raise MigrationEvidenceError(
                "cleanup_recovery_required",
                f"the Restore cleanup requires recovery ({journal['cleanup']['recovery']['reason_code']})",
            )
        return {"step": "repair"}
    if state == "not_started":
        return {"step": "revalidate"}
    return {"step": "delete" if state in _DELETING else "terminal"}


def teardown_revalidated(*, journal: Any, now: Any) -> Dict[str, Any]:
    """Return the restore.teardown_revalidated_at candidate after this pass's live barrier passed.

    The timestamp is set once and kept on a later pass; the barrier itself
    runs on every pass that has not created cleanup intent.
    """
    journal = _require_state(journal, ("not_started",))
    candidate = copy.deepcopy(journal)
    restore = candidate["restore"]
    restore["teardown_revalidated_at"] = restore["teardown_revalidated_at"] or now
    return {"candidate": validate_journal_transition(journal, candidate), "write": candidate != journal}


def cleanup_intent(*, journal: Any, operation_id: Any, now: Any) -> Dict[str, Any]:
    """Return the intent_persisted candidate, written durably before any DELETE.

    It creates the immutable operation ID once and copies the journaled Restore
    identity, method, policy, fingerprint and owned backup map structurally.
    """
    journal = _require_state(journal, ("not_started",))
    restore = journal["restore"]
    if restore["teardown_revalidated_at"] is None:
        raise MigrationEvidenceError("teardown_not_revalidated", "cleanup intent needs the teardown revalidation")
    candidate = copy.deepcopy(journal)
    cleanup = candidate["cleanup"]
    for field in (
        "namespace",
        "name",
        "uid",
        "generation",
        "activation_method",
        "mutation_kind",
        "cleanup_before_restore",
        "spec_fingerprint",
        "backup_fields",
    ):
        cleanup[field] = copy.deepcopy(restore[field])
    cleanup.update(operation_id=operation_id, state="intent_persisted", intent_at=now)
    return {"candidate": validate_journal_transition(journal, candidate)}


def pre_delete(*, journal: Any, live: Any, now: Any) -> Dict[str, Any]:
    """Decide the final pre-delete boundary from one strict read of the journaled locator.

    The journaled UID with the exact journaled projection binds that read's
    resourceVersion for the guarded DELETE. Absence is never success: it
    recovers as absent_without_completion. Another UID is preserved and
    recovers as replacement_uid. Any drift or malformed read blocks unchanged.
    """
    journal = _require_state(journal, _DELETING)
    if live is None:
        return {"action": "recover", "candidate": _recovery(journal, "absent_without_completion", None, now)}
    uid, revision = _identity(live)
    if uid != journal["cleanup"]["uid"]:
        return {"action": "recover", "candidate": _recovery(journal, "replacement_uid", live, now)}
    _require_projection(journal, live)
    return {"action": "delete", "uid": uid, "resource_version": revision}


def delete_outcome(*, journal: Any, result: Any, resource_version: Any, attempt: Any, now: Any) -> Dict[str, Any]:
    """Classify one guarded DELETE result by its verified acceptance, never by HTTP status.

    An accepted DELETE of the bound UID and resourceVersion records
    delete_accepted (rewriting only the audit pair on a resumed retry). A
    precondition conflict or a 404 removed nothing: the caller re-reads and
    re-validates in full, within the shared attempt bound. Anything else is
    not acceptance and blocks with the state unchanged.
    """
    journal = _require_state(journal, _DELETING)
    if not _positive_int(attempt):
        raise MigrationEvidenceError("malformed_cleanup_input", "the DELETE attempt is not a positive integer")
    reason = result.get("reason") if isinstance(result, dict) else None
    if reason in _REREAD_REASONS and result.get("accepted") is not True:
        if attempt >= cleanup_schedule()["attempts"]:
            raise MigrationEvidenceError(
                "cleanup_conflict_retries_exhausted", f"no guarded DELETE was accepted in {attempt} attempts"
            )
        return {"outcome": "retry"}
    if not isinstance(result, dict) or result.get("accepted") is not True or reason != "ok":
        raise MigrationEvidenceError("delete_not_accepted", f"the guarded DELETE was not accepted ({reason})")
    cleanup = journal["cleanup"]
    if result.get("uid") != cleanup["uid"] or result.get("resource_version") != resource_version:
        raise MigrationEvidenceError("malformed_delete_result", "the accepted DELETE bound another object")
    candidate = copy.deepcopy(journal)
    candidate["cleanup"].update(state="delete_accepted", final_get_resource_version=resource_version)
    candidate["cleanup"]["delete_accepted_at"] = now
    return {"outcome": "accepted", "candidate": validate_journal_transition(journal, candidate)}


def absence_poll(*, journal: Any, live: Any, now: Any) -> Dict[str, Any]:
    """Decide one absence poll after an accepted DELETE: pending, absent, or a recovered replacement."""
    journal = _require_state(journal, ("delete_accepted",))
    if live is None:
        return {"state": "absent"}
    uid, _ = _identity(live)
    if uid == journal["cleanup"]["uid"]:
        return {"state": "pending"}
    return {"state": "recover", "candidate": _recovery(journal, "replacement_during_poll", live, now)}


def final_absence(*, journal: Any, live: Any, now: Any) -> Dict[str, Any]:
    """Decide the one strict final read after the poll first saw absence.

    Only a proven 404 completes the cleanup, writing absence_verified_at and
    completed_at together; a replacement recovers and never completes.
    """
    journal = _require_state(journal, ("delete_accepted",))
    if live is not None:
        uid, _ = _identity(live)
        if uid == journal["cleanup"]["uid"]:
            raise MigrationEvidenceError("absence_not_confirmed", "the final read still finds the deleted Restore")
        return {"state": "recover", "candidate": _recovery(journal, "replacement_during_poll", live, now)}
    candidate = copy.deepcopy(journal)
    candidate["cleanup"].update(state="completed", absence_verified_at=now, completed_at=now)
    return {"state": "completed", "candidate": validate_journal_transition(journal, candidate)}


def repair_cleanup(*, journal: Any, request: Any, live: Any, now: Any) -> Dict[str, Any]:
    """Return the repaired candidate for an operator's audited acknowledgement, or raise.

    Only recovery_required, the exact run and operation, a complete request and
    a fresh strict read proving the locator absent are accepted. The repair
    keeps the recovery and any accepted-delete evidence and never synthesizes
    absence or completion.
    """
    journal = require_post_activation_completed(journal)
    record = dict(validate_repair_request(request), acknowledged_at=now)
    cleanup = validate_repair(record, journal)
    if live is not None:
        _identity(live)
        raise MigrationEvidenceError("repair_locator_occupied", "an object occupies the journaled Restore locator")
    candidate = copy.deepcopy(journal)
    candidate["cleanup"] = cleanup
    return {"candidate": validate_journal_transition(journal, candidate)}


def require_locator_absent(*, journal: Any, live: Any) -> Dict[str, Any]:
    """The BackupSchedule-enablement gate: terminal cleanup and a fresh strict read proving absence.

    A same-name object after completion or repair is a new unowned Restore: it
    inherits no evidence and blocks.
    """
    journal = _require_terminal(journal)
    if live is not None:
        _identity(live)
        raise MigrationEvidenceError(
            "locator_occupied", f"Restore {journal['restore']['name']} occupies the journaled locator"
        )
    return {}


def generic_cleanup_restores(*, journal: Any, restores: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Return the Restores generic cleanup may classify once the journaled locator is terminal.

    The caller lists the journaled namespace. The journaled locator is reserved:
    an object there is never routed through generic cleanup and blocks.
    """
    journal = _require_terminal(journal)
    if not isinstance(restores, list):
        raise MigrationEvidenceError("malformed_inventory", "the Restore inventory is not a list")
    for item in restores:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not name:
            raise MigrationEvidenceError("malformed_inventory", "a Restore has no name")
        if name == journal["restore"]["name"]:
            raise MigrationEvidenceError("locator_occupied", f"Restore {name} occupies the journaled locator")
    return {"restores": copy.deepcopy(restores)}


def _require_state(journal: Any, states: Tuple[str, ...]) -> Dict[str, Any]:
    journal = require_post_activation_completed(journal)
    state = journal["cleanup"]["state"]
    if state not in states:
        raise MigrationEvidenceError("cleanup_state_mismatch", f"this cleanup step does not apply in state {state}")
    return journal


def _require_terminal(journal: Any) -> Dict[str, Any]:
    journal = require_post_activation_completed(journal)
    if journal["cleanup"]["state"] not in _TERMINAL:
        raise MigrationEvidenceError(
            "cleanup_not_terminal", f"the Restore cleanup is {journal['cleanup']['state']}, not completed or repaired"
        )
    return journal


def _identity(live: Any) -> Tuple[str, str]:
    """The UID and resourceVersion of one complete safe response; both or the read is malformed."""
    metadata = live.get("metadata") if isinstance(live, dict) else None
    if not isinstance(metadata, dict):
        raise MigrationEvidenceError("malformed_restore_read", "the Restore read has no metadata")
    uid, revision = metadata.get("uid"), metadata.get("resourceVersion")
    if not _non_empty_str(uid) or not _non_empty_str(revision):
        raise MigrationEvidenceError("malformed_restore_read", "the Restore read lacks uid or resourceVersion")
    return uid, revision


def _require_projection(journal: Dict[str, Any], live: Dict[str, Any]) -> None:
    """The exact locator, generation, cleanup policy and method-scoped owned spec of the journal."""
    restore = journal["restore"]
    metadata, spec = live["metadata"], live.get("spec")
    if not isinstance(spec, dict):
        raise MigrationEvidenceError("malformed_restore_read", "the Restore read has no spec")
    if metadata.get("namespace") != restore["namespace"] or metadata.get("name") != restore["name"]:
        raise MigrationEvidenceError("restore_locator_mismatch", "the read is not the journaled Restore locator")
    generation = metadata.get("generation")
    if type(generation) is not int or generation != restore["generation"]:
        raise MigrationEvidenceError("restore_spec_drift", f"{restore['name']} generation drifted")
    if any(field not in spec or spec[field] != value for field, value in _owned_spec(restore).items()):
        raise MigrationEvidenceError("restore_spec_drift", f"{restore['name']} no longer holds the journaled spec")


def _owned_spec(restore: Dict[str, Any]) -> Dict[str, Any]:
    """The live spec values the journal owns: passive kinds keep `latest`, full holds concrete names."""
    kind = restore["mutation_kind"]
    if kind == "passive_patch":
        owned = {_MANAGED_CLUSTERS_FIELD: VELERO_BACKUP_LATEST}
    elif kind == "passive_restore":
        owned = {
            _MANAGED_CLUSTERS_FIELD: VELERO_BACKUP_LATEST,
            _CREDENTIALS_FIELD: VELERO_BACKUP_SKIP,
            _RESOURCES_FIELD: VELERO_BACKUP_SKIP,
        }
    else:
        owned = dict(restore["backup_fields"])
    owned[_CLEANUP_POLICY_FIELD] = restore["cleanup_before_restore"]
    return owned


def _recovery(journal: Dict[str, Any], reason: str, live: Optional[Dict[str, Any]], now: Any) -> Dict[str, Any]:
    """The recovery_required candidate; the observed pair comes only from one complete safe response."""
    observed = _identity(live) if live is not None else (None, None)
    candidate = copy.deepcopy(journal)
    candidate["cleanup"].update(
        state="recovery_required",
        recovery={
            "required_at": now,
            "reason_code": reason,
            "observed_uid": observed[0],
            "observed_resource_version": observed[1],
        },
    )
    return validate_journal_transition(journal, candidate)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value >= 1


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)
