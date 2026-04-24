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
    type: str
    choices: [operator, validator]
    default: operator
  include_decommission:
    description:
      - Whether to append decommission-scoped ClusterRole manifests to the asset list.
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
"""

from ansible.module_utils.basic import AnsibleModule

VALID_ROLES = ("operator", "validator")

_BASE_ASSETS = [
    "deploy/rbac/namespace.yaml",
    "deploy/rbac/serviceaccount.yaml",
    "deploy/rbac/role.yaml",
    "deploy/rbac/rolebinding.yaml",
    "deploy/rbac/clusterrole.yaml",
    "deploy/rbac/clusterrolebinding.yaml",
]

_DECOMMISSION_ASSETS = [
    "deploy/rbac/extensions/decommission/clusterrole.yaml",
    "deploy/rbac/extensions/decommission/clusterrolebinding.yaml",
]


def select_rbac_assets(role: str, include_decommission: bool) -> list[str]:
    """Return an ordered list of RBAC manifest paths for the requested profile."""
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid RBAC role '{role}'. Expected one of: {', '.join(VALID_ROLES)}.")
    if include_decommission and role != "operator":
        raise ValueError("include_decommission is only valid for the operator role.")
    assets = list(_BASE_ASSETS)
    if include_decommission:
        assets.extend(_DECOMMISSION_ASSETS)
    return assets


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            role=dict(type="str", default="operator", choices=list(VALID_ROLES)),
            include_decommission=dict(type="bool", default=False),
            generate_kubeconfigs=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )
    assets = select_rbac_assets(
        role=module.params["role"],
        include_decommission=module.params["include_decommission"],
    )
    module.exit_json(
        changed=False,
        assets=assets,
        role=module.params["role"],
        generate_kubeconfigs=module.params["generate_kubeconfigs"],
    )


if __name__ == "__main__":
    main()
