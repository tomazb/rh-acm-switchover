from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.release.lab_controller.live_config import build_sanitized_example_live_lab_config, redact_live_config_summary
from tests.release.lab_controller.read_only_backend import (
    LogicalRoleEvidence,
    ManagedClusterSetEvidence,
    PhysicalIdentityEvidence,
    ReadOnlyBackendDecision,
    ReadOnlyBackendPhase,
    ReadOnlyDiscoveryBackendProtocol,
    ReadOnlyDiscoveryRequest,
    ReadOnlyGuardrailEvidence,
    ReadOnlyQueryPlanBundle,
    ReadOnlyReadPrerequisiteEvidence,
    RuntimeOnlyHubRef,
    TransportSummary,
    UnimplementedReadOnlyDiscoveryBackend,
    summarize_read_only_backend_request,
    summarize_read_only_backend_result,
    validate_read_only_discovery_request,
    validate_read_only_discovery_result,
)
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyQueryFamily,
    ReadOnlyQueryPlan,
    build_example_read_only_query_plan,
    required_read_only_discovery_gate_ids,
    validate_read_only_discovery_gates,
    validate_read_only_query_plan,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

_MODULE_PATH = Path(__file__).resolve().parent / "lab_controller" / "read_only_backend.py"


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


def _passing_plan(scenario_id: str = "preflight") -> ReadOnlyQueryPlan:
    return replace(
        build_example_read_only_query_plan(),
        scenario_id=scenario_id,
        query_family=ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS,
    )


def _passing_bundle(scenario_id: str = "preflight") -> ReadOnlyQueryPlanBundle:
    plan = _passing_plan(scenario_id)
    return ReadOnlyQueryPlanBundle(
        scenario_id=scenario_id,
        query_plans=(plan,),
        required_gate_ids=tuple(required_read_only_discovery_gate_ids()),
        guardrail_decisions=(validate_read_only_query_plan(plan),),
        all_queries_guardrail_passed=True,
        live_certification_evidence=False,
    )


def _passing_guardrail_evidence(bundle: ReadOnlyQueryPlanBundle | None = None) -> ReadOnlyGuardrailEvidence:
    selected_bundle = bundle or _passing_bundle()
    return ReadOnlyGuardrailEvidence(
        gate_result=validate_read_only_discovery_gates(required_read_only_discovery_gate_ids()),
        query_results=selected_bundle.guardrail_decisions,
        guardrails_passed=True,
        validated_before_contact=True,
        no_live_contact=True,
        live_certification_evidence=False,
    )


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


def _safe_request(*, scenario_id: str = "preflight", live_execution_enabled: bool = True) -> ReadOnlyDiscoveryRequest:
    config = build_sanitized_example_live_lab_config()
    bundle = _passing_bundle(scenario_id)
    gate_status = {gate.value: "satisfied" for gate in required_read_only_discovery_gate_ids()}
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
        guardrail_evidence=_passing_guardrail_evidence(bundle),
        redaction_policy_summary={"required": True, "status": "redacted"},
        artifact_policy_summary={"redaction_required": True, "publishable": True},
        retry_policy_summary={"automatic_recovery_enabled": False, "retry_requires_operator": True},
        live_execution_enabled=live_execution_enabled,
        mutation_enabled=False,
        live_certification_evidence=False,
    )


def _proven_result(request: ReadOnlyDiscoveryRequest):
    validation = validate_read_only_discovery_request(request)
    return replace(
        validation,
        decision=ReadOnlyBackendDecision.PASS,
        request_valid=True,
        physical_identity_evidence=PhysicalIdentityEvidence(
            physical_label="hub-a",
            expected_fingerprint_summary="expected",
            observed_fingerprint_summary="observed",
            signal_count=2,
            matched_signals=("kube-system-uid", "api-fingerprint"),
            missing_signals=(),
            mismatch_reason=None,
            proven=True,
        ),
        logical_role_evidence=LogicalRoleEvidence(
            primary_physical_label="hub-a",
            secondary_physical_label="hub-b",
            active_evidence_categories=("managed-cluster-inventory",),
            passive_evidence_categories=("backup-restore-evidence",),
            ambiguous_evidence_categories=(),
            previous_artifact_supporting_only=True,
            proven=True,
        ),
        managed_cluster_set_evidence=ManagedClusterSetEvidence(
            expected_names=("mc-1", "mc-2", "mc-3"),
            observed_names=("mc-1", "mc-2", "mc-3"),
            missing_names=(),
            extra_names=(),
            exact_match=True,
            unexpected_cluster_policy="block",
        ),
        read_prerequisite_evidence=ReadOnlyReadPrerequisiteEvidence(
            required_capabilities=("read-managedclusters",),
            allowed_capabilities=("read-managedclusters",),
            denied_capabilities=(),
            missing_capabilities=(),
            proven=True,
        ),
        transport_summary=TransportSummary(
            backend_phase=ReadOnlyBackendPhase.FAKE_TRANSPORT_FUTURE,
            transport_implemented=True,
            no_live_contact=True,
            live_contact_occurred=False,
            queries_executed_count=0,
            runtime_inputs_redacted=True,
        ),
    )


def test_module_imports_without_live_dependencies() -> None:
    assert ReadOnlyBackendDecision.BLOCKED.value == "BLOCKED"
    assert ReadOnlyBackendPhase.INTERFACE_SKELETON.value == "interface_skeleton"


def test_module_source_has_no_live_execution_or_config_loading_primitives() -> None:
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
        assert forbidden_module not in imported_roots

    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    for forbidden_call in ("open", "system", "run", "Popen", "getenv", "check_output", "check_call", "call"):
        assert forbidden_call not in called_names


def test_request_model_can_represent_sanitized_read_only_request() -> None:
    request = _safe_request()

    assert request.request_id == "phase8g-request"
    assert request.scenario_id in SCENARIOS_BY_ID
    assert request.mutation_enabled is False
    assert request.live_certification_evidence is False
    assert len(request.runtime_only_hub_refs) == 2


def test_request_validation_passes_safe_request_without_live_contact() -> None:
    result = validate_read_only_discovery_request(_safe_request())

    assert result.decision is ReadOnlyBackendDecision.PASS
    assert result.no_live_contact is True
    assert result.transport_summary.queries_executed_count == 0
    assert result.live_certification_evidence is False


def test_request_validation_blocks_missing_request_id() -> None:
    request = replace(_safe_request(), request_id="")

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "request_id" in result.blocking_fields


def test_request_validation_blocks_unsupported_catalog_scenario() -> None:
    request = replace(_safe_request(scenario_id="decommission"), scenario_id="decommission")

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "scenario_id" in result.blocking_fields


def test_request_validation_blocks_failed_phase8e_guardrail_evidence() -> None:
    failed_plan = replace(_passing_plan(), mutates_state=True)
    failed_result = validate_read_only_query_plan(failed_plan)
    bundle = replace(
        _passing_bundle(),
        query_plans=(failed_plan,),
        guardrail_decisions=(failed_result,),
        all_queries_guardrail_passed=False,
    )
    evidence = replace(_passing_guardrail_evidence(bundle), query_results=(failed_result,), guardrails_passed=False)
    request = replace(_safe_request(), query_plan_bundle=bundle, guardrail_evidence=evidence)

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "guardrail_evidence" in result.blocking_fields


def test_request_validation_blocks_missing_l0_l9_gates() -> None:
    missing_l9 = tuple(required_read_only_discovery_gate_ids()[:-1])
    plan = replace(_passing_plan(), required_gate_ids=missing_l9)
    plan_result = validate_read_only_query_plan(plan)
    bundle = ReadOnlyQueryPlanBundle(
        scenario_id="preflight",
        query_plans=(plan,),
        required_gate_ids=missing_l9,
        guardrail_decisions=(plan_result,),
        all_queries_guardrail_passed=False,
        live_certification_evidence=False,
    )
    request = replace(
        _safe_request(),
        required_gate_status={gate.value: "satisfied" for gate in missing_l9},
        query_plan_bundle=bundle,
        guardrail_evidence=_passing_guardrail_evidence(bundle),
    )

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "required_gate_ids" in result.blocking_fields
    gate_summary = {entry["gate_id"]: entry for entry in result.gate_summary}
    assert gate_summary["L9"]["present_in_plan"] is False


def test_request_validation_blocks_l10_based_mutation() -> None:
    plan = replace(
        _passing_plan(),
        required_gate_ids=tuple(required_read_only_discovery_gate_ids()) + ("L10",),
        l10_present=True,
        mutates_state=True,
    )
    plan_result = validate_read_only_query_plan(plan)
    bundle = replace(
        _passing_bundle(),
        query_plans=(plan,),
        guardrail_decisions=(plan_result,),
        all_queries_guardrail_passed=False,
    )
    request = replace(_safe_request(), query_plan_bundle=bundle, guardrail_evidence=_passing_guardrail_evidence(bundle))

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "query_plan_bundle" in result.blocking_fields


def test_request_validation_blocks_mutation_enabled_true() -> None:
    result = validate_read_only_discovery_request(replace(_safe_request(), mutation_enabled=True))

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "mutation_enabled" in result.blocking_fields


def test_request_validation_blocks_live_certification_evidence_true() -> None:
    result = validate_read_only_discovery_request(replace(_safe_request(), live_certification_evidence=True))

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "live_certification_evidence" in result.blocking_fields


def test_request_validation_blocks_query_bundle_scenario_mismatch() -> None:
    request = replace(_safe_request(), query_plan_bundle=_passing_bundle("lab-readiness"))

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "query_plan_bundle.scenario_id" in result.blocking_fields


def test_request_validation_blocks_runtime_only_fields_in_artifact_facing_summary() -> None:
    summary = dict(_safe_request().validated_config_summary)
    summary["runtime_only_hub_refs"] = [{"kubeconfig_ref": "runtime-access-handle-a"}]
    request = replace(_safe_request(), validated_config_summary=summary)

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "validated_config_summary" in result.blocking_fields


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("kubeconfig_ref", "/" + "home/operator/.kube/config"),
        ("context_ref", "https://" + "api.example.invalid:6443"),
        ("credential_ref", "token" + "=unsafe"),
        ("transport_handle_ref", "cluster" + "-id-private"),
        ("transport_handle_ref", "." + "release/runtime"),
        ("transport_handle_ref", "kubectl get namespaces"),
    ],
)
def test_request_validation_blocks_unsafe_runtime_ref_values(field_name: str, unsafe_value: str) -> None:
    hub_ref = replace(_runtime_refs()[0], **{field_name: unsafe_value})
    request = replace(_safe_request(), runtime_only_hub_refs=(hub_ref, _runtime_refs()[1]))

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "runtime_only_hub_refs" in result.blocking_fields


def test_runtime_only_hub_ref_artifact_summary_excludes_raw_refs() -> None:
    summary = _runtime_refs()[0].to_artifact_safe_summary()
    summary_text = _summary_text(summary)

    assert summary["physical_label"] == "hub-a"
    for forbidden in ("kubeconfig_ref", "context_ref", "credential_ref", "transport_handle_ref"):
        assert forbidden not in summary_text
    for raw_value in (
        "runtime-access-handle-a",
        "runtime-context-handle-a",
        "runtime-auth-handle-a",
        "runtime-transport-handle-a",
    ):
        assert raw_value not in summary_text


def test_query_plan_bundle_requires_all_guardrail_decisions_pass() -> None:
    plan = replace(_passing_plan(), verb="patch")
    bundle = ReadOnlyQueryPlanBundle(
        scenario_id="preflight",
        query_plans=(plan,),
        required_gate_ids=tuple(required_read_only_discovery_gate_ids()),
        guardrail_decisions=(validate_read_only_query_plan(plan),),
        all_queries_guardrail_passed=True,
        live_certification_evidence=False,
    )

    result = validate_read_only_discovery_request(replace(_safe_request(), query_plan_bundle=bundle))

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "query_plan_bundle" in result.blocking_fields


def test_query_plan_bundle_requires_all_required_gates() -> None:
    bundle = replace(_passing_bundle(), required_gate_ids=tuple(required_read_only_discovery_gate_ids()[:-1]))

    result = validate_read_only_discovery_request(replace(_safe_request(), query_plan_bundle=bundle))

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "required_gate_ids" in result.blocking_fields


def test_unimplemented_backend_returns_blocked_without_live_contact_or_certification() -> None:
    backend = UnimplementedReadOnlyDiscoveryBackend()

    result = backend.run_discovery(_safe_request())

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert result.live_certification_evidence is False
    assert result.mutation_enabled is False
    assert result.runtime_inputs_redacted is True
    assert result.no_live_contact is True
    assert result.transport_summary.queries_executed_count == 0
    assert "not implemented in Phase 8G" in result.first_blocking_reason


def test_unimplemented_backend_does_not_execute_query_plans() -> None:
    backend = UnimplementedReadOnlyDiscoveryBackend()

    result = backend.run_discovery(_safe_request())

    assert result.executed_query_ids == ()
    assert result.transport_summary.live_contact_occurred is False
    assert result.transport_summary.transport_implemented is False


def test_result_validation_rejects_pass_without_physical_identity_proof() -> None:
    result = replace(_proven_result(_safe_request()), physical_identity_evidence=None)

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "physical_identity_evidence" in validation.blocking_fields


def test_result_validation_rejects_pass_without_logical_role_proof() -> None:
    result = replace(_proven_result(_safe_request()), logical_role_evidence=None)

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "logical_role_evidence" in validation.blocking_fields


def test_result_validation_rejects_pass_without_exact_managed_cluster_set() -> None:
    result = replace(
        _proven_result(_safe_request()),
        managed_cluster_set_evidence=ManagedClusterSetEvidence(
            expected_names=("mc-1", "mc-2", "mc-3"),
            observed_names=("mc-1", "mc-2"),
            missing_names=("mc-3",),
            extra_names=(),
            exact_match=False,
            unexpected_cluster_policy="block",
        ),
    )

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "managed_cluster_set_evidence" in validation.blocking_fields


def test_result_validation_rejects_pass_without_read_prerequisites_proof() -> None:
    result = replace(_proven_result(_safe_request()), read_prerequisite_evidence=None)

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "read_prerequisite_evidence" in validation.blocking_fields


def test_result_validation_rejects_pass_with_live_certification_evidence_true() -> None:
    result = replace(_proven_result(_safe_request()), live_certification_evidence=True)

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "live_certification_evidence" in validation.blocking_fields


def test_result_validation_rejects_pass_with_mutation_enabled_true() -> None:
    result = replace(_proven_result(_safe_request()), mutation_enabled=True)

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "mutation_enabled" in validation.blocking_fields


def test_result_validation_rejects_pass_with_runtime_inputs_redacted_false() -> None:
    result = replace(_proven_result(_safe_request()), runtime_inputs_redacted=False)

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "runtime_inputs_redacted" in validation.blocking_fields


def test_request_validation_blocks_malformed_gate_status_without_raising() -> None:
    request = replace(_safe_request(), required_gate_status=("L0",))  # type: ignore[arg-type]

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "required_gate_status" in result.blocking_fields


def test_request_validation_blocks_present_but_unsatisfied_none_gate_status() -> None:
    gate_status: dict[str, Any] = {gate.value: "satisfied" for gate in required_read_only_discovery_gate_ids()}
    gate_status["L0"] = None  # present but explicitly not satisfied: must fail closed, not pass
    request = replace(_safe_request(), required_gate_status=gate_status)

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "required_gate_status" in result.blocking_fields


def test_request_validation_blocks_malformed_query_bundle_without_raising() -> None:
    request = replace(_safe_request(), query_plan_bundle=None)  # type: ignore[arg-type]

    result = validate_read_only_discovery_request(request)

    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert "query_plan_bundle" in result.blocking_fields


def test_result_validation_blocks_malformed_transport_summary_without_raising() -> None:
    result = replace(_proven_result(_safe_request()), transport_summary="not-a-transport-summary")  # type: ignore[arg-type]

    validation = validate_read_only_discovery_result(result)

    assert validation.decision is ReadOnlyBackendDecision.BLOCKED
    assert "transport_summary" in validation.blocking_fields


def test_artifact_safe_request_summary_contains_no_runtime_only_fields() -> None:
    summary = summarize_read_only_backend_request(_safe_request())
    summary_text = _summary_text(summary)

    assert summary["backend_phase"] == ReadOnlyBackendPhase.INTERFACE_SKELETON.value
    assert summary["mutation_enabled"] is False
    assert summary["live_certification_evidence"] is False
    for forbidden in (
        "kubeconfig_ref",
        "context_ref",
        "credential_ref",
        "transport_handle_ref",
        "runtime-access-handle-a",
        "runtime-auth-handle-a",
    ):
        assert forbidden not in summary_text


def test_artifact_safe_result_summary_contains_no_unsafe_values() -> None:
    result = UnimplementedReadOnlyDiscoveryBackend().run_discovery(_safe_request())
    summary = summarize_read_only_backend_result(result)
    summary_text = _summary_text(summary)

    assert summary["decision"] == ReadOnlyBackendDecision.BLOCKED.value
    assert summary["no_live_contact"] is True
    for forbidden in (
        "https://",
        "/home/",
        "~/.kube",
        "token=",
        "password=",
        "secret=",
        "." + "release",
        "kubeconfig_ref",
        "credential_ref",
    ):
        assert forbidden not in summary_text


def test_backend_protocol_can_be_type_used_with_unimplemented_backend() -> None:
    def use_backend(backend: ReadOnlyDiscoveryBackendProtocol) -> ReadOnlyBackendDecision:
        return backend.validate_request(_safe_request()).decision

    assert use_backend(UnimplementedReadOnlyDiscoveryBackend()) is ReadOnlyBackendDecision.PASS


def test_all_read_only_request_test_scenarios_are_catalog_ids() -> None:
    assert {"preflight", "decommission", "lab-readiness"}.issubset(SCENARIOS_BY_ID)


def test_no_live_execution_behavior_is_introduced() -> None:
    request_result = validate_read_only_discovery_request(_safe_request(live_execution_enabled=True))
    backend_result = UnimplementedReadOnlyDiscoveryBackend().run_discovery(_safe_request(live_execution_enabled=True))

    assert request_result.transport_summary.queries_executed_count == 0
    assert request_result.no_live_contact is True
    assert backend_result.decision is ReadOnlyBackendDecision.BLOCKED
    assert backend_result.no_live_contact is True
    assert backend_result.transport_summary.live_contact_occurred is False
    assert backend_result.live_certification_evidence is False
