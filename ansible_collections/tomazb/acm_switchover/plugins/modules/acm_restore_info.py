# SPDX-License-Identifier: MIT

from __future__ import annotations

from copy import deepcopy

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
  method:
    description:
      - Switchover method.
      - C(passive) activates an existing passive-sync restore or creates an activation restore.
      - C(full) creates a one-time full restore.
    type: str
    choices: [passive, full]
    default: passive
  activation_method:
    description:
      - Passive activation mode.
      - C(patch) patches the passive-sync restore in-place.
      - C(restore) deletes the passive-sync restore and creates C(restore-acm-activate).
    type: str
    choices: [patch, restore]
    default: patch
  backup_name:
    description: Backup name to embed in the activation patch. Use C(latest) to pick the newest backup.
    type: str
    default: null
"""

EXAMPLES = r"""
- name: Build activation patch plan
  tomazb.acm_switchover.acm_restore_info:
    method: passive
    activation_method: patch
    restores: "{{ acm_secondary_restores_info.resources | default([]) }}"
    backup_name: latest
  register: restore_plan

- name: Debug restore patch
  ansible.builtin.debug:
    msg: "{{ restore_plan.patch }}"
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ACTIVATION_RESTORE_NAME,
    BACKUP_NAMESPACE,
    CLEANUP_BEFORE_RESTORE_VALUE,
    FULL_RESTORE_NAME,
    PASSIVE_SYNC_RESTORE_NAME,
    VELERO_BACKUP_SKIP,
    WAIT_FAILURE_PHASES,
)


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


def select_restore_by_name(restores: list[dict], name: str) -> dict | None:
    for restore in restores:
        if restore.get("metadata", {}).get("name") == name:
            return restore
    return None


def build_restore_snapshot(restore: dict) -> dict:
    metadata = restore.get("metadata", {})
    snapshot_metadata = {
        "name": metadata.get("name", PASSIVE_SYNC_RESTORE_NAME),
        "namespace": metadata.get("namespace", BACKUP_NAMESPACE),
    }
    labels = metadata.get("labels")
    annotations = metadata.get("annotations")
    if labels:
        snapshot_metadata["labels"] = deepcopy(labels)
    if annotations:
        snapshot_metadata["annotations"] = deepcopy(annotations)

    return {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": snapshot_metadata,
        "spec": deepcopy(restore.get("spec", {})),
    }


def build_wait_target(
    name: str,
    success_phases: list[str],
    namespace: str = BACKUP_NAMESPACE,
    **extra: object,
) -> dict:
    target = {
        "name": name,
        "namespace": namespace,
        "success_phases": success_phases,
        "failure_phases": WAIT_FAILURE_PHASES,
    }
    target.update(extra)
    return target


def build_activation_restore_body(backup_name: str) -> dict:
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {
            "name": ACTIVATION_RESTORE_NAME,
            "namespace": BACKUP_NAMESPACE,
        },
        "spec": {
            "cleanupBeforeRestore": CLEANUP_BEFORE_RESTORE_VALUE,
            "veleroManagedClustersBackupName": backup_name,
            "veleroCredentialsBackupName": VELERO_BACKUP_SKIP,
            "veleroResourcesBackupName": VELERO_BACKUP_SKIP,
        },
    }


def build_full_restore_body(backup_name: str) -> dict:
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {
            "name": FULL_RESTORE_NAME,
            "namespace": BACKUP_NAMESPACE,
        },
        "spec": {
            "cleanupBeforeRestore": CLEANUP_BEFORE_RESTORE_VALUE,
            "veleroManagedClustersBackupName": backup_name,
            "veleroCredentialsBackupName": backup_name,
            "veleroResourcesBackupName": backup_name,
        },
    }


def build_restore_activation_plan(
    method: str,
    activation_method: str,
    restores: list[dict],
    backup_name: str | None,
) -> dict:
    passive_restore, diagnostics = select_passive_sync_restore(restores)
    activation_restore = select_restore_by_name(restores, ACTIVATION_RESTORE_NAME)
    full_restore = select_restore_by_name(restores, FULL_RESTORE_NAME)

    patch = build_activation_patch(backup_name) if backup_name else None
    operation: dict = {"action": "none"}
    wait_target = None
    restore = passive_restore

    if method == "passive":
        if activation_method == "restore":
            wait_target = build_wait_target(ACTIVATION_RESTORE_NAME, ["Finished", "Completed"])
            restore = activation_restore or passive_restore
            if activation_restore is None:
                create_restore = build_activation_restore_body(backup_name or "latest")
                if passive_restore is not None:
                    operation = {
                        "action": "delete_and_create",
                        "delete_restore": {
                            "name": passive_restore.get("metadata", {}).get("name", PASSIVE_SYNC_RESTORE_NAME),
                            "namespace": passive_restore.get("metadata", {}).get("namespace", BACKUP_NAMESPACE),
                        },
                        "create_restore": create_restore,
                        "rollback_restore": build_restore_snapshot(passive_restore),
                    }
                else:
                    operation = {
                        "action": "create",
                        "create_restore": create_restore,
                    }
        else:
            if passive_restore is not None:
                wait_target = build_wait_target(
                    passive_restore.get("metadata", {}).get("name", PASSIVE_SYNC_RESTORE_NAME),
                    ["Enabled", "Finished", "Completed"],
                    passive_restore.get("metadata", {}).get("namespace", BACKUP_NAMESPACE),
                    velero_restore_required=True,
                    velero_restore_status_field="veleroManagedClustersRestoreName",
                    velero_success_phases=["Completed"],
                    velero_failure_phases=["Failed", "PartiallyFailed"],
                )
                current_backup = passive_restore.get("spec", {}).get("veleroManagedClustersBackupName")
                if patch is not None and current_backup != backup_name:
                    operation = {
                        "action": "patch",
                        "patch": patch,
                    }
    else:
        wait_target = build_wait_target(FULL_RESTORE_NAME, ["Finished", "Completed"])
        restore = full_restore or passive_restore
        if full_restore is None:
            create_restore = build_full_restore_body(backup_name or "latest")
            if passive_restore is not None:
                operation = {
                    "action": "delete_and_create",
                    "delete_restore": {
                        "name": passive_restore.get("metadata", {}).get("name", PASSIVE_SYNC_RESTORE_NAME),
                        "namespace": passive_restore.get("metadata", {}).get("namespace", BACKUP_NAMESPACE),
                    },
                    "create_restore": create_restore,
                    "rollback_restore": build_restore_snapshot(passive_restore),
                }
            else:
                operation = {
                    "action": "create",
                    "create_restore": create_restore,
                }

    return {
        "changed": operation["action"] != "none",
        "restore": restore,
        "patch": patch if method == "passive" and activation_method == "patch" else None,
        "operation": operation,
        "wait_target": wait_target,
        "restore_count": diagnostics["restore_count"],
        "sync_enabled_count": diagnostics["sync_enabled_count"],
        "reason": diagnostics.get("reason"),
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "restores": {"type": "list", "elements": "dict", "default": []},
            "method": {"type": "str", "choices": ["passive", "full"], "default": "passive"},
            "activation_method": {"type": "str", "choices": ["patch", "restore"], "default": "patch"},
            "backup_name": {"type": "str", "required": False, "default": None},
        },
        supports_check_mode=True,
    )
    plan = build_restore_activation_plan(
        method=module.params["method"],
        activation_method=module.params["activation_method"],
        restores=module.params["restores"],
        backup_name=module.params["backup_name"],
    )
    module.exit_json(**plan)


if __name__ == "__main__":
    main()
