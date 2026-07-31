from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .decisions import no_go, pass_decision, recovery_required
from .models import (
    GeneratedProfile,
    HubIdentityEvidence,
    LabArgoCDSettings,
    LabArtifactSettings,
    LabReleaseMetadata,
    ManagedClusterInventory,
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    SegmentDecision,
    SegmentPlan,
    StableLabConfig,
)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_proven_role_state(role_state: ObservedRoleState) -> tuple[PhysicalHubLabel, PhysicalHubLabel]:
    if (
        role_state.primary_physical_hub is not None
        and role_state.secondary_physical_hub is not None
        and role_state.primary_physical_hub is role_state.secondary_physical_hub
    ):
        raise ValueError("cannot generate role-aware profile: primary and secondary physical hubs must differ")
    if not role_state.is_proven or role_state.primary_physical_hub is None or role_state.secondary_physical_hub is None:
        reason = role_state.ambiguity_reason or "role state is not proven"
        raise ValueError(f"cannot generate role-aware profile: {reason}")
    return role_state.primary_physical_hub, role_state.secondary_physical_hub


def _hub_entry(hub: PhysicalHubConfig) -> dict[str, str]:
    return {
        "kubeconfig": hub.kubeconfig_reference,
        "context": hub.context_name,
        "acm_namespace": hub.acm_namespace,
    }


def _managed_clusters(lab_config: StableLabConfig) -> ManagedClusterInventory:
    managed_clusters = lab_config.managed_clusters
    if managed_clusters is None:
        return ManagedClusterInventory(expected_names=lab_config.expected_managed_cluster_names)
    return managed_clusters


def _argocd_settings(lab_config: StableLabConfig) -> LabArgoCDSettings:
    argocd = lab_config.argocd
    if argocd is None:
        return LabArgoCDSettings(mandatory=False, namespaces=lab_config.argocd_namespaces)
    return argocd


def _artifact_settings(lab_config: StableLabConfig, artifact_root: str | None) -> LabArtifactSettings:
    artifacts = lab_config.artifacts
    if artifacts is None:
        artifacts = LabArtifactSettings(root=lab_config.artifact_root)
    if artifact_root is None:
        return artifacts
    return LabArtifactSettings(
        root=artifact_root,
        redaction_required=artifacts.redaction_required,
        fail_on_unredacted_secret=artifacts.fail_on_unredacted_secret,
    )


def _release_entry(release: LabReleaseMetadata | None) -> dict[str, Any] | None:
    if release is None:
        return None
    entry: dict[str, Any] = {}
    if release.expected_version is not None:
        entry["expected_version"] = release.expected_version
    if release.candidate_tag is not None:
        entry["candidate_tag"] = release.candidate_tag
    if release.metadata_files:
        entry["metadata_files"] = list(release.metadata_files)
    return entry or None


def _managed_cluster_entry(managed_clusters: ManagedClusterInventory) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "expected_names": list(managed_clusters.expected_names),
        "require_observability": managed_clusters.require_observability,
    }
    if managed_clusters.contexts:
        entry["contexts"] = {str(name): str(context) for name, context in sorted(managed_clusters.contexts.items())}
    return entry


def _argocd_entry(argocd: LabArgoCDSettings) -> dict[str, Any]:
    return {
        "mandatory": argocd.mandatory,
        "namespaces": list(argocd.namespaces),
        "expected_pause": argocd.expected_pause,
        "expected_resume": argocd.expected_resume,
    }


def _artifact_entry(artifacts: LabArtifactSettings) -> dict[str, Any]:
    return {
        "root": artifacts.root,
        "redaction": {
            "required": artifacts.redaction_required,
            "fail_on_unredacted_secret": artifacts.fail_on_unredacted_secret,
        },
    }


def _final_primary_value(
    initial_role_state: ObservedRoleState,
    final_role_state: ObservedRoleState | None,
) -> str:
    if final_role_state is None:
        return "primary"
    initial_primary, initial_secondary = _require_proven_role_state(initial_role_state)
    final_primary, _ = _require_proven_role_state(final_role_state)
    if final_primary is initial_primary:
        return "primary"
    if final_primary is initial_secondary:
        return "secondary"
    raise ValueError(
        "unexpected final primary "
        f"{final_primary.value}; initial primary={initial_primary.value}; initial secondary={initial_secondary.value}"
    )


def _final_role_state_from_plan(segment_plan: SegmentPlan | None) -> ObservedRoleState | None:
    if segment_plan is None:
        return None
    return ObservedRoleState(
        primary_physical_hub=segment_plan.expected_final_role_state.primary_physical_hub,
        secondary_physical_hub=segment_plan.expected_final_role_state.secondary_physical_hub,
    )


def _scenario_ids(
    segment_plan: SegmentPlan | None,
    scenario_ids: tuple[str, ...] | None,
    lab_config: StableLabConfig,
) -> tuple[str, ...]:
    if scenario_ids is not None:
        return scenario_ids
    if segment_plan is not None:
        return (segment_plan.scenario_id,)
    return lab_config.scenario_ids


def _segment_profile_relative_path(segment_plan: SegmentPlan | None) -> str:
    if segment_plan is None:
        return "generated-profiles/profile.yaml"
    return f"generated-profiles/{segment_plan.segment_id}.yaml"


def _identity_fingerprint_payload(identity: HubIdentityEvidence | None, label: PhysicalHubLabel) -> dict[str, str]:
    if identity is None:
        raise ValueError(f"missing expected identity fingerprint for {label.value}")
    if identity.physical_label is not label:
        raise ValueError(
            f"expected identity fingerprint for {label.value} is labeled {identity.physical_label.value}; "
            "physical hub identities appear swapped"
        )
    if not identity.kube_system_uid or not identity.api_server_fingerprint:
        raise ValueError(f"missing expected identity fingerprint for {label.value}")
    return {
        "physical_label": identity.physical_label.value,
        "kube_system_uid": identity.kube_system_uid,
        "api_server_fingerprint": identity.api_server_fingerprint,
        "context_name": identity.context_name or "",
        "cluster_version": identity.cluster_version or "",
        "acm_evidence": identity.acm_evidence or "",
    }


def _identity_fingerprint_hash(identity: HubIdentityEvidence | None, label: PhysicalHubLabel) -> str:
    return _stable_hash(_identity_fingerprint_payload(identity, label))


def _managed_cluster_hash(managed_clusters: ManagedClusterInventory) -> str:
    return _stable_hash(
        {
            "expected_names": list(managed_clusters.expected_names),
            "contexts": {str(name): str(context) for name, context in sorted(managed_clusters.contexts.items())},
            "require_observability": managed_clusters.require_observability,
        }
    )


def _profile_hash_payload(profile_data: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(profile_data, sort_keys=True))
    hubs = payload.get("hubs", {})
    if isinstance(hubs, dict):
        for role in ("primary", "secondary"):
            hub = hubs.get(role)
            if not isinstance(hub, dict):
                continue
            kubeconfig = str(hub.get("kubeconfig", ""))
            hub["kubeconfig"] = "[HASHED]"
            hub["kubeconfig_sha256"] = _value_hash(kubeconfig)
    return payload


def _profile_content_hash(profile_data: Mapping[str, Any]) -> str:
    return _stable_hash(_profile_hash_payload(profile_data))


def _generated_profile_hash(
    profile_data: Mapping[str, Any],
    *,
    expected_identity_hashes: Mapping[str, str],
    managed_cluster_hash: str,
) -> str:
    return _stable_hash(
        {
            "profile": _profile_hash_payload(profile_data),
            "physical_hub_identity_fingerprints": {
                str(label): str(fingerprint) for label, fingerprint in sorted(expected_identity_hashes.items())
            },
            "expected_managed_clusters_sha256": managed_cluster_hash,
        }
    )


def _require_hub_config(lab_config: StableLabConfig, label: PhysicalHubLabel) -> PhysicalHubConfig:
    hub = lab_config.physical_hubs.get(label)
    if hub is None:
        raise ValueError(f"missing physical hub config for {label.value}")
    if hub.physical_label is not label:
        raise ValueError(
            f"physical hub config for {label.value} is labeled {hub.physical_label.value}; inventory is inconsistent"
        )
    return hub


def build_role_aware_profile(
    lab_config: StableLabConfig,
    role_state: ObservedRoleState,
    *,
    scenario_ids: tuple[str, ...] | None = None,
    final_role_state: ObservedRoleState | None = None,
    segment_plan: SegmentPlan | None = None,
    artifact_root: str | None = None,
) -> GeneratedProfile:
    primary_label, secondary_label = _require_proven_role_state(role_state)
    primary_hub = _require_hub_config(lab_config, primary_label)
    secondary_hub = _require_hub_config(lab_config, secondary_label)
    scenarios = _scenario_ids(segment_plan, scenario_ids, lab_config)
    managed_clusters = _managed_clusters(lab_config)
    argocd = _argocd_settings(lab_config)
    artifacts = _artifact_settings(lab_config, artifact_root)
    expected_identity_hashes = {
        label.value: _identity_fingerprint_hash(_require_hub_config(lab_config, label).expected_identity, label)
        for label in sorted(lab_config.physical_hubs, key=lambda item: item.value)
    }
    managed_cluster_fingerprint = _managed_cluster_hash(managed_clusters)
    if final_role_state is None:
        final_role_state = _final_role_state_from_plan(segment_plan)

    profile_data: dict[str, Any] = {
        "profile_version": 1,
        "name": lab_config.profile_name,
        "hubs": {
            "primary": _hub_entry(primary_hub),
            "secondary": _hub_entry(secondary_hub),
        },
        "managed_clusters": _managed_cluster_entry(managed_clusters),
        "streams": [{"id": stream_id} for stream_id in lab_config.enabled_streams],
        "scenarios": [{"id": scenario_id} for scenario_id in scenarios],
        "argocd": _argocd_entry(argocd),
        "baseline": {
            "initial_primary": "primary",
            "final_primary": _final_primary_value(role_state, final_role_state),
        },
        "limits": {},
        "recovery": {},
        "artifacts": _artifact_entry(artifacts),
    }
    release = _release_entry(lab_config.release)
    if release is not None:
        profile_data["release"] = release

    sha256 = _generated_profile_hash(
        profile_data,
        expected_identity_hashes=expected_identity_hashes,
        managed_cluster_hash=managed_cluster_fingerprint,
    )
    logical_to_physical = {
        "primary": primary_label.value,
        "secondary": secondary_label.value,
    }
    metadata = {
        "schema_version": 2,
        "generated": True,
        "runtime_only": True,
        "profile_sha256": sha256,
        "artifact_profile_path": _segment_profile_relative_path(segment_plan),
        "logical_to_physical": logical_to_physical,
        "expected_managed_clusters_sha256": managed_cluster_fingerprint,
        "physical_hub_identity_fingerprints": expected_identity_hashes,
        "hubs": {
            "primary": {
                "physical_label": primary_label.value,
                "context": primary_hub.context_name,
                "kubeconfig_reference_sha256": _value_hash(primary_hub.kubeconfig_reference),
                "identity_fingerprint_sha256": expected_identity_hashes[primary_label.value],
            },
            "secondary": {
                "physical_label": secondary_label.value,
                "context": secondary_hub.context_name,
                "kubeconfig_reference_sha256": _value_hash(secondary_hub.kubeconfig_reference),
                "identity_fingerprint_sha256": expected_identity_hashes[secondary_label.value],
            },
        },
    }
    return GeneratedProfile(
        profile_data=profile_data,
        sha256=sha256,
        logical_to_physical=logical_to_physical,
        metadata=metadata,
    )


def redact_generated_profile_metadata(generated_profile: GeneratedProfile) -> dict[str, Any]:
    redacted = {
        "schema_version": generated_profile.metadata["schema_version"],
        "generated": True,
        "runtime_only": True,
        "profile_sha256": generated_profile.sha256,
        "artifact_profile_path": generated_profile.metadata["artifact_profile_path"],
        "logical_to_physical": dict(generated_profile.logical_to_physical),
        "expected_managed_clusters_sha256": generated_profile.metadata["expected_managed_clusters_sha256"],
        "physical_hub_identity_fingerprints": dict(generated_profile.metadata["physical_hub_identity_fingerprints"]),
        "hubs": {},
        "redaction_status": "redacted",
    }
    hubs: dict[str, Any] = {}
    for logical_role, hub in generated_profile.metadata["hubs"].items():
        hubs[logical_role] = {
            "physical_label": hub["physical_label"],
            "context": "[REDACTED]",
            "context_sha256": _value_hash(str(hub["context"])),
            "kubeconfig_reference": "[REDACTED]",
            "kubeconfig_reference_sha256": hub["kubeconfig_reference_sha256"],
            "identity_fingerprint_sha256": hub["identity_fingerprint_sha256"],
        }
    redacted["hubs"] = hubs
    return redacted


def write_generated_profile_yaml(generated_profile: GeneratedProfile, path: str | Path) -> Path:
    """Write a deterministic runtime-only generated profile to a caller-provided path."""
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    content = "# Generated runtime-only release profile; do not commit.\n"
    content += yaml.safe_dump(generated_profile.profile_data, sort_keys=True)
    profile_path.write_text(content, encoding="utf-8")
    return profile_path


def validate_profile_role_mapping(
    profile_data: dict[str, Any],
    lab_config: StableLabConfig,
    role_state: ObservedRoleState,
):
    if not role_state.is_proven or role_state.primary_physical_hub is None or role_state.secondary_physical_hub is None:
        return recovery_required(f"cannot validate profile against ambiguous role state: {role_state.ambiguity_reason}")

    expected_primary = _require_hub_config(lab_config, role_state.primary_physical_hub)
    expected_secondary = _require_hub_config(lab_config, role_state.secondary_physical_hub)
    hubs = profile_data.get("hubs", {})
    profile_primary = hubs.get("primary", {})
    profile_secondary = hubs.get("secondary", {})

    if (
        profile_primary.get("context") != expected_primary.context_name
        or profile_primary.get("kubeconfig") != expected_primary.kubeconfig_reference
    ):
        return no_go(
            "stale role-aware profile: hubs.primary does not map to observed primary "
            f"{role_state.primary_physical_hub.value}"
        )
    if (
        profile_secondary.get("context") != expected_secondary.context_name
        or profile_secondary.get("kubeconfig") != expected_secondary.kubeconfig_reference
    ):
        return no_go(
            "stale role-aware profile: hubs.secondary does not map to observed secondary "
            f"{role_state.secondary_physical_hub.value}"
        )
    return pass_decision("role-aware profile matches observed role state")


def _profile_managed_cluster_names(profile_data: Mapping[str, Any]) -> tuple[str, ...]:
    managed_clusters = profile_data.get("managed_clusters", {})
    if not isinstance(managed_clusters, dict):
        return ()
    names = managed_clusters.get("expected_names", ())
    if not isinstance(names, list):
        return ()
    return tuple(str(name) for name in names)


def _metadata_role_mapping(metadata: Mapping[str, Any]) -> dict[str, str]:
    mapping = metadata.get("logical_to_physical", {})
    if not isinstance(mapping, dict):
        return {}
    return {str(role): str(label) for role, label in mapping.items()}


def validate_generated_profile_freshness(
    generated_profile: GeneratedProfile,
    lab_config: StableLabConfig,
    role_state: ObservedRoleState,
):
    """Reject a generated profile whose recorded role, identity, cluster, or hash data is stale."""
    if generated_profile.metadata.get("profile_sha256") != generated_profile.sha256:
        return no_go("stale role-aware profile: profile hash metadata does not match generated profile")

    role_decision = validate_profile_role_mapping(generated_profile.profile_data, lab_config, role_state)
    if role_decision.decision is not SegmentDecision.PASS:
        return role_decision

    primary_label, secondary_label = _require_proven_role_state(role_state)
    expected_mapping = {
        "primary": primary_label.value,
        "secondary": secondary_label.value,
    }
    if _metadata_role_mapping(generated_profile.metadata) != expected_mapping:
        return no_go("stale role-aware profile: recorded logical role mapping does not match observed role state")

    managed_clusters = _managed_clusters(lab_config)
    managed_cluster_fingerprint = _managed_cluster_hash(managed_clusters)
    if _profile_managed_cluster_names(generated_profile.profile_data) != managed_clusters.expected_names:
        return no_go("stale role-aware profile: managed cluster set does not match current lab config")
    if generated_profile.metadata.get("expected_managed_clusters_sha256") != managed_cluster_fingerprint:
        return no_go("stale role-aware profile: managed cluster set fingerprint does not match current lab config")

    fingerprints = generated_profile.metadata.get("physical_hub_identity_fingerprints", {})
    if not isinstance(fingerprints, dict):
        return no_go("stale role-aware profile: missing physical hub identity fingerprints")
    expected_identity_hashes: dict[str, str] = {}
    for label in sorted(lab_config.physical_hubs, key=lambda item: item.value):
        try:
            expected_hash = _identity_fingerprint_hash(_require_hub_config(lab_config, label).expected_identity, label)
        except ValueError as exc:
            return no_go(f"stale role-aware profile: {exc}")
        expected_identity_hashes[label.value] = expected_hash
        if fingerprints.get(label.value) != expected_hash:
            return no_go(
                "stale role-aware profile: physical hub identity fingerprint does not match current lab config "
                f"for {label.value}"
            )

    actual_hash = _generated_profile_hash(
        generated_profile.profile_data,
        expected_identity_hashes=expected_identity_hashes,
        managed_cluster_hash=managed_cluster_fingerprint,
    )
    if actual_hash != generated_profile.sha256:
        return no_go("stale role-aware profile: profile hash does not match generated profile data")

    return pass_decision("generated role-aware profile matches current proven role and identity state")
