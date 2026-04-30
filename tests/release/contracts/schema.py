from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .models import ProfileValidationError

REQUIRED_TOP_LEVEL_KEYS = {
    "profile_version",
    "name",
    "hubs",
    "managed_clusters",
    "streams",
    "scenarios",
    "argocd",
    "baseline",
    "limits",
    "recovery",
    "artifacts",
}
OPTIONAL_TOP_LEVEL_KEYS = {"release"}
ALLOWED_TOP_LEVEL_KEYS = REQUIRED_TOP_LEVEL_KEYS | OPTIONAL_TOP_LEVEL_KEYS
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

KNOWN_STREAMS = {"bash", "python", "ansible"}
KNOWN_SCENARIOS = {
    "static-gates",
    "lab-readiness",
    "baseline-check",
    "preflight",
    "python-passive-switchover",
    "ansible-passive-switchover",
    "python-restore-only",
    "ansible-restore-only",
    "argocd-managed-switchover",
    "runtime-parity",
    "final-baseline-check",
    "full-restore",
    "checkpoint-resume",
    "decommission",
    "failure-injection",
    "soak",
}
REQUIRED_FULL_RELEASE_SCENARIOS = {
    "static-gates",
    "lab-readiness",
    "baseline-check",
    "preflight",
    "python-passive-switchover",
    "ansible-passive-switchover",
    "python-restore-only",
    "ansible-restore-only",
    "argocd-managed-switchover",
    "runtime-parity",
    "final-baseline-check",
}

CREDENTIAL_KEYWORDS = {
    "token": "token",
    "client-key-data": "kubeconfig-key",
    "client-certificate-data": "kubeconfig-certificate",
    "certificate-authority-data": "kubeconfig-ca",
    "access_key": "cloud-access-key",
    "secret_key": "cloud-secret-key",
    "session_token": "cloud-session-token",
}
PEM_MARKERS = {
    "-----BEGIN CERTIFICATE-----": "pem-certificate",
    "-----BEGIN PRIVATE KEY-----": "pem-private-key",
    "-----BEGIN RSA PRIVATE KEY-----": "pem-private-key",
}
ALLOWED_DESTRUCTIVE_CLEANUP_RESOURCES = {
    "BackupSchedule",
    "Restore",
    "ArgoCDManagedConflict",
}
ALLOWED_RBAC_ACTIONS = {
    "no_bootstrap",
    "bootstrap_hub_rbac",
    "bootstrap_managed_cluster_rbac",
    "revalidate",
}
BASELINE_PRIMARY_VALUES = {"primary", "secondary"}


def require_mapping(value: Any, field_path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError(f"{field_path}: expected mapping, got {type(value).__name__}")
    return value


def require_sequence(value: Any, field_path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ProfileValidationError(f"{field_path}: expected list, got {type(value).__name__}")
    return value


def validate_top_level(raw: Mapping[str, Any], profile_path: str) -> None:
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - raw.keys())
    if missing:
        raise ProfileValidationError(f"{profile_path}: missing required top-level keys: {', '.join(missing)}")
    unknown = sorted(raw.keys() - ALLOWED_TOP_LEVEL_KEYS)
    if unknown:
        raise ProfileValidationError(f"{profile_path}: unknown top-level key: {unknown[0]}")
    if raw["profile_version"] != 1:
        raise ProfileValidationError(f"{profile_path}: profile_version: expected 1, got {raw['profile_version']!r}")
    name = raw["name"]
    if not isinstance(name, str) or not PROFILE_NAME_RE.match(name):
        raise ProfileValidationError(f"{profile_path}: name: expected /^[A-Za-z0-9_.-]+$/")


def validate_managed_clusters(raw: Mapping[str, Any]) -> None:
    has_names = bool(raw.get("expected_names"))
    has_count = raw.get("expected_count") is not None
    if has_names == has_count:
        raise ProfileValidationError("managed_clusters: expected exactly one of expected_names or expected_count")
    if has_names:
        names = require_sequence(raw["expected_names"], "managed_clusters.expected_names")
        if not names or not all(isinstance(name, str) and name for name in names):
            raise ProfileValidationError("managed_clusters.expected_names: expected non-empty list of strings")
    if has_count and int(raw["expected_count"]) < 1:
        raise ProfileValidationError("managed_clusters.expected_count: expected integer >= 1")
    contexts = raw.get("contexts", {})
    if contexts is not None:
        require_mapping(contexts, "managed_clusters.contexts")


def validate_hubs(raw: Mapping[str, Any], profile_path: str) -> None:
    for hub_name in ("primary", "secondary"):
        if hub_name not in raw:
            raise ProfileValidationError(f"{profile_path}: hubs.{hub_name}: missing required hub entry")
        hub = require_mapping(raw[hub_name], f"hubs.{hub_name}")
        for field_name in ("kubeconfig", "context"):
            if not hub.get(field_name):
                raise ProfileValidationError(f"{profile_path}: hubs.{hub_name}.{field_name}: required")


def validate_stream(raw: Mapping[str, Any], index: int) -> None:
    stream_id = raw.get("id")
    if stream_id not in KNOWN_STREAMS:
        raise ProfileValidationError(f"streams[{index}].id: expected one of bash, python, ansible")


def validate_scenario(
    raw: Mapping[str, Any],
    index: int,
    enabled_streams: set[str],
    max_cycles: int,
) -> None:
    scenario_id = raw.get("id")
    if scenario_id not in KNOWN_SCENARIOS:
        raise ProfileValidationError(f"scenarios[{index}].id: unknown scenario {scenario_id!r}")

    required = raw.get("required")
    if required is None:
        required = scenario_id in REQUIRED_FULL_RELEASE_SCENARIOS

    if raw.get("skip_reason") and required:
        raise ProfileValidationError(f"scenarios[{index}].skip_reason: allowed only when required is false")

    narrowed = set(raw.get("streams") or ())
    if not narrowed.issubset(enabled_streams):
        unknown = sorted(narrowed - enabled_streams)
        raise ProfileValidationError(f"scenarios[{index}].streams: stream is not enabled: {unknown[0]}")

    cycles = int(raw.get("cycles", 1))
    if cycles < 1 or cycles > max_cycles:
        raise ProfileValidationError(f"scenarios[{index}].cycles: expected integer between 1 and {max_cycles}")


def validate_release(raw: Mapping[str, Any], profile_path: str) -> None:
    if not raw:
        return
    for entry in raw.get("allow_non_authoritative_metadata", ()):
        item = require_mapping(entry, "release.allow_non_authoritative_metadata[]")
        if not item.get("path") or not item.get("reason"):
            raise ProfileValidationError(
                f"{profile_path}: release.allow_non_authoritative_metadata[]: expected path and reason"
            )


def validate_argocd(raw: Mapping[str, Any], profile_path: str) -> None:
    mandatory = bool(raw.get("mandatory", True))
    namespaces = raw.get("namespaces", ())
    if mandatory and not namespaces:
        raise ProfileValidationError(f"{profile_path}: argocd.namespaces: required when argocd.mandatory is true")
    if namespaces:
        require_sequence(namespaces, "argocd.namespaces")
    for entry in raw.get("application_selectors", ()):
        selector = require_mapping(entry, "argocd.application_selectors[]")
        if "match_labels" in selector:
            require_mapping(selector["match_labels"], "argocd.application_selectors[].match_labels")


def validate_baseline(raw: Mapping[str, Any], profile_path: str) -> None:
    if raw.get("initial_primary") not in BASELINE_PRIMARY_VALUES:
        raise ProfileValidationError(f"{profile_path}: baseline.initial_primary: expected primary or secondary")
    final_primary = raw.get("final_primary", raw.get("initial_primary"))
    if final_primary not in BASELINE_PRIMARY_VALUES:
        raise ProfileValidationError(f"{profile_path}: baseline.final_primary: expected primary or secondary")


def validate_limits(raw: Mapping[str, Any], profile_path: str) -> None:
    for field_name in (
        "max_cycles",
        "default_timeout_minutes",
        "cooldown_seconds",
        "soak_duration_minutes",
        "max_tolerated_failures",
        "artifact_retention_days",
    ):
        if field_name in raw and int(raw[field_name]) < 0:
            raise ProfileValidationError(f"{profile_path}: limits.{field_name}: expected integer >= 0")
    if "max_cycles" in raw and int(raw["max_cycles"]) < 1:
        raise ProfileValidationError(f"{profile_path}: limits.max_cycles: expected integer >= 1")
    if "default_timeout_minutes" in raw and int(raw["default_timeout_minutes"]) < 1:
        raise ProfileValidationError(f"{profile_path}: limits.default_timeout_minutes: expected integer >= 1")


def validate_recovery(raw: Mapping[str, Any], profile_path: str) -> None:
    for field_name in (
        "pre_run_heal_passes",
        "post_failure_passes_per_mutating_scenario",
    ):
        if field_name in raw and int(raw[field_name]) not in {0, 1}:
            raise ProfileValidationError(f"{profile_path}: recovery.{field_name}: expected 0 or 1")

    if "total_budget_minutes" in raw and int(raw["total_budget_minutes"]) < 0:
        raise ProfileValidationError(f"{profile_path}: recovery.total_budget_minutes: expected integer >= 0")

    cleanup = require_mapping(
        raw.get("allowed_destructive_cleanup", {}),
        "recovery.allowed_destructive_cleanup",
    )
    resources = cleanup.get("resources", ())
    for resource in resources:
        if resource not in ALLOWED_DESTRUCTIVE_CLEANUP_RESOURCES:
            raise ProfileValidationError(
                f"{profile_path}: recovery.allowed_destructive_cleanup.resources: unsupported resource {resource!r}"
            )

    for action in raw.get("rbac_actions", ()):
        if action not in ALLOWED_RBAC_ACTIONS:
            raise ProfileValidationError(f"{profile_path}: recovery.rbac_actions: unsupported action {action!r}")


def validate_artifacts(raw: Mapping[str, Any], profile_path: str) -> None:
    redaction = require_mapping(raw.get("redaction", {}), "artifacts.redaction")
    if "required" in redaction and not bool(redaction["required"]):
        raise ProfileValidationError(f"{profile_path}: artifacts.redaction.required: expected true")


def validate_profile_contents(value: Any, profile_path: str, field_path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{field_path}.{key}" if field_path else str(key)
            lowered_key = str(key).lower()
            if lowered_key in CREDENTIAL_KEYWORDS:
                raise ProfileValidationError(
                    f"{profile_path}: {child_path}: matched credential class {CREDENTIAL_KEYWORDS[lowered_key]}"
                )
            validate_profile_contents(child, profile_path, child_path)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            validate_profile_contents(child, profile_path, f"{field_path}[{index}]")
        return

    if isinstance(value, str):
        for marker, credential_class in PEM_MARKERS.items():
            if marker in value:
                raise ProfileValidationError(
                    f"{profile_path}: {field_path}: matched credential class {credential_class}"
                )
