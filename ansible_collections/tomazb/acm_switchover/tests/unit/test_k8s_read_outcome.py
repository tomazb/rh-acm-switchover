"""Unit tests for acm_k8s_read_outcome fail-closed read semantics."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ResourceNotFoundError,
    api_exception,
)

SENTINEL = "R302-SENTINEL-HTTP-BODY"


def _import_module_under_test():
    """Import the module without exposing Galaxy collections to the unit lane."""

    def package(name: str) -> ModuleType:
        module = ModuleType(name)
        module.__path__ = []
        return module

    args_common = ModuleType("ansible_collections.kubernetes.core.plugins.module_utils.args_common")
    args_common.AUTH_ARG_SPEC = {}

    client = ModuleType("ansible_collections.kubernetes.core.plugins.module_utils.k8s.client")

    def unavailable_get_api_client(**_kwargs):
        raise AssertionError("unit test must patch get_api_client before use")

    client.get_api_client = unavailable_get_api_client

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


class _FakeClient:
    def __init__(self, *, resource=None, resource_error=None, get_result=None, get_error=None):
        self._resource = resource
        self._resource_error = resource_error
        self._get_result = get_result
        self._get_error = get_error
        self.get_calls = 0
        self.resource_calls = 0

    def resource(self, kind, api_version):
        self.resource_calls += 1
        if self._resource_error is not None:
            raise self._resource_error
        return self._resource

    def get(self, resource, **params):
        self.get_calls += 1
        if self._get_error is not None:
            raise self._get_error
        return self._get_result


class _DictResult(dict):
    def to_dict(self):
        return dict(self)


def test_successful_empty_list_is_ok(monkeypatch):
    client = _FakeClient(
        resource=object(),
        get_result=_DictResult({"kind": "PodList", "items": []}),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
            "label_selectors": ["app=x"],
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
        get_result=_DictResult({"kind": "PodList", "items": [pod]}),
    )
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "list",
            "api_version": "v1",
            "kind": "Pod",
            "namespace": "ns",
        },
        client=client,
    )
    assert result["read_status"] == "ok"
    assert result["resources"] == [pod]
    assert isinstance(result["resources"][0], dict)


def test_named_get_present_is_ok(monkeypatch):
    cm = {"kind": "ConfigMap", "metadata": {"name": "cfg", "namespace": "ns"}}
    client = _FakeClient(resource=object(), get_result=_DictResult(cm))
    result = _run_module(
        monkeypatch,
        params={
            "read_mode": "get",
            "api_version": "v1",
            "kind": "ConfigMap",
            "namespace": "ns",
            "name": "cfg",
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
        },
        client=client,
    )
    dumped = repr(result)
    assert SENTINEL not in dumped
    assert "token=" not in dumped
    assert set(result.keys()) <= {"changed", "read_status", "resources"}


def test_every_path_reports_changed_false(monkeypatch):
    cases = [
        (
            {
                "read_mode": "list",
                "api_version": "v1",
                "kind": "Pod",
                "namespace": "ns",
            },
            _FakeClient(
                resource=object(),
                get_result=_DictResult({"kind": "PodList", "items": []}),
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
