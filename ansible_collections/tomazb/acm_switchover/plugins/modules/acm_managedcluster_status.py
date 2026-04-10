# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_managedcluster_status
short_description: Summarize ManagedCluster conditions
description:
  - Processes a list of ManagedCluster resource dicts and returns a compact summary
    of each cluster's C(joined) and C(available) state based on its status conditions.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  clusters:
    description: List of ManagedCluster resource dicts from the Kubernetes API.
    type: list
    elements: dict
    default: []
"""

EXAMPLES = r"""
- name: Summarize managed cluster conditions
  tomazb.acm_switchover.acm_managedcluster_status:
    clusters: "{{ acm_secondary_managed_clusters_info.resources | default([]) }}"
  register: cluster_status_result

- name: Debug cluster status
  ansible.builtin.debug:
    msg: "{{ cluster_status_result.cluster_status }}"
"""

from ansible.module_utils.basic import AnsibleModule


def summarize_cluster(cluster: dict) -> dict:
    conditions = cluster.get("status", {}).get("conditions", [])
    return {
        "name": cluster.get("metadata", {}).get("name", "unknown"),
        "joined": any(item.get("type") == "ManagedClusterJoined" and item.get("status") == "True" for item in conditions),
        "available": any(
            item.get("type") == "ManagedClusterConditionAvailable" and item.get("status") == "True"
            for item in conditions
        ),
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "clusters": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )
    cluster_status = [summarize_cluster(c) for c in module.params["clusters"]]
    module.exit_json(changed=False, cluster_status=cluster_status)


if __name__ == "__main__":
    main()
