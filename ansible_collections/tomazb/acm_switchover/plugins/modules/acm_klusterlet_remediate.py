# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_klusterlet_remediate
short_description: Remediate managed cluster klusterlet hub connections with bounded concurrency
description:
  - Re-applies the bootstrap hub kubeconfig from the new hub import secret and restarts klusterlet.
  - Returns per-cluster structured remediation results.
options:
  secondary_hub:
    description: Secondary hub kubeconfig and optional context.
    type: dict
    required: true
  managed_clusters:
    description: Mapping of ManagedCluster name to kubeconfig and optional context.
    type: dict
    default: {}
  pending_clusters:
    description: Cluster names to remediate.
    type: list
    elements: str
    default: []
  workers:
    description: Maximum concurrent workers. Use C(1) for sequential behavior.
    type: int
    default: 10
  strict:
    description: Fail the module when any cluster remediation fails.
    type: bool
    default: false
  request_timeout:
    description: Per Kubernetes API request timeout in seconds.
    type: int
    default: 30
  future_timeout:
    description: Maximum worker future wait window for the remediation batch in seconds.
    type: int
    default: 180
"""

EXAMPLES = r"""
- name: Remediate klusterlet hub connections
  tomazb.acm_switchover.acm_klusterlet_remediate:
    secondary_hub: "{{ acm_switchover_hubs.secondary }}"
    managed_clusters: "{{ acm_switchover_managed_clusters }}"
    pending_clusters: "{{ _klusterlet_remediation_candidates }}"
    workers: 10
    strict: false
    request_timeout: 30
    future_timeout: 180
  register: _klusterlet_remediation_result
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    KLUSTERLET_DEFAULT_WORKERS,
    KLUSTERLET_REQUEST_TIMEOUT,
    KLUSTERLET_WORKER_TIMEOUT,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.klusterlet import remediate_klusterlets

__all__ = ["remediate_klusterlets"]


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "secondary_hub": {"type": "dict", "required": True},
            "managed_clusters": {"type": "dict", "default": {}},
            "pending_clusters": {"type": "list", "elements": "str", "default": []},
            "workers": {"type": "int", "default": KLUSTERLET_DEFAULT_WORKERS},
            "strict": {"type": "bool", "default": False},
            "request_timeout": {"type": "int", "default": KLUSTERLET_REQUEST_TIMEOUT},
            "future_timeout": {"type": "int", "default": KLUSTERLET_WORKER_TIMEOUT},
        },
        supports_check_mode=True,
    )
    try:
        result = remediate_klusterlets(
            secondary_hub=module.params["secondary_hub"],
            managed_clusters=module.params["managed_clusters"],
            pending_clusters=module.params["pending_clusters"],
            workers=module.params["workers"],
            strict=module.params["strict"],
            check_mode=module.check_mode,
            request_timeout=module.params.get("request_timeout", KLUSTERLET_REQUEST_TIMEOUT),
            future_timeout=module.params.get("future_timeout", KLUSTERLET_WORKER_TIMEOUT),
        )
    except Exception as exc:
        module.fail_json(msg=str(exc))
        return
    if result.get("failed"):
        module.fail_json(**result)
        return
    module.exit_json(**result)


if __name__ == "__main__":
    main()
