from __future__ import annotations

from tests.release.lab_controller.models import (
    ControllerDecision,
    DesiredRoleState,
    ObservedRoleState,
    PhysicalHubLabel,
    SegmentDecision,
    SegmentPlan,
)
from tests.release.lab_controller.segments import evaluate_segment_chain, evaluate_segment_start


def _role_state(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> ObservedRoleState:
    return ObservedRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _desired(primary: PhysicalHubLabel, secondary: PhysicalHubLabel) -> DesiredRoleState:
    return DesiredRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)


def _identity_pass() -> ControllerDecision:
    return ControllerDecision(
        decision=SegmentDecision.PASS,
        reason="physical hub identities proven",
        safe_to_continue=True,
    )


def _switchover_segment(segment_id: str = "segment-python") -> SegmentPlan:
    return SegmentPlan(
        segment_id=segment_id,
        scenario_id="python-passive-switchover",
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        expected_final_role_state=_desired(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        mutates_lab=True,
    )


def test_planner_allows_one_mutating_segment_from_proven_state() -> None:
    decision = evaluate_segment_start(
        _switchover_segment(),
        observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_identity_pass(),
    )

    assert decision.decision is SegmentDecision.PASS
    assert decision.safe_to_continue is True


def test_planner_blocks_second_mutation_without_proven_final_state() -> None:
    first = _switchover_segment("segment-python")
    second = SegmentPlan(
        segment_id="segment-ansible",
        scenario_id="ansible-passive-switchover",
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        expected_final_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        mutates_lab=True,
    )

    decision = evaluate_segment_chain(
        (first, second),
        initial_observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_identity_pass(),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "previous segment segment-python has no proven final state" in decision.reason


def test_planner_allows_second_mutation_with_proven_final_state() -> None:
    first = _switchover_segment("segment-python")
    second = SegmentPlan(
        segment_id="segment-ansible",
        scenario_id="ansible-passive-switchover",
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        expected_final_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        mutates_lab=True,
    )

    decision = evaluate_segment_chain(
        (first, second),
        initial_observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_identity_pass(),
        proven_final_states={
            "segment-python": _role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        },
    )

    assert decision.decision is SegmentDecision.PASS
    assert decision.safe_to_continue is True


def test_planner_blocks_stale_static_profile_after_role_transition() -> None:
    decision = evaluate_segment_start(
        _switchover_segment(),
        observed_role_state=_role_state(PhysicalHubLabel.HUB_B, PhysicalHubLabel.HUB_A),
        identity_decision=_identity_pass(),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "does not match expected initial role state" in decision.reason


def test_planner_allows_non_mutating_checks_from_proven_state() -> None:
    plan = SegmentPlan(
        segment_id="segment-preflight",
        scenario_id="preflight",
        expected_initial_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        expected_final_role_state=_desired(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        mutates_lab=False,
    )

    decision = evaluate_segment_start(
        plan,
        observed_role_state=_role_state(PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B),
        identity_decision=_identity_pass(),
    )

    assert decision.decision is SegmentDecision.PASS
    assert decision.safe_to_continue is True


def test_planner_blocks_mutation_when_role_state_is_ambiguous() -> None:
    ambiguous = ObservedRoleState(
        primary_physical_hub=None,
        secondary_physical_hub=None,
        ambiguity_reason="both hubs active",
    )

    decision = evaluate_segment_start(
        _switchover_segment(),
        observed_role_state=ambiguous,
        identity_decision=_identity_pass(),
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "ambiguous role state" in decision.reason
