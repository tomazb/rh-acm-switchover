from __future__ import annotations

from tests.release.lab_controller.discovery import fake_hub_observation
from tests.release.lab_controller.identity import verify_physical_hub_identities
from tests.release.lab_controller.models import (
    HubIdentityEvidence,
    LabObservation,
    PhysicalHubLabel,
    SegmentDecision,
)


def _expected_identities() -> dict[PhysicalHubLabel, HubIdentityEvidence]:
    return {
        PhysicalHubLabel.HUB_A: HubIdentityEvidence(
            physical_label=PhysicalHubLabel.HUB_A,
            kube_system_uid="uid-hub-a",
            api_server_fingerprint="api-hub-a",
            context_name="hub-a-context",
            cluster_version="4.16",
            acm_evidence="mch-a",
        ),
        PhysicalHubLabel.HUB_B: HubIdentityEvidence(
            physical_label=PhysicalHubLabel.HUB_B,
            kube_system_uid="uid-hub-b",
            api_server_fingerprint="api-hub-b",
            context_name="hub-b-context",
            cluster_version="4.16",
            acm_evidence="mch-b",
        ),
    }


def test_identity_verification_passes_when_both_physical_hubs_match() -> None:
    expected = _expected_identities()
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=expected[PhysicalHubLabel.HUB_A]),
            fake_hub_observation(PhysicalHubLabel.HUB_B, identity=expected[PhysicalHubLabel.HUB_B]),
        )
    )

    decision = verify_physical_hub_identities(expected, observation)

    assert decision.decision is SegmentDecision.PASS
    assert decision.safe_to_continue is True
    assert "proven" in decision.reason


def test_identity_verification_fails_closed_when_identity_evidence_is_missing() -> None:
    expected = _expected_identities()
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=None),
            fake_hub_observation(PhysicalHubLabel.HUB_B, identity=expected[PhysicalHubLabel.HUB_B]),
        )
    )

    decision = verify_physical_hub_identities(expected, observation)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "missing identity evidence for hub-a" in decision.reason


def test_identity_verification_fails_closed_when_expected_binding_is_missing() -> None:
    expected = _expected_identities()
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=expected[PhysicalHubLabel.HUB_A]),
            fake_hub_observation(PhysicalHubLabel.HUB_B, identity=expected[PhysicalHubLabel.HUB_B]),
        )
    )

    decision = verify_physical_hub_identities(
        {PhysicalHubLabel.HUB_A: expected[PhysicalHubLabel.HUB_A]},
        observation,
    )

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "missing expected identity binding for hub-b" in decision.reason


def test_identity_verification_fails_closed_when_context_exists_but_fingerprint_is_missing() -> None:
    expected = _expected_identities()
    missing_fingerprint = HubIdentityEvidence(
        physical_label=PhysicalHubLabel.HUB_A,
        kube_system_uid=None,
        api_server_fingerprint=None,
        context_name="hub-a-context",
    )
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=missing_fingerprint),
            fake_hub_observation(PhysicalHubLabel.HUB_B, identity=expected[PhysicalHubLabel.HUB_B]),
        )
    )

    decision = verify_physical_hub_identities(expected, observation)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "missing observed identity fingerprint for hub-a" in decision.reason


def test_identity_verification_fails_closed_on_duplicate_observed_physical_label() -> None:
    expected = _expected_identities()
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=expected[PhysicalHubLabel.HUB_A]),
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=expected[PhysicalHubLabel.HUB_A]),
        )
    )

    decision = verify_physical_hub_identities(expected, observation)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "duplicate observation for hub-a" in decision.reason


def test_identity_verification_fails_closed_when_identities_are_swapped() -> None:
    expected = _expected_identities()
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=expected[PhysicalHubLabel.HUB_B]),
            fake_hub_observation(PhysicalHubLabel.HUB_B, identity=expected[PhysicalHubLabel.HUB_A]),
        )
    )

    decision = verify_physical_hub_identities(expected, observation)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "hub-a" in decision.reason
    assert "hub-b" in decision.reason


def test_identity_verification_fails_closed_when_kube_system_uid_changes() -> None:
    expected = _expected_identities()
    changed_hub_a = HubIdentityEvidence(
        physical_label=PhysicalHubLabel.HUB_A,
        kube_system_uid="uid-hub-a-recreated",
        api_server_fingerprint="api-hub-a",
        context_name="hub-a-context",
    )
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=changed_hub_a),
            fake_hub_observation(PhysicalHubLabel.HUB_B, identity=expected[PhysicalHubLabel.HUB_B]),
        )
    )

    decision = verify_physical_hub_identities(expected, observation)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "identity mismatch for hub-a" in decision.reason


def test_identity_verification_does_not_trust_matching_context_name_when_fingerprint_differs() -> None:
    expected = _expected_identities()
    changed_hub_a = HubIdentityEvidence(
        physical_label=PhysicalHubLabel.HUB_A,
        kube_system_uid="uid-other-cluster",
        api_server_fingerprint="api-other-cluster",
        context_name="hub-a-context",
    )
    observation = LabObservation(
        observations=(
            fake_hub_observation(PhysicalHubLabel.HUB_A, identity=changed_hub_a),
            fake_hub_observation(PhysicalHubLabel.HUB_B, identity=expected[PhysicalHubLabel.HUB_B]),
        )
    )

    decision = verify_physical_hub_identities(expected, observation)

    assert decision.decision is SegmentDecision.NO_GO
    assert decision.safe_to_continue is False
    assert "context name is not sufficient proof" in decision.reason
