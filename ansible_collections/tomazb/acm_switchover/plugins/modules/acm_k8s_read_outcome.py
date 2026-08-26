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
extends_documentation_fragment:
  - kubernetes.core.k8s_auth_options
"""

EXAMPLES = r"""
- name: List Thanos compactor Pods with lossless outcome
  tomazb.acm_switchover.acm_k8s_read_outcome:
    read_mode: list
    api_version: v1
    kind: Pod
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
    - C(not_found) only for a named get that received an explicit 404/NotFound.
    - C(error) for every other unverifiable outcome.
  type: str
  returned: always
  choices: [ok, not_found, error]
resources:
  description: Normalized resource dictionaries on C(ok); empty list otherwise.
  type: list
  elements: dict
  returned: always
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402
from ansible_collections.kubernetes.core.plugins.module_utils.args_common import (  # noqa: E402
    AUTH_ARG_SPEC,
)
from ansible_collections.kubernetes.core.plugins.module_utils.k8s.client import (  # noqa: E402
    get_api_client,
)

try:
    from kubernetes.dynamic.exceptions import NotFoundError
except ImportError:  # pragma: no cover - dependency declared by kubernetes.core
    NotFoundError = type("NotFoundError", (Exception,), {})  # type: ignore[misc,assignment]


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
        }
    )
    return spec


def _exit_outcome(module: AnsibleModule, read_status: str, resources: list[dict] | None = None) -> None:
    module.exit_json(
        changed=False,
        read_status=read_status,
        resources=list(resources or []),
    )


def _to_mapping(value: Any) -> dict | None:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return dict(converted)
    return None


def _normalize_resources(read_mode: str, raw: Any) -> list[dict] | None:
    mapping = _to_mapping(raw)
    if mapping is None:
        return None

    kind = mapping.get("kind")
    if isinstance(kind, str) and kind.endswith("List"):
        items = mapping.get("items", [])
        if items is None:
            items = []
        if not isinstance(items, list):
            return None
        normalized: list[dict] = []
        for item in items:
            item_mapping = _to_mapping(item)
            if item_mapping is None:
                return None
            normalized.append(item_mapping)
        return normalized

    if "items" in mapping:
        items = mapping.get("items")
        if not isinstance(items, list):
            return None
        normalized = []
        for item in items:
            item_mapping = _to_mapping(item)
            if item_mapping is None:
                return None
            normalized.append(item_mapping)
        return normalized

    if read_mode == "get":
        return [mapping]

    # Bare non-list object is not a valid list inventory proof.
    return None


def _is_named_not_found(exc: BaseException) -> bool:
    if isinstance(exc, NotFoundError):
        return True
    status = getattr(exc, "status", None)
    return status == 404


def run_module(module: AnsibleModule) -> None:
    read_mode = module.params["read_mode"]
    name = module.params.get("name")
    if read_mode == "get" and (not isinstance(name, str) or not name.strip()):
        _exit_outcome(module, "error")
        return

    try:
        api_client = get_api_client(module)
    except Exception:
        _exit_outcome(module, "error")
        return

    try:
        resource = api_client.resource(module.params["kind"], module.params["api_version"])
    except Exception:
        _exit_outcome(module, "error")
        return

    params: dict[str, Any] = {}
    namespace = module.params.get("namespace")
    if namespace:
        params["namespace"] = namespace
    if read_mode == "get":
        params["name"] = name
    else:
        label_selectors = module.params.get("label_selectors") or []
        if label_selectors:
            params["label_selector"] = ",".join(label_selectors)

    try:
        raw = api_client.get(resource, **params)
    except Exception as exc:
        if read_mode == "get" and _is_named_not_found(exc):
            _exit_outcome(module, "not_found")
            return
        _exit_outcome(module, "error")
        return

    resources = _normalize_resources(read_mode, raw)
    if resources is None:
        _exit_outcome(module, "error")
        return
    _exit_outcome(module, "ok", resources)


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
