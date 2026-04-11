# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_restore_info
short_description: Select and prepare restore activation data
description:
  - Selects the best restore resource from a list and builds the activation patch
    required to trigger managed cluster import on the new hub. No API calls are made.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  restores:
    description: List of Restore resource dicts from the Kubernetes API.
    type: list
    elements: dict
    default: []
  backup_name:
    description: Backup name to embed in the activation patch. Use C(latest) to pick the newest backup.
    type: str
    default: null
"""

EXAMPLES = r"""
- name: Build activation patch plan
  tomazb.acm_switchover.acm_restore_info:
    restores: "{{ acm_secondary_restores_info.resources | default([]) }}"
    backup_name: latest
  register: restore_plan

- name: Debug restore patch
  ansible.builtin.debug:
    msg: "{{ restore_plan.patch }}"
"""

from ansible.module_utils.basic import AnsibleModule


def select_passive_sync_restore(restores: list[dict]) -> tuple[dict | None, dict]:
    """Select the best passive sync restore and return diagnostics.

    Returns:
        Tuple of (selected_restore, diagnostics_dict)
        diagnostics_dict contains: restore_count, sync_enabled_count, reason
    """
    total_count = len(restores)
    candidates = [item for item in restores if item.get("spec", {}).get("syncRestoreWithNewBackups") is True]
    sync_enabled_count = len(candidates)

    diagnostics = {
        "restore_count": total_count,
        "sync_enabled_count": sync_enabled_count,
    }

    if not restores:
        diagnostics["reason"] = "no_restores_found"
        return None, diagnostics

    if not candidates:
        diagnostics["reason"] = "no_sync_restore"
        return None, diagnostics

    candidates.sort(key=lambda item: item.get("metadata", {}).get("creationTimestamp") or "", reverse=True)
    return candidates[0], diagnostics


def build_activation_patch(backup_name: str) -> dict:
    return {"spec": {"veleroManagedClustersBackupName": backup_name}}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "restores": {"type": "list", "elements": "dict", "default": []},
            "backup_name": {"type": "str", "required": False, "default": None},
        },
        supports_check_mode=True,
    )
    selected, diagnostics = select_passive_sync_restore(module.params["restores"])
    patch = None
    if module.params["backup_name"]:
        patch = build_activation_patch(module.params["backup_name"])
    module.exit_json(
        changed=False,
        restore=selected,
        patch=patch,
        restore_count=diagnostics["restore_count"],
        sync_enabled_count=diagnostics["sync_enabled_count"],
        reason=diagnostics.get("reason"),
    )


if __name__ == "__main__":
    main()
