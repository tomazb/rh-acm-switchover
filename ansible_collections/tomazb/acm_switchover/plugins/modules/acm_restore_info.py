# SPDX-License-Identifier: MIT

from __future__ import annotations

import re

DOCUMENTATION = r"""
---
module: acm_restore_info
short_description: Analyze the passive sync restore for preflight
description:
  - Selects the passive sync restore from a list and reports whether it is
    activation-ready. No API calls are made.
  - Activation does not plan from this analysis: the R4-04 activation transaction
    discovers, mutates and proves Restores through C(acm_migration_evidence).
author:
  - ACM Switchover Contributors (@tomazb)
options:
  restores:
    description: List of Restore resource dicts from the Kubernetes API.
    type: list
    elements: dict
    default: []
  allow_conventional_name_fallback:
    description:
      - Allow selecting C(restore-acm-passive-sync) when C(syncRestoreWithNewBackups) is omitted.
      - This is a compatibility fallback for older Restore objects and must be explicitly enabled.
    type: bool
    default: false
"""

EXAMPLES = r"""
- name: Analyze passive sync restores on the secondary hub
  tomazb.acm_switchover.acm_restore_info:
    restores: "{{ acm_secondary_restore_info.resources | default([]) }}"
  register: passive_restore_analysis
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (  # noqa: E402
    BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN,
    PASSIVE_RESTORE_CONVENTIONAL_NAME_FALLBACK_REASON,
    PASSIVE_SYNC_RESTORE_NAME,
    WAIT_FAILURE_PHASES,
)

PASSIVE_PREFLIGHT_READY_PHASES = ("Enabled", "Finished", "Completed", "Running")
BENIGN_ALREADY_AVAILABLE_MESSAGE = re.compile(BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN)


def is_benign_already_available_message(message: object) -> bool:
    return isinstance(message, str) and BENIGN_ALREADY_AVAILABLE_MESSAGE.fullmatch(message) is not None


def restore_messages_are_benign_already_available(messages: object) -> bool:
    return (
        isinstance(messages, list) and bool(messages) and all(is_benign_already_available_message(m) for m in messages)
    )


def select_passive_sync_restore(
    restores: list[dict],
    allow_conventional_name_fallback: bool = False,
) -> tuple[dict | None, dict[str, object]]:
    """Select the best passive sync restore and return diagnostics.

    Returns:
        Tuple of (selected_restore, diagnostics_dict)
        diagnostics_dict contains: restore_count, sync_enabled_count, reason
    """
    total_count = len(restores)
    candidates = [item for item in restores if item.get("spec", {}).get("syncRestoreWithNewBackups") is True]
    sync_enabled_count = len(candidates)

    diagnostics: dict[str, object] = {
        "restore_count": total_count,
        "sync_enabled_count": sync_enabled_count,
    }

    if not restores:
        diagnostics["reason"] = "no_restores_found"
        return None, diagnostics

    if candidates:
        candidates.sort(
            key=lambda item: item.get("metadata", {}).get("creationTimestamp") or "",
            reverse=True,
        )
        return candidates[0], diagnostics

    if allow_conventional_name_fallback:
        conventional_restore = next(
            (
                item
                for item in restores
                if item.get("metadata", {}).get("name") == PASSIVE_SYNC_RESTORE_NAME
                and item.get("spec", {}).get("syncRestoreWithNewBackups") is not False
            ),
            None,
        )
        if conventional_restore is not None:
            diagnostics["reason"] = PASSIVE_RESTORE_CONVENTIONAL_NAME_FALLBACK_REASON
            return conventional_restore, diagnostics

    diagnostics["reason"] = "no_sync_restore"
    return None, diagnostics


def passive_restore_phase(restore: dict | None) -> str | None:
    if restore is None:
        return None
    return restore.get("status", {}).get("phase", "Unknown")


def passive_restore_ready_for_preflight(restore: dict | None) -> bool:
    phase = passive_restore_phase(restore)
    if phase in PASSIVE_PREFLIGHT_READY_PHASES:
        return True
    if phase == "FinishedWithErrors":
        messages = (restore or {}).get("status", {}).get("messages", [])
        return restore_messages_are_benign_already_available(messages)
    return False


def passive_restore_ready_reason(restore: dict | None) -> str:
    phase = passive_restore_phase(restore)
    if phase is None:
        return "No passive Restore resource was selected."
    if phase in PASSIVE_PREFLIGHT_READY_PHASES:
        return f"Passive Restore phase {phase} is ready."
    if phase == "FinishedWithErrors":
        messages = (restore or {}).get("status", {}).get("messages", [])
        if restore_messages_are_benign_already_available(messages):
            return "Passive Restore FinishedWithErrors only contains " "already-available messages."
        return "Passive Restore FinishedWithErrors contains non-benign errors."
    if phase in WAIT_FAILURE_PHASES:
        return f"Passive Restore phase {phase} failed."
    return f"Passive Restore phase {phase} is not activation-ready."


def analyze_passive_restores(restores: list[dict], allow_conventional_name_fallback: bool = False) -> dict:
    """The selected passive sync restore and its activation readiness."""
    passive_restore, diagnostics = select_passive_sync_restore(
        restores,
        allow_conventional_name_fallback=allow_conventional_name_fallback,
    )
    return {
        "changed": False,
        "restore": passive_restore,
        "restore_phase": passive_restore_phase(passive_restore),
        "restore_ready": passive_restore_ready_for_preflight(passive_restore),
        "restore_ready_reason": passive_restore_ready_reason(passive_restore),
        "restore_count": diagnostics["restore_count"],
        "sync_enabled_count": diagnostics["sync_enabled_count"],
        "reason": diagnostics.get("reason"),
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "restores": {"type": "list", "elements": "dict", "default": []},
            "allow_conventional_name_fallback": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    module.exit_json(
        **analyze_passive_restores(
            module.params["restores"],
            allow_conventional_name_fallback=module.params["allow_conventional_name_fallback"],
        )
    )


if __name__ == "__main__":
    main()
