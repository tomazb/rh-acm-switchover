from __future__ import annotations

from collections.abc import Mapping, Sequence

from .decisions import no_go, pass_decision, scenario_segment_blocking_reason
from .models import (
    ControllerDecision,
    GeneratedSegmentProfile,
    ObservedRoleState,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)
from .profiles import build_role_aware_profile


def _role_state_text(state: ObservedRoleState) -> str:
    primary = state.primary_physical_hub.value if state.primary_physical_hub else "unknown"
    secondary = state.secondary_physical_hub.value if state.secondary_physical_hub else "unknown"
    return f"primary={primary}, secondary={secondary}"


def evaluate_segment_start(
    plan: SegmentPlan,
    *,
    observed_role_state: ObservedRoleState,
    identity_decision: ControllerDecision,
) -> ControllerDecision:
    """Decide whether one planned segment may start from the observed known state."""
    if identity_decision.decision is not SegmentDecision.PASS or not identity_decision.safe_to_continue:
        return no_go(f"identity verification blocked segment {plan.segment_id}: {identity_decision.reason}")
    if not observed_role_state.is_proven:
        return no_go(
            f"ambiguous role state blocks segment {plan.segment_id}: "
            f"{observed_role_state.ambiguity_reason or 'role state is not proven'}"
        )
    if not observed_role_state.matches_desired(plan.expected_initial_role_state):
        return no_go(
            f"observed role state {_role_state_text(observed_role_state)} does not match expected initial role state "
            f"primary={plan.expected_initial_role_state.primary_physical_hub.value}, "
            f"secondary={plan.expected_initial_role_state.secondary_physical_hub.value}"
        )
    mutation_text = "mutating" if plan.mutates_lab else "non-mutating"
    return pass_decision(f"{mutation_text} segment {plan.segment_id} may start from proven state")


def evaluate_segment_chain(
    plans: Sequence[SegmentPlan],
    *,
    initial_observed_role_state: ObservedRoleState,
    identity_decision: ControllerDecision,
    proven_final_states: Mapping[str, ObservedRoleState] | None = None,
) -> ControllerDecision:
    """Evaluate known-state handoff across planned segments without executing scenarios."""
    proven_final_states = proven_final_states or {}
    current_state = initial_observed_role_state
    previous_plan: SegmentPlan | None = None

    for plan in plans:
        if previous_plan is not None and previous_plan.mutates_lab:
            previous_final = proven_final_states.get(previous_plan.segment_id)
            if previous_final is None:
                return no_go(f"previous segment {previous_plan.segment_id} has no proven final state")
            if not previous_final.is_proven:
                return no_go(
                    f"previous segment {previous_plan.segment_id} final state is not proven: "
                    f"{previous_final.ambiguity_reason or 'role state is not proven'}"
                )
            if not previous_final.matches_desired(previous_plan.expected_final_role_state):
                return no_go(
                    f"previous segment {previous_plan.segment_id} final state {_role_state_text(previous_final)} "
                    "does not match its expected final role state"
                )
            current_state = previous_final

        decision = evaluate_segment_start(
            plan,
            observed_role_state=current_state,
            identity_decision=identity_decision,
        )
        if decision.decision is not SegmentDecision.PASS:
            return decision
        previous_plan = plan

    return pass_decision("segment chain may start from proven known-state handoffs")


def _scenario_classification_decision(plan: SegmentPlan) -> ControllerDecision:
    try:
        blocking_reason = scenario_segment_blocking_reason(plan.scenario_id, mutates_lab=plan.mutates_lab)
    except ValueError as exc:
        return no_go(str(exc))
    if blocking_reason is not None:
        return no_go(blocking_reason)
    return pass_decision(f"scenario {plan.scenario_id} classification allows segment profile generation")


def generate_segment_profile(
    plan: SegmentPlan,
    *,
    lab_config: StableLabConfig,
    observed_role_state: ObservedRoleState,
    identity_decision: ControllerDecision,
    role_decision: ControllerDecision,
    artifact_root: str | None = None,
) -> GeneratedSegmentProfile:
    """Generate a role-aware profile only after known-state segment gates pass."""
    if role_decision.decision is not SegmentDecision.PASS or not role_decision.safe_to_continue:
        return GeneratedSegmentProfile(role_decision)

    classification_decision = _scenario_classification_decision(plan)
    if classification_decision.decision is not SegmentDecision.PASS:
        return GeneratedSegmentProfile(classification_decision)

    start_decision = evaluate_segment_start(
        plan,
        observed_role_state=observed_role_state,
        identity_decision=identity_decision,
    )
    if start_decision.decision is not SegmentDecision.PASS:
        return GeneratedSegmentProfile(start_decision)

    generated_profile = build_role_aware_profile(
        lab_config,
        observed_role_state,
        segment_plan=plan,
        artifact_root=artifact_root,
    )
    return GeneratedSegmentProfile(start_decision, generated_profile)
