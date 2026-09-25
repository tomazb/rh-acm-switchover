# SPDX-License-Identifier: MIT

from __future__ import annotations

from copy import deepcopy
from typing import Any

DOCUMENTATION = r"""
---
module: acm_k8s_read_outcome
short_description: Perform one Kubernetes read and classify the outcome
description:
  - Performs exactly one Kubernetes GET or LIST via kubernetes.core client
    construction and returns a sanitized outcome.
  - Distinguishes successful empty results, named NotFound, and unverifiable
    errors without returning raw exception or response body content.
  - Read-only; always reports C(changed=false). Owns no retry or phase policy.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  read_mode:
    description: Named object get versus collection list.
    type: str
    required: true
    choices: [get, list]
  api_version:
    description: Kubernetes API version for the resource.
    type: str
    required: true
  kind:
    description: Kubernetes resource kind.
    type: str
    required: true
  namespace:
    description: Namespace for namespaced resources.
    type: str
  name:
    description: Object name. Required when C(read_mode=get).
    type: str
  label_selectors:
    description: Label selectors for list mode.
    type: list
    elements: str
    default: []
  resource_name:
    description:
      - The exact canonical Kubernetes APIResource name (plural) for C(kind); never
        synthesized from C(kind).
    type: str
    required: true
extends_documentation_fragment:
  - kubernetes.core.k8s_auth_options
"""

EXAMPLES = r"""
- name: List Thanos compactor Pods with lossless outcome
  tomazb.acm_switchover.acm_k8s_read_outcome:
    read_mode: list
    api_version: v1
    kind: Pod
    resource_name: pods
    namespace: open-cluster-management-observability
    label_selectors:
      - app.kubernetes.io/name=thanos-compact
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
  register: compactor_pods
  failed_when: false
  no_log: true

- name: Read import-controller-config with lossless outcome
  tomazb.acm_switchover.acm_k8s_read_outcome:
    read_mode: get
    api_version: v1
    kind: ConfigMap
    resource_name: configmaps
    namespace: multicluster-engine
    name: import-controller-config
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
  register: import_controller_config
  failed_when: false
  no_log: true
"""

RETURN = r"""
changed:
  description: Always false; this module never mutates the cluster.
  type: bool
  returned: always
read_status:
  description:
    - C(ok) when the read completed successfully.
    - C(not_found) only for a named get that received an explicit 404/NotFound and whose
      API group/version, read live, still serves this kind. A 404 for a kind that live
      discovery positively omits is C(kind_not_served); one whose discovery cannot be read
      is C(error).
    - C(kind_not_served) when the API group/version was read successfully and
      positively does not serve this kind.
    - C(error) for every other unverifiable outcome.
  type: str
  returned: always
  choices: [ok, not_found, kind_not_served, error]
resources:
  description: Normalized resource dictionaries on C(ok); empty list otherwise.
  type: list
  elements: dict
  returned: always
resource_version:
  description:
    - The Kubernetes C(metadata.resourceVersion) of the successful read, or C(none).
    - For C(read_mode=get) this is the returned object's own revision.
    - For C(read_mode=list) this is the single snapshot revision of the complete paginated
      read, established by its first page; it is never a per-page value.
    - Always C(none) on C(not_found), C(kind_not_served), and C(error); an absence proof and a
      failed read carry no revision, and none is ever synthesized.
  type: str
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402
from ansible_collections.kubernetes.core.plugins.module_utils.args_common import (  # noqa: E402
    AUTH_ARG_SPEC,
)
from ansible_collections.kubernetes.core.plugins.module_utils.k8s.client import (  # noqa: E402
    get_api_client,
)

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.k8s_read import (  # noqa: E402,F401
    _discovery_serves,
    strict_read,
)

# `_discovery_serves` is re-exported: the strict-read helpers moved to module_utils/k8s_read.py
# unchanged, and existing callers still reach the prover through this module.


def _argument_spec() -> dict[str, Any]:
    spec = deepcopy(AUTH_ARG_SPEC)
    spec.update(
        {
            "read_mode": {"type": "str", "required": True, "choices": ["get", "list"]},
            "api_version": {"type": "str", "required": True},
            "kind": {"type": "str", "required": True},
            "namespace": {"type": "str", "required": False},
            "name": {"type": "str", "required": False},
            "label_selectors": {
                "type": "list",
                "elements": "str",
                "required": False,
                "default": [],
            },
            "resource_name": {"type": "str", "required": True},
        }
    )
    return spec


def _exit_outcome(
    module: AnsibleModule,
    read_status: str,
    resources: list[dict] | None = None,
    resource_version: str | None = None,
) -> None:
    module.exit_json(
        changed=False,
        read_status=read_status,
        resources=list(resources or []),
        resource_version=resource_version,
    )


def run_module(module: AnsibleModule) -> None:
    read_mode = module.params["read_mode"]
    name = module.params.get("name")
    if read_mode == "get" and (not isinstance(name, str) or not name.strip()):
        _exit_outcome(module, "error")
        return

    resource_name = module.params.get("resource_name")
    if not isinstance(resource_name, str) or not resource_name.strip():
        _exit_outcome(module, "error")
        return

    try:
        api_client = get_api_client(**module.params)
    except Exception:
        _exit_outcome(module, "error")
        return

    read_status, resources, revision = strict_read(
        api_client,
        read_mode=read_mode,
        api_version=module.params["api_version"],
        kind=module.params["kind"],
        resource_name=resource_name,
        namespace=module.params.get("namespace"),
        name=name,
        label_selectors=module.params.get("label_selectors") or [],
    )
    _exit_outcome(module, read_status, resources, revision)


def main() -> None:
    module = AnsibleModule(
        argument_spec=_argument_spec(),
        supports_check_mode=True,
        required_if=[("read_mode", "get", ["name"])],
    )
    try:
        run_module(module)
    except SystemExit:
        raise
    except Exception:
        # Programmer/setup surprises stay within the sanitized error contract.
        _exit_outcome(module, "error")


if __name__ == "__main__":
    main()
