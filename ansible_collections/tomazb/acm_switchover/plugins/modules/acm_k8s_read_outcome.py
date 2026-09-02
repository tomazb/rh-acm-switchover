# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
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

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (  # noqa: E402
    STRICT_READ_MAX_PAGES,
    STRICT_READ_MAX_RESTARTS,
    STRICT_READ_PAGE_LIMIT,
    STRICT_READ_REQUEST_TIMEOUT,
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


def _strict_list_page(raw) -> tuple[list[dict] | None, str | None, str | None]:
    """Return (members, continue_token, revision) for one page, or (None, None, None) if malformed.

    The revision is the page's own `metadata.resourceVersion`. `_drain_list_once` keeps only
    page 1's, which is the snapshot the whole read belongs to (A3.0 rule 8).
    """
    mapping = _to_mapping(raw)
    if mapping is None:
        return None, None, None
    if "items" not in mapping:
        return None, None, None
    items = mapping.get("items")
    if not isinstance(items, list):
        return None, None, None
    members: list[dict] = []
    for item in items:
        item_mapping = _to_mapping(item)
        if item_mapping is None:
            return None, None, None
        members.append(item_mapping)
    metadata = mapping.get("metadata")
    if not isinstance(metadata, dict):
        return None, None, None
    revision = metadata.get("resourceVersion")
    if not isinstance(revision, str) or not revision:
        # A complete read must be describable by a revision; anything else is malformed.
        return None, None, None
    token = metadata.get("continue") or None
    return members, token, revision


def _discovery_serves(api_client, api_version: str, resource_name: str) -> bool | None:
    """True if served, False if positively absent, None if unverifiable.

    The dynamic client's discovery cache substitutes an empty resource list
    for some discovery-fetch failures, and the substituted set differs across
    the supported client range, so a lookup miss alone never proves absence.
    """
    path = f"/apis/{api_version}" if "/" in api_version else f"/api/{api_version}"
    try:
        response = api_client.client.request("GET", path, serialize=False, _request_timeout=STRICT_READ_REQUEST_TIMEOUT)
        body = json.loads(response.data.decode("utf8"))
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("kind") != "APIResourceList":
        return None
    resources = body.get("resources")
    if not isinstance(resources, list):
        return None
    for entry in resources:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not entry["name"]
            or not isinstance(entry.get("kind"), str)
            or not entry["kind"]
        ):
            return None
        if entry["name"] == resource_name:
            return True
    return False


def _drain_list(api_client, resource, params) -> tuple[list[dict] | None, str, str | None]:
    """Drain every page of one list, or fail closed. Never returns a partial prefix."""
    for _ in range(STRICT_READ_MAX_RESTARTS + 1):
        collected, status, revision = _drain_list_once(api_client, resource, params)
        if status != "restart":
            return collected, status, revision
    return None, "error", None


def _drain_list_once(api_client, resource, params) -> tuple[list[dict] | None, str, str | None]:
    collected: list[dict] = []
    continue_token = None
    snapshot_revision = None  # page 1 owns it; a restart re-enters and re-establishes it
    # and every later page must be served at that same value
    for _ in range(STRICT_READ_MAX_PAGES):
        page_params = dict(params)
        page_params["limit"] = STRICT_READ_PAGE_LIMIT
        page_params["_request_timeout"] = STRICT_READ_REQUEST_TIMEOUT
        if continue_token:
            page_params["_continue"] = continue_token
        else:
            page_params["_continue"] = None
        try:
            raw = api_client.get(resource, **page_params)
        except Exception as exc:
            if getattr(exc, "status", None) == 410 and continue_token:
                # Expired continuation: discard everything, including this read's revision.
                return None, "restart", None
            return None, "error", None
        members, token, revision = _strict_list_page(raw)
        if members is None:
            return None, "error", None
        # A3.0 rule 8: every normal continuation page belongs to page 1's snapshot.
        if snapshot_revision is None:
            snapshot_revision = revision
        elif revision != snapshot_revision:
            return None, "error", None
        collected.extend(members)
        continue_token = token
        if not continue_token:
            return collected, "ok", snapshot_revision
    return None, "error", None


def _object_revision(resources: list[dict]) -> str | None:
    """The named object's own revision, or None when the response cannot supply one.

    `None` is not an `ok` value on the `get` path: its only caller classifies it as
    `error` before any success is published (A3.0 rule 9).
    """
    if len(resources) != 1:
        return None
    metadata = resources[0].get("metadata")
    if not isinstance(metadata, dict):
        return None
    revision = metadata.get("resourceVersion")
    return revision if isinstance(revision, str) and revision else None


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

    resource_name = module.params.get("resource_name")
    if not isinstance(resource_name, str) or not resource_name.strip():
        _exit_outcome(module, "error")
        return

    try:
        api_client = get_api_client(**module.params)
    except Exception:
        _exit_outcome(module, "error")
        return

    try:
        resource = api_client.resource(module.params["kind"], module.params["api_version"])
    except Exception:
        served = _discovery_serves(api_client, module.params["api_version"], resource_name)
        _exit_outcome(module, "kind_not_served" if served is False else "error")
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

    if read_mode == "list":
        resources, status, revision = _drain_list(api_client, resource, params)
        if status != "ok":
            _exit_outcome(module, "error")
            return
        _exit_outcome(module, "ok", resources, revision)
        return

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
    revision = _object_revision(resources)
    if revision is None:
        # A3.0 rule 9: a successful named GET must expose the object's own revision.
        # A response that cannot supply one is a malformed response, not an `ok` with null.
        _exit_outcome(module, "error")
        return
    _exit_outcome(module, "ok", resources, revision)
    return


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
