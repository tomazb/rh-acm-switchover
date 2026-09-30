"""Unit tests for acm_k8s_read_outcome fail-closed read semantics."""

from __future__ import annotations

import importlib
import json
import sys
from types import ModuleType
from typing import Any

import pytest
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic.exceptions import (
    BadRequestError,
    ForbiddenError,
    GoneError,
    InternalServerError,
    NotFoundError,
    ResourceNotFoundError,
    ServiceUnavailableError,
    api_exception,
)

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants

SENTINEL = "R302-SENTINEL-HTTP-BODY"


def _import_module_under_test():
    """Import the module without exposing Galaxy collections to the unit lane."""

    def package(name: str) -> ModuleType:
        module = ModuleType(name)
        module.__path__ = []
        return module

    args_common = ModuleType("ansible_collections.kubernetes.core.plugins.module_utils.args_common")
    setattr(args_common, "AUTH_ARG_SPEC", {})

    client = ModuleType("ansible_collections.kubernetes.core.plugins.module_utils.k8s.client")

    def unavailable_get_api_client(**_kwargs):
        raise AssertionError("unit test must patch get_api_client before use")

    setattr(client, "get_api_client", unavailable_get_api_client)

    stubs = {
        "ansible_collections.kubernetes": package("ansible_collections.kubernetes"),
        "ansible_collections.kubernetes.core": package("ansible_collections.kubernetes.core"),
        "ansible_collections.kubernetes.core.plugins": package("ansible_collections.kubernetes.core.plugins"),
        "ansible_collections.kubernetes.core.plugins.module_utils": package(
            "ansible_collections.kubernetes.core.plugins.module_utils"
        ),
        "ansible_collections.kubernetes.core.plugins.module_utils.args_common": args_common,
        "ansible_collections.kubernetes.core.plugins.module_utils.k8s": package(
            "ansible_collections.kubernetes.core.plugins.module_utils.k8s"
        ),
        "ansible_collections.kubernetes.core.plugins.module_utils.k8s.client": client,
    }
    previous = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return importlib.import_module("ansible_collections.tomazb.acm_switchover.plugins.modules.acm_k8s_read_outcome")
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


acm_k8s_read_outcome = _import_module_under_test()


def _api_error(exc_type: type[Exception], status: int, body: str = SENTINEL) -> Exception:
    class _Resp:
        def __init__(self):
            self.status = status
            self.reason = "error"
            self.data = body.encode("utf-8")
            self.headers = {}

        def getheaders(self):
            return {}

    wrapped = api_exception(ApiException(http_resp=_Resp()))
    assert isinstance(wrapped, exc_type), f"expected {exc_type}, got {type(wrapped)}"
    return wrapped


def _run_module(
    monkeypatch,
    *,
    params: dict[str, Any],
    client=None,
    client_error: Exception | None = None,
    check_mode: bool = False,
) -> dict:
    captured: dict = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "kubeconfig": None,
                "context": None,
                "host": None,
                "api_key": None,
                "username": None,
                "password": None,
                "validate_certs": None,
                "ca_cert": None,
                "client_cert": None,
                "client_key": None,
                "namespace": None,
                "name": None,
                "label_selectors": [],
                **params,
            }
            self.check_mode = check_mode

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs
            raise SystemExit(1)

    monkeypatch.setattr(acm_k8s_read_outcome, "AnsibleModule", FakeModule)

    def fake_get_api_client(module=None, **kwargs):
        if client_error is not None:
            raise client_error
        return client

    monkeypatch.setattr(acm_k8s_read_outcome, "get_api_client", fake_get_api_client)

    try:
        acm_k8s_read_outcome.main()
    except SystemExit:
        pass
    if "exit" in captured:
        return captured["exit"]
    if "fail" in captured:
        return captured["fail"]
    raise AssertionError(f"module did not exit; captured={captured}")


class _RawResponse:
    """`serialize=False` makes DynamicClient.request return the raw response object."""

    def __init__(self, body):
        if isinstance(body, bytes):
            self.data = body
        elif isinstance(body, str):
            self.data = body.encode("utf-8")
        else:
            self.data = json.dumps(body).encode("utf-8")


class _FakeDynamicClient:
    """Stands in for the DynamicClient that K8SClient exposes as `.client`."""

    def __init__(self, discovery=None, discovery_error=None):
        self._discovery = discovery
        self._discovery_error = discovery_error
        self.request_calls: list[dict] = []

    def request(self, method, path, **params):
        self.request_calls.append({"method": method, "path": path, **params})
        if self._discovery_error is not None:
            raise self._discovery_error
        return _RawResponse(self._discovery)


class _FakeClient:
    def __init__(
        self,
        *,
        resource=None,
        resource_error=None,
        get_result=None,
        get_error=None,
        pages=None,
        dynamic=None,
    ):
        self._resource = resource
        self._resource_error = resource_error
        self._get_result = get_result
        self._get_error = get_error
        self._pages = pages
        self.client = dynamic  # what the module reads for discovery
        self.get_calls = 0
        self.resource_calls = 0
        self.get_params: list[dict] = []

    def resource(self, kind, api_version):
        self.resource_calls += 1
        if self._resource_error is not None:
            raise self._resource_error
        return self._resource

    def get(self, resource, **params):
        self.get_calls += 1
        self.get_params.append(params)
        if self._pages is not None:
            page = self._pages[self.get_calls - 1]
            if isinstance(page, BaseException):
                raise page
            return page
        if self._get_error is not None:
            raise self._get_error
        return self._get_result


class _DictResult(dict):
    def to_dict(self):
        return dict(self)


def test_successful_empty_list_is_ok(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        get_result=_DictResult({"kind": "PodList", "items": [], "metadata": {"resourceVersion": "1"}}),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "label_selectors": ["app=x"],
            "resource_name": "pods",
        },
        client=client,
    )
    assert result["changed"] is False
    assert result["read_status"] == "ok"
    assert result["resources"] == []
    assert client.get_calls == 1


def test_successful_nonempty_list_preserves_dicts(monkeypatch):
    pod = {"kind": "Pod", "metadata": {"name": "p1"}}
    client = _FakeClient(
        resource=_PODS_ROUTE,
        get_result=_DictResult({"kind": "PodList", "items": [pod], "metadata": {"resourceVersion": "1"}}),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "resource_name": "pods",
        },
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resources"] == [pod]
    assert isinstance(result["resources"][0], dict)


def test_named_get_present_is_ok(monkeypatch):
    cm = {"kind": "ConfigMap", "metadata": {"name": "cfg", "namespace": "ns", "resourceVersion": "1"}}
    client = _FakeClient(resource=_CONFIGMAPS_ROUTE, get_result=_DictResult(cm))
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cfg",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resources"] == [cm]
    assert result["changed"] is False


class _ResolvedResource:
    """The resolved-resource fields the dynamic client builds a named object route from."""

    def __init__(self, name, *, namespaced, group_version="v1"):
        self.name = name
        self.namespaced = namespaced
        self.group_version = group_version


_CONFIGMAPS_ROUTE = _ResolvedResource("configmaps", namespaced=True)
_PODS_ROUTE = _ResolvedResource("pods", namespaced=True)
_MCO_API = "observability.open-cluster-management.io/v1beta2"
_MCO_ROUTE = _ResolvedResource("multiclusterobservabilities", namespaced=False, group_version=_MCO_API)
_MCO_SERVED = {
    "kind": "APIResourceList",
    "groupVersion": _MCO_API,
    "resources": [{"name": "multiclusterobservabilities", "kind": "MultiClusterObservability", "namespaced": False}],
}
_CONFIGMAPS_SERVED = {
    "kind": "APIResourceList",
    "groupVersion": "v1",
    "resources": [{"name": "configmaps", "kind": "ConfigMap", "namespaced": True}],
}
_CONFIGMAPS_NOT_SERVED = {
    "kind": "APIResourceList",
    "groupVersion": "v1",
    "resources": [{"name": "pods", "kind": "Pod", "namespaced": True}],
}


def test_named_get_explicit_404_is_not_found(monkeypatch):
    client = _FakeClient(
        resource=_CONFIGMAPS_ROUTE,
        get_error=_api_error(NotFoundError, 404),
        dynamic=_FakeDynamicClient(discovery=_CONFIGMAPS_SERVED),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "missing",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "not_found"
    assert result["resources"] == []
    assert result["changed"] is False
    assert SENTINEL not in repr(result)


NAMED_CONFIGMAP_PARAMS = {
    "read_mode": "get",
    "api_version": "v1",
    "kind": "ConfigMap",
    "namespace": "ns",
    "name": "missing",
    "resource_name": "configmaps",
}


@pytest.mark.parametrize(
    "dynamic, expected_status",
    [
        (_FakeDynamicClient(discovery=_CONFIGMAPS_SERVED), "not_found"),
        (_FakeDynamicClient(discovery=_CONFIGMAPS_NOT_SERVED), "kind_not_served"),
        (_FakeDynamicClient(discovery_error=_api_error(ServiceUnavailableError, 503)), "error"),
        (_FakeDynamicClient(discovery=b"not-json"), "error"),
        (None, "error"),
    ],
    ids=["served", "positively_not_served", "discovery_unavailable", "discovery_malformed", "no_discovery_client"],
)
def test_a_named_404_is_an_absence_proof_only_when_live_discovery_serves_the_kind(
    monkeypatch, dynamic, expected_status
):
    """#317: kind resolution may come from kubernetes.core's shared on-disk discovery cache.

    A cached kind the API server no longer serves still resolves, and its object route then
    answers 404. That 404 proves nothing about the object, so `not_found` requires a live
    discovery read that serves the kind; a positive miss is `kind_not_served`, and discovery
    that cannot be read is `error`, never absence.
    """
    client = _FakeClient(resource=_CONFIGMAPS_ROUTE, get_error=_api_error(NotFoundError, 404), dynamic=dynamic)
    result = _run_module(monkeypatch, params=NAMED_CONFIGMAP_PARAMS, client=client)
    assert result["read_status"] == expected_status
    assert result["resources"] == []
    assert result["resource_version"] is None
    assert result["changed"] is False
    if dynamic is not None:
        assert [call["path"] for call in dynamic.request_calls] == ["/api/v1"]
        assert dynamic.request_calls[0]["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT


@pytest.mark.parametrize(
    "get_error",
    [_api_error(ForbiddenError, 403), _api_error(InternalServerError, 500), _api_error(BadRequestError, 400)],
    ids=["forbidden", "server_error", "bad_request"],
)
def test_a_non_404_named_get_failure_stays_error_even_when_discovery_serves_the_kind(monkeypatch, get_error):
    """Only a 404 is ever reclassified; a served kind never turns any other failure into absence."""
    dynamic = _FakeDynamicClient(discovery=_CONFIGMAPS_SERVED)
    client = _FakeClient(resource=_CONFIGMAPS_ROUTE, get_error=get_error, dynamic=dynamic)
    result = _run_module(monkeypatch, params=NAMED_CONFIGMAP_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert dynamic.request_calls == []


def _configmaps_entry(**overrides):
    entry = {"name": "configmaps", "kind": "ConfigMap", "namespaced": True, **overrides}
    return {
        "kind": "APIResourceList",
        "groupVersion": "v1",
        "resources": [{k: v for k, v in entry.items() if v is not None}],
    }


@pytest.mark.parametrize(
    "resource, discovery",
    [
        (_ResolvedResource("configmaps", namespaced=False), _CONFIGMAPS_SERVED),
        (_CONFIGMAPS_ROUTE, _configmaps_entry(namespaced=False)),
        (_CONFIGMAPS_ROUTE, _configmaps_entry(namespaced=None)),
        (_CONFIGMAPS_ROUTE, _configmaps_entry(namespaced="true")),
        (_CONFIGMAPS_ROUTE, _configmaps_entry(kind="OtherConfigMap")),
        (_ResolvedResource("configmap", namespaced=True), _CONFIGMAPS_SERVED),
        (_ResolvedResource("configmaps", namespaced=True, group_version="foo.io/v1"), _CONFIGMAPS_SERVED),
        (object(), _CONFIGMAPS_SERVED),
    ],
    # The stale-scope, plural, group/version and unknown-route cases are refused before the GET
    # by the pre-request route guard (#320); the others reach the named-404 classifier.
    ids=[
        "cached_scope_is_stale",
        "live_scope_differs",
        "live_scope_missing",
        "live_scope_not_a_bool",
        "live_kind_differs",
        "routed_plural_is_not_the_canonical_name",
        "routed_group_version_is_not_the_requested_one",
        "route_unknown",
    ],
)
def test_a_named_404_on_a_route_live_discovery_does_not_confirm_is_error(monkeypatch, resource, discovery):
    """#317: the 404 came from the route the resolved resource built, possibly from a stale cache.

    The dynamic client builds a named object's path from the resolved group/version, plural and
    scope. A stale cached scope sends the GET to a route that 404s even while the
    object exists, and live discovery still lists the plural; this was reproduced read-only
    against a live API server. When a core `v1` lookup misses, kubernetes.core resolves the kind
    in any group at `v1`, so the 404 can come from another group's route while live `/api/v1`
    discovery confirms the core kind. Absence is proved only when live discovery confirms the
    requested kind on the exact route that was read.
    """
    client = _FakeClient(
        resource=resource,
        get_error=_api_error(NotFoundError, 404),
        dynamic=_FakeDynamicClient(discovery=discovery),
    )
    result = _run_module(monkeypatch, params=NAMED_CONFIGMAP_PARAMS, client=client)
    assert result["read_status"] == "error"
    refused_before_the_get = resource is not _CONFIGMAPS_ROUTE
    assert client.get_calls == (0 if refused_before_the_get else 1)
    assert result["resources"] == []
    assert result["resource_version"] is None


def test_a_named_404_of_a_namespaced_kind_read_without_a_namespace_is_error(monkeypatch):
    """The dynamic client routes a namespaced kind read with no namespace to its cluster-wide
    collection path, so that 404 is not an absence proof for any namespaced object (#317)."""
    params = {key: value for key, value in NAMED_CONFIGMAP_PARAMS.items() if key != "namespace"}
    client = _FakeClient(
        resource=_CONFIGMAPS_ROUTE,
        get_error=_api_error(NotFoundError, 404),
        dynamic=_FakeDynamicClient(discovery=_CONFIGMAPS_SERVED),
    )
    result = _run_module(monkeypatch, params=params, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None


_MISSING = object()


def _configmaps_document(group_version):
    """A live discovery document whose only varied field is `groupVersion`."""
    document = {
        "kind": "APIResourceList",
        "resources": [{"name": "configmaps", "kind": "ConfigMap", "namespaced": True}],
    }
    if group_version is not _MISSING:
        document["groupVersion"] = group_version
    return document


@pytest.mark.parametrize(
    "group_version, expected_status",
    [
        ("v1", "not_found"),
        (_MISSING, "error"),
        ("", "error"),
        (7, "error"),
        ("apps/v1", "error"),
    ],
    ids=["matches_requested", "missing", "empty", "not_a_string", "differs_from_requested"],
)
def test_a_named_404_is_an_absence_proof_only_when_live_discovery_is_for_the_requested_group_version(
    monkeypatch, group_version, expected_status
):
    """#317: live discovery must establish the requested API group/version, not just a plural.

    The canonical plural, requested kind, and routed scope all match in every case; only the
    document's `groupVersion` varies. A document that does not declare the requested group/version
    proves nothing about the route that returned the 404, so it is unverifiable, never absence.
    """
    dynamic = _FakeDynamicClient(discovery=_configmaps_document(group_version))
    client = _FakeClient(resource=_CONFIGMAPS_ROUTE, get_error=_api_error(NotFoundError, 404), dynamic=dynamic)
    result = _run_module(monkeypatch, params=NAMED_CONFIGMAP_PARAMS, client=client)
    assert result["read_status"] == expected_status
    assert result["resources"] == []
    assert result["resource_version"] is None
    assert [call["path"] for call in dynamic.request_calls] == ["/api/v1"]


@pytest.mark.parametrize(
    "group_version",
    [_MISSING, "", 7, "operator.open-cluster-management.io/v2"],
    ids=["missing", "empty", "not_a_string", "differs_from_requested"],
)
def test_discovery_for_another_group_version_never_proves_a_kind_not_served(monkeypatch, group_version):
    """An unresolvable kind is `kind_not_served` only when discovery for its own group/version omits it."""
    discovery = {"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]}
    if group_version is not _MISSING:
        discovery["groupVersion"] = group_version
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=_FakeDynamicClient(discovery))
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "operator.open-cluster-management.io/v1",
            "kind": "MultiClusterHub",
            "resource_name": "multiclusterhubs",
        },
        client=client,
    )
    assert result["read_status"] == "error"


def test_list_path_404_is_error_not_not_found(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        get_error=_api_error(NotFoundError, 404),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "resource_name": "pods",
        },
        client=client,
    )
    assert result["read_status"] == "error"
    assert result["changed"] is False
    assert SENTINEL not in repr(result)


def test_bad_request_400_is_error(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        get_error=_api_error(BadRequestError, 400),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "resource_name": "pods",
        },
        client=client,
    )
    assert result["read_status"] == "error"
    assert SENTINEL not in repr(result)


def test_forbidden_403_is_error(monkeypatch):
    client = _FakeClient(
        resource=_CONFIGMAPS_ROUTE,
        get_error=_api_error(ForbiddenError, 403),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cfg",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "error"
    assert SENTINEL not in repr(result)


def test_resource_discovery_failure_is_error(monkeypatch):
    client = _FakeClient(resource_error=ResourceNotFoundError("no such api"))
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "NotARealKind",
            "namespace": "ns",
            "resource_name": "notarealkinds",
        },
        client=client,
    )
    assert result["read_status"] == "error"
    assert client.get_calls == 0


def test_timeout_transport_failure_is_error(monkeypatch):
    client = _FakeClient(resource=_PODS_ROUTE, get_error=TimeoutError("timed out connecting"))
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "resource_name": "pods",
        },
        client=client,
    )
    assert result["read_status"] == "error"
    assert "timed out" not in repr(result).lower()


def test_client_auth_construction_failure_is_error(monkeypatch):
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cfg",
            "resource_name": "configmaps",
        },
        client_error=Exception(f"auth failed: {SENTINEL}"),
    )
    assert result["read_status"] == "error"
    assert result["changed"] is False
    assert SENTINEL not in repr(result)


def test_malformed_list_response_is_error(monkeypatch):
    client = _FakeClient(resource=_PODS_ROUTE, get_result=object())
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "resource_name": "pods",
        },
        client=client,
    )
    assert result["read_status"] == "error"


def test_malformed_named_get_response_is_error(monkeypatch):
    client = _FakeClient(resource=_CONFIGMAPS_ROUTE, get_result={"items": "not-a-list"})
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cfg",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "error"


def test_sensitive_exception_content_never_returned(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        get_error=_api_error(ForbiddenError, 403, body=f"token={SENTINEL}"),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "resource_name": "pods",
        },
        client=client,
    )
    dumped = repr(result)
    assert SENTINEL not in dumped
    assert "token=" not in dumped
    assert set(result.keys()) <= {"changed", "read_status", "resources", "resource_version"}


def test_every_path_reports_changed_false(monkeypatch):
    cases = [
        (
            {
                "read_mode": "list",
                "api_version": "v1",
                "kind": "Pod",
                "namespace": "ns",
                "resource_name": "pods",
            },
            _FakeClient(
                resource=_PODS_ROUTE,
                get_result=_DictResult({"kind": "PodList", "items": [], "metadata": {"resourceVersion": "1"}}),
            ),
            None,
            "ok",
        ),
        (
            {
                "read_mode": "get",
                "api_version": "v1",
                "kind": "ConfigMap",
                "namespace": "ns",
                "name": "x",
                "resource_name": "configmaps",
            },
            _FakeClient(
                resource=_CONFIGMAPS_ROUTE,
                get_error=_api_error(NotFoundError, 404),
                dynamic=_FakeDynamicClient(discovery=_CONFIGMAPS_SERVED),
            ),
            None,
            "not_found",
        ),
        (
            {
                "read_mode": "list",
                "api_version": "v1",
                "kind": "Pod",
                "namespace": "ns",
                "resource_name": "pods",
            },
            _FakeClient(
                resource=_PODS_ROUTE,
                get_error=_api_error(BadRequestError, 400),
            ),
            None,
            "error",
        ),
        (
            {
                "read_mode": "list",
                "api_version": "v1",
                "kind": "Pod",
                "namespace": "ns",
                "resource_name": "pods",
            },
            None,
            Exception("boom"),
            "error",
        ),
    ]
    for params, client, client_error, expected_status in cases:
        result = _run_module(
            monkeypatch,
            params=params,
            client=client,
            client_error=client_error,
            check_mode=True,
        )
        assert result["changed"] is False
        assert result["read_status"] == expected_status


LIST_PARAMS = {
    "read_mode": "list",
    "api_version": "v1",
    "kind": "Pod",
    "namespace": "ns",
    "resource_name": "pods",
}


def _page(items, continue_token=None, resource_version="1"):
    metadata = {"resourceVersion": resource_version}
    if continue_token:
        metadata["continue"] = continue_token
    return _DictResult({"kind": "PodList", "items": items, "metadata": metadata})


def test_list_mode_follows_continue_tokens_to_exhaustion(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[_page([{"metadata": {"name": "a"}}], "tok"), _page([{"metadata": {"name": "b"}}])],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "ok"
    assert [r["metadata"]["name"] for r in result["resources"]] == ["a", "b"]
    assert [p.get("_continue") for p in client.get_params] == [None, "tok"]


def test_a_complete_list_publishes_the_page_one_snapshot_revision(monkeypatch):
    """A3.0 rule 8: one revision describes the whole read, and every page agrees with it."""
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[
            _page([{"metadata": {"name": "a"}}], "tok", resource_version="100"),
            _page([{"metadata": {"name": "b"}}], resource_version="100"),
        ],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "ok"
    assert result["resource_version"] == "100"


def test_a_continuation_page_at_a_different_revision_is_error(monkeypatch):
    """A3.0 rule 8, negative: mismatched pages are not one snapshot, so the read fails."""
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[
            _page([{"metadata": {"name": "a"}}], "tok", resource_version="100"),
            _page([{"metadata": {"name": "b"}}], resource_version="999"),
        ],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None


def test_a_restarted_read_with_an_inconsistent_continuation_is_error(monkeypatch):
    """The restarted read establishes a new snapshot, and its pages must agree with it."""
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[
            _page([{"metadata": {"name": "a"}}], "tok", resource_version="100"),
            _api_error(GoneError, 410),
            _page([{"metadata": {"name": "a"}}], "tok", resource_version="200"),
            _page([{"metadata": {"name": "b"}}], resource_version="100"),
        ],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None
    # Every scripted page was read: the error is the restarted read's revision mismatch.
    assert client.get_calls == 4


def test_a_named_get_publishes_the_objects_revision(monkeypatch):
    client = _FakeClient(
        resource=_CONFIGMAPS_ROUTE,
        get_result=_DictResult({"kind": "ConfigMap", "metadata": {"name": "cm", "resourceVersion": "77"}}),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cm",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resource_version"] == "77"


@pytest.mark.parametrize(
    "metadata",
    [
        {"name": "cm"},  # revision missing
        {"name": "cm", "resourceVersion": ""},  # revision empty
        {"name": "cm", "resourceVersion": 77},  # revision not a string
    ],
    ids=["missing", "empty", "non_string"],
)
def test_a_named_get_without_a_usable_revision_is_error(monkeypatch, metadata):
    """A3.0 rule 9: `read_status: ok` is unreachable without the object's own revision."""
    client = _FakeClient(
        resource=_CONFIGMAPS_ROUTE,
        get_result=_DictResult({"kind": "ConfigMap", "metadata": metadata}),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cm",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None


def test_every_list_page_carries_the_fixed_limit_and_a_bounded_timeout(monkeypatch):
    client = _FakeClient(resource=_PODS_ROUTE, pages=[_page([], "tok"), _page([])])
    _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert [p["limit"] for p in client.get_params] == [
        constants.STRICT_READ_PAGE_LIMIT,
        constants.STRICT_READ_PAGE_LIMIT,
    ]
    assert all(p["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT for p in client.get_params)


def test_list_mode_page_failure_is_error_and_returns_no_partial_inventory(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[_page([{"metadata": {"name": "a"}}], "tok"), _api_error(InternalServerError, 500)],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None


def test_list_mode_outstanding_continuation_at_exit_is_error(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[_page([], "tok")] * (constants.STRICT_READ_MAX_PAGES + 5),
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []


def test_expired_continuation_restarts_the_whole_read_once(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[
            _page([{"metadata": {"name": "a"}}], "tok", resource_version="100"),
            _api_error(GoneError, 410),
            _page([{"metadata": {"name": "a"}}], "tok", resource_version="200"),
            _page([{"metadata": {"name": "b"}}], resource_version="200"),
        ],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "ok"
    # The pre-410 prefix is discarded, not carried into the restart.
    assert [r["metadata"]["name"] for r in result["resources"]] == ["a", "b"]
    assert client.get_params[2].get("_continue") is None
    # ...and so is its snapshot revision.
    assert result["resource_version"] == "200"


def test_second_expired_continuation_is_error_with_no_partial_output(monkeypatch):
    client = _FakeClient(
        resource=_PODS_ROUTE,
        pages=[
            _page([{"metadata": {"name": "a"}}], "tok"),
            _api_error(GoneError, 410),
            _page([{"metadata": {"name": "a"}}], "tok"),
            _api_error(GoneError, 410),
        ],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []


@pytest.mark.parametrize(
    "page",
    [
        _DictResult({"kind": "PodList", "metadata": {"resourceVersion": "1"}}),  # items missing
        _DictResult({"kind": "PodList", "items": None, "metadata": {"resourceVersion": "1"}}),
        _DictResult({"kind": "PodList", "items": "nope", "metadata": {"resourceVersion": "1"}}),
        _DictResult({"kind": "PodList", "items": ["nope"], "metadata": {"resourceVersion": "1"}}),
        _DictResult({"kind": "PodList", "items": [], "metadata": "nope"}),
        _DictResult({"kind": "PodList", "items": []}),  # metadata missing
        _DictResult({"kind": "PodList", "items": [], "metadata": {}}),  # no revision
        _DictResult({"kind": "PodList", "items": [], "metadata": {"resourceVersion": ""}}),  # empty revision
        _DictResult({"kind": "PodList", "items": [], "metadata": {"resourceVersion": 7}}),  # non-string
    ],
)
def test_malformed_list_pages_are_error_never_empty_success(monkeypatch, page):
    client = _FakeClient(resource=_PODS_ROUTE, pages=[page])
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None


@pytest.mark.parametrize(
    "params, client_kwargs, expected_status",
    [
        (
            {
                "read_mode": "get",
                "api_version": "v1",
                "kind": "Namespace",
                "name": "absent-ns",
                "resource_name": "namespaces",
            },
            {
                "resource": _ResolvedResource("namespaces", namespaced=False),
                "get_error": _api_error(NotFoundError, 404),
                "dynamic": _FakeDynamicClient(
                    discovery={
                        "kind": "APIResourceList",
                        "groupVersion": "v1",
                        "resources": [{"name": "namespaces", "kind": "Namespace", "namespaced": False}],
                    }
                ),
            },
            "not_found",
        ),
        (
            {
                "read_mode": "list",
                "api_version": "g/v1",
                "kind": "Widget",
                "resource_name": "widgets",
            },
            {
                "resource_error": ResourceNotFoundError("no matches"),
                "dynamic": _FakeDynamicClient(
                    discovery={
                        "kind": "APIResourceList",
                        "groupVersion": "g/v1",
                        "resources": [{"name": "pods", "kind": "Pod"}],
                    }
                ),
            },
            "kind_not_served",
        ),
    ],
    ids=["not_found", "kind_not_served"],
)
def test_absence_outcomes_never_publish_a_revision(monkeypatch, params, client_kwargs, expected_status):
    """The collection half of the §10.2.1b rule: absence proofs carry no revision."""
    result = _run_module(monkeypatch, params=params, client=_FakeClient(**client_kwargs))
    assert result["read_status"] == expected_status
    assert result["resource_version"] is None


def test_every_outcome_publishes_the_resource_version_key(monkeypatch):
    """The key is always present, so callers never branch on its absence."""
    client = _FakeClient(resource=_PODS_ROUTE, pages=[_page([])])
    assert "resource_version" in _run_module(monkeypatch, params=LIST_PARAMS, client=client)


def test_positive_discovery_miss_is_kind_not_served(monkeypatch):
    client = _FakeClient(
        resource_error=ResourceNotFoundError("no matches"),
        dynamic=_FakeDynamicClient(
            discovery={
                "kind": "APIResourceList",
                "groupVersion": "operator.open-cluster-management.io/v1",
                "resources": [{"name": "pods", "kind": "Pod"}],
            }
        ),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "operator.open-cluster-management.io/v1",
            "kind": "MultiClusterHub",
            "resource_name": "multiclusterhubs",
        },
        client=client,
    )
    assert result["read_status"] == "kind_not_served"


def test_discovery_request_is_bounded_and_targets_the_exact_group_version(monkeypatch):
    dynamic = _FakeDynamicClient(
        discovery={
            "kind": "APIResourceList",
            "groupVersion": "operator.open-cluster-management.io/v1",
            "resources": [{"name": "pods", "kind": "Pod"}],
        }
    )
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "operator.open-cluster-management.io/v1",
            "kind": "MultiClusterHub",
            "resource_name": "multiclusterhubs",
        },
        client=client,
    )
    call = dynamic.request_calls[0]
    assert call["path"] == "/apis/operator.open-cluster-management.io/v1"
    assert call["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT
    assert call["serialize"] is False


def test_core_group_discovery_uses_the_core_path(monkeypatch):
    dynamic = _FakeDynamicClient(
        discovery={"kind": "APIResourceList", "groupVersion": "v1", "resources": [{"name": "pods", "kind": "Pod"}]}
    )
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert dynamic.request_calls[0]["path"] == "/api/v1"


@pytest.mark.parametrize(
    "dynamic",
    [
        _FakeDynamicClient(discovery_error=_api_error(ServiceUnavailableError, 503)),
        _FakeDynamicClient(discovery_error=_api_error(ForbiddenError, 403)),
        _FakeDynamicClient(discovery_error=_api_error(NotFoundError, 404)),
        _FakeDynamicClient(discovery_error=TimeoutError("deadline exceeded")),
        _FakeDynamicClient(discovery="<html>gateway</html>"),
        _FakeDynamicClient(discovery={"kind": "Status"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "groupVersion": "g/v1"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "groupVersion": "g/v1", "resources": "nope"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "groupVersion": "g/v1", "resources": [{"name": 7}]}),
    ],
)
def test_unverifiable_discovery_is_error_not_kind_not_served(monkeypatch, dynamic):
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    result = _run_module(
        monkeypatch,
        params={"read_mode": "list", "api_version": "g/v1", "kind": "Widget", "resource_name": "widgets"},
        client=client,
    )
    assert result["read_status"] == "error"


@pytest.mark.parametrize(
    "resources",
    [
        [{"name": "widgets", "kind": "Widget"}, {"name": 7}],
        [{"name": 7}, {"name": "widgets", "kind": "Widget"}],
    ],
    ids=["malformed_after_match", "malformed_before_match"],
)
def test_a_malformed_entry_anywhere_is_unverifiable_whatever_the_entry_order(resources):
    """Mirrors the Python prover: the whole document validates before any verdict is returned.

    Asserted on the prover itself rather than through `read_status`, because this call site
    maps both `True` and `None` to `error`, which hides the order-dependence from the module's
    output while leaving it wrong in the helper the parity contract holds equal.
    """
    dynamic = _FakeDynamicClient(discovery={"kind": "APIResourceList", "groupVersion": "g/v1", "resources": resources})
    client = _FakeClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    assert acm_k8s_read_outcome._discovery_serves(client, "g/v1", "widgets") is None


def test_irregular_plural_resource_lookup_success_reads_ok(monkeypatch):
    """The canonical plural is supplied by the caller and the read completes normally."""
    client = _FakeClient(
        resource=_MCO_ROUTE,
        dynamic=_FakeDynamicClient(discovery=_MCO_SERVED),
        pages=[
            _DictResult({"kind": "MultiClusterObservabilityList", "items": [], "metadata": {"resourceVersion": "1"}})
        ],
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "observability.open-cluster-management.io/v1beta2",
            "kind": "MultiClusterObservability",
            "resource_name": "multiclusterobservabilities",
        },
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resources"] == []


def test_irregular_plural_matches_the_exact_name_and_never_becomes_absence(monkeypatch):
    """Discovery positively serves the irregular plural, but no resource handle exists.

    A synthesized plural would miss the discovery entry and wrongly yield
    `kind_not_served`; the exact canonical name matches, so the outcome is the
    fail-closed `error` for a served kind that could not be read.
    """
    client = _FakeClient(
        resource_error=ResourceNotFoundError("no matches"),
        dynamic=_FakeDynamicClient(
            discovery={
                "kind": "APIResourceList",
                "groupVersion": "observability.open-cluster-management.io/v1beta2",
                "resources": [{"name": "multiclusterobservabilities", "kind": "MultiClusterObservability"}],
            }
        ),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "observability.open-cluster-management.io/v1beta2",
            "kind": "MultiClusterObservability",
            "resource_name": "multiclusterobservabilities",
        },
        client=client,
    )
    assert result["read_status"] == "error"


@pytest.mark.parametrize("resource_name", [None, "", "   "])
def test_missing_resource_name_is_rejected_before_any_client_work(monkeypatch, resource_name):
    client = _FakeClient(resource=_PODS_ROUTE, pages=[_page([])])
    result = _run_module(
        monkeypatch,
        params={**LIST_PARAMS, "resource_name": resource_name},
        client=client,
    )
    assert result["read_status"] == "error"
    assert client.resource_calls == 0


def test_return_documentation_lists_every_status():
    import yaml

    documented = yaml.safe_load(acm_k8s_read_outcome.RETURN)["read_status"]["choices"]
    assert sorted(documented) == ["error", "kind_not_served", "not_found", "ok"]


def test_return_documentation_declares_the_resource_version_output():
    import yaml

    returned = yaml.safe_load(acm_k8s_read_outcome.RETURN)
    assert returned["resource_version"]["type"] == "str"
    assert returned["resource_version"]["returned"] == "always"


def test_resource_name_is_required_and_module_reports_no_namespace_probing_mode():
    spec = acm_k8s_read_outcome._argument_spec()
    assert spec["read_mode"]["choices"] == ["get", "list"]
    assert spec["resource_name"]["required"] is True


def test_shipped_examples_supply_the_required_resource_name():
    """Every documented invocation must still run once `resource_name` became required.

    `resource_name` has no default and is never synthesized from `kind`, so an example that
    omits it fails argument validation before performing any read. The canonical plural is
    asserted rather than mere presence: `resource_name: pod` would satisfy the argument spec
    and still never match a discovery entry.
    """
    import yaml

    canonical_plural = {"Pod": "pods", "ConfigMap": "configmaps"}
    invocations = [
        args
        for task in yaml.safe_load(acm_k8s_read_outcome.EXAMPLES)
        for key, args in task.items()
        if key.endswith("acm_k8s_read_outcome")
    ]
    assert invocations, "EXAMPLES must invoke the module"
    for args in invocations:
        assert "resource_name" in args, f"example {args['kind']!r} omits the required resource_name"
        assert args["resource_name"] == canonical_plural[args["kind"]]


def test_the_strict_read_helpers_have_one_owner_in_module_utils():
    """The read algebra moved to module_utils/k8s_read.py; this module keeps no second copy.

    `_discovery_serves` stays reachable here for existing callers, as the same object.
    """
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import k8s_read

    assert acm_k8s_read_outcome._discovery_serves is k8s_read._discovery_serves
    assert acm_k8s_read_outcome.strict_read is k8s_read.strict_read
    for moved in ("_drain_list", "_drain_list_once", "_strict_list_page", "_normalize_resources", "_object_revision"):
        assert not hasattr(acm_k8s_read_outcome, moved), moved


def test_a_named_get_is_bounded(monkeypatch):
    """§9.1 per-call timeout: the collection bounds EVERY strict request, not just list pages.

    Python bounds each strict call with the per-instance request timeout; the collection has no
    client instance, so it passes STRICT_READ_REQUEST_TIMEOUT. An unbounded named GET can hang
    indefinitely and would break that parity.
    """
    client = _FakeClient(
        resource=_CONFIGMAPS_ROUTE,
        get_result=_DictResult({"kind": "ConfigMap", "metadata": {"name": "cm", "resourceVersion": "77"}}),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cm",
            "resource_name": "configmaps",
        },
        client=client,
    )
    assert result["read_status"] == "ok"
    assert client.get_params[0]["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT


# --------------------------------------------------------------------------------------------
# #320 / #322: the resolved route is confirmed before any request is sent.
# --------------------------------------------------------------------------------------------


def _read_params(read_mode, *, api_version, kind, resource_name, namespace=None, name=None):
    params = {"read_mode": read_mode, "api_version": api_version, "kind": kind, "resource_name": resource_name}
    if namespace is not None:
        params["namespace"] = namespace
    if read_mode == "get":
        params["name"] = name
    return params


def _routed_client(read_mode, route, obj, *, list_kind, dynamic=None):
    """A client whose one request would succeed: the named object, or a one-page list of it."""
    if read_mode == "get":
        return _FakeClient(resource=route, get_result=_DictResult(obj), dynamic=dynamic)
    page = _DictResult({"kind": list_kind, "items": [obj], "metadata": {"resourceVersion": "9"}})
    return _FakeClient(resource=route, pages=[page], dynamic=dynamic)


_CM = {"kind": "ConfigMap", "metadata": {"name": "cfg", "namespace": "ns", "resourceVersion": "1"}}
_FOREIGN_POD = {"kind": "Pod", "metadata": {"name": "other", "namespace": "elsewhere", "resourceVersion": "9"}}


@pytest.mark.parametrize("items", [[_FOREIGN_POD], []], ids=["foreign-member", "empty"])
def test_a_namespaced_list_on_a_cluster_scoped_route_is_error_before_any_request(monkeypatch, items):
    """A cached `namespaced=False` drops the namespace and would LIST cluster-wide (#320).

    Neither the cluster-wide superset nor an empty cluster-wide answer is the requested
    namespace's inventory, so no request is sent at all.
    """
    page = _DictResult({"kind": "PodList", "items": items, "metadata": {"resourceVersion": "9"}})
    client = _FakeClient(resource=_ResolvedResource("pods", namespaced=False), pages=[page])
    result = _run_module(
        monkeypatch,
        params=_read_params("list", api_version="v1", kind="Pod", resource_name="pods", namespace="ns"),
        client=client,
    )
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None
    assert client.get_calls == 0


def test_a_namespaced_named_get_on_a_cluster_scoped_route_is_error_before_any_request(monkeypatch):
    client = _routed_client("get", _ResolvedResource("configmaps", namespaced=False), _CM, list_kind="ConfigMapList")
    result = _run_module(
        monkeypatch,
        params=_read_params(
            "get", api_version="v1", kind="ConfigMap", resource_name="configmaps", namespace="ns", name="cfg"
        ),
        client=client,
    )
    assert result["read_status"] == "error"
    assert client.get_calls == 0


@pytest.mark.parametrize("read_mode", ["get", "list"])
@pytest.mark.parametrize(
    "route",
    [
        # #323's condition: a core `v1` kind resolved in another group at `v1`, routed `/apis/...`.
        _ResolvedResource("configmaps", namespaced=True, group_version="foo.io/v1"),
        _ResolvedResource("configmap", namespaced=True),
        _ResolvedResource("configmaps", namespaced=None),
    ],
    ids=["foreign-group-version", "non-canonical-plural", "unknown-scope"],
)
def test_a_route_that_is_not_the_requested_route_is_error_before_any_request(monkeypatch, read_mode, route):
    client = _routed_client(read_mode, route, _CM, list_kind="ConfigMapList")
    result = _run_module(
        monkeypatch,
        params=_read_params(
            read_mode, api_version="v1", kind="ConfigMap", resource_name="configmaps", namespace="ns", name="cfg"
        ),
        client=client,
    )
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert client.get_calls == 0


def test_an_all_namespaces_list_of_a_namespaced_kind_is_still_ok(monkeypatch):
    """Positive control: a LIST with no namespace is a legitimate cluster-wide read."""
    client = _routed_client("list", _ResolvedResource("pods", namespaced=True), _FOREIGN_POD, list_kind="PodList")
    result = _run_module(
        monkeypatch,
        params=_read_params("list", api_version="v1", kind="Pod", resource_name="pods"),
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resources"] == [_FOREIGN_POD]


@pytest.mark.parametrize("read_mode", ["get", "list"])
@pytest.mark.parametrize(
    "api_version, kind, resource_name",
    [("v1", "ConfigMap", "configmaps"), ("apps/v1", "Deployment", "deployments")],
    ids=["core", "apps"],
)
def test_a_built_in_read_never_reads_discovery_on_success(monkeypatch, read_mode, api_version, kind, resource_name):
    """Python's typed built-in readers prove nothing by discovery, so neither does the collection."""
    dynamic = _FakeDynamicClient(discovery_error=AssertionError("a built-in success must not read discovery"))
    route = _ResolvedResource(resource_name, namespaced=True, group_version=api_version)
    obj = {"kind": kind, "metadata": {"name": "cfg", "namespace": "ns", "resourceVersion": "1"}}
    client = _routed_client(read_mode, route, obj, list_kind=f"{kind}List", dynamic=dynamic)
    result = _run_module(
        monkeypatch,
        params=_read_params(
            read_mode, api_version=api_version, kind=kind, resource_name=resource_name, namespace="ns", name="cfg"
        ),
        client=client,
    )
    assert result["read_status"] == "ok"
    assert dynamic.request_calls == []


_MC_API = "cluster.open-cluster-management.io/v1"
_MC_ROUTE = _ResolvedResource("managedclusters", namespaced=False, group_version=_MC_API)
_MC = {"kind": "ManagedCluster", "metadata": {"name": "c1", "resourceVersion": "5"}}
_MC_SERVED = {
    "kind": "APIResourceList",
    "groupVersion": _MC_API,
    "resources": [{"name": "managedclusters", "kind": "ManagedCluster", "namespaced": False}],
}


def _mc_params(read_mode):
    return _read_params(
        read_mode, api_version=_MC_API, kind="ManagedCluster", resource_name="managedclusters", name="c1"
    )


def _mc_client(read_mode, dynamic):
    return _routed_client(read_mode, _MC_ROUTE, _MC, list_kind="ManagedClusterList", dynamic=dynamic)


@pytest.mark.parametrize("read_mode", ["get", "list"])
@pytest.mark.parametrize(
    "dynamic",
    [
        lambda: _FakeDynamicClient(discovery_error=_api_error(ServiceUnavailableError, 503)),
        lambda: _FakeDynamicClient(discovery=b"not json"),
        lambda: _FakeDynamicClient(discovery={"kind": "APIResourceList", "groupVersion": _MC_API, "resources": [7]}),
        lambda: _FakeDynamicClient(discovery=dict(_MC_SERVED, groupVersion="")),
    ],
    ids=["503", "undecodable", "malformed-entry", "empty-groupVersion"],
)
def test_a_custom_resource_read_with_unverifiable_discovery_is_error_before_any_request(
    monkeypatch, read_mode, dynamic
):
    """#322: Python proves a custom resource served before any object request; so does the collection."""
    client = _mc_client(read_mode, dynamic())
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None
    assert client.get_calls == 0


@pytest.mark.parametrize("read_mode", ["get", "list"])
def test_a_custom_resource_live_discovery_omits_is_kind_not_served_before_any_request(monkeypatch, read_mode):
    dynamic = _FakeDynamicClient(discovery=dict(_MC_SERVED, resources=[{"name": "other", "kind": "Other"}]))
    client = _mc_client(read_mode, dynamic)
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "kind_not_served"
    assert client.get_calls == 0


@pytest.mark.parametrize("read_mode", ["get", "list"])
@pytest.mark.parametrize("live_namespaced", [True, None, 0], ids=["namespaced", "missing", "non-bool"])
def test_a_custom_resource_read_needs_only_the_served_name_before_the_request(monkeypatch, read_mode, live_namespaced):
    """Before the request, live discovery must list the name; its scope field gates nothing.

    Python's prover never reads the scope field. The route guard has already refused a namespaced request
    on a cluster-scoped route, and the server answers any other scope mismatch at the route itself
    (a namespaced URL for a cluster-scoped kind, or a cluster URL for a named namespaced object, is
    404, which the named-404 classifier then refuses to call absence). Gating on the live scope field
    would only fail reads Python completes when a non-conformant document omits or mistypes it.
    """
    entry = {"name": "managedclusters", "kind": "ManagedCluster", "namespaced": live_namespaced}
    dynamic = _FakeDynamicClient(discovery=dict(_MC_SERVED, resources=[entry]))
    client = _mc_client(read_mode, dynamic)
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "ok"
    assert len(dynamic.request_calls) == 1
    assert client.get_calls == 1


@pytest.mark.parametrize("read_mode", ["get", "list"])
def test_a_confirmed_custom_resource_read_is_ok_after_exactly_one_discovery_read(monkeypatch, read_mode):
    dynamic = _FakeDynamicClient(discovery=_MC_SERVED)
    client = _mc_client(read_mode, dynamic)
    result = _run_module(monkeypatch, params=_mc_params(read_mode), client=client)
    assert result["read_status"] == "ok"
    assert result["resources"] == [_MC]
    assert [call["path"] for call in dynamic.request_calls] == [f"/apis/{_MC_API}"]
    assert dynamic.request_calls[0]["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT
    assert client.get_calls == 1


def test_a_custom_named_404_reads_discovery_exactly_once(monkeypatch):
    dynamic = _FakeDynamicClient(discovery=_MC_SERVED)
    client = _FakeClient(resource=_MC_ROUTE, get_error=_api_error(NotFoundError, 404), dynamic=dynamic)
    result = _run_module(monkeypatch, params=_mc_params("get"), client=client)
    assert result["read_status"] == "not_found"
    assert len(dynamic.request_calls) == 1
