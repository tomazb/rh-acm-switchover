from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .models import ControllerDecision, DesiredRoleState, ObservedRoleState, SegmentPlan

_SENSITIVE_KEYWORDS = ("kubeconfig", "token", "secret", "credential")
_TMP_PATH_PREFIX = f"/{'tmp'}/"


def _is_unredacted_sensitive_string(value: str) -> bool:
    if value == "[REDACTED]":
        return False
    lowered = value.lower()
    if any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS):
        return True
    return lowered.startswith(("/home/", _TMP_PATH_PREFIX, "~/.kube/")) or "/.kube/" in lowered


def _contains_unredacted_sensitive_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS) and child != "[REDACTED]":
                if not lowered.endswith("_sha256"):
                    return True
            if _contains_unredacted_sensitive_metadata(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_unredacted_sensitive_metadata(item) for item in value)
    if isinstance(value, str):
        return _is_unredacted_sensitive_string(value)
    return False


def _desired_role_state_payload(state: DesiredRoleState) -> dict[str, str]:
    return {
        "primary_physical_hub": state.primary_physical_hub.value,
        "secondary_physical_hub": state.secondary_physical_hub.value,
    }


def _observed_role_state_payload(state: ObservedRoleState | None) -> dict[str, str | None]:
    if state is None:
        return {
            "primary_physical_hub": None,
            "secondary_physical_hub": None,
            "ambiguity_reason": None,
        }
    return {
        "primary_physical_hub": state.primary_physical_hub.value if state.primary_physical_hub else None,
        "secondary_physical_hub": state.secondary_physical_hub.value if state.secondary_physical_hub else None,
        "ambiguity_reason": state.ambiguity_reason,
    }


def _generated_profile_payload(generated_profile_ref: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if generated_profile_ref is None:
        return None

    payload = json.loads(json.dumps(dict(generated_profile_ref), sort_keys=True))
    hubs = payload.get("hubs")
    if payload.get("redaction_status") != "redacted" or not isinstance(hubs, dict):
        raise ValueError("generated profile metadata must be redacted before artifact construction")
    for hub in hubs.values():
        if not isinstance(hub, dict) or hub.get("kubeconfig_reference") != "[REDACTED]":
            raise ValueError("generated profile metadata must be redacted before artifact construction")
    if _contains_unredacted_sensitive_metadata(payload):
        raise ValueError("generated profile metadata must be redacted before artifact construction")
    return payload


def build_segment_artifact(
    *,
    plan: SegmentPlan,
    observed_initial_role_state: ObservedRoleState,
    desired_initial_role_state: DesiredRoleState,
    observed_final_role_state: ObservedRoleState | None,
    controller_decision: ControllerDecision,
    managed_cluster_summary: dict[str, Any],
    generated_profile_ref: Mapping[str, Any] | None = None,
    generated_profile_hash: str | None = None,
    scenario_classification: str | None = None,
    identity_verification_summary: Mapping[str, Any] | None = None,
    fake_execution_result: Mapping[str, Any] | None = None,
    redaction_status: str,
) -> dict[str, Any]:
    """Build the provisional Phase 1 segment artifact payload.

    Persistence and final JSON schema are intentionally out of scope for Phase 1. The payload keeps the same
    information the future writer will need without committing generated profiles or live artifacts.
    """
    artifact = {
        "schema_version": 1,
        "segment_id": plan.segment_id,
        "scenario_id": plan.scenario_id,
        "scenario_classification": scenario_classification,
        "mutates_lab": plan.mutates_lab,
        "identity_verification_summary": dict(identity_verification_summary or {}),
        "observed_initial_role_state": _observed_role_state_payload(observed_initial_role_state),
        "desired_initial_role_state": _desired_role_state_payload(desired_initial_role_state),
        "expected_initial_role_state": _desired_role_state_payload(plan.expected_initial_role_state),
        "expected_final_role_state": _desired_role_state_payload(plan.expected_final_role_state),
        "observed_final_role_state": _observed_role_state_payload(observed_final_role_state),
        "generated_profile_hash": generated_profile_hash,
        "controller_decision": controller_decision.decision.name,
        "safe_to_continue": controller_decision.safe_to_continue,
        "reason": controller_decision.reason,
        "recovery_hint": controller_decision.recovery_hint,
        "generated_profile": _generated_profile_payload(generated_profile_ref),
        "fake_execution_result": dict(fake_execution_result or {}),
        "managed_cluster_evidence_summary": managed_cluster_summary,
        "redaction_status": redaction_status,
    }
    if _contains_unredacted_sensitive_metadata(artifact):
        raise ValueError("segment artifact payload contains unredacted sensitive metadata")
    return artifact
