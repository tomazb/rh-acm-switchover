from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .artifacts import sanitize_artifact_text, validate_artifact_payload_redacted
from .models import CertificationDecision, DesiredRoleState, ObservedRoleState


class RecoveryCategory(str, Enum):
    NONE = "none"
    MANUAL_RECOVERY_REQUIRED = "manual_recovery_required"
    FOCUSED_RETRY_ALLOWED = "focused_retry_allowed"
    PLAN_INVALID = "plan_invalid"
    SAFETY_BLOCKED = "safety_blocked"
    ARTIFACT_REDACTION_FAILED = "artifact_redaction_failed"
    UNKNOWN_STATE = "unknown_state"


class PlannedSegmentLike(Protocol):
    @property
    def segment_id(self) -> str:
        raise NotImplementedError

    @property
    def scenario_id(self) -> str:
        raise NotImplementedError

    @property
    def expected_initial_role_state(self) -> DesiredRoleState:
        raise NotImplementedError

    @property
    def expected_final_role_state(self) -> DesiredRoleState:
        raise NotImplementedError

    @property
    def mutates_lab(self) -> bool:
        raise NotImplementedError


class CertificationPlanLike(Protocol):
    @property
    def plan_id(self) -> str:
        raise NotImplementedError

    @property
    def segments(self) -> Sequence[PlannedSegmentLike]:
        raise NotImplementedError


class SegmentResultLike(Protocol):
    @property
    def planned_segment(self) -> PlannedSegmentLike:
        raise NotImplementedError

    @property
    def decision(self) -> CertificationDecision:
        raise NotImplementedError

    @property
    def reason(self) -> str:
        raise NotImplementedError

    @property
    def recovery_hint(self) -> str | None:
        raise NotImplementedError

    @property
    def controller_result(self) -> Any | None:
        raise NotImplementedError

    @property
    def proven_final_role_state(self) -> ObservedRoleState | None:
        raise NotImplementedError

    @property
    def role_transition(self) -> Any:
        raise NotImplementedError

    @property
    def artifact_payload(self) -> Mapping[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class RetryEligibility:
    retry_allowed: bool
    initial_state_proven: bool
    reason: str


@dataclass(frozen=True)
class SegmentStopClassification:
    final_decision: CertificationDecision
    recovery_category: RecoveryCategory
    retry_allowed: bool
    manual_recovery_required: bool
    initial_state_proven: bool
    operator_action_hint: str | None


@dataclass(frozen=True)
class RunRecoveryDecision:
    final_decision: CertificationDecision
    recovery_category: RecoveryCategory
    first_blocking_segment_id: str | None
    first_blocking_scenario_id: str | None
    first_blocking_reason: str | None
    mutation_attempted_before_block: bool
    mutation_completed_before_block: bool
    final_state_proven: bool
    expected_final_state: DesiredRoleState | None
    observed_final_state: ObservedRoleState | None
    safe_to_continue: bool
    retry_allowed: bool
    manual_recovery_required: bool
    operator_action_hint: str | None
    initial_state_proven: bool
    artifact_redaction_passed: bool
    summary_counts: dict[str, int]

    @property
    def decision(self) -> CertificationDecision:
        return self.final_decision

    @property
    def reason(self) -> str:
        return self.first_blocking_reason or "all certification plan segments passed and final role state is proven"

    @property
    def recovery_hint(self) -> str | None:
        return self.operator_action_hint


def _clean_text(value: str | None) -> str | None:
    return sanitize_artifact_text(value)


def _role_state_is_expected(value: Any) -> bool:
    return isinstance(value, DesiredRoleState)


def _expected_final_state(segment: PlannedSegmentLike) -> DesiredRoleState | None:
    expected = getattr(segment, "expected_final_role_state", None)
    if not _role_state_is_expected(expected):
        return None
    return expected


def _expected_initial_state(segment: PlannedSegmentLike) -> DesiredRoleState | None:
    expected = getattr(segment, "expected_initial_role_state", None)
    if not _role_state_is_expected(expected):
        return None
    return expected


def _execution_result(result: SegmentResultLike) -> Any | None:
    controller_result = result.controller_result
    if controller_result is None:
        return None
    return getattr(controller_result, "execution_result", None)


def _observed_initial_role_state(result: SegmentResultLike) -> ObservedRoleState | None:
    controller_result = result.controller_result
    if controller_result is None:
        return None
    state = getattr(controller_result, "observed_initial_role_state", None)
    if isinstance(state, ObservedRoleState):
        return state
    return None


def _mutation_attempted(result: SegmentResultLike) -> bool:
    execution_result = _execution_result(result)
    if execution_result is not None:
        return bool(getattr(execution_result, "mutation_attempted", False))
    transition = getattr(result, "role_transition", None)
    return bool(getattr(transition, "mutation_attempted", False))


def _mutation_completed(result: SegmentResultLike) -> bool:
    execution_result = _execution_result(result)
    if execution_result is not None:
        return bool(getattr(execution_result, "mutation_completed", False))
    transition = getattr(result, "role_transition", None)
    return bool(getattr(transition, "mutation_completed", False))


def _retryable_infra_failure(result: SegmentResultLike) -> bool:
    execution_result = _execution_result(result)
    if execution_result is None:
        return False
    return bool(getattr(execution_result, "retryable_infra_failure", False))


def _initial_state_proven(result: SegmentResultLike) -> bool:
    state = _observed_initial_role_state(result)
    return bool(state is not None and state.is_proven)


def _initial_state_matches_expected(result: SegmentResultLike) -> bool:
    state = _observed_initial_role_state(result)
    expected = _expected_initial_state(result.planned_segment)
    if state is None or expected is None:
        return False
    return state.matches_desired(expected)


def _result_has_rejected_artifact(result: SegmentResultLike) -> bool:
    return result.artifact_payload.get("redaction_status") == "rejected"


def _artifact_redaction_blocking_result(
    segment_results: Sequence[SegmentResultLike],
) -> SegmentResultLike | None:
    for result in segment_results:
        if _result_has_rejected_artifact(result):
            return result
        try:
            validate_artifact_payload_redacted(result.artifact_payload)
        except ValueError:
            return result
    return None


def _summary_counts(
    plan: CertificationPlanLike,
    segment_results: Sequence[SegmentResultLike],
    final_decision: CertificationDecision,
) -> dict[str, int]:
    counts = {
        "total_segments": len(plan.segments),
        "executed_segments": len(segment_results),
        "pass": sum(1 for result in segment_results if result.decision is CertificationDecision.PASS),
        "passed": sum(1 for result in segment_results if result.decision is CertificationDecision.PASS),
        "no_go": sum(1 for result in segment_results if result.decision is CertificationDecision.NO_GO),
        "recovery_required": sum(
            1 for result in segment_results if result.decision is CertificationDecision.RECOVERY_REQUIRED
        ),
        "infra_retryable": sum(
            1 for result in segment_results if result.decision is CertificationDecision.INFRA_RETRYABLE
        ),
        "blocked": sum(1 for result in segment_results if result.decision is CertificationDecision.BLOCKED),
    }
    if final_decision is not CertificationDecision.PASS and not any(
        result.decision is final_decision for result in segment_results
    ):
        if final_decision is CertificationDecision.NO_GO:
            counts["no_go"] += 1
        elif final_decision is CertificationDecision.RECOVERY_REQUIRED:
            counts["recovery_required"] += 1
        elif final_decision is CertificationDecision.INFRA_RETRYABLE:
            counts["infra_retryable"] += 1
        elif final_decision is CertificationDecision.BLOCKED:
            counts["blocked"] += 1
    return counts


def _operator_hint_for(
    decision: CertificationDecision,
    category: RecoveryCategory,
    segment_id: str | None,
    explicit_hint: str | None,
) -> str | None:
    if explicit_hint:
        return _clean_text(explicit_hint)
    if decision is CertificationDecision.PASS:
        return "no operator action required"
    if decision is CertificationDecision.INFRA_RETRYABLE and segment_id:
        return f"focused retry allowed for segment {segment_id} only"
    if decision is CertificationDecision.RECOVERY_REQUIRED:
        return "manual recovery required; rediscover the lab and prove a safe starting state before continuing"
    if decision is CertificationDecision.BLOCKED:
        return "fix the certification plan, configuration, or model data before rerunning"
    if category is RecoveryCategory.ARTIFACT_REDACTION_FAILED:
        return "review and remove unredacted sensitive artifact content before using this run"
    return "operator review required before using this run as certification evidence"


def determine_retry_eligibility(result: SegmentResultLike) -> RetryEligibility:
    if result.decision is not CertificationDecision.INFRA_RETRYABLE:
        return RetryEligibility(False, _initial_state_proven(result), "segment is not infrastructure retryable")
    if not _retryable_infra_failure(result):
        return RetryEligibility(False, _initial_state_proven(result), "execution did not mark failure retryable")
    if _mutation_attempted(result):
        return RetryEligibility(False, _initial_state_proven(result), "mutation was attempted before failure")
    if not _initial_state_proven(result):
        return RetryEligibility(False, False, "initial role state is not proven")
    if not _initial_state_matches_expected(result):
        return RetryEligibility(False, True, "initial role state does not match expected initial state")
    return RetryEligibility(True, True, "pre-mutation infrastructure failure is retryable")


def determine_manual_recovery_requirement(
    decision: CertificationDecision,
    category: RecoveryCategory,
) -> bool:
    if decision is CertificationDecision.RECOVERY_REQUIRED:
        return True
    return category is RecoveryCategory.MANUAL_RECOVERY_REQUIRED


def classify_segment_stop(result: SegmentResultLike) -> SegmentStopClassification:
    retry = determine_retry_eligibility(result)
    if result.decision is CertificationDecision.INFRA_RETRYABLE:
        if retry.retry_allowed and _initial_state_matches_expected(result):
            return SegmentStopClassification(
                final_decision=CertificationDecision.INFRA_RETRYABLE,
                recovery_category=RecoveryCategory.FOCUSED_RETRY_ALLOWED,
                retry_allowed=True,
                manual_recovery_required=False,
                initial_state_proven=True,
                operator_action_hint=_operator_hint_for(
                    CertificationDecision.INFRA_RETRYABLE,
                    RecoveryCategory.FOCUSED_RETRY_ALLOWED,
                    result.planned_segment.segment_id,
                    result.recovery_hint,
                ),
            )
        if _mutation_attempted(result) or not retry.initial_state_proven:
            decision = CertificationDecision.RECOVERY_REQUIRED
            category = RecoveryCategory.UNKNOWN_STATE
        else:
            decision = CertificationDecision.NO_GO
            category = RecoveryCategory.SAFETY_BLOCKED
        return SegmentStopClassification(
            final_decision=decision,
            recovery_category=category,
            retry_allowed=False,
            manual_recovery_required=determine_manual_recovery_requirement(decision, category),
            initial_state_proven=retry.initial_state_proven,
            operator_action_hint=_operator_hint_for(
                decision, category, result.planned_segment.segment_id, result.recovery_hint
            ),
        )

    if _result_has_rejected_artifact(result):
        return SegmentStopClassification(
            final_decision=CertificationDecision.NO_GO,
            recovery_category=RecoveryCategory.ARTIFACT_REDACTION_FAILED,
            retry_allowed=False,
            manual_recovery_required=False,
            initial_state_proven=_initial_state_proven(result),
            operator_action_hint=_operator_hint_for(
                CertificationDecision.NO_GO,
                RecoveryCategory.ARTIFACT_REDACTION_FAILED,
                result.planned_segment.segment_id,
                result.recovery_hint,
            ),
        )

    if result.decision is CertificationDecision.RECOVERY_REQUIRED:
        return SegmentStopClassification(
            final_decision=CertificationDecision.RECOVERY_REQUIRED,
            recovery_category=RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
            retry_allowed=False,
            manual_recovery_required=True,
            initial_state_proven=_initial_state_proven(result),
            operator_action_hint=_operator_hint_for(
                CertificationDecision.RECOVERY_REQUIRED,
                RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
                result.planned_segment.segment_id,
                result.recovery_hint,
            ),
        )
    if result.decision is CertificationDecision.BLOCKED:
        return SegmentStopClassification(
            final_decision=CertificationDecision.BLOCKED,
            recovery_category=RecoveryCategory.PLAN_INVALID,
            retry_allowed=False,
            manual_recovery_required=False,
            initial_state_proven=_initial_state_proven(result),
            operator_action_hint=_operator_hint_for(
                CertificationDecision.BLOCKED,
                RecoveryCategory.PLAN_INVALID,
                result.planned_segment.segment_id,
                result.recovery_hint,
            ),
        )

    reason = result.reason.lower()
    category = (
        RecoveryCategory.MANUAL_RECOVERY_REQUIRED if "both hubs active" in reason else RecoveryCategory.SAFETY_BLOCKED
    )
    return SegmentStopClassification(
        final_decision=CertificationDecision.NO_GO,
        recovery_category=category,
        retry_allowed=False,
        manual_recovery_required=determine_manual_recovery_requirement(CertificationDecision.NO_GO, category),
        initial_state_proven=_initial_state_proven(result),
        operator_action_hint=_operator_hint_for(
            CertificationDecision.NO_GO,
            category,
            result.planned_segment.segment_id,
            result.recovery_hint,
        ),
    )


def _first_non_pass(segment_results: Sequence[SegmentResultLike]) -> SegmentResultLike | None:
    return next((result for result in segment_results if result.decision is not CertificationDecision.PASS), None)


def _mutation_attempted_before(
    segment_results: Sequence[SegmentResultLike],
    blocking_result: SegmentResultLike | None,
) -> bool:
    if blocking_result is None:
        return False
    relevant_results = segment_results[: segment_results.index(blocking_result) + 1]
    return any(_mutation_attempted(result) for result in relevant_results)


def _mutation_completed_before(
    segment_results: Sequence[SegmentResultLike],
    blocking_result: SegmentResultLike | None,
) -> bool:
    if blocking_result is None:
        return False
    relevant_results = segment_results[: segment_results.index(blocking_result) + 1]
    return any(_mutation_completed(result) for result in relevant_results)


def _blocked_decision(
    plan: CertificationPlanLike,
    segment_results: Sequence[SegmentResultLike],
    *,
    reason: str,
    segment: PlannedSegmentLike | None = None,
    blocking_result: SegmentResultLike | None = None,
    category: RecoveryCategory = RecoveryCategory.PLAN_INVALID,
) -> RunRecoveryDecision:
    clean_reason = _clean_text(reason)
    mutation_attempted = (
        _mutation_attempted_before(segment_results, blocking_result)
        if blocking_result is not None
        else any(_mutation_attempted(result) for result in segment_results)
    )
    mutation_completed = (
        _mutation_completed_before(segment_results, blocking_result)
        if blocking_result is not None
        else any(_mutation_completed(result) for result in segment_results)
    )
    return RunRecoveryDecision(
        final_decision=CertificationDecision.BLOCKED,
        recovery_category=category,
        first_blocking_segment_id=segment.segment_id if segment else None,
        first_blocking_scenario_id=segment.scenario_id if segment else None,
        first_blocking_reason=clean_reason,
        mutation_attempted_before_block=mutation_attempted,
        mutation_completed_before_block=mutation_completed,
        final_state_proven=False,
        expected_final_state=None,
        observed_final_state=None,
        safe_to_continue=False,
        retry_allowed=False,
        manual_recovery_required=False,
        operator_action_hint=_operator_hint_for(
            CertificationDecision.BLOCKED, category, segment.segment_id if segment else None, None
        ),
        initial_state_proven=False,
        artifact_redaction_passed=True,
        summary_counts=_summary_counts(plan, segment_results, CertificationDecision.BLOCKED),
    )


def _non_pass_decision(
    plan: CertificationPlanLike,
    segment_results: Sequence[SegmentResultLike],
    blocking_result: SegmentResultLike,
) -> RunRecoveryDecision:
    stop = classify_segment_stop(blocking_result)
    reason = _clean_text(blocking_result.reason)
    return RunRecoveryDecision(
        final_decision=stop.final_decision,
        recovery_category=stop.recovery_category,
        first_blocking_segment_id=blocking_result.planned_segment.segment_id,
        first_blocking_scenario_id=blocking_result.planned_segment.scenario_id,
        first_blocking_reason=reason,
        mutation_attempted_before_block=_mutation_attempted_before(segment_results, blocking_result),
        mutation_completed_before_block=_mutation_completed_before(segment_results, blocking_result),
        final_state_proven=bool(
            blocking_result.proven_final_role_state is not None and blocking_result.proven_final_role_state.is_proven
        ),
        expected_final_state=_expected_final_state(blocking_result.planned_segment),
        observed_final_state=blocking_result.proven_final_role_state,
        safe_to_continue=False,
        retry_allowed=stop.retry_allowed,
        manual_recovery_required=stop.manual_recovery_required,
        operator_action_hint=stop.operator_action_hint,
        initial_state_proven=stop.initial_state_proven,
        artifact_redaction_passed=True,
        summary_counts=_summary_counts(plan, segment_results, stop.final_decision),
    )


def _redaction_failure_decision(
    plan: CertificationPlanLike,
    segment_results: Sequence[SegmentResultLike],
) -> RunRecoveryDecision:
    blocking_result = _artifact_redaction_blocking_result(segment_results)
    blocking_segment = blocking_result.planned_segment if blocking_result is not None else None
    observed_final_state = (
        blocking_result.proven_final_role_state
        if blocking_result is not None
        else segment_results[-1].proven_final_role_state if segment_results else None
    )
    expected_final_state = (
        _expected_final_state(blocking_segment)
        if blocking_segment is not None
        else _expected_final_state(plan.segments[-1]) if plan.segments else None
    )
    reason = (
        "segment artifact rejected by redaction"
        if blocking_result is not None
        else "run artifact rejected by redaction"
    )
    return RunRecoveryDecision(
        final_decision=CertificationDecision.NO_GO,
        recovery_category=RecoveryCategory.ARTIFACT_REDACTION_FAILED,
        first_blocking_segment_id=blocking_segment.segment_id if blocking_segment else None,
        first_blocking_scenario_id=blocking_segment.scenario_id if blocking_segment else None,
        first_blocking_reason=_clean_text(reason),
        mutation_attempted_before_block=(
            _mutation_attempted_before(segment_results, blocking_result)
            if blocking_result is not None
            else any(_mutation_attempted(result) for result in segment_results)
        ),
        mutation_completed_before_block=(
            _mutation_completed_before(segment_results, blocking_result)
            if blocking_result is not None
            else any(_mutation_completed(result) for result in segment_results)
        ),
        final_state_proven=bool(observed_final_state is not None and observed_final_state.is_proven),
        expected_final_state=expected_final_state,
        observed_final_state=observed_final_state,
        safe_to_continue=False,
        retry_allowed=False,
        manual_recovery_required=False,
        operator_action_hint=_operator_hint_for(
            CertificationDecision.NO_GO,
            RecoveryCategory.ARTIFACT_REDACTION_FAILED,
            blocking_segment.segment_id if blocking_segment else None,
            None,
        ),
        initial_state_proven=_initial_state_proven(blocking_result) if blocking_result is not None else False,
        artifact_redaction_passed=False,
        summary_counts=_summary_counts(plan, segment_results, CertificationDecision.NO_GO),
    )


def evaluate_run_decision(
    plan: CertificationPlanLike,
    segment_results: Sequence[SegmentResultLike],
    *,
    artifact_redaction_passed: bool,
) -> RunRecoveryDecision:
    if not artifact_redaction_passed:
        return _redaction_failure_decision(plan, segment_results)
    if not plan.segments:
        return _blocked_decision(plan, segment_results, reason="certification plan has no segments")
    if not segment_results:
        first_segment = plan.segments[0] if plan.segments else None
        return _blocked_decision(
            plan,
            segment_results,
            reason="certification plan produced no segment results",
            segment=first_segment,
        )

    first_non_pass = _first_non_pass(segment_results)
    if first_non_pass is not None:
        return _non_pass_decision(plan, segment_results, first_non_pass)

    if len(segment_results) != len(plan.segments):
        first_unrun_segment = plan.segments[len(segment_results)] if len(segment_results) < len(plan.segments) else None
        return _blocked_decision(
            plan,
            segment_results,
            reason="certification plan stopped before all required segments ran",
            segment=first_unrun_segment,
        )

    for result in segment_results:
        expected_final = _expected_final_state(result.planned_segment)
        if expected_final is None:
            return _blocked_decision(
                plan,
                segment_results,
                reason=f"segment {result.planned_segment.segment_id} is missing expected final role state",
                segment=result.planned_segment,
                blocking_result=result,
            )
        final_state = result.proven_final_role_state
        if final_state is None or not final_state.is_proven:
            reason = f"segment {result.planned_segment.segment_id} final role state is not proven"
            return RunRecoveryDecision(
                final_decision=CertificationDecision.RECOVERY_REQUIRED,
                recovery_category=RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
                first_blocking_segment_id=result.planned_segment.segment_id,
                first_blocking_scenario_id=result.planned_segment.scenario_id,
                first_blocking_reason=_clean_text(reason),
                mutation_attempted_before_block=_mutation_attempted_before(segment_results, result),
                mutation_completed_before_block=_mutation_completed_before(segment_results, result),
                final_state_proven=False,
                expected_final_state=expected_final,
                observed_final_state=final_state,
                safe_to_continue=False,
                retry_allowed=False,
                manual_recovery_required=True,
                operator_action_hint=_operator_hint_for(
                    CertificationDecision.RECOVERY_REQUIRED,
                    RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
                    result.planned_segment.segment_id,
                    None,
                ),
                initial_state_proven=_initial_state_proven(result),
                artifact_redaction_passed=True,
                summary_counts=_summary_counts(plan, segment_results, CertificationDecision.RECOVERY_REQUIRED),
            )
        if not final_state.matches_desired(expected_final):
            reason = (
                f"segment {result.planned_segment.segment_id} final role state does not match expected final role state"
            )
            return RunRecoveryDecision(
                final_decision=CertificationDecision.NO_GO,
                recovery_category=RecoveryCategory.SAFETY_BLOCKED,
                first_blocking_segment_id=result.planned_segment.segment_id,
                first_blocking_scenario_id=result.planned_segment.scenario_id,
                first_blocking_reason=_clean_text(reason),
                mutation_attempted_before_block=_mutation_attempted_before(segment_results, result),
                mutation_completed_before_block=_mutation_completed_before(segment_results, result),
                final_state_proven=True,
                expected_final_state=expected_final,
                observed_final_state=final_state,
                safe_to_continue=False,
                retry_allowed=False,
                manual_recovery_required=False,
                operator_action_hint=_operator_hint_for(
                    CertificationDecision.NO_GO,
                    RecoveryCategory.SAFETY_BLOCKED,
                    result.planned_segment.segment_id,
                    None,
                ),
                initial_state_proven=_initial_state_proven(result),
                artifact_redaction_passed=True,
                summary_counts=_summary_counts(plan, segment_results, CertificationDecision.NO_GO),
            )
        if result.planned_segment.mutates_lab and not _mutation_completed(result):
            reason = f"mutating segment {result.planned_segment.segment_id} lacks mutation completion evidence"
            return RunRecoveryDecision(
                final_decision=CertificationDecision.RECOVERY_REQUIRED,
                recovery_category=RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
                first_blocking_segment_id=result.planned_segment.segment_id,
                first_blocking_scenario_id=result.planned_segment.scenario_id,
                first_blocking_reason=_clean_text(reason),
                mutation_attempted_before_block=_mutation_attempted_before(segment_results, result),
                mutation_completed_before_block=_mutation_completed_before(segment_results, result),
                final_state_proven=True,
                expected_final_state=expected_final,
                observed_final_state=final_state,
                safe_to_continue=False,
                retry_allowed=False,
                manual_recovery_required=True,
                operator_action_hint=_operator_hint_for(
                    CertificationDecision.RECOVERY_REQUIRED,
                    RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
                    result.planned_segment.segment_id,
                    None,
                ),
                initial_state_proven=_initial_state_proven(result),
                artifact_redaction_passed=True,
                summary_counts=_summary_counts(plan, segment_results, CertificationDecision.RECOVERY_REQUIRED),
            )

    final_segment = plan.segments[-1]
    expected_final = _expected_final_state(final_segment)
    final_role_state = segment_results[-1].proven_final_role_state
    if expected_final is None:
        return _blocked_decision(
            plan,
            segment_results,
            reason="final segment is missing expected final role state",
            segment=final_segment,
            blocking_result=segment_results[-1],
        )
    if final_role_state is None or not final_role_state.is_proven:
        return RunRecoveryDecision(
            final_decision=CertificationDecision.RECOVERY_REQUIRED,
            recovery_category=RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
            first_blocking_segment_id=final_segment.segment_id,
            first_blocking_scenario_id=final_segment.scenario_id,
            first_blocking_reason="final role state is not proven",
            mutation_attempted_before_block=_mutation_attempted_before(segment_results, segment_results[-1]),
            mutation_completed_before_block=_mutation_completed_before(segment_results, segment_results[-1]),
            final_state_proven=False,
            expected_final_state=expected_final,
            observed_final_state=final_role_state,
            safe_to_continue=False,
            retry_allowed=False,
            manual_recovery_required=True,
            operator_action_hint=_operator_hint_for(
                CertificationDecision.RECOVERY_REQUIRED,
                RecoveryCategory.MANUAL_RECOVERY_REQUIRED,
                final_segment.segment_id,
                None,
            ),
            initial_state_proven=_initial_state_proven(segment_results[-1]),
            artifact_redaction_passed=True,
            summary_counts=_summary_counts(plan, segment_results, CertificationDecision.RECOVERY_REQUIRED),
        )
    if not final_role_state.matches_desired(expected_final):
        return RunRecoveryDecision(
            final_decision=CertificationDecision.NO_GO,
            recovery_category=RecoveryCategory.SAFETY_BLOCKED,
            first_blocking_segment_id=final_segment.segment_id,
            first_blocking_scenario_id=final_segment.scenario_id,
            first_blocking_reason="final role state does not match the certification plan expected final role state",
            mutation_attempted_before_block=_mutation_attempted_before(segment_results, segment_results[-1]),
            mutation_completed_before_block=_mutation_completed_before(segment_results, segment_results[-1]),
            final_state_proven=True,
            expected_final_state=expected_final,
            observed_final_state=final_role_state,
            safe_to_continue=False,
            retry_allowed=False,
            manual_recovery_required=False,
            operator_action_hint=_operator_hint_for(
                CertificationDecision.NO_GO,
                RecoveryCategory.SAFETY_BLOCKED,
                final_segment.segment_id,
                None,
            ),
            initial_state_proven=_initial_state_proven(segment_results[-1]),
            artifact_redaction_passed=True,
            summary_counts=_summary_counts(plan, segment_results, CertificationDecision.NO_GO),
        )

    return RunRecoveryDecision(
        final_decision=CertificationDecision.PASS,
        recovery_category=RecoveryCategory.NONE,
        first_blocking_segment_id=None,
        first_blocking_scenario_id=None,
        first_blocking_reason=None,
        mutation_attempted_before_block=False,
        mutation_completed_before_block=False,
        final_state_proven=True,
        expected_final_state=expected_final,
        observed_final_state=final_role_state,
        safe_to_continue=True,
        retry_allowed=False,
        manual_recovery_required=False,
        operator_action_hint=_operator_hint_for(CertificationDecision.PASS, RecoveryCategory.NONE, None, None),
        initial_state_proven=True,
        artifact_redaction_passed=True,
        summary_counts=_summary_counts(plan, segment_results, CertificationDecision.PASS),
    )


def build_recovery_summary(
    plan: CertificationPlanLike,
    segment_results: Sequence[SegmentResultLike],
    *,
    artifact_redaction_passed: bool,
) -> RunRecoveryDecision:
    return evaluate_run_decision(plan, segment_results, artifact_redaction_passed=artifact_redaction_passed)
