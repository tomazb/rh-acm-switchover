from __future__ import annotations

from tests.release.lab_controller.discovery import fake_hub_observation
from tests.release.lab_controller.models import (
    HubObservation,
    HubRoleSignal,
    LabObservation,
    ManagedClusterEvidence,
    PhysicalHubLabel,
    SegmentDecision,
)
from tests.release.lab_controller.roles import infer_observed_role_state

EXPECTED_CLUSTERS = ("mc-1", "mc-2", "mc-3")


def _hub(
    label: PhysicalHubLabel,
    signal: HubRoleSignal,
    observed_names: tuple[str, ...] | None,
) -> HubObservation:
    return fake_hub_observation(
        label,
        role_signal=signal,
        managed_clusters=ManagedClusterEvidence(
            expected_names=EXPECTED_CLUSTERS,
            observed_names=observed_names,
        ),
    )


def test_roles_detect_hub_a_primary_hub_b_secondary() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, EXPECTED_CLUSTERS),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.PASSIVE, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.PASS
    assert decision.safe_to_continue is True
    assert state.primary_physical_hub is PhysicalHubLabel.HUB_A
    assert state.secondary_physical_hub is PhysicalHubLabel.HUB_B
    assert state.ambiguity_reason is None


def test_roles_detect_hub_b_primary_hub_a_secondary() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.PASSIVE, ()),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.ACTIVE, EXPECTED_CLUSTERS),
            )
        )
    )

    assert decision.decision is SegmentDecision.PASS
    assert decision.safe_to_continue is True
    assert state.primary_physical_hub is PhysicalHubLabel.HUB_B
    assert state.secondary_physical_hub is PhysicalHubLabel.HUB_A


def test_roles_fail_both_hubs_active() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, EXPECTED_CLUSTERS),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.ACTIVE, EXPECTED_CLUSTERS),
            )
        )
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "both hubs active" in decision.reason


def test_roles_fail_both_hubs_active_even_when_cluster_evidence_conflicts() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, EXPECTED_CLUSTERS),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.ACTIVE, ("mc-1", "mc-extra")),
            )
        )
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "both hubs active" in decision.reason


def test_roles_fail_closed_on_duplicate_physical_hub_observations() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, EXPECTED_CLUSTERS),
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.PASSIVE, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "expected observations for hub-a and hub-b" in decision.reason


def test_roles_do_not_infer_secondary_from_unknown_role_signal() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, EXPECTED_CLUSTERS),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.UNKNOWN, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "unknown role signal for hub-b" in decision.reason


def test_roles_mark_neither_hub_active_recovery_required() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.PASSIVE, ()),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.PASSIVE, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "neither hub is active" in decision.reason


def test_roles_fail_unexpected_managed_cluster_set() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, ("mc-1", "mc-2", "mc-x")),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.PASSIVE, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "unexpected managed cluster set on hub-a" in decision.reason


def test_roles_fail_when_managed_cluster_evidence_is_missing() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                fake_hub_observation(PhysicalHubLabel.HUB_A, role_signal=HubRoleSignal.ACTIVE, managed_clusters=None),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.PASSIVE, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "missing managed cluster evidence for hub-a" in decision.reason


def test_roles_fail_when_extra_managed_cluster_is_present() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, ("mc-1", "mc-2", "mc-3", "mc-extra")),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.PASSIVE, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "extra: mc-extra" in decision.reason


def test_roles_fail_when_expected_managed_cluster_is_missing() -> None:
    state, decision = infer_observed_role_state(
        LabObservation(
            observations=(
                _hub(PhysicalHubLabel.HUB_A, HubRoleSignal.ACTIVE, ("mc-1", "mc-3")),
                _hub(PhysicalHubLabel.HUB_B, HubRoleSignal.PASSIVE, ()),
            )
        )
    )

    assert decision.decision is SegmentDecision.RECOVERY_REQUIRED
    assert decision.safe_to_continue is False
    assert state.primary_physical_hub is None
    assert "missing: mc-2" in decision.reason
