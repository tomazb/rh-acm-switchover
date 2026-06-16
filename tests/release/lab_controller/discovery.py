from __future__ import annotations

from typing import Any

from .models import (
    HubIdentityEvidence,
    HubObservation,
    HubRoleSignal,
    LabObservation,
    ManagedClusterEvidence,
    PhysicalHubLabel,
)

DEFAULT_EXPECTED_MANAGED_CLUSTERS = ("mc-1", "mc-2", "mc-3")
_UNSET = object()


def fake_identity(physical_label: PhysicalHubLabel) -> HubIdentityEvidence:
    """Build deterministic fake identity evidence for unit tests and non-live controller development."""
    suffix = physical_label.value
    return HubIdentityEvidence(
        physical_label=physical_label,
        kube_system_uid=f"uid-{suffix}",
        api_server_fingerprint=f"api-{suffix}",
        context_name=f"{suffix}-context",
    )


def fake_hub_observation(
    physical_label: PhysicalHubLabel,
    *,
    identity: HubIdentityEvidence | None | object = _UNSET,
    managed_clusters: ManagedClusterEvidence | None | object = _UNSET,
    role_signal: HubRoleSignal = HubRoleSignal.UNKNOWN,
) -> HubObservation:
    if identity is _UNSET:
        identity = fake_identity(physical_label)
    if managed_clusters is _UNSET:
        managed_clusters = ManagedClusterEvidence(
            expected_names=DEFAULT_EXPECTED_MANAGED_CLUSTERS,
            observed_names=(),
        )
    return HubObservation(
        physical_label=physical_label,
        identity=identity if identity is None or isinstance(identity, HubIdentityEvidence) else None,
        managed_clusters=(
            managed_clusters
            if managed_clusters is None or isinstance(managed_clusters, ManagedClusterEvidence)
            else None
        ),
        role_signal=role_signal,
    )


def fake_lab_observation(
    *,
    primary_label: PhysicalHubLabel | None,
    expected_managed_cluster_names: tuple[str, ...] = DEFAULT_EXPECTED_MANAGED_CLUSTERS,
) -> LabObservation:
    """Build a two-hub fake observation with exactly one active hub when primary_label is supplied."""
    observations: list[HubObservation] = []
    for physical_label in (PhysicalHubLabel.HUB_A, PhysicalHubLabel.HUB_B):
        is_primary = primary_label == physical_label
        observations.append(
            fake_hub_observation(
                physical_label,
                role_signal=HubRoleSignal.ACTIVE if is_primary else HubRoleSignal.PASSIVE,
                managed_clusters=ManagedClusterEvidence(
                    expected_names=expected_managed_cluster_names,
                    observed_names=expected_managed_cluster_names if is_primary else (),
                ),
            )
        )
    return LabObservation(observations=tuple(observations))


def managed_cluster_summary(observation: LabObservation) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for hub in observation.observations:
        evidence = hub.managed_clusters
        summary[hub.physical_label.value] = {
            "expected": list(evidence.expected_names) if evidence else [],
            "observed": list(evidence.observed_names) if evidence and evidence.observed_names is not None else None,
            "role_signal": hub.role_signal.value,
        }
    return summary
