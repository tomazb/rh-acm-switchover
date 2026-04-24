# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_argocd_filter
short_description: Filter Argo CD Applications to only ACM-touching ones
description:
  - Accepts a list of Argo CD Application resources and returns only those
    that manage ACM-related resources (by namespace or kind). No API calls
    are made; the filtering is based on C(status.resources) in each Application.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  applications:
    description: List of Argo CD Application resource dicts.
    required: true
    type: list
    elements: dict
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import (
    filter_acm_applications,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "applications": {"type": "list", "elements": "dict", "required": True},
        },
        supports_check_mode=True,
    )
    filtered = filter_acm_applications(module.params["applications"])
    module.exit_json(
        changed=False,
        applications=filtered,
        total=len(module.params["applications"]),
        matched=len(filtered),
    )


if __name__ == "__main__":
    main()
