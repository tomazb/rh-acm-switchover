# SPDX-License-Identifier: MIT
"""Unit tests for acm_restore_guarded_mutation: one guarded Restore PATCH or DELETE.

The module submits exactly one server-guarded request. A failed JSON Patch test or a
delete precondition conflict means the live object is not the proved one: it is a
no-mutation conflict, never retried and never replaced by an unguarded request.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from ansible.module_utils import basic
from ansible.module_utils.basic import AnsibleModule as RealAnsibleModule
from kubernetes.client import ApiClient, Configuration
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import ProtocolError, ReadTimeoutError
from urllib3.response import HTTPResponse

from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_restore_guarded_mutation as module

# Unit expectations for this module (R4-04 Task 4). Equality with the Python
# KubeClient helpers is enforced by tests/test_restore_guarded_mutation_parity.py at the
# repository root, which drives both form factors.
GUARDED_RESTORE_PATCH_VECTOR = {
    "uid": "3f0c2a4e-uid",
    "resource_version": "48213",
    "expected_managed_clusters_backup_name": " Skip ",
    "replacement_managed_clusters_backup_name": "latest",
    "content_type": "application/json-patch+json",
    "patch": [
        {"op": "test", "path": "/metadata/uid", "value": "3f0c2a4e-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "48213"},
        {"op": "test", "path": "/spec/veleroManagedClustersBackupName", "value": " Skip "},
        {"op": "replace", "path": "/spec/veleroManagedClustersBackupName", "value": "latest"},
    ],
}
# HTTP status -> (conflict, reason) for the guarded PATCH. Every other ApiException status
# and every non-API failure (timeout, transport) is (False, "unverifiable").
GUARDED_RESTORE_PATCH_STATUS_VECTOR = {
    409: (True, "precondition_failed"),
    412: (True, "precondition_failed"),
    422: (True, "precondition_failed"),
    404: (False, "not_found"),
    403: (False, "unverifiable"),
    500: (False, "unverifiable"),
}
# HTTP status -> (conflict, reason) for the preconditioned DELETE.
GUARDED_RESTORE_DELETE_STATUS_VECTOR = {
    409: (True, "precondition_failed"),
    412: (True, "precondition_failed"),
    404: (False, "not_found"),
    500: (False, "unverifiable"),
}

_SECRET = "R4-04-GUARDED-MUTATION-SECRET-BODY token=abc123 /home/op/.kube/config"
_API_VERSION = "cluster.open-cluster-management.io/v1beta1"
_NAMESPACE = "open-cluster-management-backup"
_NAME = "restore-acm-passive-sync"


class ModuleExit(SystemExit):
    def __init__(self, results: dict) -> None:
        super().__init__(results)
        self.results = results


class ModuleFail(SystemExit):
    def __init__(self, results: dict) -> None:
        super().__init__(results)
        self.results = results


class _RawResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data


class FakeResource:
    """A dynamic-client Restore resource that records every request it receives."""

    def __init__(self, *, patch_result: Any = None, delete_result: Any = None, namespaced: bool = True) -> None:
        self.patch_result = patch_result
        self.delete_result = delete_result
        self.namespaced = namespaced
        self.calls: list[tuple[str, dict]] = []

    def _answer(self, result: Any) -> Any:
        if isinstance(result, BaseException):
            raise result
        return result

    def patch(self, **kwargs):
        """Answers like the dynamic client with ``serialize=False``: the raw HTTP response,
        whose ``data`` is the body. Bytes pass through; anything else is JSON-encoded."""
        self.calls.append(("patch", kwargs))
        result = self._answer(self.patch_result)
        if isinstance(result, bytes):
            return _RawResponse(result)
        if result is None or isinstance(result, (dict, list, str)):
            return _RawResponse(json.dumps(result).encode("utf-8"))
        # A response object is returned untouched, so a lazy `data` is read by the module.
        return result

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return self._answer(self.delete_result)

    def get(self, **kwargs):  # pragma: no cover - a call here is itself the failure
        raise AssertionError("the guarded mutation module owns no read")


def _patch_args(**overrides) -> dict:
    args = {
        "kubeconfig": "/home/op/.kube/config",
        "context": "secondary-hub",
        "action": "patch",
        "api_version": _API_VERSION,
        "kind": "Restore",
        "namespace": _NAMESPACE,
        "name": _NAME,
        "expected_uid": GUARDED_RESTORE_PATCH_VECTOR["uid"],
        "expected_resource_version": GUARDED_RESTORE_PATCH_VECTOR["resource_version"],
        "expected_managed_clusters_backup_name": GUARDED_RESTORE_PATCH_VECTOR["expected_managed_clusters_backup_name"],
        "replacement_managed_clusters_backup_name": "latest",
    }
    args.update(overrides)
    return {key: value for key, value in args.items() if value is not None}


def _delete_args(**overrides) -> dict:
    args = _patch_args(action="delete")
    del args["expected_managed_clusters_backup_name"]
    del args["replacement_managed_clusters_backup_name"]
    args.update(overrides)
    return args


def _run(monkeypatch, args: dict, *, resource: FakeResource | None = None, check_mode: bool = False) -> dict:
    """Run main() through the real AnsibleModule so the argument spec is exercised."""

    class CapturingAnsibleModule(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise ModuleExit(kwargs)

        def fail_json(self, **kwargs):
            kwargs["failed"] = True
            raise ModuleFail(kwargs)

    resolved: list[tuple] = []

    def _fake_resolve(kubeconfig, context, request_timeout):
        resolved.append((kubeconfig, context, request_timeout))
        if resource is None:
            raise AssertionError("no client may be built here")
        return resource

    monkeypatch.setattr(module, "AnsibleModule", CapturingAnsibleModule)
    monkeypatch.setattr(module, "_resolve_restore_resource", _fake_resolve)
    module_args = dict(args)
    if check_mode:
        module_args["_ansible_check_mode"] = True
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": module_args}).encode("utf-8"))
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")

    try:
        module.main()
    except ModuleExit as exc:
        result = exc.results
    except ModuleFail as exc:
        result = exc.results
    else:  # pragma: no cover
        raise AssertionError("module returned without exit_json or fail_json")
    result["_resolved"] = resolved
    return result


def _accepted_restore(metadata: Any) -> dict:
    return {"apiVersion": _API_VERSION, "kind": "Restore", "metadata": metadata}


# --- parity vectors -------------------------------------------------------------------


def test_the_patch_document_is_the_shared_parity_vector():
    assert (
        module.build_guarded_restore_patch(
            uid=GUARDED_RESTORE_PATCH_VECTOR["uid"],
            resource_version=GUARDED_RESTORE_PATCH_VECTOR["resource_version"],
            expected_managed_clusters_backup_name=GUARDED_RESTORE_PATCH_VECTOR["expected_managed_clusters_backup_name"],
            replacement_managed_clusters_backup_name="latest",
        )
        == GUARDED_RESTORE_PATCH_VECTOR["patch"]
    )
    assert module.GUARDED_PATCH_CONTENT_TYPE == GUARDED_RESTORE_PATCH_VECTOR["content_type"]


@pytest.mark.parametrize("status", sorted(GUARDED_RESTORE_PATCH_STATUS_VECTOR))
def test_patch_failures_follow_the_shared_classification_and_are_never_retried(monkeypatch, status):
    resource = FakeResource(patch_result=ApiException(status=status, reason="Rejected"))
    result = _run(monkeypatch, _patch_args(), resource=resource)

    conflict, reason = GUARDED_RESTORE_PATCH_STATUS_VECTOR[status]
    assert result["failed"] is True
    assert (result["conflict"], result["reason"]) == (conflict, reason)
    assert (result["changed"], result["accepted"]) == (False, False)
    assert result["uid"] is None and result["generation"] is None
    assert [name for name, _ in resource.calls] == ["patch"]


@pytest.mark.parametrize("status", sorted(GUARDED_RESTORE_DELETE_STATUS_VECTOR))
def test_delete_failures_follow_the_shared_classification_and_are_never_retried(monkeypatch, status):
    resource = FakeResource(delete_result=ApiException(status=status, reason="Rejected"))
    result = _run(monkeypatch, _delete_args(), resource=resource)

    conflict, reason = GUARDED_RESTORE_DELETE_STATUS_VECTOR[status]
    assert result["failed"] is True
    assert (result["conflict"], result["reason"]) == (conflict, reason)
    assert (result["changed"], result["accepted"]) == (False, False)
    assert [name for name, _ in resource.calls] == ["delete"]


# --- the guarded patch ------------------------------------------------------------------


def test_one_json_patch_request_carries_every_test_before_the_replace(monkeypatch):
    resource = FakeResource(
        patch_result=_accepted_restore({"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 7})
    )
    _run(monkeypatch, _patch_args(), resource=resource)

    assert len(resource.calls) == 1
    verb, kwargs = resource.calls[0]
    assert verb == "patch"
    assert kwargs["name"] == _NAME
    assert kwargs["namespace"] == _NAMESPACE
    assert kwargs["content_type"] == GUARDED_RESTORE_PATCH_VECTOR["content_type"]
    # The client library silently downgrades a non-list body to a strategic merge patch.
    assert isinstance(kwargs["body"], list)
    assert kwargs["body"] == GUARDED_RESTORE_PATCH_VECTOR["patch"]


def test_an_accepted_patch_exposes_the_response_identity_and_generation(monkeypatch):
    resource = FakeResource(
        patch_result=_accepted_restore({"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 7})
    )
    result = _run(monkeypatch, _patch_args(), resource=resource)
    result.pop("_resolved")
    assert result == {
        "accepted": True,
        "http_accepted": True,
        "changed": True,
        "would_change": False,
        "conflict": False,
        "reason": "ok",
        "uid": "3f0c2a4e-uid",
        "resource_version": "48300",
        "generation": 7,
        "generation_reported": True,
    }


def test_the_patch_response_is_read_raw_and_decoded_by_the_module(monkeypatch):
    body = json.dumps(_accepted_restore({"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 3}))
    resource = FakeResource(patch_result=_RawResponse(body.encode("utf-8")))
    result = _run(monkeypatch, _patch_args(), resource=resource)
    assert resource.calls[0][1]["serialize"] is False
    assert (result["reason"], result["generation"]) == ("ok", 3)


@pytest.mark.parametrize("generation", [None, "7", True, 7.0])
def test_a_missing_or_non_integer_generation_is_reported_never_invented(monkeypatch, generation):
    metadata = {"uid": "3f0c2a4e-uid", "resourceVersion": "48300"}
    if generation is not None:
        metadata["generation"] = generation
    result = _run(monkeypatch, _patch_args(), resource=FakeResource(patch_result=_accepted_restore(metadata)))
    assert result["accepted"] is True
    assert result["generation"] is None
    assert result["generation_reported"] is False


@pytest.mark.parametrize(
    "response",
    [
        _accepted_restore({"resourceVersion": "48300", "generation": 7}),
        _accepted_restore({"uid": "", "resourceVersion": "48300", "generation": 7}),
        _accepted_restore({"uid": "3f0c2a4e-uid", "generation": 7}),
        _accepted_restore({"uid": "3f0c2a4e-uid", "resourceVersion": "", "generation": 7}),
        _accepted_restore({"uid": "3f0c2a4e-uid", "resourceVersion": 48300, "generation": 7}),
        _accepted_restore({"uid": "a-different-uid", "resourceVersion": "48300", "generation": 7}),
        _accepted_restore("not-a-mapping"),
        "not-a-mapping",
        None,
        [],
        b"<html>",
        b"\xff\xfe",
    ],
)
def test_a_malformed_response_identity_fails_closed_with_no_generation_evidence(monkeypatch, response):
    """July §1 step 3 recovers only a missing generation under a valid identity; a missing or
    different UID, or a missing resourceVersion, is never verified acceptance."""
    result = _run(monkeypatch, _patch_args(), resource=FakeResource(patch_result=response))
    result.pop("_resolved")
    msg = result.pop("msg")
    assert "abc123" not in msg
    assert result == {
        "failed": True,
        "accepted": False,
        "http_accepted": True,
        "changed": False,
        "would_change": False,
        "conflict": False,
        "reason": "malformed_response",
        "uid": None,
        "resource_version": None,
        "generation": None,
        "generation_reported": False,
    }


@pytest.mark.parametrize("exc", [ReadTimeoutError(None, "/restores", "read timed out"), ProtocolError("reset")])
def test_a_failure_reading_the_accepted_response_is_not_acceptance(monkeypatch, exc):
    """The raw body is read after the request returns; a failure there says nothing about
    whether the PATCH was applied."""

    class _DeferredBody:
        @property
        def data(self):
            raise exc

    result = _run(monkeypatch, _patch_args(), resource=FakeResource(patch_result=_DeferredBody()))
    assert result["failed"] is True
    # Classified at the request boundary, not rescued by main()'s generic catch-all.
    assert "did not complete verifiably" in result["msg"]
    assert (result["accepted"], result["http_accepted"], result["changed"], result["reason"]) == (
        False,
        False,
        False,
        "unverifiable",
    )
    assert (result["generation"], result["generation_reported"]) == (None, False)


def test_replacing_an_already_latest_value_is_accepted_but_unchanged(monkeypatch):
    resource = FakeResource(
        patch_result=_accepted_restore({"uid": "3f0c2a4e-uid", "resourceVersion": "48213", "generation": 6})
    )
    result = _run(monkeypatch, _patch_args(expected_managed_clusters_backup_name="latest"), resource=resource)
    assert (result["accepted"], result["changed"]) == (True, False)


@pytest.mark.parametrize("exc", [TimeoutError("read timed out"), OSError("reset"), ValueError("x")])
def test_a_timeout_or_transport_failure_is_not_acceptance(monkeypatch, exc):
    resource = FakeResource(patch_result=exc)
    result = _run(monkeypatch, _patch_args(), resource=resource)
    assert result["failed"] is True
    assert (result["accepted"], result["changed"], result["conflict"], result["reason"]) == (
        False,
        False,
        False,
        "unverifiable",
    )
    assert len(resource.calls) == 1


# --- the guarded delete -----------------------------------------------------------------


def test_delete_sends_both_uid_and_resource_version_preconditions(monkeypatch):
    resource = FakeResource(delete_result={"kind": "Status", "status": "Success"})
    result = _run(monkeypatch, _delete_args(), resource=resource)

    assert resource.calls == [
        (
            "delete",
            {
                "name": _NAME,
                "namespace": _NAMESPACE,
                "body": {
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "preconditions": {"uid": "3f0c2a4e-uid", "resourceVersion": "48213"},
                },
            },
        )
    ]
    result.pop("_resolved")
    assert result == {
        "accepted": True,
        "http_accepted": True,
        "changed": True,
        "would_change": False,
        "conflict": False,
        "reason": "ok",
        "uid": "3f0c2a4e-uid",
        "resource_version": "48213",
    }


# --- check mode -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "would_change"),
    [
        (_patch_args(), True),
        (_patch_args(expected_managed_clusters_backup_name="skip"), True),
        (_patch_args(expected_managed_clusters_backup_name="latest"), False),
        (_delete_args(), True),
    ],
)
def test_check_mode_makes_no_api_call_and_reports_the_prediction_separately(monkeypatch, args, would_change):
    result = _run(monkeypatch, args, resource=None, check_mode=True)
    assert result["_resolved"] == []
    result.pop("_resolved")
    expected = {
        "accepted": False,
        "http_accepted": False,
        "changed": False,
        "would_change": would_change,
        "conflict": False,
        "reason": "predicted",
        "uid": None,
        "resource_version": None,
    }
    if args["action"] == "patch":
        expected.update(generation=None, generation_reported=False)
    assert result == expected


# --- argument contract ------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        _patch_args(action="replace"),
        _patch_args(api_version="cluster.open-cluster-management.io/v1"),
        _patch_args(kind="BackupSchedule"),
        _patch_args(replacement_managed_clusters_backup_name="Latest"),
        _patch_args(replacement_managed_clusters_backup_name="acm-managed-clusters-schedule-20260930101010"),
        _patch_args(replacement_managed_clusters_backup_name="skip"),
        _patch_args(expected_managed_clusters_backup_name=None),
        _patch_args(replacement_managed_clusters_backup_name=None),
        _patch_args(expected_managed_clusters_backup_name=""),
        _patch_args(expected_uid=""),
        _patch_args(expected_resource_version=""),
        _patch_args(namespace=""),
        _patch_args(namespace=None),
        _patch_args(kubeconfig=None),
        _patch_args(context=None),
        _patch_args(expected_resource_version=None),
        _delete_args(expected_managed_clusters_backup_name="skip"),
        _delete_args(replacement_managed_clusters_backup_name="latest"),
        _delete_args(expected_uid=""),
        _delete_args(expected_resource_version=None),
        _patch_args(expected_uid=" 3f0c2a4e-uid"),
        _patch_args(expected_resource_version="48213\n"),
        _delete_args(expected_uid="3f0c2a4e-uid "),
        _delete_args(expected_resource_version=" 48213"),
        _patch_args(unexpected_option="x"),
    ],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_parameters_that_do_not_fit_the_action_are_rejected_before_any_request(monkeypatch, args, check_mode):
    resource = FakeResource()
    result = _run(monkeypatch, args, resource=resource, check_mode=check_mode)
    assert result["failed"] is True
    # The argument spec's own rejections carry no `changed`; the module's always do.
    assert result.get("changed", False) is False
    assert result["_resolved"] == []
    assert resource.calls == []


def test_a_namespace_scope_mismatch_is_refused_before_the_request(monkeypatch):
    resource = FakeResource(namespaced=False)
    result = _run(monkeypatch, _patch_args(), resource=resource)
    assert (result["failed"], result["reason"]) == (True, "unverifiable")
    assert resource.calls == []


def test_an_unresolvable_client_fails_closed_without_detail(monkeypatch):
    class CapturingAnsibleModule(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise ModuleExit(kwargs)

        def fail_json(self, **kwargs):
            raise ModuleFail(kwargs)

    def _boom(*_args, **_kwargs):
        raise RuntimeError(_SECRET)

    monkeypatch.setattr(module, "AnsibleModule", CapturingAnsibleModule)
    monkeypatch.setattr(module, "build_dynamic_client", _boom)
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": _patch_args()}).encode("utf-8"))
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    with pytest.raises(ModuleFail) as excinfo:
        module.main()
    assert excinfo.value.results["reason"] == "unverifiable"
    assert "abc123" not in json.dumps(excinfo.value.results)
    assert "/home/op/.kube/config" not in json.dumps(excinfo.value.results)


# --- sanitisation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "verb", "exc"),
    [
        (_patch_args(), "patch", ApiException(status=409, reason=_SECRET)),
        (_patch_args(), "patch", ApiException(status=422, reason=_SECRET)),
        (_patch_args(), "patch", ApiException(status=500, reason=_SECRET)),
        (_patch_args(), "patch", OSError(_SECRET)),
        (_delete_args(), "delete", ApiException(status=409, reason=_SECRET)),
        (_delete_args(), "delete", RuntimeError(_SECRET)),
    ],
)
def test_failures_never_expose_the_response_body_or_credentials(monkeypatch, args, verb, exc):
    if isinstance(exc, ApiException):
        exc.body = _SECRET
    resource = FakeResource(**{f"{verb}_result": exc})
    result = _run(monkeypatch, args, resource=resource)
    result.pop("_resolved")
    rendered = json.dumps(result)
    assert "abc123" not in rendered
    assert "R4-04-GUARDED-MUTATION-SECRET-BODY" not in rendered
    assert "/home/op/.kube/config" not in rendered


# --- wire level -------------------------------------------------------------------------


def _wire_client(monkeypatch, tmp_path, answer):
    """A real ApiClient/DynamicClient whose transport records every request."""
    group, version = _API_VERSION.split("/")
    routes = {
        "/version": {"major": "1", "minor": "35", "gitVersion": "v1.35.0"},
        "/apis": {
            "kind": "APIGroupList",
            "groups": [
                {
                    "name": group,
                    "versions": [{"groupVersion": _API_VERSION, "version": version}],
                    "preferredVersion": {"groupVersion": _API_VERSION, "version": version},
                }
            ],
        },
        f"/apis/{_API_VERSION}": {
            "kind": "APIResourceList",
            "groupVersion": _API_VERSION,
            "resources": [{"name": "restores", "kind": "Restore", "namespaced": True, "verbs": ["patch", "delete"]}],
        },
    }
    configuration = Configuration()
    configuration.host = "https://hub.invalid"
    configuration.verify_ssl = False
    api_client = ApiClient(configuration)
    requests: list[dict] = []

    def transport(method, url, **kwargs):
        path = urlsplit(url).path
        if path in routes:
            return HTTPResponse(
                body=json.dumps(routes[path]).encode(), status=200, headers={"Content-Type": "application/json"}
            )
        requests.append({"method": method, "path": path, **kwargs})
        status, body = answer
        return HTTPResponse(body=json.dumps(body).encode(), status=status, headers={"Content-Type": "application/json"})

    import tempfile

    import kubernetes.config as k8s_config

    monkeypatch.setattr(k8s_config, "new_client_from_config", lambda **_kwargs: api_client)
    monkeypatch.setattr(api_client.rest_client.pool_manager, "request", transport)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return requests


def _run_wire(monkeypatch, args):
    class CapturingAnsibleModule(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise ModuleExit(kwargs)

        def fail_json(self, **kwargs):
            kwargs["failed"] = True
            raise ModuleFail(kwargs)

    monkeypatch.setattr(module, "AnsibleModule", CapturingAnsibleModule)
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode("utf-8"))
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    try:
        module.main()
    except (ModuleExit, ModuleFail) as exc:
        return exc.results
    raise AssertionError("module returned without exit_json or fail_json")  # pragma: no cover


def test_the_wire_request_is_a_json_patch_list_with_its_content_type(monkeypatch, tmp_path):
    accepted = _accepted_restore({"uid": "3f0c2a4e-uid", "resourceVersion": "48300", "generation": 7})
    requests = _wire_client(monkeypatch, tmp_path, (200, accepted))

    result = _run_wire(monkeypatch, _patch_args())

    assert len(requests) == 1
    request = requests[0]
    assert request["method"] == "PATCH"
    assert request["path"] == f"/apis/{_API_VERSION}/namespaces/{_NAMESPACE}/restores/{_NAME}"
    assert request["headers"]["Content-Type"] == GUARDED_RESTORE_PATCH_VECTOR["content_type"]
    assert json.loads(request["body"]) == GUARDED_RESTORE_PATCH_VECTOR["patch"]
    assert (result["reason"], result["generation"]) == ("ok", 7)


def test_a_wire_422_test_failure_is_one_request_and_a_conflict(monkeypatch, tmp_path):
    requests = _wire_client(
        monkeypatch, tmp_path, (422, {"kind": "Status", "message": _SECRET, "reason": "Invalid", "code": 422})
    )
    result = _run_wire(monkeypatch, _patch_args())
    assert [request["method"] for request in requests] == ["PATCH"]
    assert (result["failed"], result["conflict"], result["reason"]) == (True, True, "precondition_failed")
    assert "abc123" not in json.dumps(result)


def test_the_wire_delete_carries_both_preconditions(monkeypatch, tmp_path):
    requests = _wire_client(monkeypatch, tmp_path, (200, {"kind": "Status", "status": "Success"}))
    result = _run_wire(monkeypatch, _delete_args())
    assert [request["method"] for request in requests] == ["DELETE"]
    assert json.loads(requests[0]["body"])["preconditions"] == {"uid": "3f0c2a4e-uid", "resourceVersion": "48213"}
    assert result["changed"] is True


# --- documentation ----------------------------------------------------------------------


def test_documentation_matches_the_argument_spec():
    documentation = yaml.safe_load(module.DOCUMENTATION)
    spec = module._argument_spec()
    assert set(documentation["options"]) == set(spec)
    for option, definition in spec.items():
        doc = documentation["options"][option]
        assert doc["type"] == definition["type"], option
        assert doc.get("required", False) == definition.get("required", False), option
        assert doc.get("choices") == definition.get("choices"), option
    yaml.safe_load(module.EXAMPLES)
    assert set(yaml.safe_load(module.RETURN)) >= {
        "http_accepted",
        "changed",
        "would_change",
        "conflict",
        "accepted",
        "reason",
        "uid",
        "resource_version",
        "generation",
        "generation_reported",
    }


def test_a_wire_response_without_kind_is_decoded_by_the_module_not_the_client(monkeypatch, tmp_path):
    """The client's own deserializer raises on an object without `kind`, which would read as
    an unverifiable failure; the module decodes the raw body itself, as the Python helper does."""
    _wire_client(monkeypatch, tmp_path, (200, {"metadata": {"uid": "another-uid", "resourceVersion": "9"}}))
    result = _run_wire(monkeypatch, _patch_args())
    assert (result["failed"], result["accepted"], result["http_accepted"], result["reason"]) == (
        True,
        False,
        True,
        "malformed_response",
    )
