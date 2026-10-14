# SPDX-License-Identifier: MIT
"""R4-04 migration evidence: the pure post-activation completion decisions (plan Task 9).

Collection mirror of lib/migration_post_activation.py. It decides what
post-activation may record in the migration journal: the journal an executing
run requires, the expected-name waiver covering the post-activation check, the
strict-inventory ManagedCluster readiness decision, and the two evidence
transitions, post_activation.names_verified_at (or the journaled waiver) and
post_activation.completed_at, written strictly last. Normative sources: the July
design (sections 2-4) and the August amendment (sections 7 and 8) under
docs/plans/.

Nothing here performs I/O or imports Ansible, so root tests load it directly.
Every rule fails closed by raising MigrationEvidenceError with a stable
``code``. The two form factors share no code;
tests/test_migration_evidence_parity.py holds them equal.

Callers own the strict reads, the ordering and the writes: the readiness
decision runs on every attempt, the names evidence follows the connectivity
work, and the completion transition follows every other required
post-activation operation. Each returned candidate is already validated as a
transition from the journal it was built from.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import LOCAL_CLUSTER_NAME
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import MigrationEvidenceError
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_journal import (
    validate_journal_transition,
    validate_migration_journal,
    validate_waiver,
    validate_waiver_request,
)

_SCOPE = "post_activation"
_COVERING = (_SCOPE, "both")
_READY_CONDITIONS = frozenset({"ManagedClusterConditionAvailable", "ManagedClusterJoined"})


def require_completed_restore(journal: Any) -> Dict[str, Any]:
    """Return the validated journal an executing post-activation run requires, or raise.

    Every executing run (switchover, restore-only, full restore) holds a journal once
    activation has run. Without one, or before restore.completed_at, post-activation
    can prove no migration and never records completion.
    """
    if journal is None:
        raise MigrationEvidenceError(
            "journal_required", "post-activation needs the migration journal activation records"
        )
    journal = validate_migration_journal(journal)
    if journal["restore"]["completed_at"] is None:
        raise MigrationEvidenceError("restore_not_completed", "post-activation evidence needs restore.completed_at")
    return journal


def require_post_activation_completed(journal: Any) -> Dict[str, Any]:
    """Return the validated journal a resume past post-activation relies on, or raise.

    A recorded post-activation phase marker is honoured only with the evidence it
    claims: restore.completed_at and post_activation.completed_at. A marker without
    them never stands in for that evidence.
    """
    journal = require_completed_restore(journal)
    if journal["post_activation"]["completed_at"] is None:
        raise MigrationEvidenceError(
            "post_activation_not_completed",
            "the post-activation phase is recorded without post_activation.completed_at",
        )
    return journal


def post_activation_waiver(
    *, journal: Any, requested: Any, expected_names: Any, flag: Any, journaled_at: Any
) -> Dict[str, Any]:
    """Return the waiver covering the post-activation name check, and whether to journal it.

    A journaled waiver is reused and a request differing from it is refused. Only a
    post_activation-scoped request is journaled here, and only while no waiver is
    journaled and the name check has not passed: activation journals the others. A
    request covering post-activation needs an expected name to waive.
    """
    journal = require_completed_restore(journal)
    journaled = journal["waiver"]
    if requested is not None:
        requested = validate_waiver_request(requested)
        if requested["scope"] in _COVERING:
            record = dict(requested, flag=flag, journaled_at=journaled_at, outcome="waived")
            validate_waiver(record, expected_names, _SCOPE)
    if journaled is not None:
        if requested is not None and any(requested[key] != journaled[key] for key in requested):
            raise MigrationEvidenceError(
                "waiver_request_mismatch", "a different expected-name waiver is already journaled"
            )
        if journaled["scope"] not in _COVERING:
            return {"waiver": None, "record": False}
        return {"waiver": validate_waiver(journaled, expected_names, _SCOPE), "record": False}
    if requested is None or requested["scope"] != _SCOPE:
        return {"waiver": None, "record": False}
    if journal["post_activation"]["names_verified_at"] is not None:
        return {"waiver": None, "record": False}
    return {"waiver": record, "record": True}


def managed_cluster_readiness(
    *, items: Any, expected_names: Any, min_managed_clusters: Any, names_waived: Any
) -> Dict[str, Any]:
    """Decide ManagedCluster readiness from one positively complete strict inventory.

    Non-local clusters count; each must be Available and Joined. Expected names and
    the minimum are both enforced (August section 7); a waiver lifts only the name
    predicate. A not-ready inventory is a decision, never an error, so callers can
    poll; malformed inventory or inputs raise. The first failing rule names the code.
    """
    if not isinstance(items, list):
        raise MigrationEvidenceError("malformed_inventory", "the ManagedCluster inventory is not a list")
    if not isinstance(expected_names, list) or not all(_non_empty_str(name) for name in expected_names):
        raise MigrationEvidenceError("malformed_expected_names", "expected names must be a list of non-empty strings")
    if not _count(min_managed_clusters) or not isinstance(names_waived, bool):
        raise MigrationEvidenceError("malformed_expectation", "the count floor or the waiver flag is malformed")
    names: List[str] = []
    pending: List[str] = []
    for item in items:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not _non_empty_str(name):
            raise MigrationEvidenceError("malformed_inventory", "a ManagedCluster has no name")
        if name == LOCAL_CLUSTER_NAME:
            continue
        status = {} if item.get("status") is None else item["status"]
        conditions = (
            ([] if status.get("conditions") is None else status["conditions"]) if isinstance(status, dict) else None
        )
        if not isinstance(conditions, list) or not all(isinstance(condition, dict) for condition in conditions):
            raise MigrationEvidenceError("malformed_inventory", f"ManagedCluster {name} has malformed conditions")
        names.append(name)
        true = {condition.get("type") for condition in conditions if condition.get("status") == "True"}
        if not _READY_CONDITIONS <= true:
            pending.append(name)
    missing = sorted(set(expected_names) - set(names))
    code = None
    # An empty inventory means the reconciler has not caught up, unless nothing is expected:
    # an explicit zero floor with no expected name permits an empty destination (a hub with
    # self-management disabled has no local-cluster).
    if not items and (expected_names or min_managed_clusters > 0):
        code = "inventory_empty"
    elif missing and not names_waived:
        code = "expected_names_missing"
    elif len(names) < min_managed_clusters:
        code = "count_below_minimum"
    elif pending:
        code = "clusters_not_ready"
    return {
        "ready": code is None,
        "code": code,
        "names": sorted(names),
        "missing": missing,
        "pending": sorted(pending),
        "count": len(names),
        "names_waived": names_waived,
    }


def post_activation_names_evidence(*, journal: Any, readiness: Any, waiver: Any, now: Any) -> Dict[str, Any]:
    """Return the name-check evidence candidate from a ready decision, and whether it changed.

    A ready decision under no waiver records post_activation.names_verified_at once;
    under a waiver covering post-activation it journals that waiver instead and never
    a verification time. Recorded evidence is kept, never rewritten.
    """
    journal = require_completed_restore(journal)
    if not isinstance(readiness, dict) or not isinstance(readiness.get("names_waived"), bool):
        raise MigrationEvidenceError("malformed_readiness", "the readiness decision is malformed")
    if readiness.get("ready") is not True:
        raise MigrationEvidenceError(
            "managed_clusters_not_ready", f"the ManagedClusters are not ready ({readiness.get('code')})"
        )
    if readiness["names_waived"] != (waiver is not None):
        raise MigrationEvidenceError(
            "readiness_waiver_mismatch", "the readiness decision disagrees with the post-activation waiver"
        )
    candidate = copy.deepcopy(journal)
    if waiver is not None:
        if not isinstance(waiver, dict) or waiver.get("scope") not in _COVERING:
            raise MigrationEvidenceError("waiver_scope_mismatch", "the waiver does not cover post_activation")
        candidate["waiver"] = copy.deepcopy(waiver)
    else:
        post = candidate["post_activation"]
        post["names_verified_at"] = post["names_verified_at"] or now
    return _transition(journal, candidate)


def post_activation_completion(*, journal: Any, now: Any) -> Dict[str, Any]:
    """Return the post_activation.completed_at candidate, the last post-activation evidence write.

    Callers build it only after every required post-activation operation succeeded.
    It needs the name check or a journaled waiver covering post-activation, and a
    recorded completion time is kept, never rewritten.
    """
    journal = require_completed_restore(journal)
    candidate = copy.deepcopy(journal)
    post = candidate["post_activation"]
    post["completed_at"] = post["completed_at"] or now
    return _transition(journal, candidate)


def _transition(journal: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {"candidate": validate_journal_transition(journal, candidate), "write": candidate != journal}


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)
