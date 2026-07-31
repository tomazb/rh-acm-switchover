from __future__ import annotations

from collections.abc import Iterable, Mapping

from .decisions import no_go, pass_decision
from .models import ControllerDecision, HubIdentityEvidence, LabObservation, PhysicalHubLabel

_REQUIRED_PHYSICAL_HUB_LABELS = frozenset((PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B))


def _required_fingerprint(evidence: HubIdentityEvidence) -> tuple[str, str] | None:
    if not evidence.kube_system_uid or not evidence.api_server_fingerprint:
        return None
    return evidence.kube_system_uid, evidence.api_server_fingerprint


def _label_list(labels: Iterable[PhysicalHubLabel]) -> str:
    return ", ".join(label.value for label in sorted(labels, key=lambda item: item.value))


def _validate_expected_labels(
    expected: Mapping[PhysicalHubLabel, HubIdentityEvidence],
) -> ControllerDecision | None:
    missing_expected = _REQUIRED_PHYSICAL_HUB_LABELS - set(expected)
    if missing_expected:
        return no_go(f"missing expected identity binding for {_label_list(missing_expected)}")
    return None


def _validate_observed_labels(observation: LabObservation) -> ControllerDecision | None:
    seen_observed_labels: set[PhysicalHubLabel] = set()
    for observed in observation.observations:
        if observed.physical_label in seen_observed_labels:
            return no_go(f"duplicate observation for {observed.physical_label.value}")
        seen_observed_labels.add(observed.physical_label)

    missing_observed = _REQUIRED_PHYSICAL_HUB_LABELS - seen_observed_labels
    if missing_observed:
        return no_go(f"missing observation for {_label_list(missing_observed)}")
    return None


def verify_physical_hub_identities(
    expected_identities: Mapping[PhysicalHubLabel, HubIdentityEvidence],
    observation: LabObservation,
) -> ControllerDecision:
    """Fail-closed comparison of expected physical hub bindings against observed evidence."""
    expected = dict(expected_identities)
    label_decision = _validate_expected_labels(expected) or _validate_observed_labels(observation)
    if label_decision is not None:
        return label_decision

    observed_by_label = observation.by_label()

    for label in sorted(expected, key=lambda item: item.value):
        expected_evidence = expected[label]
        observed = observed_by_label.get(label)
        if observed is None:
            return no_go(f"missing observation for {label.value}")
        if observed.identity is None:
            return no_go(f"missing identity evidence for {label.value}")

        observed_evidence = observed.identity
        if observed_evidence.physical_label is not label:
            return no_go(
                f"identity evidence for {label.value} is labeled {observed_evidence.physical_label.value}; "
                "physical hub identities appear swapped"
            )

        expected_fingerprint = _required_fingerprint(expected_evidence)
        observed_fingerprint = _required_fingerprint(observed_evidence)
        if expected_fingerprint is None:
            return no_go(f"missing expected identity fingerprint for {label.value}")
        if observed_fingerprint is None:
            return no_go(f"missing observed identity fingerprint for {label.value}")

        if observed_fingerprint == expected_fingerprint:
            continue

        matching_expected_label = next(
            (
                other_label
                for other_label, other_evidence in expected.items()
                if _required_fingerprint(other_evidence) == observed_fingerprint
            ),
            None,
        )
        if matching_expected_label is not None and matching_expected_label is not label:
            return no_go(
                f"identity mismatch for {label.value}: observed fingerprint matches "
                f"{matching_expected_label.value}, not {label.value}"
            )

        context_note = ""
        if observed_evidence.context_name == expected_evidence.context_name:
            context_note = "; context name is not sufficient proof"
        return no_go(f"identity mismatch for {label.value}{context_note}")

    observed_fingerprints: dict[tuple[str, str], PhysicalHubLabel] = {}
    for label, observed in observed_by_label.items():
        if observed.identity is None:
            continue
        fingerprint = _required_fingerprint(observed.identity)
        if fingerprint is None:
            continue
        if fingerprint in observed_fingerprints:
            return no_go(
                f"duplicate physical hub identity fingerprint on {observed_fingerprints[fingerprint].value} "
                f"and {label.value}"
            )
        observed_fingerprints[fingerprint] = label

    return pass_decision("physical hub identities proven")
