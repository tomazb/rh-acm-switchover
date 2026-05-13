# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_klusterlet_probe
short_description: Probe managed cluster klusterlet hub connections with bounded concurrency
description:
  - Compares managed-cluster klusterlet hub kubeconfigs with the new hub import secret.
  - Returns per-cluster structured probe results.
options:
  secondary_hub:
    description: Secondary hub kubeconfig and optional context.
    type: dict
    required: true
  managed_clusters:
    description: Mapping of ManagedCluster name to kubeconfig and optional context.
    type: dict
    default: {}
  candidate_clusters:
    description: Cluster names to probe.
    type: list
    elements: str
  workers:
    description: Maximum concurrent workers. Use C(1) for sequential behavior.
    type: int
    default: 10
  request_timeout:
    description: Per Kubernetes API request timeout in seconds.
    type: int
    default: 30
  future_timeout:
    description: Maximum time to wait for each worker future in seconds.
    type: int
    default: 180
"""

EXAMPLES = r"""
- name: Probe klusterlet hub connections
  tomazb.acm_switchover.acm_klusterlet_probe:
    secondary_hub: "{{ acm_switchover_hubs.secondary }}"
    managed_clusters: "{{ acm_switchover_managed_clusters }}"
    candidate_clusters: "{{ _klusterlet_probe_candidates }}"
    workers: 10
    request_timeout: 30
    future_timeout: 180
  register: _klusterlet_probe_result
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    KLUSTERLET_DEFAULT_WORKERS,
    KLUSTERLET_REQUEST_TIMEOUT,
    KLUSTERLET_WORKER_TIMEOUT,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.klusterlet import probe_klusterlet_connections

__all__ = ["probe_klusterlet_connections"]


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "secondary_hub": {"type": "dict", "required": True},
            "managed_clusters": {"type": "dict", "default": {}},
            "candidate_clusters": {"type": "list", "elements": "str", "required": False},
            "workers": {"type": "int", "default": KLUSTERLET_DEFAULT_WORKERS},
            "request_timeout": {"type": "int", "default": KLUSTERLET_REQUEST_TIMEOUT},
            "future_timeout": {"type": "int", "default": KLUSTERLET_WORKER_TIMEOUT},
        },
        supports_check_mode=True,
    )
    try:
        result = probe_klusterlet_connections(
            secondary_hub=module.params["secondary_hub"],
            managed_clusters=module.params["managed_clusters"],
            candidate_clusters=module.params.get("candidate_clusters"),
            workers=module.params["workers"],
            request_timeout=module.params.get("request_timeout", KLUSTERLET_REQUEST_TIMEOUT),
            future_timeout=module.params.get("future_timeout", KLUSTERLET_WORKER_TIMEOUT),
        )
    except Exception as exc:
        module.fail_json(msg=str(exc))
        return
    module.exit_json(**result)


if __name__ == "__main__":
    main()
