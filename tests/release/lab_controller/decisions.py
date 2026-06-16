from __future__ import annotations

from tests.release.scenarios.catalog import SCENARIOS_BY_ID

from .models import ControllerDecision, ScenarioClassification, SegmentDecision

_CLASSIFICATION_BY_SCENARIO_ID: dict[str, ScenarioClassification] = {
    "static-gates": ScenarioClassification.STATIC_ONLY,
    "runtime-parity": ScenarioClassification.STATIC_ONLY,
    "lab-readiness": ScenarioClassification.LIVE_NON_MUTATING,
    "baseline-check": ScenarioClassification.LIVE_NON_MUTATING,
    "preflight": ScenarioClassification.LIVE_NON_MUTATING,
    "final-baseline-check": ScenarioClassification.LIVE_NON_MUTATING,
    "bash-discovery": ScenarioClassification.LIVE_NON_MUTATING,
    "bash-postflight": ScenarioClassification.LIVE_NON_MUTATING,
    "rbac-bootstrap-live": ScenarioClassification.LIVE_NON_MUTATING,
    "python-passive-switchover": ScenarioClassification.LAB_MUTATING,
    "ansible-passive-switchover": ScenarioClassification.LAB_MUTATING,
    "python-restore-only": ScenarioClassification.LAB_MUTATING,
    "ansible-restore-only": ScenarioClassification.LAB_MUTATING,
    "argocd-managed-switchover": ScenarioClassification.LAB_MUTATING,
    "full-restore": ScenarioClassification.LAB_MUTATING,
    "checkpoint-resume": ScenarioClassification.LAB_MUTATING,
    "soak": ScenarioClassification.LAB_MUTATING,
    "rbac-bootstrap": ScenarioClassification.RECOVERY,
    "decommission": ScenarioClassification.DESTRUCTIVE_DISPOSABLE_LAB_ONLY,
    "failure-injection": ScenarioClassification.DESTRUCTIVE_DISPOSABLE_LAB_ONLY,
}


def pass_decision(reason: str) -> ControllerDecision:
    return ControllerDecision(decision=SegmentDecision.PASS, reason=reason, safe_to_continue=True)


def no_go(reason: str, *, recovery_hint: str | None = None) -> ControllerDecision:
    return ControllerDecision(
        decision=SegmentDecision.NO_GO,
        reason=reason,
        safe_to_continue=False,
        recovery_hint=recovery_hint,
    )


def recovery_required(reason: str, *, recovery_hint: str | None = None) -> ControllerDecision:
    return ControllerDecision(
        decision=SegmentDecision.RECOVERY_REQUIRED,
        reason=reason,
        safe_to_continue=False,
        recovery_hint=recovery_hint,
    )


def infra_retryable(reason: str, *, recovery_hint: str | None = None) -> ControllerDecision:
    return ControllerDecision(
        decision=SegmentDecision.INFRA_RETRYABLE,
        reason=reason,
        safe_to_continue=False,
        recovery_hint=recovery_hint,
    )


def classify_scenario(scenario_id: str) -> ScenarioClassification:
    """Classify a catalog scenario for known-state controller safety decisions."""
    if scenario_id not in SCENARIOS_BY_ID:
        raise ValueError(f"unknown release scenario: {scenario_id}")
    classification = _CLASSIFICATION_BY_SCENARIO_ID.get(scenario_id)
    if classification is None:
        raise ValueError(f"unclassified release scenario: {scenario_id}")
    return classification


def scenario_segment_blocking_reason(scenario_id: str, *, mutates_lab: bool) -> str | None:
    classification = classify_scenario(scenario_id)
    if classification is ScenarioClassification.DESTRUCTIVE_DISPOSABLE_LAB_ONLY:
        return f"scenario {scenario_id} is destructive/disposable-lab-only"
    if classification is ScenarioClassification.RECOVERY:
        return f"scenario {scenario_id} is a recovery scenario and cannot run in the normal planner"
    if classification is ScenarioClassification.LAB_MUTATING and not mutates_lab:
        return f"scenario {scenario_id} is lab-mutating but segment is marked non-mutating"
    if (
        classification
        in {
            ScenarioClassification.STATIC_ONLY,
            ScenarioClassification.LIVE_NON_MUTATING,
        }
        and mutates_lab
    ):
        return f"scenario {scenario_id} is non-mutating but segment is marked mutating"
    return None
