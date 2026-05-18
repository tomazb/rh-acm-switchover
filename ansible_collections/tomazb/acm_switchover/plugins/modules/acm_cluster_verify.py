# SPDX-License-Identifier: MIT

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
  expected_names:
    description: Exact non-local ManagedCluster names expected from preflight-derived restore input.
    type: list
    elements: str
    default: []
  allow_zero_managed_clusters:
    description:
      - Explicitly allow an empty non-local ManagedCluster set.
      - Defaults to false so restore-only cannot pass with only local-cluster by accident.
    type: bool
    default: false
"""

EXAMPLES = r"""
- name: Summarize cluster conditions
  tomazb.acm_switchover.acm_managedcluster_status:
    clusters: "{{ acm_secondary_managed_clusters_info.resources | default([]) }}"
  register: cluster_status_result

- name: Verify cluster group readiness
  tomazb.acm_switchover.acm_cluster_verify:
    cluster_status: "{{ cluster_status_result.cluster_status }}"
  register: verify_result

- name: Verify preflight-derived cluster group readiness
  tomazb.acm_switchover.acm_cluster_verify:
    cluster_status: "{{ cluster_status_result.cluster_status }}"
    min_managed_clusters: "{{ acm_switchover_expected_managed_cluster_count | default(0) | int }}"
    expected_names: "{{ acm_switchover_expected_managed_cluster_names | default([]) }}"
  register: verify_result
"""

from ansible.module_utils.basic import AnsibleModule


def summarize_cluster_group(
    clusters: list[dict],
    min_managed_clusters: int,
    expected_names: list[str] | None = None,
    allow_zero_managed_clusters: bool = False,
) -> dict:
    if min_managed_clusters < 0:
        raise ValueError("min_managed_clusters must be a non-negative integer")
    expected_names = sorted(expected_names or [])
    observed_names = sorted(item["name"] for item in clusters)
    pending = [item["name"] for item in clusters if not (item["joined"] and item["available"])]
    missing = sorted(set(expected_names) - set(observed_names))
    zero_without_expectation = (
        len(clusters) == 0 and min_managed_clusters == 0 and not expected_names and not allow_zero_managed_clusters
    )
    if zero_without_expectation:
        pending.append("no-managed-clusters")
    return {
        "passed": (
            not zero_without_expectation and len(clusters) >= min_managed_clusters and not pending and not missing
        ),
        "total": len(clusters),
        "pending": pending,
        "missing": missing,
    }


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "cluster_status": {"type": "list", "elements": "dict", "default": []},
            "min_managed_clusters": {"type": "int", "default": 1},
            "expected_names": {"type": "list", "elements": "str", "default": []},
            "allow_zero_managed_clusters": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    min_mc = module.params["min_managed_clusters"]
    if min_mc < 0:
        module.fail_json(msg="min_managed_clusters must be a non-negative integer")
    result = summarize_cluster_group(
        module.params["cluster_status"],
        min_mc,
        module.params["expected_names"],
        module.params["allow_zero_managed_clusters"],
    )
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
