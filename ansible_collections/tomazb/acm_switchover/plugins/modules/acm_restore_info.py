"""Restore selection helpers for switchover activation roles."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def select_passive_sync_restore(restores: list[dict]) -> dict | None:
    candidates = [item for item in restores if item.get("spec", {}).get("syncRestoreWithNewBackups") is True]
    candidates.sort(key=lambda item: item.get("metadata", {}).get("creationTimestamp", ""), reverse=True)
    return candidates[0] if candidates else None


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
    selected = select_passive_sync_restore(module.params["restores"])
    patch = None
    if module.params["backup_name"]:
        patch = build_activation_patch(module.params["backup_name"])
    module.exit_json(changed=False, restore=selected, patch=patch)


if __name__ == "__main__":
    main()
