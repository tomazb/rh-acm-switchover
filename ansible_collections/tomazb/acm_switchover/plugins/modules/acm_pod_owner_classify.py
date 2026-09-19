#!/usr/bin/python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Capture the MCH operator identity, or classify one pass of ACM-namespace Pods against it."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

DOCUMENTATION = r"""
---
module: acm_pod_owner_classify
short_description: Capture the MultiClusterHub operator identity or classify one Pod drain pass
version_added: "1.8.0"
description:
  - C(operation=capture_identity) discovers the MultiClusterHub operator Deployment through the
    one Succeeded OLM ClusterServiceVersion owning the MultiClusterHub CRD, re-reads that CSV by
    name, and reads its single install-strategy Deployment. It returns the exact
    C(operator_deployment) identity, or a determinate C(operator_identity_unavailable) reason.
  - C(operation=classify) performs one complete classification pass. It reads the namespace,
    strictly lists every Pod in it, re-reads the recorded operator Deployment, and excludes a
    Pod only when its controller chain Pod -> ReplicaSet -> recorded Deployment matches the
    recorded name and UID. Under an unavailable identity no Pod is excluded.
  - Read-only. Every request is bounded, pagination is complete, and an unverifiable read is
    reported as C(error), never as an absent object or an empty inventory. Owns no retry,
    checkpoint, phase, deletion or completion policy; the caller branches on the status fields.
options:
  operation:
    description: Which of the two read-only operations to perform.
    type: str
    required: true
    choices: [capture_identity, classify]
  kubeconfig:
    description:
      - Path to the kubeconfig routing every read. Required; there is no ambient context fallback.
    type: str
    required: true
  context:
    description: Kubernetes context of the source hub within C(kubeconfig). Required.
    type: str
    required: true
  namespace:
    description: The ACM namespace. Must be C(open-cluster-management).
    type: str
    required: true
  mch_teardown_key:
    description: The enclosing MultiClusterHub teardown record key. Required for C(capture_identity).
    type: str
  mch_expected_uid:
    description: The recorded MultiClusterHub UID. Required for C(capture_identity).
    type: str
  operator_deployment:
    description:
      - The recorded C(operator_deployment) identity. For C(classify), exactly one of this and
        C(operator_identity_unavailable) is required.
    type: dict
  operator_identity_unavailable:
    description: The recorded C(operator_identity_unavailable) outcome, for C(classify).
    type: dict
notes:
  - Invoking tasks should set no_log; the arguments include a kubeconfig path.
author:
  - ACM Switchover Contributors (@tomazb)
"""

EXAMPLES = r"""
- name: Capture the MultiClusterHub operator identity before the guarded delete
  tomazb.acm_switchover.acm_pod_owner_classify:
    operation: capture_identity
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
    namespace: open-cluster-management
    mch_teardown_key: "{{ _acm_mch_key }}"
    mch_expected_uid: "{{ _acm_mch_uid }}"
  register: _acm_mch_identity
  no_log: true

- name: Classify one drain pass against the recorded identity
  tomazb.acm_switchover.acm_pod_owner_classify:
    operation: classify
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
    namespace: open-cluster-management
    # A record carries both identity keys with one null; omit the null one.
    operator_deployment: "{{ _acm_mch_record.operator_deployment | default(omit, true) }}"
    operator_identity_unavailable: "{{ _acm_mch_record.operator_identity_unavailable | default(omit, true) }}"
  register: _acm_mch_pass
  no_log: true
"""

RETURN = r"""
changed:
  description: Always false; this module never mutates the cluster.
  type: bool
  returned: always
capture_status:
  description:
    - C(operator_deployment) or C(operator_identity_unavailable) for a determinate capture.
    - C(error) when a ClusterServiceVersion read, or client construction, was unverifiable.
  type: str
  returned: when operation is capture_identity
  choices: [operator_deployment, operator_identity_unavailable, error]
operator_deployment:
  description: The captured identity in the exact teardown-record shape, or none.
  type: dict
  returned: when operation is capture_identity
operator_identity_unavailable:
  description: The determinate unavailable outcome in the exact teardown-record shape, or none.
  type: dict
  returned: when operation is capture_identity
read_status:
  description:
    - C(ok) when the namespace was present and the whole pass was classified.
    - C(namespace_absent) when a fresh Namespace GET positively proved the namespace absent; no Pod
      or Deployment was read.
    - C(error) when the Namespace or Pod read was unverifiable. Never an empty inventory.
  type: str
  returned: when operation is classify
  choices: [ok, namespace_absent, error]
read_error_stage:
  description:
    - Which read an C(error) pass could not verify, so the caller can apply its recovery policy.
    - C(namespace) when the Namespace GET was unverifiable; no Pod was listed.
    - C(pods) when the namespace was present but the Pod list was unverifiable.
    - None for C(ok) and C(namespace_absent), and for an C(error) that no read classified (client
      construction, or an unexpected module failure). A caller must treat an C(error) with no stage
      conservatively. Carries no reason code or server text.
  type: str
  returned: when operation is classify
  choices: [namespace, pods]
namespace_resource_version:
  description: Revision of the Namespace GET on C(ok), otherwise none.
  type: str
  returned: when operation is classify
pods_resource_version:
  description: Snapshot revision of the complete Pod list on C(ok), otherwise none.
  type: str
  returned: when operation is classify
deployment_resource_version:
  description: Revision of this pass's recorded-Deployment re-read when it matched, otherwise none.
  type: str
  returned: when operation is classify
identity_status:
  description: None when the recorded Deployment matched, otherwise the unavailable or inconsistent code.
  type: str
  returned: when operation is classify
deployment_status:
  description: C(matched), C(inconsistent), or C(not_applicable) (unavailable identity, or no classification).
  type: str
  returned: when operation is classify
  choices: [matched, inconsistent, not_applicable]
decisions:
  description: One C(name)/C(decision) entry per listed Pod, in list order.
  type: list
  elements: dict
  returned: when operation is classify
blocking_count:
  description: Number of C(drain_blocking) Pods on C(ok); none otherwise.
  type: int
  returned: when operation is classify
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402
from ansible_collections.kubernetes.core.plugins.module_utils.k8s.client import (  # noqa: E402
    get_api_client,
)

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import ACM_NAMESPACE  # noqa: E402
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.k8s_read import strict_read  # noqa: E402
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.pod_owner_classify import (  # noqa: E402
    READ_STATUS_ERROR,
    IdentityCaptureError,
    capture_identity,
    classify_error_result,
    classify_pass,
)


def _argument_spec() -> dict[str, Any]:
    return {
        "operation": {"type": "str", "required": True, "choices": ["capture_identity", "classify"]},
        "kubeconfig": {"type": "str", "required": True},
        "context": {"type": "str", "required": True},
        "namespace": {"type": "str", "required": True},
        "mch_teardown_key": {"type": "str", "required": False},
        "mch_expected_uid": {"type": "str", "required": False},
        "operator_deployment": {"type": "dict", "required": False},
        "operator_identity_unavailable": {"type": "dict", "required": False},
    }


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _argument_error(params: dict) -> str | None:
    """A fixed message for an invalid invocation, checked before any client exists."""
    if not _non_empty(params.get("kubeconfig")) or not _non_empty(params.get("context")):
        return "kubeconfig and context are required: refusing an implicit Kubernetes context"
    if params.get("namespace") != ACM_NAMESPACE:
        return "namespace must be the ACM namespace"
    if params["operation"] == "capture_identity":
        if not _non_empty(params.get("mch_teardown_key")) or not _non_empty(params.get("mch_expected_uid")):
            return "capture_identity requires mch_teardown_key and mch_expected_uid"
        return None
    deployment = params.get("operator_deployment")
    unavailable = params.get("operator_identity_unavailable")
    if (deployment is None) == (unavailable is None):
        return "classify requires exactly one of operator_deployment and operator_identity_unavailable"
    if deployment is not None and not all(_non_empty(deployment.get(field)) for field in ("namespace", "name", "uid")):
        return "operator_deployment must carry its namespace, name and uid"
    return None


def _reader(api_client):
    def read(read_mode, api_version, kind, resource_name, namespace=None, name=None):
        return strict_read(
            api_client,
            read_mode=read_mode,
            api_version=api_version,
            kind=kind,
            resource_name=resource_name,
            namespace=namespace,
            name=name,
        )

    return read


def _capture_error() -> dict:
    return {"capture_status": READ_STATUS_ERROR, "operator_deployment": None, "operator_identity_unavailable": None}


def run_module(module: AnsibleModule) -> None:
    params = module.params
    error = _argument_error(params)
    if error is not None:
        module.fail_json(msg=error, changed=False)
        return

    capture = params["operation"] == "capture_identity"
    try:
        api_client = get_api_client(kubeconfig=params["kubeconfig"], context=params["context"])
    except Exception:  # noqa: BLE001 -- client construction failures echo kubeconfig content
        module.exit_json(changed=False, **(_capture_error() if capture else classify_error_result()))
        return
    read = _reader(api_client)

    if capture:
        try:
            identity = capture_identity(
                read,
                namespace=params["namespace"],
                mch_teardown_key=params["mch_teardown_key"],
                mch_expected_uid=params["mch_expected_uid"],
                captured_at=datetime.now(timezone.utc).isoformat(),
            )
        except IdentityCaptureError:
            module.exit_json(changed=False, **_capture_error())
            return
        status = (
            "operator_deployment" if identity["operator_deployment"] is not None else "operator_identity_unavailable"
        )
        module.exit_json(changed=False, capture_status=status, **identity)
        return

    identity = {
        "operator_deployment": params.get("operator_deployment"),
        "operator_identity_unavailable": params.get("operator_identity_unavailable"),
    }
    module.exit_json(changed=False, **classify_pass(read, identity, namespace=params["namespace"]))


def main() -> None:
    # No ansible-core mutually_exclusive on the identity pair: it counts present keys, and a
    # teardown record or capture result carries both with one null. `_argument_error` enforces
    # exactly one non-null identity instead.
    module = AnsibleModule(argument_spec=_argument_spec(), supports_check_mode=True)
    try:
        run_module(module)
    except SystemExit:
        raise
    except Exception:
        # Programmer and setup surprises stay inside the sanitized contract.
        failure = _capture_error() if module.params.get("operation") == "capture_identity" else classify_error_result()
        module.exit_json(changed=False, **failure)


if __name__ == "__main__":
    main()
