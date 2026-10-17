# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_backup_continuity
short_description: Prove current creation-bound ACM Backup continuity

description:
  - Reads fresh complete Backup inventory and current selected-object health through
    the strict Kubernetes read owner. Never modifies the checkpoint or cluster.
  - Historical facts supply only the raw enable boundary. Caller-supplied inventory
    and prior proof results cannot authorize success.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  inputs:
    description: Raw Backup boundary presence and value read from the checkpoint.
    type: dict
    required: true
  timeout:
    description: Maximum seconds for selection and clean completion.
    type: float
    default: 600
  poll_interval:
    description: Seconds between bounded current-evidence polls.
    type: float
    default: 30
extends_documentation_fragment:
  - kubernetes.core.k8s_auth_options
"""
EXAMPLES = r"""
- name: Prove current Backup continuity
  tomazb.acm_switchover.acm_backup_continuity:
    inputs: "{{ acm_switchover_backup_inputs }}"
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
"""
RETURN = r"""
changed:
  description: Always false.
  type: bool
  returned: always
proof:
  description: Current name, UID, creation timestamp and Completed phase; not persisted.
  type: dict
  returned: success
"""
from copy import deepcopy
from datetime import datetime, timezone

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.kubernetes.core.plugins.module_utils.args_common import AUTH_ARG_SPEC
from ansible_collections.kubernetes.core.plugins.module_utils.k8s.client import get_api_client

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.backup_continuity import (
    BackupContinuityError,
    resolve_enable_boundary,
    verify_backup_continuity,
)


def run_module(module):
    try:
        boundary = resolve_enable_boundary(module.params["inputs"], observed_at=datetime.now(timezone.utc))
        if module.params["timeout"] <= 0 or module.params["poll_interval"] < 0:
            raise BackupContinuityError("invalid_poll_bound")
        try:
            client = get_api_client(**module.params)
        except Exception:
            raise BackupContinuityError("backup_inventory_unavailable") from None
        proof = verify_backup_continuity(client, enabled_at=boundary, timeout=module.params["timeout"], poll_interval=module.params["poll_interval"])
    except BackupContinuityError as exc:
        module.fail_json(changed=False, msg=str(exc), reason=exc.reason)
        return
    module.exit_json(changed=False, proof=proof)


def main():
    spec = deepcopy(AUTH_ARG_SPEC)
    spec.update(inputs={"type":"dict","required":True}, timeout={"type":"float","default":600}, poll_interval={"type":"float","default":30})
    run_module(AnsibleModule(argument_spec=spec, supports_check_mode=True))


if __name__ == "__main__":
    main()
