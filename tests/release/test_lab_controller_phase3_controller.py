from __future__ import annotations

import json
from dataclasses import replace

from tests.release.lab_controller.controller import (
    FakeScenarioExecutor,
    ScenarioExecutionResult,
    ScenarioExecutionStatus,
    run_segment,
    verify_segment_result,
)
from tests.release.lab_controller.discovery import fake_lab_observation
from tests.release.lab_controller.models import (
    DesiredRoleState,
    HubIdentityEvidence,
    HubRoleSignal,
    ManagedClusterEvidence,
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)
from tests.release.lab_controller.profiles import build_role_aware_profile

EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")


def _identity(label: PhysicalHubLabel, *, uid_suffix: str | None = None) -> HubIdentityEvidence:
    suffix = uid_suffix or label.value
    return HubIdentityEvidence(
        physical_label=label,
        kube_system_uid=f"uid-{suffix}",
        api_server_fingerprint=f"api-{suffix}",
        context_name=f"{label.value}-context",
        cluster_version="4.16",
        acm_evidence=f"mch-{label.value}",
    )


def _hub_config(label: PhysicalHubLabel) -> PhysicalHubConfig:
    return PhysicalHubConfig(
        physical_label=label,
        kubeconfig_reference=f"kubeconfig-ref-{label.value}",
        context_name=f"{label.value}-context",
        expected_identity=_identity(label),
    )


def _lab_config(
    *,
    expected_clusters: tuple[str, ...] = EXPECTED_CLUSTERS,
    artifact_root: str = "artifacts/release-lab/unit",
) -> StableLabConfig:
    return StableLabConfig(
        physical_hubs={
            PhysicalHubLabel.HUB_A: _hub_config(PhysicalHubLabel.HUB_A),
            PhysicalHubLabel.HUB_B: _hub_config(PhysicalHubLabel.HUB_B),
        },
        expected_managed_cluster_names=expected_clusters,
        enabled_streams=("python", "ansible"),
        scenario_ids=("preflight",),
        profile_name="lab-controller-phase3",
        artifact_root=artifact_root,
    )


def _expected_identities(config: StableLabConfig | None = None) -> dict[PhysicalHubLabel, HubIdentityEvidence]:
    config = config or _lab_config()
    return {
        label: hub.expected_identity for label, hub in config.physical_hubs.items() if hub.expected_identity is not None
    }


def _desired(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> DesiredRoleState:
    return DesiredRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _role_state(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> ObservedRoleState:
    return ObservedRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _plan(
    *,
    segment_id: str = "segment-preflight",
    scenario_id: str = "preflight",
    initial_primary: PhysicalHubLabel = PhysicalHubLabel.HUB_A,
    initial_secondary: PhysicalHubLabel = PhysicalHubLabel.HUB_B,
    final_primary: PhysicalHubLabel | None = None,
    final_secondary: PhysicalHubLabel | None = None,
    mutates_lab: bool = False,
) -> SegmentPlan:
    return SegmentPlan(
        segment_id=segment_id,
        scenario_id=scenario_id,
        expected_initial_role_state=_desired(initial_primary, initial_secondary),
        expected_final_role_state=_desired(
            final_primary or initial_primary,
            final_secondary or initial_secondary,
        ),
        mutates_lab=mutates_lab,
    )


def _observation(primary: PhysicalHubLabel | None, expected_clusters: tuple[str, ...] = EXPECTED_CLUSTERS):
    return fake_lab_observation(primary_label=primary, expected_managed_cluster_names=expected_clusters)


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


def _neither_active_observation():
    return fake_lab_observation(primary_label=None, expected_managed_cluster_names=EXPECTED_CLUSTERS)


def _unknown_secondary_observation():
    observation = _observation(PhysicalHubLabel.HUB_A)
    return replace(
        observation,
        observations=tuple(
            replace(hub, role_signal=HubRoleSignal.UNKNOWN) if hub.physical_label is PhysicalHubLabel.HUB_B else hub
            for hub in observation.observations
        ),
    )


def _success(
    scenario_id: str,
    *,
    mutation_attempted: bool,
    mutation_completed: bool,
    post_primary: PhysicalHubLabel | None,
) -> ScenarioExecutionResult:
    return ScenarioExecutionResult(
        scenario_id=scenario_id,
        status=ScenarioExecutionStatus.SUCCEEDED,
        mutation_attempted=mutation_attempted,
        mutation_completed=mutation_completed,
        stdout_summary="scenario completed",
        stderr_summary="",
        post_segment_observation=_observation(post_primary) if post_primary is not None else None,
    )


def _run(plan: SegmentPlan, pre_primary: PhysicalHubLabel | None, result: ScenarioExecutionResult, **kwargs):
    config = kwargs.pop("lab_config", _lab_config())
    executor = kwargs.pop("executor", FakeScenarioExecutor(result))
    return run_segment(
        lab_config=config,
        expected_identities=_expected_identities(config),
        pre_segment_observation=_observation(pre_primary, config.expected_managed_cluster_names),
        plan=plan,
        executor=executor,
        artifact_root="artifacts/release-lab/unit",
        **kwargs,
    )


def test_non_mutating_segment_passes_from_hub_a_primary_proven_state() -> None:
    plan = _plan()
    result = _run(
        plan,
        PhysicalHubLabel.HUB_A,
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_A),
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.safe_to_continue is True
    assert result.observed_initial_role_state.primary_physical_hub is PhysicalHubLabel.HUB_A
    assert result.observed_final_role_state is not None
    assert result.observed_final_role_state.primary_physical_hub is PhysicalHubLabel.HUB_A
    assert result.artifact_payload["scenario_classification"] == "live-non-mutating"


def test_non_mutating_segment_passes_from_hub_b_primary_proven_state() -> None:
    plan = _plan(
        segment_id="segment-preflight-hub-b",
        initial_primary=PhysicalHubLabel.HUB_B,
        initial_secondary=PhysicalHubLabel.HUB_A,
    )
    result = _run(
        plan,
        PhysicalHubLabel.HUB_B,
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_B),
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.generated_profile is not None
    assert result.generated_profile.logical_to_physical["primary"] == "hub-b"
    assert result.artifact_payload["observed_final_role_state"]["primary_physical_hub"] == "hub-b"


def test_mutating_segment_passes_from_hub_a_primary_to_hub_b_primary() -> None:
    plan = _plan(
        segment_id="segment-python",
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )
    result = _run(
        plan,
        PhysicalHubLabel.HUB_A,
        _success(
            "python-passive-switchover",
            mutation_attempted=True,
            mutation_completed=True,
            post_primary=PhysicalHubLabel.HUB_B,
        ),
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.observed_final_role_state is not None
    assert result.observed_final_role_state.primary_physical_hub is PhysicalHubLabel.HUB_B
    assert result.artifact_payload["fake_execution_result"]["mutation_completed"] is True


def test_mutating_segment_passes_from_hub_b_primary_to_hub_a_primary() -> None:
    plan = _plan(
        segment_id="segment-ansible",
        scenario_id="ansible-passive-switchover",
        initial_primary=PhysicalHubLabel.HUB_B,
        initial_secondary=PhysicalHubLabel.HUB_A,
        final_primary=PhysicalHubLabel.HUB_A,
        final_secondary=PhysicalHubLabel.HUB_B,
        mutates_lab=True,
    )
    result = _run(
        plan,
        PhysicalHubLabel.HUB_B,
        _success(
            "ansible-passive-switchover",
            mutation_attempted=True,
            mutation_completed=True,
            post_primary=PhysicalHubLabel.HUB_A,
        ),
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.generated_profile is not None
    assert result.generated_profile.logical_to_physical["primary"] == "hub-b"
    assert result.observed_final_role_state is not None
    assert result.observed_final_role_state.primary_physical_hub is PhysicalHubLabel.HUB_A


def test_identity_failure_returns_no_go_and_does_not_execute() -> None:
    config = _lab_config()
    mismatched = {
        **_expected_identities(config),
        PhysicalHubLabel.HUB_A: _identity(PhysicalHubLabel.HUB_A, uid_suffix="hub-a-recreated"),
    }
    executor = FakeScenarioExecutor(
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_A)
    )

    result = run_segment(
        lab_config=config,
        expected_identities=mismatched,
        pre_segment_observation=_observation(PhysicalHubLabel.HUB_A),
        plan=_plan(),
        executor=executor,
        artifact_root="artifacts/release-lab/unit",
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert executor.executions == ()
    assert "identity" in result.decision.reason


def test_ambiguous_initial_role_returns_recovery_required_and_does_not_execute() -> None:
    executor = FakeScenarioExecutor(
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_A)
    )

    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=_unknown_secondary_observation(),
        plan=_plan(),
        executor=executor,
        artifact_root="artifacts/release-lab/unit",
    )

    assert result.decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert executor.executions == ()
    assert "unknown role signal" in result.decision.reason


def test_both_hubs_active_blocks_execution_with_no_go() -> None:
    executor = FakeScenarioExecutor(
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_A)
    )

    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=_both_active_observation(),
        plan=_plan(),
        executor=executor,
        artifact_root="artifacts/release-lab/unit",
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert executor.executions == ()
    assert "both hubs active" in result.decision.reason


def test_neither_hub_active_returns_recovery_required() -> None:
    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=_neither_active_observation(),
        plan=_plan(),
        executor=FakeScenarioExecutor(
            _success(
                "preflight",
                mutation_attempted=False,
                mutation_completed=False,
                post_primary=PhysicalHubLabel.HUB_A,
            )
        ),
        artifact_root="artifacts/release-lab/unit",
    )

    assert result.decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert "neither hub is active" in result.decision.reason


def test_unknown_scenario_returns_no_go_before_execution() -> None:
    executor = FakeScenarioExecutor(
        _success(
            "future-scenario",
            mutation_attempted=False,
            mutation_completed=False,
            post_primary=PhysicalHubLabel.HUB_A,
        )
    )

    result = _run(_plan(scenario_id="future-scenario"), PhysicalHubLabel.HUB_A, executor.result, executor=executor)

    assert result.decision.decision is SegmentDecision.NO_GO
    assert executor.executions == ()
    assert "unknown release scenario" in result.decision.reason


def test_stale_initial_desired_state_blocks_execution() -> None:
    executor = FakeScenarioExecutor(
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_B)
    )

    result = _run(_plan(), PhysicalHubLabel.HUB_B, executor.result, executor=executor)

    assert result.decision.decision is SegmentDecision.NO_GO
    assert executor.executions == ()
    assert "does not match expected initial role state" in result.decision.reason


def test_fake_execution_failure_before_mutation_can_return_infra_retryable() -> None:
    plan = _plan()
    result = _run(
        plan,
        PhysicalHubLabel.HUB_A,
        ScenarioExecutionResult(
            scenario_id="preflight",
            status=ScenarioExecutionStatus.FAILED,
            mutation_attempted=False,
            mutation_completed=False,
            failure_reason="temporary artifact storage outage",
            retryable_infra_failure=True,
        ),
    )

    assert result.decision.decision is SegmentDecision.INFRA_RETRYABLE
    assert result.safe_to_continue is False
    assert "temporary artifact storage outage" in result.decision.reason


def test_retryable_fake_failure_requires_proven_initial_state() -> None:
    verification = verify_segment_result(
        plan=_plan(),
        observed_initial_role_state=ObservedRoleState(None, None, "role state was not proven"),
        execution_result=ScenarioExecutionResult(
            scenario_id="preflight",
            status=ScenarioExecutionStatus.FAILED,
            mutation_attempted=False,
            mutation_completed=False,
            failure_reason="temporary artifact storage outage",
            retryable_infra_failure=True,
        ),
    )

    assert verification.decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert verification.decision.safe_to_continue is False
    assert "initial role state is not proven" in verification.decision.reason


def test_fake_execution_failure_after_mutation_without_proven_post_state_returns_recovery_required() -> None:
    plan = _plan(
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )
    result = _run(
        plan,
        PhysicalHubLabel.HUB_A,
        ScenarioExecutionResult(
            scenario_id="python-passive-switchover",
            status=ScenarioExecutionStatus.FAILED,
            mutation_attempted=True,
            mutation_completed=False,
            failure_reason="activation failed after patch attempt",
        ),
    )

    assert result.decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert "post-segment role state cannot be proven" in result.decision.reason


def test_successful_execution_with_wrong_final_role_returns_no_go() -> None:
    plan = _plan(
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )
    result = _run(
        plan,
        PhysicalHubLabel.HUB_A,
        _success(
            "python-passive-switchover",
            mutation_attempted=True,
            mutation_completed=True,
            post_primary=PhysicalHubLabel.HUB_A,
        ),
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert "does not match expected final role state" in result.decision.reason


def test_successful_mutating_execution_without_completion_evidence_returns_no_go() -> None:
    plan = _plan(
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )
    result = _run(
        plan,
        PhysicalHubLabel.HUB_A,
        _success(
            "python-passive-switchover",
            mutation_attempted=True,
            mutation_completed=False,
            post_primary=PhysicalHubLabel.HUB_B,
        ),
    )

    assert result.decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert "without mutation completion evidence" in result.decision.reason


def test_successful_mutating_execution_with_missing_post_observation_returns_recovery_required() -> None:
    plan = _plan(
        scenario_id="python-passive-switchover",
        final_primary=PhysicalHubLabel.HUB_B,
        final_secondary=PhysicalHubLabel.HUB_A,
        mutates_lab=True,
    )
    result = _run(
        plan,
        PhysicalHubLabel.HUB_A,
        ScenarioExecutionResult(
            scenario_id="python-passive-switchover",
            status=ScenarioExecutionStatus.SUCCEEDED,
            mutation_attempted=True,
            mutation_completed=True,
        ),
    )

    assert result.decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert "missing post-segment observation" in result.decision.reason


def test_stale_generated_profile_blocks_execution() -> None:
    config = _lab_config()
    stale_profile = build_role_aware_profile(
        config,
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )
    executor = FakeScenarioExecutor(
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_B)
    )
    result = run_segment(
        lab_config=config,
        expected_identities=_expected_identities(config),
        pre_segment_observation=_observation(PhysicalHubLabel.HUB_B),
        plan=_plan(
            segment_id="segment-preflight-hub-b",
            initial_primary=PhysicalHubLabel.HUB_B,
            initial_secondary=PhysicalHubLabel.HUB_A,
        ),
        executor=executor,
        artifact_root="artifacts/release-lab/unit",
        generated_profile=stale_profile,
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert executor.executions == ()
    assert "stale role-aware profile" in result.decision.reason


def test_profile_generated_for_different_managed_cluster_expectations_blocks_execution() -> None:
    stale_profile = build_role_aware_profile(
        _lab_config(),
        _role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
    )
    drifted_config = _lab_config(expected_clusters=("mc-1", "mc-2"))
    executor = FakeScenarioExecutor(
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_A)
    )

    result = run_segment(
        lab_config=drifted_config,
        expected_identities=_expected_identities(drifted_config),
        pre_segment_observation=_observation(PhysicalHubLabel.HUB_A, ("mc-1", "mc-2")),
        plan=_plan(),
        executor=executor,
        artifact_root="artifacts/release-lab/unit",
        generated_profile=stale_profile,
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert executor.executions == ()
    assert "managed cluster set" in result.decision.reason


def test_generated_profile_metadata_in_artifact_is_redacted() -> None:
    result = _run(
        _plan(),
        PhysicalHubLabel.HUB_A,
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_A),
    )
    artifact_text = json.dumps(result.artifact_payload, sort_keys=True)

    assert result.artifact_payload["generated_profile"]["redaction_status"] == "redacted"
    assert result.artifact_payload["generated_profile"]["hubs"]["primary"]["kubeconfig_reference"] == "[REDACTED]"
    assert "kubeconfig-ref-hub-a" not in artifact_text
    assert "kubeconfig-ref-hub-b" not in artifact_text


def test_raw_kubeconfig_like_value_in_execution_summary_is_sanitized() -> None:
    result = _run(
        _plan(),
        PhysicalHubLabel.HUB_A,
        ScenarioExecutionResult(
            scenario_id="preflight",
            status=ScenarioExecutionStatus.SUCCEEDED,
            mutation_attempted=False,
            mutation_completed=False,
            stdout_summary="wrote /home/operator/.kube/config",
            post_segment_observation=_observation(PhysicalHubLabel.HUB_A),
        ),
    )
    artifact_text = json.dumps(result.artifact_payload, sort_keys=True)

    assert result.decision.decision is SegmentDecision.PASS
    assert "/home/operator/.kube/config" not in artifact_text
    assert result.artifact_payload["fake_execution_result"]["stdout_summary"] == "wrote [REDACTED]"


def test_destructive_disposable_lab_only_scenario_does_not_receive_normal_pass_behavior() -> None:
    executor = FakeScenarioExecutor(
        _success("decommission", mutation_attempted=True, mutation_completed=True, post_primary=PhysicalHubLabel.HUB_A)
    )
    result = _run(
        _plan(scenario_id="decommission", mutates_lab=True),
        PhysicalHubLabel.HUB_A,
        executor.result,
        executor=executor,
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert executor.executions == ()
    assert "destructive/disposable-lab-only" in result.decision.reason


def test_segment_result_includes_structured_fields_for_future_artifact_merger() -> None:
    result = _run(
        _plan(),
        PhysicalHubLabel.HUB_A,
        _success("preflight", mutation_attempted=False, mutation_completed=False, post_primary=PhysicalHubLabel.HUB_A),
    )

    assert result.generated_profile_hash == result.artifact_payload["generated_profile_hash"]
    assert result.expected_initial_role_state.primary_physical_hub is PhysicalHubLabel.HUB_A
    assert result.expected_final_role_state.primary_physical_hub is PhysicalHubLabel.HUB_A
    assert result.artifact_payload["identity_verification_summary"]["decision"] == "PASS"
    assert result.artifact_payload["fake_execution_result"]["scenario_id"] == "preflight"
    assert result.artifact_payload["controller_decision"] == "PASS"
