# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

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
      - Role profile to expand. C(operator) includes write permissions; C(validator) is read-only.
    type: str
    default: operator
    choices: [operator, validator]
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
- name: Plan RBAC assets for operator with decommission
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
"""

from ansible.module_utils.basic import AnsibleModule

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
    """Return an ordered list of RBAC manifest paths for the requested profile.

    The role parameter currently accepts 'operator' or 'validator' for forward
    compatibility. Both roles receive the same base asset set; future iterations
    may differentiate validator-specific read-only assets.
    """
    assets = list(_BASE_ASSETS)
    if include_decommission:
        assets.extend(_DECOMMISSION_ASSETS)
    return assets


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            role=dict(type="str", default="operator", choices=["operator", "validator"]),
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
        generate_kubeconfigs=module.params["generate_kubeconfigs"],
    )


if __name__ == "__main__":
    main()
