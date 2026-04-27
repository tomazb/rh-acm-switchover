from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class ProfileValidationError(ValueError):
    """Raised when a release profile violates schema or content policy."""


@dataclass(frozen=True)
class LoadProfileResult:
    path: Path
    sha256: str
    profile: "ReleaseProfile"


@dataclass(frozen=True)
class HubProfile:
    kubeconfig: str
    context: str
    acm_namespace: str = "open-cluster-management"
    role_label_selector: str | None = None
    timeout_minutes: int | None = None


@dataclass(frozen=True)
class ManagedClustersProfile:
    expected_names: tuple[str, ...] = ()
    expected_count: int | None = None
    contexts: Mapping[str, str] = field(default_factory=dict)
    require_observability: bool = True


@dataclass(frozen=True)
class StreamProfile:
    id: str
    enabled: bool = True
    required: bool | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioProfile:
    id: str
    required: bool | None = None
    streams: tuple[str, ...] = ()
    cycles: int = 1
    timeout_minutes: int | None = None
    skip_reason: str | None = None


@dataclass(frozen=True)
class MetadataExemption:
    path: str
    reason: str


@dataclass(frozen=True)
class ReleaseMetadataProfile:
    expected_version: str | None = None
    candidate_tag: str | None = None
    metadata_files: tuple[str, ...] = ()
    allow_non_authoritative_metadata: tuple[MetadataExemption, ...] = ()


@dataclass(frozen=True)
class ArgoCDApplicationSelector:
    match_labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ArgoCDProfile:
    mandatory: bool = True
    namespaces: tuple[str, ...] = ()
    application_selectors: tuple[ArgoCDApplicationSelector, ...] = ()
    expected_pause: bool = True
    expected_resume: bool = True
    managed_conflict_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequiredFlagProfile:
    required: bool = True


@dataclass(frozen=True)
class BaselineLabReadinessProfile:
    required: bool = True
    required_crds: tuple[str, ...] = ()
    backup_storage_location: RequiredFlagProfile = field(
        default_factory=RequiredFlagProfile
    )
    argocd_fixture: RequiredFlagProfile = field(default_factory=RequiredFlagProfile)


@dataclass(frozen=True)
class BaselineStaticGatesProfile:
    required: bool = True
    optional_gate_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BaselineProfile:
    initial_primary: str
    final_primary: str
    backup_schedule: RequiredFlagProfile = field(default_factory=RequiredFlagProfile)
    restore: RequiredFlagProfile = field(default_factory=RequiredFlagProfile)
    observability: RequiredFlagProfile = field(default_factory=RequiredFlagProfile)
    rbac: RequiredFlagProfile = field(default_factory=RequiredFlagProfile)
    lab_readiness: BaselineLabReadinessProfile = field(
        default_factory=BaselineLabReadinessProfile
    )
    static_gates: BaselineStaticGatesProfile = field(
        default_factory=BaselineStaticGatesProfile
    )


@dataclass(frozen=True)
class LimitsProfile:
    max_cycles: int = 1
    default_timeout_minutes: int = 120
    cooldown_seconds: int = 0
    soak_duration_minutes: int = 0
    max_tolerated_failures: int = 0
    artifact_retention_days: int = 30


@dataclass(frozen=True)
class RecoveryCleanupProfile:
    resources: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryProfile:
    pre_run_heal_passes: int = 1
    post_failure_passes_per_mutating_scenario: int = 1
    total_budget_minutes: int = 30
    allowed_destructive_cleanup: RecoveryCleanupProfile = field(
        default_factory=RecoveryCleanupProfile
    )
    rbac_actions: tuple[str, ...] = ("no_bootstrap", "revalidate")
    hard_stop_on: tuple[str, ...] = (
        "hub_role_restore_unproven",
        "argocd_resume_unproven",
        "rbac_bootstrap_unproven",
        "final_baseline_unproven",
    )


@dataclass(frozen=True)
class RedactionProfile:
    required: bool = True
    fail_on_unredacted_secret: bool = True


@dataclass(frozen=True)
class ArtifactsProfile:
    root: str = "artifacts/release"
    capture_stdout: bool = True
    capture_stderr: bool = True
    capture_cluster_snapshots: bool = False
    cluster_snapshot_mode: str = "allowlist"
    redaction: RedactionProfile = field(default_factory=RedactionProfile)
    compress_after_run: bool = False
    retention_days: int = 30


@dataclass(frozen=True)
class ReleaseProfile:
    profile_version: int
    name: str
    raw: Mapping[str, Any]
    release: ReleaseMetadataProfile | None
    hubs: Mapping[str, HubProfile]
    managed_clusters: ManagedClustersProfile
    streams: tuple[StreamProfile, ...]
    scenarios: tuple[ScenarioProfile, ...]
    argocd: ArgoCDProfile
    baseline: BaselineProfile
    limits: LimitsProfile
    recovery: RecoveryProfile
    artifacts: ArtifactsProfile
