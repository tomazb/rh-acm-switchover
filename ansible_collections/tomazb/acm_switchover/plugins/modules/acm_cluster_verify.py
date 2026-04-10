# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_cluster_verify
short_description: Verify managed cluster group readiness
description:
  - Accepts a list of cluster status summaries (from M(tomazb.acm_switchover.acm_managedcluster_status))
    and returns a pass/fail verdict based on the minimum required cluster count.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  cluster_status:
    description: List of cluster summary dicts with C(name), C(joined), and C(available) keys.
    type: list
    elements: dict
    default: []
  min_managed_clusters:
    description: Minimum number of clusters that must be joined and available for the check to pass.
    type: int
    default: 1
"""

EXAMPLES = r"""
- name: Summarize cluster conditions
  tomazb.acm_switchover.acm_managedcluster_status:
    clusters: "{{ acm_secondary_managed_clusters_info.resources | default([]) }}"
  register: cluster_status_result

- name: Verify cluster group readiness
  tomazb.acm_switchover.acm_cluster_verify:
    cluster_status: "{{ cluster_status_result.cluster_status }}"
    min_managed_clusters: "{{ acm_switchover_operation.min_managed_clusters | default(1) | int }}"
  register: verify_result
"""

from ansible.module_utils.basic import AnsibleModule


def summarize_cluster_group(clusters: list[dict], min_managed_clusters: int) -> dict:
    pending = [item["name"] for item in clusters if not (item["joined"] and item["available"])]
    return {
        "passed": len(clusters) >= min_managed_clusters and not pending,
        "total": len(clusters),
        "pending": pending,
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "cluster_status": {"type": "list", "elements": "dict", "default": []},
            "min_managed_clusters": {"type": "int", "default": 1},
        },
        supports_check_mode=True,
    )
    result = summarize_cluster_group(
        module.params["cluster_status"],
        module.params["min_managed_clusters"],
    )
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
