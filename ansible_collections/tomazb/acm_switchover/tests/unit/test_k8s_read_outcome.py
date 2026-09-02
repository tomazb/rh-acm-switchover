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
        resource=object(),
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
        resource=object(),
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
    client = _FakeClient(resource=object(), get_result=_DictResult(cm))
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


def test_named_get_explicit_404_is_not_found(monkeypatch):
    client = _FakeClient(
        resource=object(),
        get_error=_api_error(NotFoundError, 404),
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


def test_list_path_404_is_error_not_not_found(monkeypatch):
    client = _FakeClient(
        resource=object(),
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
        resource=object(),
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
        resource=object(),
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
    client = _FakeClient(resource=object(), get_error=TimeoutError("timed out connecting"))
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
    client = _FakeClient(resource=object(), get_result=object())
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
    client = _FakeClient(resource=object(), get_result={"items": "not-a-list"})
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
        resource=object(),
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
                resource=object(),
                get_result=_DictResult({"kind": "PodList", "items": [], "metadata": {"resourceVersion": "1"}}),
            ),
            None,
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
                resource=object(),
                get_error=_api_error(NotFoundError, 404),
            ),
            None,
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
                resource=object(),
                get_error=_api_error(BadRequestError, 400),
            ),
            None,
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
        ),
    ]
    for params, client, client_error in cases:
        result = _run_module(
            monkeypatch,
            params=params,
            client=client,
            client_error=client_error,
            check_mode=True,
        )
        assert result["changed"] is False
        assert result["read_status"] in {"ok", "not_found", "error"}


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
        resource=object(),
        pages=[_page([{"metadata": {"name": "a"}}], "tok"), _page([{"metadata": {"name": "b"}}])],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "ok"
    assert [r["metadata"]["name"] for r in result["resources"]] == ["a", "b"]
    assert [p.get("_continue") for p in client.get_params] == [None, "tok"]


def test_a_complete_list_publishes_the_page_one_snapshot_revision(monkeypatch):
    """A3.0 rule 8: one revision describes the whole read, and every page agrees with it."""
    client = _FakeClient(
        resource=object(),
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
        resource=object(),
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
        resource=object(),
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


def test_a_named_get_publishes_the_objects_revision(monkeypatch):
    client = _FakeClient(
        resource=object(),
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
        resource=object(),
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
    client = _FakeClient(resource=object(), pages=[_page([], "tok"), _page([])])
    _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert [p["limit"] for p in client.get_params] == [
        constants.STRICT_READ_PAGE_LIMIT,
        constants.STRICT_READ_PAGE_LIMIT,
    ]
    assert all(p["_request_timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT for p in client.get_params)


def test_list_mode_page_failure_is_error_and_returns_no_partial_inventory(monkeypatch):
    client = _FakeClient(
        resource=object(),
        pages=[_page([{"metadata": {"name": "a"}}], "tok"), _api_error(InternalServerError, 500)],
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []
    assert result["resource_version"] is None


def test_list_mode_outstanding_continuation_at_exit_is_error(monkeypatch):
    client = _FakeClient(
        resource=object(),
        pages=[_page([], "tok")] * (constants.STRICT_READ_MAX_PAGES + 5),
    )
    result = _run_module(monkeypatch, params=LIST_PARAMS, client=client)
    assert result["read_status"] == "error"
    assert result["resources"] == []


def test_expired_continuation_restarts_the_whole_read_once(monkeypatch):
    client = _FakeClient(
        resource=object(),
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
        resource=object(),
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
    client = _FakeClient(resource=object(), pages=[page])
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
            {"resource": object(), "get_error": _api_error(NotFoundError, 404)},
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
                    discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]}
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
    client = _FakeClient(resource=object(), pages=[_page([])])
    assert "resource_version" in _run_module(monkeypatch, params=LIST_PARAMS, client=client)


def test_positive_discovery_miss_is_kind_not_served(monkeypatch):
    client = _FakeClient(
        resource_error=ResourceNotFoundError("no matches"),
        dynamic=_FakeDynamicClient(
            discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]}
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
    dynamic = _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]})
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
    dynamic = _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]})
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
        _FakeDynamicClient(discovery={"kind": "APIResourceList"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": "nope"}),
        _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": [{"name": 7}]}),
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


def test_irregular_plural_resource_lookup_success_reads_ok(monkeypatch):
    """The canonical plural is supplied by the caller and the read completes normally."""
    client = _FakeClient(
        resource=object(),
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
    client = _FakeClient(resource=object(), pages=[_page([])])
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


def test_a_named_get_is_bounded(monkeypatch):
    """§9.1 per-call timeout: the collection bounds EVERY strict request, not just list pages.

    Python bounds each strict call with the per-instance request timeout; the collection has no
    client instance, so it passes STRICT_READ_REQUEST_TIMEOUT. An unbounded named GET can hang
    indefinitely and would break that parity.
    """
    client = _FakeClient(
        resource=object(),
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
