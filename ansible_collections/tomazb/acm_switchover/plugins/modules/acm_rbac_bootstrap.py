# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_rbac_bootstrap
short_description: Plan RBAC manifest selection and kubeconfig output for ACM switchover
description:
  - Determines which RBAC manifests to apply based on the requested role profile and
    optional decommission permissions. Does not apply manifests itself; callers use the
    returned asset list to drive kubernetes.core.k8s tasks.
  - Returns a structured plan describing which assets to apply and whether kubeconfigs
    should be generated.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  role:
    description:
      - Role profile to bootstrap. C(operator) provisions mutating switchover access.
      - C(validator) provisions the read-only validation profile.
      - C(both) provisions both service accounts and their role-specific bindings.
    type: str
    choices: [operator, validator, both]
    default: operator
  include_decommission:
    description:
      - Whether to append decommission-scoped ClusterRole manifests to the asset list.
      - Valid with C(operator) and C(both). Rejected with C(validator) because
        decommission permissions are operator-only.
    type: bool
    default: false
  generate_kubeconfigs:
    description:
      - Whether callers should generate kubeconfigs for the bootstrapped service account.
    type: bool
    default: false
"""

EXAMPLES = r"""
- name: Plan RBAC assets with decommission
  tomazb.acm_switchover.acm_rbac_bootstrap:
    role: operator
    include_decommission: true
  register: rbac_plan

- name: Apply each RBAC manifest
  kubernetes.core.k8s:
    src: "{{ item }}"
    state: present
  loop: "{{ rbac_plan.assets }}"
"""

RETURN = r"""
assets:
  description: Ordered list of RBAC manifest paths to apply.
  type: list
  elements: str
  returned: always
generate_kubeconfigs:
  description: Whether the caller should generate kubeconfigs after applying manifests.
  type: bool
  returned: always
role:
  description: Requested RBAC role profile to apply from the multi-document manifest set.
  type: str
  returned: always
role_targets:
  description: Concrete role labels selected by the requested role profile.
  type: list
  elements: str
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    RBAC_BASE_ASSETS,
    RBAC_DECOMMISSION_ASSETS,
    RBAC_VALID_ROLES,
)


def expand_rbac_role_targets(role: str) -> list[str]:
    """Return concrete manifest role labels for a requested bootstrap role."""
    if role not in RBAC_VALID_ROLES:
        raise ValueError(f"Invalid RBAC role '{role}'. Expected one of: {', '.join(RBAC_VALID_ROLES)}.")
    if role == "both":
        return ["operator", "validator"]
    return [role]


def select_rbac_assets(role: str, include_decommission: bool) -> list[str]:
    """Return an ordered list of RBAC manifest paths for the requested profile."""
    expand_rbac_role_targets(role)
    if include_decommission and role == "validator":
        raise ValueError("include_decommission is only valid for the operator role or both.")
    assets = list(RBAC_BASE_ASSETS)
    if include_decommission:
        assets.extend(RBAC_DECOMMISSION_ASSETS)
    return assets


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            role=dict(type="str", default="operator", choices=list(RBAC_VALID_ROLES)),
            include_decommission=dict(type="bool", default=False),
            generate_kubeconfigs=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )
    try:
        role_targets = expand_rbac_role_targets(module.params["role"])
        assets = select_rbac_assets(
            role=module.params["role"],
            include_decommission=module.params["include_decommission"],
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))
        return
    module.exit_json(
        changed=False,
        assets=assets,
        role=module.params["role"],
        role_targets=role_targets,
        generate_kubeconfigs=module.params["generate_kubeconfigs"],
    )


if __name__ == "__main__":
    main()
