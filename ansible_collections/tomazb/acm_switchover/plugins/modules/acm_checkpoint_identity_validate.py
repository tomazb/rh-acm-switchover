# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_checkpoint_identity_validate
short_description: Validate a checkpoint against live hub identities
description:
  - Validates that a persisted checkpoint operation identity matches the live
    hub contexts and C(kube-system) namespace UIDs before standalone recovery
    playbooks mutate cluster resources.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  checkpoint:
    description: Persisted checkpoint record containing C(operation_identity).
    required: true
    type: dict
  hubs:
    description: Current hub connection dictionary.
    required: true
    type: dict
  operation:
    description: Current switchover operation dictionary.
    type: dict
    default: {}
  hub_identities:
    description: Live hub identity dictionary keyed by role.
    required: true
    type: dict
  collection_version:
    description: Optional collection version to include in identity comparison.
    type: str
    default: ""
"""

EXAMPLES = r"""
- name: Validate checkpoint identity before standalone Argo CD resume
  tomazb.acm_switchover.acm_checkpoint_identity_validate:
    checkpoint: "{{ _argocd_resume_checkpoint }}"
    hubs: "{{ acm_switchover_hubs }}"
    operation: "{{ acm_switchover_operation }}"
    hub_identities: "{{ _argocd_resume_hub_identities }}"
"""

RETURN = r"""
matched_mapping:
  description: The accepted hub mapping.
  returned: success
  type: str
  sample: normal
operation_identity:
  description: Normalized checkpoint operation identity.
  returned: success
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    CheckpointIdentityMismatch,
    build_operation_identity,
    normalize_operation_identity,
    validate_operation_identity,
)


def _is_nonempty(value: object) -> bool:
    return bool(str(value or "").strip())


def _has_hub(hubs: dict, role: str) -> bool:
    hub = hubs.get(role) or {}
    return _is_nonempty(hub.get("context"))


def _require_live_identity(hub_identities: dict, role: str) -> None:
    identity = hub_identities.get(role) or {}
    if not _is_nonempty(identity.get("context")):
        raise ValueError(f"Unable to determine {role} hub context for checkpoint identity validation.")
    if not _is_nonempty(identity.get("cluster_uid")):
        raise ValueError(
            f"Unable to determine {role} hub cluster identity from kube-system namespace UID. "
            "Refusing to continue because standalone resume cannot prove the same live cluster."
        )


def _roles_to_validate(checkpoint_identity: dict, hubs: dict) -> list[str]:
    roles = ["secondary"]
    if _has_hub(hubs, "primary") or checkpoint_identity.get("primary_context"):
        roles.insert(0, "primary")
    return roles


def _swapped_hubs(hubs: dict) -> dict:
    return {
        **hubs,
        "primary": hubs.get("secondary") or {},
        "secondary": hubs.get("primary") or {},
    }


def _swapped_identities(hub_identities: dict) -> dict:
    return {
        **hub_identities,
        "primary": hub_identities.get("secondary") or {},
        "secondary": hub_identities.get("primary") or {},
    }


def _operation_for_validation(checkpoint_identity: dict, operation: dict) -> dict:
    defaults = {
        "method": checkpoint_identity.get("method"),
        "activation_method": checkpoint_identity.get("activation_method"),
        "restore_only": checkpoint_identity.get("restore_only"),
        "old_hub_action": checkpoint_identity.get("old_hub_action"),
    }
    return {**defaults, **(operation or {})}


def validate_checkpoint_identity(
    *,
    checkpoint: dict,
    hubs: dict,
    operation: dict,
    hub_identities: dict,
    collection_version: str | None = None,
) -> dict:
    """Validate checkpoint identity against live hub identities."""
    checkpoint_identity = normalize_operation_identity(checkpoint.get("operation_identity") or {})
    if not checkpoint_identity:
        raise ValueError("Checkpoint is missing operation identity.")

    for role in _roles_to_validate(checkpoint_identity, hubs):
        _require_live_identity(hub_identities, role)

    validation_operation = _operation_for_validation(checkpoint_identity, operation)
    validation_collection_version = collection_version
    if validation_collection_version is None or validation_collection_version == "":
        validation_collection_version = checkpoint_identity.get("collection_version") or ""

    expected_identity = build_operation_identity(
        hubs=hubs,
        operation=validation_operation,
        collection_version=validation_collection_version,
        hub_identities=hub_identities,
    )
    try:
        validate_operation_identity({"operation_identity": checkpoint_identity}, expected_identity)
        return {"matched_mapping": "normal", "operation_identity": checkpoint_identity}
    except CheckpointIdentityMismatch as normal_error:
        if not (_has_hub(hubs, "primary") and _has_hub(hubs, "secondary")):
            raise ValueError(str(normal_error)) from normal_error

    swapped_identity = build_operation_identity(
        hubs=_swapped_hubs(hubs),
        operation=validation_operation,
        collection_version=validation_collection_version,
        hub_identities=_swapped_identities(hub_identities),
    )
    try:
        validate_operation_identity({"operation_identity": checkpoint_identity}, swapped_identity)
        return {"matched_mapping": "swapped", "operation_identity": checkpoint_identity}
    except CheckpointIdentityMismatch as swapped_error:
        raise ValueError(str(swapped_error)) from swapped_error


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "checkpoint": {"type": "dict", "required": True},
            "hubs": {"type": "dict", "required": True},
            "operation": {"type": "dict", "default": {}},
            "hub_identities": {"type": "dict", "required": True},
            "collection_version": {"type": "str", "default": ""},
        },
        supports_check_mode=True,
    )

    try:
        result = validate_checkpoint_identity(
            checkpoint=module.params["checkpoint"],
            hubs=module.params["hubs"],
            operation=module.params["operation"],
            hub_identities=module.params["hub_identities"],
            collection_version=module.params.get("collection_version") or "",
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
