"""Unit tests for acm_pod_owner_classify: the collection MCH identity and owner-chain boundary.

Every test drives the real module and the real module_utils/k8s_read.py strict reads through
a fake kubernetes.core K8SClient, so request bounds, pagination checks and read-status mapping
are the shipped ones. Objects use the camelCase shapes the dynamic client returns.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

import pytest
from kubernetes.client.exceptions import ApiException
from kubernetes.dynamic.exceptions import ForbiddenError, InternalServerError, NotFoundError, api_exception

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import constants

SENTINEL = "E5-SENTINEL-HTTP-BODY"
ACM_NS = "open-cluster-management"
MCH_KEY = "operator.open-cluster-management.io/v1/MultiClusterHub/open-cluster-management/multiclusterhub"
MCH_UID = "uid-mch"
CSV_NAME = "advanced-cluster-management.v2.13.0"
CSV_UID = "uid-csv"
DEPLOYMENT_NAME = "multiclusterhub-operator"
DEPLOYMENT_UID = "uid-deployment-recorded"
MCH_CRD = "multiclusterhubs.operator.open-cluster-management.io"
CSV_API_VERSION = "operators.coreos.com/v1alpha1"


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
        return importlib.import_module(
            "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_pod_owner_classify"
        )
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


acm_pod_owner_classify = _import_module_under_test()


def _api_error(exc_type: type[Exception], status: int, body: str = SENTINEL) -> Exception:
    class _Resp:
        def __init__(self):
            self.status = status
            self.reason = "error"
            self.data = body.encode("utf-8")
            self.headers = {"Authorization": f"Bearer {SENTINEL}"}

        def getheaders(self):
            return self.headers

    wrapped = api_exception(ApiException(http_resp=_Resp()))
    assert isinstance(wrapped, exc_type), f"expected {exc_type}, got {type(wrapped)}"
    return wrapped


class _Resource:
    def __init__(self, api_version: str, kind: str):
        self.api_version = api_version
        self.kind = kind


class _PositiveDiscovery:
    """A discovery document that validates and serves none of the kinds under test."""

    def request(self, method, path, **params):
        class _Raw:
            data = b'{"kind": "APIResourceList", "resources": [{"name": "unrelated", "kind": "Unrelated"}]}'

        return _Raw()


class _FakeK8sClient:
    """A kubernetes.core K8SClient stand-in keyed by (verb, kind, name).

    Each scripted key holds a list of responses consumed in order, the last repeating; a
    response that is an exception is raised. Every request is recorded with the parameters
    the shipped strict read sent, so bounds and routing can be asserted.
    """

    def __init__(
        self,
        responses: dict[tuple[str, str, str | None], list[Any]],
        unserved_from: dict[str, int] | None = None,
    ):
        self._responses = {key: list(values) for key, values in responses.items()}
        self.requests: list[dict] = []
        # `unserved_from[kind] = n`: from the n-th resolution of `kind` (0-based) the kind no
        # longer resolves, and discovery positively lists nothing for it (kind_not_served).
        self._unserved_from = dict(unserved_from or {})
        self._resolutions: dict[str, int] = {}
        self.client = _PositiveDiscovery()

    def resource(self, kind, api_version):
        index = self._resolutions.get(kind, 0)
        self._resolutions[kind] = index + 1
        if kind in self._unserved_from and index >= self._unserved_from[kind]:
            raise LookupError(f"{kind} is not served")
        return _Resource(api_version, kind)

    def get(self, resource, **params):
        verb = "GET" if "name" in params else "LIST"
        self.requests.append(
            {
                "verb": verb,
                "api_version": resource.api_version,
                "kind": resource.kind,
                "namespace": params.get("namespace"),
                "name": params.get("name"),
                "label_selector": params.get("label_selector"),
                "timeout": params.get("_request_timeout"),
            }
        )
        key = (verb, resource.kind, params.get("name"))
        script = self._responses.get(key)
        if script is None:
            raise AssertionError(f"unscripted request {key}")
        response = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(response, BaseException):
            raise response
        return response

    def count(self, verb, kind):
        return sum(1 for request in self.requests if request["verb"] == verb and request["kind"] == kind)


def _list(kind: str, items: list[dict], revision: str = "list-1") -> dict:
    return {"kind": f"{kind}List", "items": items, "metadata": {"resourceVersion": revision}}


def _csv(*, name=CSV_NAME, uid=CSV_UID, phase="Succeeded", deployments=(DEPLOYMENT_NAME,), owned=(MCH_CRD,)):
    return {
        "apiVersion": CSV_API_VERSION,
        "kind": "ClusterServiceVersion",
        "metadata": {"name": name, "namespace": ACM_NS, "uid": uid, "resourceVersion": "csv-1"},
        "spec": {
            "customresourcedefinitions": {"owned": [{"name": crd} for crd in owned]},
            "install": {"strategy": "deployment", "spec": {"deployments": [{"name": d} for d in deployments]}},
        },
        "status": {"phase": phase},
    }


def _deployment(uid: str | None = DEPLOYMENT_UID, revision: str = "deploy-1") -> dict:
    metadata = {"name": DEPLOYMENT_NAME, "namespace": ACM_NS, "resourceVersion": revision}
    if uid is not None:
        metadata["uid"] = uid
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata}


def _namespace(revision: str = "ns-1") -> dict:
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ACM_NS, "resourceVersion": revision}}


def _controller(kind: str, name: str, uid: str, api_version: str = "apps/v1") -> dict:
    return {"apiVersion": api_version, "kind": kind, "name": name, "uid": uid, "controller": True}


def _pod(name: str, controller: dict | None = None) -> dict:
    metadata: dict = {"name": name, "namespace": ACM_NS}
    if controller is not None:
        metadata["ownerReferences"] = [controller]
    return {"apiVersion": "v1", "kind": "Pod", "metadata": metadata}


def _replicaset(name: str, uid: str, *, deployment_name=DEPLOYMENT_NAME, deployment_uid=DEPLOYMENT_UID) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": {
            "name": name,
            "namespace": ACM_NS,
            "uid": uid,
            "resourceVersion": f"{name}-rv",
            "ownerReferences": [_controller("Deployment", deployment_name, deployment_uid)],
        },
    }


def _operator_pod(name: str, rs_name: str = "mch-operator-rs", rs_uid: str = "uid-rs-1") -> dict:
    return _pod(name, _controller("ReplicaSet", rs_name, rs_uid))


def _operator_deployment_identity() -> dict:
    return {
        "namespace": ACM_NS,
        "name": DEPLOYMENT_NAME,
        "uid": DEPLOYMENT_UID,
        "discovery_method": constants.OPERATOR_IDENTITY_DISCOVERY_METHOD,
        "captured_at": "2026-09-17T00:00:00+00:00",
        "csv": {"namespace": ACM_NS, "name": CSV_NAME, "uid": CSV_UID, "owned_crd": MCH_CRD},
        "mch_teardown_key": MCH_KEY,
        "mch_expected_uid": MCH_UID,
    }


def _unavailable_identity() -> dict:
    return {
        "reason": "csv_absent",
        "discovery_method": constants.OPERATOR_IDENTITY_DISCOVERY_METHOD,
        "captured_at": "2026-09-17T00:00:00+00:00",
        "evidence_summary": "No ClusterServiceVersion owning the MultiClusterHub CRD was found.",
        "mch_teardown_key": MCH_KEY,
        "mch_expected_uid": MCH_UID,
    }


def _run(monkeypatch, *, params: dict[str, Any], client=None, client_error=None, check_mode=False) -> dict:
    captured: dict = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "kubeconfig": "/fixture/kubeconfig",
                "context": "source-hub",
                "namespace": ACM_NS,
                "mch_teardown_key": None,
                "mch_expected_uid": None,
                "operator_deployment": None,
                "operator_identity_unavailable": None,
                **params,
            }
            self.check_mode = check_mode

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs
            raise SystemExit(1)

    monkeypatch.setattr(acm_pod_owner_classify, "AnsibleModule", FakeModule)
    routed: dict = {}

    def fake_get_api_client(**kwargs):
        routed.update(kwargs)
        if client_error is not None:
            raise client_error
        return client

    monkeypatch.setattr(acm_pod_owner_classify, "get_api_client", fake_get_api_client)
    try:
        acm_pod_owner_classify.main()
    except SystemExit:
        pass
    result = captured.get("exit") or captured.get("fail")
    if result is None:
        raise AssertionError(f"module did not exit; captured={captured}")
    result["_routed"] = routed
    result["_failed"] = "fail" in captured
    return result


def _capture(monkeypatch, client, **kwargs):
    params = {"operation": "capture_identity", "mch_teardown_key": MCH_KEY, "mch_expected_uid": MCH_UID}
    return _run(monkeypatch, params=params, client=client, **kwargs)


def _classify(monkeypatch, client, identity: str = "captured", **kwargs):
    params: dict[str, Any] = {"operation": "classify"}
    if identity == "captured":
        params["operator_deployment"] = _operator_deployment_identity()
    else:
        params["operator_identity_unavailable"] = _unavailable_identity()
    return _run(monkeypatch, params=params, client=client, **kwargs)


def _capture_client(overrides: dict | None = None) -> _FakeK8sClient:
    responses = {
        ("LIST", "ClusterServiceVersion", None): [_list("ClusterServiceVersion", [_csv()])],
        ("GET", "ClusterServiceVersion", CSV_NAME): [_csv()],
        ("GET", "Deployment", DEPLOYMENT_NAME): [_deployment()],
    }
    responses.update(overrides or {})
    return _FakeK8sClient(responses)


def _classify_client(
    pods: list[dict], replicasets: dict[str, list[Any]] | None = None, overrides: dict | None = None
) -> _FakeK8sClient:
    responses: dict = {
        ("GET", "Namespace", ACM_NS): [_namespace()],
        ("LIST", "Pod", None): [_list("Pod", pods, "pods-1")],
        ("GET", "Deployment", DEPLOYMENT_NAME): [_deployment()],
    }
    for name, outcomes in (replicasets or {}).items():
        responses[("GET", "ReplicaSet", name)] = outcomes
    responses.update(overrides or {})
    return _FakeK8sClient(responses)


def _decisions(result: dict) -> dict[str, str]:
    return {entry["name"]: entry["decision"] for entry in result["decisions"]}


# ---------------------------------------------------------------------------------------- capture


def test_a_valid_capture_returns_the_exact_operator_deployment_shape(monkeypatch):
    client = _capture_client()

    result = _capture(monkeypatch, client)

    assert result["changed"] is False
    assert result["capture_status"] == "operator_deployment"
    assert result["operator_identity_unavailable"] is None
    identity = result["operator_deployment"]
    captured_at = identity["captured_at"]
    assert isinstance(captured_at, str) and captured_at.endswith("+00:00")
    assert identity == {**_operator_deployment_identity(), "captured_at": captured_at}


def test_capture_reads_csv_list_then_named_csv_then_deployment_each_bounded_and_routed(monkeypatch):
    client = _capture_client()

    result = _capture(monkeypatch, client)

    assert [(r["verb"], r["kind"], r["namespace"], r["name"]) for r in client.requests] == [
        ("LIST", "ClusterServiceVersion", ACM_NS, None),
        ("GET", "ClusterServiceVersion", ACM_NS, CSV_NAME),
        ("GET", "Deployment", ACM_NS, DEPLOYMENT_NAME),
    ]
    assert all(r["timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT for r in client.requests)
    assert client.requests[0]["api_version"] == CSV_API_VERSION
    assert result["_routed"] == {"kubeconfig": "/fixture/kubeconfig", "context": "source-hub"}


def test_csv_revalidation_takes_the_install_deployment_from_the_named_get_body(monkeypatch):
    client = _capture_client(
        overrides={
            ("LIST", "ClusterServiceVersion", None): [
                _list("ClusterServiceVersion", [_csv(deployments=("stale-name",))])
            ],
        }
    )

    result = _capture(monkeypatch, client)

    assert result["capture_status"] == "operator_deployment"
    assert [r["name"] for r in client.requests if r["kind"] == "Deployment"] == [DEPLOYMENT_NAME]


@pytest.mark.parametrize(
    "key, error",
    [
        (("LIST", "ClusterServiceVersion", None), InternalServerError),
        (("GET", "ClusterServiceVersion", CSV_NAME), ForbiddenError),
    ],
    ids=["csv-list", "csv-get"],
)
def test_a_csv_read_error_is_an_error_never_absence(monkeypatch, key, error):
    status = 500 if error is InternalServerError else 403
    client = _capture_client(overrides={key: [_api_error(error, status)]})

    result = _capture(monkeypatch, client)

    assert result["capture_status"] == "error"
    assert result["operator_deployment"] is None and result["operator_identity_unavailable"] is None
    assert client.count("GET", "Deployment") == 0


def test_a_csv_kind_no_longer_served_at_the_named_get_is_unavailable_csv_absent(monkeypatch):
    client = _FakeK8sClient(
        {("LIST", "ClusterServiceVersion", None): [_list("ClusterServiceVersion", [_csv()])]},
        unserved_from={"ClusterServiceVersion": 1},
    )

    result = _capture(monkeypatch, client)

    assert result["capture_status"] == "operator_identity_unavailable"
    assert result["operator_identity_unavailable"]["reason"] == "csv_absent"


def test_an_unserved_deployment_kind_is_deployment_read_failed_never_absence(monkeypatch):
    client = _FakeK8sClient(
        {
            ("LIST", "ClusterServiceVersion", None): [_list("ClusterServiceVersion", [_csv()])],
            ("GET", "ClusterServiceVersion", CSV_NAME): [_csv()],
        },
        unserved_from={"Deployment": 0},
    )

    result = _capture(monkeypatch, client)

    assert result["capture_status"] == "operator_identity_unavailable"
    assert result["operator_identity_unavailable"]["reason"] == "deployment_read_failed"


def test_a_csv_named_get_404_is_unavailable_csv_absent(monkeypatch):
    client = _capture_client(overrides={("GET", "ClusterServiceVersion", CSV_NAME): [_api_error(NotFoundError, 404)]})

    result = _capture(monkeypatch, client)

    assert result["capture_status"] == "operator_identity_unavailable"
    assert result["operator_identity_unavailable"]["reason"] == "csv_absent"


@pytest.mark.parametrize(
    "response, reason",
    [
        (_api_error(NotFoundError, 404), "install_deployment_absent"),
        (_api_error(ForbiddenError, 403), "deployment_read_failed"),
        (_deployment(uid=None), "deployment_identity_incomplete"),
    ],
    ids=["absent", "forbidden", "missing-uid"],
)
def test_deployment_read_outcomes_map_to_the_shared_unavailable_reasons(monkeypatch, response, reason):
    """The missing-uid case pins the collection's runtime reachability (E5 M4 ruling).

    The collection strict GET requires only the object's own resourceVersion, so a Deployment
    body without metadata.uid is a successful read and capture reports
    deployment_identity_incomplete. Python's get_deployment_strict rejects the same body as an
    error, which capture reports as deployment_read_failed; the shared abstract vector covers
    the semantic mapping on both sides.
    """
    client = _capture_client(overrides={("GET", "Deployment", DEPLOYMENT_NAME): [response]})

    result = _capture(monkeypatch, client)

    assert result["capture_status"] == "operator_identity_unavailable"
    unavailable = result["operator_identity_unavailable"]
    assert unavailable["reason"] == reason
    assert unavailable["mch_teardown_key"] == MCH_KEY and unavailable["mch_expected_uid"] == MCH_UID
    assert result["operator_deployment"] is None


def test_capture_client_construction_failure_is_a_sanitized_error(monkeypatch):
    result = _run(
        monkeypatch,
        params={"operation": "capture_identity", "mch_teardown_key": MCH_KEY, "mch_expected_uid": MCH_UID},
        client_error=Exception(f"kubeconfig token={SENTINEL}"),
    )

    assert result["capture_status"] == "error"
    assert SENTINEL not in repr(result)


def test_classify_client_construction_failure_claims_no_read_stage(monkeypatch):
    result = _run(
        monkeypatch,
        params={"operation": "classify", "operator_deployment": _operator_deployment_identity()},
        client_error=Exception(f"kubeconfig token={SENTINEL}"),
    )

    assert result["read_status"] == "error"
    assert result.get("read_error_stage", "<absent>") is None, "no Namespace or Pod read was performed"
    assert SENTINEL not in repr({k: v for k, v in result.items() if k != "_routed"})


def test_an_unexpected_failure_after_client_construction_claims_no_read_stage(monkeypatch):
    """The module's outer catch-all returns the generic classify error without inventing a stage."""

    def explode(*_args, **_kwargs):
        raise RuntimeError(f"programmer surprise {SENTINEL}")

    monkeypatch.setattr(acm_pod_owner_classify, "classify_pass", explode)
    result = _classify(monkeypatch, _classify_client([]))

    assert result["read_status"] == "error"
    assert result.get("read_error_stage", "<absent>") is None
    assert SENTINEL not in repr({k: v for k, v in result.items() if k != "_routed"})


# --------------------------------------------------------------------------------------- classify


def test_a_pod_owned_through_the_recorded_chain_is_operator_owned(monkeypatch):
    client = _classify_client(
        [_operator_pod("multiclusterhub-operator-abc")],
        {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1")]},
    )

    result = _classify(monkeypatch, client)

    assert result["changed"] is False
    assert result["read_status"] == "ok"
    assert result.get("read_error_stage", "<absent>") is None
    assert result["identity_status"] is None
    assert result["deployment_status"] == "matched"
    assert _decisions(result) == {"multiclusterhub-operator-abc": "operator_owned"}
    assert result["blocking_count"] == 0
    assert (result["namespace_resource_version"], result["pods_resource_version"]) == ("ns-1", "pods-1")
    assert result["deployment_resource_version"] == "deploy-1"


def test_a_prefixed_pod_without_an_owner_is_drain_blocking(monkeypatch):
    client = _classify_client([_pod("multiclusterhub-operator-spoofed")])

    result = _classify(monkeypatch, client)

    assert _decisions(result) == {"multiclusterhub-operator-spoofed": "drain_blocking"}
    assert result["blocking_count"] == 1
    assert client.count("GET", "ReplicaSet") == 0


def test_a_non_prefixed_pod_with_the_recorded_chain_is_operator_owned(monkeypatch):
    client = _classify_client(
        [_operator_pod("renamed-helper-xyz", rs_name="helper-rs", rs_uid="uid-rs-helper")],
        {"helper-rs": [_replicaset("helper-rs", "uid-rs-helper")]},
    )

    result = _classify(monkeypatch, client)

    assert _decisions(result) == {"renamed-helper-xyz": "operator_owned"}


def test_a_rolling_update_with_two_replicasets_is_owned(monkeypatch):
    client = _classify_client(
        [
            _operator_pod("mch-op-old-1", rs_name="rs-old", rs_uid="uid-rs-old"),
            _operator_pod("mch-op-new-1", rs_name="rs-new", rs_uid="uid-rs-new"),
        ],
        {"rs-old": [_replicaset("rs-old", "uid-rs-old")], "rs-new": [_replicaset("rs-new", "uid-rs-new")]},
    )

    result = _classify(monkeypatch, client)

    assert _decisions(result) == {"mch-op-old-1": "operator_owned", "mch-op-new-1": "operator_owned"}
    assert client.count("GET", "ReplicaSet") == 2


@pytest.mark.parametrize(
    "recorded",
    [_api_error(NotFoundError, 404), _deployment(uid="uid-replacement"), _api_error(ForbiddenError, 403)],
    ids=["absent", "replaced", "unreadable"],
)
def test_a_lost_recorded_deployment_is_inconsistent_even_with_zero_pods(monkeypatch, recorded):
    client = _classify_client([], overrides={("GET", "Deployment", DEPLOYMENT_NAME): [recorded]})

    result = _classify(monkeypatch, client)

    assert result["read_status"] == "ok"
    assert result["identity_status"] == "operator_identity_inconsistent"
    assert result["deployment_status"] == "inconsistent"
    assert result["deployment_resource_version"] is None
    assert result["blocking_count"] == 0 and result["decisions"] == []
    assert client.count("GET", "Deployment") == 1, "the recorded Deployment is re-read with zero Pods"


def test_a_recorded_deployment_read_without_uid_is_inconsistent(monkeypatch):
    client = _classify_client(
        [_operator_pod("multiclusterhub-operator-abc")],
        {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1")]},
        overrides={("GET", "Deployment", DEPLOYMENT_NAME): [_deployment(uid=None)]},
    )

    result = _classify(monkeypatch, client)

    assert result["identity_status"] == "operator_identity_inconsistent"
    assert _decisions(result) == {"multiclusterhub-operator-abc": "drain_blocking"}
    assert client.count("GET", "ReplicaSet") == 0, "no ReplicaSet can rescue an inconsistent identity"


def test_a_replacement_deployment_owning_the_replicaset_is_never_adopted(monkeypatch):
    client = _classify_client(
        [_operator_pod("multiclusterhub-operator-abc")],
        {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1", deployment_uid="uid-replacement")]},
        overrides={("GET", "Deployment", DEPLOYMENT_NAME): [_deployment(uid="uid-replacement")]},
    )

    result = _classify(monkeypatch, client)

    assert result["identity_status"] == "operator_identity_inconsistent"
    assert _decisions(result) == {"multiclusterhub-operator-abc": "drain_blocking"}


def test_an_unavailable_identity_excludes_no_pod_and_reads_no_ownership(monkeypatch):
    client = _classify_client(
        [_operator_pod("multiclusterhub-operator-abc")],
        {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1")]},
    )

    result = _classify(monkeypatch, client, identity="unavailable")

    assert result["read_status"] == "ok"
    assert result.get("read_error_stage", "<absent>") is None
    assert result["identity_status"] == "operator_identity_unavailable"
    assert result["deployment_status"] == "not_applicable"
    assert _decisions(result) == {"multiclusterhub-operator-abc": "drain_blocking"}
    assert result["blocking_count"] == 1
    assert client.count("GET", "Deployment") == 0 and client.count("GET", "ReplicaSet") == 0


def test_an_unavailable_identity_with_zero_pods_has_nothing_blocking(monkeypatch):
    client = _classify_client([])

    result = _classify(monkeypatch, client, identity="unavailable")

    assert (result["read_status"], result["blocking_count"]) == ("ok", 0)


def test_replicaset_reads_are_memoized_by_exact_identity_within_one_pass(monkeypatch):
    client = _classify_client(
        [
            _operator_pod("pod-a", rs_name="shared-rs", rs_uid="uid-rs-shared"),
            _operator_pod("pod-b", rs_name="shared-rs", rs_uid="uid-rs-shared"),
            _operator_pod("pod-c", rs_name="shared-rs", rs_uid="uid-rs-other"),
        ],
        {"shared-rs": [_replicaset("shared-rs", "uid-rs-shared")]},
    )

    result = _classify(monkeypatch, client)

    assert client.count("GET", "ReplicaSet") == 2, "same name with a different uid must not alias"
    assert _decisions(result) == {"pod-a": "operator_owned", "pod-b": "operator_owned", "pod-c": "drain_blocking"}


def test_a_second_invocation_rereads_everything(monkeypatch):
    client = _classify_client(
        [_operator_pod("pod-a"), _operator_pod("pod-b")],
        {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1")]},
    )

    _classify(monkeypatch, client)
    _classify(monkeypatch, client)

    assert client.count("GET", "ReplicaSet") == 2
    assert client.count("GET", "Deployment") == 2
    assert client.count("LIST", "Pod") == 2


def test_a_positively_absent_namespace_is_namespace_absent_with_no_further_reads(monkeypatch):
    client = _classify_client([], overrides={("GET", "Namespace", ACM_NS): [_api_error(NotFoundError, 404)]})

    result = _classify(monkeypatch, client)

    assert result["read_status"] == "namespace_absent"
    assert result.get("read_error_stage", "<absent>") is None
    assert result["blocking_count"] is None and result["decisions"] == []
    assert result["deployment_status"] == "not_applicable"
    assert [r["kind"] for r in client.requests] == ["Namespace"]


@pytest.mark.parametrize(
    "overrides, stage",
    [
        ({("GET", "Namespace", ACM_NS): [_api_error(ForbiddenError, 403)]}, "namespace"),
        ({("GET", "Namespace", ACM_NS): [_api_error(InternalServerError, 500)]}, "namespace"),
        ({("GET", "Namespace", ACM_NS): [{"kind": "Namespace", "metadata": {"name": ACM_NS}}]}, "namespace"),
        ({("LIST", "Pod", None): [_api_error(NotFoundError, 404)]}, "pods"),
        ({("LIST", "Pod", None): [_api_error(InternalServerError, 500)]}, "pods"),
        ({("LIST", "Pod", None): [{"kind": "PodList", "items": [], "metadata": {}}]}, "pods"),
    ],
    ids=[
        "namespace-forbidden",
        "namespace-500",
        "namespace-malformed",
        "pod-list-404",
        "pod-list-500",
        "pod-list-malformed",
    ],
)
def test_an_unverifiable_namespace_or_pod_read_is_error_never_absence_or_empty(monkeypatch, overrides, stage):
    """The stage is caller-policy evidence: Python E4 records recovery_required for an unreadable
    namespace but leaves the durable phase untouched for an unreadable Pod inventory."""
    client = _classify_client([], overrides=overrides)

    result = _classify(monkeypatch, client)

    assert result["read_status"] == "error"
    assert result.get("read_error_stage", "<absent>") == stage
    if stage == "namespace":
        assert client.count("LIST", "Pod") == 0, "no Pod inventory is read after an unverifiable namespace"
    assert result["blocking_count"] is None and result["decisions"] == []
    assert result["pods_resource_version"] is None
    assert client.count("GET", "Deployment") == 0


def test_the_pod_list_is_unscoped_and_every_request_is_bounded(monkeypatch):
    client = _classify_client(
        [_operator_pod("pod-a")], {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1")]}
    )

    _classify(monkeypatch, client)

    pod_lists = [r for r in client.requests if r["kind"] == "Pod"]
    assert pod_lists and all(r["label_selector"] is None and r["namespace"] == ACM_NS for r in pod_lists)
    assert all(r["timeout"] == constants.STRICT_READ_REQUEST_TIMEOUT for r in client.requests)


def test_check_mode_performs_the_same_reads_and_reports_no_change(monkeypatch):
    client = _classify_client(
        [_operator_pod("pod-a")], {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1")]}
    )

    result = _classify(monkeypatch, client, check_mode=True)

    assert result["changed"] is False and result["read_status"] == "ok"
    assert {r["verb"] for r in client.requests} <= {"GET", "LIST"}
    capture = _capture(monkeypatch, _capture_client(), check_mode=True)
    assert capture["changed"] is False and capture["capture_status"] == "operator_deployment"


def test_outputs_are_sanitized_and_use_a_closed_key_set(monkeypatch):
    client = _classify_client(
        [], overrides={("LIST", "Pod", None): [_api_error(ForbiddenError, 403, body=f"token={SENTINEL}")]}
    )

    classify = _classify(monkeypatch, client)
    capture = _capture(
        monkeypatch,
        _capture_client(overrides={("LIST", "ClusterServiceVersion", None): [_api_error(ForbiddenError, 403)]}),
    )

    for result in (classify, capture):
        # `_routed` and `_failed` are this harness's own records, not module output.
        dumped = repr({key: value for key, value in result.items() if key not in {"_routed", "_failed"}})
        assert SENTINEL not in dumped and "Bearer" not in dumped and "/fixture/kubeconfig" not in dumped
    assert set(classify) - {"_routed", "_failed"} == {
        "changed",
        "read_status",
        "read_error_stage",
        "namespace_resource_version",
        "pods_resource_version",
        "deployment_resource_version",
        "identity_status",
        "deployment_status",
        "decisions",
        "blocking_count",
    }
    assert set(capture) - {"_routed", "_failed"} == {
        "changed",
        "capture_status",
        "operator_deployment",
        "operator_identity_unavailable",
    }


# -------------------------------------------------------------------------------------- arguments


@pytest.mark.parametrize(
    "params",
    [
        {"operation": "classify", "operator_deployment": None, "operator_identity_unavailable": None},
        {
            "operation": "classify",
            "operator_deployment": {"name": DEPLOYMENT_NAME},
            "operator_identity_unavailable": {"reason": "csv_absent"},
        },
        {"operation": "classify", "operator_deployment": {"name": DEPLOYMENT_NAME, "namespace": ACM_NS}},
        {"operation": "capture_identity", "mch_teardown_key": "", "mch_expected_uid": MCH_UID},
        {"operation": "capture_identity", "mch_teardown_key": MCH_KEY, "mch_expected_uid": " "},
        {"operation": "capture_identity", "kubeconfig": " ", "mch_teardown_key": MCH_KEY, "mch_expected_uid": MCH_UID},
        {"operation": "capture_identity", "context": "", "mch_teardown_key": MCH_KEY, "mch_expected_uid": MCH_UID},
        {
            "operation": "capture_identity",
            "namespace": "default",
            "mch_teardown_key": MCH_KEY,
            "mch_expected_uid": MCH_UID,
        },
    ],
    ids=[
        "classify-no-identity",
        "classify-both-identities",
        "classify-identity-missing-uid",
        "capture-empty-key",
        "capture-blank-uid",
        "blank-kubeconfig",
        "empty-context",
        "wrong-namespace",
    ],
)
def test_invalid_arguments_fail_before_any_read(monkeypatch, params):
    client = _FakeK8sClient({})

    result = _run(monkeypatch, params=params, client=client)

    assert result["_failed"] is True
    assert result["changed"] is False
    assert client.requests == [] and result["_routed"] == {}


@pytest.mark.parametrize("present", ["operator_deployment", "operator_identity_unavailable"])
def test_ansible_argument_validation_accepts_the_record_shape_with_the_other_identity_null(monkeypatch, present):
    """The fake module above bypasses ansible-core's argument checks, so run them here.

    A teardown record and a capture result carry both identity keys with one of them null;
    ansible-core's constraint checks count present keys, so any key-presence constraint on
    the pair would refuse the module's own record shape before run_module could decide.
    """
    from ansible.module_utils.common.arg_spec import ArgumentSpecValidator

    constructed: dict = {}

    class CaptureConstruction:
        def __init__(self, *args, **kwargs):
            constructed.update(kwargs)
            raise SystemExit(0)

    monkeypatch.setattr(acm_pod_owner_classify, "AnsibleModule", CaptureConstruction)
    with pytest.raises(SystemExit):
        acm_pod_owner_classify.main()
    spec = constructed.pop("argument_spec")
    constructed.pop("supports_check_mode", None)
    identities = {"operator_deployment": _operator_deployment_identity(), "operator_identity_unavailable": None}
    if present == "operator_identity_unavailable":
        identities = {"operator_deployment": None, "operator_identity_unavailable": _unavailable_identity()}
    params = {
        "operation": "classify",
        "kubeconfig": "/fixture/kubeconfig",
        "context": "source-hub",
        "namespace": ACM_NS,
        **identities,
    }

    result = ArgumentSpecValidator(spec, **constructed).validate(params)

    assert result.error_messages == []


def test_the_module_imports_nothing_from_the_python_cli():
    import ast
    from pathlib import Path

    plugins = Path(acm_pod_owner_classify.__file__).resolve().parents[1]
    for path in (plugins / "modules" / "acm_pod_owner_classify.py", plugins / "module_utils" / "pod_owner_classify.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.split(".")[0] in {"lib", "modules", "tests"}, (path.name, node.module)
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in {"lib", "modules", "tests"} for alias in node.names)


# --------------------------------------------------------------------------------- request surface


def test_the_module_request_surface_per_operation(monkeypatch):
    """Collection E5 module request surface; the E6 role measurement is still required."""

    def shapes(client):
        return {(r["verb"], r["api_version"], r["kind"], r["namespace"]) for r in client.requests}

    capture = _capture_client()
    _capture(monkeypatch, capture)
    captured = _classify_client(
        [_operator_pod("pod-a")], {"mch-operator-rs": [_replicaset("mch-operator-rs", "uid-rs-1")]}
    )
    _classify(monkeypatch, captured)
    unavailable = _classify_client([_operator_pod("pod-a")])
    _classify(monkeypatch, unavailable, identity="unavailable")

    assert shapes(capture) == {
        ("LIST", CSV_API_VERSION, "ClusterServiceVersion", ACM_NS),
        ("GET", CSV_API_VERSION, "ClusterServiceVersion", ACM_NS),
        ("GET", "apps/v1", "Deployment", ACM_NS),
    }
    assert shapes(captured) == {
        ("GET", "v1", "Namespace", None),
        ("LIST", "v1", "Pod", ACM_NS),
        ("GET", "apps/v1", "Deployment", ACM_NS),
        ("GET", "apps/v1", "ReplicaSet", ACM_NS),
    }
    assert shapes(unavailable) == {("GET", "v1", "Namespace", None), ("LIST", "v1", "Pod", ACM_NS)}
