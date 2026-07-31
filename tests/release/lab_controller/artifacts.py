from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from tests.release.reporting.redaction import RedactionError, sanitize_text

from .models import ControllerDecision, DesiredRoleState, ObservedRoleState, SegmentPlan

_SENSITIVE_KEYWORDS = ("kubeconfig", "token", "secret", "credential", "password")
_TMP_PATH_PREFIX = f"/{'tmp'}/"
_URL_PREFIXES = ("http://", "https://")
_CLUSTER_ID_MARKERS = ("cluster-id", "cluster_id")
_URL_PATTERN = re.compile(r"https?://[^\s,;)'\"}]+", re.IGNORECASE)
_KUBECONFIG_PATH_PATTERN = re.compile(
    r"(?:/home/[^\s,;)'\"}]+|~/\.kube/[^\s,;)'\"}]+|/tmp/[^\s,;)'\"}]+|[^\s,;)'\"}]*[/\\]\.kube[/\\][^\s,;)'\"}]+)"
)
_STRICT_SENSITIVE_KEY_MARKERS = (
    "kubeconfig",
    "token",
    "secret",
    "credential",
    "password",
    "endpoint",
    "api_url",
    "api_server",
    "context",
    "runtime_handle",
    "error",
    "exception",
    "traceback",
    "command",
    "argv",
    "shell",
)
_STRICT_MAX_STRING_LENGTH = 2048
_STRICT_MAX_DEPTH = 32
_STRICT_MAX_NODES = 10000
_STRICT_CREDENTIAL_SCHEME_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])bearer[ \t\r\n]+(?=\S)",
    re.IGNORECASE,
)


def _shared_redaction_would_change(value: str) -> bool:
    try:
        sanitized = sanitize_text(value)
    except RedactionError:
        return True
    return sanitized.text != value


def _is_unredacted_sensitive_string(value: str) -> bool:
    if value == "[REDACTED]":
        return False
    if _shared_redaction_would_change(value):
        return True
    lowered = value.lower()
    if _URL_PATTERN.search(value) or _KUBECONFIG_PATH_PATTERN.search(value):
        return True
    if any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS):
        return True
    if lowered.startswith(_URL_PREFIXES):
        return True
    if any(marker in lowered for marker in _CLUSTER_ID_MARKERS):
        return True
    return lowered.startswith(("/home/", _TMP_PATH_PREFIX, "~/.kube/")) or "/.kube/" in lowered


def _is_unredacted_sensitive_key(value: str) -> bool:
    if value == "[REDACTED]":
        return False
    lowered = value.lower()
    return bool(
        _URL_PATTERN.search(value)
        or _KUBECONFIG_PATH_PATTERN.search(value)
        or any(marker in lowered for marker in _CLUSTER_ID_MARKERS)
    )


def _contains_credential_scheme_value(value: str) -> bool:
    return bool(_STRICT_CREDENTIAL_SCHEME_VALUE_PATTERN.search(value))


def sanitize_artifact_key(value: object) -> str:
    """Return a publishable artifact mapping key without raw path, URL, or cluster identity material."""
    key = str(value)
    if _is_unredacted_sensitive_key(key):
        return "[REDACTED]"
    return key


def _contains_unredacted_sensitive_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if _is_unredacted_sensitive_key(key_text):
                return True
            lowered = key_text.lower()
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


def validate_artifact_payload_redacted(payload: Mapping[str, Any]) -> None:
    """Reject publishable artifact payloads that still contain sensitive metadata."""
    if _contains_unredacted_sensitive_metadata(payload):
        raise ValueError("artifact payload contains unredacted sensitive metadata")


def sanitize_artifact_text(value: str | None) -> str | None:
    """Return a publishable text value suitable for run-level artifact metadata."""
    if value is None:
        return None
    try:
        sanitized = sanitize_text(value).text
    except RedactionError:
        return "[REDACTED]"
    sanitized = _URL_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _KUBECONFIG_PATH_PATTERN.sub("[REDACTED]", sanitized)
    if _is_unredacted_sensitive_string(sanitized):
        return "[REDACTED]"
    return sanitized


def sanitize_artifact_payload(value: Any) -> Any:
    """Return a recursively sanitized payload suitable for publishable controller artifacts."""
    if isinstance(value, dict):
        return {sanitize_artifact_key(key): sanitize_artifact_payload(child) for key, child in value.items()}
    if isinstance(value, list):
        return [sanitize_artifact_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_artifact_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_artifact_text(value)
    return value


def strict_recursive_artifact_audit(value: Any) -> tuple[Any, dict[str, Any]]:
    """Return a JSON-safe deep copy only when every key and value is publication-safe.

    This is the all-or-nothing Phase 9B publication gate. Unlike the older best-effort sanitizer,
    it rejects unsupported values instead of stringifying or partially redacting them. The caller
    must not publish an artifact when this function raises ``ValueError``.
    """

    node_count = 0
    active_container_ids: set[int] = set()

    def _audit(child: Any, *, path: tuple[str, ...]) -> Any:
        nonlocal node_count
        node_count += 1
        if len(path) > _STRICT_MAX_DEPTH:
            raise ValueError("artifact publication audit exceeded maximum depth")
        if node_count > _STRICT_MAX_NODES:
            raise ValueError("artifact publication audit exceeded maximum node count")
        if type(child) is dict:
            container_id = id(child)
            if container_id in active_container_ids:
                raise ValueError("artifact publication audit rejected a cyclic mapping")
            active_container_ids.add(container_id)
            try:
                audited: dict[str, Any] = {}
                for key, nested in child.items():
                    if not isinstance(key, str):
                        raise ValueError("artifact publication audit rejected a non-string mapping key")
                    lowered = key.lower()
                    if any(marker in lowered for marker in _STRICT_SENSITIVE_KEY_MARKERS):
                        raise ValueError("artifact publication audit rejected a sensitive mapping key")
                    if _is_unredacted_sensitive_key(key) or _contains_control_characters(key):
                        raise ValueError("artifact publication audit rejected an unsafe mapping key")
                    audited[key] = _audit(nested, path=(*path, key))
                return audited
            finally:
                active_container_ids.remove(container_id)
        if type(child) is list:
            container_id = id(child)
            if container_id in active_container_ids:
                raise ValueError("artifact publication audit rejected a cyclic list")
            active_container_ids.add(container_id)
            try:
                return [_audit(item, path=(*path, str(index))) for index, item in enumerate(child)]
            finally:
                active_container_ids.remove(container_id)
        if child is None or isinstance(child, bool) or isinstance(child, int):
            return child
        if isinstance(child, float):
            if not math.isfinite(child):
                raise ValueError("artifact publication audit rejected a non-finite number")
            return child
        if isinstance(child, str):
            if len(child) > _STRICT_MAX_STRING_LENGTH or _contains_control_characters(child):
                raise ValueError("artifact publication audit rejected an unsafe string")
            if _contains_credential_scheme_value(child) or _is_unredacted_sensitive_string(child):
                raise ValueError("artifact publication audit rejected sensitive string content")
            return child
        raise ValueError("artifact publication audit rejected a non-JSON-safe value")

    audited_payload = _audit(value, path=())
    return audited_payload, {
        "status": "passed",
        "recursive": True,
        "node_count": node_count,
        "unsupported_value_count": 0,
        "sensitive_value_count": 0,
    }


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 and character not in "\t\n\r" for character in value)


def _payload_claims_live_certification_evidence(payload: Mapping[str, Any]) -> bool:
    if bool(payload.get("live_certification_evidence", False)):
        return True
    execution_summary = payload.get("execution_summary", {})
    if not isinstance(execution_summary, Mapping):
        return False
    if bool(execution_summary.get("live_certification_evidence", False)):
        return True
    execution_evidence = execution_summary.get("execution_evidence", {})
    if isinstance(execution_evidence, Mapping) and bool(execution_evidence.get("live_certification_evidence", False)):
        return True
    return False


def _payload_claims_dry_run_real_execution(payload: Mapping[str, Any]) -> bool:
    return bool(payload.get("dry_run", False) and payload.get("real_execution_evidence", False))


def _gitops_evidence_payload(gitops_summary: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = sanitize_artifact_payload(
        dict(
            gitops_summary
            or {
                "evaluated": False,
                "final_decision": "NOT_EVALUATED",
            }
        )
    )
    if _payload_claims_live_certification_evidence(payload):
        raise ValueError("GitOps segment artifacts cannot claim live certification evidence")
    payload["live_certification_evidence"] = False
    payload["not_live_acm_certification_evidence"] = True
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
    gitops_summary: Mapping[str, Any] | None = None,
    fake_execution_result: Mapping[str, Any] | None = None,
    execution_request_summary: Mapping[str, Any] | None = None,
    redaction_status: str,
) -> dict[str, Any]:
    """Build the provisional Phase 1 segment artifact payload.

    Persistence and final JSON schema are intentionally out of scope for Phase 1. The payload keeps the same
    information the future writer will need without committing generated profiles or live artifacts.
    """
    execution_request_payload = sanitize_artifact_payload(dict(execution_request_summary or {}))
    if _payload_claims_live_certification_evidence(execution_request_payload):
        raise ValueError("segment artifacts cannot claim live certification evidence in current controller phases")
    if _payload_claims_dry_run_real_execution(execution_request_payload):
        raise ValueError("dry-run segment artifacts cannot claim real execution evidence")
    execution_summary = execution_request_payload.get("execution_summary", {})
    if not isinstance(execution_summary, dict):
        execution_summary = {}
    execution_evidence = execution_summary.get("execution_evidence", {})
    if not isinstance(execution_evidence, dict):
        execution_evidence = {}
    execution_gate = execution_evidence.get("execution_gate", {})
    if not isinstance(execution_gate, dict):
        execution_gate = {}
    sanitized_command_summary = execution_evidence.get("sanitized_command_summary", {})
    if not isinstance(sanitized_command_summary, dict):
        sanitized_command_summary = {}
    artifact = {
        "schema_version": 1,
        "segment_id": plan.segment_id,
        "scenario_id": plan.scenario_id,
        "scenario_classification": scenario_classification,
        "mutates_lab": plan.mutates_lab,
        "identity_verification_summary": dict(identity_verification_summary or {}),
        "gitops_evidence": _gitops_evidence_payload(gitops_summary),
        "observed_initial_role_state": _observed_role_state_payload(observed_initial_role_state),
        "desired_initial_role_state": _desired_role_state_payload(desired_initial_role_state),
        "expected_initial_role_state": _desired_role_state_payload(plan.expected_initial_role_state),
        "expected_final_role_state": _desired_role_state_payload(plan.expected_final_role_state),
        "observed_final_role_state": _observed_role_state_payload(observed_final_role_state),
        "generated_profile_hash": generated_profile_hash,
        "controller_decision": controller_decision.decision.name,
        "safe_to_continue": controller_decision.safe_to_continue,
        "reason": sanitize_artifact_text(controller_decision.reason),
        "recovery_hint": sanitize_artifact_text(controller_decision.recovery_hint),
        "generated_profile": _generated_profile_payload(generated_profile_ref),
        "fake_execution_result": dict(fake_execution_result or {}),
        "execution_backend": execution_request_payload.get("execution_backend"),
        "execution_mode": execution_request_payload.get("execution_mode"),
        "dry_run": execution_request_payload.get("dry_run"),
        "intended_pytest_target": execution_request_payload.get("intended_pytest_target"),
        "intended_release_mode": execution_request_payload.get("intended_release_mode"),
        "intended_scenario": execution_request_payload.get("intended_scenario"),
        "intended_stream": execution_request_payload.get("intended_stream"),
        "execution_request_redaction_status": execution_request_payload.get("execution_request_redaction_status"),
        "execution_summary": execution_request_payload.get("execution_summary", {}),
        "execution_gate": execution_gate,
        "command_runner_kind": execution_evidence.get("command_runner_kind"),
        "executed": bool(execution_evidence.get("executed", False)),
        "return_code": execution_evidence.get("return_code"),
        "timeout": bool(execution_evidence.get("timeout", False)),
        "stdout_summary": execution_evidence.get("stdout_summary", ""),
        "stderr_summary": execution_evidence.get("stderr_summary", ""),
        "execution_evidence_type": execution_evidence.get(
            "execution_evidence_type",
            "dry_run_materialization" if execution_request_payload.get("dry_run") else "none",
        ),
        "sanitized_command_summary": sanitized_command_summary,
        "materialization_status": execution_request_payload.get("materialization_status", "not_materialized"),
        "materialized_invocation_summary": execution_request_payload.get("materialized_invocation_summary", {}),
        "materialized_argv_summary": execution_request_payload.get("materialized_argv_summary", {}),
        "environment_plan_summary": execution_request_payload.get("environment_plan_summary", {}),
        "profile_compatibility_summary": execution_request_payload.get("profile_compatibility_summary", {}),
        "artifact_directory_summary": execution_request_payload.get("artifact_directory_summary", {}),
        "future_execution_eligibility": execution_request_payload.get("future_execution_eligibility", {}),
        "execution_request": execution_request_payload,
        "real_execution_evidence": execution_request_payload.get("real_execution_evidence", False),
        "live_certification_evidence": execution_request_payload.get("live_certification_evidence", False),
        "managed_cluster_evidence_summary": managed_cluster_summary,
        "redaction_status": redaction_status,
    }
    validate_artifact_payload_redacted(artifact)
    return artifact
