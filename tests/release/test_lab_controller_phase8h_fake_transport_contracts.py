from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.release.lab_controller.live_config import (
    LiveGateId,
    build_sanitized_example_live_lab_config,
    redact_live_config_summary,
)
from tests.release.lab_controller.read_only_backend import (
    ReadOnlyBackendDecision,
    ReadOnlyDiscoveryRequest,
    ReadOnlyDiscoveryResult,
    ReadOnlyGuardrailEvidence,
    ReadOnlyQueryPlanBundle,
    RuntimeOnlyHubRef,
    UnimplementedReadOnlyDiscoveryBackend,
    validate_read_only_discovery_result,
)
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyDiscoveryGuardDecision,
    ReadOnlyDiscoveryGuardResult,
    ReadOnlyQueryFamily,
    build_example_read_only_query_plan,
    required_read_only_discovery_gate_ids,
    validate_read_only_discovery_gates,
    validate_read_only_query_plan,
)
from tests.release.lab_controller.read_only_transport import (
    FakeReadOnlyTransport,
    FakeTransportFixture,
    ReadOnlyTransportArtifactSummary,
    ReadOnlyTransportDecision,
    ReadOnlyTransportErrorCategory,
    ReadOnlyTransportKind,
    ReadOnlyTransportQuery,
    ReadOnlyTransportQueryValidation,
    ReadOnlyTransportResponse,
    ReadOnlyTransportStatus,
    build_example_fake_transport_fixture,
    build_example_transport_query,
    build_transport_queries_from_backend_request,
    collect_fake_transport_evidence,
    summarize_fake_transport_run,
    summarize_transport_response,
    validate_transport_query,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

_MODULE_PATH = Path(__file__).resolve().parent / "lab_controller" / "read_only_transport.py"


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        strings: list[str] = []
        for key, child in value.items():
            strings.extend(_flatten_strings(str(key)))
            strings.extend(_flatten_strings(child))
        return tuple(strings)
    if isinstance(value, (list, tuple, set)):
        strings = []
        for item in value:
            strings.extend(_flatten_strings(item))
        return tuple(strings)
    if isinstance(value, str):
        return (value,)
    return ()


def _summary_text(summary: Mapping[str, Any]) -> str:
    return "\n".join(_flatten_strings(summary)).lower()


# --- 1-2: module import / non-live source guard --------------------------------------------------


def test_module_imports_without_live_dependencies() -> None:
    assert ReadOnlyTransportDecision.PASS.value == "PASS"
    assert ReadOnlyTransportDecision.INFRA_RETRYABLE.value == "INFRA_RETRYABLE"
    assert ReadOnlyTransportKind.FAKE.value == "fake"
    assert ReadOnlyTransportKind.LIVE_UNSUPPORTED.value == "live_unsupported"
    assert ReadOnlyTransportStatus.SUCCESS.value == "success"
    assert ReadOnlyTransportStatus.UNSAFE_PAYLOAD.value == "unsafe_payload"
    assert ReadOnlyTransportErrorCategory.NONE.value == "none"


def test_module_source_has_no_live_execution_or_transport_primitives() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    for forbidden_module in (
        "os",
        "subprocess",
        "socket",
        "yaml",
        "json",
        "kubernetes",
        "requests",
        "urllib",
        "http",
    ):
        assert forbidden_module not in imported_roots, f"read_only_transport.py must not import {forbidden_module!r}"

    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    for forbidden_call in ("open", "system", "run", "Popen", "getenv", "check_output", "check_call", "call"):
        assert forbidden_call not in called_names, f"read_only_transport.py must not call {forbidden_call!r}"


# --- shared helpers ------------------------------------------------------------------------------


def _passing_query(scenario_id: str = "preflight") -> ReadOnlyTransportQuery:
    return build_example_transport_query(scenario_id=scenario_id)


def _blocked_guardrail_result() -> ReadOnlyDiscoveryGuardResult:
    plan = build_example_transport_query().to_query_plan()
    return validate_read_only_query_plan(replace(plan, verb="delete"))


# --- 3-18: transport query validation ------------------------------------------------------------


def test_valid_read_only_transport_query_validates() -> None:
    validation = validate_transport_query(_passing_query())

    assert isinstance(validation, ReadOnlyTransportQueryValidation)
    assert validation.decision is ReadOnlyTransportDecision.PASS
    assert validation.is_valid is True
    assert validation.blocking_fields == ()
    assert validation.guardrail_result is not None
    assert validation.guardrail_result.decision is ReadOnlyDiscoveryGuardDecision.PASS


def test_query_validation_blocks_missing_query_id() -> None:
    validation = validate_transport_query(replace(_passing_query(), query_id=""))

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "query_id" in validation.blocking_fields


def test_query_validation_blocks_unknown_scenario() -> None:
    query = replace(_passing_query(), scenario_id="not-a-catalog-scenario")

    validation = validate_transport_query(query)

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "scenario_id" in validation.blocking_fields


def test_query_validation_blocks_query_family_not_allowed_after_gates() -> None:
    query = replace(
        _passing_query(),
        query_family=ReadOnlyQueryFamily.ARGOCD_STATUS,
        resource_family=ReadOnlyQueryFamily.ARGOCD_STATUS.value,
    )

    validation = validate_transport_query(query)

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "query_family" in validation.blocking_fields


def test_query_validation_blocks_mutating_verb() -> None:
    validation = validate_transport_query(replace(_passing_query(), verb="delete"))

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "verb" in validation.blocking_fields


def test_query_validation_blocks_unknown_verb() -> None:
    validation = validate_transport_query(replace(_passing_query(), verb="frobnicate"))

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "verb" in validation.blocking_fields


def test_query_validation_blocks_non_pass_guardrail_result() -> None:
    query = replace(_passing_query(), guardrail_result=_blocked_guardrail_result())

    validation = validate_transport_query(query)

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "guardrail_result" in validation.blocking_fields


def test_query_validation_blocks_missing_l0_l9_gates() -> None:
    query = replace(_passing_query(), required_gate_ids=(LiveGateId.L0, LiveGateId.L1))

    validation = validate_transport_query(query)

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "required_gate_ids" in validation.blocking_fields


def test_query_validation_blocks_mutation_enabled_true() -> None:
    validation = validate_transport_query(replace(_passing_query(), mutation_enabled=True))

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "mutation_enabled" in validation.blocking_fields


def test_query_validation_blocks_live_certification_evidence_true() -> None:
    validation = validate_transport_query(replace(_passing_query(), live_certification_evidence=True))

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "live_certification_evidence" in validation.blocking_fields


def test_query_validation_blocks_runtime_only_artifact_fields() -> None:
    query = replace(_passing_query(), artifact_fields=("kubeconfig_path", "decision"))

    validation = validate_transport_query(query)

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert any("kubeconfig" in field for field in validation.blocking_fields)


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("hub_label", "/home/operator/.kube/config"),
        ("resource_family", "https://api.hub.example.com:6443"),
        ("hub_label", "token=abcdef123456"),
        ("resource_family", "cluster-id-7f3a9b2c"),
        ("hub_label", "artifacts/.release/run-output"),
    ],
)
def test_query_validation_blocks_unsafe_values(field_name: str, unsafe_value: str) -> None:
    base = _passing_query()
    if field_name == "hub_label":
        query = replace(base, hub_label=unsafe_value)
    else:
        query = replace(base, resource_family=unsafe_value)

    validation = validate_transport_query(query)

    assert validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert field_name in validation.blocking_fields
    assert unsafe_value not in _summary_text(validation.artifact_safe_summary)


# --- 19-20: fake transport fixture construction --------------------------------------------------


def test_fixture_construction_blocks_duplicate_query_ids() -> None:
    fixtures = (
        build_example_fake_transport_fixture(query_id="phase8h-query"),
        FakeTransportFixture(
            query_id="phase8h-query",
            status=ReadOnlyTransportStatus.FAILED,
            error_category=ReadOnlyTransportErrorCategory.TRANSPORT_FAILURE,
        ),
    )

    with pytest.raises(ValueError):
        FakeReadOnlyTransport(fixtures)


@pytest.mark.parametrize(
    "unsafe_payload",
    [
        {"api_url": "https://api.hub.example.com:6443"},
        {"observed_summary": "/home/operator/.kube/config"},
        {"auth": "token=abcdef123456"},
        {"identity": "cluster-id-7f3a9b2c"},
        {"command": "kubectl get nodes"},
        {"artifact": "artifacts/.release/run-output"},
    ],
)
def test_fixture_construction_blocks_unsafe_payload(unsafe_payload: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        FakeTransportFixture(
            query_id="phase8h-query",
            status=ReadOnlyTransportStatus.SUCCESS,
            payload=unsafe_payload,
        )


@pytest.mark.parametrize(
    "forbidden_key",
    ["redacted_api_url", "transport_handle_ref_present", "managed_token_count", "argv_summary"],
)
def test_fixture_construction_blocks_forbidden_substring_keys(forbidden_key: str) -> None:
    with pytest.raises(ValueError):
        FakeTransportFixture(
            query_id="phase8h-query",
            status=ReadOnlyTransportStatus.SUCCESS,
            payload={forbidden_key: "artifact-safe-value"},
        )


# --- 21-30: FakeReadOnlyTransport behavior -------------------------------------------------------


def _transport_with(fixture: FakeTransportFixture) -> FakeReadOnlyTransport:
    return FakeReadOnlyTransport((fixture,))


def test_fake_transport_valid_success_returns_pass() -> None:
    query = _passing_query()
    transport = _transport_with(build_example_fake_transport_fixture(query_id=query.query_id))

    response = transport.execute(query)

    assert response.decision is ReadOnlyTransportDecision.PASS
    assert response.status is ReadOnlyTransportStatus.SUCCESS
    assert response.error_category is ReadOnlyTransportErrorCategory.NONE
    assert response.transport_kind is ReadOnlyTransportKind.FAKE
    assert dict(response.artifact_safe_payload)["evidence_present"] is True


def test_fake_transport_records_calls_deterministically() -> None:
    query = _passing_query()
    transport = _transport_with(build_example_fake_transport_fixture(query_id=query.query_id))

    transport.execute(query)
    transport.execute(query)

    assert transport.call_count == 2
    assert transport.received_query_ids == (query.query_id, query.query_id)
    summaries = transport.received_query_summaries()
    assert summaries[0] == summaries[1]
    assert summaries[0]["transport_kind"] == ReadOnlyTransportKind.FAKE.value


def test_fake_transport_never_sets_live_contact_attempted_true() -> None:
    query = _passing_query()
    transport = _transport_with(build_example_fake_transport_fixture(query_id=query.query_id))

    response = transport.execute(query)

    assert response.live_contact_attempted is False
    assert response.live_contact_succeeded is False
    assert response.no_live_contact is True
    assert transport.no_live_contact is True


def test_fake_transport_never_sets_live_certification_evidence_true() -> None:
    query = _passing_query()
    transport = _transport_with(build_example_fake_transport_fixture(query_id=query.query_id))

    response = transport.execute(query)

    assert response.live_certification_evidence is False


def test_fake_transport_never_sets_mutation_attempted_true() -> None:
    query = _passing_query()
    transport = _transport_with(build_example_fake_transport_fixture(query_id=query.query_id))

    response = transport.execute(query)

    assert response.mutation_attempted is False


def test_fake_transport_timeout_retryable_returns_infra_retryable() -> None:
    query = _passing_query()
    fixture = FakeTransportFixture(
        query_id=query.query_id,
        status=ReadOnlyTransportStatus.TIMEOUT,
        error_category=ReadOnlyTransportErrorCategory.TIMEOUT,
        timeout=True,
        retryable=True,
    )

    response = _transport_with(fixture).execute(query)

    assert response.decision is ReadOnlyTransportDecision.INFRA_RETRYABLE
    assert response.status is ReadOnlyTransportStatus.TIMEOUT
    assert response.timeout is True
    assert response.retryable is True
    assert response.live_contact_attempted is False


def test_fake_transport_timeout_non_retryable_returns_no_go() -> None:
    query = _passing_query()
    fixture = FakeTransportFixture(
        query_id=query.query_id,
        status=ReadOnlyTransportStatus.TIMEOUT,
        error_category=ReadOnlyTransportErrorCategory.TIMEOUT,
        timeout=True,
        retryable=False,
    )

    response = _transport_with(fixture).execute(query)

    assert response.decision is ReadOnlyTransportDecision.NO_GO
    assert response.status is ReadOnlyTransportStatus.TIMEOUT
    assert response.timeout is True
    assert response.retryable is False


@pytest.mark.parametrize(
    ("error_category", "expected_decision", "expected_status"),
    [
        (
            ReadOnlyTransportErrorCategory.TRANSPORT_FAILURE,
            ReadOnlyTransportDecision.NO_GO,
            ReadOnlyTransportStatus.FAILED,
        ),
        (
            ReadOnlyTransportErrorCategory.POLICY_BLOCKED,
            ReadOnlyTransportDecision.BLOCKED,
            ReadOnlyTransportStatus.BLOCKED,
        ),
    ],
)
def test_fake_transport_failure_returns_structured_failure(
    error_category: ReadOnlyTransportErrorCategory,
    expected_decision: ReadOnlyTransportDecision,
    expected_status: ReadOnlyTransportStatus,
) -> None:
    query = _passing_query()
    fixture = FakeTransportFixture(
        query_id=query.query_id,
        status=ReadOnlyTransportStatus.FAILED,
        error_category=error_category,
    )

    response = _transport_with(fixture).execute(query)

    assert response.decision is expected_decision
    assert response.status is expected_status
    assert response.error_category is error_category
    assert response.first_blocking_reason is not None
    assert response.live_certification_evidence is False


def test_fake_transport_missing_fixture_blocks() -> None:
    query = _passing_query()
    transport = FakeReadOnlyTransport(())

    response = transport.execute(query)

    assert response.decision is ReadOnlyTransportDecision.BLOCKED
    assert response.status is ReadOnlyTransportStatus.BLOCKED
    assert response.error_category is ReadOnlyTransportErrorCategory.MISSING_FIXTURE


def test_fake_transport_invalid_query_blocks_before_fixture_lookup() -> None:
    query = _passing_query()
    invalid_query = replace(query, verb="delete")
    # A matching success fixture exists, proving validation happens before lookup.
    transport = _transport_with(build_example_fake_transport_fixture(query_id=query.query_id))

    response = transport.execute(invalid_query)

    assert response.decision is ReadOnlyTransportDecision.BLOCKED
    assert response.error_category is ReadOnlyTransportErrorCategory.INVALID_QUERY
    assert response.status is ReadOnlyTransportStatus.BLOCKED


def test_fake_transport_unsafe_payload_fixture_returns_no_go() -> None:
    query = _passing_query()
    fixture = FakeTransportFixture(
        query_id=query.query_id,
        status=ReadOnlyTransportStatus.UNSAFE_PAYLOAD,
        error_category=ReadOnlyTransportErrorCategory.UNSAFE_PAYLOAD,
    )

    response = _transport_with(fixture).execute(query)

    assert response.decision is ReadOnlyTransportDecision.NO_GO
    assert response.status is ReadOnlyTransportStatus.UNSAFE_PAYLOAD
    assert dict(response.artifact_safe_payload) == {}


# --- 31-32: artifact-safe summaries --------------------------------------------------------------

_UNSAFE_SUMMARY_SUBSTRINGS = (
    "https://",
    "http://",
    "/home/",
    "~/.kube",
    "token=",
    "password=",
    "secret=",
    "." + "release",
    "cluster-id",
    "kubeconfig_ref",
    "context_ref",
    "credential_ref",
    "transport_handle_ref",
    "api_url",
    "argv",
    "kubectl",
)


def test_fake_response_summary_excludes_unsafe_values() -> None:
    response = ReadOnlyTransportResponse(
        query_id="phase8h-query",
        scenario_id="preflight",
        status=ReadOnlyTransportStatus.BLOCKED,
        decision=ReadOnlyTransportDecision.BLOCKED,
        response_summary="contacting https://api.hub.example.com:6443 failed",
        error_category=ReadOnlyTransportErrorCategory.TRANSPORT_FAILURE,
        reasons=("token=abcdef123456", "/home/operator/.kube/config"),
        first_blocking_reason="cluster-id-7f3a9b2c rejected",
    )

    summary = summarize_transport_response(response)
    summary_text = _summary_text(summary)

    assert summary["transport_kind"] == ReadOnlyTransportKind.FAKE.value
    assert summary["live_contact_attempted"] is False
    assert summary["live_certification_evidence"] is False
    for forbidden in _UNSAFE_SUMMARY_SUBSTRINGS:
        assert forbidden not in summary_text


def test_transport_artifact_summary_contains_no_runtime_only_refs() -> None:
    query = _passing_query()
    other_query = replace(
        _passing_query(),
        query_id="phase8h-query-timeout",
        query_family=ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS,
        resource_family=ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS.value,
    )
    other_query = replace(other_query, guardrail_result=validate_read_only_query_plan(other_query.to_query_plan()))
    transport = FakeReadOnlyTransport(
        (
            build_example_fake_transport_fixture(query_id=query.query_id),
            FakeTransportFixture(
                query_id=other_query.query_id,
                status=ReadOnlyTransportStatus.TIMEOUT,
                error_category=ReadOnlyTransportErrorCategory.TIMEOUT,
                timeout=True,
                retryable=True,
            ),
        )
    )
    queries = (query, other_query)
    responses = collect_fake_transport_evidence(transport, queries)

    summary = summarize_fake_transport_run(queries, responses)
    payload = summary.to_payload()
    summary_text = _summary_text(payload)

    assert isinstance(summary, ReadOnlyTransportArtifactSummary)
    assert summary.transport_kind is ReadOnlyTransportKind.FAKE
    assert payload["transport_kind"] == ReadOnlyTransportKind.FAKE.value
    assert payload["live_contact_attempted"] is False
    assert payload["live_certification_evidence"] is False
    assert payload["no_live_contact"] is True
    assert payload["mutation_attempted"] is False
    assert payload["redaction_status"] == "redacted"
    assert payload["call_count"] == 2
    assert sorted(payload["scenario_ids"]) == ["preflight"]
    assert "managed_cluster_status" in payload["query_families"]
    assert "read_only" in payload["verb_classes"]
    assert ReadOnlyTransportDecision.INFRA_RETRYABLE.value in payload["decisions"]
    for forbidden in _UNSAFE_SUMMARY_SUBSTRINGS:
        assert forbidden not in summary_text


# --- 33-35: Phase 8G backend integration ---------------------------------------------------------


def _runtime_refs() -> tuple[RuntimeOnlyHubRef, ...]:
    return (
        RuntimeOnlyHubRef(
            physical_label="hub-a",
            kubeconfig_ref="runtime-access-handle-a",
            context_ref="runtime-context-handle-a",
            credential_ref="runtime-auth-handle-a",
            transport_handle_ref="runtime-transport-handle-a",
        ),
        RuntimeOnlyHubRef(
            physical_label="hub-b",
            kubeconfig_ref="runtime-access-handle-b",
            context_ref="runtime-context-handle-b",
            credential_ref="runtime-auth-handle-b",
            transport_handle_ref="runtime-transport-handle-b",
        ),
    )


def _backend_bundle(scenario_id: str = "preflight") -> ReadOnlyQueryPlanBundle:
    plan = replace(
        build_example_read_only_query_plan(),
        scenario_id=scenario_id,
        query_family=ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS,
    )
    return ReadOnlyQueryPlanBundle(
        scenario_id=scenario_id,
        query_plans=(plan,),
        required_gate_ids=tuple(required_read_only_discovery_gate_ids()),
        guardrail_decisions=(validate_read_only_query_plan(plan),),
        all_queries_guardrail_passed=True,
        live_certification_evidence=False,
    )


def _backend_request(scenario_id: str = "preflight") -> ReadOnlyDiscoveryRequest:
    config = build_sanitized_example_live_lab_config()
    bundle = _backend_bundle(scenario_id)
    gate_status = {gate.value: "satisfied" for gate in required_read_only_discovery_gate_ids()}
    guardrail_evidence = ReadOnlyGuardrailEvidence(
        gate_result=validate_read_only_discovery_gates(required_read_only_discovery_gate_ids()),
        query_results=bundle.guardrail_decisions,
        guardrails_passed=True,
        validated_before_contact=True,
        no_live_contact=True,
        live_certification_evidence=False,
    )
    return ReadOnlyDiscoveryRequest(
        request_id="phase8g-request",
        plan_id="phase8g-plan",
        scenario_id=scenario_id,
        validated_config_summary=redact_live_config_summary(config),
        runtime_only_hub_refs=_runtime_refs(),
        expected_physical_labels=tuple(hub.physical_label for hub in config.physical_hubs),
        expected_managed_cluster_names=config.managed_clusters.expected_names,
        required_gate_status=gate_status,
        query_plan_bundle=bundle,
        guardrail_evidence=guardrail_evidence,
        redaction_policy_summary={"required": True, "status": "redacted"},
        artifact_policy_summary={"redaction_required": True, "publishable": True},
        retry_policy_summary={"automatic_recovery_enabled": False, "retry_requires_operator": True},
        live_execution_enabled=True,
        mutation_enabled=False,
        live_certification_evidence=False,
    )


def _success_transport_for(queries: tuple[ReadOnlyTransportQuery, ...]) -> FakeReadOnlyTransport:
    return FakeReadOnlyTransport(
        tuple(build_example_fake_transport_fixture(query_id=query.query_id) for query in queries)
    )


def test_integration_helper_consumes_backend_request_without_live_contact() -> None:
    request = _backend_request()
    queries = build_transport_queries_from_backend_request(request)

    assert len(queries) == len(request.query_plan_bundle.query_plans)

    transport = _success_transport_for(queries)
    responses = collect_fake_transport_evidence(transport, queries)

    assert all(response.decision is ReadOnlyTransportDecision.PASS for response in responses)
    assert all(response.no_live_contact is True for response in responses)
    assert all(response.live_contact_attempted is False for response in responses)

    payload = summarize_fake_transport_run(queries, responses, request=request).to_payload()
    assert payload["request_id"] == request.request_id
    assert payload["no_live_contact"] is True
    assert payload["live_certification_evidence"] is False


def test_integration_helper_does_not_make_unimplemented_backend_pass() -> None:
    request = _backend_request()
    queries = build_transport_queries_from_backend_request(request)
    transport = _success_transport_for(queries)
    responses = collect_fake_transport_evidence(transport, queries)

    backend_result = UnimplementedReadOnlyDiscoveryBackend().run_discovery(request)

    assert all(response.decision is ReadOnlyTransportDecision.PASS for response in responses)
    assert backend_result.decision is ReadOnlyBackendDecision.BLOCKED
    assert backend_result.no_live_contact is True
    assert backend_result.live_certification_evidence is False


def test_result_validation_still_rejects_pass_without_evidence_proof() -> None:
    result = ReadOnlyDiscoveryResult(
        decision=ReadOnlyBackendDecision.PASS,
        request_id="phase8g-request",
        scenario_id="preflight",
        request_valid=True,
    )

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "physical_identity_evidence" in validation.blocking_fields


# --- 36-37: catalog alignment and non-live boundary ----------------------------------------------


@pytest.mark.parametrize("scenario_id", ["preflight", "lab-readiness", "baseline-check", "final-baseline-check"])
def test_all_fake_transport_scenario_ids_come_from_catalog(scenario_id: str) -> None:
    query = build_example_transport_query(scenario_id=scenario_id)

    assert scenario_id in SCENARIOS_BY_ID
    assert validate_transport_query(query).decision is ReadOnlyTransportDecision.PASS

    request = _backend_request(scenario_id)
    for transport_query in build_transport_queries_from_backend_request(request):
        assert transport_query.scenario_id in SCENARIOS_BY_ID


def test_no_live_execution_behavior_is_introduced() -> None:
    query = build_example_transport_query()
    assert query.mutation_enabled is False
    assert query.live_certification_evidence is False
    assert query.transport_kind is ReadOnlyTransportKind.FAKE

    # A non-fake transport kind is unsupported and fails closed (no live transport exists).
    live_validation = validate_transport_query(replace(query, transport_kind=ReadOnlyTransportKind.LIVE_UNSUPPORTED))
    assert live_validation.decision is ReadOnlyTransportDecision.BLOCKED
    assert "transport_kind" in live_validation.blocking_fields

    transport = _transport_with(build_example_fake_transport_fixture(query_id=query.query_id))
    response = transport.execute(query)
    assert response.live_contact_attempted is False
    assert response.live_contact_succeeded is False
    assert response.mutation_attempted is False
    assert response.live_certification_evidence is False
    assert response.no_live_contact is True
    assert transport.no_live_contact is True
