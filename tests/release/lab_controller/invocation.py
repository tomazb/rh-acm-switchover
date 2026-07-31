from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.release.contracts.models import ProfileValidationError
from tests.release.contracts.schema import (
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
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

from .artifacts import sanitize_artifact_key, sanitize_artifact_text, validate_artifact_payload_redacted
from .decisions import classify_scenario
from .models import GeneratedProfile, ScenarioClassification

PYTEST_RUNNER = ("python", "-m", "pytest")
RELEASE_CERTIFICATION_PYTEST_TARGET = "tests/release/test_release_certification.py"
DRY_RUN_EXECUTION_MODE = "release_framework_dry_run"
LIVE_EXECUTION_MODE = "release_framework_live"
SUPPORTED_RELEASE_MODES = ("certification", "focused-rerun", "debug")
CLI_RELEASE_STREAMS = {"bash", "python", "ansible"}
FORBIDDEN_ENV_KEYS = (
    "KUBECONFIG",
    "ACM_RELEASE_PROFILE",
    "ACM_ENABLE_LIVE_RBAC_CERTIFICATION",
)
_SENSITIVE_ENV_MARKERS = ("kubeconfig", "token", "password", "secret", "credential")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class ReleaseFrameworkArgv:
    argv: tuple[str, ...]
    pytest_target: str
    release_mode: str
    profile_reference_kind: str
    profile_reference: str
    artifact_directory: str
    scenario_selectors: tuple[str, ...]
    stream_selectors: tuple[str, ...]
    executed: bool = False
    materialized_for_future_execution: bool = True

    def to_display_command(self) -> str:
        return " ".join(_sanitized_argv(self.argv))

    def to_summary(self) -> dict[str, Any]:
        sanitized_argv = _sanitized_argv(self.argv)
        return {
            "argv": list(sanitized_argv),
            "display_command": " ".join(sanitized_argv),
            "pytest_target": sanitize_artifact_text(self.pytest_target),
            "release_mode": sanitize_artifact_text(self.release_mode),
            "profile_reference_kind": self.profile_reference_kind,
            "profile_reference": sanitize_artifact_text(self.profile_reference),
            "artifact_directory": sanitize_artifact_text(self.artifact_directory),
            "scenario_selectors": [sanitize_artifact_text(item) or "[REDACTED]" for item in self.scenario_selectors],
            "stream_selectors": [sanitize_artifact_text(item) or "[REDACTED]" for item in self.stream_selectors],
            "executed": self.executed,
            "materialized_for_future_execution": self.materialized_for_future_execution,
        }


@dataclass(frozen=True)
class ReleaseFrameworkEnvPlan:
    allowed_env: Mapping[str, str]
    redacted_env: Mapping[str, str]
    rejected_keys: tuple[str, ...]
    rejected_values: tuple[str, ...]
    forbidden_env_vars: tuple[str, ...]
    forbidden_categories: tuple[str, ...]
    safe: bool
    reason: str

    def to_summary(self) -> dict[str, Any]:
        rejected_keys = tuple(_redact_env_key_for_summary(key) for key in self.rejected_keys)
        return {
            "allowed_env": {
                sanitize_artifact_key(key): sanitize_artifact_text(value)
                for key, value in sorted(self.allowed_env.items())
            },
            "redacted_env": {
                _redact_env_key_for_summary(key): sanitize_artifact_text(value)
                for key, value in sorted(self.redacted_env.items())
            },
            "rejected_keys": list(rejected_keys),
            "rejected_values": list(self.rejected_values),
            "forbidden_env_vars": list(tuple(_redact_env_key_for_summary(key) for key in self.forbidden_env_vars)),
            "forbidden_categories": list(self.forbidden_categories),
            "safe": self.safe,
            "reason": sanitize_artifact_text(self.reason),
        }


@dataclass(frozen=True)
class ReleaseFrameworkProfileCompatibility:
    compatible: bool
    contract_shape_compatible: bool
    loader_compatible: bool
    runtime_only_profile: bool
    profile_hash_matches: bool
    selected_streams: tuple[str, ...]
    scenario_id: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_summary(self) -> dict[str, Any]:
        return {
            "compatible": self.compatible,
            "contract_shape_compatible": self.contract_shape_compatible,
            "loader_compatible": self.loader_compatible,
            "runtime_only_profile": self.runtime_only_profile,
            "profile_hash_matches": self.profile_hash_matches,
            "selected_streams": [sanitize_artifact_text(stream) or "[REDACTED]" for stream in self.selected_streams],
            "scenario_id": sanitize_artifact_text(self.scenario_id) or "[REDACTED]",
            "errors": [sanitize_artifact_text(error) or "[REDACTED]" for error in self.errors],
            "warnings": [sanitize_artifact_text(warning) or "[REDACTED]" for warning in self.warnings],
        }


@dataclass(frozen=True)
class ReleaseFrameworkArtifactDirectoryPlan:
    runtime_path: str
    artifact_path_summary: str
    relative_path: str
    safe: bool
    reason: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "artifact_path_summary": sanitize_artifact_text(self.artifact_path_summary),
            "relative_path": sanitize_artifact_text(self.relative_path),
            "safe": self.safe,
            "reason": sanitize_artifact_text(self.reason),
        }


@dataclass(frozen=True)
class ReleaseFrameworkExecutionEligibility:
    eligible: bool
    reason: str
    blocking_fields: tuple[str, ...]
    dry_run_only: bool = True
    live_execution_supported: bool = False
    real_execution_evidence: bool = False
    live_certification_evidence: bool = False

    def to_summary(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": sanitize_artifact_text(self.reason),
            "blocking_fields": [sanitize_artifact_text(field) or "[REDACTED]" for field in self.blocking_fields],
            "dry_run_only": self.dry_run_only,
            "live_execution_supported": self.live_execution_supported,
            "real_execution_evidence": self.real_execution_evidence,
            "live_certification_evidence": self.live_certification_evidence,
        }


@dataclass(frozen=True)
class MaterializedExecutionRequest:
    argv: ReleaseFrameworkArgv
    env_plan: ReleaseFrameworkEnvPlan
    profile_compatibility: ReleaseFrameworkProfileCompatibility
    artifact_directory: ReleaseFrameworkArtifactDirectoryPlan
    eligibility: ReleaseFrameworkExecutionEligibility
    materialization_hash: str

    @property
    def summary(self) -> dict[str, Any]:
        return summarize_materialized_invocation(self)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _execution_mode_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    return str(enum_value or value)


def _sanitized_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sanitize_artifact_text(item) or "[REDACTED]" for item in argv)


def _redact_env_key_for_summary(key: str) -> str:
    lowered = key.lower()
    if key in FORBIDDEN_ENV_KEYS or any(marker in lowered for marker in _SENSITIVE_ENV_MARKERS):
        return "[REDACTED]"
    return sanitize_artifact_key(key)


def _is_sensitive_env_key(key: str) -> bool:
    lowered = key.lower()
    return key in FORBIDDEN_ENV_KEYS or any(marker in lowered for marker in _SENSITIVE_ENV_MARKERS)


def _is_sensitive_env_value(value: str) -> bool:
    sanitized = sanitize_artifact_text(value)
    return sanitized != value


def build_release_framework_env_plan(explicit_env: Mapping[str, str] | None = None) -> ReleaseFrameworkEnvPlan:
    """Build a deterministic environment plan without reading or mutating process environment."""
    explicit_env = explicit_env or {}
    allowed_env: dict[str, str] = {}
    redacted_env: dict[str, str] = {}
    rejected_keys: list[str] = []
    rejected_values: list[str] = []

    for key, value in sorted((str(key), str(value)) for key, value in explicit_env.items()):
        key_is_sensitive = _is_sensitive_env_key(key)
        value_is_sensitive = _is_sensitive_env_value(value)
        if key_is_sensitive:
            rejected_keys.append(key)
        if value_is_sensitive:
            rejected_values.append(_stable_hash({"env_value": value}))
        if key_is_sensitive or value_is_sensitive:
            redacted_env[key] = "[REDACTED]"
            continue
        allowed_env[key] = value
        redacted_env[key] = value

    safe = not rejected_keys and not rejected_values
    return ReleaseFrameworkEnvPlan(
        allowed_env=allowed_env,
        redacted_env=redacted_env,
        rejected_keys=tuple(rejected_keys),
        rejected_values=tuple(rejected_values),
        forbidden_env_vars=FORBIDDEN_ENV_KEYS,
        forbidden_categories=("auth material", "kube paths", "private URLs", "profile path overrides"),
        safe=safe,
        reason="environment plan is artifact-safe" if safe else "environment plan contains unsafe entries",
    )


def _unsafe_component_reason(value: str) -> str | None:
    if not value:
        return "empty path component"
    if value in {".", ".."} or ".." in value or "/" in value or "\\" in value:
        return "path traversal"
    if not _SAFE_COMPONENT_RE.match(value):
        return "unsafe path component"
    if sanitize_artifact_text(value) != value:
        return "sensitive path component"
    return None


def _unsafe_artifact_root_reason(artifact_root: str) -> str | None:
    if not artifact_root:
        return "missing artifact root"
    path = Path(artifact_root)
    if path.is_absolute():
        return "absolute artifact root is runtime-only and cannot be published"
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if any(part == ".." for part in parts):
        return "path traversal"
    if sanitize_artifact_text(artifact_root) != artifact_root:
        return "artifact root contains sensitive path material"
    return None


def plan_release_framework_artifact_directory(
    *,
    plan_id: str,
    segment_id: str,
    scenario_id: str,
    backend_mode: str,
    artifact_root: str,
) -> ReleaseFrameworkArtifactDirectoryPlan:
    reasons: list[str] = []
    for component_name, component in (
        ("plan_id", plan_id),
        ("segment_id", segment_id),
        ("scenario_id", scenario_id),
        ("backend_mode", backend_mode),
    ):
        reason = _unsafe_component_reason(component)
        if reason is not None:
            reasons.append(f"{component_name}: {reason}")
    root_reason = _unsafe_artifact_root_reason(artifact_root)
    if root_reason is not None:
        reasons.append(root_reason)

    relative_path = "/".join((plan_id, segment_id, scenario_id, backend_mode))
    runtime_path = str(Path(artifact_root) / plan_id / segment_id / scenario_id / backend_mode)
    safe = not reasons
    return ReleaseFrameworkArtifactDirectoryPlan(
        runtime_path=runtime_path,
        artifact_path_summary=runtime_path if safe else "[REDACTED]",
        relative_path=relative_path if safe else "[REDACTED]",
        safe=safe,
        reason="artifact directory plan is deterministic and artifact-safe" if safe else "; ".join(reasons),
    )


def _schema_errors(profile_data: Mapping[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    profile_path = "runtime-profile-payload"
    try:
        validate_top_level(profile_data, profile_path)
        validate_profile_contents(profile_data, profile_path)
        limits_raw = require_mapping(profile_data["limits"], "limits")
        validate_limits(limits_raw, profile_path)
        argocd_raw = require_mapping(profile_data["argocd"], "argocd")
        validate_argocd(argocd_raw, profile_path)
        baseline_raw = require_mapping(profile_data["baseline"], "baseline")
        validate_baseline(baseline_raw, profile_path)
        recovery_raw = require_mapping(profile_data["recovery"], "recovery")
        validate_recovery(recovery_raw, profile_path)
        artifacts_raw = require_mapping(profile_data["artifacts"], "artifacts")
        validate_artifacts(artifacts_raw, profile_path)
        hubs_raw = require_mapping(profile_data["hubs"], "hubs")
        validate_hubs(hubs_raw, profile_path)
        managed_raw = require_mapping(profile_data["managed_clusters"], "managed_clusters")
        validate_managed_clusters(managed_raw)
        stream_items = [
            require_mapping(item, "streams[]") for item in require_sequence(profile_data["streams"], "streams")
        ]
        for index, item in enumerate(stream_items):
            validate_stream(item, index)
        enabled_streams = {str(item["id"]) for item in stream_items if item.get("enabled", True)}
        scenario_items = [
            require_mapping(item, "scenarios[]") for item in require_sequence(profile_data["scenarios"], "scenarios")
        ]
        for index, item in enumerate(scenario_items):
            validate_scenario(item, index, enabled_streams, int(limits_raw.get("max_cycles", 1)))
        if "release" in profile_data:
            validate_release(require_mapping(profile_data["release"], "release"), profile_path)
    except (KeyError, TypeError, ValueError, ProfileValidationError) as exc:
        errors.append(str(exc))
    return tuple(sanitize_artifact_text(error) or "[REDACTED]" for error in errors)


def _enabled_profile_streams(profile_data: Mapping[str, Any]) -> tuple[str, ...]:
    streams = profile_data.get("streams", ())
    if not isinstance(streams, list):
        return ()
    enabled = []
    for item in streams:
        if isinstance(item, dict) and item.get("enabled", True):
            stream_id = item.get("id")
            if isinstance(stream_id, str):
                enabled.append(stream_id)
    return tuple(enabled)


def _profile_scenario_ids(profile_data: Mapping[str, Any]) -> tuple[str, ...]:
    scenarios = profile_data.get("scenarios", ())
    if not isinstance(scenarios, list):
        return ()
    return tuple(str(item.get("id")) for item in scenarios if isinstance(item, dict) and item.get("id"))


def _metadata_profile_hash(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("profile_sha256")
    return str(value) if isinstance(value, str) and value else None


def _required_runtime_profile_errors(profile_data: Mapping[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    hubs = profile_data.get("hubs")
    if not isinstance(hubs, dict) or "primary" not in hubs:
        errors.append("hubs.primary is required by the release profile contract")
    if not isinstance(hubs, dict) or "secondary" not in hubs:
        errors.append("hubs.secondary is required by the release profile contract")
    for role in ("primary", "secondary"):
        hub = hubs.get(role) if isinstance(hubs, dict) else None
        if not isinstance(hub, dict) or not hub.get("context"):
            errors.append(f"hubs.{role}.context is required in the runtime-only payload")
        if not isinstance(hub, dict) or not hub.get("kubeconfig"):
            errors.append(f"hubs.{role}.kubeconfig is required in the runtime-only payload")

    managed_clusters = profile_data.get("managed_clusters", {})
    expected_names = managed_clusters.get("expected_names") if isinstance(managed_clusters, dict) else None
    expected_count = managed_clusters.get("expected_count") if isinstance(managed_clusters, dict) else None
    if not expected_names and expected_count is None:
        errors.append("managed cluster expectations must include expected_names or expected_count")

    if "argocd" not in profile_data:
        errors.append("argocd settings are required by the release profile contract")
    if "artifacts" not in profile_data:
        errors.append("artifact settings are required by the release profile contract")
    return tuple(errors)


def _scenario_stream_compatibility_errors(
    *,
    profile_data: Mapping[str, Any],
    scenario_id: str,
    selected_streams: tuple[str, ...],
) -> tuple[str, ...]:
    errors: list[str] = []
    if scenario_id not in SCENARIOS_BY_ID:
        return (f"unknown release scenario: {scenario_id}",)
    if scenario_id not in _profile_scenario_ids(profile_data):
        errors.append(f"release scenario {scenario_id} is not declared by the generated profile")

    enabled_streams = set(_enabled_profile_streams(profile_data))
    scenario_streams = set(SCENARIOS_BY_ID[scenario_id].streams)
    for stream in selected_streams:
        if stream == "local":
            if scenario_streams != {"local"}:
                errors.append(f"unsupported stream selector for scenario {scenario_id}: local")
            continue
        if stream not in enabled_streams:
            errors.append(f"stream selector is not enabled by the generated profile: {stream}")
        if stream not in scenario_streams:
            errors.append(f"unsupported stream selector for scenario {scenario_id}: {stream}")
    return tuple(errors)


def _profile_hash_matches(
    *,
    generated_profile_hash: str,
    generated_profile_metadata: Mapping[str, Any],
    request_profile_hash: str,
) -> bool:
    return (
        generated_profile_hash == request_profile_hash
        and _metadata_profile_hash(generated_profile_metadata) == generated_profile_hash
    )


def _redacted_metadata_errors(generated_profile_metadata: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        validate_artifact_payload_redacted({"generated_profile_metadata": dict(generated_profile_metadata)})
    except ValueError as exc:
        return (sanitize_artifact_text(str(exc)) or "[REDACTED]",)
    return ()


def verify_release_profile_compatibility(
    *,
    profile_data: Mapping[str, Any],
    generated_profile_hash: str,
    generated_profile_metadata: Mapping[str, Any],
    scenario_id: str,
    selected_streams: tuple[str, ...],
    request_profile_hash: str,
) -> ReleaseFrameworkProfileCompatibility:
    """Validate the runtime-only generated profile against the existing release profile contract where practical."""
    errors = list(_schema_errors(profile_data))
    warnings = ["runtime-only profile payload was not written to disk"]
    contract_shape_compatible = not errors
    errors.extend(_required_runtime_profile_errors(profile_data))
    errors.extend(
        _scenario_stream_compatibility_errors(
            profile_data=profile_data,
            scenario_id=scenario_id,
            selected_streams=selected_streams,
        )
    )
    profile_hash_matches = _profile_hash_matches(
        generated_profile_hash=generated_profile_hash,
        generated_profile_metadata=generated_profile_metadata,
        request_profile_hash=request_profile_hash,
    )
    if not profile_hash_matches:
        errors.append("generated profile hash does not match request metadata")
    errors.extend(_redacted_metadata_errors(generated_profile_metadata))

    return ReleaseFrameworkProfileCompatibility(
        compatible=not errors,
        contract_shape_compatible=contract_shape_compatible,
        loader_compatible=False,
        runtime_only_profile=bool(generated_profile_metadata.get("runtime_only", False)),
        profile_hash_matches=profile_hash_matches,
        selected_streams=selected_streams,
        scenario_id=scenario_id,
        errors=tuple(sanitize_artifact_text(error) or "[REDACTED]" for error in errors),
        warnings=tuple(warnings),
    )


def _stream_blockers(scenario_id: str, selected_streams: tuple[str, ...]) -> tuple[str, ...]:
    if scenario_id not in SCENARIOS_BY_ID:
        return ()
    scenario_streams = set(SCENARIOS_BY_ID[scenario_id].streams)
    blockers = []
    for stream in selected_streams:
        if stream == "local" and scenario_streams == {"local"}:
            continue
        if stream not in scenario_streams:
            blockers.append(stream)
    return tuple(blockers)


def evaluate_release_framework_execution_eligibility(
    *,
    scenario_id: str,
    execution_mode: str,
    selected_streams: tuple[str, ...],
    profile_compatibility: ReleaseFrameworkProfileCompatibility,
    env_plan: ReleaseFrameworkEnvPlan,
    artifact_directory_plan: ReleaseFrameworkArtifactDirectoryPlan,
) -> ReleaseFrameworkExecutionEligibility:
    blockers: list[str] = []
    reasons: list[str] = []

    if execution_mode != DRY_RUN_EXECUTION_MODE:
        blockers.append("execution_mode")
        reasons.append("only dry-run release-framework materialization is supported")
    if scenario_id not in SCENARIOS_BY_ID:
        blockers.append("scenario_id")
        reasons.append("unknown release scenario")
    else:
        classification = classify_scenario(scenario_id)
        if classification is ScenarioClassification.DESTRUCTIVE_DISPOSABLE_LAB_ONLY:
            blockers.append("scenario_id")
            reasons.append(f"scenario {scenario_id} is destructive/disposable-lab-only")
        if classification is ScenarioClassification.RECOVERY:
            blockers.append("scenario_id")
            reasons.append(f"scenario {scenario_id} is a recovery scenario")
    unsupported_streams = _stream_blockers(scenario_id, selected_streams)
    if unsupported_streams:
        blockers.append("selected_streams")
        reasons.append("selected stream is unsupported for the scenario")
    if not profile_compatibility.compatible:
        blockers.append("profile_compatibility")
        reasons.append("generated profile is not compatible with the release profile contract")
    if not env_plan.safe:
        blockers.append("environment_plan")
        reasons.append("environment plan contains unsafe entries")
    if not artifact_directory_plan.safe:
        blockers.append("artifact_directory")
        reasons.append("artifact directory plan is unsafe for artifact summaries")

    eligible = not blockers
    return ReleaseFrameworkExecutionEligibility(
        eligible=eligible,
        reason=(
            "materialized dry-run request is eligible for future dry-run execution only"
            if eligible
            else "; ".join(reasons)
        ),
        blocking_fields=tuple(dict.fromkeys(blockers)),
    )


def _build_argv(
    *,
    pytest_target: str,
    release_mode: str,
    scenario_id: str,
    selected_streams: tuple[str, ...],
    profile_hash: str,
    artifact_directory: str,
) -> ReleaseFrameworkArgv:
    if release_mode not in SUPPORTED_RELEASE_MODES:
        raise ValueError(f"unsupported release mode: {release_mode}")
    profile_reference = f"runtime-profile-sha256:{profile_hash}"
    argv = [
        *PYTEST_RUNNER,
        pytest_target,
        "--release-profile",
        profile_reference,
        "--release-mode",
        release_mode,
        "--release-scenario",
        scenario_id,
    ]
    for stream in selected_streams:
        if stream in CLI_RELEASE_STREAMS:
            argv.extend(("--release-stream", stream))
    argv.extend(("--release-artifact-dir", artifact_directory))
    return ReleaseFrameworkArgv(
        argv=tuple(argv),
        pytest_target=pytest_target,
        release_mode=release_mode,
        profile_reference_kind="runtime_profile_payload",
        profile_reference=profile_reference,
        artifact_directory=artifact_directory,
        scenario_selectors=(scenario_id,),
        stream_selectors=selected_streams,
    )


def materialize_release_framework_request(
    *,
    request: Any,
    generated_profile: GeneratedProfile,
    plan_id: str,
    artifact_root: str,
    explicit_env: Mapping[str, str] | None = None,
) -> MaterializedExecutionRequest:
    """Materialize a validated Phase 6A dry-run request into a non-executed invocation plan."""
    execution_mode = _execution_mode_value(request.execution_mode)
    artifact_directory = plan_release_framework_artifact_directory(
        plan_id=plan_id,
        segment_id=str(request.segment_id),
        scenario_id=str(request.scenario_id),
        backend_mode=execution_mode,
        artifact_root=artifact_root,
    )
    argv = _build_argv(
        pytest_target=str(request.intended_pytest_target),
        release_mode=str(request.intended_release_mode),
        scenario_id=str(request.scenario_id),
        selected_streams=tuple(str(stream) for stream in request.selected_streams),
        profile_hash=str(request.generated_profile_hash),
        artifact_directory=artifact_directory.runtime_path,
    )
    env_plan = build_release_framework_env_plan(explicit_env)
    profile_compatibility = verify_release_profile_compatibility(
        profile_data=generated_profile.profile_data,
        generated_profile_hash=generated_profile.sha256,
        generated_profile_metadata=request.generated_profile_metadata,
        scenario_id=str(request.scenario_id),
        selected_streams=tuple(str(stream) for stream in request.selected_streams),
        request_profile_hash=str(request.generated_profile_hash),
    )
    eligibility = evaluate_release_framework_execution_eligibility(
        scenario_id=str(request.scenario_id),
        execution_mode=execution_mode,
        selected_streams=tuple(str(stream) for stream in request.selected_streams),
        profile_compatibility=profile_compatibility,
        env_plan=env_plan,
        artifact_directory_plan=artifact_directory,
    )
    materialization_hash = _stable_hash(
        {
            "argv": argv.to_summary(),
            "env_plan": env_plan.to_summary(),
            "profile_compatibility": profile_compatibility.to_summary(),
            "artifact_directory": artifact_directory.to_summary(),
            "eligibility": eligibility.to_summary(),
        }
    )
    materialized = MaterializedExecutionRequest(
        argv=argv,
        env_plan=env_plan,
        profile_compatibility=profile_compatibility,
        artifact_directory=artifact_directory,
        eligibility=eligibility,
        materialization_hash=materialization_hash,
    )
    validate_materialized_invocation(materialized)
    return materialized


def summarize_materialized_invocation(materialized: MaterializedExecutionRequest) -> dict[str, Any]:
    summary = {
        "materialization_hash": materialized.materialization_hash,
        "materialized_argv_summary": materialized.argv.to_summary(),
        "environment_plan_summary": materialized.env_plan.to_summary(),
        "profile_compatibility_summary": materialized.profile_compatibility.to_summary(),
        "artifact_directory_summary": materialized.artifact_directory.to_summary(),
        "future_execution_eligibility": materialized.eligibility.to_summary(),
        "dry_run": True,
        "executed": False,
        "real_execution_evidence": False,
        "live_certification_evidence": False,
        "evidence_status": "dry_run_only",
    }
    validate_artifact_payload_redacted(summary)
    return summary


def validate_materialized_invocation(materialized: MaterializedExecutionRequest) -> None:
    if not isinstance(materialized.argv.argv, tuple):
        raise ValueError("materialized argv must be a tuple")
    if materialized.argv.executed:
        raise ValueError("materialized invocation must not be marked executed")
    if materialized.eligibility.real_execution_evidence or materialized.eligibility.live_certification_evidence:
        raise ValueError("materialized dry-run requests cannot claim real or live certification evidence")
    validate_artifact_payload_redacted(summarize_materialized_invocation(materialized))
