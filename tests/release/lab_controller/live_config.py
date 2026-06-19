from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from tests.release.lab_controller.artifacts import sanitize_artifact_payload, validate_artifact_payload_redacted
from tests.release.scenarios.catalog import SCENARIOS_BY_ID


class LiveConfigSchemaVersion(str, Enum):
    PHASE_8C = "design.phase8c"


class LiveConfigValidationDecision(str, Enum):
    PASS = "PASS"  # nosec B105
    BLOCKED = "BLOCKED"
    NO_GO = "NO_GO"


class LiveConfigFieldSensitivity(str, Enum):
    RUNTIME_ONLY = "runtime_only"
    ARTIFACT_SAFE = "artifact_safe"
    REDACTED_OR_FINGERPRINT_ONLY = "redacted_or_fingerprint_only"
    FORBIDDEN_COMMITTED_VALUE = "forbidden_committed_value"


class LiveGateId(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"
    L8 = "L8"
    L9 = "L9"
    L10 = "L10"


class LiveGateStatus(str, Enum):
    DESIGN_ONLY = "design_only"
    NOT_SATISFIED = "not_satisfied"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LiveExecutionPolicy:
    live_execution_enabled: bool = False
    read_only_discovery_enabled: bool = False
    mutation_enabled: bool = False
    automatic_recovery_enabled: bool = False
    live_certification_evidence_enabled: bool = False


@dataclass(frozen=True)
class LiveCredentialPolicy:
    runtime_only: bool = True
    persist_to_artifacts: bool = False
    inherit_environment: bool = False
    references: tuple["CredentialReferenceConfig", ...] = ()
    allowed_env_vars: tuple[str, ...] = ()
    forbidden_env_patterns: tuple[str, ...] = ("sensitive-env-values", "runtime-config-values")


@dataclass(frozen=True)
class LiveArtifactPolicy:
    artifact_dir: str = "<caller-provided-artifact-dir>"
    default_release_output: bool = False
    commit_live_artifacts: bool = False
    redaction_required: bool = True
    stdout_stderr_sanitization_required: bool = True
    retention_policy: str = "caller-defined"


@dataclass(frozen=True)
class LiveRedactionPolicy:
    required: bool = True
    reject_raw_api_urls: bool = True
    reject_private_ids: bool = True
    fingerprint_identity_values: bool = True
    reject_credential_values: bool = True
    forbidden_artifact_patterns: tuple[str, ...] = (
        "runtime-path-values",
        "endpoint-like-values",
        "private-id-like-values",
    )


@dataclass(frozen=True)
class PhysicalHubConfig:
    physical_label: str
    context_ref: str
    kubeconfig_ref: str
    expected_identity_fingerprint: str
    expected_api_fingerprint: str
    expected_cluster_version: str | None = None
    acm_hub_evidence_requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagedClusterConfig:
    expected_names: tuple[str, ...]
    exact_match_required: bool = True
    unexpected_cluster_policy: str = "block"
    cluster_identity_fingerprints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalConfig:
    operator_confirmed_live_mode: bool = False
    mutation_allowed: bool = False
    mutation_confirmation_required: bool = True
    approved_scenarios: tuple[str, ...] = ("preflight",)
    approval_timestamp: str | None = None
    approver_reference: str | None = "<operator-provided-approval-ref>"


@dataclass(frozen=True)
class CredentialReferenceConfig:
    name: str
    runtime_ref: str
    required: bool = True


@dataclass(frozen=True)
class IdentityExpectationConfig:
    hub_identity_fingerprints: tuple[str, ...]
    api_identity_fingerprints: tuple[str, ...]
    cluster_version_expectations: tuple[str, ...] = ()
    mismatch_policy: str = "block"


@dataclass(frozen=True)
class RoleDiscoveryConfig:
    required_evidence: tuple[str, ...] = ("managed-cluster-inventory", "backup-restore-evidence")
    active_role_policy: str = "exactly-one-primary"
    ambiguity_policy: str = "block"
    fresh_discovery_required: bool = True


@dataclass(frozen=True)
class RbacPrerequisiteConfig:
    read_only_checks_required: bool = True
    mutation_checks_required: bool = False
    deny_checks_required: bool = False
    prerequisite_health_checks: tuple[str, ...] = ("acm-health", "backup-restore-health")


@dataclass(frozen=True)
class ScenarioAllowlistConfig:
    approved_scenarios: tuple[str, ...] = ("preflight",)
    allowlist_version: str = "design-only"
    first_live_family: str = "read-only-preflight-only"
    unknown_scenario_policy: str = "block"


@dataclass(frozen=True)
class ExternalLiveLabConfig:
    schema_version: LiveConfigSchemaVersion | str
    lab_id: str | None
    plan_id: str | None
    physical_hubs: tuple[PhysicalHubConfig, ...]
    managed_clusters: ManagedClusterConfig
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)
    credentials: LiveCredentialPolicy = field(default_factory=LiveCredentialPolicy)
    identity_expectations: IdentityExpectationConfig = field(
        default_factory=lambda: IdentityExpectationConfig(
            hub_identity_fingerprints=("<redacted-hub-identity-fingerprint>",),
            api_identity_fingerprints=("<redacted-api-fingerprint>",),
        )
    )
    role_discovery: RoleDiscoveryConfig = field(default_factory=RoleDiscoveryConfig)
    rbac_prerequisites: RbacPrerequisiteConfig = field(default_factory=RbacPrerequisiteConfig)
    scenario_allowlist: ScenarioAllowlistConfig = field(default_factory=ScenarioAllowlistConfig)
    artifact_policy: LiveArtifactPolicy = field(default_factory=LiveArtifactPolicy)
    redaction_policy: LiveRedactionPolicy = field(default_factory=LiveRedactionPolicy)
    execution_policy: LiveExecutionPolicy = field(default_factory=LiveExecutionPolicy)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExternalLiveLabConfig":
        return cls(
            schema_version=payload.get("schema_version", ""),
            lab_id=_optional_str(payload.get("lab_id")),
            plan_id=_optional_str(payload.get("plan_id")),
            physical_hubs=tuple(_physical_hub_from_mapping(item) for item in _sequence(payload.get("physical_hubs"))),
            managed_clusters=_managed_clusters_from_mapping(payload.get("managed_clusters", {})),
            approval=_approval_from_mapping(payload.get("approval", {})),
            credentials=_credentials_from_mapping(payload.get("credentials", {})),
            identity_expectations=_identity_expectations_from_mapping(payload.get("identity_expectations", {})),
            role_discovery=_role_discovery_from_mapping(payload.get("role_discovery", {})),
            rbac_prerequisites=_rbac_prerequisites_from_mapping(payload.get("rbac_prerequisites", {})),
            scenario_allowlist=_scenario_allowlist_from_mapping(payload.get("scenario_allowlist", {})),
            artifact_policy=_artifact_policy_from_mapping(payload.get("artifact_policy", {})),
            redaction_policy=_redaction_policy_from_mapping(payload.get("redaction_policy", {})),
            execution_policy=_execution_policy_from_mapping(payload.get("execution_policy", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = _enum_value(self.schema_version)
        return payload

    def to_artifact_safe_dict(self, *, include_validation: bool = True) -> dict[str, Any]:
        validation = validate_external_live_lab_config(self)
        payload = dict(validation.artifact_safe_summary)
        if include_validation:
            payload["validation"] = sanitize_artifact_payload(validation.to_dict(include_summary=False))
        validate_artifact_payload_redacted(payload)
        return payload


@dataclass(frozen=True)
class LiveConfigValidationResult:
    decision: LiveConfigValidationDecision
    reasons: tuple[str, ...]
    blocking_fields: tuple[str, ...]
    artifact_safe_summary: dict[str, Any]
    redaction_status: str
    live_execution_enabled: bool
    mutation_enabled: bool
    automatic_recovery_enabled: bool
    live_certification_evidence_enabled: bool

    def to_dict(self, *, include_summary: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "blocking_fields": list(self.blocking_fields),
            "redaction_status": self.redaction_status,
            "live_execution_enabled": self.live_execution_enabled,
            "mutation_enabled": self.mutation_enabled,
            "automatic_recovery_enabled": self.automatic_recovery_enabled,
            "live_certification_evidence_enabled": self.live_certification_evidence_enabled,
        }
        if include_summary:
            payload["artifact_safe_summary"] = self.artifact_safe_summary
        return payload


_RUNTIME_ONLY_FIELDS = frozenset(
    {
        "physical_hubs.context_ref",
        "physical_hubs.kubeconfig_ref",
        "credentials.references",
        "credentials.references.runtime_ref",
        "credentials.allowed_env_vars",
    }
)
_ARTIFACT_SAFE_FIELDS = frozenset(
    {
        "schema_version",
        "physical_hubs.physical_label",
        "physical_hubs.acm_hub_evidence_requirements",
        "managed_clusters.expected_names",
        "managed_clusters.exact_match_required",
        "managed_clusters.unexpected_cluster_policy",
        "approval.operator_confirmed_live_mode",
        "approval.mutation_allowed",
        "approval.mutation_confirmation_required",
        "approval.approved_scenarios",
        "credentials.runtime_only",
        "credentials.persist_to_artifacts",
        "credentials.inherit_environment",
        "identity_expectations.mismatch_policy",
        "role_discovery.required_evidence",
        "role_discovery.active_role_policy",
        "role_discovery.ambiguity_policy",
        "role_discovery.fresh_discovery_required",
        "rbac_prerequisites.read_only_checks_required",
        "rbac_prerequisites.mutation_checks_required",
        "scenario_allowlist.approved_scenarios",
        "scenario_allowlist.allowlist_version",
        "scenario_allowlist.first_live_family",
        "scenario_allowlist.unknown_scenario_policy",
        "artifact_policy.default_release_output",
        "artifact_policy.commit_live_artifacts",
        "artifact_policy.redaction_required",
        "artifact_policy.stdout_stderr_sanitization_required",
        "redaction_policy.required",
        "redaction_policy.reject_raw_api_urls",
        "redaction_policy.reject_private_ids",
        "redaction_policy.fingerprint_identity_values",
        "redaction_policy.reject_credential_values",
        "execution_policy.live_execution_enabled",
        "execution_policy.read_only_discovery_enabled",
        "execution_policy.mutation_enabled",
        "execution_policy.automatic_recovery_enabled",
        "execution_policy.live_certification_evidence_enabled",
    }
)
_REDACTED_OR_FINGERPRINT_FIELDS = frozenset(
    {
        "lab_id",
        "plan_id",
        "physical_hubs.expected_identity_fingerprint",
        "physical_hubs.expected_api_fingerprint",
        "managed_clusters.cluster_identity_fingerprints",
        "approval.approver_reference",
        "identity_expectations.hub_identity_fingerprints",
        "identity_expectations.api_identity_fingerprints",
        "artifact_policy.artifact_dir",
    }
)
_INITIAL_LIVE_ALLOWED_SCENARIOS = frozenset(
    {
        "preflight",
        "lab-readiness",
        "baseline-check",
        "final-baseline-check",
    }
)
_ALLOWED_PLACEHOLDERS = frozenset(
    {
        "<runtime-only-kubeconfig-ref>",
        "<runtime-only-context-ref>",
        "<redacted-api-fingerprint>",
        "<redacted-hub-identity-fingerprint>",
        "<redacted-managed-cluster-fingerprint>",
        "<operator-provided-approval-ref>",
        "<caller-provided-artifact-dir>",
    }
)
_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_KUBECONFIG_PATH_PATTERN = re.compile(r"(^|[\s'\"])(/home/|/tmp/|~/\.kube/|[^ \t\n'\";]+[/\\]\.kube[/\\])")
_SHELL_COMMAND_PATTERN = re.compile(
    r"\b(?:oc|kubectl|ansible-playbook|bash|sh|python3?|rm|curl|wget)\b(?:\s|$)",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(bearer\s+|token\s*=|password\s*=|secret\s*=|credential\s*=|\bcredential[-_:][^\s,;]+)",
    re.IGNORECASE,
)
_PRIVATE_ID_PATTERN = re.compile(r"(cluster-id|cluster_id)", re.IGNORECASE)


def build_sanitized_example_live_lab_config() -> ExternalLiveLabConfig:
    hub_a = PhysicalHubConfig(
        physical_label="hub-a",
        context_ref="<runtime-only-context-ref>",
        kubeconfig_ref="<runtime-only-kubeconfig-ref>",
        expected_identity_fingerprint="<redacted-hub-identity-fingerprint>",
        expected_api_fingerprint="<redacted-api-fingerprint>",
        expected_cluster_version="optional-redacted-version",
        acm_hub_evidence_requirements=("managed-cluster-inventory", "backup-restore-evidence"),
    )
    hub_b = PhysicalHubConfig(
        physical_label="hub-b",
        context_ref="<runtime-only-context-ref>",
        kubeconfig_ref="<runtime-only-kubeconfig-ref>",
        expected_identity_fingerprint="<redacted-hub-identity-fingerprint>",
        expected_api_fingerprint="<redacted-api-fingerprint>",
        acm_hub_evidence_requirements=("managed-cluster-inventory", "backup-restore-evidence"),
    )
    return ExternalLiveLabConfig(
        schema_version=LiveConfigSchemaVersion.PHASE_8C,
        lab_id="redacted-lab",
        plan_id="read-only-preflight-design",
        physical_hubs=(hub_a, hub_b),
        managed_clusters=ManagedClusterConfig(
            expected_names=("mc-1", "mc-2", "mc-3"),
            cluster_identity_fingerprints=("<redacted-managed-cluster-fingerprint>",),
        ),
        approval=ApprovalConfig(),
        credentials=LiveCredentialPolicy(),
        identity_expectations=IdentityExpectationConfig(
            hub_identity_fingerprints=("<redacted-hub-identity-fingerprint>",),
            api_identity_fingerprints=("<redacted-api-fingerprint>",),
        ),
        scenario_allowlist=ScenarioAllowlistConfig(approved_scenarios=("preflight",)),
    )


def classify_live_config_field(field_path: str) -> LiveConfigFieldSensitivity:
    normalized = _normalize_field_path(field_path)
    if normalized in _RUNTIME_ONLY_FIELDS:
        return LiveConfigFieldSensitivity.RUNTIME_ONLY
    if normalized in _ARTIFACT_SAFE_FIELDS:
        return LiveConfigFieldSensitivity.ARTIFACT_SAFE
    if normalized in _REDACTED_OR_FINGERPRINT_FIELDS:
        return LiveConfigFieldSensitivity.REDACTED_OR_FINGERPRINT_ONLY
    return LiveConfigFieldSensitivity.FORBIDDEN_COMMITTED_VALUE


def is_artifact_safe_field(field_path: str) -> bool:
    return classify_live_config_field(field_path) is LiveConfigFieldSensitivity.ARTIFACT_SAFE


def required_live_gate_ids() -> tuple[LiveGateId, ...]:
    return (
        LiveGateId.L0,
        LiveGateId.L1,
        LiveGateId.L2,
        LiveGateId.L3,
        LiveGateId.L4,
        LiveGateId.L5,
        LiveGateId.L6,
        LiveGateId.L7,
        LiveGateId.L8,
        LiveGateId.L9,
        LiveGateId.L10,
    )


def validate_live_gate_set(gate_ids: Sequence[LiveGateId | str]) -> LiveConfigValidationResult:
    provided = {_enum_value(item) for item in gate_ids}
    expected = {_enum_value(item) for item in required_live_gate_ids()}
    reasons: list[str] = []
    blocking_fields: list[str] = []
    missing = sorted(expected - provided)
    unexpected = sorted(provided - expected)
    if missing:
        reasons.append(f"missing required live gate ids: {', '.join(missing)}")
        blocking_fields.append("live_gates")
    if unexpected:
        reasons.append(f"unknown live gate ids: {', '.join(unexpected)}")
        blocking_fields.append("live_gates")
    decision = LiveConfigValidationDecision.BLOCKED if reasons else LiveConfigValidationDecision.PASS
    summary = {
        "validation_decision": decision.value,
        "reasons": reasons,
        "required_gate_ids": [_enum_value(item) for item in required_live_gate_ids()],
        "gate_summary": summarize_live_gates(gate_ids),
    }
    return LiveConfigValidationResult(
        decision=decision,
        reasons=tuple(reasons),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        artifact_safe_summary=sanitize_artifact_payload(summary),
        redaction_status="redacted",
        live_execution_enabled=False,
        mutation_enabled=False,
        automatic_recovery_enabled=False,
        live_certification_evidence_enabled=False,
    )


def summarize_live_gates(gate_ids: Sequence[LiveGateId | str] | None = None) -> tuple[dict[str, str], ...]:
    selected = tuple(gate_ids or required_live_gate_ids())
    return tuple(
        {
            "gate_id": _enum_value(gate_id),
            "status": LiveGateStatus.DESIGN_ONLY.value,
            "phase8c_semantics": "model_only_not_executable",
        }
        for gate_id in selected
    )


def validate_no_committed_sensitive_values(
    config: ExternalLiveLabConfig | Mapping[str, Any],
) -> LiveConfigValidationResult:
    raw_payload = config if isinstance(config, Mapping) else None
    coerced, reasons, blocking_fields = _coerce_config(config)
    if raw_payload is not None:
        _validate_forbidden_values(raw_payload, reasons, blocking_fields)
    if coerced is not None:
        _validate_forbidden_values(coerced.to_dict(), reasons, blocking_fields)
    decision = LiveConfigValidationDecision.BLOCKED if reasons else LiveConfigValidationDecision.PASS
    summary = {
        "validation_decision": decision.value,
        "reasons": reasons,
        "blocking_fields": blocking_fields,
        "redaction_status": "blocked" if blocking_fields else "redacted",
    }
    return LiveConfigValidationResult(
        decision=decision,
        reasons=tuple(reasons),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        artifact_safe_summary=sanitize_artifact_payload(summary),
        redaction_status="blocked" if blocking_fields else "redacted",
        live_execution_enabled=False,
        mutation_enabled=False,
        automatic_recovery_enabled=False,
        live_certification_evidence_enabled=False,
    )


def validate_external_live_lab_config(
    config: ExternalLiveLabConfig | Mapping[str, Any],
) -> LiveConfigValidationResult:
    raw_payload = config if isinstance(config, Mapping) else None
    coerced, reasons, blocking_fields = _coerce_config(config)
    before_raw_forbidden = len(blocking_fields)
    if raw_payload is not None:
        _validate_forbidden_values(raw_payload, reasons, blocking_fields)
    raw_forbidden_blocked = len(blocking_fields) > before_raw_forbidden
    if coerced is None:
        return _validation_result(
            config=None,
            reasons=reasons,
            blocking_fields=blocking_fields,
            forbidden_value_blocked=True,
        )

    _validate_schema_version(coerced, reasons, blocking_fields)
    _validate_field_sensitivity_model(reasons, blocking_fields)
    _validate_physical_hubs(coerced, reasons, blocking_fields)
    _validate_managed_clusters(coerced, reasons, blocking_fields)
    _validate_approval(coerced, reasons, blocking_fields)
    _validate_credentials(coerced, reasons, blocking_fields)
    _validate_identity_expectations(coerced, reasons, blocking_fields)
    _validate_role_discovery(coerced, reasons, blocking_fields)
    _validate_rbac_prerequisites(coerced, reasons, blocking_fields)
    _validate_artifact_policy(coerced, reasons, blocking_fields)
    _validate_redaction_policy(coerced, reasons, blocking_fields)
    _validate_execution_policy(coerced, reasons, blocking_fields)
    _validate_scenario_allowlist(coerced, reasons, blocking_fields)
    before_forbidden = len(blocking_fields)
    _validate_forbidden_values(coerced.to_dict(), reasons, blocking_fields)
    return _validation_result(
        config=coerced,
        reasons=reasons,
        blocking_fields=blocking_fields,
        forbidden_value_blocked=raw_forbidden_blocked or len(blocking_fields) > before_forbidden,
    )


def redact_live_config_summary(config: ExternalLiveLabConfig | Mapping[str, Any]) -> dict[str, Any]:
    return validate_external_live_lab_config(config).artifact_safe_summary


def _validation_result(
    *,
    config: ExternalLiveLabConfig | None,
    reasons: list[str],
    blocking_fields: list[str],
    forbidden_value_blocked: bool,
) -> LiveConfigValidationResult:
    unique_reasons = tuple(dict.fromkeys(reasons))
    unique_fields = tuple(dict.fromkeys(blocking_fields))
    decision = LiveConfigValidationDecision.BLOCKED if unique_reasons else LiveConfigValidationDecision.PASS
    redaction_status = "blocked" if forbidden_value_blocked else "redacted"
    summary = _artifact_safe_summary(
        config=config,
        decision=decision,
        reasons=unique_reasons,
        blocking_fields=unique_fields,
        redaction_status=redaction_status,
    )
    execution_policy = config.execution_policy if config is not None else LiveExecutionPolicy()
    return LiveConfigValidationResult(
        decision=decision,
        reasons=unique_reasons,
        blocking_fields=unique_fields,
        artifact_safe_summary=summary,
        redaction_status=redaction_status,
        live_execution_enabled=execution_policy.live_execution_enabled,
        mutation_enabled=execution_policy.mutation_enabled,
        automatic_recovery_enabled=execution_policy.automatic_recovery_enabled,
        live_certification_evidence_enabled=execution_policy.live_certification_evidence_enabled,
    )


def _artifact_safe_summary(
    *,
    config: ExternalLiveLabConfig | None,
    decision: LiveConfigValidationDecision,
    reasons: tuple[str, ...],
    blocking_fields: tuple[str, ...],
    redaction_status: str,
) -> dict[str, Any]:
    execution_policy = config.execution_policy if config is not None else LiveExecutionPolicy()
    summary: dict[str, Any] = {
        "schema_version": _enum_value(config.schema_version) if config is not None else None,
        "validation_decision": decision.value,
        "reasons": list(reasons),
        "blocking_fields": list(blocking_fields),
        "redaction_status": redaction_status,
        "live_execution_enabled": execution_policy.live_execution_enabled,
        "mutation_enabled": execution_policy.mutation_enabled,
        "automatic_recovery_enabled": execution_policy.automatic_recovery_enabled,
        "live_certification_evidence_enabled": execution_policy.live_certification_evidence_enabled,
        "required_gate_ids": [_enum_value(item) for item in required_live_gate_ids()],
        "live_gates": list(summarize_live_gates()),
        "unsupported": [
            "live_config_loading",
            "live_discovery",
            "live_execution",
            "live_adapters",
            "automatic_recovery",
            "production_schema_finalization",
            "agent_live_behavior",
        ],
    }
    if config is not None:
        summary.update(
            {
                "lab_id": _redacted_label(config.lab_id),
                "plan_id": _redacted_label(config.plan_id),
                "physical_hubs": [
                    {
                        "physical_label": hub.physical_label,
                        "expected_identity_fingerprint": _fingerprint_summary(hub.expected_identity_fingerprint),
                        "expected_api_fingerprint": _fingerprint_summary(hub.expected_api_fingerprint),
                        "acm_hub_evidence_requirements": list(hub.acm_hub_evidence_requirements),
                    }
                    for hub in config.physical_hubs
                ],
                "managed_clusters": {
                    "expected_count": len(config.managed_clusters.expected_names),
                    "expected_names": list(config.managed_clusters.expected_names),
                    "expected_names_sha256": _stable_hash(config.managed_clusters.expected_names),
                    "exact_match_required": config.managed_clusters.exact_match_required,
                    "unexpected_cluster_policy": config.managed_clusters.unexpected_cluster_policy,
                },
                "approval": {
                    "operator_confirmed_live_mode": config.approval.operator_confirmed_live_mode,
                    "mutation_allowed": config.approval.mutation_allowed,
                    "mutation_confirmation_required": config.approval.mutation_confirmation_required,
                    "approved_scenarios": list(config.approval.approved_scenarios),
                    "approver_reference": _redacted_label(config.approval.approver_reference),
                },
                "runtime_access_policy": {
                    "runtime_only": config.credentials.runtime_only,
                    "persist_to_artifacts": config.credentials.persist_to_artifacts,
                    "inherit_environment": config.credentials.inherit_environment,
                    "reference_count": len(config.credentials.references),
                },
                "identity_expectations": {
                    "hub_identity_fingerprint_count": len(config.identity_expectations.hub_identity_fingerprints),
                    "api_identity_fingerprint_count": len(config.identity_expectations.api_identity_fingerprints),
                    "mismatch_policy": config.identity_expectations.mismatch_policy,
                },
                "role_discovery": {
                    "required_evidence": list(config.role_discovery.required_evidence),
                    "active_role_policy": config.role_discovery.active_role_policy,
                    "ambiguity_policy": config.role_discovery.ambiguity_policy,
                    "fresh_discovery_required": config.role_discovery.fresh_discovery_required,
                },
                "rbac_prerequisites": {
                    "read_only_checks_required": config.rbac_prerequisites.read_only_checks_required,
                    "mutation_checks_required": config.rbac_prerequisites.mutation_checks_required,
                    "deny_checks_required": config.rbac_prerequisites.deny_checks_required,
                    "prerequisite_health_checks": list(config.rbac_prerequisites.prerequisite_health_checks),
                },
                "scenario_allowlist": {
                    "approved_scenarios": list(config.scenario_allowlist.approved_scenarios),
                    "allowlist_version": config.scenario_allowlist.allowlist_version,
                    "first_live_family": config.scenario_allowlist.first_live_family,
                    "unknown_scenario_policy": config.scenario_allowlist.unknown_scenario_policy,
                    "initially_live_allowed_scenarios": sorted(_INITIAL_LIVE_ALLOWED_SCENARIOS),
                },
                "artifact_policy": {
                    "artifact_dir_summary": _redacted_label(config.artifact_policy.artifact_dir),
                    "default_release_output": config.artifact_policy.default_release_output,
                    "commit_live_artifacts": config.artifact_policy.commit_live_artifacts,
                    "redaction_required": config.artifact_policy.redaction_required,
                    "stdout_stderr_sanitization_required": (config.artifact_policy.stdout_stderr_sanitization_required),
                },
                "redaction_policy": {
                    "required": config.redaction_policy.required,
                    "reject_raw_api_urls": config.redaction_policy.reject_raw_api_urls,
                    "reject_private_ids": config.redaction_policy.reject_private_ids,
                    "fingerprint_identity_values": config.redaction_policy.fingerprint_identity_values,
                    "reject_sensitive_runtime_values": config.redaction_policy.reject_credential_values,
                },
            }
        )
    sanitized = sanitize_artifact_payload(summary)
    validate_artifact_payload_redacted(sanitized)
    return sanitized


def _validate_schema_version(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    schema_version = _enum_value(config.schema_version)
    if not schema_version:
        _block(reasons, blocking_fields, "schema_version", "schema_version is required")
    elif schema_version != LiveConfigSchemaVersion.PHASE_8C.value:
        _block(reasons, blocking_fields, "schema_version", f"unsupported schema_version: {schema_version}")


def _validate_field_sensitivity_model(reasons: list[str], blocking_fields: list[str]) -> None:
    for field_path in sorted(_RUNTIME_ONLY_FIELDS):
        if is_artifact_safe_field(field_path):
            _block(
                reasons,
                blocking_fields,
                field_path,
                f"runtime-only field is incorrectly artifact-safe: {field_path}",
            )


def _validate_physical_hubs(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    hubs = config.physical_hubs
    if len(hubs) != 2:
        _block(reasons, blocking_fields, "physical_hubs", "exactly two physical hubs are required in Phase 8C")
    labels = [hub.physical_label for hub in hubs]
    if len(labels) != len(set(labels)):
        _block(reasons, blocking_fields, "physical_hubs", "physical hub labels must be unique")
    for index, hub in enumerate(hubs):
        if not hub.physical_label:
            _block(reasons, blocking_fields, f"physical_hubs[{index}].physical_label", "physical_label is required")
        if not hub.context_ref:
            _block(reasons, blocking_fields, f"physical_hubs[{index}].context_ref", "context_ref is required")
        if not hub.kubeconfig_ref:
            _block(reasons, blocking_fields, f"physical_hubs[{index}].kubeconfig_ref", "kubeconfig_ref is required")


def _validate_managed_clusters(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    managed = config.managed_clusters
    if not managed.expected_names:
        _block(
            reasons,
            blocking_fields,
            "managed_clusters.expected_names",
            "managed cluster expected_names must not be empty",
        )
    if not managed.exact_match_required:
        _block(
            reasons,
            blocking_fields,
            "managed_clusters.exact_match_required",
            "managed cluster exact_match_required must be true",
        )
    if managed.unexpected_cluster_policy != "block":
        _block(
            reasons,
            blocking_fields,
            "managed_clusters.unexpected_cluster_policy",
            "unexpected_cluster_policy must be block",
        )


def _validate_approval(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    approval = config.approval
    if approval.operator_confirmed_live_mode:
        _block(
            reasons,
            blocking_fields,
            "approval.operator_confirmed_live_mode",
            "committed or sanitized config must not pre-confirm live mode",
        )
    if approval.mutation_allowed:
        _block(
            reasons,
            blocking_fields,
            "approval.mutation_allowed",
            "committed or sanitized config must not allow mutation",
        )


def _validate_credentials(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    credentials = config.credentials
    if not credentials.runtime_only:
        _block(reasons, blocking_fields, "credentials.runtime_only", "credentials must be runtime-only")
    if credentials.persist_to_artifacts:
        _block(
            reasons,
            blocking_fields,
            "credentials.persist_to_artifacts",
            "credentials must not persist to artifacts",
        )
    if credentials.inherit_environment:
        _block(
            reasons,
            blocking_fields,
            "credentials.inherit_environment",
            "credentials must not inherit the process environment",
        )


def _validate_identity_expectations(
    config: ExternalLiveLabConfig,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if config.identity_expectations.mismatch_policy != "block":
        _block(
            reasons,
            blocking_fields,
            "identity_expectations.mismatch_policy",
            "identity mismatch policy must be block",
        )


def _validate_role_discovery(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    role_discovery = config.role_discovery
    if role_discovery.active_role_policy != "exactly-one-primary":
        _block(
            reasons,
            blocking_fields,
            "role_discovery.active_role_policy",
            "active_role_policy must be exactly-one-primary",
        )
    if role_discovery.ambiguity_policy != "block":
        _block(reasons, blocking_fields, "role_discovery.ambiguity_policy", "ambiguity_policy must be block")
    if not role_discovery.fresh_discovery_required:
        _block(
            reasons,
            blocking_fields,
            "role_discovery.fresh_discovery_required",
            "fresh role discovery must be required",
        )


def _validate_rbac_prerequisites(
    config: ExternalLiveLabConfig,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    prerequisites = config.rbac_prerequisites
    if not prerequisites.read_only_checks_required:
        _block(
            reasons,
            blocking_fields,
            "rbac_prerequisites.read_only_checks_required",
            "read-only prerequisite checks must be required",
        )
    if prerequisites.mutation_checks_required:
        _block(
            reasons,
            blocking_fields,
            "rbac_prerequisites.mutation_checks_required",
            "mutation prerequisite checks must remain disabled in Phase 8C",
        )


def _validate_artifact_policy(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    policy = config.artifact_policy
    if not policy.artifact_dir:
        _block(reasons, blocking_fields, "artifact_policy.artifact_dir", "artifact_dir must be caller-provided")
    if policy.artifact_dir == ".release" or policy.default_release_output:
        _block(
            reasons,
            blocking_fields,
            "artifact_policy.artifact_dir",
            "artifact_policy must not default to .release output",
        )
    if policy.commit_live_artifacts:
        _block(
            reasons,
            blocking_fields,
            "artifact_policy.commit_live_artifacts",
            "live artifacts must not be committed",
        )
    if not policy.redaction_required:
        _block(reasons, blocking_fields, "artifact_policy.redaction_required", "artifact redaction is required")


def _validate_redaction_policy(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    policy = config.redaction_policy
    if not policy.required:
        _block(reasons, blocking_fields, "redaction_policy.required", "redaction_policy.required must be true")
    if not policy.reject_raw_api_urls:
        _block(
            reasons,
            blocking_fields,
            "redaction_policy.reject_raw_api_urls",
            "redaction policy must reject raw API URLs",
        )
    if not policy.reject_private_ids:
        _block(
            reasons,
            blocking_fields,
            "redaction_policy.reject_private_ids",
            "redaction policy must reject private IDs",
        )
    if not policy.fingerprint_identity_values:
        _block(
            reasons,
            blocking_fields,
            "redaction_policy.fingerprint_identity_values",
            "redaction policy must fingerprint identity values",
        )
    if not policy.reject_credential_values:
        _block(
            reasons,
            blocking_fields,
            "redaction_policy.reject_credential_values",
            "redaction policy must reject unsafe runtime values",
        )


def _validate_execution_policy(config: ExternalLiveLabConfig, reasons: list[str], blocking_fields: list[str]) -> None:
    policy = config.execution_policy
    if policy.live_execution_enabled:
        _block(
            reasons,
            blocking_fields,
            "execution_policy.live_execution_enabled",
            "live execution must remain disabled in Phase 8C",
        )
    if policy.mutation_enabled:
        _block(
            reasons,
            blocking_fields,
            "execution_policy.mutation_enabled",
            "mutation must remain disabled in Phase 8C",
        )
    if policy.automatic_recovery_enabled:
        _block(
            reasons,
            blocking_fields,
            "execution_policy.automatic_recovery_enabled",
            "automatic recovery must remain disabled in Phase 8C",
        )
    if policy.live_certification_evidence_enabled:
        _block(
            reasons,
            blocking_fields,
            "execution_policy.live_certification_evidence_enabled",
            "live certification evidence must remain disabled in Phase 8C",
        )


def _validate_scenario_allowlist(
    config: ExternalLiveLabConfig,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    allowlist = config.scenario_allowlist
    approved = tuple(dict.fromkeys(allowlist.approved_scenarios))
    if allowlist.unknown_scenario_policy != "block":
        _block(
            reasons,
            blocking_fields,
            "scenario_allowlist.unknown_scenario_policy",
            "unknown_scenario_policy must be block",
        )
    for scenario_id in approved:
        scenario = SCENARIOS_BY_ID.get(scenario_id)
        if scenario is None:
            _block(
                reasons,
                blocking_fields,
                "scenario_allowlist.approved_scenarios",
                f"unknown scenario ID is not allowed: {scenario_id}",
            )
            continue
        if scenario.mutates_lab:
            _block(
                reasons,
                blocking_fields,
                "scenario_allowlist.approved_scenarios",
                f"mutating scenario is not initially live-allowed: {scenario_id}",
            )
        elif scenario_id not in _INITIAL_LIVE_ALLOWED_SCENARIOS:
            _block(
                reasons,
                blocking_fields,
                "scenario_allowlist.approved_scenarios",
                f"scenario is not in the Phase 8C initial read-only allowlist: {scenario_id}",
            )


def _validate_forbidden_values(value: Any, reasons: list[str], blocking_fields: list[str], path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _validate_forbidden_values(child, reasons, blocking_fields, child_path)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_forbidden_values(child, reasons, blocking_fields, f"{path}[{index}]")
        return
    if not isinstance(value, str) or _is_allowed_placeholder(value):
        return
    lowered = value.lower()
    if (
        _URL_PATTERN.search(value)
        or _KUBECONFIG_PATH_PATTERN.search(value)
        or _CREDENTIAL_VALUE_PATTERN.search(value)
        or _PRIVATE_ID_PATTERN.search(value)
        or _SHELL_COMMAND_PATTERN.search(value)
        or ".release" in lowered
    ):
        _block(
            reasons,
            blocking_fields,
            path,
            f"forbidden real-looking committed value at {path or '<root>'}",
        )


def _coerce_config(
    config: ExternalLiveLabConfig | Mapping[str, Any],
) -> tuple[ExternalLiveLabConfig | None, list[str], list[str]]:
    if isinstance(config, ExternalLiveLabConfig):
        return config, [], []
    if not isinstance(config, Mapping):
        return None, ["config must be an ExternalLiveLabConfig or mapping"], ["config"]
    try:
        return ExternalLiveLabConfig.from_mapping(config), [], []
    except (TypeError, ValueError, AttributeError) as exc:
        return None, [f"config could not be parsed: {exc}"], ["config"]


def _physical_hub_from_mapping(value: Any) -> PhysicalHubConfig:
    payload = value if isinstance(value, Mapping) else {}
    return PhysicalHubConfig(
        physical_label=str(payload.get("physical_label", "")),
        context_ref=str(payload.get("context_ref", "")),
        kubeconfig_ref=str(payload.get("kubeconfig_ref", "")),
        expected_identity_fingerprint=str(payload.get("expected_identity_fingerprint", "")),
        expected_api_fingerprint=str(payload.get("expected_api_fingerprint", "")),
        expected_cluster_version=_optional_str(payload.get("expected_cluster_version")),
        acm_hub_evidence_requirements=_string_tuple(payload.get("acm_hub_evidence_requirements")),
    )


def _managed_clusters_from_mapping(value: Any) -> ManagedClusterConfig:
    payload = value if isinstance(value, Mapping) else {}
    return ManagedClusterConfig(
        expected_names=_string_tuple(payload.get("expected_names")),
        exact_match_required=_bool_value(payload.get("exact_match_required"), default=True),
        unexpected_cluster_policy=str(payload.get("unexpected_cluster_policy", "block")),
        cluster_identity_fingerprints=_string_tuple(payload.get("cluster_identity_fingerprints")),
    )


def _approval_from_mapping(value: Any) -> ApprovalConfig:
    payload = value if isinstance(value, Mapping) else {}
    return ApprovalConfig(
        operator_confirmed_live_mode=_bool_value(payload.get("operator_confirmed_live_mode"), default=False),
        mutation_allowed=_bool_value(payload.get("mutation_allowed"), default=False),
        mutation_confirmation_required=_bool_value(payload.get("mutation_confirmation_required"), default=True),
        approved_scenarios=_string_tuple(payload.get("approved_scenarios", ("preflight",))),
        approval_timestamp=_optional_str(payload.get("approval_timestamp")),
        approver_reference=_optional_str(payload.get("approver_reference", "<operator-provided-approval-ref>")),
    )


def _credentials_from_mapping(value: Any) -> LiveCredentialPolicy:
    payload = value if isinstance(value, Mapping) else {}
    return LiveCredentialPolicy(
        runtime_only=_bool_value(payload.get("runtime_only"), default=True),
        persist_to_artifacts=_bool_value(payload.get("persist_to_artifacts"), default=False),
        inherit_environment=_bool_value(payload.get("inherit_environment"), default=False),
        references=tuple(_credential_reference_from_mapping(item) for item in _sequence(payload.get("references"))),
        allowed_env_vars=_string_tuple(payload.get("allowed_env_vars")),
        forbidden_env_patterns=_string_tuple(payload.get("forbidden_env_patterns"))
        or LiveCredentialPolicy().forbidden_env_patterns,
    )


def _credential_reference_from_mapping(value: Any) -> CredentialReferenceConfig:
    payload = value if isinstance(value, Mapping) else {}
    return CredentialReferenceConfig(
        name=str(payload.get("name", "")),
        runtime_ref=str(payload.get("runtime_ref", "")),
        required=_bool_value(payload.get("required"), default=True),
    )


def _identity_expectations_from_mapping(value: Any) -> IdentityExpectationConfig:
    payload = value if isinstance(value, Mapping) else {}
    return IdentityExpectationConfig(
        hub_identity_fingerprints=_string_tuple(
            payload.get("hub_identity_fingerprints", ("<redacted-hub-identity-fingerprint>",))
        ),
        api_identity_fingerprints=_string_tuple(
            payload.get("api_identity_fingerprints", ("<redacted-api-fingerprint>",))
        ),
        cluster_version_expectations=_string_tuple(payload.get("cluster_version_expectations")),
        mismatch_policy=str(payload.get("mismatch_policy", "block")),
    )


def _role_discovery_from_mapping(value: Any) -> RoleDiscoveryConfig:
    payload = value if isinstance(value, Mapping) else {}
    return RoleDiscoveryConfig(
        required_evidence=_string_tuple(payload.get("required_evidence")) or RoleDiscoveryConfig().required_evidence,
        active_role_policy=str(payload.get("active_role_policy", "exactly-one-primary")),
        ambiguity_policy=str(payload.get("ambiguity_policy", "block")),
        fresh_discovery_required=_bool_value(payload.get("fresh_discovery_required"), default=True),
    )


def _rbac_prerequisites_from_mapping(value: Any) -> RbacPrerequisiteConfig:
    payload = value if isinstance(value, Mapping) else {}
    return RbacPrerequisiteConfig(
        read_only_checks_required=_bool_value(payload.get("read_only_checks_required"), default=True),
        mutation_checks_required=_bool_value(payload.get("mutation_checks_required"), default=False),
        deny_checks_required=_bool_value(payload.get("deny_checks_required"), default=False),
        prerequisite_health_checks=_string_tuple(payload.get("prerequisite_health_checks"))
        or RbacPrerequisiteConfig().prerequisite_health_checks,
    )


def _scenario_allowlist_from_mapping(value: Any) -> ScenarioAllowlistConfig:
    payload = value if isinstance(value, Mapping) else {}
    return ScenarioAllowlistConfig(
        approved_scenarios=_string_tuple(payload.get("approved_scenarios", ("preflight",))),
        allowlist_version=str(payload.get("allowlist_version", "design-only")),
        first_live_family=str(payload.get("first_live_family", "read-only-preflight-only")),
        unknown_scenario_policy=str(payload.get("unknown_scenario_policy", "block")),
    )


def _artifact_policy_from_mapping(value: Any) -> LiveArtifactPolicy:
    payload = value if isinstance(value, Mapping) else {}
    return LiveArtifactPolicy(
        artifact_dir=str(payload.get("artifact_dir", "<caller-provided-artifact-dir>")),
        default_release_output=_bool_value(payload.get("default_release_output"), default=False),
        commit_live_artifacts=_bool_value(payload.get("commit_live_artifacts"), default=False),
        redaction_required=_bool_value(payload.get("redaction_required"), default=True),
        stdout_stderr_sanitization_required=_bool_value(
            payload.get("stdout_stderr_sanitization_required"),
            default=True,
        ),
        retention_policy=str(payload.get("retention_policy", "caller-defined")),
    )


def _redaction_policy_from_mapping(value: Any) -> LiveRedactionPolicy:
    payload = value if isinstance(value, Mapping) else {}
    default = LiveRedactionPolicy()
    return LiveRedactionPolicy(
        required=_bool_value(payload.get("required"), default=True),
        reject_raw_api_urls=_bool_value(payload.get("reject_raw_api_urls"), default=True),
        reject_private_ids=_bool_value(payload.get("reject_private_ids"), default=True),
        fingerprint_identity_values=_bool_value(payload.get("fingerprint_identity_values"), default=True),
        reject_credential_values=_bool_value(payload.get("reject_credential_values"), default=True),
        forbidden_artifact_patterns=_string_tuple(payload.get("forbidden_artifact_patterns"))
        or default.forbidden_artifact_patterns,
    )


def _execution_policy_from_mapping(value: Any) -> LiveExecutionPolicy:
    payload = value if isinstance(value, Mapping) else {}
    return LiveExecutionPolicy(
        live_execution_enabled=_bool_value(payload.get("live_execution_enabled"), default=False),
        read_only_discovery_enabled=_bool_value(payload.get("read_only_discovery_enabled"), default=False),
        mutation_enabled=_bool_value(payload.get("mutation_enabled"), default=False),
        automatic_recovery_enabled=_bool_value(payload.get("automatic_recovery_enabled"), default=False),
        live_certification_evidence_enabled=_bool_value(
            payload.get("live_certification_evidence_enabled"),
            default=False,
        ),
    )


def _sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _string_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value) if str(item))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return not default


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _normalize_field_path(field_path: str) -> str:
    normalized = re.sub(r"\[\d+\]", "", field_path)
    parts = normalized.split(".")
    if len(parts) >= 3 and parts[0] in {"physical_hubs", "credentials"}:
        return ".".join((parts[0], parts[-1]))
    return normalized


def _block(reasons: list[str], blocking_fields: list[str], field_path: str, reason: str) -> None:
    reasons.append(reason)
    blocking_fields.append(field_path)


def _is_allowed_placeholder(value: str) -> bool:
    return value in _ALLOWED_PLACEHOLDERS or (value.startswith("<redacted-") and value.endswith(">"))


def _stable_hash(values: Sequence[str]) -> str:
    payload = json.dumps(list(values), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fingerprint_summary(value: str | None) -> str | None:
    if value is None:
        return None
    if _is_allowed_placeholder(value):
        return value
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _redacted_label(value: str | None) -> str | None:
    if value is None:
        return None
    if value in _ALLOWED_PLACEHOLDERS or value.startswith("redacted-") or value.endswith("-design"):
        return value
    return "[REDACTED]"


__all__ = [
    "ApprovalConfig",
    "CredentialReferenceConfig",
    "ExternalLiveLabConfig",
    "IdentityExpectationConfig",
    "LiveArtifactPolicy",
    "LiveConfigFieldSensitivity",
    "LiveConfigSchemaVersion",
    "LiveConfigValidationDecision",
    "LiveConfigValidationResult",
    "LiveCredentialPolicy",
    "LiveExecutionPolicy",
    "LiveGateId",
    "LiveGateStatus",
    "LiveRedactionPolicy",
    "ManagedClusterConfig",
    "PhysicalHubConfig",
    "RbacPrerequisiteConfig",
    "RoleDiscoveryConfig",
    "ScenarioAllowlistConfig",
    "build_sanitized_example_live_lab_config",
    "classify_live_config_field",
    "is_artifact_safe_field",
    "redact_live_config_summary",
    "required_live_gate_ids",
    "summarize_live_gates",
    "validate_external_live_lab_config",
    "validate_live_gate_set",
    "validate_no_committed_sensitive_values",
]
