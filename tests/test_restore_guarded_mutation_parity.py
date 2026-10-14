"""Parity contract: the guarded Restore mutations must agree across form factors.

Identical inputs are fed to the Python helpers (KubeClient.json_patch_custom_resource_guarded
and KubeClient.delete_restore_guarded) and to the collection's acm_restore_guarded_mutation
module, and their wire requests and result classifications are compared. The two runtimes
share no code; this file is what keeps them equal.
"""

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from ansible.module_utils import basic
from ansible.module_utils.basic import AnsibleModule as RealAnsibleModule
from kubernetes.client.rest import ApiException
from kubernetes.dynamic.exceptions import api_exception
from urllib3.exceptions import ProtocolError, ReadTimeoutError

import lib.kube_client as python_form
from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_restore_guarded_mutation as collection_form
from lib.validation import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent

NAME = "restore-acm-passive-sync"
NAMESPACE = "open-cluster-management-backup"
UID = "3f0c2a4e-uid"
RESOURCE_VERSION = "48213"

# The keys both form factors publish. Python adds `dry_run`; the module adds `failed`/`msg`.
COMPARED_KEYS = (
    "accepted",
    "http_accepted",
    "changed",
    "would_change",
    "conflict",
    "reason",
    "uid",
    "resource_version",
    "generation",
    "generation_reported",
)


def _restore(metadata):
    return json.dumps(
        {"apiVersion": "cluster.open-cluster-management.io/v1beta1", "kind": "Restore", "metadata": metadata}
    )


# (vector id, how the one request ends). `status` is an API error, `raise` a failure of the
# request itself, `body` a 2xx with the given raw body, `deferred` a 2xx whose body read fails.
PATCH_VECTORS = [
    ("accepted", ("body", _restore({"uid": UID, "resourceVersion": "48300", "generation": 7}).encode())),
    ("accepted_without_generation", ("body", _restore({"uid": UID, "resourceVersion": "48300"}).encode())),
    (
        "accepted_bool_generation",
        ("body", _restore({"uid": UID, "resourceVersion": "48300", "generation": True}).encode()),
    ),
    ("conflict_409", ("status", 409)),
    ("conflict_412", ("status", 412)),
    ("test_failed_422", ("status", 422)),
    ("not_found_404", ("status", 404)),
    ("forbidden_403", ("status", 403)),
    ("server_error_500", ("status", 500)),
    ("timeout", ("raise", lambda: ReadTimeoutError(None, "/restores", "read timed out"))),
    ("transport", ("raise", lambda: ProtocolError("connection reset"))),
    ("body_read_timeout", ("deferred", lambda: ReadTimeoutError(None, "/restores", "read timed out"))),
    ("body_read_transport", ("deferred", lambda: ProtocolError("connection reset"))),
    ("malformed_missing_uid", ("body", _restore({"resourceVersion": "48300", "generation": 7}).encode())),
    ("malformed_other_uid", ("body", _restore({"uid": "other", "resourceVersion": "48300", "generation": 7}).encode())),
    ("malformed_missing_rv", ("body", _restore({"uid": UID, "generation": 7}).encode())),
    ("malformed_empty_rv", ("body", _restore({"uid": UID, "resourceVersion": "", "generation": 7}).encode())),
    ("malformed_numeric_rv", ("body", _restore({"uid": UID, "resourceVersion": 48300, "generation": 7}).encode())),
    ("malformed_metadata", ("body", _restore("not-a-mapping").encode())),
    ("malformed_without_kind", ("body", json.dumps({"metadata": {"uid": "other", "resourceVersion": "1"}}).encode())),
    ("malformed_undecodable", ("body", b"<html>")),
    ("malformed_list", ("body", b"[]")),
    ("malformed_null", ("body", b"null")),
]

DELETE_VECTORS = [
    ("accepted", ("body", json.dumps({"kind": "Status", "status": "Success"}).encode())),
    ("accepted_object", ("body", _restore({"uid": UID, "resourceVersion": "48300"}).encode())),
    ("malformed_undecodable", ("body", b"<html>")),
    ("malformed_invalid_utf8", ("body", b"\xff\xfe")),
    ("malformed_list", ("body", b"[]")),
    ("malformed_null", ("body", b"null")),
    ("conflict_409", ("status", 409)),
    ("conflict_412", ("status", 412)),
    ("not_found_404", ("status", 404)),
    ("forbidden_403", ("status", 403)),
    ("server_error_500", ("status", 500)),
    ("timeout", ("raise", lambda: ReadTimeoutError(None, "/restores", "read timed out"))),
    ("deferred_timeout", ("deferred", lambda: ReadTimeoutError(None, "/restores", "read timed out"))),
]

PATCH_INPUTS = [
    {"expected_managed_clusters_backup_name": " Skip ", "replacement_managed_clusters_backup_name": "latest"},
    {"expected_managed_clusters_backup_name": "skip", "replacement_managed_clusters_backup_name": "latest"},
    {"expected_managed_clusters_backup_name": "latest", "replacement_managed_clusters_backup_name": "latest"},
]


class _DeferredBody:
    def __init__(self, exc):
        self._exc = exc

    @property
    def data(self):
        raise self._exc


def _python_answer(outcome):
    """Mock side effect/return for KubeClient's raw PATCH call."""
    kind, value = outcome
    if kind == "status":
        return {"side_effect": ApiException(status=value, reason="Rejected")}
    if kind == "raise":
        return {"side_effect": value()}
    if kind == "deferred":
        return {"return_value": _DeferredBody(value())}
    return {"return_value": Mock(data=value)}


def _collection_answer(outcome):
    """What the dynamic client's `serialize=False` request raises or returns."""
    kind, value = outcome
    if kind == "status":
        raise api_exception(ApiException(status=value, reason="Rejected"))
    if kind == "raise":
        raise value()
    if kind == "deferred":
        return _DeferredBody(value())
    return Mock(data=value)


class _Resource:
    namespaced = True

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        return _collection_answer(self.outcome)

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return _collection_answer(self.outcome)

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _collection_answer(self.outcome)


@pytest.fixture
def python_client():
    with patch("lib.kube_client.config.new_client_from_config") as new_client, patch(
        "lib.kube_client.client.CustomObjectsApi"
    ), patch("lib.kube_client.client.CoreV1Api"), patch("lib.kube_client.client.AppsV1Api"):
        # The pre-37 client shape: no `param_serialize`, so requests take the legacy `call_api` path.
        api_client = MagicMock(name="api_client", spec=["call_api", "configuration"])
        new_client.return_value = api_client
        yield python_form.KubeClient(context="secondary-hub", dry_run=False)


class _ModuleExit(SystemExit):
    def __init__(self, results):
        super().__init__(0)
        self.results = results


def _run_collection(monkeypatch, args, resource, *, check_mode=False):
    class CapturingAnsibleModule(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise _ModuleExit(kwargs)

        def fail_json(self, **kwargs):
            raise _ModuleExit({**kwargs, "failed": True})

    module_args = {
        "kubeconfig": "/tmp/kubeconfig",
        "context": "secondary-hub",
        "api_version": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "namespace": NAMESPACE,
        "name": NAME,
        "expected_uid": UID,
        "expected_resource_version": RESOURCE_VERSION,
        **args,
    }
    for option in [key for key, value in args.items() if value is None]:
        del module_args[option]
    if check_mode:
        module_args["_ansible_check_mode"] = True
    monkeypatch.setattr(collection_form, "AnsibleModule", CapturingAnsibleModule)
    monkeypatch.setattr(collection_form, "_resolve_restore_resource", lambda *_args: resource)
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": module_args}).encode("utf-8"))
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    with pytest.raises(_ModuleExit) as exited:
        collection_form.main()
    return exited.value.results


def _compared(result, keys=COMPARED_KEYS):
    return {key: result[key] for key in keys if key in result}


def _python_patch(client, inputs):
    return client.json_patch_custom_resource_guarded(
        NAME, namespace=NAMESPACE, uid=UID, resource_version=RESOURCE_VERSION, **inputs
    )


# --- the wire document ----------------------------------------------------------------


@pytest.mark.parametrize("inputs", PATCH_INPUTS)
def test_both_form_factors_build_the_same_patch_document(inputs):
    identity = {"uid": UID, "resource_version": RESOURCE_VERSION}
    assert python_form.build_guarded_restore_patch(**identity, **inputs) == collection_form.build_guarded_restore_patch(
        **identity, **inputs
    )


@pytest.mark.parametrize("replacement", ["Latest", "skip", "acm-managed-clusters-schedule-20260930101010", ""])
def test_both_form_factors_refuse_a_replacement_other_than_latest(replacement):
    identity = {"uid": UID, "resource_version": RESOURCE_VERSION, "expected_managed_clusters_backup_name": "skip"}
    with pytest.raises(ValidationError):
        python_form.build_guarded_restore_patch(**identity, replacement_managed_clusters_backup_name=replacement)
    with pytest.raises(ValueError):
        collection_form.build_guarded_restore_patch(**identity, replacement_managed_clusters_backup_name=replacement)


@pytest.mark.parametrize("inputs", PATCH_INPUTS)
def test_both_form_factors_send_the_same_request(python_client, monkeypatch, inputs):
    python_client._api_client.call_api = Mock(**_python_answer(PATCH_VECTORS[0][1]))
    _python_patch(python_client, inputs)
    python_call = python_client._api_client.call_api.call_args

    resource = _Resource(PATCH_VECTORS[0][1])
    _run_collection(monkeypatch, {"action": "patch", **inputs}, resource)
    [(verb, collection_call)] = resource.calls

    assert verb == "patch"
    assert python_call.kwargs["body"] == collection_call["body"]
    assert python_call.kwargs["header_params"]["Content-Type"] == collection_call["content_type"]
    assert python_form.GUARDED_PATCH_CONTENT_TYPE == collection_form.GUARDED_PATCH_CONTENT_TYPE


# --- classification -------------------------------------------------------------------


@pytest.mark.parametrize(("vector_id", "outcome"), PATCH_VECTORS, ids=[vector[0] for vector in PATCH_VECTORS])
def test_patch_outcomes_are_classified_identically(python_client, monkeypatch, vector_id, outcome):
    inputs = PATCH_INPUTS[0]
    python_client._api_client.call_api = Mock(**_python_answer(outcome))
    python_result = _python_patch(python_client, inputs)

    resource = _Resource(outcome)
    collection_result = _run_collection(monkeypatch, {"action": "patch", **inputs}, resource)

    assert _compared(python_result) == _compared(collection_result), vector_id
    # Both fail every outcome except verified acceptance, and both sent exactly one request.
    assert collection_result.get("failed", False) is (not python_result["accepted"]), vector_id
    assert python_client._api_client.call_api.call_count == 1
    assert len(resource.calls) == 1


@pytest.mark.parametrize(("vector_id", "outcome"), DELETE_VECTORS, ids=[vector[0] for vector in DELETE_VECTORS])
def test_delete_outcomes_are_classified_identically(python_client, monkeypatch, vector_id, outcome):
    python_client._api_client.call_api = Mock(**_python_answer(outcome))
    python_result = python_client.delete_restore_guarded(
        NAME, namespace=NAMESPACE, uid=UID, resource_version=RESOURCE_VERSION
    )

    resource = _Resource(outcome)
    collection_result = _run_collection(monkeypatch, {"action": "delete"}, resource)

    assert _compared(python_result) == _compared(collection_result), vector_id
    assert collection_result.get("failed", False) is (not python_result["accepted"]), vector_id
    assert python_client._api_client.call_api.call_count == 1 and len(resource.calls) == 1
    assert python_client._api_client.call_api.call_args.kwargs["body"] == resource.calls[0][1]["body"]


class _HubPoolManager:
    """The urllib3 pool manager under a real, installed `ApiClient`: it serves the discovery
    documents the dynamic client reads and answers the one Restore request with a fixed
    status and raw body, so both form factors run the installed client unmocked."""

    _DISCOVERY = {
        "/apis": {
            "kind": "APIGroupList",
            "groups": [
                {
                    "name": "cluster.open-cluster-management.io",
                    "versions": [{"groupVersion": "cluster.open-cluster-management.io/v1beta1", "version": "v1beta1"}],
                    "preferredVersion": {
                        "groupVersion": "cluster.open-cluster-management.io/v1beta1",
                        "version": "v1beta1",
                    },
                }
            ],
        },
        "/api": {"kind": "APIVersions", "versions": ["v1"]},
        "/api/v1": {"kind": "APIResourceList", "groupVersion": "v1", "resources": []},
        "/apis/cluster.open-cluster-management.io/v1beta1": {
            "kind": "APIResourceList",
            "groupVersion": "cluster.open-cluster-management.io/v1beta1",
            "resources": [{"name": "restores", "kind": "Restore", "namespaced": True, "verbs": ["delete"]}],
        },
    }

    def __init__(self, status, body):
        self._status = status
        self._body = body
        self.requests = []

    def request(self, method, url, **kwargs):
        import io
        from urllib.parse import urlsplit

        import urllib3

        path = urlsplit(url).path
        if method == "GET" and path == "/version":
            status, body = 200, json.dumps({"major": "1", "minor": "35", "gitVersion": "v1.35.0"}).encode()
        elif method == "GET" and path in self._DISCOVERY:
            status, body = 200, json.dumps(self._DISCOVERY[path]).encode()
        else:
            self.requests.append({"method": method, "path": path, **kwargs})
            status, body = self._status, self._body
        return urllib3.HTTPResponse(
            body=io.BytesIO(body),
            status=status,
            reason="Fake",
            headers={"Content-Type": "application/json"},
            preload_content=False,
        )


def _installed_api_client(pool_manager):
    from kubernetes.client import ApiClient, Configuration

    configuration = Configuration()
    configuration.host = "https://hub.invalid"
    configuration.api_key = {"BearerToken": "Bearer parity-token", "authorization": "Bearer parity-token"}
    api_client = ApiClient(configuration=configuration)
    api_client.rest_client.pool_manager = pool_manager
    return api_client


_DELETED_RESTORE = _restore({"name": NAME, "uid": UID, "resourceVersion": "48300"}).encode()
_DELETE_STATUS = json.dumps({"kind": "Status", "apiVersion": "v1", "status": "Success"}).encode()


@pytest.mark.parametrize("status", [200, 202])
@pytest.mark.parametrize(
    "body",
    [_DELETE_STATUS, _DELETED_RESTORE, b"null", b"[]", b'"Restore"', b"{not json", b"\xff\xfe"],
    ids=["status-object", "deleted-restore", "null", "list", "string", "invalid-json", "invalid-utf8"],
)
def test_a_2xx_delete_through_the_installed_client_is_classified_identically(monkeypatch, tmp_path, status, body):
    """No decoder is mocked: each form factor sends its DELETE through the installed kubernetes
    client (36 or 37) to a faked socket. A 202 is accepted exactly like a 200, and a body that is
    not a JSON object is `malformed_response` in both."""
    import tempfile

    from kubernetes.dynamic import DynamicClient

    # The dynamic client's discovery cache file lives in the temp directory; keep it per test.
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    python_pool = _HubPoolManager(status, body)
    python_client = python_form.KubeClient.__new__(python_form.KubeClient)
    python_client.request_timeout = 30
    python_client.dry_run = False
    python_client._api_client = _installed_api_client(python_pool)
    python_result = python_client.delete_restore_guarded(
        NAME, namespace=NAMESPACE, uid=UID, resource_version=RESOURCE_VERSION
    )

    collection_pool = _HubPoolManager(status, body)
    dynamic = DynamicClient(_installed_api_client(collection_pool))
    resource = dynamic.resources.get(
        api_version="cluster.open-cluster-management.io/v1beta1", kind="Restore", name="restores"
    )
    collection_result = _run_collection(monkeypatch, {"action": "delete"}, resource)

    assert _compared(python_result) == _compared(collection_result)
    assert collection_result.get("failed", False) is (not python_result["accepted"])
    expected = "ok" if body in (_DELETE_STATUS, _DELETED_RESTORE) else "malformed_response"
    assert (python_result["reason"], python_result["http_accepted"]) == (expected, True)
    assert [request["method"] for request in python_pool.requests] == ["DELETE"]
    assert [request["method"] for request in collection_pool.requests] == ["DELETE"]
    assert json.loads(python_pool.requests[0]["body"])["preconditions"] == (
        json.loads(collection_pool.requests[0]["body"])["preconditions"]
    )


# --- dry-run and check mode -----------------------------------------------------------


@pytest.mark.parametrize("inputs", PATCH_INPUTS)
def test_patch_dry_run_and_check_mode_predict_identically_without_a_request(python_client, monkeypatch, inputs):
    python_client.dry_run = True
    python_client._api_client.call_api = Mock()
    python_result = _python_patch(python_client, inputs)

    resource = _Resource(("raise", lambda: AssertionError("no request in check mode")))
    collection_result = _run_collection(monkeypatch, {"action": "patch", **inputs}, resource, check_mode=True)

    assert _compared(python_result) == _compared(collection_result)
    assert python_result["reason"] == "predicted"
    python_client._api_client.call_api.assert_not_called()
    assert resource.calls == []


def test_delete_dry_run_and_check_mode_predict_identically_without_a_request(python_client, monkeypatch):
    python_client.dry_run = True
    delete = python_client._api_client.call_api
    python_result = python_client.delete_restore_guarded(
        NAME, namespace=NAMESPACE, uid=UID, resource_version=RESOURCE_VERSION
    )

    resource = _Resource(("raise", lambda: AssertionError("no request in check mode")))
    collection_result = _run_collection(monkeypatch, {"action": "delete"}, resource, check_mode=True)

    assert _compared(python_result) == _compared(collection_result)
    assert (python_result["reason"], python_result["would_change"]) == ("predicted", True)
    delete.assert_not_called()
    assert resource.calls == []


# --- identity inputs ------------------------------------------------------------------

PADDED_IDENTITIES = [
    {"uid": " " + UID, "resource_version": RESOURCE_VERSION},
    {"uid": UID + "\n", "resource_version": RESOURCE_VERSION},
    {"uid": UID, "resource_version": RESOURCE_VERSION + " "},
    {"uid": UID, "resource_version": "\t" + RESOURCE_VERSION},
]


def _python_guarded(client, action, identity):
    if action == "patch":
        return client.json_patch_custom_resource_guarded(NAME, namespace=NAMESPACE, **identity, **PATCH_INPUTS[0])
    return client.delete_restore_guarded(NAME, namespace=NAMESPACE, **identity)


@pytest.mark.parametrize("action", ["patch", "delete"])
@pytest.mark.parametrize("identity", PADDED_IDENTITIES)
@pytest.mark.parametrize("dry_run", [False, True])
def test_both_form_factors_refuse_a_padded_identity_before_any_request(
    python_client, monkeypatch, action, identity, dry_run
):
    python_client.dry_run = dry_run
    python_client._api_client.call_api = Mock()
    delete = python_client.custom_api.delete_namespaced_custom_object
    with pytest.raises(ValidationError):
        _python_guarded(python_client, action, identity)
    python_client._api_client.call_api.assert_not_called()
    delete.assert_not_called()

    args = {
        "action": action,
        "expected_uid": identity["uid"],
        "expected_resource_version": identity["resource_version"],
    }
    if action == "patch":
        args.update(PATCH_INPUTS[0])
    resource = _Resource(("raise", lambda: AssertionError("no request for an invalid identity")))
    collection_result = _run_collection(monkeypatch, args, resource, check_mode=dry_run)
    assert collection_result["failed"] is True
    assert collection_result["reason"] == "invalid_input"
    assert resource.calls == []


# --- no shared runtime code -----------------------------------------------------------


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_runtime_implementations_do_not_import_each_other():
    python_imports = _imported_modules(REPO_ROOT / "lib" / "kube_client.py")
    collection_imports = _imported_modules(Path(collection_form.__file__))
    assert not any(name.startswith("ansible_collections") for name in python_imports)
    assert not any(name == "lib" or name.startswith(("lib.", "modules")) for name in collection_imports)


# --- the single-request guarded CREATE (R4-04 PR C Task 8) -----------------------------

CREATE_NAME = "restore-acm-full"
CREATE_BODY = {
    "apiVersion": "cluster.open-cluster-management.io/v1beta1",
    "kind": "Restore",
    "metadata": {"name": CREATE_NAME, "namespace": NAMESPACE},
    "spec": {"veleroManagedClustersBackupName": "acm-managed-clusters-schedule-20240101120000"},
}
CREATE_ARGS = {
    "action": "create",
    "name": CREATE_NAME,
    "body": CREATE_BODY,
    "expected_uid": None,
    "expected_resource_version": None,
}


def _created(metadata):
    return {"apiVersion": CREATE_BODY["apiVersion"], "kind": "Restore", "metadata": metadata, "spec": {}}


# (vector id, how the one request ends). `created` is a 2xx whose raw body is the object's
# JSON; the other kinds are as for PATCH_VECTORS. Both form factors read the raw body.
CREATE_VECTORS = [
    ("accepted", ("created", {"name": CREATE_NAME, "uid": "u-1", "resourceVersion": "10", "generation": 1})),
    ("accepted_without_generation", ("created", {"name": CREATE_NAME, "uid": "u-1", "resourceVersion": "10"})),
    (
        "accepted_bool_generation",
        ("created", {"name": CREATE_NAME, "uid": "u-1", "resourceVersion": "10", "generation": True}),
    ),
    ("malformed_missing_uid", ("created", {"name": CREATE_NAME, "resourceVersion": "10"})),
    ("malformed_empty_uid", ("created", {"name": CREATE_NAME, "uid": "", "resourceVersion": "10"})),
    ("malformed_missing_rv", ("created", {"name": CREATE_NAME, "uid": "u-1"})),
    ("malformed_other_name", ("created", {"name": "other", "uid": "u-1", "resourceVersion": "10"})),
    ("malformed_metadata", ("created", "not-a-mapping")),
    ("malformed_undecodable", ("body", b"<html>")),
    ("malformed_invalid_utf8", ("body", b"\xff\xfe")),
    ("malformed_list", ("body", b"[]")),
    ("malformed_null", ("body", b"null")),
    ("already_exists_409", ("status", 409)),
    ("not_found_404", ("status", 404)),
    ("server_error_500", ("status", 500)),
    ("gateway_timeout_504", ("status", 504)),
    ("timeout", ("raise", lambda: ReadTimeoutError(None, "/restores", "read timed out"))),
    ("transport", ("raise", lambda: ProtocolError("connection reset"))),
    ("deferred_timeout", ("deferred", lambda: ReadTimeoutError(None, "/restores", "read timed out"))),
]


@pytest.mark.parametrize(("vector_id", "outcome"), CREATE_VECTORS, ids=[vector[0] for vector in CREATE_VECTORS])
def test_create_outcomes_are_classified_identically(python_client, monkeypatch, vector_id, outcome):
    kind, value = outcome
    if kind == "created":
        outcome = ("body", json.dumps(_created(value)).encode())
    python_client._api_client.call_api = Mock(**_python_answer(outcome))
    python_result = python_client.create_restore_guarded(CREATE_BODY, namespace=NAMESPACE)

    resource = _Resource(outcome)
    collection_result = _run_collection(monkeypatch, CREATE_ARGS, resource)

    assert _compared(python_result) == _compared(collection_result), vector_id
    assert collection_result.get("failed", False) is (not python_result["accepted"]), vector_id
    assert python_client._api_client.call_api.call_count == 1 and len(resource.calls) == 1
    assert python_client._api_client.call_api.call_args.kwargs["body"] == resource.calls[0][1]["body"] == CREATE_BODY


def test_create_dry_run_and_check_mode_predict_identically_without_a_request(python_client, monkeypatch):
    python_client.dry_run = True
    create = python_client._api_client.call_api
    python_result = python_client.create_restore_guarded(CREATE_BODY, namespace=NAMESPACE)

    resource = _Resource(("raise", lambda: AssertionError("no request in check mode")))
    collection_result = _run_collection(monkeypatch, CREATE_ARGS, resource, check_mode=True)

    assert _compared(python_result) == _compared(collection_result)
    assert (python_result["reason"], python_result["would_change"]) == ("predicted", True)
    create.assert_not_called()
    assert resource.calls == []
