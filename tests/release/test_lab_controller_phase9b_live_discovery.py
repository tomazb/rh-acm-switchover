from __future__ import annotations

import ast
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.release.lab_controller.live_discovery import (
    IDENTITY_BUNDLE_QUERY_ID,
    IDENTITY_QUERY_IDS,
    ControllerOwnedLiveDiscoveryClient,
    LiveDiscoveryBounds,
    Phase9BDecision,
    Phase9BLiveDiscoveryRequest,
    Phase9BRuntimeHandle,
    TypedReadApi,
    TypedReadPage,
    TypedReadRequest,
    build_phase9b_identity_enrollment,
    fingerprint_identity_inputs,
    run_phase9b_live_discovery,
)
from tests.release.lab_controller.read_only_backend import (
    Phase9BReadOnlyBackendResult,
    ReadOnlyBackendDecision,
    ReadOnlyBackendPhase,
)
from tests.release.lab_controller.read_only_discovery import required_read_only_discovery_gate_ids
from tests.release.lab_controller.read_only_live_transport import (
    ReadOnlyLiveClientRequest,
    ReadOnlyLivePermanentError,
    ReadOnlyLiveSafetyError,
)

_MODULE_PATH = Path(__file__).resolve().parent / "lab_controller" / "live_discovery.py"
_SOURCE_REVISION = "a" * 40
_CONFIG_HASH = "b" * 64
_PROFILE_HASH = "c" * 64
_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


class _FakeClock:
    def __init__(self, now: datetime = _NOW, *, monotonic_step: float = 0.001) -> None:
        self.now = now
        self.monotonic_value = 100.0
        self.monotonic_step = monotonic_step

    def utcnow(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        value = self.monotonic_value
        self.monotonic_value += self.monotonic_step
        return value


class _UnsafeRepresentation:
    def __repr__(self) -> str:
        return "credential=" + "unsafe-value"


class _ScriptedTypedApi:
    """Deterministic typed API boundary; never contacts a cluster."""

    def __init__(
        self,
        scripts: Mapping[str, Sequence[Sequence[TypedReadPage | Exception]]],
    ) -> None:
        self._scripts = {query_id: deque(deque(run) for run in runs) for query_id, runs in scripts.items()}
        self._active: dict[str, deque[TypedReadPage | Exception]] = {}
        self.requests: list[TypedReadRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def read_page(self, request: TypedReadRequest) -> TypedReadPage:
        self.requests.append(request)
        if request.continuation_token is None:
            if request.query_id not in self._scripts or not self._scripts[request.query_id]:
                raise RuntimeError("unexpected fake query")
            self._active[request.query_id] = self._scripts[request.query_id].popleft()
        active = self._active[request.query_id]
        if not active:
            raise RuntimeError("fake page script exhausted")
        response = active.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def _identity_items(
    seed: str,
    *,
    infrastructure_name: str | None = None,
    duplicate: bool = False,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    items: dict[str, tuple[Mapping[str, Any], ...]] = {
        "identity.kube_system_namespace": (
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "kube-system", "uid": f"{seed}-namespace-uid"},
            },
        ),
        "identity.openshift_infrastructure": (
            {
                "apiVersion": "config.openshift.io/v1",
                "kind": "Infrastructure",
                "metadata": {"name": "cluster", "uid": f"{seed}-infrastructure-uid"},
                "status": {"infrastructureName": infrastructure_name or f"{seed}-infrastructure"},
            },
        ),
        "identity.openshift_cluster_version": (
            {
                "apiVersion": "config.openshift.io/v1",
                "kind": "ClusterVersion",
                "metadata": {"name": "version", "uid": f"{seed}-clusterversion-uid"},
                "status": {"desired": {"version": "4.18.7"}},
            },
        ),
    }
    if duplicate:
        items["identity.kube_system_namespace"] = items["identity.kube_system_namespace"] * 2
    return items


def _page(
    query_id: str,
    items: Sequence[Mapping[str, Any]],
    *,
    origin: str,
    revision: str = _SOURCE_REVISION,
    collected_at: datetime = _NOW,
    requested_token: str | None = None,
    continuation_token: str | None = None,
    resource_version: str = "101",
    remaining_item_count: int | None = 0,
    truncated: bool = False,
) -> TypedReadPage:
    return TypedReadPage(
        query_id=query_id,
        items=tuple(items),
        requested_continuation_token=requested_token,
        continuation_token=continuation_token,
        resource_version=resource_version,
        remaining_item_count=remaining_item_count,
        truncated=truncated,
        collected_at=collected_at,
        evidence_origin=origin,
        source_revision=revision,
    )


def _scripts(
    seed: str,
    origin: str,
    *,
    first_items: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    second_items: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    page_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, tuple[tuple[TypedReadPage, ...], ...]]:
    first = first_items or _identity_items(seed)
    second = second_items or first
    overrides = page_overrides or {}

    def _build_page(query_id: str, items: Sequence[Mapping[str, Any]]) -> TypedReadPage:
        page_kwargs: dict[str, Any] = {"origin": origin}
        page_kwargs.update(dict(overrides.get(query_id, {})))
        return _page(query_id, items, **page_kwargs)

    return {
        query_id: (
            (_build_page(query_id, first[query_id]),),
            (_build_page(query_id, second[query_id]),),
        )
        for query_id in IDENTITY_QUERY_IDS
    }


def _fingerprint(seed: str) -> str:
    return fingerprint_identity_inputs(
        kube_system_uid=f"{seed}-namespace-uid",
        infrastructure_uid=f"{seed}-infrastructure-uid",
        infrastructure_name=f"{seed}-infrastructure",
        cluster_version_uid=f"{seed}-clusterversion-uid",
    )


def _handle(
    public_hub_id: str,
    seed: str,
    origin: str,
    *,
    api: _ScriptedTypedApi | None = None,
    access_handle: object | None = None,
    context_handle: object | None = None,
) -> Phase9BRuntimeHandle:
    effective_access_handle = access_handle if access_handle is not None else object()
    effective_context_handle = context_handle if context_handle is not None else object()
    reader = api if api is not None else _ScriptedTypedApi(_scripts(seed, origin))
    return Phase9BRuntimeHandle(
        public_hub_id=public_hub_id,
        access_handle=effective_access_handle,
        context_handle=effective_context_handle,
        typed_api=TypedReadApi(
            access_handle=effective_access_handle,
            context_handle=effective_context_handle,
            reader=reader,
            timeout_contract="typed_request_timeout_v1",
        ),
        expected_evidence_origin=origin,
    )


def _request(
    *,
    handles: tuple[Phase9BRuntimeHandle, ...] | None = None,
    bounds: LiveDiscoveryBounds | None = None,
    **kwargs: Any,
) -> Phase9BLiveDiscoveryRequest:
    base: dict[str, Any] = {
        "allow_live_contact": True,
        "allow_read_only_queries": True,
        "approval_reference": "operator-approval-present",
        "source_revision": _SOURCE_REVISION,
        "expected_source_revision": _SOURCE_REVISION,
        "source_tree_clean": True,
        "config_sha256": _CONFIG_HASH,
        "profile_sha256": _PROFILE_HASH,
        "required_gate_ids": tuple(required_read_only_discovery_gate_ids()),
        "runtime_handles": (
            handles
            if handles is not None
            else (
                _handle("physical-hub-1", "alpha", "origin-alpha"),
                _handle("physical-hub-2", "bravo", "origin-bravo"),
            )
        ),
        "bounds": bounds or LiveDiscoveryBounds(),
    }
    base["identity_enrollment"] = build_phase9b_identity_enrollment(
        hub_fingerprints={
            "physical-hub-1": _fingerprint("alpha"),
            "physical-hub-2": _fingerprint("bravo"),
        },
        source_revision=_SOURCE_REVISION,
        config_sha256=_CONFIG_HASH,
        profile_sha256=_PROFILE_HASH,
    )
    base.update(kwargs)
    return Phase9BLiveDiscoveryRequest(**base)


def _run(request: Phase9BLiveDiscoveryRequest):
    return run_phase9b_live_discovery(request, clock=_FakeClock())


def _all_api_calls(request: Phase9BLiveDiscoveryRequest) -> int:
    return sum(
        int(getattr(getattr(handle.typed_api, "reader", None), "call_count", 0)) for handle in request.runtime_handles
    )


def test_proves_two_stable_distinct_multi_signal_physical_hubs() -> None:
    request = _request()
    result = _run(request)

    assert result.decision is Phase9BDecision.PASS
    assert result.artifact is not None
    assert request.identity_enrollment is not None
    assert len(result.identity_fingerprints) == 2
    assert len(set(result.identity_fingerprints.values())) == 2
    assert result.artifact["purpose"] == "live_read_only"
    assert result.artifact["certification_eligible"] is False
    assert result.artifact["live_certification_evidence"] is False
    assert result.artifact["mutation_attempted"] is False
    assert result.artifact["identity_enrollment_sha256"] == request.identity_enrollment.enrollment_sha256
    for proof in result.artifact["physical_identity_proofs"].values():
        assert proof["signal_count"] == 3
        assert proof["stable"] is True
        assert proof["pagination_complete"] is True


def test_swapped_runtime_clients_are_rejected_by_enrolled_fingerprint_and_origin() -> None:
    alpha_api = _ScriptedTypedApi(_scripts("alpha", "origin-alpha"))
    bravo_api = _ScriptedTypedApi(_scripts("bravo", "origin-bravo"))
    handles = (
        _handle("physical-hub-1", "alpha", "origin-alpha", api=bravo_api),
        _handle("physical-hub-2", "bravo", "origin-bravo", api=alpha_api),
    )

    result = _run(_request(handles=handles))

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert "wrong_evidence_origin" in result.reason_codes


def test_enrolled_fingerprint_mismatch_is_rejected_independently_of_origin() -> None:
    enrollment = build_phase9b_identity_enrollment(
        hub_fingerprints={
            "physical-hub-1": _fingerprint("bravo"),
            "physical-hub-2": _fingerprint("alpha"),
        },
        source_revision=_SOURCE_REVISION,
        config_sha256=_CONFIG_HASH,
        profile_sha256=_PROFILE_HASH,
    )

    result = _run(_request(identity_enrollment=enrollment))

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert "identity_fingerprint_mismatch" in result.reason_codes


def test_swapped_context_handle_is_rejected_before_contact() -> None:
    first, second = _request().runtime_handles
    request = _request(handles=(replace(first, context_handle=second.context_handle), second))

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "runtime_handle_binding_mismatch" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_duplicate_physical_hub_fingerprints_are_rejected() -> None:
    handles = (
        _handle("physical-hub-1", "alpha", "origin-alpha"),
        _handle("physical-hub-2", "alpha", "origin-bravo"),
    )

    result = _run(_request(handles=handles))

    assert result.decision is Phase9BDecision.BLOCKED
    assert "duplicate_identity_fingerprint" in result.reason_codes


@pytest.mark.parametrize(
    ("query_id", "mutation", "expected_reason"),
    [
        (
            "identity.kube_system_namespace",
            lambda items: {**items, "identity.kube_system_namespace": ()},
            "missing_identity_signal",
        ),
        (
            "identity.kube_system_namespace",
            lambda items: _identity_items("alpha", duplicate=True),
            "ambiguous_identity_signal",
        ),
        (
            "identity.openshift_infrastructure",
            lambda items: {
                **items,
                "identity.openshift_infrastructure": (
                    {
                        "apiVersion": "config.openshift.io/v1",
                        "kind": "Infrastructure",
                        "metadata": {"name": "cluster", "uid": ""},
                        "status": {"infrastructureName": "alpha-infrastructure"},
                    },
                ),
            },
            "unreadable_identity_signal",
        ),
        (
            "identity.openshift_cluster_version",
            lambda items: {
                **items,
                "identity.openshift_cluster_version": (
                    {
                        "apiVersion": "config.openshift.io/v1",
                        "kind": "ClusterVersion",
                        "metadata": {"name": "version", "uid": "alpha-clusterversion-uid"},
                        "status": {"desired": {}},
                    },
                ),
            },
            "unreadable_identity_signal",
        ),
    ],
)
def test_missing_duplicate_or_unreadable_identity_is_rejected(
    query_id: str,
    mutation: Any,
    expected_reason: str,
) -> None:
    del query_id
    items = mutation(_identity_items("alpha"))
    api = _ScriptedTypedApi(_scripts("alpha", "origin-alpha", first_items=items, second_items=items))
    handles = (
        _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
        _handle("physical-hub-2", "bravo", "origin-bravo"),
    )

    result = _run(_request(handles=handles))

    assert result.decision is Phase9BDecision.BLOCKED
    assert expected_reason in result.reason_codes


def test_conflicting_or_changing_identity_during_collection_is_rejected() -> None:
    first = _identity_items("alpha")
    second = _identity_items("alpha", infrastructure_name="changed-infrastructure")
    api = _ScriptedTypedApi(_scripts("alpha", "origin-alpha", first_items=first, second_items=second))
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "identity_changed_during_collection" in result.reason_codes


def test_conflicting_identity_object_contract_is_rejected() -> None:
    items = _identity_items("alpha")
    items["identity.openshift_infrastructure"] = (
        {
            "apiVersion": "config.openshift.io/v1",
            "kind": "ClusterVersion",
            "metadata": {"name": "cluster", "uid": "alpha-infrastructure-uid"},
            "status": {"infrastructureName": "alpha-infrastructure"},
        },
    )
    api = _ScriptedTypedApi(_scripts("alpha", "origin-alpha", first_items=items, second_items=items))
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "conflicting_identity_signal" in result.reason_codes


@pytest.mark.parametrize("missing_discriminator", ["apiVersion", "kind"])
def test_missing_identity_object_discriminator_is_rejected(missing_discriminator: str) -> None:
    items = _identity_items("alpha")
    infrastructure = dict(items["identity.openshift_infrastructure"][0])
    infrastructure.pop(missing_discriminator)
    items["identity.openshift_infrastructure"] = (infrastructure,)
    api = _ScriptedTypedApi(_scripts("alpha", "origin-alpha", first_items=items, second_items=items))
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "conflicting_identity_signal" in result.reason_codes


def test_cluster_version_corroboration_change_is_recorded_but_is_not_identity_authority() -> None:
    first = _identity_items("alpha")
    second = _identity_items("alpha")
    second["identity.openshift_cluster_version"] = (
        {
            "apiVersion": "config.openshift.io/v1",
            "kind": "ClusterVersion",
            "metadata": {"name": "version", "uid": "alpha-clusterversion-uid"},
            "status": {"desired": {"version": "4.19.1"}},
        },
    )
    api = _ScriptedTypedApi(_scripts("alpha", "origin-alpha", first_items=first, second_items=second))
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.PASS
    assert result.artifact is not None
    corroboration = result.artifact["physical_identity_proofs"]["physical-hub-1"]["cluster_version_corroboration"]
    assert corroboration["authoritative"] is False
    assert corroboration["stable"] is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("allow_live_contact", False, "live_contact_disabled"),
        ("allow_read_only_queries", False, "read_only_queries_disabled"),
        ("approval_reference", None, "missing_operator_authorization"),
        ("source_tree_clean", False, "dirty_source_revision"),
        ("source_revision", "d" * 40, "wrong_source_revision"),
        ("config_sha256", "", "invalid_config_hash"),
        ("profile_sha256", "", "invalid_profile_hash"),
        ("inherit_ambient_credentials", True, "ambient_credentials_forbidden"),
    ],
)
def test_controller_gates_block_before_any_api_contact(field: str, value: Any, reason: str) -> None:
    request = _request(**{field: value})

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert reason in result.reason_codes
    assert _all_api_calls(request) == 0


@pytest.mark.parametrize("invalid_reference", ["", False, 0, object()])
def test_invalid_operator_authorization_reference_blocks_before_contact(invalid_reference: Any) -> None:
    request = _request(approval_reference=invalid_reference)

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "missing_operator_authorization" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_missing_l0_l9_gate_blocks_before_contact() -> None:
    request = _request(required_gate_ids=tuple(required_read_only_discovery_gate_ids())[:-1])

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "missing_controller_gates" in result.reason_codes
    assert _all_api_calls(request) == 0


@pytest.mark.parametrize("missing_field", ["access_handle", "context_handle", "typed_api"])
def test_missing_runtime_handle_component_blocks_before_contact(missing_field: str) -> None:
    handles = list(_request().runtime_handles)
    handles[0] = replace(handles[0], **{missing_field: None})
    request = _request(handles=tuple(handles))

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_runtime_handle" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_explicit_empty_runtime_handle_set_is_not_replaced_by_test_defaults() -> None:
    request = _request(handles=())

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_runtime_handle" in result.reason_codes


def test_missing_identity_enrollment_blocks_before_contact() -> None:
    request = _request(identity_enrollment=None)

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_identity_enrollment" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_malformed_typed_page_reader_blocks_before_contact() -> None:
    first, second = _request().runtime_handles
    malformed_api = replace(first.typed_api, reader=object())  # type: ignore[arg-type]
    request = _request(handles=(replace(first, typed_api=malformed_api), second))

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_runtime_handle" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_missing_typed_request_timeout_contract_blocks_before_contact() -> None:
    first, second = _request().runtime_handles
    unbounded_api = replace(first.typed_api, timeout_contract="")
    request = _request(handles=(replace(first, typed_api=unbounded_api), second))

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_runtime_handle" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_tampered_identity_enrollment_blocks_before_contact() -> None:
    request = _request()
    assert request.identity_enrollment is not None
    tampered = replace(
        request.identity_enrollment,
        hub_fingerprints=(
            ("physical-hub-1", _fingerprint("bravo")),
            ("physical-hub-2", _fingerprint("alpha")),
        ),
    )
    request = replace(request, identity_enrollment=tampered)

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_identity_enrollment" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_duplicate_runtime_handle_objects_block_before_contact() -> None:
    shared_access = object()
    shared_context = object()
    request = _request(
        handles=(
            _handle(
                "physical-hub-1",
                "alpha",
                "origin-alpha",
                access_handle=shared_access,
                context_handle=shared_context,
            ),
            _handle(
                "physical-hub-2",
                "bravo",
                "origin-bravo",
                access_handle=shared_access,
                context_handle=shared_context,
            ),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "duplicate_runtime_handle" in result.reason_codes
    assert _all_api_calls(request) == 0


@pytest.mark.parametrize(
    "forbidden_claim",
    [
        {"certification_eligible": True},
        {"live_certification_evidence": True},
        {"logical_primary": "physical-hub-1"},
        {"known_state": "ready"},
        {"readiness": True},
        {"mutation_authorized": True},
        {"recovery_authorized": True},
        {"executable_profile": "profile"},
        {"authorization_token": "opaque"},
    ],
)
def test_forbidden_phase9c_or_certification_claims_block_before_contact(
    forbidden_claim: Mapping[str, Any],
) -> None:
    request = _request(requested_claims=forbidden_claim)

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "forbidden_claim" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_request_contract_accepts_no_command_shell_argv_or_release_adapter() -> None:
    request = _request()
    replace_request: Any = replace
    for field in ("command", "command_string", "argv", "shell", "release_adapter", "mutation_enabled"):
        with pytest.raises(TypeError):
            replace_request(request, **{field: "forbidden"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_query_ids", ("unknown.query",)),
        ("requested_verb", "patch"),
        ("requested_verb", "delete"),
        ("requested_verb", "watch"),
    ],
)
def test_unknown_or_mutating_query_blocks_before_contact(field: str, value: Any) -> None:
    request = replace(_request(), **{field: value})

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "query_not_allowlisted" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_typed_request_rejects_allowlisted_id_with_mismatched_resource_definition() -> None:
    with pytest.raises(ValueError, match="does not match"):
        TypedReadRequest(
            query_id="identity.kube_system_namespace",
            verb="list",
            api_group="config.openshift.io",
            api_version="v1",
            resource_plural="namespaces",
            field_selector="metadata.name=kube-system",
            continuation_token=None,
            resource_version=None,
            page_size=100,
            timeout_seconds=15.0,
        )


def test_typed_api_receives_only_fixed_allowlisted_list_queries() -> None:
    request = _request()

    result = _run(request)

    assert result.decision is Phase9BDecision.PASS
    typed_requests = [
        typed
        for handle in request.runtime_handles
        for typed in handle.typed_api.reader.requests  # type: ignore[attr-defined]
    ]
    assert {typed.query_id for typed in typed_requests} == set(IDENTITY_QUERY_IDS)
    assert {typed.verb for typed in typed_requests} == {"list"}
    assert all(typed.field_selector.startswith("metadata.name=") for typed in typed_requests)
    assert all(not hasattr(typed, field) for typed in typed_requests for field in ("command", "argv", "shell"))


def test_concrete_client_cannot_contact_api_when_bypassing_controller_entrypoint() -> None:
    reader = _ScriptedTypedApi(_scripts("alpha", "origin-alpha"))
    clock = _FakeClock()
    client = ControllerOwnedLiveDiscoveryClient(
        public_hub_id="physical-hub-1",
        api=TypedReadApi(
            access_handle=object(),
            context_handle=object(),
            reader=reader,
            timeout_contract="typed_request_timeout_v1",
        ),
        expected_origin="origin-alpha",
        source_revision=_SOURCE_REVISION,
        bounds=LiveDiscoveryBounds(),
        clock=clock,
        collection_start_utc=_NOW,
        controller_deadline=clock.monotonic() + 120.0,
    )
    request = ReadOnlyLiveClientRequest(
        query_id=IDENTITY_BUNDLE_QUERY_ID,
        scenario_id="preflight",
        query_family="cluster_identity",
        verb="get",
        hub_label="physical-hub-1",
        resource_family="cluster_identity",
        timeout_seconds=15.0,
    )

    with pytest.raises(ReadOnlyLiveSafetyError):
        client.execute_read_query(request)

    assert reader.call_count == 0


def test_api_failure_and_timeout_fail_closed_without_exception_text_leakage() -> None:
    unsafe_message = "http" + "s://api.internal " + "token=" + "unsafe"
    scripts: dict[str, Any] = _scripts("alpha", "origin-alpha")
    scripts["identity.kube_system_namespace"] = ((RuntimeError(unsafe_message),),)
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.INFRA_RETRYABLE
    assert result.artifact is None
    assert unsafe_message not in "\n".join(result.reasons)


def test_api_timeout_is_retryable_and_does_not_publish_exception_text() -> None:
    scripts: dict[str, Any] = _scripts("alpha", "origin-alpha")
    scripts["identity.kube_system_namespace"] = ((TimeoutError("private timeout detail"),),)
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.INFRA_RETRYABLE
    assert result.artifact is None
    assert result.reason_codes == ("api_timeout",)
    assert "private timeout detail" not in "\n".join(result.reasons)


def test_permanent_api_failure_blocks_without_retry_or_exception_leakage() -> None:
    scripts: dict[str, Any] = _scripts("alpha", "origin-alpha")
    scripts["identity.kube_system_namespace"] = ((ReadOnlyLivePermanentError("private permanent failure detail"),),)
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert result.reason_codes == ("api_permanent_failure",)
    assert "private permanent failure detail" not in "\n".join(result.reasons)


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"remaining_item_count": 1, "continuation_token": None}, "missing_continuation_state"),
        ({"continuation_token": "same", "requested_token": "same"}, "invalid_token_transition"),
        ({"truncated": True, "continuation_token": None}, "truncated_collection"),
    ],
)
def test_incomplete_or_invalid_pagination_is_rejected(
    override: Mapping[str, Any],
    reason: str,
) -> None:
    scripts = _scripts(
        "alpha",
        "origin-alpha",
        page_overrides={"identity.kube_system_namespace": override},
    )
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert reason in result.reason_codes


def test_repeated_continuation_token_and_page_loop_are_rejected() -> None:
    query_id = "identity.kube_system_namespace"
    pages = (
        _page(
            query_id,
            (),
            origin="origin-alpha",
            continuation_token="next",
            remaining_item_count=1,
            truncated=True,
        ),
        _page(
            query_id,
            (),
            origin="origin-alpha",
            requested_token="next",
            continuation_token="next",
            remaining_item_count=1,
            truncated=True,
        ),
    )
    scripts = _scripts("alpha", "origin-alpha")
    scripts[query_id] = (pages,)
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "repeated_continuation_token" in result.reason_codes


def test_resource_version_change_across_pages_is_rejected() -> None:
    query_id = "identity.kube_system_namespace"
    pages = (
        _page(
            query_id,
            (),
            origin="origin-alpha",
            continuation_token="next",
            remaining_item_count=1,
            truncated=True,
            resource_version="101",
        ),
        _page(
            query_id,
            _identity_items("alpha")[query_id],
            origin="origin-alpha",
            requested_token="next",
            resource_version="102",
        ),
    )
    scripts = _scripts("alpha", "origin-alpha")
    scripts[query_id] = (pages,)
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "inconsistent_resource_version" in result.reason_codes


@pytest.mark.parametrize(
    ("bounds", "expected_reason"),
    [
        (LiveDiscoveryBounds(max_pages_per_query=1), "page_limit_before_completeness"),
        (LiveDiscoveryBounds(max_items_per_query=1), "item_limit_before_completeness"),
        (LiveDiscoveryBounds(total_deadline_seconds=0.0001), "collection_deadline_exceeded"),
    ],
)
def test_configured_bounds_fail_before_incomplete_collection(
    bounds: LiveDiscoveryBounds,
    expected_reason: str,
) -> None:
    query_id = "identity.kube_system_namespace"
    terminal_items = _identity_items("alpha")[query_id]
    if bounds.max_items_per_query == 1:
        terminal_items = terminal_items * 2
    pages = (
        _page(
            query_id,
            (),
            origin="origin-alpha",
            continuation_token="next",
            remaining_item_count=1,
            truncated=True,
        ),
        _page(
            query_id,
            terminal_items,
            origin="origin-alpha",
            requested_token="next",
        ),
    )
    scripts = _scripts("alpha", "origin-alpha")
    scripts[query_id] = (pages,)
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        ),
        bounds=bounds,
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert expected_reason in result.reason_codes


def test_request_timeout_is_capped_to_remaining_total_deadline() -> None:
    bounds = LiveDiscoveryBounds(total_deadline_seconds=0.05)
    request = _request(bounds=bounds)

    result = _run(request)

    assert result.decision is Phase9BDecision.PASS
    typed_requests = [
        typed
        for handle in request.runtime_handles
        for typed in handle.typed_api.reader.requests  # type: ignore[attr-defined]
    ]
    assert typed_requests
    assert all(0 < typed.timeout_seconds <= bounds.total_deadline_seconds for typed in typed_requests)


@pytest.mark.parametrize(
    ("page_overrides", "reason"),
    [
        ({"collected_at": _NOW - timedelta(minutes=10)}, "stale_evidence"),
        ({"collected_at": _NOW + timedelta(minutes=10)}, "excessive_clock_skew"),
        ({"origin": "wrong-origin"}, "wrong_evidence_origin"),
        ({"revision": "d" * 40}, "wrong_evidence_source_revision"),
    ],
)
def test_stale_skewed_wrong_origin_or_wrong_source_evidence_is_rejected(
    page_overrides: Mapping[str, Any],
    reason: str,
) -> None:
    query_id = "identity.kube_system_namespace"
    kwargs = dict(page_overrides)
    origin = str(kwargs.pop("origin", "origin-alpha"))
    scripts = _scripts(
        "alpha",
        "origin-alpha",
        page_overrides={query_id: {"origin": origin, **kwargs}},
    )
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert reason in result.reason_codes


def test_mixed_origin_across_pages_is_rejected() -> None:
    query_id = "identity.kube_system_namespace"
    pages = (
        _page(
            query_id,
            (),
            origin="origin-alpha",
            continuation_token="next",
            remaining_item_count=1,
            truncated=True,
        ),
        _page(
            query_id,
            _identity_items("alpha")[query_id],
            origin="origin-other",
            requested_token="next",
        ),
    )
    scripts = _scripts("alpha", "origin-alpha")
    scripts[query_id] = (pages,)
    api = _ScriptedTypedApi(scripts)
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha", api=api),
            _handle("physical-hub-2", "bravo", "origin-bravo"),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "wrong_evidence_origin" in result.reason_codes


def test_cross_hub_evidence_skew_is_rejected() -> None:
    skewed_scripts = _scripts(
        "bravo",
        "origin-bravo",
        page_overrides={query_id: {"collected_at": _NOW - timedelta(seconds=90)} for query_id in IDENTITY_QUERY_IDS},
    )
    request = _request(
        handles=(
            _handle("physical-hub-1", "alpha", "origin-alpha"),
            _handle(
                "physical-hub-2",
                "bravo",
                "origin-bravo",
                api=_ScriptedTypedApi(skewed_scripts),
            ),
        )
    )

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "excessive_clock_skew" in result.reason_codes


@pytest.mark.parametrize(
    "unsafe_value",
    [
        {"nested": {"token": "unsafe"}},
        {"nested": "bearer " + "unsafe"},
        {"endpoint": "http" + "s://api.internal"},
        {"kubeconfig": "/home/" + "operator/.kube/config"},
        {"context": "runtime-context-value"},
        {"runtime_handle": "private-handle"},
        {"error": "exception text"},
        {"bytes_value": b"unsafe"},
        {"object_value": _UnsafeRepresentation()},
    ],
)
def test_recursive_redaction_failure_blocks_artifact_publication(unsafe_value: Mapping[str, Any]) -> None:
    result = _run(_request(additional_artifact_fields={"diagnostics": unsafe_value}))

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert "redaction_failure" in result.reason_codes


def test_recursive_redaction_rejects_cyclic_artifact_input_without_contact() -> None:
    cyclic: dict[str, Any] = {}
    cyclic["nested"] = cyclic
    request = _request(additional_artifact_fields={"diagnostics": cyclic})

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert "redaction_failure" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_recursive_redaction_rejects_excessive_depth_without_contact() -> None:
    deeply_nested: dict[str, Any] = {}
    cursor = deeply_nested
    for index in range(40):
        child: dict[str, Any] = {}
        cursor[f"level_{index}"] = child
        cursor = child
    request = _request(additional_artifact_fields={"diagnostics": deeply_nested})

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert "redaction_failure" in result.reason_codes
    assert _all_api_calls(request) == 0


@pytest.mark.parametrize("invalid_value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_collection_bounds_block_before_contact(invalid_value: float) -> None:
    request = _request(bounds=LiveDiscoveryBounds(total_deadline_seconds=invalid_value))

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_collection_bounds" in result.reason_codes
    assert _all_api_calls(request) == 0


@pytest.mark.parametrize("field", ["request_timeout_seconds", "total_deadline_seconds"])
def test_oversized_integer_collection_bounds_block_without_overflow(field: str) -> None:
    request = _request(bounds=replace(LiveDiscoveryBounds(), **{field: 10**10000}))

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_collection_bounds" in result.reason_codes
    assert _all_api_calls(request) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_timeout_seconds", 61.0),
        ("page_size", 501),
        ("max_pages_per_query", 101),
        ("max_items_per_query", 10001),
        ("total_deadline_seconds", 301.0),
        ("max_evidence_age_seconds", 901.0),
        ("max_clock_skew_seconds", 121.0),
    ],
)
def test_controller_hard_bound_ceiling_blocks_before_contact(field: str, value: Any) -> None:
    request = _request(bounds=replace(LiveDiscoveryBounds(), **{field: value}))

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_collection_bounds" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_phase9b_backend_result_forces_phase_and_non_authority_flags() -> None:
    result = Phase9BReadOnlyBackendResult(
        decision=ReadOnlyBackendDecision.PASS,
        purpose="certification",
        certification_eligible=True,
        live_certification_evidence=True,
        mutation_attempted=True,
        backend_phase=ReadOnlyBackendPhase.INTERFACE_SKELETON,
    )

    assert result.purpose == "live_read_only"
    assert result.certification_eligible is False
    assert result.live_certification_evidence is False
    assert result.mutation_attempted is False
    assert result.backend_phase is ReadOnlyBackendPhase.LIVE_READ_ONLY_PHASE_9B


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("required_gate_ids", "missing_controller_gates"),
        ("runtime_handles", "invalid_runtime_handle"),
        ("requested_query_ids", "query_not_allowlisted"),
        ("requested_claims", "forbidden_claim"),
        ("additional_artifact_fields", "redaction_failure"),
    ],
)
def test_malformed_request_containers_block_without_contact(field: str, reason: str) -> None:
    request = _request()
    replace_request: Any = replace
    malformed = replace_request(request, **{field: None})

    result = _run(malformed)

    assert result.decision is Phase9BDecision.BLOCKED
    assert reason in result.reason_codes
    assert _all_api_calls(request) == 0


def test_malformed_identity_enrollment_pairs_block_without_contact() -> None:
    request = _request()
    assert request.identity_enrollment is not None
    replace_enrollment: Any = replace
    malformed_enrollment = replace_enrollment(request.identity_enrollment, hub_fingerprints=(("physical-hub-1",),))
    malformed = replace(request, identity_enrollment=malformed_enrollment)

    result = _run(malformed)

    assert result.decision is Phase9BDecision.BLOCKED
    assert "invalid_identity_enrollment" in result.reason_codes
    assert _all_api_calls(request) == 0


@pytest.mark.parametrize(
    "claim",
    [
        {"certification_eligible": True},
        {"diagnostics": {"isCertified": True}},
        {"diagnostics": {"knownState": "ready"}},
        {"diagnostics": {"status": "primary"}},
        {"logical_roles": {"physical-hub-1": "primary"}},
        {"mutation_authorized": True},
        {"recovery_authorized": True},
    ],
)
def test_additional_artifact_fields_cannot_override_phase9b_authority(claim: Mapping[str, Any]) -> None:
    request = _request(additional_artifact_fields=claim)

    result = _run(request)

    assert result.decision is Phase9BDecision.BLOCKED
    assert result.artifact is None
    assert "forbidden_claim" in result.reason_codes
    assert _all_api_calls(request) == 0


def test_allowlisted_additional_artifact_fields_use_audited_snapshot() -> None:
    diagnostics = {"diagnostics": {"status": "bounded-read"}}
    request = _request(additional_artifact_fields=diagnostics)

    result = _run(request)
    diagnostics["diagnostics"]["status"] = "changed-after-run"

    assert result.decision is Phase9BDecision.PASS
    assert result.artifact is not None
    assert result.artifact["diagnostics"] == {"status": "bounded-read"}


def test_module_source_has_no_ambient_or_command_execution_paths() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_roots = {"http", "kubernetes", "os", "requests", "socket", "subprocess", "urllib", "yaml"}
    imported_roots: set[str] = set()
    forbidden_calls: list[str] = []
    ambient_accesses: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", maxsplit=1)[0])
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr == "environ":
                ambient_accesses.append("os.environ")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"create", "delete", "patch"}:
                forbidden_calls.append(node.func.attr)
            for keyword in node.keywords:
                if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    forbidden_calls.append("shell")

    assert imported_roots.isdisjoint(forbidden_roots)
    assert ambient_accesses == []
    assert forbidden_calls == []


def test_call_trace_is_complete_allowlisted_and_non_mutating() -> None:
    result = _run(_request())

    assert result.decision is Phase9BDecision.PASS
    assert result.artifact is not None
    trace = result.artifact["call_trace"]
    assert len(trace) == 12
    assert {entry["query_id"] for entry in trace} == set(IDENTITY_QUERY_IDS)
    assert {entry["verb"] for entry in trace} == {"list"}
    assert all(entry["pagination_complete"] is True for entry in trace)
    assert all(entry["mutation_attempted"] is False for entry in trace)
