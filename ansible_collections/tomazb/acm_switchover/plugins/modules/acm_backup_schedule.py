"""BackupSchedule operation planning module for switchover roles."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def backup_schedule_pause_mode(acm_version: str) -> str:
    major, minor, *_rest = [int(part) for part in acm_version.split(".")]
    return "delete" if (major, minor) <= (2, 11) else "pause"


def build_backup_schedule_operation(acm_version: str, intent: str, schedules: list[dict]) -> dict:
    mode = backup_schedule_pause_mode(acm_version)
    if intent == "pause" and mode == "delete":
        return {"action": "delete", "mode": mode}
    if intent == "pause":
        return {"action": "patch", "mode": mode, "patch": {"spec": {"paused": True}}}
    return {"action": "patch", "mode": mode, "patch": {"spec": {"paused": False}}}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "acm_version": {"type": "str", "required": True},
            "intent": {"type": "str", "required": True},
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
