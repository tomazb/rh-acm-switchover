"""Pure, deterministic read-only discovery guardrails for the lab role controller.

Phase 8E turns the Phase 8D read-only discovery design into deterministic, non-live guardrail
code. This module models the *future* read-only discovery surface so that any later read-only
discovery implementation must pass these guardrails before any live contact is possible.

Strict non-live boundary (intentionally enforced by tests):

- This module does not load live config files.
- It does not parse real YAML/JSON config files.
- It does not read kubeconfig files.
- It does not read ``os.environ``.
- It does not run subprocesses, ``oc``, ``kubectl``, or ``ansible-playbook``.
- It does not call release adapters or contact clusters.
- It does not write artifacts and does not implement live discovery.

It only classifies query families, verbs, and scenarios; validates future read-only query plans
and gate sets; validates future discovery artifact field contracts; and produces artifact-safe,
redacted summaries. All decisions are deterministic and fail closed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tests.release.lab_controller.artifacts import sanitize_artifact_payload, validate_artifact_payload_redacted
from tests.release.lab_controller.live_config import LiveGateId
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

# The redaction patterns below detect unsafe artifact *values*. They mirror the Phase 8C live-config
# patterns and operate on string values only (never on mapping keys), keeping the structural/shape
# decision (BLOCKED) separate from the redaction decision (NO_GO).
_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_KUBECONFIG_PATH_PATTERN = re.compile(r"(^|[\s'\"])(/home/|/tmp/|~/\.kube/|[^ \t\n'\";]+[/\\]\.kube[/\\])")
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(bearer\s+|token\s*[:=]|password\s*[:=]|secret\s*[:=]|credential\s*[:=]|\bcredential[-_:][^\s,;]+)",
    re.IGNORECASE,
)
_PRIVATE_ID_PATTERN = re.compile(r"\bcluster[-_]id(?:[-_:][A-Za-z0-9][\w.-]*)?\b", re.IGNORECASE)
_ARBITRARY_COMMAND_VALUE_PATTERN = re.compile(
    r"\b(?:oc|kubectl|ansible-playbook|bash|sh|python3?|rm|curl|wget)\b(?=\s|$|[|;&])",
    re.IGNORECASE,
)
_RELEASE_PATH_MARKER = "." + "release"


class ReadOnlyDiscoveryGuardDecision(str, Enum):
    """Deterministic guardrail decision for a future read-only discovery request."""

    PASS = "PASS"  # nosec B105
    BLOCKED = "BLOCKED"
    NO_GO = "NO_GO"


class ReadOnlyQueryFamily(str, Enum):
    """Future read-only discovery query families recognised by the guardrails."""

    CLUSTER_IDENTITY = "cluster_identity"
    NAMESPACE_UID = "namespace_uid"
    CLUSTER_VERSION = "cluster_version"
    ACM_MCE_MCH_STATUS = "acm_mce_mch_status"
    MANAGED_CLUSTER_STATUS = "managed_cluster_status"
    BACKUP_RESTORE_STATUS = "backup_restore_status"
    ARGOCD_STATUS = "argocd_status"
    SUBJECT_ACCESS_REVIEW = "subject_access_review"
    LOGS_EVENTS = "logs_events"
    SECRET_BEARING_RESOURCES = "secret_bearing_resources"  # nosec B105
    ARBITRARY_SHELL = "arbitrary_shell"
    MUTATION_CAPABLE = "mutation_capable"
    AGENT_INVENTED = "agent_invented"


class ReadOnlyQueryFamilyStatus(str, Enum):
    """Policy status for a query family in the read-only discovery guardrails."""

    ALLOWED_AFTER_GATES = "allowed_after_gates"
    CONDITIONAL = "conditional"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    FORBIDDEN = "forbidden"


class ReadOnlyVerbClass(str, Enum):
    """Classification of a query verb/operation."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class ReadOnlyScenarioEligibility(str, Enum):
    """Eligibility class for a catalog scenario in read-only discovery."""

    INITIALLY_ALLOWED = "initially_allowed"
    SUPPORTING_NON_LIVE_ONLY = "supporting_non_live_only"
    DEFERRED = "deferred"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ReadOnlyGateRequirement:
    """A single live gate requirement for read-only discovery."""

    gate_id: LiveGateId
    required_before_read_only_discovery: bool
    purpose: str


# --- Query family policy -------------------------------------------------------------------------

_QUERY_FAMILY_STATUS: dict[ReadOnlyQueryFamily, ReadOnlyQueryFamilyStatus] = {
    ReadOnlyQueryFamily.CLUSTER_IDENTITY: ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES,
    ReadOnlyQueryFamily.NAMESPACE_UID: ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES,
    ReadOnlyQueryFamily.CLUSTER_VERSION: ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES,
    ReadOnlyQueryFamily.ACM_MCE_MCH_STATUS: ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES,
    ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS: ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES,
    ReadOnlyQueryFamily.BACKUP_RESTORE_STATUS: ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES,
    # Conditional families are allowed only when the scenario requires them and no mutation is
    # involved, or only if separately designed as non-mutating. Phase 8E has no such proof field, so
    # conditional families remain blocked by query-plan validation.
    ReadOnlyQueryFamily.ARGOCD_STATUS: ReadOnlyQueryFamilyStatus.CONDITIONAL,
    ReadOnlyQueryFamily.SUBJECT_ACCESS_REVIEW: ReadOnlyQueryFamilyStatus.CONDITIONAL,
    # Logs/events are deferred unless strong redaction is proven by a later audited design.
    ReadOnlyQueryFamily.LOGS_EVENTS: ReadOnlyQueryFamilyStatus.DEFERRED,
    # Secret-bearing resources are blocked; shell/mutation/agent-invented families are forbidden.
    ReadOnlyQueryFamily.SECRET_BEARING_RESOURCES: ReadOnlyQueryFamilyStatus.BLOCKED,
    ReadOnlyQueryFamily.ARBITRARY_SHELL: ReadOnlyQueryFamilyStatus.FORBIDDEN,
    ReadOnlyQueryFamily.MUTATION_CAPABLE: ReadOnlyQueryFamilyStatus.FORBIDDEN,
    ReadOnlyQueryFamily.AGENT_INVENTED: ReadOnlyQueryFamilyStatus.FORBIDDEN,
}

# Family statuses that allow a read-only discovery query plan to proceed past the family check.
_ALLOWED_FAMILY_STATUSES = frozenset({ReadOnlyQueryFamilyStatus.ALLOWED_AFTER_GATES})


def read_only_query_family_status(query_family: ReadOnlyQueryFamily | str) -> ReadOnlyQueryFamilyStatus | None:
    """Return the policy status of a query family, or None for an unknown family (fail closed)."""
    family = _coerce_query_family(query_family)
    if family is None:
        return None
    return _QUERY_FAMILY_STATUS.get(family)


# --- Verb policy ---------------------------------------------------------------------------------

_READ_ONLY_VERBS = frozenset(
    {
        "get",
        "list",
        "describe",
    }
)
_MUTATING_VERBS = frozenset(
    {
        "create",
        "update",
        "patch",
        "delete",
        "apply",
        "scale",
        "rollout",
        "annotate",
        "label",
        "pause",
        "resume",
        "sync",
        "refresh",
        "restore",
        "decommission",
    }
)
_UNSAFE_VERBS = frozenset(
    {
        "exec",
        "port-forward",
        "portforward",
        "cp",
        "attach",
        "ssh",
        "bash",
        "sh",
    }
)


def classify_read_only_verb(verb: str) -> ReadOnlyVerbClass:
    """Classify a verb/operation. Unknown verbs fail closed as UNKNOWN. Never executes anything."""
    normalized = str(verb).strip().lower()
    if normalized in _READ_ONLY_VERBS:
        return ReadOnlyVerbClass.READ_ONLY
    if normalized in _MUTATING_VERBS:
        return ReadOnlyVerbClass.MUTATING
    if normalized in _UNSAFE_VERBS:
        return ReadOnlyVerbClass.UNSAFE
    return ReadOnlyVerbClass.UNKNOWN


# --- Gate policy ---------------------------------------------------------------------------------

_READ_ONLY_DISCOVERY_GATE_IDS: tuple[LiveGateId, ...] = (
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
)

_GATE_PURPOSES: dict[LiveGateId, str] = {
    LiveGateId.L0: "explicit live mode selected",
    LiveGateId.L1: "clean working tree and expected branch/commit verified",
    LiveGateId.L2: "external live lab config provided from outside Git",
    LiveGateId.L3: "runtime-only kubeconfig and credential references validated",
    LiveGateId.L4: "physical hub identity proof gate initialized",
    LiveGateId.L5: "logical role discovery gate initialized",
    LiveGateId.L6: "managed cluster set expectation available",
    LiveGateId.L7: "RBAC/read prerequisites available",
    LiveGateId.L8: "scenario allowlist permits read-only discovery/preflight",
    LiveGateId.L9: "materialized read-only invocation reviewed",
    LiveGateId.L10: "final confirmation before mutation (not required for read-only discovery)",
}


def required_read_only_discovery_gate_ids() -> tuple[LiveGateId, ...]:
    """Return the gates (L0-L9) required before any future read-only discovery query.

    L10 is intentionally excluded: it is final confirmation before mutation and is not required for
    read-only discovery.
    """
    return _READ_ONLY_DISCOVERY_GATE_IDS


def required_read_only_discovery_gate_requirements() -> tuple[ReadOnlyGateRequirement, ...]:
    """Return the full L0-L10 requirement model, marking L10 as not required for read-only discovery."""
    requirements = [
        ReadOnlyGateRequirement(gate_id, True, _GATE_PURPOSES[gate_id]) for gate_id in _READ_ONLY_DISCOVERY_GATE_IDS
    ]
    requirements.append(ReadOnlyGateRequirement(LiveGateId.L10, False, _GATE_PURPOSES[LiveGateId.L10]))
    return tuple(requirements)


def validate_read_only_discovery_gates(gate_ids: Sequence[LiveGateId | str]) -> ReadOnlyDiscoveryGuardResult:
    """Validate that all read-only discovery gates (L0-L9) are present. Fails closed if any is missing."""
    provided = {_gate_value(item) for item in gate_ids}
    required = required_read_only_discovery_gate_ids()
    allowed = {gate.value for gate in required} | {LiveGateId.L10.value}
    missing = [gate.value for gate in required if gate.value not in provided]
    unknown = sorted(provided - allowed)
    reasons: list[str] = []
    blocking_fields: list[str] = []
    if missing:
        _block(
            reasons,
            blocking_fields,
            "required_gate_ids",
            f"missing required read-only discovery gates: {', '.join(missing)}",
        )
    if unknown:
        _block(
            reasons,
            blocking_fields,
            "gate_ids",
            "unknown read-only discovery gates present",
        )
    decision = ReadOnlyDiscoveryGuardDecision.BLOCKED if reasons else ReadOnlyDiscoveryGuardDecision.PASS
    summary = _sanitized_summary(
        {
            "decision": decision.value,
            "reasons": list(reasons),
            "required_gate_ids": [gate.value for gate in required],
            "missing_gates": missing,
            "unknown_gates": unknown,
            "gate_summary": list(summarize_read_only_discovery_gates(gate_ids)),
            "live_certification_evidence": False,
            "live_execution_enabled": False,
            "mutation_enabled": False,
        }
    )
    return ReadOnlyDiscoveryGuardResult(
        decision=decision,
        reasons=tuple(dict.fromkeys(reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        required_gates=tuple(gate.value for gate in required),
        missing_gates=tuple(missing),
        artifact_safe_summary=summary,
        live_certification_evidence=False,
    )


def summarize_read_only_discovery_gates(
    gate_ids: Sequence[LiveGateId | str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Summarize the L0-L10 gate model for artifacts. Marks L10 as not required for read-only discovery."""
    provided = {_gate_value(item) for item in gate_ids} if gate_ids is not None else None
    entries: list[dict[str, Any]] = []
    for requirement in required_read_only_discovery_gate_requirements():
        entry: dict[str, Any] = {
            "gate_id": requirement.gate_id.value,
            "required_for_read_only_discovery": requirement.required_before_read_only_discovery,
            "status": "design_only",
            "phase8e_semantics": "model_only_not_executable",
        }
        if provided is not None:
            entry["present_in_plan"] = requirement.gate_id.value in provided
        entries.append(entry)
    return tuple(entries)


# --- Scenario eligibility policy -----------------------------------------------------------------

# Every catalog scenario must have an explicit, conservative eligibility classification. Unknown
# scenario IDs are fail-closed at validation time.
_SCENARIO_ELIGIBILITY: dict[str, ReadOnlyScenarioEligibility] = {
    "static-gates": ReadOnlyScenarioEligibility.SUPPORTING_NON_LIVE_ONLY,
    "lab-readiness": ReadOnlyScenarioEligibility.INITIALLY_ALLOWED,
    "baseline-check": ReadOnlyScenarioEligibility.INITIALLY_ALLOWED,
    "preflight": ReadOnlyScenarioEligibility.INITIALLY_ALLOWED,
    "python-passive-switchover": ReadOnlyScenarioEligibility.BLOCKED,
    "ansible-passive-switchover": ReadOnlyScenarioEligibility.BLOCKED,
    "python-restore-only": ReadOnlyScenarioEligibility.BLOCKED,
    "ansible-restore-only": ReadOnlyScenarioEligibility.BLOCKED,
    "argocd-managed-switchover": ReadOnlyScenarioEligibility.BLOCKED,
    "runtime-parity": ReadOnlyScenarioEligibility.SUPPORTING_NON_LIVE_ONLY,
    "final-baseline-check": ReadOnlyScenarioEligibility.INITIALLY_ALLOWED,
    "bash-discovery": ReadOnlyScenarioEligibility.DEFERRED,
    "bash-postflight": ReadOnlyScenarioEligibility.DEFERRED,
    "full-restore": ReadOnlyScenarioEligibility.BLOCKED,
    "checkpoint-resume": ReadOnlyScenarioEligibility.BLOCKED,
    "decommission": ReadOnlyScenarioEligibility.BLOCKED,
    "rbac-bootstrap": ReadOnlyScenarioEligibility.BLOCKED,
    "rbac-bootstrap-live": ReadOnlyScenarioEligibility.DEFERRED,
    "failure-injection": ReadOnlyScenarioEligibility.BLOCKED,
    "soak": ReadOnlyScenarioEligibility.BLOCKED,
}


def read_only_scenario_eligibility(scenario_id: str) -> ReadOnlyScenarioEligibility | None:
    """Return the eligibility class for a scenario, or None for an unknown scenario (fail closed)."""
    return _SCENARIO_ELIGIBILITY.get(scenario_id)


def read_only_scenario_eligibility_map() -> dict[str, ReadOnlyScenarioEligibility]:
    """Return a copy of the full scenario eligibility classification."""
    return dict(_SCENARIO_ELIGIBILITY)


def unclassified_catalog_scenarios() -> tuple[str, ...]:
    """Return any catalog scenario IDs missing an explicit read-only eligibility classification."""
    return tuple(sorted(set(SCENARIOS_BY_ID) - set(_SCENARIO_ELIGIBILITY)))


# --- Query plan model and validation -------------------------------------------------------------


@dataclass(frozen=True)
class ReadOnlyQueryPlan:
    """A future read-only discovery query plan. Phase 8E validates but never executes it."""

    scenario_id: str
    query_family: ReadOnlyQueryFamily | str
    verb: str
    required_gate_ids: tuple[LiveGateId | str, ...] = ()
    artifact_fields: tuple[str, ...] = ()
    emits_logs: bool = False
    may_expose_secrets: bool = False
    mutates_state: bool = False
    uses_arbitrary_command: bool = False
    agent_invented: bool = False
    redaction_required: bool = True
    l10_present: bool = False
    live_certification_evidence: bool = False


@dataclass(frozen=True)
class ReadOnlyDiscoveryGuardResult:
    """Structured, deterministic guardrail decision. Errors are not raised for policy failures."""

    decision: ReadOnlyDiscoveryGuardDecision
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()
    scenario_eligibility: ReadOnlyScenarioEligibility | None = None
    query_family_status: ReadOnlyQueryFamilyStatus | None = None
    verb_class: ReadOnlyVerbClass | None = None
    required_gates: tuple[str, ...] = ()
    missing_gates: tuple[str, ...] = ()
    artifact_safe_summary: dict[str, Any] = field(default_factory=dict)
    live_certification_evidence: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "blocking_fields": list(self.blocking_fields),
            "scenario_eligibility": self.scenario_eligibility.value if self.scenario_eligibility else None,
            "query_family_status": self.query_family_status.value if self.query_family_status else None,
            "verb_class": self.verb_class.value if self.verb_class else None,
            "required_gates": list(self.required_gates),
            "missing_gates": list(self.missing_gates),
            "artifact_safe_summary": self.artifact_safe_summary,
            "live_certification_evidence": self.live_certification_evidence,
        }


def _validate_query_plan_scenario(
    plan: ReadOnlyQueryPlan,
    reasons: list[str],
    blocking_fields: list[str],
) -> ReadOnlyScenarioEligibility | None:
    eligibility = read_only_scenario_eligibility(plan.scenario_id)
    if eligibility is None:
        _block(reasons, blocking_fields, "scenario_id", "unknown scenario id is not eligible for read-only discovery")
    elif eligibility is not ReadOnlyScenarioEligibility.INITIALLY_ALLOWED:
        _block(
            reasons,
            blocking_fields,
            "scenario_id",
            f"scenario is not eligible for read-only discovery: {eligibility.value}",
        )
    return eligibility


def _validate_query_plan_family(
    plan: ReadOnlyQueryPlan,
    reasons: list[str],
    blocking_fields: list[str],
) -> tuple[ReadOnlyQueryFamily | None, ReadOnlyQueryFamilyStatus | None]:
    family = _coerce_query_family(plan.query_family)
    family_status = _QUERY_FAMILY_STATUS.get(family) if family is not None else None
    if family is None:
        _block(reasons, blocking_fields, "query_family", "unknown query family is not allowed in read-only discovery")
    elif family_status not in _ALLOWED_FAMILY_STATUSES:
        reason = (
            f"query family is not allowed in read-only discovery: {family_status.value if family_status else 'unknown'}"
        )
        if family_status is ReadOnlyQueryFamilyStatus.CONDITIONAL:
            reason = (
                "conditional query family requires a separately audited non-mutating design "
                "and scenario requirement before read-only discovery"
            )
        _block(
            reasons,
            blocking_fields,
            "query_family",
            reason,
        )
    return family, family_status


def _validate_query_plan_verb(
    plan: ReadOnlyQueryPlan,
    reasons: list[str],
    blocking_fields: list[str],
) -> ReadOnlyVerbClass:
    verb_class = classify_read_only_verb(plan.verb)
    if verb_class is not ReadOnlyVerbClass.READ_ONLY:
        _block(reasons, blocking_fields, "verb", f"verb is not read-only: {verb_class.value}")
    return verb_class


def _read_only_gate_gaps(gate_ids: Sequence[LiveGateId | str]) -> tuple[list[str], list[str]]:
    required = required_read_only_discovery_gate_ids()
    provided = {_gate_value(item) for item in gate_ids}
    allowed_gates = {gate.value for gate in required} | {LiveGateId.L10.value}
    missing = [gate.value for gate in required if gate.value not in provided]
    unknown_gates = sorted(provided - allowed_gates)
    return missing, unknown_gates


def _validate_query_plan_gates(
    plan: ReadOnlyQueryPlan,
    reasons: list[str],
    blocking_fields: list[str],
) -> tuple[list[str], list[str]]:
    missing, unknown_gates = _read_only_gate_gaps(plan.required_gate_ids)
    if missing:
        _block(
            reasons,
            blocking_fields,
            "required_gate_ids",
            f"missing required read-only discovery gates: {', '.join(missing)}",
        )
    if unknown_gates:
        _block(
            reasons,
            blocking_fields,
            "required_gate_ids",
            "unknown read-only discovery gates present",
        )
    return missing, unknown_gates


def _validate_query_plan_artifact_fields(
    plan: ReadOnlyQueryPlan,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    forbidden_artifact_fields = _forbidden_artifact_field_names(plan.artifact_fields)
    for field_name in forbidden_artifact_fields:
        _block(
            reasons,
            blocking_fields,
            field_name,
            f"runtime-only/forbidden artifact field must not appear in query plan: {field_name}",
        )


def _validate_query_plan_flags(
    plan: ReadOnlyQueryPlan,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    if plan.mutates_state:
        _block(reasons, blocking_fields, "mutates_state", "read-only discovery must not mutate state")
    if plan.uses_arbitrary_command:
        _block(
            reasons, blocking_fields, "uses_arbitrary_command", "read-only discovery must not use arbitrary commands"
        )
    if plan.agent_invented:
        _block(
            reasons, blocking_fields, "agent_invented", "agent-invented queries are forbidden in read-only discovery"
        )
    if plan.may_expose_secrets:
        _block(reasons, blocking_fields, "may_expose_secrets", "queries that may expose secrets are forbidden")
    if plan.emits_logs:
        _block(
            reasons, blocking_fields, "emits_logs", "log/event emission is deferred until strong redaction is proven"
        )
    if not plan.redaction_required:
        _block(reasons, blocking_fields, "redaction_required", "redaction must be required for read-only discovery")
    if plan.live_certification_evidence:
        _block(
            reasons,
            blocking_fields,
            "live_certification_evidence",
            "read-only discovery must not claim live certification evidence",
        )


def _validate_query_plan_l10_boundary(
    plan: ReadOnlyQueryPlan,
    family: ReadOnlyQueryFamily | None,
    verb_class: ReadOnlyVerbClass,
    reasons: list[str],
    blocking_fields: list[str],
) -> None:
    mutating_intent = (
        plan.mutates_state or verb_class is ReadOnlyVerbClass.MUTATING or family is ReadOnlyQueryFamily.MUTATION_CAPABLE
    )
    if plan.l10_present and mutating_intent:
        _block(
            reasons,
            blocking_fields,
            "l10_present",
            "L10 cannot authorize mutation in Phase 8E read-only discovery",
        )


def validate_read_only_query_plan(plan: ReadOnlyQueryPlan) -> ReadOnlyDiscoveryGuardResult:
    """Validate a future read-only discovery query plan. Fails closed; returns a structured result."""
    reasons: list[str] = []
    blocking_fields: list[str] = []

    eligibility = _validate_query_plan_scenario(plan, reasons, blocking_fields)
    family, family_status = _validate_query_plan_family(plan, reasons, blocking_fields)
    verb_class = _validate_query_plan_verb(plan, reasons, blocking_fields)
    missing, unknown_gates = _validate_query_plan_gates(plan, reasons, blocking_fields)
    _validate_query_plan_artifact_fields(plan, reasons, blocking_fields)
    _validate_query_plan_flags(plan, reasons, blocking_fields)
    _validate_query_plan_l10_boundary(plan, family, verb_class, reasons, blocking_fields)

    required = required_read_only_discovery_gate_ids()
    decision = ReadOnlyDiscoveryGuardDecision.BLOCKED if reasons else ReadOnlyDiscoveryGuardDecision.PASS
    summary = _sanitized_summary(
        {
            "scenario_id": plan.scenario_id,
            "scenario_eligibility": eligibility.value if eligibility else "unknown",
            "query_family": family.value if family else "unknown",
            "query_family_status": family_status.value if family_status else "unknown",
            "verb_class": verb_class.value,
            "decision": decision.value,
            "reasons": list(reasons),
            "blocking_fields": list(blocking_fields),
            "required_gate_ids": [gate.value for gate in required],
            "missing_gates": missing,
            "unknown_gates": unknown_gates,
            "redaction_status": "redacted",
            "redaction_required": plan.redaction_required,
            "emits_logs": plan.emits_logs,
            "mutates_state": plan.mutates_state,
            "may_expose_sensitive_data": plan.may_expose_secrets,
            "agent_invented": plan.agent_invented,
            "uses_arbitrary_command": plan.uses_arbitrary_command,
            "artifact_field_count": len(plan.artifact_fields),
            "live_certification_evidence": False,
            "live_execution_enabled": False,
            "mutation_enabled": False,
        }
    )
    return ReadOnlyDiscoveryGuardResult(
        decision=decision,
        reasons=tuple(dict.fromkeys(reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        scenario_eligibility=eligibility,
        query_family_status=family_status,
        verb_class=verb_class,
        required_gates=tuple(gate.value for gate in required),
        missing_gates=tuple(missing),
        artifact_safe_summary=summary,
        live_certification_evidence=False,
    )


def summarize_read_only_query_plan(plan: ReadOnlyQueryPlan) -> dict[str, Any]:
    """Return an artifact-safe summary for a query plan, excluding runtime-only/unsafe values."""
    return validate_read_only_query_plan(plan).artifact_safe_summary


def build_example_read_only_query_plan() -> ReadOnlyQueryPlan:
    """Return a sanitized example query plan that passes the guardrails (no live values)."""
    return ReadOnlyQueryPlan(
        scenario_id="preflight",
        query_family=ReadOnlyQueryFamily.CLUSTER_IDENTITY,
        verb="get",
        required_gate_ids=tuple(required_read_only_discovery_gate_ids()),
        artifact_fields=("physical_identity_evidence", "gate_status", "decision"),
    )


# --- Discovery artifact contract guardrails ------------------------------------------------------

_REQUIRED_ARTIFACT_FIELDS: tuple[str, ...] = (
    "artifact_version",
    "controller_phase",
    "discovery_mode",
    "live_execution_enabled",
    "mutation_enabled",
    "live_certification_evidence",
    "physical_identity_evidence",
    "logical_role_evidence",
    "managed_cluster_set_evidence",
    "read_prerequisite_evidence",
    "scenario_id",
    "gate_status",
    "decision",
    "safe_to_continue",
    "retry_allowed",
    "manual_recovery_required",
    "first_blocking_reason",
    "redaction_status",
    "command_query_summary",
    "runtime_inputs_redacted",
)

# Field-name substrings that must never appear as artifact keys: these denote runtime-only or
# otherwise forbidden inputs that must not be published in a discovery artifact.
_FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS: tuple[str, ...] = (
    "kubeconfig",
    "context_ref",
    "runtime_ref",
    "api_url",
    "api_server",
    "token",
    "password",
    "secret",
    "credential",
    "raw_command",
    "command_string",
    "argv",
)

_VALID_REDACTION_STATUSES = frozenset({"safe", "pass", "redacted"})


@dataclass(frozen=True)
class ReadOnlyDiscoveryArtifactContract:
    """Field contract for a future read-only discovery artifact. Fail-closed, non-live defaults."""

    artifact_version: str = "design.phase8e"
    controller_phase: str = "phase-8e-read-only-discovery-guardrails"
    discovery_mode: str = "read_only"
    live_execution_enabled: bool = False
    mutation_enabled: bool = False
    live_certification_evidence: bool = False
    physical_identity_evidence: str = "redacted-physical-identity-evidence-summary"
    logical_role_evidence: str = "redacted-logical-role-evidence-summary"
    managed_cluster_set_evidence: str = "expected-count-and-hash-summary"
    read_prerequisite_evidence: str = "redacted-read-prerequisite-summary"
    scenario_id: str = "preflight"
    gate_status: str = "design_only"
    decision: str = ReadOnlyDiscoveryGuardDecision.BLOCKED.value
    safe_to_continue: bool = False
    retry_allowed: bool = False
    manual_recovery_required: bool = False
    first_blocking_reason: str | None = None
    redaction_status: str = "redacted"
    command_query_summary: tuple[str, ...] = ()
    runtime_inputs_redacted: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "artifact_version": self.artifact_version,
            "controller_phase": self.controller_phase,
            "discovery_mode": self.discovery_mode,
            "live_execution_enabled": self.live_execution_enabled,
            "mutation_enabled": self.mutation_enabled,
            "live_certification_evidence": self.live_certification_evidence,
            "physical_identity_evidence": self.physical_identity_evidence,
            "logical_role_evidence": self.logical_role_evidence,
            "managed_cluster_set_evidence": self.managed_cluster_set_evidence,
            "read_prerequisite_evidence": self.read_prerequisite_evidence,
            "scenario_id": self.scenario_id,
            "gate_status": self.gate_status,
            "decision": self.decision,
            "safe_to_continue": self.safe_to_continue,
            "retry_allowed": self.retry_allowed,
            "manual_recovery_required": self.manual_recovery_required,
            "first_blocking_reason": self.first_blocking_reason,
            "redaction_status": self.redaction_status,
            "command_query_summary": list(self.command_query_summary),
            "runtime_inputs_redacted": self.runtime_inputs_redacted,
        }


def build_example_read_only_discovery_artifact() -> ReadOnlyDiscoveryArtifactContract:
    """Return a sanitized, valid example discovery artifact contract (no live values)."""
    return ReadOnlyDiscoveryArtifactContract(
        decision=ReadOnlyDiscoveryGuardDecision.PASS.value,
        command_query_summary=("cluster_identity:get", "managed_cluster_status:list"),
    )


def _validate_artifact_safety_flags(
    payload: Mapping[str, Any],
    structural_reasons: list[str],
    blocking_fields: list[str],
) -> None:
    """Fail closed on the fixed artifact safety flags using exact boolean comparisons.

    None, strings, and other non-boolean payloads must not slip through a truthy/falsy check.
    ``live_execution_enabled`` MAY be ``True`` (it models future live contact, separately gated by
    L0-L9 and never implying certification per the Phase 8D design), but it must be an explicit
    boolean so a ``None``/string payload cannot bypass the type contract.
    """
    if "mutation_enabled" in payload and payload.get("mutation_enabled") is not False:
        _block(structural_reasons, blocking_fields, "mutation_enabled", "mutation_enabled must be false")
    if "live_certification_evidence" in payload and payload.get("live_certification_evidence") is not False:
        _block(
            structural_reasons,
            blocking_fields,
            "live_certification_evidence",
            "live_certification_evidence must be false for read-only discovery",
        )
    if "live_execution_enabled" in payload and not isinstance(payload.get("live_execution_enabled"), bool):
        _block(
            structural_reasons,
            blocking_fields,
            "live_execution_enabled",
            "live_execution_enabled must be an explicit boolean",
        )
    if "runtime_inputs_redacted" in payload and payload.get("runtime_inputs_redacted") is not True:
        _block(structural_reasons, blocking_fields, "runtime_inputs_redacted", "runtime_inputs_redacted must be true")


def validate_read_only_discovery_artifact_contract(
    artifact: ReadOnlyDiscoveryArtifactContract | Mapping[str, Any],
) -> ReadOnlyDiscoveryGuardResult:
    """Validate a future discovery artifact contract. Structural defects BLOCK; unsafe values are NO_GO."""
    if isinstance(artifact, ReadOnlyDiscoveryArtifactContract):
        payload: dict[str, Any] | None = artifact.to_payload()
    elif isinstance(artifact, Mapping):
        payload = dict(artifact)
    else:
        payload = None

    structural_reasons: list[str] = []
    redaction_reasons: list[str] = []
    blocking_fields: list[str] = []

    if payload is None:
        return ReadOnlyDiscoveryGuardResult(
            decision=ReadOnlyDiscoveryGuardDecision.BLOCKED,
            reasons=("artifact must be a ReadOnlyDiscoveryArtifactContract or mapping",),
            blocking_fields=("artifact",),
            artifact_safe_summary=_sanitized_summary({"decision": "BLOCKED"}),
            live_certification_evidence=False,
        )

    for field_name in _REQUIRED_ARTIFACT_FIELDS:
        if field_name not in payload:
            _block(structural_reasons, blocking_fields, field_name, f"required artifact field is missing: {field_name}")

    if payload.get("discovery_mode") != "read_only":
        _block(structural_reasons, blocking_fields, "discovery_mode", "discovery_mode must be read_only")
    _validate_artifact_safety_flags(payload, structural_reasons, blocking_fields)
    redaction_status = payload.get("redaction_status")
    if redaction_status is not None and redaction_status not in _VALID_REDACTION_STATUSES:
        _block(
            structural_reasons,
            blocking_fields,
            "redaction_status",
            "redaction_status must be one of safe/pass/redacted",
        )

    forbidden_keys = _forbidden_artifact_keys(payload)
    for key in forbidden_keys:
        _block(
            structural_reasons, blocking_fields, key, f"runtime-only/forbidden field must not appear in artifact: {key}"
        )

    if _payload_has_sensitive_value(payload):
        _block(
            redaction_reasons,
            blocking_fields,
            "redaction_status",
            "artifact contains an unredacted sensitive value (kubeconfig/API URL/credential/.release)",
        )

    if redaction_reasons:
        decision = ReadOnlyDiscoveryGuardDecision.NO_GO
    elif structural_reasons:
        decision = ReadOnlyDiscoveryGuardDecision.BLOCKED
    else:
        decision = ReadOnlyDiscoveryGuardDecision.PASS

    reasons = tuple(dict.fromkeys(structural_reasons + redaction_reasons))
    summary = _sanitized_summary(
        {
            "decision": decision.value,
            "reasons": list(reasons),
            "blocking_fields": list(dict.fromkeys(blocking_fields)),
            "scenario_id": payload.get("scenario_id"),
            "discovery_mode": payload.get("discovery_mode"),
            "redaction_status": "redacted" if redaction_reasons else (redaction_status or "redacted"),
            "live_certification_evidence": False,
            "live_execution_enabled": payload.get("live_execution_enabled") is True,
            "mutation_enabled": False,
            "runtime_inputs_redacted": True,
        }
    )
    return ReadOnlyDiscoveryGuardResult(
        decision=decision,
        reasons=reasons,
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        artifact_safe_summary=summary,
        live_certification_evidence=False,
    )


# --- Internal helpers ----------------------------------------------------------------------------


def _coerce_query_family(value: ReadOnlyQueryFamily | str) -> ReadOnlyQueryFamily | None:
    if isinstance(value, ReadOnlyQueryFamily):
        return value
    try:
        return ReadOnlyQueryFamily(str(value))
    except ValueError:
        return None


def _gate_value(value: LiveGateId | str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _block(reasons: list[str], blocking_fields: list[str], field_path: str, reason: str) -> None:
    reasons.append(reason)
    blocking_fields.append(field_path)


def _forbidden_artifact_keys(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    _collect_forbidden_keys(value, found)
    return tuple(dict.fromkeys(found))


def _forbidden_artifact_field_names(field_names: Sequence[str]) -> tuple[str, ...]:
    found: list[str] = []
    for field_name in field_names:
        lowered = str(field_name).lower()
        if any(substring in lowered for substring in _FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS):
            found.append(str(field_name))
    return tuple(dict.fromkeys(found))


def _collect_forbidden_keys(value: Any, found: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(substring in lowered for substring in _FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS):
                found.append(str(key))
            _collect_forbidden_keys(child, found)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _collect_forbidden_keys(child, found)


def _payload_has_sensitive_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_payload_has_sensitive_value(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_payload_has_sensitive_value(child) for child in value)
    if isinstance(value, str):
        return _value_is_sensitive(value)
    return False


def _value_is_sensitive(value: str) -> bool:
    if _URL_PATTERN.search(value):
        return True
    if _KUBECONFIG_PATH_PATTERN.search(value):
        return True
    if _CREDENTIAL_VALUE_PATTERN.search(value):
        return True
    if _PRIVATE_ID_PATTERN.search(value):
        return True
    if _ARBITRARY_COMMAND_VALUE_PATTERN.search(value):
        return True
    if _RELEASE_PATH_MARKER in value.lower():
        return True
    return False


def _sanitized_summary(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_artifact_payload(payload)
    validate_artifact_payload_redacted(sanitized)
    return sanitized


__all__ = [
    "ReadOnlyDiscoveryArtifactContract",
    "ReadOnlyDiscoveryGuardDecision",
    "ReadOnlyDiscoveryGuardResult",
    "ReadOnlyGateRequirement",
    "ReadOnlyQueryFamily",
    "ReadOnlyQueryFamilyStatus",
    "ReadOnlyQueryPlan",
    "ReadOnlyScenarioEligibility",
    "ReadOnlyVerbClass",
    "build_example_read_only_discovery_artifact",
    "build_example_read_only_query_plan",
    "classify_read_only_verb",
    "read_only_query_family_status",
    "read_only_scenario_eligibility",
    "read_only_scenario_eligibility_map",
    "required_read_only_discovery_gate_ids",
    "required_read_only_discovery_gate_requirements",
    "summarize_read_only_discovery_gates",
    "summarize_read_only_query_plan",
    "unclassified_catalog_scenarios",
    "validate_read_only_discovery_artifact_contract",
    "validate_read_only_discovery_gates",
    "validate_read_only_query_plan",
]
