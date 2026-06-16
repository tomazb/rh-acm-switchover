from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .artifacts import build_segment_artifact
from .decisions import classify_scenario as _classify_scenario
from .decisions import infra_retryable, no_go, pass_decision, recovery_required
from .discovery import managed_cluster_summary
from .identity import verify_physical_hub_identities
from .models import (
    ControllerDecision,
    DesiredRoleState,
    GeneratedProfile,
    HubIdentityEvidence,
    LabObservation,
    ObservedRoleState,
    PhysicalHubLabel,
    ScenarioClassification,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)
from .profiles import redact_generated_profile_metadata, validate_generated_profile_freshness
from .roles import infer_observed_role_state
from .segments import generate_segment_profile


class ScenarioExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ScenarioExecutionResult:
    scenario_id: str
    status: ScenarioExecutionStatus
    mutation_attempted: bool
    mutation_completed: bool
    failure_reason: str | None = None
    retryable_infra_failure: bool = False
    stdout_summary: str = ""
    stderr_summary: str = ""
    post_segment_observation: LabObservation | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ScenarioExecutionStatus.SUCCEEDED


class ScenarioExecutor(Protocol):
    def execute(self, plan: SegmentPlan, generated_profile: GeneratedProfile) -> ScenarioExecutionResult:
        """Execute one scenario through a deterministic non-live backend."""


class FakeScenarioExecutor:
    """Deterministic test-only scenario executor.

    The executor records calls and returns the result supplied by the caller. It does
    not shell out, read kubeconfigs, or invoke the pytest release framework.
    """

    def __init__(self, result: ScenarioExecutionResult) -> None:
        self.result = result
        self._executions: list[tuple[str, str]] = []

    @property
    def executions(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._executions)

    def execute(self, plan: SegmentPlan, generated_profile: GeneratedProfile) -> ScenarioExecutionResult:
        self._executions.append((plan.segment_id, generated_profile.sha256))
        return self.result


@dataclass(frozen=True)
class SegmentPlanningResult:
    decision: ControllerDecision
    identity_decision: ControllerDecision
    role_decision: ControllerDecision
    scenario_classification: ScenarioClassification | None
    observed_initial_role_state: ObservedRoleState
    generated_profile: GeneratedProfile | None
    generated_profile_metadata: dict[str, Any] | None
    generated_profile_hash: str | None
    redaction_status: str


@dataclass(frozen=True)
class SegmentVerificationResult:
    decision: ControllerDecision
    observed_final_role_state: ObservedRoleState | None


@dataclass(frozen=True)
class SegmentControllerResult:
    decision: ControllerDecision
    generated_profile: GeneratedProfile | None
    generated_profile_metadata: dict[str, Any] | None
    generated_profile_hash: str | None
    observed_initial_role_state: ObservedRoleState
    expected_initial_role_state: DesiredRoleState
    expected_final_role_state: DesiredRoleState
    observed_final_role_state: ObservedRoleState | None
    scenario_classification: ScenarioClassification | None
    execution_result: ScenarioExecutionResult | None
    artifact_payload: dict[str, Any]

    @property
    def safe_to_continue(self) -> bool:
        return self.decision.safe_to_continue


def _unproven_role_state(reason: str) -> ObservedRoleState:
    return ObservedRoleState(primary_physical_hub=None, secondary_physical_hub=None, ambiguity_reason=reason)


def _role_state_text(state: ObservedRoleState) -> str:
    primary = state.primary_physical_hub.value if state.primary_physical_hub else "unknown"
    secondary = state.secondary_physical_hub.value if state.secondary_physical_hub else "unknown"
    return f"primary={primary}, secondary={secondary}"


def _identity_summary(decision: ControllerDecision) -> dict[str, Any]:
    return {
        "decision": decision.decision.name,
        "safe_to_continue": decision.safe_to_continue,
        "reason": decision.reason,
        "physical_hubs": [PhysicalHubLabel.HUB_A.value, PhysicalHubLabel.HUB_B.value],
    }


def _execution_result_summary(
    execution_result: ScenarioExecutionResult | None,
    *,
    redact_streams: bool = False,
) -> dict[str, Any]:
    if execution_result is None:
        return {}
    stdout_summary = (
        "[REDACTED]" if redact_streams and execution_result.stdout_summary else execution_result.stdout_summary
    )
    stderr_summary = (
        "[REDACTED]" if redact_streams and execution_result.stderr_summary else execution_result.stderr_summary
    )
    failure_reason = (
        "[REDACTED]" if redact_streams and execution_result.failure_reason else execution_result.failure_reason
    )
    return {
        "scenario_id": execution_result.scenario_id,
        "status": execution_result.status.value,
        "mutation_attempted": execution_result.mutation_attempted,
        "mutation_completed": execution_result.mutation_completed,
        "retryable_infra_failure": execution_result.retryable_infra_failure,
        "failure_reason": failure_reason,
        "stdout_summary": stdout_summary,
        "stderr_summary": stderr_summary,
    }


def _classification_for_plan(plan: SegmentPlan) -> tuple[ScenarioClassification | None, ControllerDecision]:
    try:
        classification = _classify_scenario(plan.scenario_id)
    except ValueError as exc:
        return None, no_go(str(exc))
    return classification, pass_decision(f"scenario {plan.scenario_id} is classified as {classification.value}")


def _build_artifact_payload(
    *,
    plan: SegmentPlan,
    planning_result: SegmentPlanningResult,
    observed_final_role_state: ObservedRoleState | None,
    execution_result: ScenarioExecutionResult | None,
    decision: ControllerDecision,
    pre_segment_observation: LabObservation,
) -> tuple[dict[str, Any], ControllerDecision]:
    redaction_status = planning_result.redaction_status
    try:
        artifact = build_segment_artifact(
            plan=plan,
            observed_initial_role_state=planning_result.observed_initial_role_state,
            desired_initial_role_state=plan.expected_initial_role_state,
            observed_final_role_state=observed_final_role_state,
            controller_decision=decision,
            managed_cluster_summary=managed_cluster_summary(pre_segment_observation),
            generated_profile_ref=planning_result.generated_profile_metadata,
            generated_profile_hash=planning_result.generated_profile_hash,
            scenario_classification=(
                planning_result.scenario_classification.value if planning_result.scenario_classification else None
            ),
            identity_verification_summary=_identity_summary(planning_result.identity_decision),
            fake_execution_result=_execution_result_summary(execution_result),
            redaction_status=redaction_status,
        )
        return artifact, decision
    except ValueError as exc:
        redaction_decision = no_go(f"redaction failure: {exc}")
        artifact = build_segment_artifact(
            plan=plan,
            observed_initial_role_state=planning_result.observed_initial_role_state,
            desired_initial_role_state=plan.expected_initial_role_state,
            observed_final_role_state=observed_final_role_state,
            controller_decision=redaction_decision,
            managed_cluster_summary=managed_cluster_summary(pre_segment_observation),
            generated_profile_ref=None,
            generated_profile_hash=planning_result.generated_profile_hash,
            scenario_classification=(
                planning_result.scenario_classification.value if planning_result.scenario_classification else None
            ),
            identity_verification_summary=_identity_summary(planning_result.identity_decision),
            fake_execution_result=_execution_result_summary(execution_result, redact_streams=True),
            redaction_status="rejected",
        )
        return artifact, redaction_decision


def plan_segment(
    *,
    lab_config: StableLabConfig,
    expected_identities: Mapping[PhysicalHubLabel, HubIdentityEvidence],
    pre_segment_observation: LabObservation,
    plan: SegmentPlan,
    artifact_root: str | None = None,
    generated_profile: GeneratedProfile | None = None,
) -> SegmentPlanningResult:
    """Plan one known-state segment without executing live commands."""
    identity_decision = verify_physical_hub_identities(expected_identities, pre_segment_observation)
    if identity_decision.decision is not SegmentDecision.PASS:
        role_state = _unproven_role_state(identity_decision.reason)
        return SegmentPlanningResult(
            decision=identity_decision,
            identity_decision=identity_decision,
            role_decision=identity_decision,
            scenario_classification=None,
            observed_initial_role_state=role_state,
            generated_profile=None,
            generated_profile_metadata=None,
            generated_profile_hash=None,
            redaction_status="not_published",
        )

    observed_initial_role_state, role_decision = infer_observed_role_state(pre_segment_observation)
    if role_decision.decision is not SegmentDecision.PASS:
        return SegmentPlanningResult(
            decision=role_decision,
            identity_decision=identity_decision,
            role_decision=role_decision,
            scenario_classification=None,
            observed_initial_role_state=observed_initial_role_state,
            generated_profile=None,
            generated_profile_metadata=None,
            generated_profile_hash=None,
            redaction_status="not_published",
        )

    scenario_classification, classification_decision = _classification_for_plan(plan)
    if classification_decision.decision is not SegmentDecision.PASS:
        return SegmentPlanningResult(
            decision=classification_decision,
            identity_decision=identity_decision,
            role_decision=role_decision,
            scenario_classification=scenario_classification,
            observed_initial_role_state=observed_initial_role_state,
            generated_profile=None,
            generated_profile_metadata=None,
            generated_profile_hash=None,
            redaction_status="not_published",
        )

    try:
        segment_profile = generate_segment_profile(
            plan,
            lab_config=lab_config,
            observed_role_state=observed_initial_role_state,
            identity_decision=identity_decision,
            role_decision=role_decision,
            artifact_root=artifact_root,
        )
    except ValueError as exc:
        decision = no_go(str(exc))
        return SegmentPlanningResult(
            decision=decision,
            identity_decision=identity_decision,
            role_decision=role_decision,
            scenario_classification=scenario_classification,
            observed_initial_role_state=observed_initial_role_state,
            generated_profile=None,
            generated_profile_metadata=None,
            generated_profile_hash=None,
            redaction_status="not_published",
        )

    if segment_profile.decision.decision is not SegmentDecision.PASS or segment_profile.generated_profile is None:
        return SegmentPlanningResult(
            decision=segment_profile.decision,
            identity_decision=identity_decision,
            role_decision=role_decision,
            scenario_classification=scenario_classification,
            observed_initial_role_state=observed_initial_role_state,
            generated_profile=None,
            generated_profile_metadata=None,
            generated_profile_hash=None,
            redaction_status="not_published",
        )

    selected_profile = generated_profile or segment_profile.generated_profile
    try:
        freshness_decision = validate_generated_profile_freshness(
            selected_profile,
            lab_config,
            observed_initial_role_state,
        )
    except ValueError as exc:
        freshness_decision = no_go(f"stale role-aware profile: {exc}")
    if freshness_decision.decision is not SegmentDecision.PASS:
        return SegmentPlanningResult(
            decision=freshness_decision,
            identity_decision=identity_decision,
            role_decision=role_decision,
            scenario_classification=scenario_classification,
            observed_initial_role_state=observed_initial_role_state,
            generated_profile=selected_profile,
            generated_profile_metadata=None,
            generated_profile_hash=selected_profile.sha256,
            redaction_status="not_published",
        )

    try:
        redacted_profile_metadata = redact_generated_profile_metadata(selected_profile)
    except (KeyError, TypeError, ValueError) as exc:
        decision = no_go(f"redaction failure: {exc}")
        return SegmentPlanningResult(
            decision=decision,
            identity_decision=identity_decision,
            role_decision=role_decision,
            scenario_classification=scenario_classification,
            observed_initial_role_state=observed_initial_role_state,
            generated_profile=selected_profile,
            generated_profile_metadata=None,
            generated_profile_hash=selected_profile.sha256,
            redaction_status="rejected",
        )

    return SegmentPlanningResult(
        decision=segment_profile.decision,
        identity_decision=identity_decision,
        role_decision=role_decision,
        scenario_classification=scenario_classification,
        observed_initial_role_state=observed_initial_role_state,
        generated_profile=selected_profile,
        generated_profile_metadata=redacted_profile_metadata,
        generated_profile_hash=selected_profile.sha256,
        redaction_status="redacted",
    )


def _decision_for_failed_execution(
    plan: SegmentPlan,
    execution_result: ScenarioExecutionResult,
    observed_initial_role_state: ObservedRoleState,
    observed_final_role_state: ObservedRoleState | None,
    final_role_decision: ControllerDecision | None,
) -> ControllerDecision:
    reason = execution_result.failure_reason or "scenario execution failed"
    if not execution_result.mutation_attempted:
        if execution_result.retryable_infra_failure:
            if not observed_initial_role_state.is_proven:
                return recovery_required(
                    "retryable infrastructure failure occurred before mutation, but the initial role state is not "
                    f"proven: {observed_initial_role_state.ambiguity_reason or 'role state is not proven'}",
                    recovery_hint="rediscover the lab and prove a safe starting state before retrying",
                )
            if not observed_initial_role_state.matches_desired(plan.expected_initial_role_state):
                return no_go(
                    "retryable infrastructure failure occurred before mutation, but the observed initial role state "
                    f"{_role_state_text(observed_initial_role_state)} does not match the expected initial role state"
                )
            return infra_retryable(f"retryable infrastructure failure before mutation: {reason}")
        return no_go(f"scenario execution failed before mutation: {reason}")

    if observed_final_role_state is None or final_role_decision is None:
        return recovery_required(
            f"scenario execution failed after mutation attempt and post-segment role state cannot be proven: {reason}",
            recovery_hint="rediscover the lab and prove a safe starting state before retrying",
        )
    if final_role_decision.decision is not SegmentDecision.PASS:
        if final_role_decision.decision is SegmentDecision.NO_GO:
            return final_role_decision
        return recovery_required(
            f"scenario execution failed after mutation attempt and final role state is not proven: "
            f"{final_role_decision.reason}",
            recovery_hint="rediscover the lab and prove a safe starting state before retrying",
        )
    if not observed_final_role_state.matches_desired(plan.expected_final_role_state):
        return no_go(
            f"scenario execution failed after mutation and observed final role state "
            f"{_role_state_text(observed_final_role_state)} does not match expected final role state"
        )
    return no_go(f"scenario execution failed after mutation, but final role state matches expected state: {reason}")


def verify_segment_result(
    *,
    plan: SegmentPlan,
    observed_initial_role_state: ObservedRoleState,
    execution_result: ScenarioExecutionResult,
    post_segment_observation: LabObservation | None = None,
) -> SegmentVerificationResult:
    """Verify the fake scenario result and final role state for one segment."""
    if execution_result.scenario_id != plan.scenario_id:
        decision = no_go(f"fake executor returned scenario {execution_result.scenario_id}, expected {plan.scenario_id}")
        if execution_result.mutation_attempted:
            decision = recovery_required(
                f"fake executor returned scenario {execution_result.scenario_id} after mutation attempt; "
                f"expected {plan.scenario_id}",
                recovery_hint="rediscover the lab and prove a safe starting state before retrying",
            )
        return SegmentVerificationResult(decision=decision, observed_final_role_state=None)

    final_observation = post_segment_observation or execution_result.post_segment_observation
    observed_final_role_state: ObservedRoleState | None = None
    final_role_decision: ControllerDecision | None = None
    if final_observation is not None:
        observed_final_role_state, final_role_decision = infer_observed_role_state(final_observation)

    if not execution_result.succeeded:
        return SegmentVerificationResult(
            decision=_decision_for_failed_execution(
                plan,
                execution_result,
                observed_initial_role_state,
                observed_final_role_state,
                final_role_decision,
            ),
            observed_final_role_state=observed_final_role_state,
        )

    if execution_result.mutation_attempted and not plan.mutates_lab:
        if observed_final_role_state is None:
            return SegmentVerificationResult(
                decision=recovery_required(
                    "fake execution attempted mutation for a non-mutating segment and post-segment role state "
                    "cannot be proven",
                    recovery_hint="rediscover the lab before continuing",
                ),
                observed_final_role_state=None,
            )
        return SegmentVerificationResult(
            decision=no_go("fake execution attempted mutation for a non-mutating segment"),
            observed_final_role_state=observed_final_role_state,
        )

    if plan.mutates_lab and not execution_result.mutation_attempted:
        return SegmentVerificationResult(
            decision=no_go(f"mutating segment {plan.segment_id} completed without a mutation attempt"),
            observed_final_role_state=observed_final_role_state,
        )

    if final_observation is None:
        if plan.mutates_lab:
            return SegmentVerificationResult(
                decision=recovery_required(
                    f"missing post-segment observation for mutating segment {plan.segment_id}",
                    recovery_hint="rediscover the lab and prove the final role state",
                ),
                observed_final_role_state=None,
            )
        observed_final_role_state = observed_initial_role_state
        final_role_decision = pass_decision("non-mutating segment retains observed initial role state")

    if final_role_decision is None or observed_final_role_state is None:
        return SegmentVerificationResult(
            decision=recovery_required("post-segment role state cannot be proven"),
            observed_final_role_state=observed_final_role_state,
        )
    if final_role_decision.decision is not SegmentDecision.PASS:
        return SegmentVerificationResult(
            decision=final_role_decision,
            observed_final_role_state=observed_final_role_state,
        )
    if plan.mutates_lab and not execution_result.mutation_completed:
        return SegmentVerificationResult(
            decision=no_go(f"mutating segment {plan.segment_id} succeeded without mutation completion evidence"),
            observed_final_role_state=observed_final_role_state,
        )
    if not observed_final_role_state.matches_desired(plan.expected_final_role_state):
        return SegmentVerificationResult(
            decision=no_go(
                f"observed final role state {_role_state_text(observed_final_role_state)} does not match expected "
                f"final role state primary={plan.expected_final_role_state.primary_physical_hub.value}, "
                f"secondary={plan.expected_final_role_state.secondary_physical_hub.value}"
            ),
            observed_final_role_state=observed_final_role_state,
        )
    return SegmentVerificationResult(
        decision=pass_decision(f"segment {plan.segment_id} passed with expected final role state"),
        observed_final_role_state=observed_final_role_state,
    )


def run_segment(
    *,
    lab_config: StableLabConfig,
    expected_identities: Mapping[PhysicalHubLabel, HubIdentityEvidence],
    pre_segment_observation: LabObservation,
    plan: SegmentPlan,
    executor: ScenarioExecutor,
    post_segment_observation: LabObservation | None = None,
    artifact_root: str | None = None,
    generated_profile: GeneratedProfile | None = None,
) -> SegmentControllerResult:
    """Run one deterministic known-state segment end-to-end with fake execution."""
    planning_result = plan_segment(
        lab_config=lab_config,
        expected_identities=expected_identities,
        pre_segment_observation=pre_segment_observation,
        plan=plan,
        artifact_root=artifact_root,
        generated_profile=generated_profile,
    )
    if planning_result.decision.decision is not SegmentDecision.PASS or planning_result.generated_profile is None:
        artifact_payload, decision = _build_artifact_payload(
            plan=plan,
            planning_result=planning_result,
            observed_final_role_state=None,
            execution_result=None,
            decision=planning_result.decision,
            pre_segment_observation=pre_segment_observation,
        )
        return SegmentControllerResult(
            decision=decision,
            generated_profile=planning_result.generated_profile,
            generated_profile_metadata=planning_result.generated_profile_metadata,
            generated_profile_hash=planning_result.generated_profile_hash,
            observed_initial_role_state=planning_result.observed_initial_role_state,
            expected_initial_role_state=plan.expected_initial_role_state,
            expected_final_role_state=plan.expected_final_role_state,
            observed_final_role_state=None,
            scenario_classification=planning_result.scenario_classification,
            execution_result=None,
            artifact_payload=artifact_payload,
        )

    execution_result = executor.execute(plan, planning_result.generated_profile)
    verification_result = verify_segment_result(
        plan=plan,
        observed_initial_role_state=planning_result.observed_initial_role_state,
        execution_result=execution_result,
        post_segment_observation=post_segment_observation,
    )
    artifact_payload, decision = _build_artifact_payload(
        plan=plan,
        planning_result=planning_result,
        observed_final_role_state=verification_result.observed_final_role_state,
        execution_result=execution_result,
        decision=verification_result.decision,
        pre_segment_observation=pre_segment_observation,
    )
    return SegmentControllerResult(
        decision=decision,
        generated_profile=planning_result.generated_profile,
        generated_profile_metadata=planning_result.generated_profile_metadata,
        generated_profile_hash=planning_result.generated_profile_hash,
        observed_initial_role_state=planning_result.observed_initial_role_state,
        expected_initial_role_state=plan.expected_initial_role_state,
        expected_final_role_state=plan.expected_final_role_state,
        observed_final_role_state=verification_result.observed_final_role_state,
        scenario_classification=planning_result.scenario_classification,
        execution_result=execution_result,
        artifact_payload=artifact_payload,
    )
