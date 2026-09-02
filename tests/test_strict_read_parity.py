"""Parity contract: the strict read algebra must agree across form factors.

Vectors are declared once and asserted against both implementations. The two
runtimes share no code; this file is what keeps them equal.
"""

import json
import sys
import types
from unittest.mock import Mock, patch

import pytest
from kubernetes.client.rest import ApiException
from kubernetes.dynamic.exceptions import ResourceNotFoundError, api_exception

from lib.constants import STRICT_READ_MAX_PAGES, STRICT_READ_PAGE_LIMIT
from lib.kube_client import KubeClient
from lib.strict_read import StrictReadStatus

# (vector id, normative outcome, python status, collection read_status, expected revision)
#
# The last column is the exact revision both form factors must publish, and `None` means both
# must publish no revision at all (Python `resource_version is None`, collection `null`). It is
# what makes §10.2.1b's provenance rule parity-checkable rather than described.
VECTORS = [
    ("true_empty", "success, complete inventory", StrictReadStatus.ITEMS, "ok", "100"),
    ("complete_pagination", "success, complete inventory", StrictReadStatus.ITEMS, "ok", "100"),
    ("object_absent", "named-object absence", StrictReadStatus.OBJECT_ABSENT, "not_found", None),
    ("kind_not_served", "positive kind-not-served", StrictReadStatus.CRD_ABSENT, "kind_not_served", None),
    ("namespace_absent", "positive namespace absence", StrictReadStatus.NAMESPACE_ABSENT, "not_found", None),
    ("named_get_success", "success, complete inventory", StrictReadStatus.ITEMS, "ok", "77"),
    ("authorization_failure", "api failure", StrictReadStatus.ERROR, "error", None),
    ("transport_failure", "api failure", StrictReadStatus.ERROR, "error", None),
    ("discovery_unverifiable", "api failure", StrictReadStatus.ERROR, "error", None),
    ("discovery_http_404", "api failure", StrictReadStatus.ERROR, "error", None),
    ("malformed_discovery", "malformed response", StrictReadStatus.ERROR, "error", None),
    # A malformed entry makes the whole document unverifiable whatever order the server served
    # its entries in. Stopping at the first match would let one response mean `served` or
    # `unverifiable` depending on ordering alone, which no declared vector could pin down.
    ("malformed_discovery_after_match", "malformed response", StrictReadStatus.ERROR, "error", None),
    ("malformed_discovery_before_match", "malformed response", StrictReadStatus.ERROR, "error", None),
    ("malformed_items", "malformed response", StrictReadStatus.ERROR, "error", None),
    ("missing_items_key", "malformed response", StrictReadStatus.ERROR, "error", None),
    ("missing_list_revision", "malformed response", StrictReadStatus.ERROR, "error", None),
    # A3.0 rule 8: a continuation page served at a revision other than page 1's is not part of
    # the same snapshot, so the whole read fails closed in both form factors.
    ("inconsistent_continuation_revision", "malformed response", StrictReadStatus.ERROR, "error", None),
    # A3.0 rule 9: a well-formed named GET whose object carries no revision is a malformed
    # response, never an `ITEMS`/`ok` with a null revision.
    ("named_get_missing_revision", "malformed response", StrictReadStatus.ERROR, "error", None),
    ("later_page_failure", "truncation / incomplete", StrictReadStatus.ERROR, "error", None),
    ("outstanding_continuation", "truncation / incomplete", StrictReadStatus.ERROR, "error", None),
    # Page 1 of the restarted read is served at "200"; the abandoned read's "100" is discarded.
    ("expired_continuation_restart", "success, complete inventory", StrictReadStatus.ITEMS, "ok", "200"),
    ("second_expired_continuation", "truncation / incomplete", StrictReadStatus.ERROR, "error", None),
    ("timeout_exhausted", "timeout / retry exhaustion", StrictReadStatus.ERROR, "error", None),
]


# --------------------------------------------------------------------------------------------
# Shared vector fixtures.
#
# Both runners drive the real implementations: the Python runner builds a live
# `lib.kube_client.KubeClient` (mocking only its transports) and calls a real strict method; the
# collection runner imports and executes the real `acm_k8s_read_outcome` module. Neither runner
# hand-constructs a `StrictReadOutcome` or a result dict.
# --------------------------------------------------------------------------------------------

_GROUP = "g"
_VERSION = "v1"
_PLURAL = "widgets"

_OBS_GROUP = "observability.open-cluster-management.io"
_OBS_VERSION = "v1beta2"
_OBS_PLURAL = "multiclusterobservabilities"


def _raw_body(payload):
    """A raw client response: the Python prover decodes the discovery body itself.

    Returning a decoded mapping here would mock away the decode step and hide a client
    signature change, which is exactly how the `response_type`/`response_types_map` rename
    reached the branch unnoticed.
    """
    return Mock(data=json.dumps(payload).encode("utf-8"))


def _served_discovery(name=_PLURAL, kind="Widget"):
    return _raw_body({"kind": "APIResourceList", "resources": [{"name": name, "kind": kind}]})


def _unserved_discovery(excluding):
    # A structurally valid APIResourceList that simply does not list `excluding`.
    resources = [{"name": "pods", "kind": "Pod"}]
    assert all(entry["name"] != excluding for entry in resources), "fixture must not actually serve `excluding`"
    return _raw_body({"kind": "APIResourceList", "resources": resources})


# ==============================================================================================
# Python runner: drives lib.kube_client.KubeClient's real strict methods.
# ==============================================================================================


def _python_client(*, call_api=None, list_effects=None, get_effects=None, namespace_effects=None):
    """Build a live KubeClient with only its transports mocked (per TestDiscoveryProver/
    TestStrictCustomResourceReads/TestStrictCoreReads in tests/test_kube_client.py).

    `_discovery_serves` is never mocked: `_api_client.call_api` is, so discovery vectors exercise
    the real prover.
    """
    client = KubeClient.__new__(KubeClient)
    client.request_timeout = 30
    client.dry_run = False
    client._api_client = Mock()
    client._api_client.call_api = call_api if call_api is not None else Mock(return_value=_served_discovery())
    client.custom_api = Mock()
    client.core_v1 = Mock()
    if list_effects is not None:
        client.custom_api.list_cluster_custom_object = Mock(side_effect=list_effects)
    if get_effects is not None:
        client.custom_api.get_cluster_custom_object = Mock(side_effect=get_effects)
    if namespace_effects is not None:
        client.core_v1.read_namespace = Mock(side_effect=namespace_effects)
    return client


def _assert_list_calls_bounded(client, expected_call_count):
    calls = client.custom_api.list_cluster_custom_object.call_args_list
    assert len(calls) == expected_call_count
    for call in calls:
        assert call.kwargs["limit"] == STRICT_READ_PAGE_LIMIT
        assert call.kwargs["_request_timeout"] == client.request_timeout


def _python_true_empty():
    client = _python_client(list_effects=[{"items": [], "metadata": {"resourceVersion": "100"}}])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 1)
    return outcome


def _python_complete_pagination():
    pages = [
        {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok", "resourceVersion": "100"}},
        {"items": [{"metadata": {"name": "b"}}], "metadata": {"resourceVersion": "100"}},
    ]
    client = _python_client(list_effects=pages)
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 2)
    return outcome


def _python_object_absent():
    client = _python_client(get_effects=[ApiException(status=404)])
    outcome = client.get_custom_resource_strict(_GROUP, _VERSION, _PLURAL, "mch")
    return outcome


def _python_kind_not_served():
    call_api = Mock(return_value=_unserved_discovery(_OBS_PLURAL))
    client = _python_client(call_api=call_api, list_effects=[])
    outcome = client.list_custom_resources_strict(_OBS_GROUP, _OBS_VERSION, _OBS_PLURAL)
    client.custom_api.list_cluster_custom_object.assert_not_called()
    return outcome


def _python_namespace_absent():
    client = _python_client(namespace_effects=[ApiException(status=404)])
    outcome = client.get_namespace_strict("acm")
    assert client.core_v1.read_namespace.call_args.kwargs["_request_timeout"] == client.request_timeout
    return outcome


def _python_named_get_success():
    resource = {"metadata": {"name": "mch", "resourceVersion": "77"}}
    client = _python_client(get_effects=[resource])
    outcome = client.get_custom_resource_strict(_GROUP, _VERSION, _PLURAL, "mch")
    assert client.custom_api.get_cluster_custom_object.call_args.kwargs["_request_timeout"] == client.request_timeout
    return outcome


def _python_authorization_failure():
    client = _python_client(list_effects=[ApiException(status=403)])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 1)
    return outcome


def _python_transport_failure():
    client = _python_client(list_effects=[OSError("connection reset")])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 1)
    return outcome


def _python_discovery_unverifiable():
    call_api = Mock(side_effect=ApiException(status=503))
    client = _python_client(call_api=call_api, list_effects=[])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    client.custom_api.list_cluster_custom_object.assert_not_called()
    return outcome


def _python_discovery_http_404():
    call_api = Mock(side_effect=ApiException(status=404))
    client = _python_client(call_api=call_api, list_effects=[])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    client.custom_api.list_cluster_custom_object.assert_not_called()
    return outcome


def _python_malformed_discovery():
    call_api = Mock(return_value=_raw_body({"kind": "APIResourceList", "resources": [{"name": 7}]}))
    client = _python_client(call_api=call_api, list_effects=[])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    client.custom_api.list_cluster_custom_object.assert_not_called()
    return outcome


def _python_malformed_discovery_after_match():
    """The requested entry is valid and comes first; a malformed entry follows it.

    The list transport is stocked with a complete successful page, so a prover that returns on
    the first match publishes a healthy inventory at revision "100" instead of failing closed.
    """
    call_api = Mock(
        return_value=_raw_body(
            {"kind": "APIResourceList", "resources": [{"name": _PLURAL, "kind": "Widget"}, {"name": 7}]}
        )
    )
    client = _python_client(call_api=call_api, list_effects=[{"items": [], "metadata": {"resourceVersion": "100"}}])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    client.custom_api.list_cluster_custom_object.assert_not_called()
    return outcome


def _python_malformed_discovery_before_match():
    """The mirror: the malformed entry precedes the requested one. Already fails closed."""
    call_api = Mock(
        return_value=_raw_body(
            {"kind": "APIResourceList", "resources": [{"name": 7}, {"name": _PLURAL, "kind": "Widget"}]}
        )
    )
    client = _python_client(call_api=call_api, list_effects=[{"items": [], "metadata": {"resourceVersion": "100"}}])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    client.custom_api.list_cluster_custom_object.assert_not_called()
    return outcome


def _python_malformed_items():
    client = _python_client(list_effects=[{"items": "nope", "metadata": {"resourceVersion": "100"}}])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 1)
    return outcome


def _python_missing_items_key():
    client = _python_client(list_effects=[{"metadata": {"resourceVersion": "100"}}])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 1)
    return outcome


def _python_missing_list_revision():
    client = _python_client(list_effects=[{"items": [], "metadata": {}}])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 1)
    return outcome


def _python_inconsistent_continuation_revision():
    pages = [
        {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok", "resourceVersion": "100"}},
        {"items": [{"metadata": {"name": "b"}}], "metadata": {"resourceVersion": "999"}},
    ]
    client = _python_client(list_effects=pages)
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 2)
    assert outcome.items == []
    assert outcome.resource_version is None
    return outcome


def _python_named_get_missing_revision():
    resource = {"metadata": {"name": "mch"}}  # no resourceVersion key at all
    client = _python_client(get_effects=[resource])
    outcome = client.get_custom_resource_strict(_GROUP, _VERSION, _PLURAL, "mch")
    assert outcome.items == []
    assert outcome.resource_version is None
    return outcome


def _python_later_page_failure():
    pages = [
        {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok", "resourceVersion": "100"}},
        ApiException(status=500),
    ]
    client = _python_client(list_effects=pages)
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 2)
    return outcome


def _python_outstanding_continuation():
    page = {"items": [], "metadata": {"continue": "tok", "resourceVersion": "100"}}
    client = _python_client(list_effects=[page] * (STRICT_READ_MAX_PAGES + 5))
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, STRICT_READ_MAX_PAGES)
    return outcome


def _python_expired_continuation_restart():
    pages = [
        {"items": [{"metadata": {"name": "abandoned"}}], "metadata": {"continue": "tok", "resourceVersion": "100"}},
        ApiException(status=410),
        {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok", "resourceVersion": "200"}},
        {"items": [{"metadata": {"name": "b"}}], "metadata": {"resourceVersion": "200"}},
    ]
    client = _python_client(list_effects=pages)
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 4)
    calls = client.custom_api.list_cluster_custom_object.call_args_list
    assert calls[2].kwargs["_continue"] is None, "the restart must re-issue page 1 with no continuation token"
    assert [item["metadata"]["name"] for item in outcome.items] == [
        "a",
        "b",
    ], "no pre-410 prefix may be published"
    return outcome


def _python_second_expired_continuation():
    pages = [
        {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok", "resourceVersion": "100"}},
        ApiException(status=410),
        {"items": [{"metadata": {"name": "a"}}], "metadata": {"continue": "tok", "resourceVersion": "200"}},
        ApiException(status=410),
    ]
    client = _python_client(list_effects=pages)
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 4)
    assert outcome.items == []
    assert outcome.resource_version is None
    return outcome


def _python_timeout_exhausted():
    client = _python_client(list_effects=[TimeoutError("deadline exceeded")])
    outcome = client.list_custom_resources_strict(_GROUP, _VERSION, _PLURAL)
    _assert_list_calls_bounded(client, 1)
    return outcome


_PYTHON_VECTORS = {
    "true_empty": _python_true_empty,
    "complete_pagination": _python_complete_pagination,
    "object_absent": _python_object_absent,
    "kind_not_served": _python_kind_not_served,
    "namespace_absent": _python_namespace_absent,
    "named_get_success": _python_named_get_success,
    "authorization_failure": _python_authorization_failure,
    "transport_failure": _python_transport_failure,
    "discovery_unverifiable": _python_discovery_unverifiable,
    "discovery_http_404": _python_discovery_http_404,
    "malformed_discovery": _python_malformed_discovery,
    "malformed_discovery_after_match": _python_malformed_discovery_after_match,
    "malformed_discovery_before_match": _python_malformed_discovery_before_match,
    "malformed_items": _python_malformed_items,
    "missing_items_key": _python_missing_items_key,
    "missing_list_revision": _python_missing_list_revision,
    "inconsistent_continuation_revision": _python_inconsistent_continuation_revision,
    "named_get_missing_revision": _python_named_get_missing_revision,
    "later_page_failure": _python_later_page_failure,
    "outstanding_continuation": _python_outstanding_continuation,
    "expired_continuation_restart": _python_expired_continuation_restart,
    "second_expired_continuation": _python_second_expired_continuation,
    "timeout_exhausted": _python_timeout_exhausted,
}


# ==============================================================================================
# Collection runner: drives the real acm_k8s_read_outcome module.
# ==============================================================================================

_COLLECTION_MODULE = None


def _namespace_package(name):
    module = types.ModuleType(name)
    module.__path__ = []
    return module


def _stub_kubernetes_core_collection():
    """Stub `kubernetes.core`'s module_utils: it is not vendored in this repo."""
    args_common = types.ModuleType("ansible_collections.kubernetes.core.plugins.module_utils.args_common")
    args_common.AUTH_ARG_SPEC = {}

    client_module = types.ModuleType("ansible_collections.kubernetes.core.plugins.module_utils.k8s.client")

    def _unavailable_get_api_client(**_kwargs):
        raise AssertionError("test must patch get_api_client before use")

    client_module.get_api_client = _unavailable_get_api_client

    return {
        "ansible_collections.kubernetes": _namespace_package("ansible_collections.kubernetes"),
        "ansible_collections.kubernetes.core": _namespace_package("ansible_collections.kubernetes.core"),
        "ansible_collections.kubernetes.core.plugins": _namespace_package(
            "ansible_collections.kubernetes.core.plugins"
        ),
        "ansible_collections.kubernetes.core.plugins.module_utils": _namespace_package(
            "ansible_collections.kubernetes.core.plugins.module_utils"
        ),
        "ansible_collections.kubernetes.core.plugins.module_utils.args_common": args_common,
        "ansible_collections.kubernetes.core.plugins.module_utils.k8s": _namespace_package(
            "ansible_collections.kubernetes.core.plugins.module_utils.k8s"
        ),
        "ansible_collections.kubernetes.core.plugins.module_utils.k8s.client": client_module,
    }


def _stub_ansible_module_utils_basic():
    """Only used when ansible-core itself is not importable, so this file stays
    collection-import-safe without it (standing CI constraint, see AGENTS.md)."""
    basic_module = types.ModuleType("ansible.module_utils.basic")

    class _PlaceholderAnsibleModule:
        def __init__(self, *args, **kwargs):
            raise AssertionError("placeholder AnsibleModule must be monkeypatched before use")

    basic_module.AnsibleModule = _PlaceholderAnsibleModule
    return {
        "ansible": _namespace_package("ansible"),
        "ansible.module_utils": _namespace_package("ansible.module_utils"),
        "ansible.module_utils.basic": basic_module,
    }


def _import_collection_module():
    """Import acm_k8s_read_outcome lazily so root tests/ stays import-safe without ansible-core."""
    global _COLLECTION_MODULE
    if _COLLECTION_MODULE is not None:
        return _COLLECTION_MODULE

    import importlib

    stubs = _stub_kubernetes_core_collection()
    try:
        import ansible  # noqa: F401
    except ImportError:
        stubs.update(_stub_ansible_module_utils_basic())

    previous = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        module = importlib.import_module(
            "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_k8s_read_outcome"
        )
    finally:
        for name, prior in previous.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
    _COLLECTION_MODULE = module
    return module


def _collection_constants():
    import ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants as ans_constants

    return ans_constants


def _assert_collection_list_calls_bounded(client, expected_call_count):
    """Mirror of `_assert_list_calls_bounded` for the collection's list-mode page requests."""
    consts = _collection_constants()
    assert len(client.get_params) == expected_call_count
    for params in client.get_params:
        assert params["limit"] == consts.STRICT_READ_PAGE_LIMIT
        assert params["_request_timeout"] == consts.STRICT_READ_REQUEST_TIMEOUT


class _DictResult(dict):
    def to_dict(self):
        return dict(self)


class _RawDiscoveryResponse:
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
        self.request_calls = []

    def request(self, method, path, **params):
        self.request_calls.append({"method": method, "path": path, **params})
        if self._discovery_error is not None:
            raise self._discovery_error
        return _RawDiscoveryResponse(self._discovery)


class _FakeK8sClient:
    """Same shape as `_FakeClient` in
    ansible_collections/tomazb/acm_switchover/tests/unit/test_k8s_read_outcome.py."""

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
        self.get_params = []

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


def _collection_api_error(status, body="R4-03-vector-body"):
    """Build the real dynamic-client exception one status maps to (see
    kubernetes.dynamic.exceptions.api_exception)."""

    class _Resp:
        def __init__(self):
            self.status = status
            self.reason = "vector"
            self.data = body.encode("utf-8")
            self.headers = {}

        def getheaders(self):
            return {}

    return api_exception(ApiException(http_resp=_Resp()))


def _run_collection(params, client=None, client_error=None):
    module = _import_collection_module()
    captured = {}

    class _FakeAnsibleModule:
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
            self.check_mode = False

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs
            raise SystemExit(1)

    def _fake_get_api_client(module=None, **kwargs):
        if client_error is not None:
            raise client_error
        return client

    with patch.object(module, "AnsibleModule", _FakeAnsibleModule), patch.object(
        module, "get_api_client", _fake_get_api_client
    ):
        try:
            module.main()
        except SystemExit:
            pass
    if "exit" in captured:
        return captured["exit"]
    if "fail" in captured:
        return captured["fail"]
    raise AssertionError(f"collection module did not exit; captured={captured}")


_WIDGET_PARAMS = {"read_mode": "list", "api_version": "g/v1", "kind": "Widget", "resource_name": "widgets"}


def _collection_true_empty():
    client = _FakeK8sClient(
        resource=object(),
        get_result=_DictResult({"kind": "WidgetList", "items": [], "metadata": {"resourceVersion": "100"}}),
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 1)
    return result


def _collection_complete_pagination():
    client = _FakeK8sClient(
        resource=object(),
        pages=[
            _DictResult(
                {
                    "kind": "WidgetList",
                    "items": [{"metadata": {"name": "a"}}],
                    "metadata": {"continue": "tok", "resourceVersion": "100"},
                }
            ),
            _DictResult(
                {"kind": "WidgetList", "items": [{"metadata": {"name": "b"}}], "metadata": {"resourceVersion": "100"}}
            ),
        ],
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 2)
    return result


def _collection_object_absent():
    client = _FakeK8sClient(resource=object(), get_error=_collection_api_error(404))
    return _run_collection(
        {"read_mode": "get", "api_version": "g/v1", "kind": "Widget", "name": "mch", "resource_name": "widgets"},
        client=client,
    )


def _collection_kind_not_served():
    dynamic = _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": [{"name": "pods", "kind": "Pod"}]})
    client = _FakeK8sClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    return _run_collection(
        {
            "read_mode": "list",
            "api_version": f"{_OBS_GROUP}/{_OBS_VERSION}",
            "kind": "MultiClusterObservability",
            "resource_name": _OBS_PLURAL,
        },
        client=client,
    )


def _collection_namespace_absent():
    client = _FakeK8sClient(resource=object(), get_error=_collection_api_error(404))
    return _run_collection(
        {
            "read_mode": "get",
            "api_version": "v1",
            "kind": "Namespace",
            "name": "acm",
            "resource_name": "namespaces",
        },
        client=client,
    )


def _collection_named_get_success():
    client = _FakeK8sClient(
        resource=object(),
        get_result=_DictResult({"kind": "Widget", "metadata": {"name": "mch", "resourceVersion": "77"}}),
    )
    return _run_collection(
        {"read_mode": "get", "api_version": "g/v1", "kind": "Widget", "name": "mch", "resource_name": "widgets"},
        client=client,
    )


def _collection_authorization_failure():
    client = _FakeK8sClient(resource=object(), get_error=_collection_api_error(403))
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 1)
    return result


def _collection_transport_failure():
    client = _FakeK8sClient(resource=object(), get_error=OSError("connection reset"))
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 1)
    return result


def _collection_discovery_unverifiable():
    dynamic = _FakeDynamicClient(discovery_error=_collection_api_error(503))
    client = _FakeK8sClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    return _run_collection(_WIDGET_PARAMS, client=client)


def _collection_discovery_http_404():
    dynamic = _FakeDynamicClient(discovery_error=_collection_api_error(404))
    client = _FakeK8sClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    return _run_collection(_WIDGET_PARAMS, client=client)


def _collection_malformed_discovery():
    dynamic = _FakeDynamicClient(discovery={"kind": "APIResourceList", "resources": [{"name": 7}]})
    client = _FakeK8sClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    return _run_collection(_WIDGET_PARAMS, client=client)


def _collection_malformed_discovery_after_match():
    """Mirror of the Python vector: valid requested entry first, malformed entry after it.

    This call site maps both `True` and `None` to `error`, so the module's `read_status` is
    already `error` before the prover is fixed. The order-sensitivity itself is asserted on the
    prover directly in the collection unit lane; this vector holds the outcome contract equal.
    """
    dynamic = _FakeDynamicClient(
        discovery={"kind": "APIResourceList", "resources": [{"name": "widgets", "kind": "Widget"}, {"name": 7}]}
    )
    client = _FakeK8sClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    return _run_collection(_WIDGET_PARAMS, client=client)


def _collection_malformed_discovery_before_match():
    dynamic = _FakeDynamicClient(
        discovery={"kind": "APIResourceList", "resources": [{"name": 7}, {"name": "widgets", "kind": "Widget"}]}
    )
    client = _FakeK8sClient(resource_error=ResourceNotFoundError("no matches"), dynamic=dynamic)
    return _run_collection(_WIDGET_PARAMS, client=client)


def _collection_malformed_items():
    client = _FakeK8sClient(
        resource=object(),
        get_result=_DictResult({"kind": "WidgetList", "items": "nope", "metadata": {"resourceVersion": "100"}}),
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 1)
    return result


def _collection_missing_items_key():
    client = _FakeK8sClient(
        resource=object(),
        get_result=_DictResult({"kind": "WidgetList", "metadata": {"resourceVersion": "100"}}),
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 1)
    return result


def _collection_missing_list_revision():
    client = _FakeK8sClient(
        resource=object(),
        get_result=_DictResult({"kind": "WidgetList", "items": [], "metadata": {}}),
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 1)
    return result


def _collection_inconsistent_continuation_revision():
    client = _FakeK8sClient(
        resource=object(),
        pages=[
            _DictResult(
                {
                    "kind": "WidgetList",
                    "items": [{"metadata": {"name": "a"}}],
                    "metadata": {"continue": "tok", "resourceVersion": "100"},
                }
            ),
            _DictResult(
                {"kind": "WidgetList", "items": [{"metadata": {"name": "b"}}], "metadata": {"resourceVersion": "999"}}
            ),
        ],
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 2)
    assert result["resources"] == []
    assert result["resource_version"] is None
    return result


def _collection_named_get_missing_revision():
    client = _FakeK8sClient(
        resource=object(),
        get_result=_DictResult({"kind": "Widget", "metadata": {"name": "mch"}}),
    )
    result = _run_collection(
        {"read_mode": "get", "api_version": "g/v1", "kind": "Widget", "name": "mch", "resource_name": "widgets"},
        client=client,
    )
    assert result["resources"] == []
    assert result["resource_version"] is None
    return result


def _collection_later_page_failure():
    client = _FakeK8sClient(
        resource=object(),
        pages=[
            _DictResult(
                {
                    "kind": "WidgetList",
                    "items": [{"metadata": {"name": "a"}}],
                    "metadata": {"continue": "tok", "resourceVersion": "100"},
                }
            ),
            _collection_api_error(500),
        ],
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 2)
    return result


def _collection_outstanding_continuation():
    consts = _collection_constants()
    page = _DictResult({"kind": "WidgetList", "items": [], "metadata": {"continue": "tok", "resourceVersion": "100"}})
    client = _FakeK8sClient(resource=object(), pages=[page] * (consts.STRICT_READ_MAX_PAGES + 5))
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, consts.STRICT_READ_MAX_PAGES)
    return result


def _collection_expired_continuation_restart():
    client = _FakeK8sClient(
        resource=object(),
        pages=[
            _DictResult(
                {
                    "kind": "WidgetList",
                    "items": [{"metadata": {"name": "abandoned"}}],
                    "metadata": {"continue": "tok", "resourceVersion": "100"},
                }
            ),
            _collection_api_error(410),
            _DictResult(
                {
                    "kind": "WidgetList",
                    "items": [{"metadata": {"name": "a"}}],
                    "metadata": {"continue": "tok", "resourceVersion": "200"},
                }
            ),
            _DictResult(
                {"kind": "WidgetList", "items": [{"metadata": {"name": "b"}}], "metadata": {"resourceVersion": "200"}}
            ),
        ],
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 4)
    assert client.get_params[2].get("_continue") is None, "the restart must re-issue page 1 with no continuation token"
    assert [r["metadata"]["name"] for r in result["resources"]] == [
        "a",
        "b",
    ], "no pre-410 prefix may be published"
    return result


def _collection_second_expired_continuation():
    client = _FakeK8sClient(
        resource=object(),
        pages=[
            _DictResult(
                {
                    "kind": "WidgetList",
                    "items": [{"metadata": {"name": "a"}}],
                    "metadata": {"continue": "tok", "resourceVersion": "100"},
                }
            ),
            _collection_api_error(410),
            _DictResult(
                {
                    "kind": "WidgetList",
                    "items": [{"metadata": {"name": "a"}}],
                    "metadata": {"continue": "tok", "resourceVersion": "200"},
                }
            ),
            _collection_api_error(410),
        ],
    )
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 4)
    assert result["resources"] == []
    assert result["resource_version"] is None
    return result


def _collection_timeout_exhausted():
    client = _FakeK8sClient(resource=object(), get_error=TimeoutError("deadline exceeded"))
    result = _run_collection(_WIDGET_PARAMS, client=client)
    _assert_collection_list_calls_bounded(client, 1)
    return result


_COLLECTION_VECTORS = {
    "true_empty": _collection_true_empty,
    "complete_pagination": _collection_complete_pagination,
    "object_absent": _collection_object_absent,
    "kind_not_served": _collection_kind_not_served,
    "namespace_absent": _collection_namespace_absent,
    "named_get_success": _collection_named_get_success,
    "authorization_failure": _collection_authorization_failure,
    "transport_failure": _collection_transport_failure,
    "discovery_unverifiable": _collection_discovery_unverifiable,
    "discovery_http_404": _collection_discovery_http_404,
    "malformed_discovery": _collection_malformed_discovery,
    "malformed_discovery_after_match": _collection_malformed_discovery_after_match,
    "malformed_discovery_before_match": _collection_malformed_discovery_before_match,
    "malformed_items": _collection_malformed_items,
    "missing_items_key": _collection_missing_items_key,
    "missing_list_revision": _collection_missing_list_revision,
    "inconsistent_continuation_revision": _collection_inconsistent_continuation_revision,
    "named_get_missing_revision": _collection_named_get_missing_revision,
    "later_page_failure": _collection_later_page_failure,
    "outstanding_continuation": _collection_outstanding_continuation,
    "expired_continuation_restart": _collection_expired_continuation_restart,
    "second_expired_continuation": _collection_second_expired_continuation,
    "timeout_exhausted": _collection_timeout_exhausted,
}


def run_python_vector(vector_id):
    try:
        handler = _PYTHON_VECTORS[vector_id]
    except KeyError:
        raise NotImplementedError(vector_id)
    return handler()


def run_collection_vector(vector_id):
    try:
        handler = _COLLECTION_VECTORS[vector_id]
    except KeyError:
        raise NotImplementedError(vector_id)
    return handler()


@pytest.mark.parametrize("vector_id, _normative, expected_status, _collection, _revision", VECTORS)
def test_python_strict_surface_matches_the_vector(vector_id, _normative, expected_status, _collection, _revision):
    outcome = run_python_vector(vector_id)
    assert outcome.status is expected_status


@pytest.mark.parametrize("vector_id, _normative, _python, expected_read_status, _revision", VECTORS)
def test_collection_module_matches_the_vector(vector_id, _normative, _python, expected_read_status, _revision):
    result = run_collection_vector(vector_id)
    assert result["read_status"] == expected_read_status


@pytest.mark.parametrize("vector_id, _normative, python_status, collection_status, _revision", VECTORS)
def test_error_is_never_absence_in_either_form_factor(
    vector_id, _normative, python_status, collection_status, _revision
):
    if python_status is not StrictReadStatus.ERROR:
        return
    assert run_python_vector(vector_id).proves_absence is False
    assert run_collection_vector(vector_id)["resources"] == []


@pytest.mark.parametrize("vector_id, _normative, _python, _collection, expected_revision", VECTORS)
def test_both_form_factors_publish_the_same_revision(vector_id, _normative, _python, _collection, expected_revision):
    """§10.2.1b provenance, held equal: same revision, or no revision, on both sides."""
    assert run_python_vector(vector_id).resource_version == expected_revision
    assert run_collection_vector(vector_id)["resource_version"] == expected_revision


@pytest.mark.parametrize("vector_id, _normative, python_status, _collection, expected_revision", VECTORS)
def test_no_vector_synthesizes_a_revision(vector_id, _normative, python_status, _collection, expected_revision):
    """A non-success outcome must publish no revision, not an empty string or a placeholder."""
    if python_status is StrictReadStatus.ITEMS:
        return
    assert expected_revision is None
    assert run_python_vector(vector_id).resource_version is None
    assert run_collection_vector(vector_id)["resource_version"] is None


def test_strict_read_bounds_are_mirrored():
    import inspect

    import ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants as ans_constants
    import lib.constants as py_constants
    from lib.kube_client import KubeClient

    for name in ("STRICT_READ_PAGE_LIMIT", "STRICT_READ_MAX_PAGES", "STRICT_READ_MAX_RESTARTS"):
        assert getattr(py_constants, name) == getattr(ans_constants, name), name
    # Python bounds each call with the per-instance request timeout; the collection
    # module has no instance, so its constant must equal that default.
    default_timeout = inspect.signature(KubeClient.__init__).parameters["request_timeout"].default
    assert ans_constants.STRICT_READ_REQUEST_TIMEOUT == default_timeout
