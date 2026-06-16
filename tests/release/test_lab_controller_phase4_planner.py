from __future__ import annotations

import json
from dataclasses import replace

from tests.release.lab_controller.controller import ScenarioExecutionStatus
from tests.release.lab_controller.discovery import fake_lab_observation
from tests.release.lab_controller.models import (
    DesiredRoleState,
    HubIdentityEvidence,
    HubRoleSignal,
    ManagedClusterEvidence,
    PhysicalHubConfig,
    PhysicalHubLabel,
    StableLabConfig,
)
from tests.release.lab_controller.planner import (
    CertificationDecision,
    build_ping_pong_plan,
    evaluate_certification_decision,
    merge_segment_artifacts,
    run_certification_plan,
)

EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")


def _identity(label: PhysicalHubLabel) -> HubIdentityEvidence:
    return HubIdentityEvidence(
        physical_label=label,
        kube_system_uid=f"uid-{label.value}",
        api_server_fingerprint=f"api-{label.value}",
        context_name=f"{label.value}-context",
    )


def _hub_config(label: PhysicalHubLabel) -> PhysicalHubConfig:
    return PhysicalHubConfig(
        physical_label=label,
        kubeconfig_reference=f"kubeconfig-ref-{label.value}",
        context_name=f"{label.value}-context",
        expected_identity=_identity(label),
    )


def _lab_config() -> StableLabConfig:
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: _hub_config(PhysicalHubLabel.HUB_A),
            PhysicalHubLabel.HUB_B: _hub_config(PhysicalHubLabel.HUB_B),
        },
        expected_managed_cluster_names=EXPECTED_CLUSTERS,
        enabled_streams=("python", "ansible"),
        scenario_ids=("preflight",),
        profile_name="lab-controller-phase4",
        artifact_root="artifacts/release-lab/unit",
    )


def _expected_identities(config: StableLabConfig | None = None) -> dict[PhysicalHubLabel, HubIdentityEvidence]:
    config = config or _lab_config()
    return {
        label: hub.expected_identity for label, hub in config.physical_hubs.items() if hub.expected_identity is not None
    }


def _desired(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> DesiredRoleState:
    return DesiredRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _observation(primary: PhysicalHubLabel | None):
    return fake_lab_observation(primary_label=primary, expected_managed_cluster_names=EXPECTED_CLUSTERS)


def _both_active_observation():
    return replace(
        _observation(PhysicalHubLabel.HUB_A),
        observations=tuple(
            replace(
                hub,
                role_signal=HubRoleSignal.ACTIVE,
                managed_clusters=ManagedClusterEvidence(
                    expected_names=EXPECTED_CLUSTERS,
                    observed_names=EXPECTED_CLUSTERS,
                ),
            )
            for hub in _observation(PhysicalHubLabel.HUB_A).observations
        ),
    )


def _managed_cluster_drift_observation():
    return replace(
        _observation(PhysicalHubLabel.HUB_A),
        observations=tuple(
            replace(
                hub,
                managed_clusters=ManagedClusterEvidence(
                    expected_names=EXPECTED_CLUSTERS,
                    observed_names=("mc-1", "mc-2", "mc-extra") if hub.physical_label is PhysicalHubLabel.HUB_A else (),
                ),
            )
            for hub in _observation(PhysicalHubLabel.HUB_A).observations
        ),
    )


def _replace_segment(plan, index: int, segment):
    segments = list(plan.segments)
    segments[index] = segment
    return replace(plan, segments=tuple(segments))


def test_full_ping_pong_plan_passes() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.PASS
    assert result.final_role_state is not None
    assert result.final_role_state.primary_physical_hub is PhysicalHubLabel.HUB_A
    assert [segment.decision for segment in result.segment_results] == [CertificationDecision.PASS] * 5
    assert [transition.scenario_id for transition in result.role_transition_graph] == [
        "preflight",
        "python-passive-switchover",
        "final-baseline-check",
        "ansible-passive-switchover",
        "final-baseline-check",
    ]


def test_second_mutation_is_blocked_when_previous_final_state_is_not_proven() -> None:
    plan = build_ping_pong_plan()
    broken_python = replace(
        plan.segments[1],
        execution_result=replace(plan.segments[1].execution_result, post_segment_observation=None),
        post_segment_observation=None,
    )

    result = run_certification_plan(
        _replace_segment(plan, 1, broken_python),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.RECOVERY_REQUIRED
    assert len(result.segment_results) == 2
    assert result.segment_results[-1].planned_segment.segment_id == "python-hub-a-to-hub-b"
    assert "missing post-segment observation" in result.first_blocking_reason


def test_plan_stops_immediately_on_segment_no_go() -> None:
    plan = build_ping_pong_plan()
    failed_segment = replace(
        plan.segments[1],
        execution_result=replace(
            plan.segments[1].execution_result,
            status=ScenarioExecutionStatus.FAILED,
            failure_reason="activation failed",
        ),
    )

    result = run_certification_plan(
        _replace_segment(plan, 1, failed_segment),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.NO_GO
    assert [item.planned_segment.segment_id for item in result.segment_results] == [
        "baseline-hub-a",
        "python-hub-a-to-hub-b",
    ]
    assert "activation failed" in result.first_blocking_reason


def test_plan_stops_immediately_on_recovery_required() -> None:
    plan = build_ping_pong_plan()
    failed_segment = replace(
        plan.segments[1],
        execution_result=replace(
            plan.segments[1].execution_result,
            status=ScenarioExecutionStatus.FAILED,
            mutation_completed=False,
            post_segment_observation=None,
            failure_reason="activation state is ambiguous",
        ),
        post_segment_observation=None,
    )

    result = run_certification_plan(
        _replace_segment(plan, 1, failed_segment),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.RECOVERY_REQUIRED
    assert len(result.segment_results) == 2
    assert "post-segment role state cannot be proven" in result.first_blocking_reason


def test_retryable_pre_mutation_failure_returns_infra_retryable_and_does_not_continue() -> None:
    plan = build_ping_pong_plan()
    retryable = replace(
        plan.segments[0],
        execution_result=replace(
            plan.segments[0].execution_result,
            status=ScenarioExecutionStatus.FAILED,
            failure_reason="temporary artifact store outage",
            retryable_infra_failure=True,
        ),
    )

    result = run_certification_plan(
        _replace_segment(plan, 0, retryable),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.INFRA_RETRYABLE
    assert len(result.segment_results) == 1
    assert "temporary artifact store outage" in result.first_blocking_reason


def test_wrong_expected_initial_state_blocks_the_next_segment() -> None:
    plan = build_ping_pong_plan()
    wrong_verification = replace(
        plan.segments[2],
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )

    result = run_certification_plan(
        _replace_segment(plan, 2, wrong_verification),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.BLOCKED
    assert result.segment_results[-1].planned_segment.segment_id == "verify-hub-b"
    assert "does not match proven final role state" in result.first_blocking_reason


def test_stale_role_handoff_blocks_the_next_segment() -> None:
    plan = build_ping_pong_plan()
    stale_verification = replace(
        plan.segments[2],
        pre_segment_observation=_observation(PhysicalHubLabel.HUB_A),
    )

    result = run_certification_plan(
        _replace_segment(plan, 2, stale_verification),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.BLOCKED
    assert result.segment_results[-1].planned_segment.segment_id == "verify-hub-b"
    assert "stale handoff observation" in result.first_blocking_reason


def test_missing_post_state_after_mutating_segment_returns_recovery_required() -> None:
    plan = build_ping_pong_plan()
    missing_post = replace(
        plan.segments[1],
        execution_result=replace(plan.segments[1].execution_result, post_segment_observation=None),
        post_segment_observation=None,
    )

    result = run_certification_plan(
        _replace_segment(plan, 1, missing_post),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.RECOVERY_REQUIRED
    assert "missing post-segment observation" in result.first_blocking_reason


def test_successful_mutating_segment_with_mutation_completed_false_does_not_allow_handoff() -> None:
    plan = build_ping_pong_plan()
    incomplete = replace(
        plan.segments[1],
        execution_result=replace(plan.segments[1].execution_result, mutation_completed=False),
    )

    result = run_certification_plan(
        _replace_segment(plan, 1, incomplete),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.RECOVERY_REQUIRED
    assert len(result.segment_results) == 2
    assert "without mutation completion evidence" in result.first_blocking_reason


def test_both_hubs_active_in_any_segment_blocks_plan() -> None:
    plan = build_ping_pong_plan()
    ambiguous = replace(plan.segments[2], pre_segment_observation=_both_active_observation())

    result = run_certification_plan(
        _replace_segment(plan, 2, ambiguous),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.NO_GO
    assert result.segment_results[-1].decision is CertificationDecision.NO_GO
    assert "both hubs active" in result.first_blocking_reason


def test_neither_hub_active_returns_recovery_required() -> None:
    plan = build_ping_pong_plan()
    neither = replace(plan.segments[0], pre_segment_observation=_observation(None))

    result = run_certification_plan(
        _replace_segment(plan, 0, neither),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.RECOVERY_REQUIRED
    assert "neither hub is active" in result.first_blocking_reason


def test_managed_cluster_drift_blocks_plan() -> None:
    plan = build_ping_pong_plan()
    drift = replace(plan.segments[0], pre_segment_observation=_managed_cluster_drift_observation())

    result = run_certification_plan(
        _replace_segment(plan, 0, drift),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.RECOVERY_REQUIRED
    assert "unexpected managed cluster set" in result.first_blocking_reason


def test_unknown_scenario_in_plan_fails_closed() -> None:
    plan = build_ping_pong_plan()
    unknown = replace(
        plan.segments[0],
        scenario_id="future-certification-scenario",
        execution_result=replace(plan.segments[0].execution_result, scenario_id="future-certification-scenario"),
    )

    result = run_certification_plan(
        _replace_segment(plan, 0, unknown),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.BLOCKED
    assert "unknown release scenario" in result.first_blocking_reason


def test_destructive_disposable_lab_only_scenario_cannot_pass_through_normal_planner() -> None:
    plan = build_ping_pong_plan()
    destructive = replace(
        plan.segments[0],
        scenario_id="decommission",
        mutates_lab=True,
        execution_result=replace(plan.segments[0].execution_result, scenario_id="decommission"),
    )

    result = run_certification_plan(
        _replace_segment(plan, 0, destructive),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.BLOCKED
    assert "destructive/disposable-lab-only" in result.first_blocking_reason


def test_role_transition_graph_records_hub_a_to_hub_b_and_hub_b_to_hub_a() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    transitions = [item for item in result.role_transition_graph if item.mutation_attempted]

    assert [(item.initial_primary_physical_hub, item.expected_final_primary_physical_hub) for item in transitions] == [
        (PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        (PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
    ]
    assert all(item.mutation_completed for item in transitions)


def test_merged_artifact_contains_all_segment_decisions_and_final_decision() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    payload = result.artifact_bundle.payload

    assert payload["final_decision"] == "PASS"
    assert payload["summary_counts"]["total_segments"] == 5
    assert payload["summary_counts"]["passed"] == 5
    assert [item["decision"] for item in payload["per_segment_decisions"]] == ["PASS"] * 5
    assert len(payload["role_transition_graph"]) == 5


def test_merged_artifact_rejects_or_sanitizes_sensitive_payloads() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    sensitive_url = "https://" + "api" + ".private" + ".cluster:6443"
    tampered_result = replace(
        result.segment_results[0],
        artifact_payload={
            **result.segment_results[0].artifact_payload,
            "debug_path": "/home/operator/.kube/config",
            "api_server": sensitive_url,
        },
    )

    bundle = merge_segment_artifacts(
        result.plan,
        (tampered_result,) + result.segment_results[1:],
        final_decision=result.decision,
        final_reason=result.reason,
        final_role_state=result.final_role_state,
    )
    artifact_text = json.dumps(bundle.payload, sort_keys=True)

    assert bundle.redaction_status == "rejected"
    assert "/home/operator/.kube/config" not in artifact_text
    assert sensitive_url not in artifact_text
    assert bundle.payload["segment_artifacts"][0]["redaction_status"] == "rejected"


def test_merged_artifact_rejects_raw_api_server_url_payload() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    sensitive_url = "https://" + "api" + ".private" + ".cluster:6443"
    tampered_result = replace(
        result.segment_results[0],
        artifact_payload={
            **result.segment_results[0].artifact_payload,
            "api_server": sensitive_url,
        },
    )

    bundle = merge_segment_artifacts(
        result.plan,
        (tampered_result,) + result.segment_results[1:],
        final_decision=result.decision,
        final_reason=result.reason,
        final_role_state=result.final_role_state,
    )
    artifact_text = json.dumps(bundle.payload, sort_keys=True)

    assert bundle.redaction_status == "rejected"
    assert sensitive_url not in artifact_text
    assert bundle.payload["segment_artifacts"][0]["redaction_status"] == "rejected"


def test_final_pass_requires_final_proven_role_state() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    incomplete_result = replace(result.segment_results[-1], proven_final_role_state=None)

    decision, reason, _ = evaluate_certification_decision(
        result.plan,
        result.segment_results[:-1] + (incomplete_result,),
    )

    assert decision is CertificationDecision.RECOVERY_REQUIRED
    assert "final role state is not proven" in reason


def test_final_pass_requires_every_passed_segment_final_state_to_be_proven() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    missing_intermediate_state = replace(result.segment_results[1], proven_final_role_state=None)

    decision, reason, _ = evaluate_certification_decision(
        result.plan,
        (result.segment_results[0], missing_intermediate_state) + result.segment_results[2:],
    )

    assert decision is CertificationDecision.RECOVERY_REQUIRED
    assert "python-hub-a-to-hub-b" in reason
    assert "final role state is not proven" in reason


def test_final_pass_requires_every_passed_segment_final_state_to_match_expected() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    wrong_intermediate_state = replace(
        result.segment_results[1],
        proven_final_role_state=result.segment_results[0].proven_final_role_state,
    )

    decision, reason, _ = evaluate_certification_decision(
        result.plan,
        (result.segment_results[0], wrong_intermediate_state) + result.segment_results[2:],
    )

    assert decision is CertificationDecision.NO_GO
    assert "python-hub-a-to-hub-b" in reason
    assert "does not match expected final role state" in reason
