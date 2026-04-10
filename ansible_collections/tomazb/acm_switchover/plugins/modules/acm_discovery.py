# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_discovery
short_description: Classify ACM hub role from observed state facts
description:
  - Read-only helper that inspects a hub's restore state and managed cluster count
    and returns a classified role (primary, secondary, or standby).
  - Does not make Kubernetes API calls; callers supply the observed facts.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  restore_state:
    description:
      - Current restore state observed on the hub (e.g. C(none), C(passive-sync)).
    type: str
    default: none
  managed_clusters:
    description:
      - Number of non-local ManagedClusters currently registered on the hub.
    type: int
    default: 0
"""

EXAMPLES = r"""
- name: Classify hub role
  tomazb.acm_switchover.acm_discovery:
    restore_state: passive-sync
    managed_clusters: 0
  register: hub_info

- name: Show hub role
  ansible.builtin.debug:
    msg: "Hub is acting as {{ hub_info.hub_role }}"
"""

RETURN = r"""
hub_role:
  description: Classified hub role based on the supplied facts.
  type: str
  returned: always
  sample: secondary
"""

from ansible.module_utils.basic import AnsibleModule


def classify_hub_state(facts: dict) -> str:
    """Classify hub role from observed state facts.

    Returns 'secondary' when the hub is in passive-sync restore mode,
    'primary' when it has managed clusters registered, and 'standby' otherwise.
    """
    if facts.get("restore_state") == "passive-sync":
        return "secondary"
    if facts.get("managed_clusters", 0) > 0:
        return "primary"
    return "standby"


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            restore_state=dict(type="str", default="none"),
            managed_clusters=dict(type="int", default=0),
        ),
        supports_check_mode=True,
    )
    facts = {
        "restore_state": module.params["restore_state"],
        "managed_clusters": module.params["managed_clusters"],
    }
    hub_role = classify_hub_state(facts)
    module.exit_json(changed=False, hub_role=hub_role)


if __name__ == "__main__":
    main()
