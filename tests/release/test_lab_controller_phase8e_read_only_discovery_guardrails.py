from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.release.lab_controller.live_config import LiveGateId
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyDiscoveryGuardDecision,
    ReadOnlyDiscoveryGuardResult,
    ReadOnlyQueryFamily,
    ReadOnlyQueryFamilyStatus,
    ReadOnlyQueryPlan,
    ReadOnlyScenarioEligibility,
    ReadOnlyVerbClass,
    build_example_read_only_discovery_artifact,
    build_example_read_only_query_plan,
    classify_read_only_verb,
    read_only_query_family_status,
    read_only_scenario_eligibility,
    required_read_only_discovery_gate_ids,
    required_read_only_discovery_gate_requirements,
    summarize_read_only_discovery_gates,
    summarize_read_only_query_plan,
    unclassified_catalog_scenarios,
    validate_read_only_discovery_artifact_contract,
    validate_read_only_discovery_gates,
    validate_read_only_query_plan,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

_MODULE_PATH = Path(__file__).resolve().parent / "lab_controller" / "read_only_discovery.py"


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


# --- 1-3: Gate policy ----------------------------------------------------------------------------


def test_required_read_only_gate_set_includes_l0_through_l9() -> None:
    gate_ids = required_read_only_discovery_gate_ids()

    assert gate_ids == (
        LiveGateId.L0,
        LiveGateId.L1,
        LiveGateId.L2,
        LiveGateId.L3,
        LiveGateId.L4,
        LiveGateId.L5,
        LiveGateId.L6,
        LiveGateId.L7,
        LiveGateId.L8,
        LiveGateId.L9,
    )
    assert validate_read_only_discovery_gates(gate_ids).decision is ReadOnlyDiscoveryGuardDecision.PASS


def test_l10_is_not_required_for_read_only_discovery() -> None:
    assert LiveGateId.L10 not in required_read_only_discovery_gate_ids()

    l10_requirement = next(
        req for req in required_read_only_discovery_gate_requirements() if req.gate_id is LiveGateId.L10
    )
    assert l10_requirement.required_before_read_only_discovery is False

    summary = {entry["gate_id"]: entry for entry in summarize_read_only_discovery_gates()}
    assert summary["L10"]["required_for_read_only_discovery"] is False
    assert all(summary[f"L{index}"]["required_for_read_only_discovery"] is True for index in range(10))

    # Providing L10 in addition to L0-L9 is harmless; the gate set still passes.
    with_l10 = tuple(required_read_only_discovery_gate_ids()) + (LiveGateId.L10,)
    assert validate_read_only_discovery_gates(with_l10).decision is ReadOnlyDiscoveryGuardDecision.PASS


def test_unknown_extra_gate_blocks_read_only_discovery() -> None:
    unsafe_unknown_gate = "L99-token=unsafe"
    gate_ids = tuple(required_read_only_discovery_gate_ids()) + (unsafe_unknown_gate,)

    result = validate_read_only_discovery_gates(gate_ids)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "gate_ids" in result.blocking_fields
    assert any("unknown read-only discovery gates" in reason for reason in result.reasons)
    assert all(unsafe_unknown_gate not in reason for reason in result.reasons)


def test_l10_cannot_authorize_mutation_in_phase_8e() -> None:
    plan = replace(
        build_example_read_only_query_plan(),
        mutates_state=True,
        l10_present=True,
    )

    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "l10_present" in result.blocking_fields
    assert any("L10" in reason for reason in result.reasons)
    assert "mutates_state" in result.blocking_fields


# --- 4-14: Scenario eligibility policy -----------------------------------------------------------


@pytest.mark.parametrize("scenario_id", ["lab-readiness", "baseline-check", "preflight", "final-baseline-check"])
def test_initial_allowed_scenarios_are_classified_initially_allowed(scenario_id: str) -> None:
    assert read_only_scenario_eligibility(scenario_id) is ReadOnlyScenarioEligibility.INITIALLY_ALLOWED


@pytest.mark.parametrize("scenario_id", ["static-gates", "runtime-parity"])
def test_static_and_runtime_parity_are_supporting_non_live_only(scenario_id: str) -> None:
    assert read_only_scenario_eligibility(scenario_id) is ReadOnlyScenarioEligibility.SUPPORTING_NON_LIVE_ONLY


@pytest.mark.parametrize("scenario_id", ["bash-discovery", "bash-postflight"])
def test_bash_discovery_and_postflight_are_deferred(scenario_id: str) -> None:
    assert read_only_scenario_eligibility(scenario_id) is ReadOnlyScenarioEligibility.DEFERRED


@pytest.mark.parametrize("scenario_id", ["python-passive-switchover", "ansible-passive-switchover"])
def test_passive_switchover_scenarios_are_blocked(scenario_id: str) -> None:
    assert read_only_scenario_eligibility(scenario_id) is ReadOnlyScenarioEligibility.BLOCKED


@pytest.mark.parametrize("scenario_id", ["python-restore-only", "ansible-restore-only"])
def test_restore_only_scenarios_are_blocked(scenario_id: str) -> None:
    assert read_only_scenario_eligibility(scenario_id) is ReadOnlyScenarioEligibility.BLOCKED


def test_decommission_is_blocked() -> None:
    assert read_only_scenario_eligibility("decommission") is ReadOnlyScenarioEligibility.BLOCKED


def test_failure_injection_is_blocked() -> None:
    assert read_only_scenario_eligibility("failure-injection") is ReadOnlyScenarioEligibility.BLOCKED


def test_full_restore_is_blocked() -> None:
    assert read_only_scenario_eligibility("full-restore") is ReadOnlyScenarioEligibility.BLOCKED


def test_soak_is_blocked_or_deferred() -> None:
    assert read_only_scenario_eligibility("soak") in {
        ReadOnlyScenarioEligibility.BLOCKED,
        ReadOnlyScenarioEligibility.DEFERRED,
    }


def test_rbac_bootstrap_is_blocked_because_it_mutates_rbac() -> None:
    assert read_only_scenario_eligibility("rbac-bootstrap") is ReadOnlyScenarioEligibility.BLOCKED


def test_unknown_scenario_id_is_blocked_in_query_plan() -> None:
    assert read_only_scenario_eligibility("totally-unknown-scenario") is None

    plan = replace(build_example_read_only_query_plan(), scenario_id="totally-unknown-scenario")
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "scenario_id" in result.blocking_fields
    assert result.scenario_eligibility is None


# --- 15-19: Query family policy ------------------------------------------------------------------


def test_allowed_read_only_query_family_passes_with_gates_and_safe_fields() -> None:
    result = validate_read_only_query_plan(build_example_read_only_query_plan())

    assert result.decision is ReadOnlyDiscoveryGuardDecision.PASS
    assert result.reasons == ()
    assert result.scenario_eligibility is ReadOnlyScenarioEligibility.INITIALLY_ALLOWED
    assert result.query_family_status is ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES
    assert result.verb_class is ReadOnlyVerbClass.READ_ONLY
    assert result.missing_gates == ()
    assert result.live_certification_evidence is False


@pytest.mark.parametrize(
    "family",
    [
        ReadOnlyQueryFamily.CLUSTER_IDENTITY,
        ReadOnlyQueryFamily.NAMESPACE_UID,
        ReadOnlyQueryFamily.CLUSTER_VERSION,
        ReadOnlyQueryFamily.ACM_MCE_MCH_STATUS,
        ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS,
        ReadOnlyQueryFamily.BACKUP_RESTORE_STATUS,
    ],
)
def test_allowed_after_gates_families_pass(family: ReadOnlyQueryFamily) -> None:
    plan = replace(build_example_read_only_query_plan(), query_family=family)
    assert validate_read_only_query_plan(plan).decision is ReadOnlyDiscoveryGuardDecision.PASS


@pytest.mark.parametrize(
    "family",
    [ReadOnlyQueryFamily.ARGOCD_STATUS, ReadOnlyQueryFamily.SUBJECT_ACCESS_REVIEW],
)
def test_conditional_families_block_without_separate_design_or_scenario_requirement(
    family: ReadOnlyQueryFamily,
) -> None:
    plan = replace(build_example_read_only_query_plan(), query_family=family)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "query_family" in result.blocking_fields
    assert result.query_family_status is ReadOnlyQueryFamilyStatus.CONDITIONAL


def test_secret_bearing_query_family_is_blocked() -> None:
    plan = replace(build_example_read_only_query_plan(), query_family=ReadOnlyQueryFamily.SECRET_BEARING_RESOURCES)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "query_family" in result.blocking_fields
    assert result.query_family_status is ReadOnlyQueryFamilyStatus.BLOCKED


def test_arbitrary_shell_query_family_is_blocked() -> None:
    plan = replace(build_example_read_only_query_plan(), query_family=ReadOnlyQueryFamily.ARBITRARY_SHELL)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert result.query_family_status is ReadOnlyQueryFamilyStatus.FORBIDDEN


def test_mutation_capable_query_family_is_blocked() -> None:
    plan = replace(build_example_read_only_query_plan(), query_family=ReadOnlyQueryFamily.MUTATION_CAPABLE)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert result.query_family_status is ReadOnlyQueryFamilyStatus.FORBIDDEN


def test_agent_invented_query_family_is_blocked() -> None:
    plan = replace(build_example_read_only_query_plan(), query_family=ReadOnlyQueryFamily.AGENT_INVENTED)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert result.query_family_status is ReadOnlyQueryFamilyStatus.FORBIDDEN


def test_unknown_query_family_fails_closed() -> None:
    assert read_only_query_family_status("invented-family") is None

    plan = replace(build_example_read_only_query_plan(), query_family="invented-family")
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "query_family" in result.blocking_fields


def test_logs_events_family_is_deferred_and_blocks_plan() -> None:
    assert read_only_query_family_status(ReadOnlyQueryFamily.LOGS_EVENTS) is ReadOnlyQueryFamilyStatus.DEFERRED

    plan = replace(build_example_read_only_query_plan(), query_family=ReadOnlyQueryFamily.LOGS_EVENTS)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED


# --- 20-23: Verb policy --------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["get", "list", "describe"])
def test_read_only_verbs_pass(verb: str) -> None:
    assert classify_read_only_verb(verb) is ReadOnlyVerbClass.READ_ONLY

    plan = replace(build_example_read_only_query_plan(), verb=verb)
    assert validate_read_only_query_plan(plan).decision is ReadOnlyDiscoveryGuardDecision.PASS


@pytest.mark.parametrize("verb", ["watch", "subjectaccessreview", "selfsubjectaccessreview"])
def test_unmodeled_read_like_verbs_fail_closed(verb: str) -> None:
    assert classify_read_only_verb(verb) is ReadOnlyVerbClass.UNKNOWN

    plan = replace(build_example_read_only_query_plan(), verb=verb)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "verb" in result.blocking_fields


@pytest.mark.parametrize("verb", ["create", "update", "patch", "delete", "apply", "scale", "rollout"])
def test_mutating_verbs_are_blocked(verb: str) -> None:
    assert classify_read_only_verb(verb) is ReadOnlyVerbClass.MUTATING

    plan = replace(build_example_read_only_query_plan(), verb=verb)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "verb" in result.blocking_fields


@pytest.mark.parametrize("verb", ["pause", "resume", "sync", "restore", "decommission"])
def test_mutating_lifecycle_verbs_are_blocked(verb: str) -> None:
    assert classify_read_only_verb(verb) is ReadOnlyVerbClass.MUTATING

    plan = replace(build_example_read_only_query_plan(), verb=verb)
    assert validate_read_only_query_plan(plan).decision is ReadOnlyDiscoveryGuardDecision.BLOCKED


@pytest.mark.parametrize("verb", ["exec", "port-forward", "cp"])
def test_unsafe_verbs_are_blocked(verb: str) -> None:
    assert classify_read_only_verb(verb) is ReadOnlyVerbClass.UNSAFE

    plan = replace(build_example_read_only_query_plan(), verb=verb)
    assert validate_read_only_query_plan(plan).decision is ReadOnlyDiscoveryGuardDecision.BLOCKED


def test_unknown_verb_fails_closed() -> None:
    assert classify_read_only_verb("frobnicate") is ReadOnlyVerbClass.UNKNOWN

    plan = replace(build_example_read_only_query_plan(), verb="frobnicate")
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "verb" in result.blocking_fields


# --- 24-29: Query plan flag and gate validation --------------------------------------------------


def test_missing_required_gate_blocks_query_plan() -> None:
    incomplete_gates = tuple(gate for gate in required_read_only_discovery_gate_ids() if gate is not LiveGateId.L9)
    plan = replace(build_example_read_only_query_plan(), required_gate_ids=incomplete_gates)

    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "required_gate_ids" in result.blocking_fields
    assert "L9" in result.missing_gates


def test_unknown_extra_gate_blocks_query_plan() -> None:
    unsafe_unknown_gate = "L99-token=unsafe"
    gate_ids = tuple(required_read_only_discovery_gate_ids()) + (unsafe_unknown_gate,)
    plan = replace(build_example_read_only_query_plan(), required_gate_ids=gate_ids)

    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "required_gate_ids" in result.blocking_fields
    assert any("unknown read-only discovery gates" in reason for reason in result.reasons)
    assert all(unsafe_unknown_gate not in reason for reason in result.reasons)


@pytest.mark.parametrize("artifact_field", ["kubeconfig_ref", "raw_command", "argv", "api_url"])
def test_runtime_only_or_forbidden_artifact_field_blocks_query_plan(artifact_field: str) -> None:
    plan = replace(build_example_read_only_query_plan(), artifact_fields=(artifact_field,))

    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert artifact_field in result.blocking_fields


def test_mutates_state_blocks_query_plan() -> None:
    plan = replace(build_example_read_only_query_plan(), mutates_state=True)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "mutates_state" in result.blocking_fields


def test_uses_arbitrary_command_blocks_query_plan() -> None:
    plan = replace(build_example_read_only_query_plan(), uses_arbitrary_command=True)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "uses_arbitrary_command" in result.blocking_fields


def test_agent_invented_flag_blocks_query_plan() -> None:
    plan = replace(build_example_read_only_query_plan(), agent_invented=True)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "agent_invented" in result.blocking_fields


def test_may_expose_secrets_blocks_query_plan() -> None:
    plan = replace(build_example_read_only_query_plan(), may_expose_secrets=True)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "may_expose_secrets" in result.blocking_fields


def test_redaction_required_false_blocks_query_plan() -> None:
    plan = replace(build_example_read_only_query_plan(), redaction_required=False)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "redaction_required" in result.blocking_fields


def test_live_certification_evidence_blocks_query_plan() -> None:
    plan = replace(build_example_read_only_query_plan(), live_certification_evidence=True)
    result = validate_read_only_query_plan(plan)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "live_certification_evidence" in result.blocking_fields


# --- 30-36: Discovery artifact contract guardrails -----------------------------------------------


def test_example_artifact_contract_passes() -> None:
    result = validate_read_only_discovery_artifact_contract(build_example_read_only_discovery_artifact())

    assert result.decision is ReadOnlyDiscoveryGuardDecision.PASS
    assert result.live_certification_evidence is False


def test_live_certification_evidence_true_blocks_artifact_contract() -> None:
    artifact = replace(build_example_read_only_discovery_artifact(), live_certification_evidence=True)
    result = validate_read_only_discovery_artifact_contract(artifact)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "live_certification_evidence" in result.blocking_fields


def test_runtime_inputs_redacted_false_blocks_artifact_contract() -> None:
    artifact = replace(build_example_read_only_discovery_artifact(), runtime_inputs_redacted=False)
    result = validate_read_only_discovery_artifact_contract(artifact)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "runtime_inputs_redacted" in result.blocking_fields


def test_mutation_enabled_true_blocks_artifact_contract() -> None:
    artifact = replace(build_example_read_only_discovery_artifact(), mutation_enabled=True)
    result = validate_read_only_discovery_artifact_contract(artifact)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "mutation_enabled" in result.blocking_fields


def test_discovery_mode_must_be_read_only() -> None:
    artifact = replace(build_example_read_only_discovery_artifact(), discovery_mode="mutating")
    result = validate_read_only_discovery_artifact_contract(artifact)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "discovery_mode" in result.blocking_fields


@pytest.mark.parametrize("bad_value", [None, "false", "False", 0, 1])
def test_mutation_enabled_non_false_value_fails_closed(bad_value: object) -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["mutation_enabled"] = bad_value

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "mutation_enabled" in result.blocking_fields


@pytest.mark.parametrize("bad_value", [None, "false", "true", 0])
def test_live_certification_evidence_non_false_value_fails_closed(bad_value: object) -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["live_certification_evidence"] = bad_value

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "live_certification_evidence" in result.blocking_fields


@pytest.mark.parametrize("bad_value", [None, "true", "True", 1])
def test_runtime_inputs_redacted_non_true_value_fails_closed(bad_value: object) -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["runtime_inputs_redacted"] = bad_value

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "runtime_inputs_redacted" in result.blocking_fields


def test_live_execution_enabled_true_is_permitted_for_future_live_contact() -> None:
    # Per the Phase 8D design, an artifact may flag future live contact via live_execution_enabled
    # as long as it never implies certification and never enables mutation.
    artifact = replace(build_example_read_only_discovery_artifact(), live_execution_enabled=True)
    result = validate_read_only_discovery_artifact_contract(artifact)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.PASS
    assert result.live_certification_evidence is False
    assert result.artifact_safe_summary["live_execution_enabled"] is True


@pytest.mark.parametrize("bad_value", [None, "yes", "true", 1])
def test_live_execution_enabled_non_boolean_fails_closed(bad_value: object) -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["live_execution_enabled"] = bad_value

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "live_execution_enabled" in result.blocking_fields


def test_runtime_only_field_in_artifact_blocks_contract() -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["kubeconfig_ref"] = "fingerprint-only-handle"

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "kubeconfig_ref" in result.blocking_fields


def test_raw_kubeconfig_like_value_in_artifact_is_no_go() -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["physical_identity_evidence"] = "/" + "home/operator/.kube/config"

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.NO_GO


def test_raw_api_url_like_value_in_artifact_is_no_go() -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["logical_role_evidence"] = "https://" + "api.example.invalid:6443"

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.NO_GO


def test_credential_like_value_in_artifact_is_no_go() -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["read_prerequisite_evidence"] = "token" + "=unsafe-value"

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.NO_GO


@pytest.mark.parametrize("bad_value", ["cluster-id-unsafe", "cluster_id_unsafe"])
def test_private_id_like_value_in_artifact_is_no_go(bad_value: str) -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["managed_cluster_set_evidence"] = bad_value

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.NO_GO


@pytest.mark.parametrize(
    "bad_value",
    ["kubectl get namespaces", "bash -c whoami", "ansible-playbook site.yml", "curl|cat", "kubectl;delete"],
)
def test_arbitrary_command_like_value_in_artifact_is_no_go(bad_value: str) -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["command_query_summary"] = [bad_value]

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.NO_GO


def test_release_path_value_in_artifact_is_no_go() -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    payload["command_query_summary"] = ["." + "release/artifacts/run.json"]

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.NO_GO


def test_missing_required_artifact_field_blocks_contract() -> None:
    payload = build_example_read_only_discovery_artifact().to_payload()
    del payload["gate_status"]

    result = validate_read_only_discovery_artifact_contract(payload)

    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert "gate_status" in result.blocking_fields


# --- 37: Artifact-safe summaries -----------------------------------------------------------------


def test_artifact_safe_summary_excludes_runtime_only_and_unsafe_command_strings() -> None:
    # A plan with a shell-like verb must still produce a summary that omits the raw command string
    # and any runtime-only field names.
    plan = replace(build_example_read_only_query_plan(), verb="bash -c rm -rf /tmp")
    summary = summarize_read_only_query_plan(plan)
    summary_text = _summary_text(summary)

    for forbidden in ("kubeconfig", "context_ref", "runtime_ref", "bash -c", "rm -rf", "https://", "token="):
        assert forbidden not in summary_text

    assert summary["scenario_id"] == "preflight"
    assert summary["verb_class"] == ReadOnlyVerbClass.UNKNOWN.value
    assert summary["live_certification_evidence"] is False
    assert summary["mutation_enabled"] is False


def test_passing_plan_summary_includes_decision_family_and_gate_ids() -> None:
    # managed_cluster_status is a benign family label that survives the shared artifact redactor
    # (cluster_identity legitimately over-redacts because it contains the substring "cluster_id").
    plan = replace(build_example_read_only_query_plan(), query_family=ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    summary = summarize_read_only_query_plan(plan)

    assert summary["decision"] == ReadOnlyDiscoveryGuardDecision.PASS.value
    assert summary["query_family"] == ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS.value
    assert summary["verb_class"] == ReadOnlyVerbClass.READ_ONLY.value
    assert "L0" in summary["required_gate_ids"]
    assert "L9" in summary["required_gate_ids"]
    assert summary["missing_gates"] == []
    assert summary["redaction_status"] == "redacted"


# --- 38: Structured, exception-free results ------------------------------------------------------


def test_guardrail_results_are_structured_without_exceptions() -> None:
    plan = ReadOnlyQueryPlan(
        scenario_id="unknown-scenario",
        query_family="unknown-family",
        verb="frobnicate",
        required_gate_ids=(),
    )
    result = validate_read_only_query_plan(plan)

    assert isinstance(result, ReadOnlyDiscoveryGuardResult)
    assert result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert isinstance(result.reasons, tuple)
    assert isinstance(result.blocking_fields, tuple)
    assert isinstance(result.artifact_safe_summary, dict)
    assert isinstance(result.to_dict(), dict)

    artifact_result = validate_read_only_discovery_artifact_contract({})
    assert artifact_result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED
    assert isinstance(artifact_result.reasons, tuple)

    # A non-mapping, non-contract input fails closed instead of raising.
    bogus_result = validate_read_only_discovery_artifact_contract("not-an-artifact")  # type: ignore[arg-type]
    assert bogus_result.decision is ReadOnlyDiscoveryGuardDecision.BLOCKED


# --- 39: Catalog coverage ------------------------------------------------------------------------


def test_all_catalog_scenarios_have_explicit_eligibility() -> None:
    assert unclassified_catalog_scenarios() == ()
    for scenario_id in SCENARIOS_BY_ID:
        assert read_only_scenario_eligibility(scenario_id) is not None


def test_eligibility_classification_is_conservative() -> None:
    # Only the four read-only candidates may be initially allowed.
    initially_allowed = {
        scenario_id
        for scenario_id in SCENARIOS_BY_ID
        if read_only_scenario_eligibility(scenario_id) is ReadOnlyScenarioEligibility.INITIALLY_ALLOWED
    }
    assert initially_allowed == {"lab-readiness", "baseline-check", "preflight", "final-baseline-check"}


# --- 40: No live execution behavior --------------------------------------------------------------


def test_no_live_execution_behavior_is_introduced() -> None:
    # Every guardrail result keeps certification evidence false and never enables live execution.
    pass_plan = validate_read_only_query_plan(build_example_read_only_query_plan())
    assert pass_plan.live_certification_evidence is False
    assert pass_plan.artifact_safe_summary["live_execution_enabled"] is False
    assert pass_plan.artifact_safe_summary["mutation_enabled"] is False

    gate_result = validate_read_only_discovery_gates(required_read_only_discovery_gate_ids())
    assert gate_result.live_certification_evidence is False

    artifact_result = validate_read_only_discovery_artifact_contract(build_example_read_only_discovery_artifact())
    assert artifact_result.live_certification_evidence is False


def test_module_source_has_no_live_execution_primitives() -> None:
    # Parse the module AST so docstring text that *describes* the non-live boundary (e.g. mentioning
    # os.environ or kubectl) does not trip the guardrail; only real imports/calls matter.
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    for forbidden_module in ("os", "subprocess", "socket", "yaml", "json", "kubernetes", "requests", "urllib", "http"):
        assert forbidden_module not in imported_roots, f"read_only_discovery.py must not import {forbidden_module!r}"

    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    for forbidden_call in ("open", "system", "run", "Popen", "getenv", "check_output", "check_call", "call"):
        assert forbidden_call not in called_names, f"read_only_discovery.py must not call {forbidden_call!r}"
