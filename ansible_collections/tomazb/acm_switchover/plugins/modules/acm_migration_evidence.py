# SPDX-License-Identifier: MIT
"""Decide one step of the R4-04 activation migration-evidence transaction."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_migration_evidence
short_description: Decide one step of the activation migration-evidence transaction
version_added: "1.8.0"
description:
  - A thin adapter over C(module_utils/migration_activation.py), the collection mirror of
    the Python CLI's activation transaction. It performs no Kubernetes request and no
    write. The role passes the sanitized results of its strict C(acm_k8s_read_outcome)
    reads and C(acm_restore_guarded_mutation) requests in C(inputs).
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
      - create_body
      - deletion_poll
      - freeze
      - frozen_backups
      - journal_matches
      - passive_restore
      - post_create
      - post_delete
      - post_patch
      - pre_create
      - pre_delete
      - pre_patch
      - reconcile
      - status_subresource
      - verify_passive_sync
  inputs:
    description:
      - The step's keyword inputs, as the matching C(migration_activation) function takes
        them. Read results are C(acm_k8s_read_outcome) results; C(journal) is the validated
        journal published by C(checkpoint_phase) or the last persisted C(candidate).
      - C(freeze) generates its C(run_id) and C(resolved_at) unless they are supplied.
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
  returned: for freeze, post_patch and post_create
  type: dict
"""

import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Callable, Dict  # noqa: E402

from ansible.module_utils.basic import AnsibleModule  # noqa: E402

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import (  # noqa: E402
    migration_activation as transaction,
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
}


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
    except (KeyError, TypeError):
        module.fail_json(msg=f"The {module.params['operation']} step received incomplete inputs.", changed=False)
        return
    module.exit_json(changed=False, **decision)


if __name__ == "__main__":
    main()
