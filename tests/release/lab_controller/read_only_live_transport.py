"""Opt-in, read-only live transport contracts for the lab role controller (Phase 8J).

Phase 8J turns the Phase 8I read-only live transport *design review* into the first opt-in,
read-only live transport implementation. It is intentionally narrow and fail-closed:

- It is **disabled by default**. Live contact requires explicit opt-in flags plus an injected
  client. With either flag false, no client present, no runtime handle, or missing gate evidence,
  the transport returns ``BLOCKED`` *before* any client call.
- It owns all live-contact semantics behind a controller-owned, typed client protocol
  (``ReadOnlyLiveClientProtocol``). It executes only structured read-only query objects, never shell
  strings, never ``oc``/``kubectl``/``ansible-playbook``, never a release adapter.
- It reuses the Phase 8E read-only discovery guardrails, the Phase 8H structured query validation,
  and the existing redaction helpers unchanged. It does not widen them.
- It never mutates and never sets ``live_certification_evidence`` true. ``mutation_attempted`` and
  ``live_certification_evidence`` are forced false on every result.

Strict non-live boundary (intentionally enforced by tests):

- This module does not import ``os``, ``subprocess``, ``socket``, ``yaml``, ``json``,
  ``kubernetes``, ``requests``, ``urllib``, or ``http``.
- It does not read ``os.environ`` or inherit ambient environment.
- It does not load live config files, read kubeconfigs, or dereference runtime handles.
- It does not run subprocesses or contact clusters by itself. Real cluster contact happens only in a
  caller-supplied injected client, exercised in tests with fakes only.

The runtime-only handle/credential/context references supplied by a caller are never artifact-facing.
Artifact summaries carry only physical labels, presence booleans, redacted fingerprints, and
allowlisted structured fields.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from tests.release.lab_controller.artifacts import (
    sanitize_artifact_payload,
    sanitize_artifact_text,
    validate_artifact_payload_redacted,
)
from tests.release.lab_controller.live_config import LiveGateId
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyDiscoveryGuardDecision,
    required_read_only_discovery_gate_ids,
    validate_read_only_discovery_gates,
)
from tests.release.lab_controller.read_only_transport import (
    ReadOnlyTransportDecision,
    ReadOnlyTransportQuery,
    validate_transport_query,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

# Redaction patterns mirror the Phase 8E/8G/8H value-unsafe patterns unchanged (never widened). They
# detect unsafe artifact *values*; the structural/key decision stays separate from value redaction.
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
# appear as artifact-payload or summary keys.
_FORBIDDEN_ARTIFACT_KEY_SUBSTRINGS: tuple[str, ...] = (
    "kubeconfig_ref",
    "context_ref",
    "credential_ref",
    "transport_handle_ref",
    "client_ref",
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

# Conservative bounds so a broad live response dump cannot be published. Exceeding any bound rejects
# the payload rather than guessing what is safe.
_MAX_TOP_LEVEL_KEYS = 25
_MAX_STRING_LENGTH = 512
_MAX_COLLECTION_LENGTH = 50

# Only these value types can be proven artifact-safe by the str-based redaction layer. Any other
# type (e.g., ``bytes`` from a real client, or an arbitrary object whose ``repr`` could leak) is
# rejected fail-closed instead of being published unredacted.
_SAFE_SCALAR_TYPES: tuple[type, ...] = (str, bool, int, float)


class ReadOnlyLiveTransportKind(str, Enum):
    """Transport implementation represented by a Phase 8J result."""

    LIVE_READ_ONLY = "live_read_only"


class ReadOnlyLiveTransportStatus(str, Enum):
    """Status of a read-only live transport interaction."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNSAFE_PAYLOAD = "unsafe_payload"  # nosec B105


class ReadOnlyLiveTransportErrorCategory(str, Enum):
    """Deterministic error categories for a read-only live transport interaction."""

    NONE = "none"
    INVALID_QUERY = "invalid_query"
    NOT_OPTED_IN = "not_opted_in"
    MISSING_CLIENT = "missing_client"
    MISSING_HANDLE = "missing_handle"
    MISSING_GATES = "missing_gates"
    POLICY_BLOCKED = "policy_blocked"
    TIMEOUT = "timeout"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    SAFETY_VIOLATION = "safety_violation"
    UNSAFE_PAYLOAD = "unsafe_payload"  # nosec B105
    REDACTION_FAILURE = "redaction_failure"


# --- Typed transport errors ----------------------------------------------------------------------


class ReadOnlyLiveTransportError(Exception):
    """Base class for read-only live transport client errors. Not retryable by default."""

    retryable: bool = False

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(message)
        if retryable is not None:
            self.retryable = bool(retryable)


class ReadOnlyLiveTransientError(ReadOnlyLiveTransportError):
    """A transient, read-only failure where no mutation occurred. Retryable by default."""

    retryable: bool = True


class ReadOnlyLiveTimeoutError(ReadOnlyLiveTransientError):
    """A read-only transport timeout before completion. Retryable unless explicitly disabled."""

    retryable: bool = True


class ReadOnlyLivePermanentError(ReadOnlyLiveTransportError):
    """A permanent read-only failure. Not retryable."""

    retryable: bool = False


class ReadOnlyLiveSafetyError(ReadOnlyLiveTransportError):
    """A safety-relevant failure (e.g., unsafe observation). Not retryable."""

    retryable: bool = False


# --- Runtime-only handles and options ------------------------------------------------------------


@dataclass(frozen=True)
class ReadOnlyLiveTransportOptions:
    """Explicit, deliberately risky opt-in options for read-only live contact.

    Both ``allow_live_contact`` and ``allow_read_only_queries`` must be exactly true before any
    contact. ``approval_reference`` is runtime-only and only its presence is published.
    """

    allow_live_contact: bool = False
    allow_read_only_queries: bool = False
    timeout_seconds: float = 30.0
    approval_reference: str | None = None


@dataclass(frozen=True)
class RuntimeOnlyLiveHubHandle:
    """Runtime-only hub handles supplied by a caller.

    Values are opaque references. They are never dereferenced here and never copied into
    artifact-facing summaries. Summaries publish only the physical label and presence booleans.
    """

    physical_label: str
    kubeconfig_ref: str
    context_ref: str
    credential_ref: str | None = None
    client_ref: str | None = None

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "physical_label": self.physical_label,
                "cluster_access_handle_present": bool(self.kubeconfig_ref),
                "context_handle_present": bool(self.context_ref),
                "auth_handle_present": self.credential_ref is not None,
                "client_handle_present": self.client_ref is not None,
                "runtime_values_redacted": True,
            }
        )


@dataclass(frozen=True)
class RuntimeOnlyLiveTransportContext:
    """Runtime-only context bundling the hub handle, opt-in options, and L0-L9 gate evidence.

    ``allowed_env_var_names`` holds explicit, minimal environment variable *names* only (never
    values). Nothing here is read from the ambient environment.
    """

    handle: RuntimeOnlyLiveHubHandle
    options: ReadOnlyLiveTransportOptions
    gate_ids: tuple[LiveGateId | str, ...]
    allowed_env_var_names: tuple[str, ...] = ()

    def to_artifact_safe_summary(self) -> dict[str, Any]:
        return _safe_summary(
            {
                "handle": self.handle.to_artifact_safe_summary(),
                "allow_live_contact": self.options.allow_live_contact,
                "allow_read_only_queries": self.options.allow_read_only_queries,
                "timeout_seconds": self.options.timeout_seconds,
                "approval_reference_present": self.options.approval_reference is not None,
                "gate_ids": [_gate_value(gate) for gate in self.gate_ids],
                "allowed_env_var_names": sorted(str(name) for name in self.allowed_env_var_names),
                "runtime_values_redacted": True,
            }
        )


# --- Structured client contract ------------------------------------------------------------------


@dataclass(frozen=True)
class ReadOnlyLiveClientRequest:
    """A structured read-only request handed to the injected client. Never a shell string."""

    query_id: str
    scenario_id: str
    query_family: str
    verb: str
    hub_label: str
    resource_family: str
    timeout_seconds: float


@dataclass(frozen=True)
class RawReadOnlyLiveResponse:
    """Raw runtime data returned by an injected client.

    The payload is **not** assumed artifact-safe. It must be summarized/redacted (and may be
    rejected) before any artifact publication.
    """

    query_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ReadOnlyLiveClientProtocol(Protocol):
    """Minimal controller-owned client protocol for read-only live reads.

    A real implementation may be backed by a typed Kubernetes/OpenShift client supplied by the
    caller. It receives structured query data and returns raw runtime data. It may raise the typed
    transport errors above. It is never handed a shell string and never invoked by default.
    """

    def execute_read_query(self, request: ReadOnlyLiveClientRequest) -> RawReadOnlyLiveResponse:
        """Execute a single structured read-only request and return raw runtime data."""
        ...


# --- Guard and result models ---------------------------------------------------------------------


@dataclass(frozen=True)
class ReadOnlyLiveContactGuardDecision:
    """Structured opt-in/guardrail decision evaluated before any client call. Never raises."""

    decision: ReadOnlyTransportDecision
    error_category: ReadOnlyLiveTransportErrorCategory = ReadOnlyLiveTransportErrorCategory.NONE
    reasons: tuple[str, ...] = ()
    blocking_fields: tuple[str, ...] = ()

    @property
    def is_pass(self) -> bool:
        return self.decision is ReadOnlyTransportDecision.PASS


@dataclass(frozen=True)
class ReadOnlyLiveTransportResult:
    """A read-only live transport result. ``mutation_attempted`` and ``live_certification_evidence``
    are forced false on every result regardless of inputs."""

    query_id: str | None
    scenario_id: str | None
    status: ReadOnlyLiveTransportStatus
    decision: ReadOnlyTransportDecision
    response_summary: str
    error_category: ReadOnlyLiveTransportErrorCategory = ReadOnlyLiveTransportErrorCategory.NONE
    timeout: bool = False
    retryable: bool = False
    live_contact_attempted: bool = False
    live_contact_succeeded: bool = False
    real_execution_evidence: bool = False
    mutation_attempted: bool = False
    live_certification_evidence: bool = False
    redaction_status: str = "redacted"
    artifact_safe_payload: Mapping[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    first_blocking_reason: str | None = None
    transport_kind: ReadOnlyLiveTransportKind = ReadOnlyLiveTransportKind.LIVE_READ_ONLY

    def __post_init__(self) -> None:
        # Hard invariant: a Phase 8J result can never claim mutation or live certification evidence.
        object.__setattr__(self, "mutation_attempted", False)
        object.__setattr__(self, "live_certification_evidence", False)

    @property
    def no_live_contact(self) -> bool:
        return not self.live_contact_attempted


# --- Opt-in guard --------------------------------------------------------------------------------


def evaluate_read_only_live_contact_guard(
    context: RuntimeOnlyLiveTransportContext,
    client: ReadOnlyLiveClientProtocol | None,
    query: ReadOnlyTransportQuery,
) -> ReadOnlyLiveContactGuardDecision:
    """Evaluate every opt-in and guardrail check before any client call. Fails closed."""
    if not isinstance(context, RuntimeOnlyLiveTransportContext):
        return ReadOnlyLiveContactGuardDecision(
            decision=ReadOnlyTransportDecision.BLOCKED,
            error_category=ReadOnlyLiveTransportErrorCategory.INVALID_QUERY,
            reasons=("context must be a RuntimeOnlyLiveTransportContext",),
            blocking_fields=("context",),
        )

    reasons: list[str] = []
    blocking_fields: list[str] = []
    category = ReadOnlyLiveTransportErrorCategory.NONE

    def _fail(error_category: ReadOnlyLiveTransportErrorCategory, field_name: str, reason: str) -> None:
        nonlocal category
        if category is ReadOnlyLiveTransportErrorCategory.NONE:
            category = error_category
        reasons.append(reason)
        blocking_fields.append(field_name)

    options = context.options
    if not isinstance(options, ReadOnlyLiveTransportOptions) or options.allow_live_contact is not True:
        _fail(
            ReadOnlyLiveTransportErrorCategory.NOT_OPTED_IN,
            "allow_live_contact",
            "allow_live_contact must be exactly true before read-only live contact",
        )
    if not isinstance(options, ReadOnlyLiveTransportOptions) or options.allow_read_only_queries is not True:
        _fail(
            ReadOnlyLiveTransportErrorCategory.NOT_OPTED_IN,
            "allow_read_only_queries",
            "allow_read_only_queries must be exactly true before read-only live contact",
        )
    if client is None:
        _fail(
            ReadOnlyLiveTransportErrorCategory.MISSING_CLIENT,
            "client",
            "an injected read-only client is required before contact",
        )
    elif not isinstance(client, ReadOnlyLiveClientProtocol) or not callable(
        getattr(client, "execute_read_query", None)
    ):
        _fail(
            ReadOnlyLiveTransportErrorCategory.MISSING_CLIENT,
            "client",
            "client must implement a callable ReadOnlyLiveClientProtocol before contact",
        )
    if not _runtime_handle_present(context.handle):
        _fail(
            ReadOnlyLiveTransportErrorCategory.MISSING_HANDLE,
            "handle",
            "a runtime-only hub handle with access and context references is required before contact",
        )
    if not _positive_timeout(options):
        _fail(
            ReadOnlyLiveTransportErrorCategory.INVALID_QUERY,
            "timeout_seconds",
            "timeout_seconds must be a positive number",
        )

    gate_result = validate_read_only_discovery_gates(context.gate_ids)
    if gate_result.decision is not ReadOnlyDiscoveryGuardDecision.PASS:
        _fail(
            ReadOnlyLiveTransportErrorCategory.MISSING_GATES,
            "required_gate_ids",
            "L0-L9 read-only discovery gate evidence must be present before contact",
        )

    query_validation = validate_transport_query(query)
    if query_validation.decision is not ReadOnlyTransportDecision.PASS:
        for field_name in query_validation.blocking_fields or ("query",):
            _fail(
                ReadOnlyLiveTransportErrorCategory.POLICY_BLOCKED,
                field_name,
                "query failed the Phase 8E/8H read-only guardrails before contact",
            )

    decision = ReadOnlyTransportDecision.BLOCKED if reasons else ReadOnlyTransportDecision.PASS
    return ReadOnlyLiveContactGuardDecision(
        decision=decision,
        error_category=category if reasons else ReadOnlyLiveTransportErrorCategory.NONE,
        reasons=tuple(dict.fromkeys(reasons)),
        blocking_fields=tuple(dict.fromkeys(blocking_fields)),
    )


# --- Transport -----------------------------------------------------------------------------------


class ReadOnlyLiveTransport:
    """An opt-in, read-only live transport.

    Every ``execute`` call validates the opt-in guard before touching the injected client. If the
    guard does not pass, the client is never called and the result is ``BLOCKED`` with no live
    contact. Otherwise the structured request is handed to the injected client and the raw response
    is classified and redacted.
    """

    transport_kind = ReadOnlyLiveTransportKind.LIVE_READ_ONLY

    def __init__(
        self,
        context: RuntimeOnlyLiveTransportContext,
        client: ReadOnlyLiveClientProtocol | None = None,
    ) -> None:
        self._context = context
        self._client = client
        self._received_requests: list[ReadOnlyLiveClientRequest] = []
        self._live_contact_attempts = 0

    @property
    def live_contact_attempts(self) -> int:
        return self._live_contact_attempts

    @property
    def received_requests(self) -> tuple[ReadOnlyLiveClientRequest, ...]:
        return tuple(self._received_requests)

    def execute(self, query: ReadOnlyTransportQuery) -> ReadOnlyLiveTransportResult:
        guard = evaluate_read_only_live_contact_guard(self._context, self._client, query)
        if not guard.is_pass:
            return _blocked_result(query, guard)

        client = self._client
        if client is None:  # pragma: no cover - guard guarantees a client; defensive only
            return _blocked_result(
                query,
                ReadOnlyLiveContactGuardDecision(
                    decision=ReadOnlyTransportDecision.BLOCKED,
                    error_category=ReadOnlyLiveTransportErrorCategory.MISSING_CLIENT,
                    reasons=("an injected read-only client is required before contact",),
                    blocking_fields=("client",),
                ),
            )

        request = _build_client_request(query, self._context.options)
        self._received_requests.append(request)
        self._live_contact_attempts += 1

        try:
            raw = client.execute_read_query(request)
        except ReadOnlyLiveTimeoutError as exc:
            return _timeout_result(query, exc)
        except ReadOnlyLiveTransientError as exc:
            return _transient_result(query, exc)
        except ReadOnlyLiveSafetyError as exc:
            return _safety_result(query, exc)
        except ReadOnlyLivePermanentError as exc:
            return _permanent_result(query, exc)
        except Exception:  # noqa: BLE001 - fail closed; message intentionally not echoed
            return _unexpected_error_result(query)

        return _result_from_raw(query, raw)


# --- Response classification ---------------------------------------------------------------------


def _result_from_raw(
    query: ReadOnlyTransportQuery,
    raw: RawReadOnlyLiveResponse,
) -> ReadOnlyLiveTransportResult:
    if not isinstance(raw, RawReadOnlyLiveResponse):
        return ReadOnlyLiveTransportResult(
            query_id=_safe_text(_query_id(query)),
            scenario_id=_scenario_id(query),
            status=ReadOnlyLiveTransportStatus.UNSAFE_PAYLOAD,
            decision=ReadOnlyTransportDecision.NO_GO,
            response_summary="live read-only client returned a non-structured response",
            error_category=ReadOnlyLiveTransportErrorCategory.SAFETY_VIOLATION,
            live_contact_attempted=True,
            live_contact_succeeded=False,
            real_execution_evidence=True,
            redaction_status="rejected",
            first_blocking_reason="live read-only client returned a non-structured response",
        )

    if raw.query_id != query.query_id:
        # A response for a different request must never be attributed to this query.
        return ReadOnlyLiveTransportResult(
            query_id=_safe_text(_query_id(query)),
            scenario_id=_scenario_id(query),
            status=ReadOnlyLiveTransportStatus.FAILED,
            decision=ReadOnlyTransportDecision.NO_GO,
            response_summary="live read-only client returned a mismatched response",
            error_category=ReadOnlyLiveTransportErrorCategory.SAFETY_VIOLATION,
            live_contact_attempted=True,
            live_contact_succeeded=False,
            real_execution_evidence=True,
            redaction_status="rejected",
            first_blocking_reason="live read-only client returned a mismatched query_id",
        )

    sanitized, category = _classify_live_payload(raw.payload)
    if sanitized is None:
        return ReadOnlyLiveTransportResult(
            query_id=_safe_text(_query_id(query)),
            scenario_id=_scenario_id(query),
            status=ReadOnlyLiveTransportStatus.UNSAFE_PAYLOAD,
            decision=ReadOnlyTransportDecision.NO_GO,
            response_summary="live read-only response was rejected before publication",
            error_category=category,
            live_contact_attempted=True,
            live_contact_succeeded=True,
            real_execution_evidence=True,
            redaction_status="rejected",
            first_blocking_reason="live read-only response was not artifact-safe",
        )

    return ReadOnlyLiveTransportResult(
        query_id=_safe_text(_query_id(query)),
        scenario_id=_scenario_id(query),
        status=ReadOnlyLiveTransportStatus.SUCCESS,
        decision=ReadOnlyTransportDecision.PASS,
        response_summary="live read-only transport returned a redacted read result",
        error_category=ReadOnlyLiveTransportErrorCategory.NONE,
        live_contact_attempted=True,
        live_contact_succeeded=True,
        real_execution_evidence=True,
        redaction_status="redacted",
        artifact_safe_payload=sanitized,
    )


def _timeout_result(query: ReadOnlyTransportQuery, exc: ReadOnlyLiveTimeoutError) -> ReadOnlyLiveTransportResult:
    retryable = bool(exc.retryable)
    decision = ReadOnlyTransportDecision.INFRA_RETRYABLE if retryable else ReadOnlyTransportDecision.NO_GO
    summary = (
        "live read-only transport timed out before completing the read and is infra-retryable"
        if retryable
        else "live read-only transport timed out before completing the read and is not retryable"
    )
    return ReadOnlyLiveTransportResult(
        query_id=_safe_text(_query_id(query)),
        scenario_id=_scenario_id(query),
        status=ReadOnlyLiveTransportStatus.TIMEOUT,
        decision=decision,
        response_summary=summary,
        error_category=ReadOnlyLiveTransportErrorCategory.TIMEOUT,
        timeout=True,
        retryable=retryable,
        live_contact_attempted=True,
        live_contact_succeeded=False,
        real_execution_evidence=True,
        first_blocking_reason=_sanitized_error_detail(str(exc)),
    )


def _transient_result(query: ReadOnlyTransportQuery, exc: ReadOnlyLiveTransientError) -> ReadOnlyLiveTransportResult:
    retryable = bool(exc.retryable)
    decision = ReadOnlyTransportDecision.INFRA_RETRYABLE if retryable else ReadOnlyTransportDecision.NO_GO
    summary = (
        "live read-only transport hit a transient error and is infra-retryable"
        if retryable
        else "live read-only transport hit a transient error and is not retryable"
    )
    return ReadOnlyLiveTransportResult(
        query_id=_safe_text(_query_id(query)),
        scenario_id=_scenario_id(query),
        status=ReadOnlyLiveTransportStatus.FAILED,
        decision=decision,
        response_summary=summary,
        error_category=ReadOnlyLiveTransportErrorCategory.TRANSIENT_FAILURE,
        retryable=retryable,
        live_contact_attempted=True,
        live_contact_succeeded=False,
        real_execution_evidence=True,
        first_blocking_reason=_sanitized_error_detail(str(exc)),
    )


def _permanent_result(query: ReadOnlyTransportQuery, exc: ReadOnlyLivePermanentError) -> ReadOnlyLiveTransportResult:
    return ReadOnlyLiveTransportResult(
        query_id=_safe_text(_query_id(query)),
        scenario_id=_scenario_id(query),
        status=ReadOnlyLiveTransportStatus.FAILED,
        decision=ReadOnlyTransportDecision.NO_GO,
        response_summary="live read-only transport hit a permanent error",
        error_category=ReadOnlyLiveTransportErrorCategory.PERMANENT_FAILURE,
        retryable=False,
        live_contact_attempted=True,
        live_contact_succeeded=False,
        real_execution_evidence=True,
        first_blocking_reason=_sanitized_error_detail(str(exc)),
    )


def _safety_result(query: ReadOnlyTransportQuery, exc: ReadOnlyLiveSafetyError) -> ReadOnlyLiveTransportResult:
    return ReadOnlyLiveTransportResult(
        query_id=_safe_text(_query_id(query)),
        scenario_id=_scenario_id(query),
        status=ReadOnlyLiveTransportStatus.FAILED,
        decision=ReadOnlyTransportDecision.NO_GO,
        response_summary="live read-only transport hit a safety error",
        error_category=ReadOnlyLiveTransportErrorCategory.SAFETY_VIOLATION,
        retryable=False,
        live_contact_attempted=True,
        live_contact_succeeded=False,
        real_execution_evidence=True,
        first_blocking_reason=_sanitized_error_detail(str(exc)),
    )


def _unexpected_error_result(query: ReadOnlyTransportQuery) -> ReadOnlyLiveTransportResult:
    return ReadOnlyLiveTransportResult(
        query_id=_safe_text(_query_id(query)),
        scenario_id=_scenario_id(query),
        status=ReadOnlyLiveTransportStatus.FAILED,
        decision=ReadOnlyTransportDecision.NO_GO,
        response_summary="live read-only transport hit an unexpected error",
        error_category=ReadOnlyLiveTransportErrorCategory.PERMANENT_FAILURE,
        retryable=False,
        live_contact_attempted=True,
        live_contact_succeeded=False,
        real_execution_evidence=True,
        first_blocking_reason="[REDACTED]",
    )


def _blocked_result(
    query: ReadOnlyTransportQuery,
    guard: ReadOnlyLiveContactGuardDecision,
) -> ReadOnlyLiveTransportResult:
    reason = guard.reasons[0] if guard.reasons else "live read-only contact blocked before any cluster contact"
    return ReadOnlyLiveTransportResult(
        query_id=_safe_text(_query_id(query)),
        scenario_id=_scenario_id(query),
        status=ReadOnlyLiveTransportStatus.BLOCKED,
        decision=ReadOnlyTransportDecision.BLOCKED,
        response_summary="live read-only transport blocked the request before any cluster contact",
        error_category=guard.error_category,
        live_contact_attempted=False,
        live_contact_succeeded=False,
        real_execution_evidence=False,
        reasons=tuple(_safe_text(item) for item in guard.reasons),
        first_blocking_reason=_safe_text(reason),
    )


# --- Summaries -----------------------------------------------------------------------------------


def summarize_live_transport_result(result: ReadOnlyLiveTransportResult) -> dict[str, Any]:
    """Return an artifact-safe summary of a read-only live transport result.

    Raw runtime inputs never appear. The summary records IDs, families, decisions, booleans,
    redacted reasons, and the already-redacted artifact-safe payload only.
    """
    payload = {
        "transport_kind": result.transport_kind.value,
        "discovery_mode": "read_only",
        "query_id": result.query_id,
        "scenario_id": result.scenario_id,
        "status": result.status.value,
        "decision": result.decision.value,
        "error_category": result.error_category.value,
        "timeout": result.timeout,
        "retryable": result.retryable,
        "live_contact_attempted": result.live_contact_attempted,
        "live_contact_succeeded": result.live_contact_succeeded,
        "real_execution_evidence": result.real_execution_evidence,
        "mutation_attempted": False,
        "mutation_enabled": False,
        "live_certification_evidence": False,
        "no_live_contact": result.no_live_contact,
        "redaction_status": result.redaction_status,
        "response_summary": sanitize_artifact_text(result.response_summary),
        "first_blocking_reason": sanitize_artifact_text(result.first_blocking_reason),
        "reasons": [sanitize_artifact_text(item) for item in result.reasons],
        "artifact_safe_payload": dict(result.artifact_safe_payload),
    }
    return _safe_summary(payload)


# --- Example builders ----------------------------------------------------------------------------


def build_example_runtime_handle() -> RuntimeOnlyLiveHubHandle:
    """Return a sanitized example runtime handle. No real path, URL, or credential is embedded."""
    return RuntimeOnlyLiveHubHandle(
        physical_label="primary",
        kubeconfig_ref="runtime-kubeconfig-handle",
        context_ref="runtime-context-handle",
        credential_ref="runtime-credential-handle",
        client_ref="runtime-client-handle",
    )


def build_example_transport_context() -> RuntimeOnlyLiveTransportContext:
    """Return a sanitized example context that is opt-in for deterministic, fake-backed tests."""
    return RuntimeOnlyLiveTransportContext(
        handle=build_example_runtime_handle(),
        options=ReadOnlyLiveTransportOptions(
            allow_live_contact=True,
            allow_read_only_queries=True,
            timeout_seconds=30.0,
            approval_reference="runtime-approval-handle",
        ),
        gate_ids=tuple(required_read_only_discovery_gate_ids()),
    )


# --- Internal helpers ----------------------------------------------------------------------------


def _build_client_request(
    query: ReadOnlyTransportQuery,
    options: ReadOnlyLiveTransportOptions,
) -> ReadOnlyLiveClientRequest:
    return ReadOnlyLiveClientRequest(
        query_id=query.query_id,
        scenario_id=query.scenario_id,
        query_family=_family_value(query.query_family),
        verb=str(query.verb).strip().lower(),
        hub_label=query.hub_label,
        resource_family=query.resource_family,
        timeout_seconds=float(options.timeout_seconds),
    )


def _classify_live_payload(
    payload: Any,
) -> tuple[dict[str, Any] | None, ReadOnlyLiveTransportErrorCategory]:
    """Summarize/redact a raw live payload, or reject it. Fails closed."""
    if not isinstance(payload, Mapping):
        return None, ReadOnlyLiveTransportErrorCategory.REDACTION_FAILURE
    if _payload_has_unsupported_shape(payload):
        return None, ReadOnlyLiveTransportErrorCategory.REDACTION_FAILURE
    if _payload_has_forbidden_artifact_key(payload):
        return None, ReadOnlyLiveTransportErrorCategory.REDACTION_FAILURE
    if _payload_too_broad(payload):
        return None, ReadOnlyLiveTransportErrorCategory.UNSAFE_PAYLOAD
    if _payload_has_unsafe_value(payload):
        return None, ReadOnlyLiveTransportErrorCategory.UNSAFE_PAYLOAD
    sanitized = sanitize_artifact_payload(dict(payload))
    try:
        validate_artifact_payload_redacted(sanitized)
    except ValueError:
        return None, ReadOnlyLiveTransportErrorCategory.REDACTION_FAILURE
    return sanitized, ReadOnlyLiveTransportErrorCategory.NONE


def _runtime_handle_present(handle: Any) -> bool:
    return isinstance(handle, RuntimeOnlyLiveHubHandle) and bool(handle.kubeconfig_ref) and bool(handle.context_ref)


def _positive_timeout(options: Any) -> bool:
    if not isinstance(options, ReadOnlyLiveTransportOptions):
        return False
    timeout = options.timeout_seconds
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return False
    return timeout > 0


def _payload_has_unsupported_shape(value: Any) -> bool:
    """Return True if any value or mapping key is not a safe, artifact-publishable type.

    The str-based redaction layer can only prove ``str`` values safe; ``bytes`` and arbitrary
    objects bypass it. Mapping keys must be strings. Anything else fails closed.
    """
    if value is None or isinstance(value, _SAFE_SCALAR_TYPES):
        return False
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                return True
            if _payload_has_unsupported_shape(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_payload_has_unsupported_shape(item) for item in value)
    return True


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


def _payload_too_broad(value: Any, *, depth: int = 0) -> bool:
    if isinstance(value, Mapping):
        if depth == 0 and len(value) > _MAX_TOP_LEVEL_KEYS:
            return True
        if len(value) > _MAX_COLLECTION_LENGTH:
            return True
        return any(_payload_too_broad(child, depth=depth + 1) for child in value.values())
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_COLLECTION_LENGTH:
            return True
        return any(_payload_too_broad(item, depth=depth + 1) for item in value)
    if isinstance(value, str):
        return len(value) > _MAX_STRING_LENGTH
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


def _sanitized_error_detail(text: str) -> str:
    sanitized = sanitize_artifact_text(text)
    if sanitized is None or _value_is_unsafe(sanitized):
        return "[REDACTED]"
    return sanitized


def _safe_summary(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_artifact_payload(payload)
    validate_artifact_payload_redacted(sanitized)
    return sanitized


def _safe_text(value: str | None) -> str:
    sanitized = sanitize_artifact_text(value)
    return sanitized if sanitized is not None else ""


def _query_id(query: Any) -> str | None:
    return query.query_id if isinstance(query, ReadOnlyTransportQuery) else None


def _scenario_id(query: Any) -> str | None:
    if isinstance(query, ReadOnlyTransportQuery) and query.scenario_id in SCENARIOS_BY_ID:
        return query.scenario_id
    return None


def _family_value(query_family: Any) -> str:
    if isinstance(query_family, Enum):
        return str(query_family.value)
    return str(query_family)


def _gate_value(gate: LiveGateId | str) -> str:
    if isinstance(gate, Enum):
        return str(gate.value)
    return str(gate)


__all__ = [
    "RawReadOnlyLiveResponse",
    "ReadOnlyLiveClientProtocol",
    "ReadOnlyLiveClientRequest",
    "ReadOnlyLiveContactGuardDecision",
    "ReadOnlyLivePermanentError",
    "ReadOnlyLiveSafetyError",
    "ReadOnlyLiveTimeoutError",
    "ReadOnlyLiveTransientError",
    "ReadOnlyLiveTransport",
    "ReadOnlyLiveTransportError",
    "ReadOnlyLiveTransportErrorCategory",
    "ReadOnlyLiveTransportKind",
    "ReadOnlyLiveTransportOptions",
    "ReadOnlyLiveTransportResult",
    "ReadOnlyLiveTransportStatus",
    "RuntimeOnlyLiveHubHandle",
    "RuntimeOnlyLiveTransportContext",
    "build_example_runtime_handle",
    "build_example_transport_context",
    "evaluate_read_only_live_contact_guard",
    "summarize_live_transport_result",
]
