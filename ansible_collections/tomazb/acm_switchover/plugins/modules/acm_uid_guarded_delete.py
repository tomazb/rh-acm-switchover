#!/usr/bin/python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Delete a Kubernetes object only when it is still the object the caller proved."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_uid_guarded_delete
short_description: Delete a Kubernetes object guarded by its metadata.uid
version_added: "1.8.0"
description:
  - Deletes a single object only when its C(metadata.uid) still matches C(expected_uid),
    sending the UID as a delete precondition so the API server evaluates it atomically
    with the deletion.
  - A name-only delete cannot bind identity. Between the read that establishes which
    object is being removed and the delete itself, the name may come to refer to a
    different object; this module refuses that case and leaves the replacement intact.
  - Only an API 404 means absent. Discovery, authorization, TLS, timeout, transport and
    decode failures are unverifiable and fail closed, never reported as an absent object.
  - Resource discovery is performed live on every invocation; a cached discovery result
    from an earlier run is never allowed to stand in for it.
  - When the API server answers the delete itself with a 404 the object went away before
    this invocation acted. That answer is proved with one further live read. A confirmed
    absence is an idempotent result with C(changed=false), while a replacement or an
    unverifiable read fails.
options:
  kubeconfig:
    description:
      - Path to the kubeconfig routing this delete. Required; there is no ambient or
        default context fallback, because that would route a delete at whatever cluster
        the environment happens to point at.
    type: str
    required: true
  context:
    description:
      - Kubernetes context name within C(kubeconfig). Required for the same reason.
    type: str
    required: true
  api_version:
    description: API version of the target, for example C(v1) or C(group/v1beta2).
    type: str
    required: true
  kind:
    description: Kubernetes resource kind of the target.
    type: str
    required: true
  resource_name:
    description:
      - The exact canonical Kubernetes APIResource name (plural) for C(kind); never
        synthesized from C(kind).
    type: str
    required: true
  namespace:
    description:
      - Namespace of the target. Required for a namespaced kind and rejected for a
        cluster-scoped one, validated against the scope reported by discovery before any
        object is read. The client routes by the discovered scope, so a mismatch would
        silently act on the wrong object or read a route whose 404 means nothing.
    type: str
    required: false
  name:
    description: Name of the target object.
    type: str
    required: true
  expected_uid:
    description:
      - The C(metadata.uid) the caller proved. An unconditional delete is not an
        acceptable fallback, so this is required and may not be empty.
    type: str
    required: true
  request_timeout:
    description:
      - Per-request timeout in seconds. Must be a positive finite number; NaN and
        infinity are rejected.
    type: float
    required: false
  wait_timeout:
    description:
      - Total bounded budget, in seconds, for the object to disappear. Must be a positive
        finite number, since a non-finite budget defeats the deadline it exists to
        enforce. Individual poll delays are capped to the remaining budget.
    type: float
    required: false
  wait_sleep:
    description:
      - Delay in seconds between absence polls. Must be a positive finite number.
    type: float
    required: false
notes:
  - Invoking tasks must set no_log on the task. Module output carries only a stable
    stage, a status classification and the non-secret resource identity, but the task
    arguments include a kubeconfig path.
author:
  - ACM Switchover Contributors (@tomazb)
"""

EXAMPLES = r"""
- name: Delete the proved MultiClusterObservability
  tomazb.acm_switchover.acm_uid_guarded_delete:
    kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.primary.context }}"
    api_version: observability.open-cluster-management.io/v1beta2
    kind: MultiClusterObservability
    resource_name: multiclusterobservabilities
    name: observability
    expected_uid: "{{ _mco_proved_uid }}"
  no_log: true
"""

RETURN = r"""
changed:
  description:
    - A successful result is true only after the intended-UID delete was accepted and
      both absence proofs succeeded.
    - An object that was already absent, or that a proved final read shows absent after
      the delete itself returned 404, reports false; this invocation deleted nothing.
    - A failed result is true when the intended-UID delete was accepted before a later
      poll or proof failure, so callers can report the mutation that already happened.
  returned: always
  type: bool
would_change:
  description: In check mode, whether a real run would delete the proved object.
  returned: success
  type: bool
stage:
  description: How far the state machine got.
  returned: always
  type: str
  sample: completed
reason:
  description: Closed classification of why the stage ended.
  returned: always
  type: str
  sample: ok
resource_version:
  description:
    - The resourceVersion observed at the guarded read, or none when the object was
      already absent. Failures report stage and reason only.
  returned: success
  type: str
"""

from ansible.module_utils.basic import AnsibleModule  # noqa: E402

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.uid_guarded_delete import (  # noqa: E402
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_WAIT_SLEEP,
    DEFAULT_WAIT_TIMEOUT,
    REASON_UNVERIFIABLE,
    STAGE_READ,
    GuardedDeleteError,
    build_dynamic_client,
    normalize_timeout,
    run_guarded_delete,
    validate_namespace_scope,
)


def _argument_spec() -> dict:
    return {
        "kubeconfig": {"type": "str", "required": True},
        "context": {"type": "str", "required": True},
        "api_version": {"type": "str", "required": True},
        "kind": {"type": "str", "required": True},
        "resource_name": {"type": "str", "required": True},
        "namespace": {"type": "str", "required": False},
        "name": {"type": "str", "required": True},
        "expected_uid": {"type": "str", "required": True},
        "request_timeout": {"type": "float", "required": False},
        "wait_timeout": {"type": "float", "required": False},
        "wait_sleep": {"type": "float", "required": False},
    }


def _resolve_resource(
    kubeconfig: str,
    context: str,
    request_timeout,
    api_version: str,
    kind: str,
    resource_name: str,
):
    """Build the explicitly routed client and resolve the target resource.

    Kept as its own seam so a resolution failure is classified by the caller rather
    than escaping raw: discovery is one of the unverifiable classes and must never
    read as an absent object.
    """
    client = build_dynamic_client(kubeconfig, context, request_timeout)
    return client.resources.get(api_version=api_version, kind=kind, name=resource_name)


def run_module(module: AnsibleModule) -> None:
    params = module.params
    try:
        request_timeout = normalize_timeout(params.get("request_timeout"), "request_timeout", DEFAULT_REQUEST_TIMEOUT)
        wait_timeout = normalize_timeout(params.get("wait_timeout"), "wait_timeout", DEFAULT_WAIT_TIMEOUT)
        wait_sleep = normalize_timeout(params.get("wait_sleep"), "wait_sleep", DEFAULT_WAIT_SLEEP)
    except ValueError as exc:
        module.fail_json(msg=str(exc), changed=False, reason=REASON_UNVERIFIABLE, stage=STAGE_READ)
        return

    try:
        resource = _resolve_resource(
            params["kubeconfig"],
            params["context"],
            request_timeout,
            params["api_version"],
            params["kind"],
            params["resource_name"],
        )
    except Exception:  # noqa: BLE001
        # Deliberately does not interpolate the exception: discovery failures echo
        # request content, and the kubeconfig path is itself an infrastructure detail.
        module.fail_json(
            msg=(
                f"Cannot resolve {params['kind']} on the target hub, so the state of "
                f"{params['name']} is unverifiable. Refusing to treat it as absent."
            ),
            changed=False,
            reason=REASON_UNVERIFIABLE,
            stage=STAGE_READ,
        )
        return

    try:
        validate_namespace_scope(resource, params.get("namespace"))
        result = run_guarded_delete(
            resource,
            name=params["name"],
            namespace=params.get("namespace"),
            expected_uid=params["expected_uid"],
            check_mode=module.check_mode,
            wait_timeout=wait_timeout,
            wait_sleep=wait_sleep,
        )
    except GuardedDeleteError as exc:
        module.fail_json(msg=exc.message, changed=exc.changed, reason=exc.reason, stage=exc.stage)
        return

    module.exit_json(**result)


def main() -> None:
    module = AnsibleModule(argument_spec=_argument_spec(), supports_check_mode=True)
    try:
        run_module(module)
    except SystemExit:
        raise
    except Exception:
        # Programmer and setup surprises stay inside the sanitized contract rather
        # than surfacing a traceback that may carry request or credential material.
        module.fail_json(
            msg="The guarded delete failed before it could verify anything. Nothing was deleted.",
            changed=False,
            reason=REASON_UNVERIFIABLE,
            stage=STAGE_READ,
        )


if __name__ == "__main__":
    main()
