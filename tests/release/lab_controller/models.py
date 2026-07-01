from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
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


class CertificationDecision(str, Enum):
    PASS = SegmentDecision.PASS.name
    NO_GO = "NO_GO"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    INFRA_RETRYABLE = "INFRA_RETRYABLE"
    BLOCKED = "BLOCKED"


class ScenarioClassification(str, Enum):
    STATIC_ONLY = "static-only"
    LIVE_NON_MUTATING = "live-non-mutating"
    LAB_MUTATING = "lab-mutating"
    RECOVERY = "recovery"
    DESTRUCTIVE_DISPOSABLE_LAB_ONLY = "destructive-disposable-lab-only"


class ArgoCDInterferenceMode(str, Enum):
    NOT_OWNED = "not_owned"
    OBSERVE_ONLY = "observe_only"
    OWNED_AUTOSYNC_OFF = "owned_autosync_off"
    AUTOMATED_SYNC = "automated_sync"
    AUTOMATED_SELF_HEAL = "automated_self_heal"
    AUTOMATED_PRUNE = "automated_prune"
    APPLICATIONSET_CHILD = "applicationset_child"
    UNKNOWN = "unknown"


class CoordinationStrategy(str, Enum):
    NOT_REQUIRED = "not_required"
    OBSERVE_ONLY = "observe_only"
    APPLICATION_COORDINATION_REQUIRED = "application_coordination_required"
    APPLICATIONSET_PARENT_COORDINATION_REQUIRED = "applicationset_parent_coordination_required"
    PARENT_LEVEL_COORDINATION = "parent_level_coordination"
    BLOCKED_UNKNOWN = "blocked_unknown"


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
class GitOpsTrackedResource:
    group: str
    kind: str
    namespace: str | None
    name: str
    tracking_id: str | None = None
    owning_application: str | None = None
    acm_object: bool = False

    @property
    def ref(self) -> str:
        group = self.group or ""
        namespace = self.namespace or ""
        return f"{group}/{self.kind}:{namespace}/{self.name}"


@dataclass(frozen=True)
class ArgoCDApplicationEvidence:
    name: str
    namespace: str | None
    owns_acm_resources: bool
    tracked_resources: tuple[GitOpsTrackedResource, ...] = ()
    sync_policy: Any = field(default_factory=dict)
    sync_options: tuple[str, ...] = ()
    applicationset_parent: str | None = None
    applicationset_owner_uid_present: bool = False
    applicationset_owner_uid_is_identity_evidence: bool = False


@dataclass(frozen=True)
class ApplicationSetOwnershipEvidence:
    name: str
    namespace: str | None
    child_applications: tuple[str, ...] = ()
    parent_level_coordination: bool = False
    owner_uid_identity_evidence: str = "not_used"


@dataclass(frozen=True)
class GitOpsCapabilityEvidence:
    automated_enabled_supported: bool | None
    source: str = "unknown"
    detail: str | None = None
    field_path: str = "spec.syncPolicy.automated.enabled"

    @classmethod
    def unknown(cls, detail: str | None = None) -> "GitOpsCapabilityEvidence":
        return cls(automated_enabled_supported=None, source="unknown", detail=detail)


@dataclass(frozen=True)
class GitOpsOwnershipEvidence:
    evaluated: bool
    source: str
    applications: tuple[ArgoCDApplicationEvidence, ...] = ()
    application_sets: tuple[ApplicationSetOwnershipEvidence, ...] = ()
    tracked_resources: tuple[GitOpsTrackedResource, ...] = ()
    automated_enabled_capability: GitOpsCapabilityEvidence = field(default_factory=GitOpsCapabilityEvidence.unknown)
    unknown_reason: str | None = None
    live_certification_evidence: bool = False

    @classmethod
    def unknown(cls, reason: str) -> "GitOpsOwnershipEvidence":
        return cls(evaluated=True, source="unknown", unknown_reason=reason)

    @classmethod
    def not_evaluated(cls, reason: str = "GitOps evidence was not supplied") -> "GitOpsOwnershipEvidence":
        return cls(evaluated=False, source="not_evaluated", unknown_reason=reason)


@dataclass(frozen=True)
class GitOpsRiskDecision:
    decision: SegmentDecision
    safe_to_continue: bool
    interference_mode: ArgoCDInterferenceMode
    coordination_strategy: CoordinationStrategy
    reason: str
    blocking_reason: str | None = None


@dataclass(frozen=True)
class ControllerDecision:
    decision: SegmentDecision
    reason: str
    safe_to_continue: bool
    recovery_hint: str | None = None


@dataclass(frozen=True)
class PhysicalHubConfig:
    """Stable physical lab inventory for one hub.

    physical_label is an inventory label such as hub-a or hub-b. It is not a logical
    primary/secondary role and must be mapped from proven role evidence for each segment.
    """

    physical_label: PhysicalHubLabel
    kubeconfig_reference: str
    context_name: str
    expected_identity: HubIdentityEvidence | None = None
    acm_namespace: str = "open-cluster-management"


@dataclass(frozen=True)
class ManagedClusterInventory:
    expected_names: tuple[str, ...]
    contexts: Mapping[str, str] = field(default_factory=dict)
    require_observability: bool = True


@dataclass(frozen=True)
class LabReleaseMetadata:
    expected_version: str | None = None
    candidate_tag: str | None = None
    metadata_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class LabArgoCDSettings:
    mandatory: bool = False
    namespaces: tuple[str, ...] = ()
    expected_pause: bool = True
    expected_resume: bool = True


@dataclass(frozen=True)
class LabArtifactSettings:
    root: str
    redaction_required: bool = True
    fail_on_unredacted_secret: bool = True


@dataclass(frozen=True)
class StableLabConfig:
    physical_hubs: Mapping[PhysicalHubLabel, PhysicalHubConfig]
    enabled_streams: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    profile_name: str
    expected_managed_cluster_names: tuple[str, ...] = ()
    artifact_root: str = "artifacts/release"
    argocd_namespaces: tuple[str, ...] = ()
    managed_clusters: ManagedClusterInventory | None = None
    release: LabReleaseMetadata | None = None
    argocd: LabArgoCDSettings | None = None
    artifacts: LabArtifactSettings | None = None
    gitops: GitOpsOwnershipEvidence | None = None

    def __post_init__(self) -> None:
        managed_clusters = self.managed_clusters
        if managed_clusters is None:
            managed_clusters = ManagedClusterInventory(expected_names=self.expected_managed_cluster_names)
        elif not self.expected_managed_cluster_names:
            object.__setattr__(self, "expected_managed_cluster_names", managed_clusters.expected_names)

        artifacts = self.artifacts
        if artifacts is None:
            artifacts = LabArtifactSettings(root=self.artifact_root)
        elif self.artifact_root == "artifacts/release":
            object.__setattr__(self, "artifact_root", artifacts.root)

        argocd = self.argocd
        if argocd is None:
            argocd = LabArgoCDSettings(mandatory=False, namespaces=self.argocd_namespaces)
        elif not self.argocd_namespaces:
            object.__setattr__(self, "argocd_namespaces", argocd.namespaces)

        object.__setattr__(self, "managed_clusters", managed_clusters)
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "argocd", argocd)


@dataclass(frozen=True)
class GeneratedProfile:
    profile_data: dict[str, Any]
    sha256: str
    logical_to_physical: dict[str, str]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GeneratedSegmentProfile:
    decision: ControllerDecision
    generated_profile: GeneratedProfile | None = None
