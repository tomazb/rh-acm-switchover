"""Phase 8J opt-in read-only live transport contracts for the lab role controller.

Phase 8J implements a narrowly scoped, opt-in, read-only live transport abstraction behind a
controller-owned typed client protocol. The transport is disabled by default, requires explicit
opt-in flags plus an injected client, validates the Phase 8E/8H guardrails and L0-L9 gate evidence
before any contact, never mutates, and never claims live certification evidence.

These tests use *fake* injected clients only. They never contact a cluster, never read a kubeconfig,
never read the environment, never run ``oc``/``kubectl``/``ansible-playbook``, and never call a
release adapter. Live test scaffolding is opt-in and excluded from normal CI (see
``test_lab_controller_phase8j_live_opt_in.py``).
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts.release import run_lab_role_controller as lab_controller_cli
from tests.release.lab_controller.read_only_backend import (
    ReadOnlyBackendDecision,
    UnimplementedReadOnlyDiscoveryBackend,
)
from tests.release.lab_controller.read_only_discovery import (
    ReadOnlyQueryFamily,
    required_read_only_discovery_gate_ids,
)
from tests.release.lab_controller.read_only_live_transport import (
    RawReadOnlyLiveResponse,
    ReadOnlyLiveClientRequest,
    ReadOnlyLiveContactGuardDecision,
    ReadOnlyLivePermanentError,
    ReadOnlyLiveSafetyError,
    ReadOnlyLiveTimeoutError,
    ReadOnlyLiveTransientError,
    ReadOnlyLiveTransport,
    ReadOnlyLiveTransportErrorCategory,
    ReadOnlyLiveTransportKind,
    ReadOnlyLiveTransportOptions,
    ReadOnlyLiveTransportResult,
    ReadOnlyLiveTransportStatus,
    RuntimeOnlyLiveHubHandle,
    RuntimeOnlyLiveTransportContext,
    build_example_runtime_handle,
    build_example_transport_context,
    evaluate_read_only_live_contact_guard,
    summarize_live_transport_result,
)
from tests.release.lab_controller.read_only_transport import (
    ReadOnlyTransportDecision,
    build_example_transport_query,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

_MODULE_PATH = Path(__file__).resolve().parent / "lab_controller" / "read_only_live_transport.py"
_CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release" / "run_lab_role_controller.py"
_PLANNER_PATH = Path(__file__).resolve().parent / "lab_controller" / "planner.py"


# --- helpers -------------------------------------------------------------------------------------


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        strings: list[str] = []
        for key, child in value.items():
            strings.extend(_flatten_strings(str(key)))
            strings.extend(_flatten_strings(child))
        return tuple(strings)
    if isinstance(value, (list, tuple, set)):
        strings = []
        for item in value:
            strings.extend(_flatten_strings(item))
        return tuple(strings)
    if isinstance(value, str):
        return (value,)
    return ()


def _summary_blob(summary: Mapping[str, Any]) -> str:
    return "\n".join(_flatten_strings(summary))


# Unsafe literals are assembled at runtime so no raw secret/URL/path token is committed.
_UNSAFE_URL = "http" + "s://" + "api.internal.example/healthz"
_UNSAFE_KUBECONFIG = "/home/" + "operator/.kube/config"
_UNSAFE_TOKEN = "bearer " + "0123456789abcdef"
_UNSAFE_PRIVATE_ID = "cluster" + "-id-" + "9f8e7d6c"
_UNSAFE_RELEASE_PATH = "." + "release" + "/run.json"


class _RecordingFakeClient:
    """A deterministic fake read-only live client. Records structured requests; never contacts."""

    def __init__(
        self,
        *,
        response: RawReadOnlyLiveResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.requests: list[ReadOnlyLiveClientRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def execute_read_query(self, request: ReadOnlyLiveClientRequest) -> RawReadOnlyLiveResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return RawReadOnlyLiveResponse(
            query_id=request.query_id,
            payload={
                "observed_identity_summary": "redacted-physical-identity-summary",
                "evidence_present": True,
                "signal_count": 2,
            },
        )


def _options(**kwargs: Any) -> ReadOnlyLiveTransportOptions:
    base: dict[str, Any] = {
        "allow_live_contact": True,
        "allow_read_only_queries": True,
        "timeout_seconds": 30.0,
    }
    base.update(kwargs)
    return ReadOnlyLiveTransportOptions(**base)


def _handle(**kwargs: Any) -> RuntimeOnlyLiveHubHandle:
    base: dict[str, Any] = {
        "physical_label": "primary",
        "kubeconfig_ref": "runtime-kubeconfig-handle",
        "context_ref": "runtime-context-handle",
    }
    base.update(kwargs)
    return RuntimeOnlyLiveHubHandle(**base)


def _context(
    *,
    options: ReadOnlyLiveTransportOptions | None = None,
    handle: RuntimeOnlyLiveHubHandle | None = None,
    gate_ids: tuple[Any, ...] | None = None,
) -> RuntimeOnlyLiveTransportContext:
    return RuntimeOnlyLiveTransportContext(
        handle=handle if handle is not None else _handle(),
        options=options if options is not None else _options(),
        gate_ids=gate_ids if gate_ids is not None else tuple(required_read_only_discovery_gate_ids()),
    )


def _query(**kwargs: Any):
    query = build_example_transport_query()
    if kwargs:
        query = replace(query, **kwargs)
    return query


def _transport(
    *,
    client: _RecordingFakeClient | None = None,
    context: RuntimeOnlyLiveTransportContext | None = None,
) -> ReadOnlyLiveTransport:
    return ReadOnlyLiveTransport(
        context=context if context is not None else _context(),
        client=client,
    )


@pytest.mark.parametrize(
    ("malformed_options", "expected_category"),
    [
        (None, ReadOnlyLiveTransportErrorCategory.NOT_OPTED_IN),
        (object(), ReadOnlyLiveTransportErrorCategory.NOT_OPTED_IN),
        (
            ReadOnlyLiveTransportOptions(
                allow_live_contact=True,
                allow_read_only_queries=True,
                total_deadline_seconds=float("inf"),
            ),
            ReadOnlyLiveTransportErrorCategory.INVALID_QUERY,
        ),
        (
            ReadOnlyLiveTransportOptions(
                allow_live_contact=True,
                allow_read_only_queries=True,
                page_size=501,
            ),
            ReadOnlyLiveTransportErrorCategory.INVALID_QUERY,
        ),
        (
            ReadOnlyLiveTransportOptions(
                allow_live_contact=True,
                allow_read_only_queries=True,
                timeout_seconds=10**10000,
            ),
            ReadOnlyLiveTransportErrorCategory.INVALID_QUERY,
        ),
        (
            ReadOnlyLiveTransportOptions(
                allow_live_contact=True,
                allow_read_only_queries=True,
                total_deadline_seconds=10**10000,
            ),
            ReadOnlyLiveTransportErrorCategory.INVALID_QUERY,
        ),
    ],
)
def test_malformed_or_unbounded_options_block_without_client_contact(
    malformed_options: Any,
    expected_category: ReadOnlyLiveTransportErrorCategory,
) -> None:
    client = _RecordingFakeClient()
    context = replace(_context(), options=malformed_options)

    result = _transport(client=client, context=context).execute(_query())

    assert result.status is ReadOnlyLiveTransportStatus.BLOCKED
    assert result.error_category is expected_category
    assert client.call_count == 0


# --- 1 / 28: non-live source guard ---------------------------------------------------------------


def test_module_imports_without_live_dependencies() -> None:
    assert ReadOnlyLiveTransportKind.LIVE_READ_ONLY.value == "live_read_only"
    assert ReadOnlyLiveTransportStatus.SUCCESS.value == "success"
    assert ReadOnlyLiveTransportErrorCategory.NONE.value == "none"
    assert ReadOnlyTransportDecision.INFRA_RETRYABLE.value == "INFRA_RETRYABLE"


def test_module_source_has_no_live_execution_or_transport_primitives() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    for forbidden_module in (
        "os",
        "subprocess",
        "socket",
        "yaml",
        "json",
        "kubernetes",
        "openshift",
        "requests",
        "urllib",
        "http",
    ):
        assert (
            forbidden_module not in imported_roots
        ), f"read_only_live_transport.py must not import {forbidden_module!r}"

    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    for forbidden_call in (
        "open",
        "system",
        "run",
        "Popen",
        "getenv",
        "check_output",
        "check_call",
        "call",
        "environ",
    ):
        assert forbidden_call not in called_names, f"read_only_live_transport.py must not call {forbidden_call!r}"


# --- 2 / 38: no default CLI/planner integration --------------------------------------------------


def test_cli_does_not_integrate_live_transport() -> None:
    cli_source = _CLI_PATH.read_text(encoding="utf-8")
    planner_source = _PLANNER_PATH.read_text(encoding="utf-8")
    assert "read_only_live_transport" not in cli_source
    assert "ReadOnlyLiveTransport" not in cli_source
    assert "read_only_live_transport" not in planner_source
    assert "ReadOnlyLiveTransport" not in planner_source

    assert lab_controller_cli.SUPPORTED_MODES == {"fake", "release-framework-dry-run", "release-framework-local"}
    assert "live" not in lab_controller_cli.SUPPORTED_MODES
    assert "read-only-live" not in lab_controller_cli.SUPPORTED_MODES


def test_transport_is_disabled_by_default() -> None:
    # Default options never opt in, so a default context blocks before any contact.
    default_options = ReadOnlyLiveTransportOptions()
    assert default_options.allow_live_contact is False
    assert default_options.allow_read_only_queries is False

    client = _RecordingFakeClient()
    transport = _transport(client=client, context=_context(options=default_options))
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.live_contact_attempted is False
    assert client.call_count == 0


# --- 3-7: opt-in / structural guards (no client call) --------------------------------------------


def test_blocks_when_allow_live_contact_false() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client, context=_context(options=_options(allow_live_contact=False)))
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.NOT_OPTED_IN
    assert result.live_contact_attempted is False
    assert client.call_count == 0


def test_blocks_when_allow_read_only_queries_false() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client, context=_context(options=_options(allow_read_only_queries=False)))
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.NOT_OPTED_IN
    assert client.call_count == 0


def test_blocks_when_client_missing() -> None:
    transport = _transport(client=None, context=_context())
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.MISSING_CLIENT
    assert result.live_contact_attempted is False


def test_blocks_when_client_does_not_implement_protocol() -> None:
    # A non-conforming object must fail closed in the guard, before any contact, and must not record
    # any live-contact attempt or real execution evidence.
    transport = ReadOnlyLiveTransport(context=_context(), client=object())  # type: ignore[arg-type]
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.MISSING_CLIENT
    assert result.live_contact_attempted is False
    assert result.real_execution_evidence is False
    assert transport.live_contact_attempts == 0


def test_blocks_when_client_method_not_callable() -> None:
    # A structural match whose execute_read_query is not callable must also fail closed before
    # contact, so no false real-execution evidence is recorded.
    class _NonCallableClient:
        execute_read_query = "not-callable"

    transport = ReadOnlyLiveTransport(context=_context(), client=_NonCallableClient())  # type: ignore[arg-type]
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.MISSING_CLIENT
    assert result.live_contact_attempted is False
    assert result.real_execution_evidence is False


def test_blocks_when_runtime_handle_missing() -> None:
    client = _RecordingFakeClient()
    bad_handle = _handle(kubeconfig_ref="", context_ref="")
    transport = _transport(client=client, context=_context(handle=bad_handle))
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.MISSING_HANDLE
    assert client.call_count == 0


def test_blocks_unsafe_runtime_handle_values_before_client_call() -> None:
    for bad_handle in (
        _handle(kubeconfig_ref=_UNSAFE_KUBECONFIG),
        _handle(context_ref=_UNSAFE_URL),
        _handle(credential_ref=_UNSAFE_TOKEN),
        _handle(client_ref=_UNSAFE_PRIVATE_ID),
    ):
        client = _RecordingFakeClient()
        transport = _transport(client=client, context=_context(handle=bad_handle))
        result = transport.execute(_query())

        assert result.decision is ReadOnlyTransportDecision.BLOCKED
        assert result.error_category is ReadOnlyLiveTransportErrorCategory.SAFETY_VIOLATION
        assert result.live_contact_attempted is False
        assert result.real_execution_evidence is False
        assert client.call_count == 0
        summary = summarize_live_transport_result(result)
        for unsafe in (_UNSAFE_KUBECONFIG, _UNSAFE_URL, _UNSAFE_TOKEN, _UNSAFE_PRIVATE_ID):
            assert unsafe not in _summary_blob(summary)


def test_blocks_when_gates_missing() -> None:
    client = _RecordingFakeClient()
    partial_gates = tuple(required_read_only_discovery_gate_ids())[:5]
    transport = _transport(client=client, context=_context(gate_ids=partial_gates))
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.MISSING_GATES
    assert client.call_count == 0


# --- 8-16: query guardrail blocks (no client call) ----------------------------------------------


def test_blocks_when_guardrail_result_not_pass() -> None:
    from tests.release.lab_controller.read_only_discovery import validate_read_only_query_plan

    # Build a genuinely blocked guardrail_result via a delete (mutating) plan, then attach it to an
    # otherwise-passing query so the guardrail_result field itself is what fails validation.
    blocked_plan = replace(build_example_transport_query().to_query_plan(), verb="delete")
    blocked_result = validate_read_only_query_plan(blocked_plan)
    query = replace(_query(), guardrail_result=blocked_result)

    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert client.call_count == 0


def test_blocks_disallowed_scenario() -> None:
    query = _query(scenario_id="python-passive-switchover")
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert client.call_count == 0


def test_blocks_disallowed_query_family() -> None:
    query = _query(query_family=ReadOnlyQueryFamily.LOGS_EVENTS, resource_family="logs_events")
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert client.call_count == 0


def test_blocks_mutating_verb() -> None:
    query = _query(verb="delete")
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.mutation_attempted is False
    assert client.call_count == 0


def test_blocks_mutation_enabled_true() -> None:
    query = _query(mutation_enabled=True)
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.mutation_attempted is False
    assert client.call_count == 0


def test_blocks_live_certification_evidence_true() -> None:
    query = _query(live_certification_evidence=True)
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert result.live_certification_evidence is False
    assert client.call_count == 0


def test_blocks_redaction_required_false() -> None:
    query = _query(redaction_required=False)
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert client.call_count == 0


def test_blocks_runtime_only_artifact_fields() -> None:
    query = _query(artifact_fields=("kubeconfig_ref", "decision"))
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert client.call_count == 0


def test_blocks_unsafe_query_value_before_client_call() -> None:
    query = _query(hub_label=_UNSAFE_URL)
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(query)

    assert result.decision is ReadOnlyTransportDecision.BLOCKED
    assert client.call_count == 0
    assert _UNSAFE_URL not in _summary_blob(summarize_live_transport_result(result))


# --- 17-21 / 35: success path -------------------------------------------------------------------


def test_success_calls_fake_client_exactly_once() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.PASS
    assert result.status is ReadOnlyLiveTransportStatus.SUCCESS
    assert client.call_count == 1


def test_success_sets_live_contact_flags_true() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.live_contact_attempted is True
    assert result.live_contact_succeeded is True


def test_success_keeps_mutation_attempted_false() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.mutation_attempted is False


def test_success_keeps_live_certification_evidence_false() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.live_certification_evidence is False


def test_success_summarizes_payload_without_raw_unsafe_values() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.artifact_safe_payload["evidence_present"] is True
    assert result.artifact_safe_payload["signal_count"] == 2
    summary = summarize_live_transport_result(result)
    blob = _summary_blob(summary)
    for unsafe in (_UNSAFE_URL, _UNSAFE_KUBECONFIG, _UNSAFE_TOKEN, _UNSAFE_PRIVATE_ID):
        assert unsafe not in blob


def test_real_execution_evidence_distinct_from_certification_evidence() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.real_execution_evidence is True
    assert result.live_contact_attempted is True
    assert result.live_certification_evidence is False
    assert result.mutation_attempted is False


# --- 22 / 31-34: unsafe payload rejection -------------------------------------------------------


def _success_with_payload(payload: Mapping[Any, Any]) -> _RecordingFakeClient:
    return _RecordingFakeClient(
        response=RawReadOnlyLiveResponse(query_id="phase8h-query", payload=payload),
    )


def test_rejects_unsafe_success_payload_as_no_go() -> None:
    client = _success_with_payload({"observed_endpoint": _UNSAFE_URL})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.status is ReadOnlyLiveTransportStatus.UNSAFE_PAYLOAD
    assert result.artifact_safe_payload == {}
    assert _UNSAFE_URL not in _summary_blob(summarize_live_transport_result(result))


def test_rejects_token_bearing_payload() -> None:
    client = _success_with_payload({"observed_auth": _UNSAFE_TOKEN})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert _UNSAFE_TOKEN not in _summary_blob(summarize_live_transport_result(result))


def test_rejects_forbidden_artifact_key_payload() -> None:
    client = _success_with_payload({"kubeconfig_ref": "present"})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.REDACTION_FAILURE


def test_rejects_private_cluster_id_payload() -> None:
    client = _success_with_payload({"observed_cluster": _UNSAFE_PRIVATE_ID})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert _UNSAFE_PRIVATE_ID not in _summary_blob(summarize_live_transport_result(result))


def test_rejects_release_path_payload() -> None:
    client = _success_with_payload({"observed_path": _UNSAFE_RELEASE_PATH})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO


def test_rejects_broad_payload_dump() -> None:
    broad = {f"field_{index}": f"value-{index}" for index in range(60)}
    client = _success_with_payload(broad)
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.UNSAFE_PAYLOAD


def test_rejects_non_string_scalar_payload_value() -> None:
    # A real client (e.g. k8s) can return bytes (Secret/ConfigMap binary data). Bytes bypass the
    # str-only redaction checks, so the transport must reject unexpected value types fail-closed.
    unsafe_bytes = (_UNSAFE_TOKEN + " " + _UNSAFE_URL).encode("utf-8")
    client = _success_with_payload({"observed_blob": unsafe_bytes})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.artifact_safe_payload == {}
    blob = _summary_blob(summarize_live_transport_result(result))
    assert _UNSAFE_TOKEN not in blob
    assert _UNSAFE_URL not in blob


def test_rejects_object_valued_payload() -> None:
    class _Leaky:
        def __repr__(self) -> str:
            return _UNSAFE_TOKEN

    client = _success_with_payload({"observed_obj": _Leaky()})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert _UNSAFE_TOKEN not in _summary_blob(summarize_live_transport_result(result))


def test_rejects_large_binary_payload() -> None:
    client = _success_with_payload({"blob": b"x" * 100_000})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.artifact_safe_payload == {}


def test_rejects_non_string_mapping_key() -> None:
    client = _success_with_payload({("tuple", "key"): "value"})
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO


def test_rejects_mismatched_response_query_id() -> None:
    # A buggy client returning a response for a different request must fail closed, so a foreign
    # payload is never attributed to this query.
    client = _RecordingFakeClient(
        response=RawReadOnlyLiveResponse(query_id="some-other-query", payload={"evidence_present": True}),
    )
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.SAFETY_VIOLATION
    assert result.artifact_safe_payload == {}


# --- 23-26: error classification ----------------------------------------------------------------


def test_sanitizes_unsafe_exception_messages() -> None:
    client = _RecordingFakeClient(error=ReadOnlyLivePermanentError(_UNSAFE_TOKEN))
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.live_contact_attempted is True
    blob = _summary_blob(summarize_live_transport_result(result))
    assert _UNSAFE_TOKEN not in blob


def test_transient_error_returns_infra_retryable() -> None:
    client = _RecordingFakeClient(error=ReadOnlyLiveTransientError("temporary read failure"))
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.INFRA_RETRYABLE
    assert result.retryable is True
    assert result.mutation_attempted is False
    assert result.live_contact_attempted is True


def test_permanent_error_returns_no_go() -> None:
    client = _RecordingFakeClient(error=ReadOnlyLivePermanentError("forbidden"))
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.PERMANENT_FAILURE


def test_safety_error_returns_no_go() -> None:
    client = _RecordingFakeClient(error=ReadOnlyLiveSafetyError("unsafe observation"))
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.error_category is ReadOnlyLiveTransportErrorCategory.SAFETY_VIOLATION


def test_retryable_timeout_returns_infra_retryable() -> None:
    client = _RecordingFakeClient(error=ReadOnlyLiveTimeoutError("read timed out", retryable=True))
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.INFRA_RETRYABLE
    assert result.timeout is True
    assert result.retryable is True
    assert result.mutation_attempted is False


def test_non_retryable_timeout_returns_no_go() -> None:
    client = _RecordingFakeClient(error=ReadOnlyLiveTimeoutError("read timed out", retryable=False))
    transport = _transport(client=client)
    result = transport.execute(_query())

    assert result.decision is ReadOnlyTransportDecision.NO_GO
    assert result.timeout is True
    assert result.retryable is False


# --- 27: structured query contract --------------------------------------------------------------


def test_fake_client_receives_structured_query_not_shell_string() -> None:
    client = _RecordingFakeClient()
    transport = _transport(client=client)
    transport.execute(_query())

    assert client.call_count == 1
    request = client.requests[0]
    assert isinstance(request, ReadOnlyLiveClientRequest)
    assert not isinstance(request, str)
    assert request.verb == "get"
    for value in (request.query_family, request.verb, request.resource_family, request.hub_label):
        assert "|" not in str(value)
        assert ";" not in str(value)
        assert "&&" not in str(value)


# --- 29-30: runtime-only handle redaction -------------------------------------------------------


def test_kubeconfig_like_handle_values_never_appear_in_summaries() -> None:
    handle = _handle(kubeconfig_ref=_UNSAFE_KUBECONFIG, context_ref="runtime-context-handle")
    summary = handle.to_artifact_safe_summary()

    assert summary["cluster_access_handle_present"] is True
    assert summary["context_handle_present"] is True
    assert _UNSAFE_KUBECONFIG not in _summary_blob(summary)


def test_api_url_handle_value_never_appears_in_summaries() -> None:
    context = _context(handle=_handle(context_ref=_UNSAFE_URL))
    summary = context.to_artifact_safe_summary()

    assert _UNSAFE_URL not in _summary_blob(summary)


def test_runtime_handle_summary_contains_only_presence_booleans() -> None:
    handle = _handle(credential_ref="runtime-credential-handle", client_ref="runtime-client-handle")
    summary = handle.to_artifact_safe_summary()

    assert summary["auth_handle_present"] is True
    assert summary["client_handle_present"] is True
    assert summary["runtime_values_redacted"] is True
    assert "runtime-credential-handle" not in _summary_blob(summary)


# --- 36-37: behavioral alignment with prior phases ----------------------------------------------


def test_phase8h_query_contract_aligns_with_live_guard() -> None:
    from tests.release.lab_controller.read_only_transport import validate_transport_query

    query = _query()
    assert validate_transport_query(query).decision is ReadOnlyTransportDecision.PASS

    guard = evaluate_read_only_live_contact_guard(_context(), _RecordingFakeClient(), query)
    assert isinstance(guard, ReadOnlyLiveContactGuardDecision)
    assert guard.decision is ReadOnlyTransportDecision.PASS


def test_phase8g_unimplemented_backend_still_blocks() -> None:
    from tests.release.lab_controller.read_only_backend import ReadOnlyDiscoveryRequest

    backend = UnimplementedReadOnlyDiscoveryBackend()
    # A minimal malformed request is enough to confirm fail-closed behavior remains BLOCKED.
    result = backend.run_discovery(object())  # type: ignore[arg-type]
    assert result.decision is ReadOnlyBackendDecision.BLOCKED
    assert ReadOnlyDiscoveryRequest is not None


# --- 39-40: opt-in and no-mutation invariants ---------------------------------------------------


def test_execute_is_no_op_contact_when_not_opted_in_even_with_client() -> None:
    client = _RecordingFakeClient()
    transport = _transport(
        client=client,
        context=_context(options=_options(allow_live_contact=False, allow_read_only_queries=False)),
    )
    result = transport.execute(_query())

    assert result.live_contact_attempted is False
    assert result.live_contact_succeeded is False
    assert client.call_count == 0


def test_no_result_path_sets_mutation_or_certification_true() -> None:
    success_client = _RecordingFakeClient()
    transient_client = _RecordingFakeClient(error=ReadOnlyLiveTransientError("temporary"))
    permanent_client = _RecordingFakeClient(error=ReadOnlyLivePermanentError("forbidden"))
    unsafe_client = _success_with_payload({"observed_endpoint": _UNSAFE_URL})

    for client in (success_client, transient_client, permanent_client, unsafe_client):
        result = _transport(client=client).execute(_query())
        assert result.mutation_attempted is False
        assert result.live_certification_evidence is False

    blocked = _transport(client=None).execute(_query())
    assert blocked.mutation_attempted is False
    assert blocked.live_certification_evidence is False


def test_no_mutating_verb_is_executable_through_transport() -> None:
    client = _RecordingFakeClient()
    for verb in (
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
    ):
        result = _transport(client=client).execute(_query(verb=verb))
        assert result.decision is ReadOnlyTransportDecision.BLOCKED
        assert result.error_category is ReadOnlyLiveTransportErrorCategory.POLICY_BLOCKED
        assert result.first_blocking_reason == "query failed the Phase 8E/8H read-only guardrails before contact"
    assert client.call_count == 0


# --- artifact-safety of summaries ---------------------------------------------------------------


def test_blocked_and_success_summaries_are_artifact_safe() -> None:
    from tests.release.lab_controller.artifacts import validate_artifact_payload_redacted

    blocked = _transport(client=None).execute(_query())
    success = _transport(client=_RecordingFakeClient()).execute(_query())

    for result in (blocked, success):
        summary = summarize_live_transport_result(result)
        assert summary["mutation_attempted"] is False
        assert summary["live_certification_evidence"] is False
        assert summary["discovery_mode"] == "read_only"
        assert summary["transport_kind"] == ReadOnlyLiveTransportKind.LIVE_READ_ONLY.value
        validate_artifact_payload_redacted(summary)


def test_example_builders_are_safe_and_opt_in_aware() -> None:
    handle = build_example_runtime_handle()
    context = build_example_transport_context()

    assert isinstance(handle, RuntimeOnlyLiveHubHandle)
    assert isinstance(context, RuntimeOnlyLiveTransportContext)
    # Examples are opt-in by default so they can drive deterministic tests without real clusters.
    assert context.options.allow_live_contact is True
    assert context.options.allow_read_only_queries is True
    # No real kubeconfig path or URL is embedded in the example handle.
    blob = _summary_blob(context.to_artifact_safe_summary())
    assert "/home/" not in blob
    assert "://" not in blob


def test_result_dataclass_defaults_are_safe() -> None:
    result = ReadOnlyLiveTransportResult(
        query_id="q",
        scenario_id="preflight",
        status=ReadOnlyLiveTransportStatus.BLOCKED,
        decision=ReadOnlyTransportDecision.BLOCKED,
        response_summary="blocked",
        error_category=ReadOnlyLiveTransportErrorCategory.NOT_OPTED_IN,
    )

    assert result.mutation_attempted is False
    assert result.live_certification_evidence is False
    assert result.live_contact_attempted is False
    assert result.real_execution_evidence is False


def test_result_forces_mutation_and_certification_false_even_if_requested() -> None:
    # Defense in depth: a Phase 8J result can never claim mutation or live certification evidence.
    result = ReadOnlyLiveTransportResult(
        query_id="q",
        scenario_id="preflight",
        status=ReadOnlyLiveTransportStatus.SUCCESS,
        decision=ReadOnlyTransportDecision.PASS,
        response_summary="ok",
        mutation_attempted=True,
        live_certification_evidence=True,
    )

    assert result.mutation_attempted is False
    assert result.live_certification_evidence is False


def test_result_clears_contact_success_and_execution_evidence_when_no_contact_occurred() -> None:
    result = ReadOnlyLiveTransportResult(
        query_id="q",
        scenario_id="preflight",
        status=ReadOnlyLiveTransportStatus.BLOCKED,
        decision=ReadOnlyTransportDecision.BLOCKED,
        response_summary="blocked",
        live_contact_attempted=False,
        live_contact_succeeded=True,
        real_execution_evidence=True,
    )

    assert result.live_contact_attempted is False
    assert result.live_contact_succeeded is False
    assert result.real_execution_evidence is False


def test_scenario_ids_used_by_examples_come_from_catalog() -> None:
    context = build_example_transport_context()
    query = build_example_transport_query()
    assert query.scenario_id in SCENARIOS_BY_ID
    assert context.handle.physical_label != ""
