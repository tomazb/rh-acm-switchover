from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class PhysicalHubLabel(str, Enum):
    HUB_A = "hub-a"
    HUB_B = "hub-b"


class LogicalRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class HubRoleSignal(str, Enum):
    ACTIVE = "active"
    PASSIVE = "passive"
    UNKNOWN = "unknown"


class SegmentDecision(Enum):
    PASS = auto()
    NO_GO = auto()
    RECOVERY_REQUIRED = auto()
    INFRA_RETRYABLE = auto()


class ScenarioClassification(str, Enum):
    STATIC_ONLY = "static-only"
    LIVE_NON_MUTATING = "live-non-mutating"
    LAB_MUTATING = "lab-mutating"
    RECOVERY = "recovery"
    DESTRUCTIVE_DISPOSABLE_LAB_ONLY = "destructive-disposable-lab-only"


@dataclass(frozen=True)
class HubIdentityEvidence:
    physical_label: PhysicalHubLabel
    kube_system_uid: str | None
    api_server_fingerprint: str | None
    context_name: str | None
    cluster_version: str | None = None
    acm_evidence: str | None = None


@dataclass(frozen=True)
class ManagedClusterEvidence:
    expected_names: tuple[str, ...]
    observed_names: tuple[str, ...] | None

    @property
    def expected_set(self) -> set[str]:
        return set(self.expected_names)

    @property
    def observed_set(self) -> set[str] | None:
        if self.observed_names is None:
            return None
        return set(self.observed_names)


@dataclass(frozen=True)
class HubObservation:
    physical_label: PhysicalHubLabel
    identity: HubIdentityEvidence | None
    managed_clusters: ManagedClusterEvidence | None
    role_signal: HubRoleSignal = HubRoleSignal.UNKNOWN


@dataclass(frozen=True)
class LabObservation:
    observations: tuple[HubObservation, ...]

    def by_label(self) -> dict[PhysicalHubLabel, HubObservation]:
        return {observation.physical_label: observation for observation in self.observations}


@dataclass(frozen=True)
class DesiredRoleState:
    primary_physical_hub: PhysicalHubLabel
    secondary_physical_hub: PhysicalHubLabel


@dataclass(frozen=True)
class ObservedRoleState:
    primary_physical_hub: PhysicalHubLabel | None
    secondary_physical_hub: PhysicalHubLabel | None
    ambiguity_reason: str | None = None

    @property
    def is_proven(self) -> bool:
        return (
            self.primary_physical_hub is not None
            and self.secondary_physical_hub is not None
            and self.ambiguity_reason is None
        )

    def matches_desired(self, desired: DesiredRoleState) -> bool:
        return (
            self.is_proven
            and self.primary_physical_hub is desired.primary_physical_hub
            and self.secondary_physical_hub is desired.secondary_physical_hub
        )


@dataclass(frozen=True)
class SegmentPlan:
    segment_id: str
    scenario_id: str
    expected_initial_role_state: DesiredRoleState
    expected_final_role_state: DesiredRoleState
    mutates_lab: bool


@dataclass(frozen=True)
class ControllerDecision:
    decision: SegmentDecision
    reason: str
    safe_to_continue: bool
    recovery_hint: str | None = None


@dataclass(frozen=True)
class PhysicalHubConfig:
    physical_label: PhysicalHubLabel
    kubeconfig_reference: str
    context_name: str
    acm_namespace: str = "open-cluster-management"


@dataclass(frozen=True)
class StableLabConfig:
    physical_hubs: Mapping[PhysicalHubLabel, PhysicalHubConfig]
    expected_managed_cluster_names: tuple[str, ...]
    enabled_streams: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    profile_name: str
    artifact_root: str
    argocd_namespaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedProfile:
    profile_data: dict[str, Any]
    sha256: str
    logical_to_physical: dict[str, str]
    metadata: dict[str, Any]
