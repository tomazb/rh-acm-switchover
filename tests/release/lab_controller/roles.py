from __future__ import annotations

from .decisions import no_go, pass_decision, recovery_required
from .models import (
    ControllerDecision,
    HubObservation,
    HubRoleSignal,
    LabObservation,
    ManagedClusterEvidence,
    ObservedRoleState,
    PhysicalHubLabel,
)

_REQUIRED_PHYSICAL_HUB_LABELS = frozenset((PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B))


def _unproven_state(reason: str) -> ObservedRoleState:
    return ObservedRoleState(primary_physical_hub=None, secondary_physical_hub=None, ambiguity_reason=reason)


def _empty_unproven_response(reason: str, decision: ControllerDecision) -> tuple[ObservedRoleState, ControllerDecision]:
    return _unproven_state(reason), decision


def _managed_cluster_problem(label: PhysicalHubLabel, evidence: ManagedClusterEvidence | None) -> str | None:
    if evidence is None:
        return f"missing managed cluster evidence for {label.value}"
    if not evidence.expected_names:
        return f"missing expected managed cluster names for {label.value}"
    observed = evidence.observed_set
    if observed is None:
        return f"missing observed managed cluster names for {label.value}"
    return None


def _active_cluster_set_problem(label: PhysicalHubLabel, evidence: ManagedClusterEvidence) -> str | None:
    expected = evidence.expected_set
    observed = evidence.observed_set or set()
    if observed == expected:
        return None
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    details = []
    if missing:
        details.append(f"missing: {', '.join(missing)}")
    if extra:
        details.append(f"extra: {', '.join(extra)}")
    return f"unexpected managed cluster set on {label.value}: {'; '.join(details)}"


def _active_candidates(observations: tuple[HubObservation, ...]) -> tuple[PhysicalHubLabel, ...]:
    return tuple(
        observation.physical_label for observation in observations if observation.role_signal is HubRoleSignal.ACTIVE
    )


def _observation_label_problem(observations: tuple[HubObservation, ...]) -> str | None:
    labels = tuple(observation.physical_label for observation in observations)
    if len(labels) != 2 or set(labels) != _REQUIRED_PHYSICAL_HUB_LABELS:
        observed = ", ".join(label.value for label in labels) or "none"
        return f"expected observations for hub-a and hub-b, got {observed}"
    return None


def infer_observed_role_state(observation: LabObservation) -> tuple[ObservedRoleState, ControllerDecision]:
    """Infer the current logical role mapping from deterministic Phase 1 evidence."""
    observations = observation.observations
    label_problem = _observation_label_problem(observations)
    if label_problem:
        reason = label_problem
        return _empty_unproven_response(reason, no_go(reason))

    for hub in observations:
        problem = _managed_cluster_problem(hub.physical_label, hub.managed_clusters)
        if problem:
            return _empty_unproven_response(problem, recovery_required(problem))

    active = _active_candidates(observations)
    if len(active) > 1:
        reason = "both hubs active; logical primary is ambiguous"
        return _empty_unproven_response(reason, no_go(reason))
    if not active:
        reason = "neither hub is active; recovery is required before mutation"
        return _empty_unproven_response(reason, recovery_required(reason))

    unknown_role_signals = tuple(hub.physical_label for hub in observations if hub.role_signal is HubRoleSignal.UNKNOWN)
    if unknown_role_signals:
        label = unknown_role_signals[0].value
        reason = f"unknown role signal for {label}; recovery is required before mutation"
        return _empty_unproven_response(reason, recovery_required(reason))

    for hub in observations:
        if hub.role_signal is not HubRoleSignal.ACTIVE:
            continue
        managed_clusters = hub.managed_clusters
        if managed_clusters is None:
            problem = f"missing managed cluster evidence for {hub.physical_label.value}"
            return _empty_unproven_response(problem, recovery_required(problem))
        problem = _active_cluster_set_problem(hub.physical_label, managed_clusters)
        if problem:
            return _empty_unproven_response(problem, recovery_required(problem))

    primary = active[0]
    secondary = next(hub.physical_label for hub in observations if hub.physical_label is not primary)
    state = ObservedRoleState(primary_physical_hub=primary, secondary_physical_hub=secondary)
    return state, pass_decision(f"{primary.value} is proven primary and {secondary.value} is proven secondary")
