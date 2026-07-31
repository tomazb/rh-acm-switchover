from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.release.lab_controller.controller import ScenarioExecutionStatus, run_segment
from tests.release.lab_controller.discovery import fake_lab_observation
from tests.release.lab_controller.execution import (
    ExecutionMode,
    ReleaseFrameworkDryRunBackend,
    ReleaseFrameworkLocalBackend,
    build_release_framework_request,
)
from tests.release.lab_controller.harness import (
    CommandRunResult,
    FakeCommandRunner,
    ReleaseFrameworkExecutionHarness,
    evaluate_execution_gates,
    execute_materialized_invocation,
    summarize_execution_evidence,
)
from tests.release.lab_controller.invocation import materialize_release_framework_request
from tests.release.lab_controller.models import (
    DesiredRoleState,
    GeneratedProfile,
    HubIdentityEvidence,
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)
from tests.release.lab_controller.planner import (
    CertificationDecision,
    build_ping_pong_plan,
    run_certification_plan,
)
from tests.release.lab_controller.profiles import build_role_aware_profile, redact_generated_profile_metadata

EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")
PLAN_ID = "phase6c-plan"
ARTIFACT_ROOT = "artifacts/release-lab/unit"


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
        enabled_streams=("bash", "python", "ansible"),
        scenario_ids=("preflight",),
        profile_name="lab-controller-phase6c",
        artifact_root=ARTIFACT_ROOT,
    )


def _expected_identities(config: StableLabConfig | None = None) -> dict[PhysicalHubLabel, HubIdentityEvidence]:
    config = config or _lab_config()
    return {
        label: hub.expected_identity for label, hub in config.physical_hubs.items() if hub.expected_identity is not None
    }


def _desired(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> DesiredRoleState:
    return DesiredRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


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
        expected_final_role_state=_desired(final_primary or initial_primary, final_secondary or initial_secondary),
        mutates_lab=mutates_lab,
    )


def _profile_for_plan(plan: SegmentPlan, *, config: StableLabConfig | None = None) -> GeneratedProfile:
    config = config or _lab_config()
    return build_role_aware_profile(
        config,
        ObservedRoleState(
            primary_physical_hub=plan.expected_initial_role_state.primary_physical_hub,
            secondary_physical_hub=plan.expected_initial_role_state.secondary_physical_hub,
        ),
        segment_plan=plan,
    )


def _request_for_plan(
    plan: SegmentPlan,
    *,
    generated_profile: GeneratedProfile | None = None,
    artifact_root: str = ARTIFACT_ROOT,
    explicit_env: dict[str, str] | None = None,
):
    generated = generated_profile or _profile_for_plan(plan)
    return build_release_framework_request(
        plan=plan,
        generated_profile=generated,
        generated_profile_metadata=redact_generated_profile_metadata(generated),
        artifact_root=artifact_root,
        plan_id=PLAN_ID,
        explicit_env=explicit_env,
    )


def _materialized(
    plan: SegmentPlan,
    *,
    generated_profile: GeneratedProfile | None = None,
    artifact_root: str = ARTIFACT_ROOT,
    explicit_env: dict[str, str] | None = None,
):
    generated = generated_profile or _profile_for_plan(plan)
    request = _request_for_plan(
        plan,
        generated_profile=generated,
        artifact_root=artifact_root,
        explicit_env=explicit_env,
    )
    return materialize_release_framework_request(
        request=request,
        generated_profile=generated,
        plan_id=PLAN_ID,
        artifact_root=artifact_root,
        explicit_env=explicit_env,
    )


def _summary_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


def _successful_runner() -> FakeCommandRunner:
    return FakeCommandRunner(
        results=(
            CommandRunResult(
                return_code=0,
                stdout="passed with /home/operator/.kube/config hidden",
                stderr="",
                timed_out=False,
            ),
        )
    )


def test_fake_backend_remains_default_and_existing_planner_pass_behavior_is_unchanged() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert result.decision is CertificationDecision.PASS
    assert result.artifact_bundle.payload["execution_backends"]["backend_counts"] == {"fake": 5}
    assert result.artifact_bundle.payload["execution_harness_summary"]["executed_segments"] == 0


def test_dry_run_materialization_remains_non_executing() -> None:
    materialized = _materialized(_plan())
    runner = _successful_runner()
    harness = ReleaseFrameworkExecutionHarness(command_runner=runner)

    evidence = harness.execute(
        materialized,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
        allow_local_execution=False,
    )

    assert evidence.executed is False
    assert evidence.real_execution_evidence is False
    assert evidence.live_certification_evidence is False
    assert runner.requests == ()


def test_execution_gate_blocks_when_allow_local_execution_is_false() -> None:
    decision = evaluate_execution_gates(
        _materialized(_plan()),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=False,
    )

    assert decision.allowed is False
    assert decision.local_execution_allowed is False
    assert decision.live_execution_allowed is False
    assert "allow_local_execution" in decision.blocking_fields


def test_execution_gate_allows_local_execution_only_when_all_local_gates_pass() -> None:
    decision = evaluate_execution_gates(
        _materialized(_plan()),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is True
    assert decision.execution_mode == "release_framework_local"
    assert decision.local_execution_allowed is True
    assert decision.live_execution_allowed is False
    assert decision.real_execution_evidence_possible is True
    assert decision.live_certification_evidence is False


def test_live_execution_mode_fails_closed() -> None:
    decision = evaluate_execution_gates(
        _materialized(_plan()),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LIVE,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert decision.live_execution_allowed is False
    assert decision.live_certification_evidence is False
    assert "execution_mode" in decision.blocking_fields


def test_unsupported_live_backend_fails_closed() -> None:
    with pytest.raises(ValueError, match="live release-framework execution is not supported"):
        ReleaseFrameworkLocalBackend(
            command_runner=_successful_runner(),
            requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LIVE,
            allow_local_execution=True,
        )


def test_local_harness_rejects_unknown_scenario() -> None:
    materialized = _materialized(_plan())
    broken = replace(materialized.argv, scenario_selectors=("future-scenario",))
    decision = evaluate_execution_gates(
        replace(materialized, argv=broken),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert "scenario_id" in decision.blocking_fields


def test_local_harness_rejects_destructive_or_disposable_lab_only_scenario() -> None:
    materialized = _materialized(_plan())
    broken = replace(materialized.argv, scenario_selectors=("decommission",))
    decision = evaluate_execution_gates(
        replace(materialized, argv=broken),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert "scenario_id" in decision.blocking_fields
    assert "destructive/disposable-lab-only" in decision.reason


def test_local_harness_rejects_unsafe_env() -> None:
    decision = evaluate_execution_gates(
        _materialized(_plan(), explicit_env={"API_TOKEN": "secret-token"}),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert "environment_plan" in decision.blocking_fields


def test_local_harness_rejects_unsafe_artifact_path(tmp_path: Path) -> None:
    decision = evaluate_execution_gates(
        _materialized(_plan(), artifact_root=str(tmp_path / ".kube" / "output")),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert "artifact_directory" in decision.blocking_fields


def test_local_harness_rejects_unredacted_generated_profile_metadata() -> None:
    materialized = _materialized(_plan())
    broken = replace(
        materialized,
        profile_compatibility=replace(
            materialized.profile_compatibility,
            compatible=False,
            errors=("generated profile metadata must be redacted",),
        ),
    )

    decision = evaluate_execution_gates(
        broken,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert "profile_compatibility" in decision.blocking_fields


def test_local_harness_rejects_argv_that_does_not_match_expected_pytest_target() -> None:
    materialized = _materialized(_plan())
    broken_argv = ("python", "-m", "pytest", "tests/release/test_other.py")
    broken = replace(materialized.argv, argv=broken_argv, pytest_target="tests/release/test_other.py")

    decision = evaluate_execution_gates(
        replace(materialized, argv=broken),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert "argv" in decision.blocking_fields


def test_local_harness_rejects_unsupported_flags() -> None:
    materialized = _materialized(_plan())
    broken = replace(materialized.argv, argv=materialized.argv.argv + ("--invented-release-flag",))

    decision = evaluate_execution_gates(
        replace(materialized, argv=broken),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert decision.allowed is False
    assert "argv" in decision.blocking_fields


def test_fake_command_runner_captures_structured_argv_and_explicit_env_only() -> None:
    runner = _successful_runner()
    materialized = _materialized(_plan(), explicit_env={"SAFE_FLAG": "1"})

    execute_materialized_invocation(
        materialized,
        command_runner=runner,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
        timeout_seconds=45,
    )

    assert len(runner.requests) == 1
    assert isinstance(runner.requests[0].argv, tuple)
    assert runner.requests[0].env == {"SAFE_FLAG": "1"}
    assert runner.requests[0].timeout_seconds == 45


def test_fake_command_runner_success_produces_sanitized_execution_evidence() -> None:
    evidence = execute_materialized_invocation(
        _materialized(_plan()),
        command_runner=_successful_runner(),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )
    summary = summarize_execution_evidence(evidence)

    assert evidence.executed is True
    assert evidence.return_code == 0
    assert evidence.status == "succeeded"
    assert evidence.real_execution_evidence is True
    assert evidence.live_certification_evidence is False
    assert "/home/operator/.kube/config" not in _summary_text(summary)


def test_fake_command_runner_failure_produces_sanitized_failure_evidence() -> None:
    runner = FakeCommandRunner(
        results=(
            CommandRunResult(
                return_code=2,
                stdout="",
                stderr="failed with token=abc123 and https://api.private.cluster:6443",
            ),
        )
    )

    evidence = execute_materialized_invocation(
        _materialized(_plan()),
        command_runner=runner,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )
    summary_text = _summary_text(summarize_execution_evidence(evidence))

    assert evidence.status == "failed"
    assert evidence.return_code == 2
    assert "token=abc123" not in summary_text
    assert "https://api.private.cluster:6443" not in summary_text


def test_fake_command_runner_timeout_produces_sanitized_timeout_evidence() -> None:
    runner = FakeCommandRunner(
        results=(
            CommandRunResult(
                return_code=None,
                stdout="partial /tmp/private-output",
                stderr="timeout",
                timed_out=True,
            ),
        )
    )

    evidence = execute_materialized_invocation(
        _materialized(_plan()),
        command_runner=runner,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )
    summary = summarize_execution_evidence(evidence)

    assert evidence.status == "timeout"
    assert evidence.timeout is True
    assert evidence.retryable_infra_failure is True
    assert "/tmp/private-output" not in _summary_text(summary)


def test_stdout_and_stderr_summaries_are_sanitized_for_sensitive_values() -> None:
    raw_url = "https://" + "api" + ".private" + ".cluster:6443"
    runner = FakeCommandRunner(
        results=(
            CommandRunResult(
                return_code=1,
                stdout=f"used kubeconfig /home/operator/.kube/config and {raw_url}",
                stderr="password=secret-token",
            ),
        )
    )

    evidence = execute_materialized_invocation(
        _materialized(_plan()),
        command_runner=runner,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )
    summary_text = _summary_text(summarize_execution_evidence(evidence))

    assert "/home/operator/.kube/config" not in summary_text
    assert raw_url not in summary_text
    assert "secret-token" not in summary_text


def test_dry_run_artifacts_keep_real_execution_evidence_false() -> None:
    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=_plan(),
        executor=ReleaseFrameworkDryRunBackend(plan_id=PLAN_ID),
    )

    assert result.artifact_payload["executed"] is False
    assert result.artifact_payload["real_execution_evidence"] is False
    assert result.artifact_payload["live_certification_evidence"] is False


def test_local_execution_artifacts_mark_real_but_never_live_certification_evidence() -> None:
    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=_plan(),
        executor=ReleaseFrameworkLocalBackend(
            command_runner=_successful_runner(),
            allow_local_execution=True,
            plan_id=PLAN_ID,
        ),
    )

    assert result.decision.decision is SegmentDecision.PASS
    assert result.artifact_payload["executed"] is True
    assert result.artifact_payload["execution_evidence_type"] == "local_release_framework"
    assert result.artifact_payload["real_execution_evidence"] is True
    assert result.artifact_payload["live_certification_evidence"] is False


def test_live_certification_evidence_is_always_false_in_phase6c() -> None:
    evidence = execute_materialized_invocation(
        _materialized(_plan()),
        command_runner=_successful_runner(),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert evidence.live_certification_evidence is False
    assert summarize_execution_evidence(evidence)["live_certification_evidence"] is False


def test_planner_run_artifacts_include_execution_gate_summaries_when_harness_is_used() -> None:
    result = run_certification_plan(
        build_ping_pong_plan(plan_id=PLAN_ID),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        execution_backend=ReleaseFrameworkLocalBackend(
            command_runner=FakeCommandRunner(
                results=tuple(CommandRunResult(return_code=0, stdout="ok", stderr="") for _ in range(5))
            ),
            allow_local_execution=True,
            plan_id=PLAN_ID,
        ),
    )

    first_segment = result.segment_results[0].artifact_payload
    summary = result.artifact_bundle.payload["execution_harness_summary"]

    assert first_segment["execution_gate"]["allowed"] is True
    assert first_segment["command_runner_kind"] == "fake"
    assert summary["executed_segments"] == 5
    assert summary["non_executed_segments"] == 0
    assert summary["any_real_execution_evidence"] is True
    assert summary["any_live_certification_evidence"] is False


def test_phase5_recovery_and_final_decision_behavior_remains_unchanged_for_fake_outcomes() -> None:
    pass_result = run_certification_plan(
        build_ping_pong_plan(),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )
    failing_plan = build_ping_pong_plan()
    no_go_result = run_certification_plan(
        replace(
            failing_plan,
            segments=(
                replace(
                    failing_plan.segments[0],
                    execution_result=replace(
                        failing_plan.segments[0].execution_result,
                        status=ScenarioExecutionStatus.FAILED,
                        failure_reason="preflight failed",
                    ),
                ),
            )
            + failing_plan.segments[1:],
        ),
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
    )

    assert pass_result.decision is CertificationDecision.PASS
    assert no_go_result.decision is CertificationDecision.NO_GO


def test_local_execution_failure_before_mutation_does_not_become_post_mutation_recovery() -> None:
    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=_plan(),
        executor=ReleaseFrameworkLocalBackend(
            command_runner=FakeCommandRunner(results=(CommandRunResult(return_code=1, stdout="", stderr="failed"),)),
            allow_local_execution=True,
            plan_id=PLAN_ID,
        ),
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert result.execution_result is not None
    assert result.execution_result.mutation_attempted is False
    assert "failed before mutation" in result.decision.reason


def test_materialization_blockers_prevent_execution() -> None:
    runner = _successful_runner()
    evidence = execute_materialized_invocation(
        _materialized(_plan(), explicit_env={"API_TOKEN": "secret-token"}),
        command_runner=runner,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert evidence.executed is False
    assert evidence.status == "blocked"
    assert runner.requests == ()
    assert "environment_plan" in evidence.gate.blocking_fields


def test_known_state_profile_freshness_and_redaction_gates_cannot_be_bypassed_by_local_harness() -> None:
    plan = _plan()
    stale_profile = build_role_aware_profile(
        _lab_config(),
        ObservedRoleState(
            primary_physical_hub=PhysicalHubLabel.HUB_B,
            secondary_physical_hub=PhysicalHubLabel.HUB_A,
        ),
        segment_plan=plan,
    )
    runner = _successful_runner()

    result = run_segment(
        lab_config=_lab_config(),
        expected_identities=_expected_identities(),
        pre_segment_observation=fake_lab_observation(primary_label=PhysicalHubLabel.HUB_A),
        plan=plan,
        executor=ReleaseFrameworkLocalBackend(
            command_runner=runner,
            allow_local_execution=True,
            plan_id=PLAN_ID,
        ),
        generated_profile=stale_profile,
    )

    assert result.decision.decision is SegmentDecision.NO_GO
    assert result.execution_result is None
    assert runner.requests == ()
    assert "stale role-aware profile" in result.decision.reason


def test_command_summary_is_sanitized_and_derived_from_structured_argv(tmp_path: Path) -> None:
    materialized = _materialized(_plan(), artifact_root=str(tmp_path / ".kube" / "output"))
    evidence = execute_materialized_invocation(
        materialized,
        command_runner=_successful_runner(),
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )
    summary = summarize_execution_evidence(evidence)
    summary_text = _summary_text(summary)

    assert summary["sanitized_command_summary"]["argv"] == list(materialized.argv.to_summary()["argv"])
    assert str(tmp_path) not in summary_text
    assert "[REDACTED]" in summary_text


def test_no_test_requires_real_subprocess_execution() -> None:
    runner = _successful_runner()

    execute_materialized_invocation(
        _materialized(_plan()),
        command_runner=runner,
        requested_execution_mode=ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        allow_local_execution=True,
    )

    assert runner.kind == "fake"
    assert len(runner.requests) == 1
