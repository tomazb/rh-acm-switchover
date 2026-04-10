# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_backup_schedule
short_description: Plan BackupSchedule pause or enable operations
description:
  - Computes the action required to pause or enable an ACM BackupSchedule without
    making any API calls. Returns a structured operation dict that callers apply.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  acm_version:
    description: ACM version string (e.g. C(2.14.3)). Determines the pause mechanism.
    required: true
    type: str
  intent:
    description: Desired state. Use C(pause) during primary prep, C(enable) during finalization.
    required: true
    type: str
  schedules:
    description: List of BackupSchedule resource dicts from the Kubernetes API.
    type: list
    elements: dict
    default: []
"""

EXAMPLES = r"""
- name: Pause backup schedules on primary hub
  tomazb.acm_switchover.acm_backup_schedule:
    acm_version: "{{ acm_primary_mch_info.resources[0].status.currentVersion }}"
    intent: pause
    schedules: "{{ acm_primary_backup_schedules_info.resources | default([]) }}"
  register: pause_op

- name: Enable backup schedules on new hub
  tomazb.acm_switchover.acm_backup_schedule:
    acm_version: "{{ acm_secondary_mch_info.resources[0].status.currentVersion }}"
    intent: enable
    schedules: "{{ acm_secondary_backup_schedules_info.resources | default([]) }}"
  register: enable_op
"""

RETURN = r"""
changed:
  description: Whether any BackupSchedule resource was modified.
  returned: always
  type: bool
operation:
  description: Planned operation derived from intent and current schedule state.
  returned: always
  type: dict
  contains:
    action:
      description: >
        Action to perform. C(patch) to update spec, C(delete) to remove the resource
        (ACM <= 2.11 pause), C(none) when no change is required.
      type: str
      sample: patch
    mode:
      description: Pause mechanism determined by ACM version. Either C(pause) or C(delete).
      type: str
      sample: pause
    patch:
      description: Patch payload to apply. Present only when action is C(patch).
      type: dict
      returned: when action == 'patch'
      sample: {"spec": {"paused": true}}
"""

from ansible.module_utils.basic import AnsibleModule


def backup_schedule_pause_mode(acm_version: str) -> str:
    major, minor, *_rest = [int(part) for part in acm_version.split(".")]
    return "delete" if (major, minor) <= (2, 11) else "pause"


def build_backup_schedule_operation(acm_version: str, intent: str, schedules: list[dict]) -> dict:
    mode = backup_schedule_pause_mode(acm_version)
    if not schedules:
        return {"action": "none", "mode": mode}
    if intent == "pause":
        if mode == "delete":
            return {"action": "delete", "mode": mode}
        already_paused = all(s.get("spec", {}).get("paused", False) for s in schedules)
        if already_paused:
            return {"action": "none", "mode": mode}
        return {"action": "patch", "mode": mode, "patch": {"spec": {"paused": True}}}
    # intent == "enable"
    already_enabled = all(not s.get("spec", {}).get("paused", False) for s in schedules)
    if already_enabled:
        return {"action": "none", "mode": mode}
    return {"action": "patch", "mode": mode, "patch": {"spec": {"paused": False}}}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "acm_version": {"type": "str", "required": True},
            "intent": {"type": "str", "required": True, "choices": ["pause", "enable"]},
            "schedules": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )
    operation = build_backup_schedule_operation(
        module.params["acm_version"],
        module.params["intent"],
        module.params["schedules"],
    )
    module.exit_json(changed=operation["action"] != "none", operation=operation)


if __name__ == "__main__":
    main()
