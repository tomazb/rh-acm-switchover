"""Pure, deterministic fake read-only transport contracts for the lab role controller.

Phase 8H turns the Phase 8G read-only backend interface skeleton into deterministic *fake*
transport contracts. It lets future backend code exercise transport result handling, evidence
shaping, timeout/error categories, and artifact summaries **without any live contact**.

Strict non-live boundary (intentionally enforced by tests):

- This module does not implement a real transport.
- It does not contact clusters.
- It does not load live config files.
- It does not parse real YAML/JSON config files.
- It does not read kubeconfig files.
- It does not read ``os.environ``.
- It does not run subprocesses, ``oc``, ``kubectl``, or ``ansible-playbook``.
- It does not call release adapters and does not write artifacts or ``.release`` output.
- It does not implement live discovery.

It only models structured read-only transport query requests/responses, validates fake query
requests against the Phase 8E guardrails before any lookup, returns deterministic fake responses
from in-memory fixtures, classifies timeout/failure categories, and produces artifact-safe,
redacted summaries. Every decision is deterministic and fails closed. A fake transport never sets
``live_contact_attempted``, ``live_contact_succeeded``, ``mutation_attempted``, or
``live_certification_evidence`` to true.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tests.release.lab_controller.artifacts import (
    sanitize_artifact_payload,
    sanitize_artifact_text,
    validate_artifact_payload_redacted,
)
from tests.release.lab_controller.live_config import LiveGateId
from tests.release.lab_controller.read_only_backend import ReadOnlyDiscoveryRequest, ReadOnlyQueryPlanBundle
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyDiscoveryGuardDecision,
    ReadOnlyDiscoveryGuardResult,
    ReadOnlyQueryFamily,
    ReadOnlyQueryPlan,
    classify_read_only_verb,
    required_read_only_discovery_gate_ids,
    validate_read_only_query_plan,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

# Redaction patterns mirror the Phase 8E/8G value-unsafe patterns. They detect unsafe artifact
# *values* (never mapping keys); the structural/shape decision stays separate from redaction.
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

# Field-name substrings that denote runtime-only or otherwise forbidden inputs. They must never
# appear as fixture-payload or summary keys.
_FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS: tuple[str, ...] = (
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


class ReadOnlyTransportDecision(str, Enum):
    """Controller decision vocabulary for a fake read-only transport interaction."""

    PASS = "PASS"  # nosec B105
    BLOCKED = "BLOCKED"
    NO_GO = "NO_GO"
    INFRA_RETRYABLE = "INFRA_RETRYABLE"


class ReadOnlyTransportKind(str, Enum):
    """Transport implementations represented by these contracts."""

    FAKE = "fake"
    LIVE_UNSUPPORTED = "live_unsupported"


class ReadOnlyTransportStatus(str, Enum):
    """Status of a fake read-only transport response."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNSAFE_PAYLOAD = "unsafe_payload"  # nosec B105


class ReadOnlyTransportErrorCategory(str, Enum):
    """Deterministic error categories for a fake read-only transport response."""

    NONE = "none"
    INVALID_QUERY = "invalid_query"
    MISSING_FIXTURE = "missing_fixture"
    DUPLICATE_FIXTURE = "duplicate_fixture"
    TIMEOUT = "timeout"
    TRANSPORT_FAILURE = "transport_failure"
    POLICY_BLOCKED = "policy_blocked"
    UNSAFE_PAYLOAD = "unsafe_payload"  # nosec B105


@dataclass(frozen=True)
class ReadOnlyTransportQuery:
    """A structured read-only query a future transport would receive. Never executed live."""

    query_id: str
    scenario_id: str
    query_family: ReadOnlyQueryFamily | str
    verb: str
    hub_label: str
    resource_family: str
    guardrail_result: ReadOnlyDiscoveryGuardResult
    required_gate_ids: tuple[LiveGateId | str, ...] = ()
    artifact_fields: tuple[str, ...] = ()
    redaction_required: bool = True
    live_certification_evidence: bool = False
    mutation_enabled: bool = False
    transport_kind: ReadOnlyTransportKind = ReadOnlyTransportKind.FAKE

    def to_query_plan(self) -> ReadOnlyQueryPlan:
        """Project this transport query onto the Phase 8E query-plan model for guardrail validation."""
        return ReadOnlyQueryPlan(
            scenario_id=self.scenario_id,
            query_family=self.query_family,
            verb=self.verb,
            required_gate_ids=self.required_gate_ids,
            artifact_fields=self.artifact_fields,
            redaction_required=self.redaction_required,
            live_certification_evidence=self.live_certification_evidence,
        )


@dataclass(frozen=True)
class ReadOnlyTransportResponse:
    """A deterministic fake transport response. Never represents live contact."""

    query_id: str | None
    scenario_id: str | None
    status: ReadOnlyTransportStatus
    decision: ReadOnlyTransportDecision
    response_summary: str
    error_category: ReadOnlyTransportErrorCategory = ReadOnlyTransportErrorCategory.NONE
    timeout: bool = False
    retryable: bool = False
    live_contact_attempted: bool = False
    live_contact_succeeded: bool = False
    mutation_attempted: bool = False
    live_certification_evidence: bool = False
    redaction_status: str = "redacted"
    artifact_safe_payload: Mapping[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    first_blocking_reason: str | None = None
    transport_kind: ReadOnlyTransportKind = ReadOnlyTransportKind.FAKE
    no_live_contact: bool = True


@dataclass(frozen=True)
class ReadOnlyTransportQueryValidation:
    """Structured, deterministic validation decision for a transport query. Never raises for policy."""

    decision: ReadOnlyTransportDecision
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()
    guardrail_result: ReadOnlyDiscoveryGuardResult | None = None
    artifact_safe_summary: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.decision is ReadOnlyTransportDecision.PASS


@dataclass(frozen=True)
class FakeTransportFixture:
    """A deterministic fake response definition keyed by ``query_id``.

    The stored ``payload`` must be artifact-safe; unsafe payloads are rejected at construction. To
    exercise the unsafe-payload NO_GO path without storing raw sensitive data, declare
    ``status=ReadOnlyTransportStatus.UNSAFE_PAYLOAD`` instead of embedding an unsafe value.
    """

    query_id: str
    status: ReadOnlyTransportStatus
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_category: ReadOnlyTransportErrorCategory = ReadOnlyTransportErrorCategory.NONE
    timeout: bool = False
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.query_id:
            raise ValueError("fake transport fixture requires a query_id")
        if not isinstance(self.status, ReadOnlyTransportStatus):
            raise ValueError("fake transport fixture status must be a ReadOnlyTransportStatus")
        _assert_artifact_safe_payload(self.payload)


@dataclass(frozen=True)
class ReadOnlyTransportArtifactSummary:
    """Typed wrapper for an artifact-safe fake transport summary payload."""

    transport_kind: ReadOnlyTransportKind
    decision: ReadOnlyTransportDecision
    payload: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return _safe_summary(dict(self.payload))


class FakeReadOnlyTransport:
    """A deterministic, in-memory fake read-only transport.

    It validates every query through the Phase 8E guardrails before any fixture lookup, returns
    deterministic responses from an in-memory fixture map, records the queries it received, and
    never contacts a cluster, runs a subprocess, reads the environment, or mutates external state.
    """

    transport_kind = ReadOnlyTransportKind.FAKE

    def __init__(self, fixtures: Sequence[FakeTransportFixture] = ()) -> None:
        self._fixtures: dict[str, FakeTransportFixture] = _build_fixture_map(fixtures)
        self._received: list[ReadOnlyTransportQuery] = []

    @property
    def call_count(self) -> int:
        return len(self._received)

    @property
    def received_query_ids(self) -> tuple[str, ...]:
        return tuple(query.query_id for query in self._received)

    @property
    def no_live_contact(self) -> bool:
        return True

    def received_query_summaries(self) -> tuple[dict[str, Any], ...]:
        """Return artifact-safe summaries of every received query (no runtime-only values)."""
        return tuple(_safe_query_call_summary(query) for query in self._received)

    def execute(self, query: ReadOnlyTransportQuery) -> ReadOnlyTransportResponse:
        """Validate, look up, and shape a deterministic fake response. Never contacts a cluster."""
        self._received.append(query)
        validation = validate_transport_query(query)
        if validation.decision is not ReadOnlyTransportDecision.PASS:
            return _blocked_response(query, validation)
        fixture = self._fixtures.get(query.query_id)
        if fixture is None:
            return _missing_fixture_response(query)
        return _response_from_fixture(query, fixture)


# --- Query validation ----------------------------------------------------------------------------


def validate_transport_query(query: ReadOnlyTransportQuery) -> ReadOnlyTransportQueryValidation:
    """Validate a structured transport query against the Phase 8E guardrails. Fails closed."""
    if not isinstance(query, ReadOnlyTransportQuery):
        return ReadOnlyTransportQueryValidation(
            decision=ReadOnlyTransportDecision.BLOCKED,
            reasons=("query must be a ReadOnlyTransportQuery",),
            blocking_fields=("query",),
        )

    reasons: list[str] = []
    blocking_fields: list[str] = []

    if not query.query_id:
        _block(reasons, blocking_fields, "query_id", "query_id is required")
    if query.mutation_enabled is not False:
        _block(reasons, blocking_fields, "mutation_enabled", "mutation_enabled must be exactly false")
    if query.live_certification_evidence is not False:
        _block(
            reasons,
            blocking_fields,
            "live_certification_evidence",
            "live_certification_evidence must be exactly false",
        )
    if query.redaction_required is not True:
        _block(reasons, blocking_fields, "redaction_required", "redaction_required must be exactly true")
    if query.transport_kind is not ReadOnlyTransportKind.FAKE:
        _block(reasons, blocking_fields, "transport_kind", "only fake transport is supported in Phase 8H")

    for field_name, value in (
        ("query_id", query.query_id),
        ("hub_label", query.hub_label),
        ("resource_family", query.resource_family),
    ):
        if value and _value_is_unsafe(str(value)):
            _block(reasons, blocking_fields, field_name, f"{field_name} contains an unsafe artifact-facing value")

    plan_result = validate_read_only_query_plan(query.to_query_plan())
    if plan_result.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
        if plan_result.reasons:
            for reason, plan_field in zip(plan_result.reasons, plan_result.blocking_fields):
                _block(reasons, blocking_fields, plan_field, reason)
        else:
            _block(reasons, blocking_fields, "query_plan", "query failed Phase 8E read-only guardrails")

    if not isinstance(query.guardrail_result, ReadOnlyDiscoveryGuardResult):
        _block(reasons, blocking_fields, "guardrail_result", "guardrail_result must be a ReadOnlyDiscoveryGuardResult")
    elif query.guardrail_result.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
        _block(reasons, blocking_fields, "guardrail_result", "guardrail_result must be PASS")
    elif query.guardrail_result.live_certification_evidence is not False:
        _block(
            reasons,
            blocking_fields,
            "guardrail_result",
            "guardrail_result must not claim live certification evidence",
        )

    decision = ReadOnlyTransportDecision.BLOCKED if reasons else ReadOnlyTransportDecision.PASS
    summary = _safe_summary(
        {
            "transport_kind": ReadOnlyTransportKind.FAKE.value,
            "query_id": query.query_id,
            "scenario_id": query.scenario_id,
            "query_family": _family_value(query.query_family),
            "verb_class": classify_read_only_verb(query.verb).value,
            "decision": decision.value,
            "reasons": list(dict.fromkeys(reasons)),
            "blocking_fields": list(dict.fromkeys(blocking_fields)),
            "redaction_required": query.redaction_required,
            "mutation_enabled": False,
            "live_certification_evidence": False,
            "live_contact_attempted": False,
        }
    )
    return ReadOnlyTransportQueryValidation(
        decision=decision,
        reasons=tuple(dict.fromkeys(reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
        guardrail_result=plan_result,
        artifact_safe_summary=summary,
    )


# --- Response shaping ----------------------------------------------------------------------------


def _blocked_response(
    query: ReadOnlyTransportQuery,
    validation: ReadOnlyTransportQueryValidation,
) -> ReadOnlyTransportResponse:
    reason = validation.reasons[0] if validation.reasons else "transport query failed read-only guardrails"
    return ReadOnlyTransportResponse(
        query_id=_safe_text(query.query_id),
        scenario_id=query.scenario_id if query.scenario_id in SCENARIOS_BY_ID else None,
        status=ReadOnlyTransportStatus.BLOCKED,
        decision=ReadOnlyTransportDecision.BLOCKED,
        response_summary="fake transport blocked an invalid read-only query before fixture lookup",
        error_category=ReadOnlyTransportErrorCategory.INVALID_QUERY,
        reasons=validation.reasons,
        first_blocking_reason=_safe_text(reason),
    )


def _missing_fixture_response(query: ReadOnlyTransportQuery) -> ReadOnlyTransportResponse:
    reason = "no fake transport fixture is registered for this read-only query"
    return ReadOnlyTransportResponse(
        query_id=_safe_text(query.query_id),
        scenario_id=query.scenario_id if query.scenario_id in SCENARIOS_BY_ID else None,
        status=ReadOnlyTransportStatus.BLOCKED,
        decision=ReadOnlyTransportDecision.BLOCKED,
        response_summary="fake transport has no fixture for this read-only query",
        error_category=ReadOnlyTransportErrorCategory.MISSING_FIXTURE,
        reasons=(reason,),
        first_blocking_reason=reason,
    )


def _response_from_fixture(
    query: ReadOnlyTransportQuery,
    fixture: FakeTransportFixture,
) -> ReadOnlyTransportResponse:
    scenario_id = query.scenario_id if query.scenario_id in SCENARIOS_BY_ID else None
    family_label = _family_value(query.query_family)

    if fixture.status is ReadOnlyTransportStatus.SUCCESS:
        return ReadOnlyTransportResponse(
            query_id=query.query_id,
            scenario_id=scenario_id,
            status=ReadOnlyTransportStatus.SUCCESS,
            decision=ReadOnlyTransportDecision.PASS,
            response_summary=_safe_text(f"fake transport returned a read-only {family_label} result"),
            error_category=ReadOnlyTransportErrorCategory.NONE,
            artifact_safe_payload=_safe_summary(dict(fixture.payload)),
        )

    if fixture.status is ReadOnlyTransportStatus.TIMEOUT:
        if fixture.retryable:
            decision = ReadOnlyTransportDecision.INFRA_RETRYABLE
            summary = "fake transport timed out before any live contact and is infra-retryable"
        else:
            decision = ReadOnlyTransportDecision.NO_GO
            summary = "fake transport timed out before any live contact and is not retryable"
        return ReadOnlyTransportResponse(
            query_id=query.query_id,
            scenario_id=scenario_id,
            status=ReadOnlyTransportStatus.TIMEOUT,
            decision=decision,
            response_summary=summary,
            error_category=ReadOnlyTransportErrorCategory.TIMEOUT,
            timeout=True,
            retryable=bool(fixture.retryable),
            first_blocking_reason=summary,
        )

    if fixture.status is ReadOnlyTransportStatus.UNSAFE_PAYLOAD:
        summary = "fake transport produced an unsafe payload and was blocked before publication"
        return ReadOnlyTransportResponse(
            query_id=query.query_id,
            scenario_id=scenario_id,
            status=ReadOnlyTransportStatus.UNSAFE_PAYLOAD,
            decision=ReadOnlyTransportDecision.NO_GO,
            response_summary=summary,
            error_category=ReadOnlyTransportErrorCategory.UNSAFE_PAYLOAD,
            first_blocking_reason=summary,
        )

    # FAILED or an explicitly BLOCKED fixture.
    error_category = (
        fixture.error_category
        if fixture.error_category is not ReadOnlyTransportErrorCategory.NONE
        else (
            ReadOnlyTransportErrorCategory.POLICY_BLOCKED
            if fixture.status is ReadOnlyTransportStatus.BLOCKED
            else ReadOnlyTransportErrorCategory.TRANSPORT_FAILURE
        )
    )
    if error_category is ReadOnlyTransportErrorCategory.POLICY_BLOCKED:
        decision = ReadOnlyTransportDecision.BLOCKED
        status = ReadOnlyTransportStatus.BLOCKED
        summary = "fake transport returned a policy-blocked read-only result"
    else:
        decision = ReadOnlyTransportDecision.NO_GO
        status = ReadOnlyTransportStatus.FAILED
        summary = "fake transport returned a failed read-only result"
    return ReadOnlyTransportResponse(
        query_id=query.query_id,
        scenario_id=scenario_id,
        status=status,
        decision=decision,
        response_summary=summary,
        error_category=error_category,
        first_blocking_reason=summary,
    )


# --- Summaries -----------------------------------------------------------------------------------


def summarize_transport_response(response: ReadOnlyTransportResponse) -> dict[str, Any]:
    """Return an artifact-safe summary of a single fake transport response."""
    payload = {
        "transport_kind": response.transport_kind.value,
        "query_id": response.query_id,
        "scenario_id": response.scenario_id,
        "status": response.status.value,
        "decision": response.decision.value,
        "error_category": response.error_category.value,
        "timeout": response.timeout,
        "retryable": response.retryable,
        "live_contact_attempted": response.live_contact_attempted,
        "live_contact_succeeded": response.live_contact_succeeded,
        "mutation_attempted": response.mutation_attempted,
        "live_certification_evidence": response.live_certification_evidence,
        "no_live_contact": response.no_live_contact,
        "redaction_status": response.redaction_status,
        "response_summary": sanitize_artifact_text(response.response_summary),
        "first_blocking_reason": sanitize_artifact_text(response.first_blocking_reason),
        "reasons": [sanitize_artifact_text(reason) for reason in response.reasons],
        "artifact_safe_payload": dict(response.artifact_safe_payload),
    }
    return _safe_summary(payload)


def summarize_fake_transport_run(
    queries: Sequence[ReadOnlyTransportQuery],
    responses: Sequence[ReadOnlyTransportResponse],
    *,
    request: ReadOnlyDiscoveryRequest | None = None,
) -> ReadOnlyTransportArtifactSummary:
    """Return an artifact-safe summary of a full fake transport run, never claiming live contact."""
    payload = {
        "transport_kind": ReadOnlyTransportKind.FAKE.value,
        "no_live_contact": all(response.no_live_contact for response in responses) if responses else True,
        "live_contact_attempted": any(response.live_contact_attempted for response in responses),
        "live_contact_succeeded": any(response.live_contact_succeeded for response in responses),
        "mutation_attempted": any(response.mutation_attempted for response in responses),
        "live_certification_evidence": any(response.live_certification_evidence for response in responses),
        "call_count": len(responses),
        "request_id": request.request_id if isinstance(request, ReadOnlyDiscoveryRequest) else None,
        "scenario_ids": sorted({response.scenario_id for response in responses if response.scenario_id}),
        "query_ids": [response.query_id for response in responses],
        "query_families": sorted({_family_value(query.query_family) for query in queries}),
        "verb_classes": sorted({classify_read_only_verb(query.verb).value for query in queries}),
        "decisions": [response.decision.value for response in responses],
        "statuses": [response.status.value for response in responses],
        "timeouts": [response.timeout for response in responses],
        "retryable": [response.retryable for response in responses],
        "redaction_status": "redacted",
        "blocked_reasons": [
            sanitize_artifact_text(response.first_blocking_reason)
            for response in responses
            if response.first_blocking_reason
        ],
        "response_summaries": [summarize_transport_response(response) for response in responses],
    }
    return ReadOnlyTransportArtifactSummary(
        transport_kind=ReadOnlyTransportKind.FAKE,
        decision=_aggregate_decision(responses),
        payload=_safe_summary(payload),
    )


# --- Phase 8G integration helpers ----------------------------------------------------------------


def build_transport_queries_from_backend_request(
    request: ReadOnlyDiscoveryRequest,
) -> tuple[ReadOnlyTransportQuery, ...]:
    """Project a validated Phase 8G discovery request onto fake transport queries (no live contact)."""
    if not isinstance(request, ReadOnlyDiscoveryRequest):
        return ()
    bundle = request.query_plan_bundle
    if not isinstance(bundle, ReadOnlyQueryPlanBundle):
        return ()

    hub_label = request.expected_physical_labels[0] if request.expected_physical_labels else "primary"
    queries: list[ReadOnlyTransportQuery] = []
    for index, plan in enumerate(bundle.query_plans):
        queries.append(
            ReadOnlyTransportQuery(
                query_id=f"{request.request_id}-q{index}",
                scenario_id=plan.scenario_id,
                query_family=plan.query_family,
                verb=plan.verb,
                hub_label=hub_label,
                resource_family=_family_value(plan.query_family),
                guardrail_result=validate_read_only_query_plan(plan),
                required_gate_ids=plan.required_gate_ids,
                artifact_fields=plan.artifact_fields,
                redaction_required=plan.redaction_required,
                live_certification_evidence=False,
                mutation_enabled=False,
            )
        )
    return tuple(queries)


def collect_fake_transport_evidence(
    transport: FakeReadOnlyTransport,
    queries: Sequence[ReadOnlyTransportQuery],
) -> tuple[ReadOnlyTransportResponse, ...]:
    """Execute each query through the fake transport and collect deterministic responses."""
    return tuple(transport.execute(query) for query in queries)


# --- Example builders ----------------------------------------------------------------------------


def build_example_transport_query(
    scenario_id: str = "preflight",
    query_family: ReadOnlyQueryFamily = ReadOnlyQueryFamily.CLUSTER_IDENTITY,
    verb: str = "get",
) -> ReadOnlyTransportQuery:
    """Return a sanitized example transport query that passes the guardrails (no live values)."""
    gate_ids = tuple(required_read_only_discovery_gate_ids())
    artifact_fields = ("physical_identity_evidence", "gate_status", "decision")
    plan = ReadOnlyQueryPlan(
        scenario_id=scenario_id,
        query_family=query_family,
        verb=verb,
        required_gate_ids=gate_ids,
        artifact_fields=artifact_fields,
    )
    return ReadOnlyTransportQuery(
        query_id="phase8h-query",
        scenario_id=scenario_id,
        query_family=query_family,
        verb=verb,
        hub_label="primary",
        resource_family=_family_value(query_family),
        guardrail_result=validate_read_only_query_plan(plan),
        required_gate_ids=gate_ids,
        artifact_fields=artifact_fields,
    )


def build_example_fake_transport_fixture(query_id: str = "phase8h-query") -> FakeTransportFixture:
    """Return a sanitized example success fixture (artifact-safe payload only)."""
    return FakeTransportFixture(
        query_id=query_id,
        status=ReadOnlyTransportStatus.SUCCESS,
        payload={
            "observed_identity_summary": "redacted-physical-identity-summary",
            "evidence_present": True,
            "signal_count": 2,
        },
    )


# --- Internal helpers ----------------------------------------------------------------------------


def _build_fixture_map(fixtures: Sequence[FakeTransportFixture]) -> dict[str, FakeTransportFixture]:
    mapping: dict[str, FakeTransportFixture] = {}
    for fixture in fixtures:
        if not isinstance(fixture, FakeTransportFixture):
            raise ValueError("fake transport fixtures must be FakeTransportFixture instances")
        if fixture.query_id in mapping:
            raise ValueError("duplicate fake transport fixture query_id is not allowed")
        mapping[fixture.query_id] = fixture
    return mapping


def _aggregate_decision(responses: Sequence[ReadOnlyTransportResponse]) -> ReadOnlyTransportDecision:
    if not responses:
        return ReadOnlyTransportDecision.BLOCKED
    decisions = {response.decision for response in responses}
    if ReadOnlyTransportDecision.BLOCKED in decisions:
        return ReadOnlyTransportDecision.BLOCKED
    if ReadOnlyTransportDecision.NO_GO in decisions:
        return ReadOnlyTransportDecision.NO_GO
    if ReadOnlyTransportDecision.INFRA_RETRYABLE in decisions:
        return ReadOnlyTransportDecision.INFRA_RETRYABLE
    return ReadOnlyTransportDecision.PASS


def _safe_query_call_summary(query: ReadOnlyTransportQuery) -> dict[str, Any]:
    is_query = isinstance(query, ReadOnlyTransportQuery)
    payload = {
        "transport_kind": ReadOnlyTransportKind.FAKE.value,
        "query_id": query.query_id if is_query else None,
        "scenario_id": query.scenario_id if is_query else None,
        "query_family": _family_value(query.query_family) if is_query else None,
        "verb": query.verb if is_query else None,
        "verb_class": classify_read_only_verb(query.verb).value if is_query else None,
        "hub_label": query.hub_label if is_query else None,
        "resource_family": query.resource_family if is_query else None,
        "live_contact_attempted": False,
        "live_certification_evidence": False,
        "mutation_enabled": False,
    }
    return _safe_summary(payload)


def _assert_artifact_safe_payload(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise ValueError("fake transport fixture payload must be a mapping")
    if _payload_has_forbidden_artifact_key(payload):
        raise ValueError("fake transport fixture payload must not contain runtime-only artifact keys")
    if _payload_has_unsafe_value(payload):
        raise ValueError("fake transport fixture payload must not contain unsafe artifact-facing values")
    validate_artifact_payload_redacted(dict(payload))


def _payload_has_forbidden_artifact_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in _FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS):
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


def _safe_text(value: str | None) -> str:
    sanitized = sanitize_artifact_text(value)
    return sanitized if sanitized is not None else ""


def _family_value(query_family: ReadOnlyQueryFamily | str) -> str:
    if isinstance(query_family, Enum):
        return str(query_family.value)
    return str(query_family)


def _block(reasons: list[str], blocking_fields: list[str], field_name: str, reason: str) -> None:
    reasons.append(reason)
    blocking_fields.append(field_name)


__all__ = [
    "FakeReadOnlyTransport",
    "FakeTransportFixture",
    "ReadOnlyTransportArtifactSummary",
    "ReadOnlyTransportDecision",
    "ReadOnlyTransportErrorCategory",
    "ReadOnlyTransportKind",
    "ReadOnlyTransportQuery",
    "ReadOnlyTransportQueryValidation",
    "ReadOnlyTransportResponse",
    "ReadOnlyTransportStatus",
    "build_example_fake_transport_fixture",
    "build_example_transport_query",
    "build_transport_queries_from_backend_request",
    "collect_fake_transport_evidence",
    "summarize_fake_transport_run",
    "summarize_transport_response",
    "validate_transport_query",
]
