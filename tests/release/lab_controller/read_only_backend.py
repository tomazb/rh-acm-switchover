"""Pure read-only backend interface contracts for the lab role controller.

Phase 8G provides typed request/result/evidence models for a future read-only discovery backend.
It deliberately does not implement a transport, load live config files, read kubeconfigs, inspect
the process environment, shell out, call release adapters, contact clusters, write artifacts, or
execute query plans. The unimplemented backend fails closed with ``BLOCKED`` so later phases can
wire against the interface without accidentally crossing into live execution.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Protocol

from tests.release.lab_controller.artifacts import sanitize_artifact_payload, validate_artifact_payload_redacted
from tests.release.lab_controller.live_config import LiveGateId
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyDiscoveryGuardDecision,
    ReadOnlyDiscoveryGuardResult,
    ReadOnlyQueryPlan,
    ReadOnlyScenarioEligibility,
    read_only_scenario_eligibility,
    required_read_only_discovery_gate_ids,
    summarize_read_only_discovery_gates,
    summarize_read_only_query_plan,
    validate_read_only_discovery_gates,
    validate_read_only_query_plan,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_KUBECONFIG_PATH_PATTERN = re.compile(r"(^|[\s'\"])(/home/|/tmp/|~/\.kube/|[^ \t\n'\";]+[/\\]\.kube[/\\])")
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(bearer\s+|token\s*[:=]|password\s*[:=]|secret\s*[:=]|credential\s*[:=]|\bcredential[-_:][^\s,;]+)",
    re.IGNORECASE,
)
_PRIVATE_ID_PATTERN = re.compile(r"\bcluster[-_]id(?:[-_:][A-Za-z0-9][\w.-]*)?\b", re.IGNORECASE)
_COMMAND_LIKE_PATTERN = re.compile(
    r"\b(?:oc|kubectl|ansible-playbook|bash|sh|python3?|rm|curl|wget)\b(?=\s|$|[|;&])",
    re.IGNORECASE,
)
_RELEASE_PATH_MARKER = "." + "release"

_FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS = (
    "kubeconfig_ref",
    "context_ref",
    "credential_ref",
    "transport_handle_ref",
    "runtime_only_hub_refs",
    "runtime_ref",
    "raw_api",
    "api_url",
    "api_server",
    "token",
    "password",
    "secret",
    "raw_command",
    "command_string",
    "argv",
)
_VALID_REDACTION_STATUSES = frozenset({"redacted", "safe", "pass"})
_SATISFIED_GATE_STATUS = "satisfied"


class ReadOnlyBackendDecision(str, Enum):
    """Controller decision vocabulary for future read-only backend interactions."""

    PASS = "PASS"  # nosec B105
    BLOCKED = "BLOCKED"
    NO_GO = "NO_GO"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    INFRA_RETRYABLE = "INFRA_RETRYABLE"


class ReadOnlyBackendPhase(str, Enum):
    """Implementation phase represented by a backend result or summary."""

    INTERFACE_SKELETON = "interface_skeleton"
    FAKE_TRANSPORT_FUTURE = "fake_transport_future"
    LIVE_TRANSPORT_FUTURE = "live_transport_future"


@dataclass(frozen=True)
class RuntimeOnlyHubRef:
    """Runtime-only hub handles for a future backend.

    Values are opaque references supplied by a future caller. They are never dereferenced here and
    are never copied into artifact-facing summaries.
    """

    physical_label: str
    kubeconfig_ref: str
    context_ref: str
    credential_ref: str | None = None
    transport_handle_ref: str | None = None

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "physical_label": self.physical_label,
                "cluster_access_handle_present": bool(self.kubeconfig_ref),
                "context_handle_present": bool(self.context_ref),
                "auth_handle_present": self.credential_ref is not None,
                "transport_handle_present": self.transport_handle_ref is not None,
                "runtime_values_redacted": True,
            }
        )


@dataclass(frozen=True)
class ReadOnlyQueryPlanBundle:
    """Phase 8E-validated query plans for a future read-only backend."""

    scenario_id: str
    query_plans: tuple[ReadOnlyQueryPlan, ...]
    required_gate_ids: tuple[LiveGateId | str, ...]
    guardrail_decisions: tuple[ReadOnlyDiscoveryGuardResult, ...]
    all_queries_guardrail_passed: bool
    live_certification_evidence: bool = False


@dataclass(frozen=True)
class ReadOnlyGuardrailEvidence:
    """Proof that Phase 8E guardrails ran and passed before any future contact."""

    gate_result: ReadOnlyDiscoveryGuardResult
    query_results: tuple[ReadOnlyDiscoveryGuardResult, ...]
    guardrails_passed: bool
    validated_before_contact: bool
    no_live_contact: bool = True
    live_certification_evidence: bool = False


@dataclass(frozen=True)
class PhysicalIdentityEvidence:
    physical_label: str
    expected_fingerprint_summary: str | None
    observed_fingerprint_summary: str | None
    signal_count: int
    matched_signals: tuple[str, ...]
    missing_signals: tuple[str, ...]
    mismatch_reason: str | None
    proven: bool
    live_certification_evidence: bool = False

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "physical_label": self.physical_label,
                "expected_fingerprint_summary": self.expected_fingerprint_summary,
                "observed_fingerprint_summary": self.observed_fingerprint_summary,
                "signal_count": self.signal_count,
                "matched_signal_count": len(self.matched_signals),
                "missing_signal_count": len(self.missing_signals),
                "mismatch_reason": self.mismatch_reason,
                "proven": self.proven,
                "live_certification_evidence": False,
            }
        )


@dataclass(frozen=True)
class LogicalRoleEvidence:
    primary_physical_label: str | None
    secondary_physical_label: str | None
    active_evidence_categories: tuple[str, ...]
    passive_evidence_categories: tuple[str, ...]
    ambiguous_evidence_categories: tuple[str, ...]
    previous_artifact_supporting_only: bool
    proven: bool
    live_certification_evidence: bool = False

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "primary_physical_label": self.primary_physical_label,
                "secondary_physical_label": self.secondary_physical_label,
                "active_evidence_categories": list(self.active_evidence_categories),
                "passive_evidence_categories": list(self.passive_evidence_categories),
                "ambiguous_evidence_count": len(self.ambiguous_evidence_categories),
                "previous_artifact_supporting_only": self.previous_artifact_supporting_only,
                "proven": self.proven,
                "live_certification_evidence": False,
            }
        )


@dataclass(frozen=True)
class ManagedClusterSetEvidence:
    expected_names: tuple[str, ...]
    observed_names: tuple[str, ...]
    missing_names: tuple[str, ...]
    extra_names: tuple[str, ...]
    exact_match: bool
    unexpected_cluster_policy: str
    live_certification_evidence: bool = False

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "expected_names": list(self.expected_names),
                "observed_count": len(self.observed_names),
                "missing_names": list(self.missing_names),
                "extra_names": list(self.extra_names),
                "exact_match": self.exact_match,
                "unexpected_cluster_policy": self.unexpected_cluster_policy,
                "live_certification_evidence": False,
            }
        )


@dataclass(frozen=True)
class ReadPrerequisiteEvidence:
    required_capabilities: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    denied_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    proven: bool
    live_certification_evidence: bool = False

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "required_capabilities": list(self.required_capabilities),
                "allowed_capabilities": list(self.allowed_capabilities),
                "denied_capabilities": list(self.denied_capabilities),
                "missing_capabilities": list(self.missing_capabilities),
                "proven": self.proven,
                "live_certification_evidence": False,
            }
        )


ReadOnlyReadPrerequisiteEvidence = ReadPrerequisiteEvidence


@dataclass(frozen=True)
class TransportSummary:
    backend_phase: ReadOnlyBackendPhase = ReadOnlyBackendPhase.INTERFACE_SKELETON
    transport_implemented: bool = False
    no_live_contact: bool = True
    live_contact_occurred: bool = False
    queries_executed_count: int = 0
    runtime_inputs_redacted: bool = True
    timeout_category: str | None = None
    error_category: str | None = None

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "backend_phase": self.backend_phase.value,
                "transport_implemented": self.transport_implemented,
                "no_live_contact": self.no_live_contact,
                "live_contact_occurred": self.live_contact_occurred,
                "queries_executed_count": self.queries_executed_count,
                "runtime_inputs_redacted": self.runtime_inputs_redacted,
                "timeout_category": self.timeout_category,
                "error_category": self.error_category,
            }
        )


@dataclass(frozen=True)
class ReadOnlyBackendArtifactSummary:
    """Typed wrapper for artifact-safe backend summary payloads."""

    backend_phase: ReadOnlyBackendPhase
    decision: ReadOnlyBackendDecision
    payload: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return _safe_summary(dict(self.payload))


@dataclass(frozen=True)
class ReadOnlyDiscoveryRequest:
    request_id: str
    plan_id: str
    scenario_id: str
    validated_config_summary: Mapping[str, Any]
    runtime_only_hub_refs: tuple[RuntimeOnlyHubRef, ...]
    expected_physical_labels: tuple[str, ...]
    expected_managed_cluster_names: tuple[str, ...]
    required_gate_status: Mapping[str, str]
    query_plan_bundle: ReadOnlyQueryPlanBundle
    guardrail_evidence: ReadOnlyGuardrailEvidence
    redaction_policy_summary: Mapping[str, Any]
    artifact_policy_summary: Mapping[str, Any]
    retry_policy_summary: Mapping[str, Any]
    live_execution_enabled: bool
    mutation_enabled: bool = False
    live_certification_evidence: bool = False


@dataclass(frozen=True)
class ReadOnlyDiscoveryResult:
    decision: ReadOnlyBackendDecision
    request_id: str | None
    scenario_id: str | None
    backend_phase: ReadOnlyBackendPhase = ReadOnlyBackendPhase.INTERFACE_SKELETON
    request_valid: bool = False
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()
    physical_identity_evidence: PhysicalIdentityEvidence | None = None
    logical_role_evidence: LogicalRoleEvidence | None = None
    managed_cluster_set_evidence: ManagedClusterSetEvidence | None = None
    read_prerequisite_evidence: ReadPrerequisiteEvidence | None = None
    gate_summary: tuple[Mapping[str, Any], ...] = ()
    query_plan_summary: tuple[Mapping[str, Any], ...] = ()
    transport_summary: TransportSummary = field(default_factory=TransportSummary)
    retry_allowed: bool = False
    manual_recovery_required: bool = False
    first_blocking_reason: str | None = None
    redaction_status: str = "redacted"
    mutation_enabled: bool = False
    live_certification_evidence: bool = False
    runtime_inputs_redacted: bool = True
    no_live_contact: bool = True
    executed_query_ids: tuple[str, ...] = ()
    artifact_safe_summary: Mapping[str, Any] = field(default_factory=dict)


class ReadOnlyDiscoveryBackendProtocol(Protocol):
    def validate_request(self, request: ReadOnlyDiscoveryRequest) -> ReadOnlyDiscoveryResult:
        """Validate a future discovery request without executing transport."""

    def run_discovery(self, request: ReadOnlyDiscoveryRequest) -> ReadOnlyDiscoveryResult:
        """Run discovery in a future backend implementation."""


class UnimplementedReadOnlyDiscoveryBackend:
    """Phase 8G fail-closed backend placeholder.

    This backend validates requests but never executes query plans and never contacts live clusters.
    """

    def validate_request(self, request: ReadOnlyDiscoveryRequest) -> ReadOnlyDiscoveryResult:
        return validate_read_only_discovery_request(request)

    def run_discovery(self, request: ReadOnlyDiscoveryRequest) -> ReadOnlyDiscoveryResult:
        validation = validate_read_only_discovery_request(request)
        if validation.decision is not ReadOnlyBackendDecision.PASS:
            return validation

        reason = "read-only backend transport is not implemented in Phase 8G"
        result = ReadOnlyDiscoveryResult(
            decision=ReadOnlyBackendDecision.BLOCKED,
            request_id=request.request_id,
            scenario_id=request.scenario_id,
            backend_phase=ReadOnlyBackendPhase.INTERFACE_SKELETON,
            request_valid=True,
            reasons=(reason,),
            blocking_fields=("transport",),
            gate_summary=validation.gate_summary,
            query_plan_summary=validation.query_plan_summary,
            transport_summary=TransportSummary(
                backend_phase=ReadOnlyBackendPhase.INTERFACE_SKELETON,
                transport_implemented=False,
                no_live_contact=True,
                live_contact_occurred=False,
                queries_executed_count=0,
                runtime_inputs_redacted=True,
            ),
            first_blocking_reason=reason,
            redaction_status="redacted",
            mutation_enabled=False,
            live_certification_evidence=False,
            runtime_inputs_redacted=True,
            no_live_contact=True,
            executed_query_ids=(),
        )
        return replace(result, artifact_safe_summary=summarize_read_only_backend_result(result))


def validate_read_only_discovery_request(request: ReadOnlyDiscoveryRequest) -> ReadOnlyDiscoveryResult:
    """Validate request shape and Phase 8E proof without performing discovery."""
    reasons: list[str] = []
    blocking_fields: list[str] = []

    if not isinstance(request, ReadOnlyDiscoveryRequest):
        return _validation_result(
            request=None,
            decision=ReadOnlyBackendDecision.BLOCKED,
            reasons=("request must be a ReadOnlyDiscoveryRequest",),
            blocking_fields=("request",),
        )

    _validate_request_identity(request, reasons, blocking_fields)
    _validate_request_safety_flags(request, reasons, blocking_fields)
    _validate_request_scenario(request, reasons, blocking_fields)
    _validate_artifact_facing_mapping(
        request.validated_config_summary,
        "validated_config_summary",
        reasons,
        blocking_fields,
    )
    _validate_artifact_facing_mapping(
        request.redaction_policy_summary,
        "redaction_policy_summary",
        reasons,
        blocking_fields,
    )
    _validate_artifact_facing_mapping(
        request.artifact_policy_summary,
        "artifact_policy_summary",
        reasons,
        blocking_fields,
    )
    _validate_artifact_facing_mapping(
        request.retry_policy_summary,
        "retry_policy_summary",
        reasons,
        blocking_fields,
    )
    _validate_runtime_only_refs(request, reasons, blocking_fields)
    _validate_expected_values(request, reasons, blocking_fields)
    _validate_required_gate_status(request, reasons, blocking_fields)
    _validate_query_plan_bundle(request.query_plan_bundle, request.scenario_id, reasons, blocking_fields)
    _validate_guardrail_evidence(request.guardrail_evidence, reasons, blocking_fields)

    decision = ReadOnlyBackendDecision.BLOCKED if reasons else ReadOnlyBackendDecision.PASS
    return _validation_result(
        request=request,
        decision=decision,
        reasons=tuple(dict.fromkeys(reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
    )


def validate_read_only_discovery_result(result: ReadOnlyDiscoveryResult) -> ReadOnlyDiscoveryResult:
    """Validate a backend result contract without interpreting live evidence."""
    if not isinstance(result, ReadOnlyDiscoveryResult):
        return _result_contract_failure(
            result=None,
            reasons=("result must be a ReadOnlyDiscoveryResult",),
            blocking_fields=("result",),
        )

    reasons: list[str] = []
    blocking_fields: list[str] = []

    if result.mutation_enabled is not False:
        _block(reasons, blocking_fields, "mutation_enabled", "mutation_enabled must be exactly false")
    if result.live_certification_evidence is not False:
        _block(
            reasons,
            blocking_fields,
            "live_certification_evidence",
            "live_certification_evidence must be exactly false",
        )
    if result.runtime_inputs_redacted is not True:
        _block(reasons, blocking_fields, "runtime_inputs_redacted", "runtime_inputs_redacted must be exactly true")
    if result.redaction_status not in _VALID_REDACTION_STATUSES:
        _block(reasons, blocking_fields, "redaction_status", "redaction_status must be redacted/safe/pass")

    _validate_transport_summary(result.transport_summary, reasons, blocking_fields)
    _validate_evidence_certification_flags(result, reasons, blocking_fields)

    if result.decision is ReadOnlyBackendDecision.PASS:
        _validate_pass_result_evidence(result, reasons, blocking_fields)

    if _payload_has_forbidden_artifact_key(result.artifact_safe_summary) or _payload_has_unsafe_value(
        result.artifact_safe_summary
    ):
        _block(reasons, blocking_fields, "artifact_safe_summary", "result artifact summary is not artifact-safe")

    if reasons:
        return _result_contract_failure(
            result=result,
            reasons=tuple(dict.fromkeys(reasons)),
            blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        )
    return replace(result, artifact_safe_summary=summarize_read_only_backend_result(result))


def summarize_read_only_backend_request(request: ReadOnlyDiscoveryRequest) -> dict[str, Any]:
    """Return an artifact-safe request summary without runtime-only values."""
    validation = validate_read_only_discovery_request(request)
    query_summaries = []
    if isinstance(request, ReadOnlyDiscoveryRequest) and isinstance(request.query_plan_bundle, ReadOnlyQueryPlanBundle):
        query_summaries = [summarize_read_only_query_plan(plan) for plan in request.query_plan_bundle.query_plans]
    payload = {
        "backend_phase": ReadOnlyBackendPhase.INTERFACE_SKELETON.value,
        "request_id": request.request_id if isinstance(request, ReadOnlyDiscoveryRequest) else None,
        "plan_id": request.plan_id if isinstance(request, ReadOnlyDiscoveryRequest) else None,
        "scenario_id": request.scenario_id if isinstance(request, ReadOnlyDiscoveryRequest) else None,
        "decision": validation.decision.value,
        "reasons": list(validation.reasons),
        "gate_summary": list(validation.gate_summary),
        "query_plan_summary": query_summaries,
        "physical_labels": (
            list(request.expected_physical_labels) if isinstance(request, ReadOnlyDiscoveryRequest) else []
        ),
        "expected_managed_cluster_count": (
            len(request.expected_managed_cluster_names) if isinstance(request, ReadOnlyDiscoveryRequest) else 0
        ),
        "expected_managed_cluster_names": (
            list(request.expected_managed_cluster_names) if isinstance(request, ReadOnlyDiscoveryRequest) else []
        ),
        "runtime_input_presence": (
            [hub_ref.to_artifact_safe_summary() for hub_ref in request.runtime_only_hub_refs]
            if isinstance(request, ReadOnlyDiscoveryRequest)
            else []
        ),
        "redaction_status": validation.redaction_status,
        "runtime_inputs_redacted": True,
        "live_execution_enabled": (
            request.live_execution_enabled is True if isinstance(request, ReadOnlyDiscoveryRequest) else False
        ),
        "mutation_enabled": False,
        "live_certification_evidence": False,
        "no_live_contact": True,
    }
    return _safe_summary(payload)


def summarize_read_only_backend_result(result: ReadOnlyDiscoveryResult) -> dict[str, Any]:
    """Return an artifact-safe result summary without runtime-only values."""
    transport_summary = (
        result.transport_summary if isinstance(result.transport_summary, TransportSummary) else TransportSummary()
    )
    payload = {
        "backend_phase": result.backend_phase.value,
        "request_id": result.request_id,
        "scenario_id": result.scenario_id,
        "decision": result.decision.value,
        "request_valid": result.request_valid,
        "reasons": list(result.reasons),
        "blocking_fields": list(result.blocking_fields),
        "gate_summary": list(result.gate_summary),
        "query_plan_summary": list(result.query_plan_summary),
        "physical_identity_evidence": _evidence_summary(result.physical_identity_evidence),
        "logical_role_evidence": _evidence_summary(result.logical_role_evidence),
        "managed_cluster_set_evidence": _evidence_summary(result.managed_cluster_set_evidence),
        "read_prerequisite_evidence": _evidence_summary(result.read_prerequisite_evidence),
        "transport_summary": transport_summary.to_artifact_safe_summary(),
        "retry_allowed": result.retry_allowed,
        "manual_recovery_required": result.manual_recovery_required,
        "first_blocking_reason": result.first_blocking_reason,
        "redaction_status": result.redaction_status,
        "runtime_inputs_redacted": result.runtime_inputs_redacted,
        "live_execution_enabled": False,
        "mutation_enabled": False,
        "live_certification_evidence": False,
        "no_live_contact": result.no_live_contact,
        "executed_query_count": len(result.executed_query_ids),
    }
    return _safe_summary(payload)


def _validation_result(
    *,
    request: ReadOnlyDiscoveryRequest | None,
    decision: ReadOnlyBackendDecision,
    reasons: Sequence[str],
    blocking_fields: Sequence[str],
) -> ReadOnlyDiscoveryResult:
    gate_ids: Sequence[LiveGateId | str] = ()
    query_summaries: tuple[Mapping[str, Any], ...] = ()
    if request is not None:
        if isinstance(request.required_gate_status, Mapping):
            gate_ids = tuple(request.required_gate_status.keys())
        if isinstance(request.query_plan_bundle, ReadOnlyQueryPlanBundle):
            query_summaries = tuple(
                summarize_read_only_query_plan(plan) for plan in request.query_plan_bundle.query_plans
            )
    result = ReadOnlyDiscoveryResult(
        decision=decision,
        request_id=request.request_id if request is not None else None,
        scenario_id=request.scenario_id if request is not None else None,
        backend_phase=ReadOnlyBackendPhase.INTERFACE_SKELETON,
        request_valid=decision is ReadOnlyBackendDecision.PASS,
        reasons=tuple(dict.fromkeys(reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        gate_summary=tuple(summarize_read_only_discovery_gates(gate_ids)),
        query_plan_summary=query_summaries,
        transport_summary=TransportSummary(
            backend_phase=ReadOnlyBackendPhase.INTERFACE_SKELETON,
            transport_implemented=False,
            no_live_contact=True,
            live_contact_occurred=False,
            queries_executed_count=0,
            runtime_inputs_redacted=True,
        ),
        first_blocking_reason=next(iter(reasons), None) if decision is not ReadOnlyBackendDecision.PASS else None,
        redaction_status="redacted",
        mutation_enabled=False,
        live_certification_evidence=False,
        runtime_inputs_redacted=True,
        no_live_contact=True,
        executed_query_ids=(),
    )
    return replace(result, artifact_safe_summary=summarize_read_only_backend_result(result))


def _result_contract_failure(
    *,
    result: ReadOnlyDiscoveryResult | None,
    reasons: Sequence[str],
    blocking_fields: Sequence[str],
) -> ReadOnlyDiscoveryResult:
    transport_summary = (
        result.transport_summary
        if result is not None and isinstance(result.transport_summary, TransportSummary)
        else TransportSummary()
    )
    failed = ReadOnlyDiscoveryResult(
        decision=ReadOnlyBackendDecision.BLOCKED,
        request_id=result.request_id if result is not None else None,
        scenario_id=result.scenario_id if result is not None else None,
        backend_phase=result.backend_phase if result is not None else ReadOnlyBackendPhase.INTERFACE_SKELETON,
        request_valid=False,
        reasons=tuple(dict.fromkeys(reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        gate_summary=result.gate_summary if result is not None else (),
        query_plan_summary=result.query_plan_summary if result is not None else (),
        transport_summary=transport_summary,
        first_blocking_reason=next(iter(reasons), None),
        redaction_status="redacted",
        mutation_enabled=False,
        live_certification_evidence=False,
        runtime_inputs_redacted=True,
        no_live_contact=True,
        executed_query_ids=(),
    )
    return replace(failed, artifact_safe_summary=summarize_read_only_backend_result(failed))


def _validate_request_identity(
    request: ReadOnlyDiscoveryRequest,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not request.request_id:
        _block(reasons, blocking_fields, "request_id", "request_id is required")
    if not request.plan_id:
        _block(reasons, blocking_fields, "plan_id", "plan_id is required")
    if not request.scenario_id:
        _block(reasons, blocking_fields, "scenario_id", "scenario_id is required")
    for field_name, value in (("request_id", request.request_id), ("plan_id", request.plan_id)):
        if _value_is_unsafe(value):
            _block(reasons, blocking_fields, field_name, f"{field_name} contains an unsafe artifact-facing value")


def _validate_request_safety_flags(
    request: ReadOnlyDiscoveryRequest,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not isinstance(request.live_execution_enabled, bool):
        _block(reasons, blocking_fields, "live_execution_enabled", "live_execution_enabled must be an explicit bool")
    if request.mutation_enabled is not False:
        _block(reasons, blocking_fields, "mutation_enabled", "mutation_enabled must be exactly false")
    if request.live_certification_evidence is not False:
        _block(
            reasons,
            blocking_fields,
            "live_certification_evidence",
            "live_certification_evidence must be exactly false",
        )


def _validate_request_scenario(
    request: ReadOnlyDiscoveryRequest,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if request.scenario_id not in SCENARIOS_BY_ID:
        _block(reasons, blocking_fields, "scenario_id", "unknown scenario id is unsupported")
        return
    eligibility = read_only_scenario_eligibility(request.scenario_id)
    if eligibility is not ReadOnlyScenarioEligibility.INITIALLY_ALLOWED:
        _block(reasons, blocking_fields, "scenario_id", "scenario is not eligible for read-only backend discovery")


def _validate_artifact_facing_mapping(
    value: Mapping[str, Any],
    field_name: str,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not isinstance(value, Mapping):
        _block(reasons, blocking_fields, field_name, f"{field_name} must be a mapping")
        return
    if _payload_has_forbidden_artifact_key(value):
        _block(reasons, blocking_fields, field_name, f"{field_name} contains runtime-only artifact fields")
    if _payload_has_unsafe_value(value):
        _block(reasons, blocking_fields, field_name, f"{field_name} contains unsafe artifact-facing values")


def _validate_runtime_only_refs(
    request: ReadOnlyDiscoveryRequest,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if request.live_execution_enabled is True and not request.runtime_only_hub_refs:
        _block(
            reasons,
            blocking_fields,
            "runtime_only_hub_refs",
            "runtime-only hub refs are required when future live read contact is intended",
        )

    labels = set()
    for hub_ref in request.runtime_only_hub_refs:
        labels.add(hub_ref.physical_label)
        if not hub_ref.physical_label:
            _block(reasons, blocking_fields, "runtime_only_hub_refs", "physical_label is required")
        if request.live_execution_enabled is True and (not hub_ref.kubeconfig_ref or not hub_ref.context_ref):
            _block(
                reasons,
                blocking_fields,
                "runtime_only_hub_refs",
                "cluster access and context handles are required for future contact",
            )
        for value in (
            hub_ref.physical_label,
            hub_ref.kubeconfig_ref,
            hub_ref.context_ref,
            hub_ref.credential_ref,
            hub_ref.transport_handle_ref,
        ):
            if value is not None and _value_is_unsafe(value):
                _block(
                    reasons,
                    blocking_fields,
                    "runtime_only_hub_refs",
                    "runtime-only hub refs contain unsafe raw runtime values",
                )
                break

    expected_labels = set(request.expected_physical_labels)
    if request.live_execution_enabled is True and expected_labels and labels != expected_labels:
        _block(
            reasons,
            blocking_fields,
            "runtime_only_hub_refs",
            "runtime-only hub refs must match expected physical labels",
        )


def _validate_expected_values(
    request: ReadOnlyDiscoveryRequest,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not request.expected_physical_labels:
        _block(reasons, blocking_fields, "expected_physical_labels", "expected physical labels are required")
    if not request.expected_managed_cluster_names:
        _block(
            reasons,
            blocking_fields,
            "expected_managed_cluster_names",
            "expected managed cluster names are required",
        )
    for label in request.expected_physical_labels:
        if _value_is_unsafe(label):
            _block(reasons, blocking_fields, "expected_physical_labels", "expected physical label is unsafe")
    for name in request.expected_managed_cluster_names:
        if _value_is_unsafe(name):
            _block(
                reasons,
                blocking_fields,
                "expected_managed_cluster_names",
                "expected managed cluster name is unsafe",
            )


def _validate_required_gate_status(
    request: ReadOnlyDiscoveryRequest,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not isinstance(request.required_gate_status, Mapping):
        _block(reasons, blocking_fields, "required_gate_status", "required_gate_status must be a mapping")
        return
    required = tuple(gate.value for gate in required_read_only_discovery_gate_ids())
    missing = [gate_id for gate_id in required if gate_id not in request.required_gate_status]
    not_satisfied = [
        gate_id
        for gate_id in required
        if gate_id in request.required_gate_status
        and request.required_gate_status.get(gate_id) != _SATISFIED_GATE_STATUS
    ]
    if missing:
        _block(reasons, blocking_fields, "required_gate_ids", "missing required L0-L9 gate status")
    if not_satisfied:
        _block(reasons, blocking_fields, "required_gate_status", "required gates must be exactly satisfied")


def _validate_query_plan_bundle(
    bundle: ReadOnlyQueryPlanBundle,
    request_scenario_id: str,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not isinstance(bundle, ReadOnlyQueryPlanBundle):
        _block(reasons, blocking_fields, "query_plan_bundle", "query_plan_bundle is required")
        return
    if bundle.scenario_id != request_scenario_id:
        _block(
            reasons,
            blocking_fields,
            "query_plan_bundle.scenario_id",
            "query plan bundle scenario must match request scenario",
        )
    if bundle.live_certification_evidence is not False:
        _block(
            reasons,
            blocking_fields,
            "query_plan_bundle",
            "query plan bundle must not claim live certification evidence",
        )
    if bundle.all_queries_guardrail_passed is not True:
        _block(
            reasons,
            blocking_fields,
            "query_plan_bundle",
            "all query guardrail decisions must be exactly PASS",
        )
    gate_result = validate_read_only_discovery_gates(bundle.required_gate_ids)
    if gate_result.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
        _block(reasons, blocking_fields, "required_gate_ids", "query plan bundle is missing required L0-L9 gates")
    if len(bundle.guardrail_decisions) != len(bundle.query_plans):
        _block(
            reasons,
            blocking_fields,
            "query_plan_bundle",
            "query plan bundle must include one guardrail decision per query plan",
        )
    for plan, recorded_result in zip(bundle.query_plans, bundle.guardrail_decisions):
        if plan.scenario_id != bundle.scenario_id:
            _block(reasons, blocking_fields, "query_plan_bundle", "query scenario must match bundle scenario")
        recomputed = validate_read_only_query_plan(plan)
        if recorded_result.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
            _block(reasons, blocking_fields, "query_plan_bundle", "recorded guardrail decision is not PASS")
        if recomputed.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
            _block(reasons, blocking_fields, "query_plan_bundle", "query plan fails Phase 8E guardrails")
        if plan.live_certification_evidence:
            _block(
                reasons,
                blocking_fields,
                "query_plan_bundle",
                "query plan must not claim live certification evidence",
            )
        if plan.l10_present and plan.mutates_state:
            _block(reasons, blocking_fields, "query_plan_bundle", "L10 cannot justify mutation in Phase 8G")


def _validate_guardrail_evidence(
    evidence: ReadOnlyGuardrailEvidence,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not isinstance(evidence, ReadOnlyGuardrailEvidence):
        _block(reasons, blocking_fields, "guardrail_evidence", "guardrail evidence is required")
        return
    if evidence.guardrails_passed is not True:
        _block(reasons, blocking_fields, "guardrail_evidence", "guardrails_passed must be exactly true")
    if evidence.validated_before_contact is not True:
        _block(reasons, blocking_fields, "guardrail_evidence", "guardrails must be validated before contact")
    if evidence.no_live_contact is not True:
        _block(reasons, blocking_fields, "guardrail_evidence", "Phase 8G guardrail evidence must have no live contact")
    if evidence.live_certification_evidence is not False:
        _block(
            reasons,
            blocking_fields,
            "guardrail_evidence",
            "guardrail evidence must not claim live certification evidence",
        )
    if not isinstance(evidence.gate_result, ReadOnlyDiscoveryGuardResult):
        _block(reasons, blocking_fields, "guardrail_evidence", "gate guardrail result is malformed")
    elif evidence.gate_result.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
        _block(reasons, blocking_fields, "guardrail_evidence", "gate guardrail result must be PASS")
    if not isinstance(evidence.query_results, tuple) or not all(
        isinstance(result, ReadOnlyDiscoveryGuardResult) for result in evidence.query_results
    ):
        _block(reasons, blocking_fields, "guardrail_evidence", "query guardrail results are malformed")
    elif any(result.decision is not ReadOnlyDiscoveryGuardDecision.PASS for result in evidence.query_results):
        _block(reasons, blocking_fields, "guardrail_evidence", "query guardrail results must all be PASS")


def _validate_transport_summary(
    transport_summary: TransportSummary,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if not isinstance(transport_summary, TransportSummary):
        _block(reasons, blocking_fields, "transport_summary", "transport_summary must be a TransportSummary")
        return
    if transport_summary.runtime_inputs_redacted is not True:
        _block(reasons, blocking_fields, "transport_summary", "transport runtime inputs must be redacted")
    if transport_summary.queries_executed_count < 0:
        _block(reasons, blocking_fields, "transport_summary", "queries_executed_count must not be negative")
    if transport_summary.live_contact_occurred and transport_summary.no_live_contact:
        _block(reasons, blocking_fields, "transport_summary", "transport contact flags are contradictory")


def _validate_evidence_certification_flags(
    result: ReadOnlyDiscoveryResult,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    for field_name, evidence in (
        ("physical_identity_evidence", result.physical_identity_evidence),
        ("logical_role_evidence", result.logical_role_evidence),
        ("managed_cluster_set_evidence", result.managed_cluster_set_evidence),
        ("read_prerequisite_evidence", result.read_prerequisite_evidence),
    ):
        if evidence is not None and evidence.live_certification_evidence is not False:
            _block(
                reasons,
                blocking_fields,
                field_name,
                "evidence objects must not claim live certification evidence in Phase 8G",
            )


def _validate_pass_result_evidence(
    result: ReadOnlyDiscoveryResult,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if result.request_valid is not True:
        _block(reasons, blocking_fields, "request_valid", "PASS requires a valid request")
    physical = result.physical_identity_evidence
    if physical is None or physical.proven is not True or physical.signal_count <= 0 or not physical.matched_signals:
        _block(reasons, blocking_fields, "physical_identity_evidence", "PASS requires physical identity proof")
    elif physical.missing_signals or physical.mismatch_reason:
        _block(reasons, blocking_fields, "physical_identity_evidence", "PASS cannot have identity gaps")

    logical = result.logical_role_evidence
    if (
        logical is None
        or logical.proven is not True
        or not logical.primary_physical_label
        or not logical.secondary_physical_label
    ):
        _block(reasons, blocking_fields, "logical_role_evidence", "PASS requires logical role proof")
    elif logical.ambiguous_evidence_categories:
        _block(reasons, blocking_fields, "logical_role_evidence", "PASS cannot have ambiguous logical role evidence")

    managed = result.managed_cluster_set_evidence
    if managed is None or managed.exact_match is not True:
        _block(reasons, blocking_fields, "managed_cluster_set_evidence", "PASS requires exact managed cluster set")
    elif (
        set(managed.expected_names) != set(managed.observed_names)
        or managed.missing_names
        or managed.extra_names
        or managed.unexpected_cluster_policy != "block"
    ):
        _block(reasons, blocking_fields, "managed_cluster_set_evidence", "PASS requires no managed cluster drift")

    prerequisites = result.read_prerequisite_evidence
    if prerequisites is None or prerequisites.proven is not True:
        _block(reasons, blocking_fields, "read_prerequisite_evidence", "PASS requires read prerequisite proof")
    elif prerequisites.missing_capabilities or prerequisites.denied_capabilities:
        _block(
            reasons, blocking_fields, "read_prerequisite_evidence", "PASS requires no denied or missing capabilities"
        )


def _evidence_summary(evidence: Any) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return evidence.to_artifact_safe_summary()


def _payload_has_forbidden_artifact_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS:
                return True
            if _payload_has_forbidden_artifact_key(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_payload_has_forbidden_artifact_key(item) for item in value)
    return False


def _payload_has_unsafe_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_payload_has_unsafe_value(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_payload_has_unsafe_value(item) for item in value)
    if isinstance(value, str):
        return _value_is_unsafe(value)
    return False


def _value_is_unsafe(value: str) -> bool:
    lowered = value.lower()
    return bool(
        _URL_PATTERN.search(value)
        or _KUBECONFIG_PATH_PATTERN.search(value)
        or _CREDENTIAL_VALUE_PATTERN.search(value)
        or _PRIVATE_ID_PATTERN.search(value)
        or _COMMAND_LIKE_PATTERN.search(value)
        or _RELEASE_PATH_MARKER in lowered
    )


def _safe_summary(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_artifact_payload(payload)
    validate_artifact_payload_redacted(sanitized)
    return sanitized


def _gate_value(gate_id: LiveGateId | str) -> str:
    if isinstance(gate_id, Enum):
        return str(gate_id.value)
    return str(gate_id)


def _block(reasons: list[str], blocking_fields: list[str], field_name: str, reason: str) -> None:
    reasons.append(reason)
    blocking_fields.append(field_name)


__all__ = [
    "LogicalRoleEvidence",
    "ManagedClusterSetEvidence",
    "PhysicalIdentityEvidence",
    "ReadOnlyBackendArtifactSummary",
    "ReadOnlyBackendDecision",
    "ReadOnlyBackendPhase",
    "ReadOnlyDiscoveryBackendProtocol",
    "ReadOnlyDiscoveryRequest",
    "ReadOnlyDiscoveryResult",
    "ReadOnlyGuardrailEvidence",
    "ReadOnlyQueryPlanBundle",
    "ReadOnlyReadPrerequisiteEvidence",
    "ReadPrerequisiteEvidence",
    "RuntimeOnlyHubRef",
    "TransportSummary",
    "UnimplementedReadOnlyDiscoveryBackend",
    "summarize_read_only_backend_request",
    "summarize_read_only_backend_result",
    "validate_read_only_discovery_request",
    "validate_read_only_discovery_result",
]
