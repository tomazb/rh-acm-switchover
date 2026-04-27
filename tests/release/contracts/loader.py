from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml

from .models import (
    ArgoCDApplicationSelector,
    ArgoCDProfile,
    ArtifactsProfile,
    BaselineLabReadinessProfile,
    BaselineProfile,
    BaselineStaticGatesProfile,
    HubProfile,
    LimitsProfile,
    LoadProfileResult,
    ManagedClustersProfile,
    MetadataExemption,
    ProfileValidationError,
    RecoveryCleanupProfile,
    RecoveryProfile,
    RedactionProfile,
    ReleaseMetadataProfile,
    ReleaseProfile,
    RequiredFlagProfile,
    ScenarioProfile,
    StreamProfile,
)
from .schema import (
    require_mapping,
    require_sequence,
    validate_argocd,
    validate_artifacts,
    validate_baseline,
    validate_hubs,
    validate_limits,
    validate_managed_clusters,
    validate_profile_contents,
    validate_recovery,
    validate_release,
    validate_scenario,
    validate_stream,
    validate_top_level,
)


def _read_yaml(path: Path) -> tuple[Mapping[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ProfileValidationError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProfileValidationError(f"{path}: expected YAML mapping at document root")
    return parsed, hashlib.sha256(content.encode("utf-8")).hexdigest()


def _required_flag(
    raw: Mapping[str, Any] | None, default: bool = True
) -> RequiredFlagProfile:
    payload = raw or {}
    return RequiredFlagProfile(required=bool(payload.get("required", default)))


def _release(raw: Mapping[str, Any] | None) -> ReleaseMetadataProfile | None:
    if raw is None:
        return None
    metadata_files = tuple(str(item) for item in raw.get("metadata_files", ()))
    allow_non_authoritative = tuple(
        MetadataExemption(path=str(item["path"]), reason=str(item["reason"]))
        for item in raw.get("allow_non_authoritative_metadata", ())
    )
    return ReleaseMetadataProfile(
        expected_version=raw.get("expected_version"),
        candidate_tag=raw.get("candidate_tag"),
        metadata_files=metadata_files,
        allow_non_authoritative_metadata=allow_non_authoritative,
    )


def _hub(raw: Mapping[str, Any], default_timeout_minutes: int) -> HubProfile:
    return HubProfile(
        kubeconfig=str(raw["kubeconfig"]),
        context=str(raw["context"]),
        acm_namespace=str(raw.get("acm_namespace", "open-cluster-management")),
        role_label_selector=raw.get("role_label_selector"),
        timeout_minutes=(
            int(raw["timeout_minutes"])
            if raw.get("timeout_minutes") is not None
            else default_timeout_minutes
        ),
    )


def _managed_clusters(
    raw: Mapping[str, Any], require_observability: bool
) -> ManagedClustersProfile:
    return ManagedClustersProfile(
        expected_names=tuple(str(name) for name in raw.get("expected_names") or ()),
        expected_count=(
            int(raw["expected_count"])
            if raw.get("expected_count") is not None
            else None
        ),
        contexts={
            str(name): str(context)
            for name, context in (raw.get("contexts") or {}).items()
        },
        require_observability=bool(
            raw.get("require_observability", require_observability)
        ),
    )


def _stream(raw: Mapping[str, Any]) -> StreamProfile:
    stream_id = str(raw["id"])
    default_required = stream_id in {"python", "ansible"}
    return StreamProfile(
        id=stream_id,
        enabled=bool(raw.get("enabled", True)),
        required=bool(raw.get("required", default_required)),
        env={str(key): str(value) for key, value in (raw.get("env") or {}).items()},
        extra_args=tuple(str(item) for item in raw.get("extra_args", ())),
    )


def _scenario(raw: Mapping[str, Any], default_timeout_minutes: int) -> ScenarioProfile:
    return ScenarioProfile(
        id=str(raw["id"]),
        required=raw.get("required"),
        streams=tuple(str(item) for item in raw.get("streams", ())),
        cycles=int(raw.get("cycles", 1)),
        timeout_minutes=(
            int(raw["timeout_minutes"])
            if raw.get("timeout_minutes") is not None
            else default_timeout_minutes
        ),
        skip_reason=raw.get("skip_reason"),
    )


def _argocd_selector(raw: Mapping[str, Any]) -> ArgoCDApplicationSelector:
    return ArgoCDApplicationSelector(
        match_labels={
            str(key): str(value)
            for key, value in require_mapping(
                raw.get("match_labels", {}), "match_labels"
            ).items()
        }
    )


def _argocd(raw: Mapping[str, Any]) -> ArgoCDProfile:
    return ArgoCDProfile(
        mandatory=bool(raw.get("mandatory", True)),
        namespaces=tuple(str(item) for item in raw.get("namespaces", ())),
        application_selectors=tuple(
            _argocd_selector(require_mapping(item, "argocd.application_selectors[]"))
            for item in raw.get("application_selectors", ())
        ),
        expected_pause=bool(raw.get("expected_pause", True)),
        expected_resume=bool(raw.get("expected_resume", True)),
        managed_conflict_allowlist=tuple(
            str(item) for item in raw.get("managed_conflict_allowlist", ())
        ),
    )


def _lab_readiness(
    raw: Mapping[str, Any], argocd_mandatory: bool
) -> BaselineLabReadinessProfile:
    return BaselineLabReadinessProfile(
        required=bool(raw.get("required", True)),
        required_crds=tuple(str(item) for item in raw.get("required_crds", ())),
        backup_storage_location=_required_flag(
            require_mapping(
                raw.get("backup_storage_location", {}),
                "baseline.lab_readiness.backup_storage_location",
            )
        ),
        argocd_fixture=_required_flag(
            require_mapping(
                raw.get("argocd_fixture", {}), "baseline.lab_readiness.argocd_fixture"
            ),
            default=argocd_mandatory,
        ),
    )


def _static_gates(raw: Mapping[str, Any]) -> BaselineStaticGatesProfile:
    return BaselineStaticGatesProfile(
        required=bool(raw.get("required", True)),
        optional_gate_ids=tuple(str(item) for item in raw.get("optional_gate_ids", ())),
    )


def _baseline(raw: Mapping[str, Any], argocd_mandatory: bool) -> BaselineProfile:
    observability = _required_flag(
        require_mapping(raw.get("observability", {}), "baseline.observability")
    )
    return BaselineProfile(
        initial_primary=str(raw["initial_primary"]),
        final_primary=str(raw.get("final_primary", raw["initial_primary"])),
        backup_schedule=_required_flag(
            require_mapping(raw.get("backup_schedule", {}), "baseline.backup_schedule")
        ),
        restore=_required_flag(
            require_mapping(raw.get("restore", {}), "baseline.restore")
        ),
        observability=observability,
        rbac=_required_flag(require_mapping(raw.get("rbac", {}), "baseline.rbac")),
        lab_readiness=_lab_readiness(
            require_mapping(raw.get("lab_readiness", {}), "baseline.lab_readiness"),
            argocd_mandatory,
        ),
        static_gates=_static_gates(
            require_mapping(raw.get("static_gates", {}), "baseline.static_gates")
        ),
    )


def _limits(raw: Mapping[str, Any]) -> LimitsProfile:
    return LimitsProfile(
        max_cycles=int(raw.get("max_cycles", 1)),
        default_timeout_minutes=int(raw.get("default_timeout_minutes", 120)),
        cooldown_seconds=int(raw.get("cooldown_seconds", 0)),
        soak_duration_minutes=int(raw.get("soak_duration_minutes", 0)),
        max_tolerated_failures=int(raw.get("max_tolerated_failures", 0)),
        artifact_retention_days=int(raw.get("artifact_retention_days", 30)),
    )


def _recovery(raw: Mapping[str, Any]) -> RecoveryProfile:
    cleanup = require_mapping(
        raw.get("allowed_destructive_cleanup", {}),
        "recovery.allowed_destructive_cleanup",
    )
    return RecoveryProfile(
        pre_run_heal_passes=int(raw.get("pre_run_heal_passes", 1)),
        post_failure_passes_per_mutating_scenario=int(
            raw.get("post_failure_passes_per_mutating_scenario", 1)
        ),
        total_budget_minutes=int(raw.get("total_budget_minutes", 30)),
        allowed_destructive_cleanup=RecoveryCleanupProfile(
            resources=tuple(str(item) for item in cleanup.get("resources", ()))
        ),
        rbac_actions=tuple(
            str(item)
            for item in raw.get("rbac_actions", ("no_bootstrap", "revalidate"))
        ),
        hard_stop_on=tuple(
            str(item)
            for item in raw.get(
                "hard_stop_on",
                (
                    "hub_role_restore_unproven",
                    "argocd_resume_unproven",
                    "rbac_bootstrap_unproven",
                    "final_baseline_unproven",
                ),
            )
        ),
    )


def _artifacts(raw: Mapping[str, Any], limits: LimitsProfile) -> ArtifactsProfile:
    redaction = require_mapping(raw.get("redaction", {}), "artifacts.redaction")
    return ArtifactsProfile(
        root=str(raw.get("root", "artifacts/release")),
        capture_stdout=bool(raw.get("capture_stdout", True)),
        capture_stderr=bool(raw.get("capture_stderr", True)),
        capture_cluster_snapshots=bool(raw.get("capture_cluster_snapshots", False)),
        cluster_snapshot_mode=str(raw.get("cluster_snapshot_mode", "allowlist")),
        redaction=RedactionProfile(
            required=bool(redaction.get("required", True)),
            fail_on_unredacted_secret=bool(
                redaction.get("fail_on_unredacted_secret", True)
            ),
        ),
        compress_after_run=bool(raw.get("compress_after_run", False)),
        retention_days=int(raw.get("retention_days", limits.artifact_retention_days)),
    )


def load_profile(path: str | Path) -> LoadProfileResult:
    profile_path = Path(path)
    raw, sha256 = _read_yaml(profile_path)
    validate_top_level(raw, str(profile_path))
    validate_profile_contents(raw, str(profile_path))

    release_raw = (
        require_mapping(raw["release"], "release") if "release" in raw else None
    )
    if release_raw is not None:
        validate_release(release_raw, str(profile_path))

    limits_raw = require_mapping(raw["limits"], "limits")
    validate_limits(limits_raw, str(profile_path))
    limits = _limits(limits_raw)

    argocd_raw = require_mapping(raw["argocd"], "argocd")
    validate_argocd(argocd_raw, str(profile_path))
    argocd = _argocd(argocd_raw)

    baseline_raw = require_mapping(raw["baseline"], "baseline")
    validate_baseline(baseline_raw, str(profile_path))
    baseline = _baseline(baseline_raw, argocd.mandatory)

    recovery_raw = require_mapping(raw["recovery"], "recovery")
    validate_recovery(recovery_raw, str(profile_path))
    recovery = _recovery(recovery_raw)

    artifacts_raw = require_mapping(raw["artifacts"], "artifacts")
    validate_artifacts(artifacts_raw, str(profile_path))
    artifacts = _artifacts(artifacts_raw, limits)

    hubs_raw = require_mapping(raw["hubs"], "hubs")
    validate_hubs(hubs_raw, str(profile_path))
    stream_items = [
        require_mapping(item, "streams[]")
        for item in require_sequence(raw["streams"], "streams")
    ]
    for index, item in enumerate(stream_items):
        validate_stream(item, index)
    enabled_streams = {
        str(item["id"]) for item in stream_items if item.get("enabled", True)
    }

    managed_raw = require_mapping(raw["managed_clusters"], "managed_clusters")
    validate_managed_clusters(managed_raw)
    managed_clusters = _managed_clusters(managed_raw, baseline.observability.required)

    scenario_items = [
        require_mapping(item, "scenarios[]")
        for item in require_sequence(raw["scenarios"], "scenarios")
    ]
    for index, item in enumerate(scenario_items):
        validate_scenario(item, index, enabled_streams, limits.max_cycles)

    profile = ReleaseProfile(
        profile_version=1,
        name=str(raw["name"]),
        raw=raw,
        release=_release(release_raw),
        hubs={
            "primary": _hub(
                require_mapping(hubs_raw["primary"], "hubs.primary"),
                limits.default_timeout_minutes,
            ),
            "secondary": _hub(
                require_mapping(hubs_raw["secondary"], "hubs.secondary"),
                limits.default_timeout_minutes,
            ),
        },
        managed_clusters=managed_clusters,
        streams=tuple(_stream(item) for item in stream_items),
        scenarios=tuple(
            _scenario(item, limits.default_timeout_minutes) for item in scenario_items
        ),
        argocd=argocd,
        baseline=baseline,
        limits=limits,
        recovery=recovery,
        artifacts=artifacts,
    )
    return LoadProfileResult(path=profile_path, sha256=sha256, profile=profile)
