#!/usr/bin/python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Apply one server-guarded PATCH or DELETE to an ACM Restore."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_restore_guarded_mutation
short_description: Apply one server-guarded PATCH or DELETE to an ACM Restore
version_added: "1.8.0"
description:
  - Submits exactly one request to a single ACM Restore, guarded so the API server applies
    it only while the Restore is still the object the caller proved.
  - C(action=patch) sends one RFC 6902 JSON Patch (C(application/json-patch+json)) whose
    C(test) operations bind the exact C(metadata.uid), C(metadata.resourceVersion) and raw
    C(spec.veleroManagedClustersBackupName) before a C(replace) of that field with
    C(latest). Nothing else is mutated.
  - C(action=delete) sends a DELETE whose preconditions carry both the expected UID and
    the expected resourceVersion. It does not wait for the Restore to disappear.
  - A failed JSON Patch test or a precondition conflict (HTTP 409, 412 or, for a patch,
    422) fails with C(conflict=true); the request mutated nothing and is never retried or
    replaced by an unguarded request. A retry must start again from a fresh read.
  - A timeout or transport failure is not acceptance. Only an accepted PATCH response
    supplies C(generation); when it omits one, C(generation_reported) is false.
  - Owns no read, polling or phase policy. Check mode makes no API call and reports the
    prediction in C(would_change).
options:
  kubeconfig:
    description:
      - Path to the kubeconfig routing this request. Required; there is no ambient or
        default context fallback.
    type: str
    required: true
  context:
    description:
      - Kubernetes context name within C(kubeconfig). Required for the same reason.
    type: str
    required: true
  action:
    description: The single guarded request to submit.
    type: str
    required: true
    choices: [patch, delete]
  api_version:
    description: API version of the Restore.
    type: str
    required: true
    choices: [cluster.open-cluster-management.io/v1beta1]
  kind:
    description: Kind of the target.
    type: str
    required: true
    choices: [Restore]
  namespace:
    description: Namespace of the Restore.
    type: str
    required: true
  name:
    description: Name of the Restore.
    type: str
    required: true
  expected_uid:
    description: The C(metadata.uid) the caller proved. May not be empty.
    type: str
    required: true
  expected_resource_version:
    description: The C(metadata.resourceVersion) the caller proved. May not be empty.
    type: str
    required: true
  expected_managed_clusters_backup_name:
    description:
      - The exact raw C(spec.veleroManagedClustersBackupName) the caller proved, compared
        without trimming or case folding. Required for, and only accepted with,
        C(action=patch).
    type: str
    required: false
  replacement_managed_clusters_backup_name:
    description:
      - The replacement value. Only C(latest) is accepted. Required for, and only accepted
        with, C(action=patch).
    type: str
    required: false
    choices: [latest]
  request_timeout:
    description:
      - Per-request timeout in seconds. Must be a positive finite number.
    type: float
    required: false
notes:
  - Invoking tasks must set no_log on the task. Module output carries only a stable
    reason, the conflict flag and the non-secret Restore identity, but the task arguments
    include a kubeconfig path.
author:
  - ACM Switchover Contributors (@tomazb)
"""

EXAMPLES = r"""
- name: Point the proved passive sync Restore at the latest ManagedClusters backup
  tomazb.acm_switchover.acm_restore_guarded_mutation:
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context }}"
    action: patch
    api_version: cluster.open-cluster-management.io/v1beta1
    kind: Restore
    namespace: open-cluster-management-backup
    name: restore-acm-passive-sync
    expected_uid: "{{ _restore_proved_uid }}"
    expected_resource_version: "{{ _restore_proved_resource_version }}"
    expected_managed_clusters_backup_name: "{{ _restore_proved_managed_clusters_raw }}"
    replacement_managed_clusters_backup_name: latest
  no_log: true
"""

RETURN = r"""
changed:
  description:
    - True only when this invocation's guarded request was accepted and changed the
      Restore. A patch whose proved raw value was already C(latest) reports false.
  returned: always
  type: bool
would_change:
  description: In check mode, whether a real run would change the Restore.
  returned: always
  type: bool
accepted:
  description: Whether the API server accepted the guarded request.
  returned: always
  type: bool
conflict:
  description:
    - True when the server rejected the request because the live Restore is not the
      proved one. Nothing was mutated.
  returned: always
  type: bool
reason:
  description:
    - Closed classification of the outcome.
    - C(ok), C(predicted) (check mode), C(precondition_failed), C(not_found),
      C(unverifiable), C(invalid_input) or C(malformed_response) (an accepted PATCH whose
      response lacks the proved UID or a resourceVersion).
  returned: always
  type: str
  sample: ok
uid:
  description:
    - For an accepted patch, the response's C(metadata.uid); for an accepted delete, the
      UID its precondition bound. Otherwise null.
  returned: always
  type: str
resource_version:
  description:
    - For an accepted patch, the response's C(metadata.resourceVersion); for an accepted
      delete, the resourceVersion its precondition bound. Otherwise null.
  returned: always
  type: str
generation:
  description:
    - The integer C(metadata.generation) of an accepted PATCH response; null when the
      response omits it. Never inferred.
  returned: when action is patch
  type: int
generation_reported:
  description: Whether an accepted PATCH response carried an integer generation.
  returned: when action is patch
  type: bool
"""


from ansible.module_utils.basic import AnsibleModule  # noqa: E402

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.uid_guarded_delete import (  # noqa: E402
    DEFAULT_REQUEST_TIMEOUT,
    GuardedDeleteError,
    api_status,
    build_dynamic_client,
    normalize_timeout,
    validate_namespace_scope,
)

RESTORE_API_VERSION = "cluster.open-cluster-management.io/v1beta1"
RESTORE_KIND = "Restore"
RESTORE_RESOURCE_NAME = "restores"

# Mirrored from lib/kube_client.py; the two test suites hold the same vectors.
GUARDED_PATCH_CONTENT_TYPE = "application/json-patch+json"
GUARDED_PATCH_REPLACEMENT_MANAGED_CLUSTERS_BACKUP_NAME = "latest"
# A failed `test` operation or a conditional conflict: the atomic PATCH mutated nothing.
GUARDED_PATCH_CONFLICT_STATUSES = frozenset({409, 412, 422})
GUARDED_DELETE_CONFLICT_STATUSES = frozenset({409, 412})
_MANAGED_CLUSTERS_BACKUP_NAME_PATH = "/spec/veleroManagedClustersBackupName"
_PATCH_ONLY_OPTIONS = ("expected_managed_clusters_backup_name", "replacement_managed_clusters_backup_name")

REASON_OK = "ok"
REASON_PREDICTED = "predicted"
REASON_PRECONDITION_FAILED = "precondition_failed"
REASON_NOT_FOUND = "not_found"
REASON_UNVERIFIABLE = "unverifiable"
REASON_INVALID_INPUT = "invalid_input"
REASON_MALFORMED_RESPONSE = "malformed_response"


def _argument_spec() -> dict:
    return {
        "kubeconfig": {"type": "str", "required": True},
        "context": {"type": "str", "required": True},
        "action": {"type": "str", "required": True, "choices": ["patch", "delete"]},
        "api_version": {"type": "str", "required": True, "choices": [RESTORE_API_VERSION]},
        "kind": {"type": "str", "required": True, "choices": [RESTORE_KIND]},
        "namespace": {"type": "str", "required": True},
        "name": {"type": "str", "required": True},
        "expected_uid": {"type": "str", "required": True},
        "expected_resource_version": {"type": "str", "required": True},
        "expected_managed_clusters_backup_name": {"type": "str", "required": False},
        "replacement_managed_clusters_backup_name": {
            "type": "str",
            "required": False,
            "choices": [GUARDED_PATCH_REPLACEMENT_MANAGED_CLUSTERS_BACKUP_NAME],
        },
        "request_timeout": {"type": "float", "required": False},
    }


def build_guarded_restore_patch(
    *,
    uid: str,
    resource_version: str,
    expected_managed_clusters_backup_name: str,
    replacement_managed_clusters_backup_name: str,
) -> list[dict]:
    """The RFC 6902 document: three exact `test` operations, then the `replace`.

    Values are compared exactly; none is trimmed or normalized here.
    """
    for field, value in (
        ("expected_uid", uid),
        ("expected_resource_version", resource_version),
        ("expected_managed_clusters_backup_name", expected_managed_clusters_backup_name),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"A non-empty {field} is required for a guarded Restore patch.")
    if replacement_managed_clusters_backup_name != GUARDED_PATCH_REPLACEMENT_MANAGED_CLUSTERS_BACKUP_NAME:
        raise ValueError("The guarded Restore patch only replaces veleroManagedClustersBackupName with 'latest'.")
    return [
        {"op": "test", "path": "/metadata/uid", "value": uid},
        {"op": "test", "path": "/metadata/resourceVersion", "value": resource_version},
        {"op": "test", "path": _MANAGED_CLUSTERS_BACKUP_NAME_PATH, "value": expected_managed_clusters_backup_name},
        {
            "op": "replace",
            "path": _MANAGED_CLUSTERS_BACKUP_NAME_PATH,
            "value": replacement_managed_clusters_backup_name,
        },
    ]


def classify_failure(exc: BaseException, conflict_statuses: frozenset) -> tuple[bool, str]:
    """(conflict, reason) for a failed request. Nothing from the exception is returned."""
    status = api_status(exc)
    if status in conflict_statuses:
        return True, REASON_PRECONDITION_FAILED
    if status == 404:
        return False, REASON_NOT_FOUND
    return False, REASON_UNVERIFIABLE


def _base_result(action: str) -> dict:
    result = {
        "accepted": False,
        "changed": False,
        "would_change": False,
        "conflict": False,
        "reason": REASON_UNVERIFIABLE,
        "uid": None,
        "resource_version": None,
    }
    if action == "patch":
        result.update(generation=None, generation_reported=False)
    return result


def _response_mapping(response):
    if callable(getattr(response, "to_dict", None)):
        response = response.to_dict()
    return response if isinstance(response, dict) else None


def _record_patch_response(result: dict, response, expected_uid: str) -> None:
    """Copy only the accepted response's own identity; never infer a missing value."""
    result["reason"] = REASON_MALFORMED_RESPONSE
    body = _response_mapping(response)
    metadata = body.get("metadata") if body is not None else None
    if not isinstance(metadata, dict):
        return
    uid = metadata.get("uid")
    revision = metadata.get("resourceVersion")
    generation = metadata.get("generation")
    result["uid"] = uid if isinstance(uid, str) and uid else None
    result["resource_version"] = revision if isinstance(revision, str) and revision else None
    if isinstance(generation, int) and not isinstance(generation, bool):
        result.update(generation=generation, generation_reported=True)
    if result["uid"] == expected_uid and result["resource_version"] is not None:
        result["reason"] = REASON_OK


def _resolve_restore_resource(kubeconfig: str, context: str, request_timeout):
    """The explicitly routed client, resolved to the Restore resource by live discovery."""
    client = build_dynamic_client(kubeconfig, context, request_timeout)
    return client.resources.get(api_version=RESTORE_API_VERSION, kind=RESTORE_KIND, name=RESTORE_RESOURCE_NAME)


def _validated_request(params: dict):
    """(request_timeout, patch_ops, would_change), or ValueError for inputs that do not fit the action."""
    action = params["action"]
    if action == "delete" and any(params.get(option) is not None for option in _PATCH_ONLY_OPTIONS):
        raise ValueError(
            "expected_managed_clusters_backup_name and replacement_managed_clusters_backup_name "
            "are accepted only with action=patch."
        )
    for option in ("namespace", "expected_uid", "expected_resource_version"):
        if not params[option]:
            raise ValueError(f"{option} may not be empty.")
    request_timeout = normalize_timeout(params.get("request_timeout"), "request_timeout", DEFAULT_REQUEST_TIMEOUT)
    if action == "delete":
        return request_timeout, None, True
    patch_ops = build_guarded_restore_patch(
        uid=params["expected_uid"],
        resource_version=params["expected_resource_version"],
        expected_managed_clusters_backup_name=params["expected_managed_clusters_backup_name"],
        replacement_managed_clusters_backup_name=params["replacement_managed_clusters_backup_name"],
    )
    would_change = params["expected_managed_clusters_backup_name"] != params["replacement_managed_clusters_backup_name"]
    return request_timeout, patch_ops, would_change


def _submit(resource, params: dict, patch_ops):
    """The one guarded request. Returns the PATCH response; a DELETE returns None."""
    if params["action"] == "patch":
        return resource.patch(
            name=params["name"],
            namespace=params["namespace"],
            body=patch_ops,
            content_type=GUARDED_PATCH_CONTENT_TYPE,
        )
    resource.delete(
        name=params["name"],
        namespace=params["namespace"],
        body={
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {
                "uid": params["expected_uid"],
                "resourceVersion": params["expected_resource_version"],
            },
        },
    )
    return None


def _failure_message(action: str, name: str, conflict: bool, reason: str) -> str:
    if conflict:
        return (
            f"Refusing to {action} {name}: the live Restore is no longer the proved object, "
            "and it was left unchanged. Re-prove it from a fresh read; never retry without the guard."
        )
    if reason == REASON_NOT_FOUND:
        return f"{name} was absent when the guarded {action} arrived; nothing was mutated."
    return f"The guarded {action} of {name} did not complete verifiably; it is not accepted."


def run_module(module: AnsibleModule) -> None:
    params = module.params
    action = params["action"]
    result = _base_result(action)

    def fail(msg: str, **updates) -> None:
        result.update(updates)
        module.fail_json(msg=msg, **result)

    try:
        request_timeout, patch_ops, would_change = _validated_request(params)
    except ValueError as exc:
        fail(str(exc), reason=REASON_INVALID_INPUT)
        return

    if module.check_mode:
        # No client, no discovery and no request: the prediction is all check mode reports.
        result.update(would_change=would_change, reason=REASON_PREDICTED)
        module.exit_json(**result)
        return

    try:
        resource = _resolve_restore_resource(params["kubeconfig"], params["context"], request_timeout)
        validate_namespace_scope(resource, params["namespace"])
    except GuardedDeleteError as exc:
        fail(exc.message, reason=REASON_UNVERIFIABLE)
        return
    except Exception:  # noqa: BLE001
        # Deliberately does not interpolate the exception: discovery failures echo
        # request content, and the kubeconfig path is itself an infrastructure detail.
        fail(
            f"Cannot resolve Restore on the target hub, so {params['name']} was not mutated.",
            reason=REASON_UNVERIFIABLE,
        )
        return

    try:
        response = _submit(resource, params, patch_ops)
    except Exception as exc:  # noqa: BLE001 -- classified immediately, never propagated raw
        conflict_statuses = GUARDED_PATCH_CONFLICT_STATUSES if action == "patch" else GUARDED_DELETE_CONFLICT_STATUSES
        conflict, reason = classify_failure(exc, conflict_statuses)
        fail(_failure_message(action, params["name"], conflict, reason), conflict=conflict, reason=reason)
        return

    result.update(accepted=True, changed=would_change)
    if action == "patch":
        _record_patch_response(result, response, params["expected_uid"])
    else:
        result.update(
            reason=REASON_OK,
            uid=params["expected_uid"],
            resource_version=params["expected_resource_version"],
        )
    module.exit_json(**result)


def main() -> None:
    module = AnsibleModule(
        argument_spec=_argument_spec(),
        required_if=[("action", "patch", _PATCH_ONLY_OPTIONS)],
        supports_check_mode=True,
    )
    try:
        run_module(module)
    except SystemExit:
        raise
    except Exception:
        # Programmer and setup surprises stay inside the sanitized contract rather
        # than surfacing a traceback that may carry request or credential material.
        module.fail_json(
            msg="The guarded Restore mutation failed before it could verify anything.",
            **_base_result(module.params.get("action")),
        )


if __name__ == "__main__":
    main()
