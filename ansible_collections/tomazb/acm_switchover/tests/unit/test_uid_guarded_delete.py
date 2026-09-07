# SPDX-License-Identifier: MIT
"""Unit tests for the UID-guarded delete state machine.

Written against the July deletion boundary before the implementation exists. Two
rules run through every case:

* **Only an API 404 means absent.** Discovery, authorization, TLS, timeout,
  transport and decode failures are *unverifiable*, and every one of them must fail
  closed. Absence is a positive proof obligation, never a default.
* **A same-name different-UID object is fatal wherever it appears**, and is left
  intact. The safe response to "this is not the object you proved" is to stop.
"""

from __future__ import annotations

from typing import Any

import pytest
from kubernetes.client.exceptions import ApiException

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.uid_guarded_delete import (
    REASON_NOT_FOUND,
    REASON_OK,
    REASON_TIMEOUT,
    REASON_UID_MISMATCH,
    REASON_UNVERIFIABLE,
    STAGE_ABSENT,
    STAGE_COMPLETED,
    STAGE_WOULD_DELETE,
    GuardedDeleteError,
    build_dynamic_client,
    run_guarded_delete,
    safe_api_reason,
)


def _obj(uid: str, resource_version: str = "1") -> dict:
    return {"metadata": {"name": "observability", "uid": uid, "resourceVersion": resource_version}}


class FakeResource:
    """Stands in for a dynamic-client resource.

    ``get_results`` is a list consumed one call at a time; each entry is either an
    object to return or an exception to raise. Recording every call is what lets the
    tests assert that a delete never happened, rather than only that a result looked
    right.
    """

    def __init__(self, get_results: list[Any], delete_result: Any = None) -> None:
        self.get_results = list(get_results)
        self.delete_result = delete_result
        self.calls: list[str] = []

    def get(self, name: str, namespace: str | None = None):
        self.calls.append("get")
        if not self.get_results:
            raise AssertionError("unexpected extra get()")
        item = self.get_results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def delete(self, name: str, namespace: str | None = None, body: Any = None):
        self.calls.append("delete")
        self.last_delete_body = body
        if isinstance(self.delete_result, Exception):
            raise self.delete_result
        return self.delete_result


def _run(
    resource,
    *,
    name: str = "observability",
    namespace: str | None = None,
    expected_uid: Any = "uid-1",
    check_mode: bool = False,
    wait_timeout: float = 30,
    wait_sleep: float = 0,
    monotonic: Any = None,
    sleep: Any = None,
):
    """Explicit keyword pass-through rather than a splatted dict.

    A ``**dict[str, object]`` splat is unverifiable to mypy and produced six
    arg-type errors against the gate's exact command; naming the parameters keeps the
    call site typed.
    """
    return run_guarded_delete(
        resource,
        name=name,
        namespace=namespace,
        expected_uid=expected_uid,
        check_mode=check_mode,
        wait_timeout=wait_timeout,
        wait_sleep=wait_sleep,
        monotonic=monotonic or iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0]).__next__,
        sleep=sleep or (lambda _s: None),
    )


# --------------------------------------------------------------------------- happy path


def test_changed_is_true_only_after_bounded_completion_and_confirmed_final_absence():
    """The success case, and the only path that may report ``changed: true``.

    The final GET is separate from the poll's own read on purpose: the poll observing
    absence is the poll's evidence, and the boundary requires one independent live
    proof before a completion is recorded.
    """
    resource = FakeResource(
        get_results=[
            _obj("uid-1"),  # initial read
            ApiException(status=404),  # poll sees absence
            ApiException(status=404),  # final independent proof
        ],
        delete_result={},
    )
    result = _run(resource)
    assert result["changed"] is True
    assert result["would_change"] is False
    assert result["stage"] == STAGE_COMPLETED
    assert result["reason"] == REASON_OK
    assert resource.calls == ["get", "delete", "get", "get"]


def test_the_delete_body_carries_the_uid_precondition():
    resource = FakeResource(
        get_results=[_obj("uid-1"), ApiException(status=404), ApiException(status=404)],
        delete_result={},
    )
    _run(resource)
    assert resource.last_delete_body["preconditions"]["uid"] == "uid-1"


def test_a_reappearance_after_observed_absence_is_not_a_completion():
    """Absence observed then contradicted means completion is unproven."""
    resource = FakeResource(
        get_results=[_obj("uid-1"), ApiException(status=404), _obj("uid-1")],
        delete_result={},
    )
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource)
    assert exc.value.reason == REASON_UNVERIFIABLE


# --------------------------------------------------------------------------- identity


def test_a_replacement_present_before_delete_is_fatal_and_survives():
    """Same name, different UID at the initial read: stop, and do not delete."""
    resource = FakeResource(get_results=[_obj("uid-REPLACEMENT")])
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource)
    assert exc.value.reason == REASON_UID_MISMATCH
    assert "delete" not in resource.calls, "the replacement must be left intact"


def test_a_replacement_appearing_during_polling_is_fatal_and_survives():
    """Recreated mid-poll. Treating this as success would report a teardown that did
    not happen, and a second delete would destroy someone else's object."""
    resource = FakeResource(
        get_results=[_obj("uid-1"), _obj("uid-REPLACEMENT")],
        delete_result={},
    )
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource)
    assert exc.value.reason == REASON_UID_MISMATCH
    assert resource.calls.count("delete") == 1, "no second delete against the replacement"


@pytest.mark.parametrize("status", [409, 412])
def test_precondition_failure_is_fatal_and_never_falls_back_to_a_name_only_delete(status):
    """The server refused because the live object is not the proved one. A retry
    without the precondition would delete whatever is there now -- the exact failure
    the precondition exists to prevent."""
    resource = FakeResource(get_results=[_obj("uid-1")], delete_result=ApiException(status=status))
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource)
    assert exc.value.reason == REASON_UID_MISMATCH
    assert resource.calls.count("delete") == 1, "exactly one delete, never an unconditional retry"


@pytest.mark.parametrize("uid", ["", "   ", None, 17])
def test_a_missing_expected_uid_is_refused_before_any_request(uid):
    resource = FakeResource(get_results=[])
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource, expected_uid=uid)
    assert exc.value.reason == REASON_UID_MISMATCH
    assert resource.calls == [], "no request may be issued without a proved identity"


# --------------------------------------------------------------------------- absence


def test_an_already_absent_object_reports_not_changed_without_deleting():
    resource = FakeResource(get_results=[ApiException(status=404)])
    result = _run(resource)
    assert result["changed"] is False
    assert result["would_change"] is False
    assert result["stage"] == STAGE_ABSENT
    assert result["reason"] == REASON_NOT_FOUND
    assert "delete" not in resource.calls


def test_disappearance_between_read_and_delete_is_not_reported_as_changed():
    """The object went away before this invocation's delete landed. This invocation
    did not change anything, and must not claim it did."""
    resource = FakeResource(get_results=[_obj("uid-1")], delete_result=ApiException(status=404))
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource)
    assert exc.value.reason == REASON_NOT_FOUND


@pytest.mark.parametrize(
    "failure",
    [
        ApiException(status=403),  # authorization
        ApiException(status=401),
        ApiException(status=500),  # transport / server
        ApiException(status=503),
        TimeoutError("read timed out"),  # timeout
        ValueError("could not decode response"),  # decode
        RuntimeError("discovery unavailable"),  # discovery
    ],
)
def test_only_a_404_means_absent_every_other_read_failure_fails_closed(failure):
    """The single most important property in this module. A 403 is not an empty
    cluster, and a decode failure is not a deleted object."""
    resource = FakeResource(get_results=[failure])
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource)
    assert exc.value.reason != REASON_NOT_FOUND
    assert "delete" not in resource.calls


def test_safe_api_reason_classifies_status_without_returning_server_text():
    assert safe_api_reason(ApiException(status=404)) == REASON_NOT_FOUND
    assert safe_api_reason(ApiException(status=500)) == REASON_UNVERIFIABLE
    assert safe_api_reason(RuntimeError("anything")) == REASON_UNVERIFIABLE


# --------------------------------------------------------------------------- check mode


def test_check_mode_reads_and_validates_uid_but_issues_no_delete():
    resource = FakeResource(get_results=[_obj("uid-1", resource_version="42")])
    result = _run(resource, check_mode=True)
    assert result["changed"] is False
    assert result["would_change"] is True
    assert result["stage"] == STAGE_WOULD_DELETE
    assert result["resource_version"] == "42"
    assert resource.calls == ["get"], "check mode stops after the read"


def test_check_mode_on_an_absent_object_predicts_no_change():
    resource = FakeResource(get_results=[ApiException(status=404)])
    result = _run(resource, check_mode=True)
    assert result["changed"] is False
    assert result["would_change"] is False
    assert result["stage"] == STAGE_ABSENT


def test_check_mode_still_refuses_a_replacement():
    """A preview must not report that it would delete an object it has not proved."""
    resource = FakeResource(get_results=[_obj("uid-REPLACEMENT")])
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource, check_mode=True)
    assert exc.value.reason == REASON_UID_MISMATCH


# --------------------------------------------------------------------------- budgets


def test_a_same_uid_object_that_never_disappears_fails_on_a_bounded_budget():
    """Bounded, and it fails rather than reporting success."""
    resource = FakeResource(
        get_results=[_obj("uid-1")] * 6,
        delete_result={},
    )
    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource, monotonic=iter([0.0, 0.0, 10.0, 20.0, 30.0, 40.0]).__next__, wait_timeout=10)
    assert exc.value.reason == REASON_TIMEOUT


def test_the_absence_budget_uses_a_monotonic_clock_not_wall_time():
    """A wall clock can be stepped backwards by NTP, which would extend the budget
    bounding a destructive operation. Pinned by injecting a clock the test controls."""
    import inspect

    from ansible_collections.tomazb.acm_switchover.plugins.module_utils import uid_guarded_delete

    source = inspect.getsource(uid_guarded_delete.await_absence)
    assert "time.time(" not in source
    assert "monotonic" in inspect.signature(uid_guarded_delete.await_absence).parameters


# --------------------------------------------------------------------------- routing


@pytest.mark.parametrize(
    "kubeconfig,context",
    [("", "ctx"), ("   ", "ctx"), (None, "ctx"), ("/kc", ""), ("/kc", None), ("/kc", "   ")],
)
def test_client_construction_refuses_implicit_routing(kubeconfig, context):
    """An ambient fallback would route a delete at whatever cluster the environment
    happens to point at. Both are required, and neither may default."""
    with pytest.raises(ValueError):
        build_dynamic_client(kubeconfig, context, request_timeout=5)


def test_explicit_kubeconfig_and_context_reach_client_construction(monkeypatch):
    seen: dict = {}

    class _Cfg:
        timeout = None

    class _ApiClient:
        configuration = _Cfg()

    def _fake_new_client(**kwargs):
        seen.update(kwargs)
        return _ApiClient()

    import kubernetes.config as k8s_config
    import kubernetes.dynamic as k8s_dynamic

    monkeypatch.setattr(k8s_config, "new_client_from_config", _fake_new_client)
    monkeypatch.setattr(k8s_dynamic, "DynamicClient", lambda api_client: api_client)

    build_dynamic_client("/tmp/kc", "primary-hub", request_timeout=7)
    assert seen["config_file"] == "/tmp/kc"
    assert seen["context"] == "primary-hub"
    assert seen["persist_config"] is False


# --------------------------------------------------------------------------- redaction


_SECRETS = [
    "apiVersion: v1\nclusters:\n- cluster:\n    server: https://api.example.com",  # kubeconfig text
    "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.PAYLOAD.SIG",  # bearer token
    "-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----",  # client cert
    "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",  # private key
    "authorization: Bearer sha256~abcdef",  # response header
    '{"kind":"Status","message":"secret-payload-value"}',  # response body
    "dXNlcm5hbWU6IHN1cGVyLXNlY3JldA==",  # base64 Secret material
]


@pytest.mark.parametrize("secret", _SECRETS)
def test_injected_api_error_content_never_reaches_the_result_or_message(secret):
    """Credential material routinely rides inside API error bodies and headers, and a
    kubeconfig path is itself an infrastructure detail. Nothing from the exception may
    be interpolated into anything an operator, a log, or a callback can see.

    This is the collection-side counterpart of issue #283.
    """
    failure = ApiException(status=500, reason=secret)
    failure.body = secret
    resource = FakeResource(get_results=[failure])

    with pytest.raises(GuardedDeleteError) as exc:
        _run(resource)

    rendered = f"{exc.value.message} {exc.value.reason} {exc.value.stage} {exc.value}"
    assert secret not in rendered
    for fragment in ("BEGIN RSA PRIVATE KEY", "Bearer ", "secret-payload-value", "eyJhbGci"):
        assert fragment not in rendered


def test_a_successful_result_carries_only_the_closed_vocabulary():
    """No server text, no headers, no object body -- only stage, reason and identity."""
    resource = FakeResource(
        get_results=[_obj("uid-1"), ApiException(status=404), ApiException(status=404)],
        delete_result={},
    )
    result = _run(resource)
    assert set(result) == {"changed", "would_change", "stage", "reason", "resource_version"}


# --------------------------------------------------------------------------- module wrapper


def _load_module():
    import importlib

    return importlib.import_module("ansible_collections.tomazb.acm_switchover.plugins.modules.acm_uid_guarded_delete")


def _fake_ansible_module(params: dict, captured: dict, check_mode: bool = False):
    class FakeModule:
        def __init__(self, **_kwargs):
            self.params = params
            self.check_mode = check_mode
            captured["argument_spec"] = _kwargs.get("argument_spec")
            captured["supports_check_mode"] = _kwargs.get("supports_check_mode")

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs
            raise SystemExit(1)

    return FakeModule


def _base_params(**overrides) -> dict:
    params = {
        "kubeconfig": "/tmp/kc",
        "context": "primary-hub",
        "api_version": "observability.open-cluster-management.io/v1beta2",
        "kind": "MultiClusterObservability",
        "resource_name": "multiclusterobservabilities",
        "namespace": None,
        "name": "observability",
        "expected_uid": "uid-1",
        "request_timeout": None,
        "wait_timeout": 30,
        # Deliberately positive: the module rejects a zero sleep, because a zero-delay
        # absence poll busy-loops against the API server. Pinned below.
        "wait_sleep": 0.01,
    }
    params.update(overrides)
    return params


def test_routing_and_plural_options_are_required():
    """An ambient fallback is the failure this boundary exists to prevent, so neither
    routing option may default.

    ``resource_name`` is required because the canonical plural must never be
    synthesized from ``kind`` -- the contract acm_k8s_read_outcome established.
    """
    module = _load_module()
    spec = module._argument_spec()
    for option in ("kubeconfig", "context", "resource_name", "api_version", "kind", "name", "expected_uid"):
        assert spec[option]["required"] is True, f"{option} must be required"
    assert "default" not in spec["kubeconfig"]
    assert "default" not in spec["context"]


def test_the_module_supports_check_mode(monkeypatch):
    module = _load_module()
    captured: dict = {}
    monkeypatch.setattr(module, "AnsibleModule", _fake_ansible_module(_base_params(), captured))
    monkeypatch.setattr(module, "_resolve_resource", lambda *a, **k: FakeResource([ApiException(status=404)]))
    with pytest.raises(SystemExit):
        module.main()
    assert captured["supports_check_mode"] is True


def test_a_successful_run_exits_with_only_the_closed_result_vocabulary(monkeypatch):
    module = _load_module()
    captured: dict = {}
    resource = FakeResource(
        get_results=[_obj("uid-1"), ApiException(status=404), ApiException(status=404)],
        delete_result={},
    )
    monkeypatch.setattr(module, "AnsibleModule", _fake_ansible_module(_base_params(), captured))
    monkeypatch.setattr(module, "_resolve_resource", lambda *a, **k: resource)
    with pytest.raises(SystemExit):
        module.main()
    assert set(captured["exit"]) == {"changed", "would_change", "stage", "reason", "resource_version"}
    assert captured["exit"]["changed"] is True


def test_a_guarded_delete_failure_fails_the_module_with_the_safe_message(monkeypatch):
    module = _load_module()
    captured: dict = {}
    resource = FakeResource(get_results=[_obj("uid-REPLACEMENT")])
    monkeypatch.setattr(module, "AnsibleModule", _fake_ansible_module(_base_params(), captured))
    monkeypatch.setattr(module, "_resolve_resource", lambda *a, **k: resource)
    with pytest.raises(SystemExit):
        module.main()
    failure = captured["fail"]
    assert failure["reason"] == REASON_UID_MISMATCH
    assert failure["changed"] is False
    assert "left intact" in failure["msg"]


@pytest.mark.parametrize("secret", _SECRETS)
def test_module_failure_output_never_carries_injected_error_content(monkeypatch, secret):
    """The redaction contract asserted at the surface an operator and a callback
    actually see, not only inside the state machine."""
    module = _load_module()
    captured: dict = {}
    failure = ApiException(status=500, reason=secret)
    failure.body = secret
    monkeypatch.setattr(module, "AnsibleModule", _fake_ansible_module(_base_params(), captured))
    monkeypatch.setattr(module, "_resolve_resource", lambda *a, **k: FakeResource([failure]))
    with pytest.raises(SystemExit):
        module.main()
    rendered = " ".join(str(value) for value in captured["fail"].values())
    assert secret not in rendered


def test_a_resource_resolution_failure_fails_closed_without_leaking(monkeypatch):
    """Discovery is one of the unverifiable classes: it must not read as absent, and
    its exception text -- which here contains a kubeconfig path -- must not surface."""
    module = _load_module()
    captured: dict = {}

    def _boom(*_a, **_k):
        raise RuntimeError("discovery blew up with /tmp/kubeconfig inside")

    monkeypatch.setattr(module, "AnsibleModule", _fake_ansible_module(_base_params(), captured))
    monkeypatch.setattr(module, "_resolve_resource", _boom)
    with pytest.raises(SystemExit):
        module.main()
    failure = captured["fail"]
    assert failure["reason"] == REASON_UNVERIFIABLE
    assert failure["changed"] is False
    assert "/tmp/kubeconfig" not in " ".join(str(v) for v in failure.values())


def test_a_zero_or_negative_poll_interval_is_refused(monkeypatch):
    """A zero-delay absence poll busy-loops against the API server, so it is refused
    rather than silently accepted. Nothing is deleted."""
    module = _load_module()
    for bad in (0, -1):
        captured: dict = {}
        resource = FakeResource(get_results=[])
        monkeypatch.setattr(module, "AnsibleModule", _fake_ansible_module(_base_params(wait_sleep=bad), captured))
        monkeypatch.setattr(module, "_resolve_resource", lambda *a, **k: resource)
        with pytest.raises(SystemExit):
            module.main()
        assert captured["fail"]["changed"] is False
        assert "positive number" in captured["fail"]["msg"]
        assert resource.calls == []
