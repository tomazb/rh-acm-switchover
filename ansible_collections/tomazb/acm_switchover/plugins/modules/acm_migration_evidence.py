# SPDX-License-Identifier: MIT
"""Decide one step of the R4-04 activation, post-activation or Restore-cleanup migration-evidence transaction."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_migration_evidence
short_description: Decide one step of the activation, post-activation or Restore-cleanup migration-evidence transaction
version_added: "1.8.0"
description:
  - A thin adapter over C(module_utils/migration_activation.py),
    C(module_utils/migration_post_activation.py) and C(module_utils/migration_cleanup.py),
    the collection mirrors of the Python CLI's activation transaction, post-activation
    evidence and durable Restore-cleanup transaction. It performs no Kubernetes
    request and no write. The role passes the sanitized results of its strict
    C(acm_k8s_read_outcome) reads and C(acm_restore_guarded_mutation) requests in C(inputs).
  - A step that produces a migration journal transition returns the complete validated
    C(candidate); the role persists it only through C(checkpoint_phase) C(status=update).
  - A refusal fails the task with the operator message the Python CLI raises for the same
    decision, except C(expected_managed_clusters), which returns it as a decision. Always
    reports C(changed=false).
options:
  operation:
    description: The transaction step to decide.
    type: str
    required: true
    choices:
      - acm_minor
      - activation_waiver
      - cleanup_absence_poll
      - cleanup_delete_outcome
      - cleanup_final_absence
      - cleanup_generic_restores
      - cleanup_intent
      - cleanup_locator_absent
      - cleanup_poll_read_timeout
      - cleanup_pre_delete
      - cleanup_repair
      - cleanup_schedule
      - cleanup_step
      - complete
      - completion_bundle
      - completion_poll
      - create_body
      - deletion_poll
      - expected_managed_clusters
      - freeze
      - frozen_backups
      - journal_matches
      - managed_cluster_readiness
      - managed_clusters_poll
      - old_hub_evidence
      - passive_restore
      - post_activation_completion
      - post_activation_names_evidence
      - post_activation_waiver
      - post_create
      - post_delete
      - post_patch
      - pre_create
      - pre_delete
      - pre_patch
      - reconcile
      - require_completed_restore
      - require_post_activation_completed
      - revalidate_completed
      - status_subresource
      - teardown_revalidated
      - verify_managed_clusters
      - verify_passive_sync
  inputs:
    description:
      - The step's keyword inputs, as the matching C(migration_activation),
        C(migration_post_activation) or C(migration_cleanup) function takes them. Read results are
        C(acm_k8s_read_outcome) results; C(journal) is the validated journal published by
        C(checkpoint_phase) or the last persisted C(candidate).
      - C(freeze) generates its C(run_id) and C(resolved_at), and C(activation_waiver),
        C(completion_bundle), C(complete), C(post_activation_waiver),
        C(post_activation_names_evidence) and C(post_activation_completion) their timestamp,
        unless they are supplied.
      - C(managed_cluster_readiness) takes C(mc_read) instead of the inventory items, and
        C(post_activation_waiver) journals the collection's waiver variable as the C(flag).
      - C(expected_managed_clusters) takes the strict primary ManagedCluster read as C(mc_read)
        and, unlike the other steps, returns a refused derivation as a decision
        (C(derivable=false)) instead of failing, so preflight records it as a critical result.
      - The cleanup steps and the old-hub evidence gate C(old_hub_evidence) take the strict read of
        the journaled Restore locator as C(live_read) instead of the object, C(cleanup_generic_restores)
        the strict Restore list as
        C(restores_read); C(cleanup_intent) generates its C(operation_id), and
        C(teardown_revalidated), C(cleanup_intent) and the cleanup steps that can record a
        transition their timestamp, unless supplied.
      - C(cleanup_absence_poll) and C(cleanup_final_absence) take the poll C(deadline) in epoch
        seconds and refuse once it has passed; C(cleanup_poll_read_timeout) returns the whole
        seconds a poll read may take before that C(deadline), or null when under one second.
    type: dict
    default: {}
author:
  - ACM Switchover Contributors (@tomazb)
"""

EXAMPLES = r"""
- name: Freeze the migration journal before any Restore mutation
  tomazb.acm_switchover.acm_migration_evidence:
    operation: freeze
    inputs:
      kind: passive_patch
      acm_minor: "2.14"
      restores_read: "{{ _acm_restores_read }}"
      live_read: "{{ _acm_passive_read }}"
      inventory_read: "{{ _acm_backups_read }}"
  register: _acm_freeze

- name: Persist the frozen journal
  tomazb.acm_switchover.checkpoint_phase:
    phase: activation
    checkpoint: "{{ acm_switchover_execution.checkpoint }}"
    status: update
    operational_data:
      migration_backups: "{{ _acm_freeze.candidate }}"
"""

RETURN = r"""
changed:
  description: Always false; this module decides and never mutates.
  returned: always
  type: bool
candidate:
  description: The complete validated journal transition to persist.
  returned: >-
    for freeze, post_patch, post_create, completion_bundle, complete,
    post_activation_names_evidence, post_activation_completion, teardown_revalidated,
    cleanup_intent, cleanup_final_absence and cleanup_repair; for cleanup_pre_delete,
    cleanup_delete_outcome and cleanup_absence_poll when they record recovery or an accepted DELETE
  type: dict
write:
  description: Whether the C(candidate) differs from the journal it was built from.
  returned: >-
    for post_activation_names_evidence, post_activation_completion and teardown_revalidated
  type: bool
derivable:
  description: Whether a positively complete primary inventory derived the expectation.
  returned: for expected_managed_clusters
  type: bool
code:
  description: The stable refusal code of a refused derivation, otherwise null.
  returned: for expected_managed_clusters
  type: str
message:
  description: The sanitized preflight result message for the derivation.
  returned: for expected_managed_clusters
  type: str
names:
  description: The sorted expected non-local ManagedCluster names, or null when refused.
  returned: for expected_managed_clusters
  type: list
  elements: str
count:
  description: The number of expected names, or null when refused.
  returned: for expected_managed_clusters
  type: int
"""

import time  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Callable, Dict  # noqa: E402

from ansible.module_utils.basic import AnsibleModule  # noqa: E402

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import (  # noqa: E402
    migration_activation as transaction,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_cleanup as cleanup  # noqa: E402
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import (  # noqa: E402
    migration_post_activation as post_activation,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (  # noqa: E402
    MigrationEvidenceError,
)


def _checked(requirement: Callable[..., None], *args, **kwargs) -> dict:
    """A requirement step: it refuses or it passes with nothing to return."""
    requirement(*args, **kwargs)
    return {}


def _expected_managed_clusters(inputs: dict) -> dict:
    """The preflight derivation as a decision the role records as a critical result (#340 E1).

    A refusal is returned, not raised, so preflight reports it like every other validation
    finding (PreflightValidator._derive_expected_managed_cluster_names); it is never zero names.
    """
    mc_read = inputs.get("mc_read")
    status = mc_read.get("read_status") if isinstance(mc_read, dict) else None
    try:
        if status != "ok":
            raise MigrationEvidenceError(
                "inventory_unreadable",
                f"the primary ManagedCluster inventory is not positively complete ({status or 'no read'})",
            )
        names = post_activation.expected_managed_cluster_names(mc_read.get("resources"))
    except MigrationEvidenceError as exc:
        return {
            "derivable": False,
            "code": exc.code,
            "message": f"Cannot derive the expected ManagedClusters from the primary hub ({exc})",
            "names": None,
            "count": None,
        }
    return {
        "derivable": True,
        "code": None,
        "message": f"primary hub inventory expects {len(names)} non-local ManagedCluster(s)",
        "names": names,
        "count": len(names),
    }


_OPERATIONS: Dict[str, Callable[[dict], dict]] = {
    "acm_minor": lambda inputs: transaction.acm_minor(inputs["mch_read"]),
    "status_subresource": lambda inputs: _checked(transaction.require_status_subresource, inputs["discovery_read"]),
    "passive_restore": lambda inputs: {"restore": transaction.find_passive_sync_restore(inputs["restores_read"])},
    "verify_passive_sync": lambda inputs: transaction.verify_passive_sync(inputs["restores_read"], inputs["live_read"]),
    "freeze": lambda inputs: transaction.freeze(**_with_identity(inputs)),
    "journal_matches": lambda inputs: _checked(
        transaction.require_journal_matches_run, inputs["journal"], inputs["kind"], inputs["acm_minor"]
    ),
    "frozen_backups": lambda inputs: {"backups": transaction.frozen_backup_names(inputs["journal"])},
    "pre_patch": lambda inputs: transaction.pre_patch(**inputs),
    "post_patch": lambda inputs: transaction.post_patch(**inputs),
    "pre_create": lambda inputs: transaction.pre_create(**inputs),
    "pre_delete": lambda inputs: transaction.pre_delete(**inputs),
    "post_delete": lambda inputs: _checked(transaction.post_delete, **inputs),
    "deletion_poll": lambda inputs: transaction.deletion_poll(**inputs),
    "create_body": lambda inputs: transaction.create_body(**inputs),
    "post_create": lambda inputs: transaction.post_create(**inputs),
    "reconcile": lambda inputs: transaction.reconcile(**inputs),
    "completion_poll": lambda inputs: transaction.completion_poll(**inputs),
    "activation_waiver": lambda inputs: transaction.activation_waiver(**_with_time(inputs, "journaled_at")),
    "managed_clusters_poll": lambda inputs: transaction.managed_clusters_poll(**inputs),
    "verify_managed_clusters": lambda inputs: transaction.verify_managed_clusters(**inputs),
    "completion_bundle": lambda inputs: transaction.completion_bundle(**_with_time(inputs, "now")),
    "complete": lambda inputs: transaction.complete(**_with_time(inputs, "now")),
    "revalidate_completed": lambda inputs: transaction.revalidate_completed(**inputs),
    "require_completed_restore": lambda inputs: {
        "journal": post_activation.require_completed_restore(inputs.get("journal"))
    },
    "require_post_activation_completed": lambda inputs: {
        "journal": post_activation.require_post_activation_completed(inputs.get("journal"))
    },
    "post_activation_waiver": lambda inputs: post_activation.post_activation_waiver(
        flag=transaction.WAIVER_FLAG, **_with_time(_with_default(inputs, "requested"), "journaled_at")
    ),
    "expected_managed_clusters": _expected_managed_clusters,
    "managed_cluster_readiness": lambda inputs: post_activation.managed_cluster_readiness(
        items=transaction.strict_list(inputs["mc_read"], "ManagedCluster inventory"),
        expected_names=inputs["expected_names"],
        min_managed_clusters=_native(inputs["min_managed_clusters"]),
        names_waived=_native(inputs["names_waived"]),
    ),
    "post_activation_names_evidence": lambda inputs: post_activation.post_activation_names_evidence(
        **_with_time(_with_default(inputs, "waiver"), "now")
    ),
    "post_activation_completion": lambda inputs: post_activation.post_activation_completion(
        **_with_time(inputs, "now")
    ),
    "cleanup_schedule": lambda inputs: cleanup.cleanup_schedule(),
    "cleanup_step": lambda inputs: cleanup.cleanup_step(
        journal=inputs.get("journal"), repair_requested=_native(inputs["repair_requested"])
    ),
    "teardown_revalidated": lambda inputs: cleanup.teardown_revalidated(**_with_time(inputs, "now")),
    "cleanup_intent": lambda inputs: cleanup.cleanup_intent(**_with_time(_with_operation_id(inputs), "now")),
    "cleanup_pre_delete": lambda inputs: cleanup.pre_delete(**_with_time(_with_locator(inputs), "now")),
    "cleanup_delete_outcome": lambda inputs: cleanup.delete_outcome(
        **_with_time(dict(inputs, attempt=_native(inputs["attempt"])), "now")
    ),
    "cleanup_absence_poll": lambda inputs: cleanup.absence_poll(
        **_with_time(_with_locator(_within_deadline(inputs)), "now")
    ),
    "cleanup_final_absence": lambda inputs: cleanup.final_absence(
        **_with_time(_with_locator(_within_deadline(inputs)), "now")
    ),
    "cleanup_poll_read_timeout": lambda inputs: {
        "request_timeout": cleanup.poll_read_timeout(remaining=float(inputs["deadline"]) - time.time())
    },
    "cleanup_repair": lambda inputs: cleanup.repair_cleanup(**_with_time(_with_locator(inputs), "now")),
    "cleanup_locator_absent": lambda inputs: cleanup.require_locator_absent(**_with_locator(inputs)),
    "old_hub_evidence": lambda inputs: cleanup.require_old_hub_evidence(
        **_with_default(_with_locator(inputs), "journal")
    ),
    "cleanup_generic_restores": lambda inputs: cleanup.generic_cleanup_restores(
        journal=inputs.get("journal"),
        restores=transaction.strict_list(inputs["restores_read"], "Restore inventory"),
    ),
}


def _with_locator(inputs: dict) -> dict:
    """The strict read of the journaled locator as its object, None for a proven absence.

    Unreadable is never absent: any other read outcome refuses with the state unchanged.
    """
    resolved = dict(inputs)
    journal = resolved.get("journal")
    label = f"Restore {journal['restore']['name']}" if isinstance(journal, dict) else "The journaled Restore"
    resolved["live"] = transaction.strict_get(resolved.pop("live_read"), label)
    return resolved


def _within_deadline(inputs: dict) -> dict:
    """A poll decision re-checks the elapsed deadline (epoch seconds) itself, at classification."""
    resolved = dict(inputs)
    deadline = resolved.pop("deadline", None)
    if deadline is not None:
        cleanup.require_within_deadline(remaining=float(deadline) - time.time())
    return resolved


def _with_operation_id(inputs: dict) -> dict:
    """The immutable cleanup operation ID, minted once like the Python CLI does at intent."""
    resolved = dict(inputs)
    resolved.setdefault("operation_id", str(uuid.uuid4()))
    return resolved


def _with_default(inputs: dict, field: str) -> dict:
    """An optional input the role omits when it has none."""
    resolved = dict(inputs)
    resolved.setdefault(field, None)
    return resolved


def _native(value):
    """A scalar the non-native templating of the ansible-core floor rendered as text."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if value in ("True", "False"):
        return value == "True"
    return value


def _with_time(inputs: dict, field: str) -> dict:
    """The decision instant, taken now unless supplied, as the Python CLI timestamps its writes."""
    resolved = dict(inputs)
    resolved.setdefault(field, datetime.now(timezone.utc).isoformat())
    return resolved


def _with_identity(inputs: dict) -> dict:
    """A fresh transaction identity, exactly as the Python CLI mints one at the freeze."""
    resolved = dict(inputs)
    resolved.setdefault("run_id", str(uuid.uuid4()))
    resolved.setdefault("resolved_at", datetime.now(timezone.utc).isoformat())
    return resolved


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "operation": {"type": "str", "required": True, "choices": sorted(_OPERATIONS)},
            "inputs": {"type": "dict", "default": {}},
        },
        supports_check_mode=True,
    )
    try:
        decision = _OPERATIONS[module.params["operation"]](module.params["inputs"])
    except transaction.ActivationRefused as exc:
        module.fail_json(msg=str(exc), changed=False)
        return
    except MigrationEvidenceError as exc:
        module.fail_json(msg=f"Migration evidence refused ({exc})", changed=False)
        return
    except (KeyError, TypeError, ValueError):
        module.fail_json(msg=f"The {module.params['operation']} step received incomplete inputs.", changed=False)
        return
    module.exit_json(changed=False, **decision)


if __name__ == "__main__":
    main()
