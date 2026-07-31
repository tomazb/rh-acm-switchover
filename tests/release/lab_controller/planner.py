from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .artifacts import sanitize_artifact_payload, sanitize_artifact_text, validate_artifact_payload_redacted
from .controller import (
    FakeScenarioExecutor,
    ScenarioExecutionResult,
    ScenarioExecutionStatus,
    SegmentControllerResult,
    run_segment,
)
from .decisions import scenario_segment_blocking_reason
from .execution import ExecutionBackend, ExecutionMode, ReleaseFrameworkDryRunBackend
from .models import (
    CertificationDecision,
    DesiredRoleState,
    HubIdentityEvidence,
    LabObservation,
    ObservedRoleState,
    PhysicalHubLabel,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)
from .recovery import RunRecoveryDecision, evaluate_run_decision
from .roles import infer_observed_role_state


@dataclass(frozen=True)
class PlannedSegment:
    segment_id: str
    scenario_id: str
    expected_initial_role_state: DesiredRoleState
    expected_final_role_state: DesiredRoleState
    mutates_lab: bool
    pre_segment_observation: LabObservation
    execution_result: ScenarioExecutionResult
    post_segment_observation: LabObservation | None = None

    @property
    def segment_plan(self) -> SegmentPlan:
        return SegmentPlan(
            segment_id=self.segment_id,
            scenario_id=self.scenario_id,
            expected_initial_role_state=self.expected_initial_role_state,
            expected_final_role_state=self.expected_final_role_state,
            mutates_lab=self.mutates_lab,
        )


@dataclass(frozen=True)
class CertificationPlan:
    plan_id: str
    segments: tuple[PlannedSegment, ...]


@dataclass(frozen=True)
class RoleTransition:
    segment_id: str
    scenario_id: str
    initial_primary_physical_hub: PhysicalHubLabel | None
    initial_secondary_physical_hub: PhysicalHubLabel | None
    expected_final_primary_physical_hub: PhysicalHubLabel | None
    expected_final_secondary_physical_hub: PhysicalHubLabel | None
    observed_final_primary_physical_hub: PhysicalHubLabel | None
    observed_final_secondary_physical_hub: PhysicalHubLabel | None
    mutation_attempted: bool
    mutation_completed: bool
    controller_decision: CertificationDecision

    def to_payload(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "scenario_id": self.scenario_id,
            "initial_primary_physical_hub": _label_value(self.initial_primary_physical_hub),
            "initial_secondary_physical_hub": _label_value(self.initial_secondary_physical_hub),
            "expected_final_primary_physical_hub": _label_value(self.expected_final_primary_physical_hub),
            "expected_final_secondary_physical_hub": _label_value(self.expected_final_secondary_physical_hub),
            "observed_final_primary_physical_hub": _label_value(self.observed_final_primary_physical_hub),
            "observed_final_secondary_physical_hub": _label_value(self.observed_final_secondary_physical_hub),
            "mutation_attempted": self.mutation_attempted,
            "mutation_completed": self.mutation_completed,
            "controller_decision": self.controller_decision.value,
        }


@dataclass(frozen=True)
class SegmentRunResult:
    planned_segment: PlannedSegment
    decision: CertificationDecision
    reason: str
    recovery_hint: str | None
    controller_result: SegmentControllerResult | None
    proven_final_role_state: ObservedRoleState | None
    role_transition: RoleTransition
    artifact_payload: dict[str, Any]


@dataclass(frozen=True)
class CertificationArtifactBundle:
    payload: dict[str, Any]
    redaction_status: str


@dataclass(frozen=True)
class CertificationRunResult:
    plan: CertificationPlan
    decision: CertificationDecision
    reason: str
    segment_results: tuple[SegmentRunResult, ...]
    role_transition_graph: tuple[RoleTransition, ...]
    artifact_bundle: CertificationArtifactBundle
    final_role_state: ObservedRoleState | None
    first_blocking_reason: str | None
    recovery_hint: str | None
    recovery_summary: RunRecoveryDecision


def _label_value(label: PhysicalHubLabel | None) -> str | None:
    if label is None:
        return None
    return label.value


def _role_state_payload(state: ObservedRoleState | None) -> dict[str, str | None]:
    if state is None:
        return {
            "primary_physical_hub": None,
            "secondary_physical_hub": None,
            "ambiguity_reason": None,
        }
    return {
        "primary_physical_hub": _label_value(state.primary_physical_hub),
        "secondary_physical_hub": _label_value(state.secondary_physical_hub),
        "ambiguity_reason": state.ambiguity_reason,
    }


def _desired_role_state_payload(state: DesiredRoleState | None) -> dict[str, str | None]:
    if state is None:
        return {
            "primary_physical_hub": None,
            "secondary_physical_hub": None,
        }
    return {
        "primary_physical_hub": state.primary_physical_hub.value,
        "secondary_physical_hub": state.secondary_physical_hub.value,
    }


def _expected_role_state(segment: PlannedSegment) -> DesiredRoleState | None:
    expected = getattr(segment, "expected_final_role_state", None)
    if not isinstance(expected, DesiredRoleState):
        return None
    return expected


def _certification_decision_from_segment(decision: SegmentDecision) -> CertificationDecision:
    return CertificationDecision[decision.name]


def _planned_segment_payload(segment: PlannedSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "scenario_id": segment.scenario_id,
        "mutates_lab": segment.mutates_lab,
        "expected_initial_role_state": _desired_role_state_payload(segment.expected_initial_role_state),
        "expected_final_role_state": _desired_role_state_payload(_expected_role_state(segment)),
    }


def _successful_execution_result(
    scenario_id: str,
    *,
    mutation_attempted: bool,
    mutation_completed: bool,
    post_primary: PhysicalHubLabel,
) -> ScenarioExecutionResult:
    return ScenarioExecutionResult(
        scenario_id=scenario_id,
        status=ScenarioExecutionStatus.SUCCEEDED,
        mutation_attempted=mutation_attempted,
        mutation_completed=mutation_completed,
        stdout_summary="fake scenario completed",
        stderr_summary="",
        post_segment_observation=fake_observation(post_primary),
    )


def fake_observation(primary: PhysicalHubLabel) -> LabObservation:
    from .discovery import fake_lab_observation

    return fake_lab_observation(primary_label=primary)


def _planned_segment(
    *,
    segment_id: str,
    scenario_id: str,
    initial: DesiredRoleState,
    final: DesiredRoleState,
    mutates_lab: bool,
    pre_primary: PhysicalHubLabel,
    post_primary: PhysicalHubLabel,
) -> PlannedSegment:
    execution_result = _successful_execution_result(
        scenario_id,
        mutation_attempted=mutates_lab,
        mutation_completed=mutates_lab,
        post_primary=post_primary,
    )
    return PlannedSegment(
        segment_id=segment_id,
        scenario_id=scenario_id,
        expected_initial_role_state=initial,
        expected_final_role_state=final,
        mutates_lab=mutates_lab,
        pre_segment_observation=fake_observation(pre_primary),
        execution_result=execution_result,
        post_segment_observation=fake_observation(post_primary),
    )


def build_ping_pong_plan(*, plan_id: str = "phase4-ping-pong") -> CertificationPlan:
    """Build the deterministic Phase 4 fake 2-hub ping-pong certification plan."""
    hub_a_primary = DesiredRoleState(
        primary_physical_hub=PhysicalHubLabel.HUB_A,
        secondary_physical_hub=PhysicalHubLabel.HUB_B,
    )
    hub_b_primary = DesiredRoleState(
        primary_physical_hub=PhysicalHubLabel.HUB_B,
        secondary_physical_hub=PhysicalHubLabel.HUB_A,
    )
    return CertificationPlan(
        plan_id=plan_id,
        segments=(
            _planned_segment(
                segment_id="baseline-hub-a",
                scenario_id="preflight",
                initial=hub_a_primary,
                final=hub_a_primary,
                mutates_lab=False,
                pre_primary=PhysicalHubLabel.HUB_A,
                post_primary=PhysicalHubLabel.HUB_A,
            ),
            _planned_segment(
                segment_id="python-hub-a-to-hub-b",
                scenario_id="python-passive-switchover",
                initial=hub_a_primary,
                final=hub_b_primary,
                mutates_lab=True,
                pre_primary=PhysicalHubLabel.HUB_A,
                post_primary=PhysicalHubLabel.HUB_B,
            ),
            _planned_segment(
                segment_id="verify-hub-b",
                scenario_id="final-baseline-check",
                initial=hub_b_primary,
                final=hub_b_primary,
                mutates_lab=False,
                pre_primary=PhysicalHubLabel.HUB_B,
                post_primary=PhysicalHubLabel.HUB_B,
            ),
            _planned_segment(
                segment_id="ansible-hub-b-to-hub-a",
                scenario_id="ansible-passive-switchover",
                initial=hub_b_primary,
                final=hub_a_primary,
                mutates_lab=True,
                pre_primary=PhysicalHubLabel.HUB_B,
                post_primary=PhysicalHubLabel.HUB_A,
            ),
            _planned_segment(
                segment_id="verify-hub-a",
                scenario_id="final-baseline-check",
                initial=hub_a_primary,
                final=hub_a_primary,
                mutates_lab=False,
                pre_primary=PhysicalHubLabel.HUB_A,
                post_primary=PhysicalHubLabel.HUB_A,
            ),
        ),
    )


def _plan_validation_failure(segment: PlannedSegment) -> tuple[CertificationDecision, str] | None:
    if not isinstance(getattr(segment, "expected_initial_role_state", None), DesiredRoleState):
        return (
            CertificationDecision.BLOCKED,
            f"segment {segment.segment_id} is missing expected initial role state",
        )
    if _expected_role_state(segment) is None:
        return (
            CertificationDecision.BLOCKED,
            f"segment {segment.segment_id} is missing expected final role state",
        )
    try:
        blocking_reason = scenario_segment_blocking_reason(segment.scenario_id, mutates_lab=segment.mutates_lab)
    except ValueError as exc:
        return CertificationDecision.NO_GO, str(exc)
    if blocking_reason is not None:
        return CertificationDecision.NO_GO, blocking_reason
    return None


def _transition_for_result(planned_segment: PlannedSegment, result: SegmentControllerResult) -> RoleTransition:
    execution_result = result.execution_result
    observed_initial = result.observed_initial_role_state
    observed_final = result.observed_final_role_state
    expected_final = _expected_role_state(planned_segment)
    return RoleTransition(
        segment_id=planned_segment.segment_id,
        scenario_id=planned_segment.scenario_id,
        initial_primary_physical_hub=observed_initial.primary_physical_hub,
        initial_secondary_physical_hub=observed_initial.secondary_physical_hub,
        expected_final_primary_physical_hub=expected_final.primary_physical_hub if expected_final else None,
        expected_final_secondary_physical_hub=expected_final.secondary_physical_hub if expected_final else None,
        observed_final_primary_physical_hub=observed_final.primary_physical_hub if observed_final else None,
        observed_final_secondary_physical_hub=observed_final.secondary_physical_hub if observed_final else None,
        mutation_attempted=execution_result.mutation_attempted if execution_result else False,
        mutation_completed=execution_result.mutation_completed if execution_result else False,
        controller_decision=_certification_decision_from_segment(result.decision.decision),
    )


def _blocked_transition(planned_segment: PlannedSegment, decision: CertificationDecision) -> RoleTransition:
    expected_initial = (
        planned_segment.expected_initial_role_state
        if isinstance(getattr(planned_segment, "expected_initial_role_state", None), DesiredRoleState)
        else None
    )
    expected_final = _expected_role_state(planned_segment)
    return RoleTransition(
        segment_id=planned_segment.segment_id,
        scenario_id=planned_segment.scenario_id,
        initial_primary_physical_hub=expected_initial.primary_physical_hub if expected_initial else None,
        initial_secondary_physical_hub=expected_initial.secondary_physical_hub if expected_initial else None,
        expected_final_primary_physical_hub=expected_final.primary_physical_hub if expected_final else None,
        expected_final_secondary_physical_hub=expected_final.secondary_physical_hub if expected_final else None,
        observed_final_primary_physical_hub=None,
        observed_final_secondary_physical_hub=None,
        mutation_attempted=False,
        mutation_completed=False,
        controller_decision=decision,
    )


def _blocked_segment_result(
    planned_segment: PlannedSegment,
    *,
    decision: CertificationDecision,
    reason: str,
    recovery_hint: str | None = None,
) -> SegmentRunResult:
    transition = _blocked_transition(planned_segment, decision)
    artifact = {
        "schema_version": 1,
        "segment_id": planned_segment.segment_id,
        "scenario_id": planned_segment.scenario_id,
        "controller_decision": decision.value,
        "safe_to_continue": False,
        "reason": reason,
        "recovery_hint": recovery_hint,
        "mutates_lab": planned_segment.mutates_lab,
        "redaction_status": "not_published",
    }
    return SegmentRunResult(
        planned_segment=planned_segment,
        decision=decision,
        reason=reason,
        recovery_hint=recovery_hint,
        controller_result=None,
        proven_final_role_state=None,
        role_transition=transition,
        artifact_payload=artifact,
    )


def _segment_run_result(planned_segment: PlannedSegment, result: SegmentControllerResult) -> SegmentRunResult:
    decision = _certification_decision_from_segment(result.decision.decision)
    return SegmentRunResult(
        planned_segment=planned_segment,
        decision=decision,
        reason=result.decision.reason,
        recovery_hint=result.decision.recovery_hint,
        controller_result=result,
        proven_final_role_state=result.observed_final_role_state,
        role_transition=_transition_for_result(planned_segment, result),
        artifact_payload=result.artifact_payload,
    )


def run_segment_plan(
    planned_segment: PlannedSegment,
    *,
    lab_config: StableLabConfig,
    expected_identities: Mapping[PhysicalHubLabel, HubIdentityEvidence],
    artifact_root: str | None = None,
    execution_backend: ExecutionBackend | None = None,
    execution_mode: ExecutionMode | str = ExecutionMode.FAKE,
    plan_id: str = "standalone",
) -> SegmentRunResult:
    """Run one planned segment through the Phase 3 non-live controller wrapper."""
    executor: FakeScenarioExecutor | ExecutionBackend
    if execution_backend is None:
        mode = ExecutionMode(execution_mode)
        if mode is ExecutionMode.FAKE:
            executor = FakeScenarioExecutor(planned_segment.execution_result)
        elif mode is ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN:
            executor = ReleaseFrameworkDryRunBackend(
                simulated_status=planned_segment.execution_result.status.value,
                simulated_mutation_attempted=planned_segment.execution_result.mutation_attempted,
                simulated_mutation_completed=planned_segment.execution_result.mutation_completed,
                simulated_failure_reason=planned_segment.execution_result.failure_reason,
                simulated_retryable_infra_failure=planned_segment.execution_result.retryable_infra_failure,
                simulated_stdout_summary=planned_segment.execution_result.stdout_summary,
                simulated_stderr_summary=planned_segment.execution_result.stderr_summary,
                simulated_post_segment_observation=planned_segment.execution_result.post_segment_observation,
                plan_id=plan_id,
            )
        else:
            raise ValueError("live release-framework execution is not supported in Phase 6A")
    else:
        executor = execution_backend
    result = run_segment(
        lab_config=lab_config,
        expected_identities=expected_identities,
        pre_segment_observation=planned_segment.pre_segment_observation,
        plan=planned_segment.segment_plan,
        executor=executor,
        post_segment_observation=planned_segment.post_segment_observation,
        artifact_root=artifact_root,
    )
    return _segment_run_result(planned_segment, result)


def _role_state_text(state: ObservedRoleState) -> str:
    primary = state.primary_physical_hub.value if state.primary_physical_hub else "unknown"
    secondary = state.secondary_physical_hub.value if state.secondary_physical_hub else "unknown"
    return f"primary={primary}, secondary={secondary}"


def _handoff_block(previous_result: SegmentRunResult, next_segment: PlannedSegment) -> SegmentRunResult | None:
    previous_final = previous_result.proven_final_role_state
    if previous_final is None or not previous_final.is_proven:
        return _blocked_segment_result(
            next_segment,
            decision=CertificationDecision.RECOVERY_REQUIRED,
            reason=(
                f"previous segment {previous_result.planned_segment.segment_id} ended without a proven final role "
                "state"
            ),
            recovery_hint="rediscover the lab and prove a safe starting state before continuing",
        )
    if not previous_final.matches_desired(previous_result.planned_segment.expected_final_role_state):
        return _blocked_segment_result(
            next_segment,
            decision=CertificationDecision.NO_GO,
            reason=(
                f"previous segment {previous_result.planned_segment.segment_id} final role state "
                f"{_role_state_text(previous_final)} does not match expected final role state"
            ),
        )
    if not previous_final.matches_desired(next_segment.expected_initial_role_state):
        return _blocked_segment_result(
            next_segment,
            decision=CertificationDecision.BLOCKED,
            reason=(
                f"segment {next_segment.segment_id} expected initial role state does not match proven final role "
                f"state from {previous_result.planned_segment.segment_id}"
            ),
        )

    observed_next_state, observed_next_decision = infer_observed_role_state(next_segment.pre_segment_observation)
    if observed_next_decision.decision is not SegmentDecision.PASS:
        return None
    if not observed_next_state.matches_desired(next_segment.expected_initial_role_state):
        return _blocked_segment_result(
            next_segment,
            decision=CertificationDecision.NO_GO,
            reason=(
                f"stale handoff observation for segment {next_segment.segment_id}: observed "
                f"{_role_state_text(observed_next_state)} does not match proven final role state from "
                f"{previous_result.planned_segment.segment_id}"
            ),
        )
    return None


def _first_blocking_result(results: Sequence[SegmentRunResult]) -> SegmentRunResult | None:
    return next((result for result in results if result.decision is not CertificationDecision.PASS), None)


def _result_count(results: Sequence[SegmentRunResult], decision: CertificationDecision) -> int:
    return sum(1 for result in results if result.decision is decision)


def evaluate_certification_decision(
    plan: CertificationPlan,
    segment_results: Sequence[SegmentRunResult],
) -> tuple[CertificationDecision, str, str | None]:
    """Evaluate the run-level decision from ordered segment results."""
    summary = evaluate_run_decision(plan, segment_results, artifact_redaction_passed=True)
    return summary.final_decision, summary.reason, summary.operator_action_hint


def _copy_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(payload), sort_keys=True))


def _safe_segment_artifact(result: SegmentRunResult) -> tuple[dict[str, Any], bool]:
    try:
        validate_artifact_payload_redacted(result.artifact_payload)
        return _copy_payload(result.artifact_payload), True
    except ValueError:
        return (
            {
                "schema_version": 1,
                "segment_id": result.planned_segment.segment_id,
                "scenario_id": result.planned_segment.scenario_id,
                "controller_decision": result.decision.value,
                "reason": "segment artifact rejected by run-level redaction",
                "redaction_status": "rejected",
            },
            False,
        )


def _execution_backends_payload(segment_results: Sequence[SegmentRunResult]) -> dict[str, Any]:
    per_segment = []
    backend_counts: dict[str, int] = {}
    dry_run_segments = 0
    real_execution_evidence_exists = False
    live_certification_evidence_exists = False

    for result in segment_results:
        artifact = result.artifact_payload
        backend_kind = str(artifact.get("execution_backend") or "not_executed")
        execution_mode = str(artifact.get("execution_mode") or "not_executed")
        dry_run = bool(artifact.get("dry_run"))
        real_execution_evidence = bool(artifact.get("real_execution_evidence"))
        live_certification_evidence = bool(artifact.get("live_certification_evidence"))
        materialization_status = str(artifact.get("materialization_status") or "not_materialized")
        future_execution_eligibility = artifact.get("future_execution_eligibility") or {}
        future_execution_eligible = (
            bool(future_execution_eligibility.get("eligible"))
            if isinstance(future_execution_eligibility, dict)
            else False
        )

        backend_counts[backend_kind] = backend_counts.get(backend_kind, 0) + 1
        if dry_run:
            dry_run_segments += 1
        real_execution_evidence_exists = real_execution_evidence_exists or real_execution_evidence
        live_certification_evidence_exists = live_certification_evidence_exists or live_certification_evidence
        per_segment.append(
            {
                "segment_id": result.planned_segment.segment_id,
                "scenario_id": result.planned_segment.scenario_id,
                "backend_kind": backend_kind,
                "execution_mode": execution_mode,
                "dry_run": dry_run,
                "materialization_status": materialization_status,
                "future_execution_eligible": future_execution_eligible,
                "real_execution_evidence": real_execution_evidence,
                "live_certification_evidence": live_certification_evidence,
            }
        )

    return {
        "per_segment": per_segment,
        "backend_counts": dict(sorted(backend_counts.items())),
        "dry_run_segments": dry_run_segments,
        "total_segments_by_backend_kind": dict(sorted(backend_counts.items())),
        "real_execution_evidence_exists": real_execution_evidence_exists,
        "live_certification_evidence_exists": live_certification_evidence_exists,
    }


def _materialized_release_framework_payload(segment_results: Sequence[SegmentRunResult]) -> dict[str, Any]:
    materialized_segments = []
    not_materialized_segments = []
    blockers = []
    real_execution_evidence_exists = False
    live_certification_evidence_exists = False

    for result in segment_results:
        artifact = result.artifact_payload
        real_execution_evidence_exists = real_execution_evidence_exists or bool(artifact.get("real_execution_evidence"))
        live_certification_evidence_exists = live_certification_evidence_exists or bool(
            artifact.get("live_certification_evidence")
        )
        materialization_status = artifact.get("materialization_status")
        if materialization_status == "materialized":
            eligibility = artifact.get("future_execution_eligibility") or {}
            blocking_fields = eligibility.get("blocking_fields", []) if isinstance(eligibility, dict) else []
            if isinstance(blocking_fields, list) and blocking_fields:
                blockers.append(
                    {
                        "segment_id": result.planned_segment.segment_id,
                        "scenario_id": result.planned_segment.scenario_id,
                        "blocking_fields": blocking_fields,
                        "reason": sanitize_artifact_text(
                            str(eligibility.get("reason", "materialization blocker"))
                            if isinstance(eligibility, dict)
                            else "materialization blocker"
                        ),
                    }
                )
            materialized_segments.append(
                {
                    "segment_id": result.planned_segment.segment_id,
                    "scenario_id": result.planned_segment.scenario_id,
                    "future_execution_eligible": (
                        bool(eligibility.get("eligible")) if isinstance(eligibility, dict) else False
                    ),
                }
            )
        else:
            not_materialized_segments.append(
                {
                    "segment_id": result.planned_segment.segment_id,
                    "scenario_id": result.planned_segment.scenario_id,
                    "reason": sanitize_artifact_text(
                        str(artifact.get("reason") or "segment did not use release-framework dry-run materialization")
                    ),
                }
            )

    return {
        "materialized_segments": len(materialized_segments),
        "materialized_segment_refs": materialized_segments,
        "not_materialized_segments": not_materialized_segments,
        "materialization_blockers": blockers,
        "real_execution_evidence_exists": real_execution_evidence_exists,
        "live_certification_evidence_exists": live_certification_evidence_exists,
    }


def _execution_harness_payload(segment_results: Sequence[SegmentRunResult]) -> dict[str, Any]:
    executed_segments = []
    non_executed_segments = []
    blocked_execution_segments = []
    gate_blockers = []
    any_real_execution_evidence = False
    any_live_certification_evidence = False

    for result in segment_results:
        artifact = result.artifact_payload
        executed = bool(artifact.get("executed"))
        real_execution_evidence = bool(artifact.get("real_execution_evidence"))
        live_certification_evidence = bool(artifact.get("live_certification_evidence"))
        execution_gate = artifact.get("execution_gate") or {}
        gate_allowed = bool(execution_gate.get("allowed")) if isinstance(execution_gate, dict) else False
        gate_blocking_fields = execution_gate.get("blocking_fields", []) if isinstance(execution_gate, dict) else []
        ref = {
            "segment_id": result.planned_segment.segment_id,
            "scenario_id": result.planned_segment.scenario_id,
            "execution_mode": str(artifact.get("execution_mode") or "not_executed"),
            "execution_evidence_type": str(artifact.get("execution_evidence_type") or "none"),
            "command_runner_kind": str(artifact.get("command_runner_kind") or "none"),
        }
        if executed:
            executed_segments.append(ref)
        else:
            non_executed_segments.append(ref)
        if isinstance(gate_blocking_fields, list) and gate_blocking_fields:
            blocked_ref = {
                "segment_id": result.planned_segment.segment_id,
                "scenario_id": result.planned_segment.scenario_id,
                "blocking_fields": gate_blocking_fields,
                "reason": sanitize_artifact_text(
                    str(execution_gate.get("reason", "execution gate blocked"))
                    if isinstance(execution_gate, dict)
                    else "execution gate blocked"
                ),
            }
            blocked_execution_segments.append(blocked_ref)
            gate_blockers.append(blocked_ref)
        elif isinstance(execution_gate, dict) and execution_gate and not gate_allowed and not executed:
            blocked_execution_segments.append(
                {
                    "segment_id": result.planned_segment.segment_id,
                    "scenario_id": result.planned_segment.scenario_id,
                    "blocking_fields": [],
                    "reason": sanitize_artifact_text(str(execution_gate.get("reason", "execution not allowed"))),
                }
            )
        any_real_execution_evidence = any_real_execution_evidence or real_execution_evidence
        any_live_certification_evidence = any_live_certification_evidence or live_certification_evidence

    return {
        "executed_segments": len(executed_segments),
        "executed_segment_refs": executed_segments,
        "non_executed_segments": len(non_executed_segments),
        "non_executed_segment_refs": non_executed_segments,
        "blocked_execution_segments": blocked_execution_segments,
        "any_real_execution_evidence": any_real_execution_evidence,
        "any_live_certification_evidence": any_live_certification_evidence,
        "execution_gate_blockers": gate_blockers,
    }


def _gitops_evidence_payload(segment_results: Sequence[SegmentRunResult]) -> dict[str, Any]:
    summaries = [
        result.artifact_payload.get("gitops_evidence")
        for result in segment_results
        if isinstance(result.artifact_payload.get("gitops_evidence"), dict)
    ]
    evaluated = any(bool(summary.get("evaluated", False)) for summary in summaries)
    first_blocking = next((summary for summary in summaries if summary.get("blocking_reason")), None)
    first_evaluated = next((summary for summary in summaries if summary.get("evaluated")), None)
    selected = first_blocking or first_evaluated or (summaries[0] if summaries else {})
    final_decision = "NOT_EVALUATED"
    if summaries:
        if first_blocking:
            final_decision = str(first_blocking.get("final_decision") or "NO_GO")
        elif evaluated:
            final_decision = "PASS"
    tracked_refs: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for summary in summaries:
        tracked = summary.get("tracked_acm_resources", [])
        if not isinstance(tracked, list):
            continue
        for item in tracked:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("ref") or "")
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            tracked_refs.append(dict(item))
    return sanitize_artifact_payload(
        {
            "evaluated": evaluated,
            "evaluated_segments": sum(1 for summary in summaries if summary.get("evaluated")),
            "tracked_acm_resources": tracked_refs,
            "application_evidence": (
                list(selected.get("application_evidence", []))
                if isinstance(selected.get("application_evidence", []), list)
                else []
            ),
            "application_set_evidence": (
                list(selected.get("application_set_evidence", []))
                if isinstance(selected.get("application_set_evidence", []), list)
                else []
            ),
            "sync_policy_classification": selected.get("sync_policy_classification", {}),
            "automated_enabled_capability": selected.get("automated_enabled_capability", {}),
            "coordination_strategy": selected.get("coordination_strategy"),
            "final_decision": final_decision,
            "blocking_reason": sanitize_artifact_text(
                str(first_blocking.get("blocking_reason")) if first_blocking else None
            ),
            "live_certification_evidence": False,
            "not_live_acm_certification_evidence": True,
        }
    )


def _segment_decision_payload(result: SegmentRunResult) -> dict[str, Any]:
    return {
        "segment_id": result.planned_segment.segment_id,
        "scenario_id": result.planned_segment.scenario_id,
        "decision": result.decision.value,
        "reason": sanitize_artifact_text(result.reason),
        "recovery_hint": sanitize_artifact_text(result.recovery_hint),
        "mutation_attempted": result.role_transition.mutation_attempted,
        "mutation_completed": result.role_transition.mutation_completed,
        "final_state_proven": bool(
            result.proven_final_role_state is not None and result.proven_final_role_state.is_proven
        ),
    }


def _minimal_rejected_bundle(
    plan: CertificationPlan,
    segment_results: Sequence[SegmentRunResult],
    run_decision: RunRecoveryDecision,
) -> CertificationArtifactBundle:
    payload = {
        "artifact_version": 1,
        "schema_version": 1,
        "controller_phase": "phase5",
        "plan_id": plan.plan_id,
        "final_decision": run_decision.final_decision.value,
        "safe_to_continue": False,
        "retry_allowed": False,
        "manual_recovery_required": run_decision.manual_recovery_required,
        "first_blocking_segment": run_decision.first_blocking_segment_id,
        "first_blocking_scenario": run_decision.first_blocking_scenario_id,
        "first_blocking_reason": run_decision.first_blocking_reason or "run artifact rejected by redaction",
        "recovery_category": run_decision.recovery_category.value,
        "operator_action_hint": run_decision.operator_action_hint,
        "mutation_attempted_before_block": run_decision.mutation_attempted_before_block,
        "mutation_completed_before_block": run_decision.mutation_completed_before_block,
        "final_state_proven": run_decision.final_state_proven,
        "final_role_state": _role_state_payload(run_decision.observed_final_state),
        "ordered_segments": [_planned_segment_payload(segment) for segment in plan.segments],
        "segment_decisions": [_segment_decision_payload(result) for result in segment_results],
        "per_segment_decisions": [_segment_decision_payload(result) for result in segment_results],
        "role_transition_graph": [result.role_transition.to_payload() for result in segment_results],
        "segment_artifacts": [],
        "execution_backends": _execution_backends_payload(segment_results),
        "materialized_release_framework": _materialized_release_framework_payload(segment_results),
        "execution_harness_summary": _execution_harness_payload(segment_results),
        "gitops_evidence": _gitops_evidence_payload(segment_results),
        "final_reason": run_decision.reason,
        "recovery_hint": run_decision.operator_action_hint,
        "summary_counts": run_decision.summary_counts,
        "runtime_parity": {
            "status": "not_implemented",
            "authoritative": False,
            "phase": "Phase 5 deterministic planner placeholder",
        },
        "live_certification_evidence": False,
        "live_execution_evidence": False,
        "redaction_status": "rejected",
    }
    return CertificationArtifactBundle(
        payload=sanitize_artifact_payload(payload),
        redaction_status="rejected",
    )


def merge_segment_artifacts(
    plan: CertificationPlan,
    segment_results: Sequence[SegmentRunResult],
    *,
    final_decision: CertificationDecision | None = None,
    final_reason: str | None = None,
    final_role_state: ObservedRoleState | None,
    recovery_hint: str | None = None,
    run_decision: RunRecoveryDecision | None = None,
) -> CertificationArtifactBundle:
    """Merge segment artifacts into the deterministic Phase 5 run artifact."""
    if run_decision is None:
        run_decision = evaluate_run_decision(plan, segment_results, artifact_redaction_passed=True)
    redaction_status = "redacted"
    segment_artifacts: list[dict[str, Any]] = []
    for result in segment_results:
        segment_artifact, artifact_is_safe = _safe_segment_artifact(result)
        if not artifact_is_safe or segment_artifact.get("redaction_status") == "rejected":
            redaction_status = "rejected"
        segment_artifacts.append(segment_artifact)

    if redaction_status == "rejected" and run_decision.artifact_redaction_passed:
        run_decision = evaluate_run_decision(plan, segment_results, artifact_redaction_passed=False)
    if not run_decision.artifact_redaction_passed:
        redaction_status = "rejected"

    final_decision_value = run_decision.final_decision
    final_reason_value = sanitize_artifact_text(run_decision.reason)
    recovery_hint_value = sanitize_artifact_text(run_decision.operator_action_hint)
    segment_decisions = [_segment_decision_payload(result) for result in segment_results]
    payload = {
        "artifact_version": 1,
        "schema_version": 1,
        "controller_phase": "phase5",
        "plan_id": plan.plan_id,
        "final_decision": final_decision_value.value,
        "safe_to_continue": run_decision.safe_to_continue,
        "retry_allowed": run_decision.retry_allowed,
        "manual_recovery_required": run_decision.manual_recovery_required,
        "first_blocking_segment": run_decision.first_blocking_segment_id,
        "first_blocking_scenario": run_decision.first_blocking_scenario_id,
        "first_blocking_reason": run_decision.first_blocking_reason,
        "recovery_category": run_decision.recovery_category.value,
        "operator_action_hint": recovery_hint_value,
        "mutation_attempted_before_block": run_decision.mutation_attempted_before_block,
        "mutation_completed_before_block": run_decision.mutation_completed_before_block,
        "final_state_proven": run_decision.final_state_proven,
        "ordered_segments": [_planned_segment_payload(segment) for segment in plan.segments],
        "segment_decisions": segment_decisions,
        "per_segment_decisions": segment_decisions,
        "role_transition_graph": [result.role_transition.to_payload() for result in segment_results],
        "segment_artifacts": segment_artifacts,
        "execution_backends": _execution_backends_payload(segment_results),
        "materialized_release_framework": _materialized_release_framework_payload(segment_results),
        "execution_harness_summary": _execution_harness_payload(segment_results),
        "gitops_evidence": _gitops_evidence_payload(segment_results),
        "final_role_state": _role_state_payload(run_decision.observed_final_state),
        "final_reason": final_reason_value,
        "recovery_hint": recovery_hint_value,
        "summary_counts": run_decision.summary_counts,
        "runtime_parity": {
            "status": "not_implemented",
            "authoritative": False,
            "phase": "Phase 5 deterministic planner placeholder",
        },
        "live_certification_evidence": False,
        "live_execution_evidence": False,
        "redaction_status": redaction_status,
    }
    try:
        validate_artifact_payload_redacted(payload)
    except ValueError:
        if run_decision.artifact_redaction_passed:
            run_decision = evaluate_run_decision(plan, segment_results, artifact_redaction_passed=False)
        return _minimal_rejected_bundle(plan, segment_results, run_decision)
    return CertificationArtifactBundle(payload=payload, redaction_status=redaction_status)


def run_certification_plan(
    plan: CertificationPlan,
    *,
    lab_config: StableLabConfig,
    expected_identities: Mapping[PhysicalHubLabel, HubIdentityEvidence],
    artifact_root: str | None = None,
    execution_backend: ExecutionBackend | None = None,
    execution_mode: ExecutionMode | str = ExecutionMode.FAKE,
) -> CertificationRunResult:
    """Run a deterministic multi-segment certification plan without live execution."""
    segment_results: list[SegmentRunResult] = []
    previous_result: SegmentRunResult | None = None

    for planned_segment in plan.segments:
        validation_failure = _plan_validation_failure(planned_segment)
        if validation_failure is not None:
            validation_decision, validation_reason = validation_failure
            segment_results.append(
                _blocked_segment_result(
                    planned_segment,
                    decision=validation_decision,
                    reason=validation_reason,
                )
            )
            break

        if previous_result is not None:
            handoff_block = _handoff_block(previous_result, planned_segment)
            if handoff_block is not None:
                segment_results.append(handoff_block)
                break

        result = run_segment_plan(
            planned_segment,
            lab_config=lab_config,
            expected_identities=expected_identities,
            artifact_root=artifact_root,
            execution_backend=execution_backend,
            execution_mode=execution_mode,
            plan_id=plan.plan_id,
        )
        segment_results.append(result)
        if result.decision is not CertificationDecision.PASS:
            break
        previous_result = result

    final_role_state = segment_results[-1].proven_final_role_state if segment_results else None
    recovery_summary = evaluate_run_decision(plan, tuple(segment_results), artifact_redaction_passed=True)
    artifact_bundle = merge_segment_artifacts(
        plan,
        tuple(segment_results),
        run_decision=recovery_summary,
        final_role_state=final_role_state,
    )
    if artifact_bundle.redaction_status == "rejected":
        recovery_summary = evaluate_run_decision(plan, tuple(segment_results), artifact_redaction_passed=False)
        artifact_bundle = merge_segment_artifacts(
            plan,
            tuple(segment_results),
            run_decision=recovery_summary,
            final_role_state=final_role_state,
        )

    return CertificationRunResult(
        plan=plan,
        decision=recovery_summary.final_decision,
        reason=recovery_summary.reason,
        segment_results=tuple(segment_results),
        role_transition_graph=tuple(result.role_transition for result in segment_results),
        artifact_bundle=artifact_bundle,
        final_role_state=final_role_state,
        first_blocking_reason=recovery_summary.first_blocking_reason,
        recovery_hint=recovery_summary.operator_action_hint,
        recovery_summary=recovery_summary,
    )
