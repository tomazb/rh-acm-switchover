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
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    StableLabConfig,
)
from tests.release.lab_controller.planner import (
    CertificationDecision,
    CertificationPlan,
    build_ping_pong_plan,
    merge_segment_artifacts,
    run_certification_plan,
)
from tests.release.lab_controller.recovery import RecoveryCategory, evaluate_run_decision

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
        profile_name="lab-controller-phase5",
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


def _replace_segment(plan: CertificationPlan, index: int, segment):
    segments = list(plan.segments)
    segments[index] = segment
    return replace(plan, segments=tuple(segments))


def _run(plan: CertificationPlan):
    return run_certification_plan(
        plan,
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )


def test_full_ping_pong_pass_has_positive_recovery_metadata() -> None:
    result = _run(build_ping_pong_plan())
    summary = result.recovery_summary
    payload = result.artifact_bundle.payload

    assert summary.final_decision is CertificationDecision.PASS
    assert summary.safe_to_continue is True
    assert summary.retry_allowed is False
    assert summary.manual_recovery_required is False
    assert summary.final_state_proven is True
    assert payload["safe_to_continue"] is True
    assert payload["retry_allowed"] is False
    assert payload["manual_recovery_required"] is False
    assert payload["final_state_proven"] is True


def test_no_go_segment_records_blocking_segment_and_sanitized_reason() -> None:
    plan = build_ping_pong_plan()
    raw_url = "https://" + "api" + ".private" + ".cluster:6443"
    failed_segment = replace(
        plan.segments[1],
        execution_result=replace(
            plan.segments[1].execution_result,
            status=ScenarioExecutionStatus.FAILED,
            failure_reason=f"activation failed against {raw_url}",
        ),
    )

    result = _run(_replace_segment(plan, 1, failed_segment))

    assert result.recovery_summary.final_decision is CertificationDecision.NO_GO
    assert result.recovery_summary.safe_to_continue is False
    assert result.recovery_summary.first_blocking_segment_id == "python-hub-a-to-hub-b"
    assert result.recovery_summary.first_blocking_scenario_id == "python-passive-switchover"
    assert raw_url not in result.recovery_summary.first_blocking_reason
    assert "[REDACTED]" in result.recovery_summary.first_blocking_reason


def test_recovery_required_after_mutation_without_proven_final_state_disables_retry() -> None:
    plan = build_ping_pong_plan()
    missing_post = replace(
        plan.segments[1],
        execution_result=replace(plan.segments[1].execution_result, post_segment_observation=None),
        post_segment_observation=None,
    )

    result = _run(_replace_segment(plan, 1, missing_post))

    assert result.recovery_summary.final_decision is CertificationDecision.RECOVERY_REQUIRED
    assert result.recovery_summary.retry_allowed is False
    assert result.recovery_summary.manual_recovery_required is True
    assert result.recovery_summary.mutation_attempted_before_block is True


def test_infra_retryable_before_mutation_allows_focused_retry_only() -> None:
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

    result = _run(_replace_segment(plan, 0, retryable))

    assert result.recovery_summary.final_decision is CertificationDecision.INFRA_RETRYABLE
    assert result.recovery_summary.safe_to_continue is False
    assert result.recovery_summary.retry_allowed is True
    assert result.recovery_summary.manual_recovery_required is False
    assert result.recovery_summary.mutation_attempted_before_block is False
    assert result.recovery_summary.initial_state_proven is True


def test_infra_retryable_is_downgraded_when_initial_state_is_not_proven() -> None:
    result = _run(build_ping_pong_plan())
    retryable = replace(
        result.segment_results[0],
        decision=CertificationDecision.INFRA_RETRYABLE,
        controller_result=replace(
            result.segment_results[0].controller_result,
            observed_initial_role_state=ObservedRoleState(None, None, "initial state was not proven"),
            execution_result=replace(
                result.segment_results[0].controller_result.execution_result,
                status=ScenarioExecutionStatus.FAILED,
                retryable_infra_failure=True,
                mutation_attempted=False,
            ),
        ),
    )

    summary = evaluate_run_decision(result.plan, (retryable,), artifact_redaction_passed=True)

    assert summary.final_decision is CertificationDecision.RECOVERY_REQUIRED
    assert summary.retry_allowed is False
    assert summary.manual_recovery_required is True


def test_infra_retryable_is_downgraded_when_mutation_was_attempted() -> None:
    result = _run(build_ping_pong_plan())
    retryable = replace(
        result.segment_results[1],
        decision=CertificationDecision.INFRA_RETRYABLE,
        controller_result=replace(
            result.segment_results[1].controller_result,
            execution_result=replace(
                result.segment_results[1].controller_result.execution_result,
                status=ScenarioExecutionStatus.FAILED,
                retryable_infra_failure=True,
                mutation_attempted=True,
                mutation_completed=False,
            ),
        ),
    )

    summary = evaluate_run_decision(
        result.plan, result.segment_results[:1] + (retryable,), artifact_redaction_passed=True
    )

    assert summary.final_decision is CertificationDecision.RECOVERY_REQUIRED
    assert summary.retry_allowed is False
    assert summary.manual_recovery_required is True
    assert summary.mutation_attempted_before_block is True


def test_blocked_plan_config_issue_is_not_retryable() -> None:
    result = _run(CertificationPlan(plan_id="empty", segments=()))

    assert result.recovery_summary.final_decision is CertificationDecision.BLOCKED
    assert result.recovery_summary.safe_to_continue is False
    assert result.recovery_summary.retry_allowed is False
    assert result.recovery_summary.manual_recovery_required is False


def test_wrong_handoff_state_blocks_without_retry() -> None:
    plan = build_ping_pong_plan()
    stale_verification = replace(plan.segments[2], pre_segment_observation=_observation(PhysicalHubLabel.HUB_A))

    result = _run(_replace_segment(plan, 2, stale_verification))

    assert result.recovery_summary.final_decision is CertificationDecision.BLOCKED
    assert result.recovery_summary.retry_allowed is False
    assert result.recovery_summary.first_blocking_segment_id == "verify-hub-b"


def test_missing_expected_final_role_state_prevents_pass() -> None:
    plan = build_ping_pong_plan()
    malformed = replace(plan.segments[-1], expected_final_role_state=None)

    result = _run(_replace_segment(plan, -1, malformed))

    assert result.recovery_summary.final_decision is CertificationDecision.BLOCKED
    assert result.recovery_summary.safe_to_continue is False
    assert "expected final role state" in result.recovery_summary.first_blocking_reason


def test_earlier_passed_segment_with_mismatched_final_role_prevents_run_pass() -> None:
    result = _run(build_ping_pong_plan())
    wrong_intermediate_state = replace(
        result.segment_results[1],
        proven_final_role_state=result.segment_results[0].proven_final_role_state,
    )

    summary = evaluate_run_decision(
        result.plan,
        (result.segment_results[0], wrong_intermediate_state) + result.segment_results[2:],
        artifact_redaction_passed=True,
    )

    assert summary.final_decision is CertificationDecision.NO_GO
    assert summary.first_blocking_segment_id == "python-hub-a-to-hub-b"
    assert summary.safe_to_continue is False


def test_both_hubs_active_is_fail_closed_with_manual_recovery() -> None:
    plan = build_ping_pong_plan()
    ambiguous = replace(plan.segments[0], pre_segment_observation=_both_active_observation())

    result = _run(_replace_segment(plan, 0, ambiguous))

    assert result.recovery_summary.final_decision is CertificationDecision.NO_GO
    assert result.recovery_summary.recovery_category is RecoveryCategory.MANUAL_RECOVERY_REQUIRED
    assert result.recovery_summary.manual_recovery_required is True


def test_neither_hub_active_requires_manual_recovery() -> None:
    plan = build_ping_pong_plan()
    neither = replace(plan.segments[0], pre_segment_observation=_observation(None))

    result = _run(_replace_segment(plan, 0, neither))

    assert result.recovery_summary.final_decision is CertificationDecision.RECOVERY_REQUIRED
    assert result.recovery_summary.manual_recovery_required is True
    assert result.recovery_summary.retry_allowed is False


def test_managed_cluster_drift_blocks_run_and_requires_recovery() -> None:
    plan = build_ping_pong_plan()
    drift = replace(plan.segments[0], pre_segment_observation=_managed_cluster_drift_observation())

    result = _run(_replace_segment(plan, 0, drift))

    assert result.recovery_summary.final_decision is CertificationDecision.RECOVERY_REQUIRED
    assert result.recovery_summary.manual_recovery_required is True
    assert "managed cluster set" in result.recovery_summary.first_blocking_reason


def test_destructive_scenario_in_normal_plan_is_blocked() -> None:
    plan = build_ping_pong_plan()
    destructive = replace(
        plan.segments[0],
        scenario_id="decommission",
        mutates_lab=True,
        execution_result=replace(plan.segments[0].execution_result, scenario_id="decommission"),
    )

    result = _run(_replace_segment(plan, 0, destructive))

    assert result.recovery_summary.final_decision is CertificationDecision.BLOCKED
    assert result.recovery_summary.recovery_category is RecoveryCategory.PLAN_INVALID
    assert "destructive/disposable-lab-only" in result.recovery_summary.first_blocking_reason


def test_unknown_scenario_in_plan_is_blocked() -> None:
    plan = build_ping_pong_plan()
    unknown = replace(
        plan.segments[0],
        scenario_id="future-certification-scenario",
        execution_result=replace(plan.segments[0].execution_result, scenario_id="future-certification-scenario"),
    )

    result = _run(_replace_segment(plan, 0, unknown))

    assert result.recovery_summary.final_decision is CertificationDecision.BLOCKED
    assert result.recovery_summary.recovery_category is RecoveryCategory.PLAN_INVALID
    assert "unknown release scenario" in result.recovery_summary.first_blocking_reason


def test_nested_sensitive_payload_in_segment_artifact_is_rejected_and_sanitized() -> None:
    result = _run(build_ping_pong_plan())
    raw_path = "/home/operator/.kube/config"
    tampered_result = replace(
        result.segment_results[0],
        artifact_payload={
            **result.segment_results[0].artifact_payload,
            "nested": {"debug": {"kubeconfig_path": raw_path}},
        },
    )

    bundle = merge_segment_artifacts(
        result.plan,
        (tampered_result,) + result.segment_results[1:],
        run_decision=result.recovery_summary,
        final_role_state=result.final_role_state,
    )
    artifact_text = json.dumps(bundle.payload, sort_keys=True)

    assert bundle.redaction_status == "rejected"
    assert raw_path not in artifact_text
    assert bundle.payload["segment_artifacts"][0]["redaction_status"] == "rejected"


def test_raw_api_server_url_in_first_blocking_reason_is_sanitized() -> None:
    plan = build_ping_pong_plan()
    raw_url = "https://" + "api" + ".private" + ".cluster:6443"
    failed_segment = replace(
        plan.segments[0],
        execution_result=replace(
            plan.segments[0].execution_result,
            status=ScenarioExecutionStatus.FAILED,
            failure_reason=f"preflight failed against {raw_url}",
        ),
    )

    result = _run(_replace_segment(plan, 0, failed_segment))
    artifact_text = json.dumps(result.artifact_bundle.payload, sort_keys=True)

    assert raw_url not in result.artifact_bundle.payload["first_blocking_reason"]
    assert raw_url not in artifact_text
    assert "[REDACTED]" in result.artifact_bundle.payload["first_blocking_reason"]


def test_raw_kubeconfig_path_in_operator_hint_is_sanitized() -> None:
    result = _run(build_ping_pong_plan())
    raw_path = "/home/operator/.kube/config"
    tampered_result = replace(
        result.segment_results[0],
        decision=CertificationDecision.RECOVERY_REQUIRED,
        recovery_hint=f"inspect {raw_path}",
        reason="manual state proof required",
    )
    summary = evaluate_run_decision(result.plan, (tampered_result,), artifact_redaction_passed=True)
    bundle = merge_segment_artifacts(
        result.plan,
        (tampered_result,),
        run_decision=summary,
        final_role_state=result.final_role_state,
    )

    assert raw_path not in bundle.payload["operator_action_hint"]
    assert "[REDACTED]" in bundle.payload["operator_action_hint"]


def test_runtime_parity_placeholder_remains_non_authoritative() -> None:
    result = _run(build_ping_pong_plan())

    assert result.artifact_bundle.payload["runtime_parity"] == {
        "status": "not_implemented",
        "authoritative": False,
        "phase": "Phase 5 deterministic planner placeholder",
    }


def test_summary_counts_cover_all_final_decisions() -> None:
    pass_result = _run(build_ping_pong_plan())

    no_go_plan = build_ping_pong_plan()
    no_go_result = _run(
        _replace_segment(
            no_go_plan,
            0,
            replace(
                no_go_plan.segments[0],
                execution_result=replace(
                    no_go_plan.segments[0].execution_result,
                    status=ScenarioExecutionStatus.FAILED,
                    failure_reason="preflight failed",
                ),
            ),
        )
    )

    recovery_plan = build_ping_pong_plan()
    recovery_result = _run(
        _replace_segment(
            recovery_plan, 0, replace(recovery_plan.segments[0], pre_segment_observation=_observation(None))
        )
    )

    retry_plan = build_ping_pong_plan()
    retry_result = _run(
        _replace_segment(
            retry_plan,
            0,
            replace(
                retry_plan.segments[0],
                execution_result=replace(
                    retry_plan.segments[0].execution_result,
                    status=ScenarioExecutionStatus.FAILED,
                    failure_reason="temporary artifact store outage",
                    retryable_infra_failure=True,
                ),
            ),
        )
    )

    blocked_result = _run(CertificationPlan(plan_id="empty", segments=()))

    assert pass_result.artifact_bundle.payload["summary_counts"]["pass"] == 5
    assert no_go_result.artifact_bundle.payload["summary_counts"]["no_go"] == 1
    assert recovery_result.artifact_bundle.payload["summary_counts"]["recovery_required"] == 1
    assert retry_result.artifact_bundle.payload["summary_counts"]["infra_retryable"] == 1
    assert blocked_result.artifact_bundle.payload["summary_counts"]["blocked"] == 1
