from __future__ import annotations

import hashlib
import json
from typing import Any

from .decisions import no_go, pass_decision, recovery_required
from .models import (
    GeneratedProfile,
    ObservedRoleState,
    PhysicalHubConfig,
    PhysicalHubLabel,
    StableLabConfig,
)


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_proven_role_state(role_state: ObservedRoleState) -> tuple[PhysicalHubLabel, PhysicalHubLabel]:
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


def build_role_aware_profile(
    lab_config: StableLabConfig,
    role_state: ObservedRoleState,
    *,
    scenario_ids: tuple[str, ...] | None = None,
    final_role_state: ObservedRoleState | None = None,
) -> GeneratedProfile:
    primary_label, secondary_label = _require_proven_role_state(role_state)
    primary_hub = lab_config.physical_hubs[primary_label]
    secondary_hub = lab_config.physical_hubs[secondary_label]
    scenarios = scenario_ids or lab_config.scenario_ids

    profile_data: dict[str, Any] = {
        "profile_version": 1,
        "name": lab_config.profile_name,
        "hubs": {
            "primary": _hub_entry(primary_hub),
            "secondary": _hub_entry(secondary_hub),
        },
        "managed_clusters": {
            "expected_names": list(lab_config.expected_managed_cluster_names),
        },
        "streams": [{"id": stream_id} for stream_id in lab_config.enabled_streams],
        "scenarios": [{"id": scenario_id} for scenario_id in scenarios],
        "argocd": {
            "mandatory": False,
            "namespaces": list(lab_config.argocd_namespaces),
        },
        "baseline": {
            "initial_primary": "primary",
            "final_primary": _final_primary_value(role_state, final_role_state),
        },
        "limits": {},
        "recovery": {},
        "artifacts": {
            "root": lab_config.artifact_root,
        },
    }
    sha256 = _stable_hash(profile_data)
    logical_to_physical = {
        "primary": primary_label.value,
        "secondary": secondary_label.value,
    }
    metadata = {
        "schema_version": 1,
        "profile_sha256": sha256,
        "logical_to_physical": logical_to_physical,
        "hubs": {
            "primary": {
                "physical_label": primary_label.value,
                "context": primary_hub.context_name,
                "kubeconfig_reference": primary_hub.kubeconfig_reference,
            },
            "secondary": {
                "physical_label": secondary_label.value,
                "context": secondary_hub.context_name,
                "kubeconfig_reference": secondary_hub.kubeconfig_reference,
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
        "profile_sha256": generated_profile.sha256,
        "logical_to_physical": dict(generated_profile.logical_to_physical),
        "hubs": {},
        "redaction_status": "redacted",
    }
    hubs: dict[str, Any] = {}
    for logical_role, hub in generated_profile.metadata["hubs"].items():
        hubs[logical_role] = {
            "physical_label": hub["physical_label"],
            "context": hub["context"],
            "kubeconfig_reference": "[REDACTED]",
        }
    redacted["hubs"] = hubs
    return redacted


def validate_profile_role_mapping(
    profile_data: dict[str, Any],
    lab_config: StableLabConfig,
    role_state: ObservedRoleState,
):
    if not role_state.is_proven or role_state.primary_physical_hub is None or role_state.secondary_physical_hub is None:
        return recovery_required(f"cannot validate profile against ambiguous role state: {role_state.ambiguity_reason}")

    expected_primary = lab_config.physical_hubs[role_state.primary_physical_hub]
    expected_secondary = lab_config.physical_hubs[role_state.secondary_physical_hub]
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
