# SPDX-License-Identifier: MIT
"""Decide one step of the R4-04 activation or post-activation migration-evidence transaction."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_migration_evidence
short_description: Decide one step of the activation or post-activation migration-evidence transaction
version_added: "1.8.0"
description:
  - A thin adapter over C(module_utils/migration_activation.py) and
    C(module_utils/migration_post_activation.py), the collection mirrors of the Python
    CLI's activation transaction and post-activation evidence. It performs no Kubernetes
    request and no write. The role passes the sanitized results of its strict
    C(acm_k8s_read_outcome) reads and C(acm_restore_guarded_mutation) requests in C(inputs).
  - A step that produces a migration journal transition returns the complete validated
    C(candidate); the role persists it only through C(checkpoint_phase) C(status=update).
  - A refusal fails the task with the operator message the Python CLI raises for the same
    decision. Always reports C(changed=false).
options:
  operation:
    description: The transaction step to decide.
    type: str
    required: true
    choices:
      - acm_minor
      - activation_waiver
      - complete
      - completion_bundle
      - completion_poll
      - create_body
      - deletion_poll
      - freeze
      - frozen_backups
      - journal_matches
      - managed_cluster_readiness
      - managed_clusters_poll
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
      - verify_managed_clusters
      - verify_passive_sync
  inputs:
    description:
      - The step's keyword inputs, as the matching C(migration_activation) or
        C(migration_post_activation) function takes them. Read results are
        C(acm_k8s_read_outcome) results; C(journal) is the validated journal published by
        C(checkpoint_phase) or the last persisted C(candidate).
      - C(freeze) generates its C(run_id) and C(resolved_at), and C(activation_waiver),
        C(completion_bundle), C(complete), C(post_activation_waiver),
        C(post_activation_names_evidence) and C(post_activation_completion) their timestamp,
        unless they are supplied.
      - C(managed_cluster_readiness) takes C(mc_read) instead of the inventory items, and
        C(post_activation_waiver) journals the collection's waiver variable as the C(flag).
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
    post_activation_names_evidence and post_activation_completion
  type: dict
write:
  description: Whether the post-activation C(candidate) differs from the journal it was built from.
  returned: for post_activation_names_evidence and post_activation_completion
  type: bool
"""

import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Callable, Dict  # noqa: E402

from ansible.module_utils.basic import AnsibleModule  # noqa: E402

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import (  # noqa: E402
    migration_activation as transaction,
)
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
}


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
    except (KeyError, TypeError):
        module.fail_json(msg=f"The {module.params['operation']} step received incomplete inputs.", changed=False)
        return
    module.exit_json(changed=False, **decision)


if __name__ == "__main__":
    main()
