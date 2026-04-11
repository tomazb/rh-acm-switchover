# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_checkpoint
short_description: Build and query switchover checkpoint records
description:
  - Creates a structured checkpoint record for a given switchover phase and
    determines whether a phase still needs to run based on a prior checkpoint.
    No API calls are made; callers persist and reload the checkpoint dict.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  phase:
    description: Switchover phase name (e.g. C(activation), C(preflight)).
    required: true
    type: str
  operational_data:
    description: Arbitrary phase-specific metadata to embed in the checkpoint.
    type: dict
    default: {}
"""

EXAMPLES = r"""
- name: Create checkpoint record for activation phase
  tomazb.acm_switchover.acm_checkpoint:
    phase: activation
    operational_data:
      method: passive
  register: cp

- name: Show checkpoint
  ansible.builtin.debug:
    msg: "{{ cp.checkpoint }}"
"""

RETURN = r"""
checkpoint:
  description: The checkpoint record dict.
  returned: always
  type: dict
  contains:
    schema_version:
      description: Record schema version.
      type: str
      sample: "1.0"
    phase:
      description: Phase this checkpoint was created for.
      type: str
    completed_phases:
      description: List of phase names that have been completed.
      type: list
    operational_data:
      description: Phase-specific metadata supplied by the caller.
      type: dict
    errors:
      description: List of error strings recorded during the phase.
      type: list
    report_refs:
      description: List of report reference strings attached to this checkpoint.
      type: list
    created_at:
      description: ISO-8601 UTC timestamp when the record was created.
      type: str
    updated_at:
      description: ISO-8601 UTC timestamp of the last update.
      type: str
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    build_checkpoint_record,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "phase": {"type": "str", "required": True},
            "operational_data": {"type": "dict", "default": {}},
        },
        supports_check_mode=True,
    )
    module.exit_json(
        changed=False,
        checkpoint=build_checkpoint_record(
            module.params["phase"],
            module.params["operational_data"],
        ),
    )


if __name__ == "__main__":
    main()
