from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts.release import run_lab_role_controller as lab_controller_cli
from tests.release.lab_controller import read_only_preflight_pilot as pilot_module
from tests.release.lab_controller.live_config import LiveGateId
from tests.release.lab_controller.read_only_discovery import ReadOnlyQueryFamily, required_read_only_discovery_gate_ids
from tests.release.lab_controller.read_only_preflight_pilot import (
    Phase8LPilotDecision,
    Phase8LPilotMode,
    Phase8LPilotResult,
    PilotInput,
    build_pilot_query_package,
    run_preflight_pilot_rehearsal,
    validate_pilot_artifact_payload,
    write_pilot_artifact,
)
from tests.release.lab_controller.read_only_transport import (
    FakeReadOnlyTransport,
    FakeTransportFixture,
    ReadOnlyTransportDecision,
    ReadOnlyTransportErrorCategory,
    ReadOnlyTransportKind,
    ReadOnlyTransportQuery,
    ReadOnlyTransportResponse,
    ReadOnlyTransportStatus,
)

_MODULE_PATH = Path(__file__).resolve().parent / "lab_controller" / "read_only_preflight_pilot.py"
_CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release" / "run_lab_role_controller.py"
_PLANNER_PATH = Path(__file__).resolve().parent / "lab_controller" / "planner.py"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_DOC_PATH = _REPO_ROOT / "docs" / "development" / "lab-role-controller-agent-instructions.md"
_FORBIDDEN_TRUE_ARTIFACT_FLAGS = {
    "live_contact_attempted",
    "live_contact_succeeded",
    "real_execution_evidence",
    "live_certification_evidence",
    "mutation_enabled",
    "mutation_attempted",
}


class _FaultyFakeTransport(FakeReadOnlyTransport):
    """Fake-only test double that simulates an impossible live-evidence flag bug."""

    def __init__(self) -> None:
        super().__init__(())
        self._calls = 0

    @property
    def call_count(self) -> int:
        return self._calls

    def execute(self, query: ReadOnlyTransportQuery) -> ReadOnlyTransportResponse:
        self._calls += 1
        return ReadOnlyTransportResponse(
            query_id=query.query_id,
            scenario_id=query.scenario_id,
            status=ReadOnlyTransportStatus.SUCCESS,
            decision=ReadOnlyTransportDecision.PASS,
            response_summary="faulty fake response claimed live contact",
            live_contact_attempted=True,
            live_contact_succeeded=True,
        )


class _UnsafePayloadFakeTransport(FakeReadOnlyTransport):
    """Fake-only test double that bypasses normal fixture payload validation."""

    def __init__(self) -> None:
        super().__init__(())
        self._calls = 0

    @property
    def call_count(self) -> int:
        return self._calls

    def execute(self, query: ReadOnlyTransportQuery) -> ReadOnlyTransportResponse:
        self._calls += 1
        return ReadOnlyTransportResponse(
            query_id=query.query_id,
            scenario_id=query.scenario_id,
            status=ReadOnlyTransportStatus.SUCCESS,
            decision=ReadOnlyTransportDecision.PASS,
            response_summary="faulty fake response leaked forbidden payload evidence",
            artifact_safe_payload={"Live_Contact_Attempted": True},
        )


class _NonFakeTransport:
    """Transport-shaped object that must never be executed by Phase 8L."""

    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, query: ReadOnlyTransportQuery) -> ReadOnlyTransportResponse:
        self.call_count += 1
        raise AssertionError("non-fake transport must not be executed")


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


def _summary_text(summary: Mapping[str, Any]) -> str:
    return "\n".join(_flatten_strings(summary)).lower()


def _artifact_has_forbidden_true_flag(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_TRUE_ARTIFACT_FLAGS and child is not False:
                return True
            if _artifact_has_forbidden_true_flag(child):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_artifact_has_forbidden_true_flag(item) for item in value)
    return False


def _gate_status() -> dict[str, str]:
    return {gate.value: "satisfied" for gate in required_read_only_discovery_gate_ids()}


def _runtime_summary(**overrides: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "runtime_values_redacted": True,
        "physical_labels": ["hub-a", "hub-b"],
        "cluster_access_handle_present": True,
        "context_handle_present": True,
        "auth_handle_present": True,
        "handle_fingerprint_count": 2,
    }
    summary.update(overrides)
    return summary


def _pilot_input(**overrides: Any) -> PilotInput:
    base: dict[str, Any] = {
        "approval_reference": "approval-ref-redacted",
        "expected_branch": "ansible",
        "expected_commit": "0123456789abcdef0123456789abcdef01234567",
        "clean_worktree": True,
        "expected_physical_hub_labels": ("hub-a", "hub-b"),
        "expected_role_labels": ("primary", "secondary"),
        "expected_managed_cluster_set": ("mc-1", "mc-2", "mc-3"),
        "runtime_handle_summary": _runtime_summary(),
        "artifact_directory_ref": "caller-provided-temp-artifact-dir",
        "scenario_allowlist": ("preflight",),
        "query_family_allowlist": (ReadOnlyQueryFamily.CLUSTER_IDENTITY,),
        "opt_in_flags": {
            "operator_approved_phase8l_rehearsal": True,
            "read_only_scope": True,
            "fake_or_dry_run_only": True,
        },
        "timeout_seconds": 30,
        "retry_budget": 1,
        "redaction_policy_version": "phase8l-redaction-policy",
        "pilot_mode": Phase8LPilotMode.FAKE_BACKED_REHEARSAL,
        "precontact_gate_status": _gate_status(),
    }
    base.update(overrides)
    return PilotInput(**base)


def _query_ids(inputs: PilotInput) -> tuple[str, ...]:
    package_result = build_pilot_query_package(inputs)
    assert package_result.decision is Phase8LPilotDecision.PASS
    assert package_result.query_package is not None
    return tuple(query.query_id for query in package_result.query_package.queries)


def _queries(inputs: PilotInput) -> tuple[ReadOnlyTransportQuery, ...]:
    package_result = build_pilot_query_package(inputs)
    assert package_result.decision is Phase8LPilotDecision.PASS
    assert package_result.query_package is not None
    return package_result.query_package.queries


def _success_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "identity_status": "matched",
        "managed_cluster_state": "exact",
        "logical_role_state": "proven",
        "read_prerequisite_state": "represented",
        "redaction_status": "redacted",
    }
    payload.update(overrides)
    return payload


def _success_response(query: ReadOnlyTransportQuery, **overrides: Any) -> ReadOnlyTransportResponse:
    payload = overrides.pop("artifact_safe_payload", _success_payload())
    query_id = overrides.pop("query_id", query.query_id)
    scenario_id = overrides.pop("scenario_id", query.scenario_id)
    return ReadOnlyTransportResponse(
        query_id=query_id,
        scenario_id=scenario_id,
        status=ReadOnlyTransportStatus.SUCCESS,
        decision=ReadOnlyTransportDecision.PASS,
        response_summary="fake transport returned a read-only result",
        artifact_safe_payload=payload,
        **overrides,
    )


def _run_with_collected_responses(
    monkeypatch: pytest.MonkeyPatch,
    inputs: PilotInput,
    responses: tuple[ReadOnlyTransportResponse, ...],
) -> Phase8LPilotResult:
    def fake_collect(
        transport: FakeReadOnlyTransport,
        queries: tuple[ReadOnlyTransportQuery, ...],
    ) -> tuple[ReadOnlyTransportResponse, ...]:
        return responses

    monkeypatch.setattr(pilot_module, "collect_fake_transport_evidence", fake_collect)
    return _run(inputs, fake_transport=FakeReadOnlyTransport(()))


def _fake_transport_for(inputs: PilotInput, fixture: FakeTransportFixture) -> FakeReadOnlyTransport:
    query_count = len(_query_ids(inputs))
    return FakeReadOnlyTransport(
        tuple(replace(fixture, query_id=query_id) for query_id in _query_ids(inputs)[:query_count])
    )


def _single_fixture(inputs: PilotInput, **kwargs: Any) -> FakeReadOnlyTransport:
    return FakeReadOnlyTransport(
        tuple(FakeTransportFixture(query_id=query_id, **kwargs) for query_id in _query_ids(inputs))
    )


def _fixture_transport_with_overrides(
    inputs: PilotInput,
    overrides_by_index: Mapping[int, Mapping[str, Any]],
) -> FakeReadOnlyTransport:
    fixtures = []
    for index, query_id in enumerate(_query_ids(inputs)):
        overrides = dict(overrides_by_index.get(index, {}))
        status = overrides.pop("status", ReadOnlyTransportStatus.SUCCESS)
        payload = overrides.pop("payload", _success_payload())
        fixtures.append(FakeTransportFixture(query_id=query_id, status=status, payload=payload, **overrides))
    return FakeReadOnlyTransport(tuple(fixtures))


def _run(inputs: PilotInput, *, fake_transport: FakeReadOnlyTransport | None = None) -> Phase8LPilotResult:
    return run_preflight_pilot_rehearsal(inputs, fake_transport=fake_transport)


# --- 1-2: import/source/default integration ------------------------------------------------------


def test_module_imports_without_live_risk_imports_or_calls() -> None:
    assert Phase8LPilotMode.DRY_RUN_NO_CONTACT.value == "dry_run_no_contact"
    assert Phase8LPilotMode.FAKE_BACKED_REHEARSAL.value == "fake_backed_rehearsal"
    assert Phase8LPilotDecision.INFRA_RETRYABLE.value == "INFRA_RETRYABLE"

    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    for forbidden_module in (
        "os",
        "subprocess",
        "socket",
        "yaml",
        "kubernetes",
        "openshift",
        "requests",
        "urllib",
        "http",
    ):
        assert forbidden_module not in imported_roots

    for forbidden_call in ("open", "system", "run", "Popen", "getenv", "check_output", "check_call", "call"):
        assert forbidden_call not in called_names


def test_cli_and_planner_defaults_remain_non_live_and_do_not_import_phase8l() -> None:
    cli_source = _CLI_PATH.read_text(encoding="utf-8")
    planner_source = _PLANNER_PATH.read_text(encoding="utf-8")

    assert "read_only_preflight_pilot" not in cli_source
    assert "Phase8LPilot" not in cli_source
    assert "read_only_preflight_pilot" not in planner_source
    assert "Phase8LPilot" not in planner_source
    assert lab_controller_cli.SUPPORTED_MODES == {"fake", "release-framework-dry-run", "release-framework-local"}
    assert "live" not in lab_controller_cli.SUPPORTED_MODES


# --- 3-6: explicit pilot modes -------------------------------------------------------------------


def test_dry_run_no_contact_never_calls_fake_transport() -> None:
    inputs = _pilot_input(pilot_mode=Phase8LPilotMode.DRY_RUN_NO_CONTACT)
    query_id = _query_ids(inputs)[0]
    transport = FakeReadOnlyTransport(
        (FakeTransportFixture(query_id=query_id, status=ReadOnlyTransportStatus.SUCCESS),)
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.PASS
    assert transport.call_count == 0
    assert result.artifact_summary["simulated_contact_attempted"] is False
    assert result.artifact_summary["live_contact_attempted"] is False


def test_fake_backed_rehearsal_uses_fake_transport_only() -> None:
    inputs = _pilot_input()
    query_ids = _query_ids(inputs)
    transport = _single_fixture(
        inputs,
        status=ReadOnlyTransportStatus.SUCCESS,
        payload={
            "identity_status": "matched",
            "managed_cluster_state": "exact",
            "logical_role_state": "proven",
            "read_prerequisite_state": "represented",
            "redaction_status": "redacted",
        },
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.PASS
    assert transport.call_count == len(query_ids)
    assert {summary["hub_label"] for summary in transport.received_query_summaries()} == set(
        inputs.expected_physical_hub_labels
    )
    assert result.artifact_summary["simulated_contact_attempted"] is True
    assert result.artifact_summary["simulated_contact_succeeded"] is True
    assert result.artifact_summary["rehearsal_contact_simulated"] is True
    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["real_execution_evidence"] is False


def test_fake_backed_rehearsal_rejects_non_fake_transport_without_execution() -> None:
    transport = _NonFakeTransport()

    result = _run(_pilot_input(), fake_transport=transport)  # type: ignore[arg-type]

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_transport" in result.blocking_fields
    assert transport.call_count == 0
    assert result.artifact_summary["live_contact_attempted"] is False


def test_live_read_only_mode_is_unsupported_in_phase8l() -> None:
    result = _run(_pilot_input(pilot_mode=Phase8LPilotMode.LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "unsupported" in (result.first_blocking_reason or "").lower()
    assert result.artifact_summary["live_contact_attempted"] is False


def test_unknown_mode_blocks() -> None:
    result = _run(_pilot_input(pilot_mode="surprise-live-mode"))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "pilot_mode" in result.blocking_fields


def test_non_pilot_input_blocks_without_exception() -> None:
    result = run_preflight_pilot_rehearsal(object())  # type: ignore[arg-type]

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "input" in result.blocking_fields
    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["mutation_attempted"] is False


# --- 7-28: input, gate, and query guardrails ------------------------------------------------------


@pytest.mark.parametrize(
    ("field_name", "value", "blocking_field"),
    [
        ("approval_reference", "", "approval_reference"),
        ("expected_branch", "", "expected_branch"),
        ("expected_commit", "", "expected_commit"),
        ("runtime_handle_summary", {}, "runtime_handle_summary"),
        ("artifact_directory_ref", "", "artifact_directory_ref"),
        ("expected_managed_cluster_set", (), "expected_managed_cluster_set"),
        ("scenario_allowlist", (), "scenario_allowlist"),
        ("query_family_allowlist", (), "query_family_allowlist"),
        ("redaction_policy_version", "", "redaction_policy_version"),
    ],
)
def test_missing_required_input_blocks(field_name: str, value: object, blocking_field: str) -> None:
    result = _run(_pilot_input(**{field_name: value}))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert blocking_field in result.blocking_fields


@pytest.mark.parametrize(
    ("field_name", "value", "blocking_field"),
    [
        ("approval_reference", None, "approval_reference"),
        ("expected_branch", object(), "expected_branch"),
        ("expected_commit", 123, "expected_commit"),
        ("artifact_directory_ref", None, "artifact_directory_ref"),
        ("artifact_directory_ref", 123, "artifact_directory_ref"),
        ("redaction_policy_version", None, "redaction_policy_version"),
    ],
)
def test_malformed_text_inputs_block_without_exception_and_emit_safe_artifact(
    field_name: str, value: object, blocking_field: str
) -> None:
    result = _run(_pilot_input(**{field_name: value}))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert blocking_field in result.blocking_fields
    assert result.artifact_summary["decision"] == Phase8LPilotDecision.BLOCKED.value
    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["real_execution_evidence"] is False
    assert result.artifact_summary["mutation_enabled"] is False


@pytest.mark.parametrize(
    ("field_name", "value", "blocking_field"),
    [
        ("expected_physical_hub_labels", None, "expected_physical_hub_labels"),
        ("expected_physical_hub_labels", "hub-a", "expected_physical_hub_labels"),
        ("expected_managed_cluster_set", None, "expected_managed_cluster_set"),
        ("expected_managed_cluster_set", "mc-1", "expected_managed_cluster_set"),
        ("deferred_query_families", None, "deferred_query_families"),
        ("deferred_query_families", "argocd_status", "deferred_query_families"),
    ],
)
def test_malformed_sequence_inputs_block_without_exception(field_name: str, value: object, blocking_field: str) -> None:
    result = _run(_pilot_input(**{field_name: value}))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert blocking_field in result.blocking_fields


@pytest.mark.parametrize(
    ("field_name", "value", "blocking_field"),
    [
        ("scenario_allowlist", None, "scenario_allowlist"),
        ("scenario_allowlist", "preflight", "scenario_allowlist"),
        ("scenario_allowlist", (["preflight"],), "scenario_allowlist"),
        ("query_family_allowlist", None, "query_family_allowlist"),
        ("query_family_allowlist", "cluster_identity", "query_family_allowlist"),
        ("opt_in_flags", None, "opt_in_flags"),
        ("opt_in_flags", (("operator_approved_phase8l_rehearsal", True),), "opt_in_flags"),
        ("precontact_gate_status", None, "precontact_gate_status"),
        ("precontact_gate_status", (("L0", "satisfied"),), "precontact_gate_status"),
    ],
)
def test_malformed_container_inputs_block_without_exception_and_emit_safe_artifact(
    field_name: str, value: object, blocking_field: str
) -> None:
    result = _run(_pilot_input(**{field_name: value}))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert blocking_field in result.blocking_fields
    assert result.artifact_summary["decision"] == Phase8LPilotDecision.BLOCKED.value
    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["real_execution_evidence"] is False
    assert result.artifact_summary["mutation_enabled"] is False


@pytest.mark.parametrize(
    "opt_in_flags",
    [
        {"operator_approved_phase8l_rehearsal": True},
        {
            "operator_approved_phase8l_rehearsal": True,
            "read_only_scope": False,
            "fake_or_dry_run_only": True,
        },
        {"unrelated": True},
    ],
)
def test_required_opt_in_flags_must_be_present_and_true(opt_in_flags: Mapping[str, bool]) -> None:
    result = _run(_pilot_input(opt_in_flags=opt_in_flags))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "opt_in_flags" in result.blocking_fields


def test_dirty_worktree_blocks_unless_explicitly_allowed_for_dry_run_policy() -> None:
    blocked = _run(_pilot_input(clean_worktree=False))
    allowed = _run(
        _pilot_input(
            pilot_mode=Phase8LPilotMode.DRY_RUN_NO_CONTACT,
            clean_worktree=False,
            allow_dirty_worktree_for_dry_run=True,
        )
    )

    assert blocked.decision is Phase8LPilotDecision.BLOCKED
    assert "clean_worktree" in blocked.blocking_fields
    assert allowed.decision is Phase8LPilotDecision.PASS


@pytest.mark.parametrize(
    "unsafe_summary",
    [
        _runtime_summary(raw_context_ref="runtime-context-handle"),
        _runtime_summary(cluster_access_handle_present="/home/operator/.kube/config"),
        _runtime_summary(auth_handle_present="token=abcdef"),
        _runtime_summary(api_url="https://api.example.invalid:6443"),
    ],
)
def test_unsafe_runtime_handle_summary_blocks(unsafe_summary: Mapping[str, Any]) -> None:
    result = _run(_pilot_input(runtime_handle_summary=unsafe_summary))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "runtime_handle_summary" in result.blocking_fields


def test_release_artifact_ref_blocks() -> None:
    result = _run(_pilot_input(artifact_directory_ref=".release"))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "artifact_directory_ref" in result.blocking_fields


def test_mutating_scenario_blocks() -> None:
    result = _run(_pilot_input(scenario_allowlist=("python-passive-switchover",)))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "scenario_allowlist" in result.blocking_fields


@pytest.mark.parametrize(
    "family",
    [
        ReadOnlyQueryFamily.MUTATION_CAPABLE,
        ReadOnlyQueryFamily.ARBITRARY_SHELL,
        ReadOnlyQueryFamily.SECRET_BEARING_RESOURCES,
        ReadOnlyQueryFamily.LOGS_EVENTS,
        ReadOnlyQueryFamily.ARGOCD_STATUS,
        ReadOnlyQueryFamily.SUBJECT_ACCESS_REVIEW,
    ],
)
def test_forbidden_conditional_or_deferred_query_family_blocks(family: ReadOnlyQueryFamily) -> None:
    result = _run(_pilot_input(query_family_allowlist=(family,)))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "query_family_allowlist" in result.blocking_fields


def test_conditional_query_family_can_be_deferred_without_execution() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.ARGOCD_STATUS),
        deferred_query_families=(ReadOnlyQueryFamily.ARGOCD_STATUS,),
    )

    package_result = build_pilot_query_package(inputs)
    result = _run(inputs)

    assert package_result.decision is Phase8LPilotDecision.PASS
    assert package_result.query_package is not None
    assert package_result.query_package.deferred_query_families == ("argocd_status",)
    assert all(
        query.query_family is not ReadOnlyQueryFamily.ARGOCD_STATUS for query in package_result.query_package.queries
    )
    assert result.decision is Phase8LPilotDecision.PASS


@pytest.mark.parametrize(
    ("deferred_families", "blocking_field"),
    [
        (("not-a-family",), "deferred_query_families"),
        ((ReadOnlyQueryFamily.CLUSTER_IDENTITY,), "deferred_query_families"),
        ((ReadOnlyQueryFamily.ARGOCD_STATUS,), "deferred_query_families"),
    ],
)
def test_deferred_query_families_must_be_known_deferrable_and_allowlisted(
    deferred_families: tuple[ReadOnlyQueryFamily | str, ...],
    blocking_field: str,
) -> None:
    result = _run(_pilot_input(deferred_query_families=deferred_families))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert blocking_field in result.blocking_fields


@pytest.mark.parametrize(
    "verb",
    [
        "delete",
        "patch",
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
    ],
)
def test_mutating_verb_blocks(verb: str) -> None:
    result = _run(_pilot_input(query_verbs={"cluster_identity": verb}))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "query_verbs" in result.blocking_fields


def test_malformed_query_verbs_block_without_exception() -> None:
    result = _run(_pilot_input(query_verbs=None))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "query_verbs" in result.blocking_fields


def test_unknown_query_verb_override_key_blocks() -> None:
    result = _run(_pilot_input(query_verbs={"managed_cluster_stats": "get"}))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "query_verbs" in result.blocking_fields


def test_missing_l0_l9_gate_blocks() -> None:
    gates = _gate_status()
    gates.pop(LiveGateId.L9.value)

    result = _run(_pilot_input(precontact_gate_status=gates))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "precontact_gate_status" in result.blocking_fields
    assert result.artifact_summary["gate_status"]["phase8e_guardrails"] == "BLOCKED"


def test_failed_l0_l9_gate_blocks() -> None:
    gates = _gate_status()
    gates[LiveGateId.L4.value] = "blocked"

    result = _run(_pilot_input(precontact_gate_status=gates))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "precontact_gate_status" in result.blocking_fields


def test_unknown_l0_l9_gate_blocks() -> None:
    gates = _gate_status()
    gates["L99"] = "satisfied"

    result = _run(_pilot_input(precontact_gate_status=gates))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "precontact_gate_status" in result.blocking_fields


def test_unsafe_l0_l9_gate_status_blocks_without_artifact_exception() -> None:
    gates = _gate_status()
    gates[LiveGateId.L4.value] = "https://api.example.invalid:6443"

    result = _run(_pilot_input(precontact_gate_status=gates))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "precontact_gate_status" in result.blocking_fields
    assert "https://" not in _summary_text(result.artifact_summary)


def test_case_insensitive_satisfied_l0_l9_values_keep_gate_artifact_pass() -> None:
    gates = {gate: "Satisfied" for gate in _gate_status()}

    result = _run(_pilot_input(precontact_gate_status=gates))

    assert result.decision is Phase8LPilotDecision.PASS
    assert result.artifact_summary["gate_status"]["pre_contact"] == "PASS"


def test_l10_cannot_authorize_mutation() -> None:
    result = _run(_pilot_input(l10_authorizes_mutation=True))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "l10_authorizes_mutation" in result.blocking_fields


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("live_certification_evidence", True),
        ("mutation_enabled", True),
        ("redaction_required", False),
    ],
)
def test_safety_flags_block_when_not_false_false_true(field_name: str, value: object) -> None:
    result = _run(_pilot_input(**{field_name: value}))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert field_name in result.blocking_fields


def test_query_package_is_structured_and_has_no_shell_strings() -> None:
    inputs = _pilot_input(
        scenario_allowlist=("lab-readiness", "preflight"),
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS),
    )

    package_result = build_pilot_query_package(inputs)

    assert package_result.decision is Phase8LPilotDecision.PASS
    assert package_result.query_package is not None
    assert all(isinstance(query, ReadOnlyTransportQuery) for query in package_result.query_package.queries)
    assert "kubectl" not in _summary_text(package_result.query_package.to_artifact_safe_summary())
    assert "raw_command" not in _summary_text(package_result.query_package.to_artifact_safe_summary())


# --- 30-43: fake-backed and dry-run decisions ----------------------------------------------------


def test_fake_backed_success_keeps_all_live_and_mutation_evidence_false() -> None:
    result = _run(_pilot_input())
    artifact = result.artifact_summary

    assert result.decision is Phase8LPilotDecision.PASS
    assert artifact["simulated_contact_attempted"] is True
    assert artifact["simulated_contact_succeeded"] is True
    assert artifact["live_contact_attempted"] is False
    assert artifact["live_contact_succeeded"] is False
    assert artifact["real_execution_evidence"] is False
    assert artifact["live_certification_evidence"] is False
    assert artifact["mutation_enabled"] is False
    assert artifact["mutation_attempted"] is False


def test_dry_run_success_has_no_fake_or_live_execution() -> None:
    result = _run(_pilot_input(pilot_mode=Phase8LPilotMode.DRY_RUN_NO_CONTACT))

    assert result.decision is Phase8LPilotDecision.PASS
    assert result.artifact_summary["query_result_summary"]["executed_query_count"] == 0
    assert result.artifact_summary["simulated_contact_attempted"] is False
    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["real_execution_evidence"] is False
    assert result.artifact_summary["physical_identity_evidence"]["status"] == "not_proven"
    assert result.artifact_summary["physical_identity_evidence"]["proven"] is False
    assert result.artifact_summary["logical_role_evidence"]["status"] == "not_proven"
    assert result.artifact_summary["logical_role_evidence"]["proven"] is False
    assert result.artifact_summary["managed_cluster_set_evidence"]["status"] == "not_proven"
    assert result.artifact_summary["managed_cluster_set_evidence"]["exact_match"] is False
    assert result.artifact_summary["read_prerequisite_evidence"]["status"] == "not_proven"
    assert result.artifact_summary["read_prerequisite_evidence"]["proven"] is False


def test_fake_retryable_timeout_maps_infra_retryable() -> None:
    inputs = _pilot_input()
    transport = _single_fixture(
        inputs,
        status=ReadOnlyTransportStatus.TIMEOUT,
        error_category=ReadOnlyTransportErrorCategory.TIMEOUT,
        timeout=True,
        retryable=True,
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.INFRA_RETRYABLE
    assert result.retry_allowed is True
    assert result.artifact_summary["mutation_attempted"] is False


def test_fake_permanent_failure_maps_no_go() -> None:
    inputs = _pilot_input()
    transport = _single_fixture(
        inputs,
        status=ReadOnlyTransportStatus.FAILED,
        error_category=ReadOnlyTransportErrorCategory.TRANSPORT_FAILURE,
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO


def test_fake_policy_block_maps_blocked() -> None:
    inputs = _pilot_input()
    transport = _single_fixture(
        inputs,
        status=ReadOnlyTransportStatus.BLOCKED,
        error_category=ReadOnlyTransportErrorCategory.POLICY_BLOCKED,
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.BLOCKED


@pytest.mark.parametrize("count_case", ["missing", "extra"])
def test_fake_response_count_mismatch_blocks_before_pass(
    monkeypatch: pytest.MonkeyPatch,
    count_case: str,
) -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    queries = _queries(inputs)
    responses = tuple(_success_response(query) for query in queries)
    if count_case == "missing":
        collected = responses[:-1]
    else:
        collected = responses + (_success_response(queries[-1], query_id="unexpected-extra-query"),)

    result = _run_with_collected_responses(monkeypatch, inputs, collected)

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_count" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False


def test_fake_response_order_mismatch_blocks_before_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    queries = _queries(inputs)
    responses = tuple(_success_response(query) for query in reversed(queries))

    result = _run_with_collected_responses(monkeypatch, inputs, responses)

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_order" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False


def test_fake_response_duplicate_query_id_blocks_before_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    queries = _queries(inputs)
    responses = (_success_response(queries[0]), _success_response(queries[0]))

    result = _run_with_collected_responses(monkeypatch, inputs, responses)

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_query_id" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False


def test_fake_response_query_id_mismatch_blocks_before_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _pilot_input()
    query = _queries(inputs)[0]
    response = _success_response(query, query_id="unexpected-query-id")

    result = _run_with_collected_responses(monkeypatch, inputs, (response,))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_query_id" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False


def test_fake_response_scenario_id_mismatch_blocks_before_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _pilot_input()
    query = _queries(inputs)[0]
    response = _success_response(query, scenario_id="lab-readiness")

    result = _run_with_collected_responses(monkeypatch, inputs, (response,))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_scenario_id" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False


def test_fake_response_transport_kind_not_fake_blocks_before_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _pilot_input()
    query = _queries(inputs)[0]
    response = _success_response(query, transport_kind=ReadOnlyTransportKind.LIVE_UNSUPPORTED)

    result = _run_with_collected_responses(monkeypatch, inputs, (response,))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_transport_kind" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False


def test_fake_response_no_live_contact_false_blocks_before_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _pilot_input()
    query = _queries(inputs)[0]
    response = _success_response(query, no_live_contact=False)

    result = _run_with_collected_responses(monkeypatch, inputs, (response,))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_no_live_contact" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False


@pytest.mark.parametrize(
    "flag_name",
    [
        "live_contact_attempted",
        "live_contact_succeeded",
        "real_execution_evidence",
        "live_certification_evidence",
        "mutation_enabled",
        "mutation_attempted",
    ],
)
def test_fake_response_live_certification_and_mutation_evidence_flags_block_before_pass(
    monkeypatch: pytest.MonkeyPatch,
    flag_name: str,
) -> None:
    inputs = _pilot_input()
    response = _success_response(_queries(inputs)[0])
    object.__setattr__(response, flag_name, True)

    result = _run_with_collected_responses(monkeypatch, inputs, (response,))

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "fake_response_evidence_flags" in result.blocking_fields
    assert result.artifact_summary["safe_to_continue"] is False
    assert not _artifact_has_forbidden_true_flag(result.artifact_summary)


def test_unsafe_fake_success_payload_maps_no_go() -> None:
    inputs = _pilot_input()
    transport = _single_fixture(
        inputs,
        status=ReadOnlyTransportStatus.UNSAFE_PAYLOAD,
        error_category=ReadOnlyTransportErrorCategory.UNSAFE_PAYLOAD,
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.artifact_summary["redaction_status"] == "rejected"


@pytest.mark.parametrize(
    ("payload", "expected_decision"),
    [
        ({"redaction_status": "rejected"}, Phase8LPilotDecision.NO_GO),
        ({"identity_status": "mismatch"}, Phase8LPilotDecision.NO_GO),
        ({"managed_cluster_state": "drift"}, Phase8LPilotDecision.NO_GO),
        ({"logical_role_state": "both_hubs_active"}, Phase8LPilotDecision.NO_GO),
        ({"logical_role_state": "neither_hub_active"}, Phase8LPilotDecision.RECOVERY_REQUIRED),
        ({"logical_role_state": "ambiguous"}, Phase8LPilotDecision.RECOVERY_REQUIRED),
    ],
)
def test_fake_payload_evidence_maps_conservatively(
    payload: Mapping[str, Any],
    expected_decision: Phase8LPilotDecision,
) -> None:
    inputs = _pilot_input()
    transport = _single_fixture(inputs, status=ReadOnlyTransportStatus.SUCCESS, payload=payload)

    result = _run(inputs, fake_transport=transport)

    assert result.decision is expected_decision
    if expected_decision is Phase8LPilotDecision.RECOVERY_REQUIRED:
        assert result.manual_recovery_required is True


def test_later_managed_cluster_drift_payload_dominates_earlier_safe_payload() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    transport = _fixture_transport_with_overrides(
        inputs, {1: {"payload": _success_payload(managed_cluster_state="drift")}}
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.artifact_summary["managed_cluster_set_evidence"]["status"] == "drift"
    assert result.artifact_summary["managed_cluster_set_evidence"]["exact_match"] is False


def test_later_identity_mismatch_payload_dominates_earlier_safe_payload() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    transport = _fixture_transport_with_overrides(
        inputs, {1: {"payload": _success_payload(identity_status="mismatch")}}
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.artifact_summary["physical_identity_evidence"]["status"] == "mismatch"
    assert result.artifact_summary["physical_identity_evidence"]["proven"] is False


def test_retryable_evidence_does_not_downgrade_no_go_payload_evidence() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    transport = _fixture_transport_with_overrides(
        inputs,
        {
            0: {
                "status": ReadOnlyTransportStatus.TIMEOUT,
                "error_category": ReadOnlyTransportErrorCategory.TIMEOUT,
                "timeout": True,
                "retryable": True,
            },
            1: {"payload": _success_payload(managed_cluster_state="drift")},
        },
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.retry_allowed is False
    assert result.manual_recovery_required is False


def test_retryable_evidence_does_not_override_recovery_required_payload_evidence() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    transport = _fixture_transport_with_overrides(
        inputs,
        {
            0: {
                "status": ReadOnlyTransportStatus.TIMEOUT,
                "error_category": ReadOnlyTransportErrorCategory.TIMEOUT,
                "timeout": True,
                "retryable": True,
            },
            1: {"payload": {"logical_role_state": "neither_hub_active"}},
        },
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.RECOVERY_REQUIRED
    assert result.retry_allowed is False
    assert result.manual_recovery_required is True


def test_multiple_payloads_do_not_synthesize_pass_from_favorable_first_payload_only() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    query_ids = _query_ids(inputs)
    transport = FakeReadOnlyTransport(
        (
            FakeTransportFixture(
                query_id=query_ids[0],
                status=ReadOnlyTransportStatus.SUCCESS,
                payload=_success_payload(),
            ),
            FakeTransportFixture(
                query_id=query_ids[1],
                status=ReadOnlyTransportStatus.SUCCESS,
                payload={"identity_status": "matched", "redaction_status": "redacted"},
            ),
        )
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert result.artifact_summary["safe_to_continue"] is False
    assert result.artifact_summary["physical_identity_evidence"]["proven"] is False


def test_fake_response_no_go_evidence_takes_precedence_over_retryable_or_blocked_results() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    transport = _fixture_transport_with_overrides(
        inputs,
        {
            0: {
                "status": ReadOnlyTransportStatus.TIMEOUT,
                "error_category": ReadOnlyTransportErrorCategory.TIMEOUT,
                "timeout": True,
                "retryable": True,
            },
            1: {
                "status": ReadOnlyTransportStatus.UNSAFE_PAYLOAD,
                "error_category": ReadOnlyTransportErrorCategory.UNSAFE_PAYLOAD,
            },
        },
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.retry_allowed is False


@pytest.mark.parametrize(
    "non_terminal_fixture",
    [
        FakeTransportFixture(
            query_id="placeholder",
            status=ReadOnlyTransportStatus.TIMEOUT,
            error_category=ReadOnlyTransportErrorCategory.TIMEOUT,
            timeout=True,
            retryable=True,
        ),
        FakeTransportFixture(
            query_id="placeholder",
            status=ReadOnlyTransportStatus.BLOCKED,
            error_category=ReadOnlyTransportErrorCategory.POLICY_BLOCKED,
        ),
    ],
)
def test_fake_payload_no_go_evidence_takes_precedence_over_retryable_or_blocked_results(
    non_terminal_fixture: FakeTransportFixture,
) -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    transport = _fixture_transport_with_overrides(
        inputs,
        {
            0: {
                "status": non_terminal_fixture.status,
                "error_category": non_terminal_fixture.error_category,
                "timeout": non_terminal_fixture.timeout,
                "retryable": non_terminal_fixture.retryable,
            },
            1: {"payload": {"identity_status": "mismatch"}},
        },
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.retry_allowed is False
    assert result.manual_recovery_required is False


def test_faulty_fake_live_contact_flags_do_not_reach_artifact() -> None:
    result = _run(_pilot_input(), fake_transport=_FaultyFakeTransport())

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["live_contact_succeeded"] is False
    assert not _artifact_has_forbidden_true_flag(result.artifact_summary)


def test_faulty_fake_unsafe_payload_maps_no_go_without_artifact_leak() -> None:
    result = _run(_pilot_input(), fake_transport=_UnsafePayloadFakeTransport())

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.artifact_summary["redaction_status"] == "rejected"
    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["real_execution_evidence"] is False
    assert not _artifact_has_forbidden_true_flag(result.artifact_summary)
    assert "unsafe_payload_removed" in _summary_text(result.artifact_summary)


def test_fake_payload_no_go_evidence_takes_precedence_over_recovery_required_evidence() -> None:
    inputs = _pilot_input(
        query_family_allowlist=(ReadOnlyQueryFamily.CLUSTER_IDENTITY, ReadOnlyQueryFamily.MANAGED_CLUSTER_STATUS)
    )
    transport = _fixture_transport_with_overrides(
        inputs,
        {
            0: {"payload": {"logical_role_state": "neither_hub_active"}},
            1: {"payload": {"identity_status": "mismatch"}},
        },
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.manual_recovery_required is False


def test_fake_success_without_required_positive_evidence_blocks() -> None:
    inputs = _pilot_input()
    transport = _single_fixture(
        inputs,
        status=ReadOnlyTransportStatus.SUCCESS,
        payload={"identity_status": "matched"},
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert "required evidence" in (result.first_blocking_reason or "")


# --- 44-51 / 55: artifact contract, writing, docs, recommendation -------------------------------


def test_artifact_summary_includes_required_fields_and_next_safe_phase() -> None:
    result = _run(_pilot_input())
    artifact = result.artifact_summary

    for field in (
        "artifact_version",
        "phase",
        "mode",
        "branch",
        "commit",
        "clean_worktree",
        "approval_reference_redacted",
        "scenario_ids",
        "query_family_allowlist",
        "gate_status",
        "opt_in_flags",
        "runtime_handle_summary",
        "artifact_directory_ref_redacted",
        "live_contact_attempted",
        "live_contact_succeeded",
        "real_execution_evidence",
        "simulated_contact_attempted",
        "simulated_contact_succeeded",
        "live_certification_evidence",
        "mutation_enabled",
        "mutation_attempted",
        "query_plan_summary",
        "query_result_summary",
        "physical_identity_evidence",
        "logical_role_evidence",
        "managed_cluster_set_evidence",
        "read_prerequisite_evidence",
        "redaction_status",
        "decision",
        "retry_allowed",
        "manual_recovery_required",
        "first_blocking_reason",
        "next_phase_recommendation",
    ):
        assert field in artifact

    assert artifact["artifact_version"].startswith("provisional.phase8l")
    assert artifact["phase"] == "8L"
    assert artifact["next_phase_recommendation"] == "READY_FOR_PHASE_8M_READ_ONLY_LIVE_PREFLIGHT_PILOT_APPROVAL_PACKAGE"
    assert "broad" not in str(artifact["next_phase_recommendation"]).lower()


@pytest.mark.parametrize(
    "unsafe_payload",
    [
        {"evidence": "https://api.example.invalid:6443"},
        {"evidence": "token=abcdef"},
        {"evidence": "cluster-id-private"},
        {"runtime": "/home/operator/.kube/config"},
        {"runtime_handle_summary": {"kubeconfig_ref": "runtime-kubeconfig-handle"}},
        {"context_ref": "runtime-context-handle"},
        {"credential_ref": "runtime-credential-handle"},
        {"artifact_directory_ref_redacted": ".release"},
    ],
)
def test_artifact_summary_rejects_unsafe_values_and_raw_runtime_refs(unsafe_payload: Mapping[str, Any]) -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)
    payload.update(unsafe_payload)

    validation = validate_pilot_artifact_payload(payload)

    assert validation.decision is Phase8LPilotDecision.NO_GO


def test_artifact_validation_rejects_nested_live_or_mutation_evidence_claim() -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)
    payload["query_result_summary"] = {
        **dict(payload["query_result_summary"]),
        "responses": [{"live_contact_attempted": True}],
    }

    validation = validate_pilot_artifact_payload(payload)

    assert validation.decision is Phase8LPilotDecision.NO_GO


def test_artifact_validation_blocks_non_mapping_payload_without_exception() -> None:
    validation = validate_pilot_artifact_payload(["not", "a", "mapping"])  # type: ignore[arg-type]

    assert validation.decision is Phase8LPilotDecision.BLOCKED
    assert validation.first_blocking_reason == "artifact payload must be a mapping"


def test_artifact_validation_blocks_live_unsupported_mode_with_pass_decision() -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)
    payload["mode"] = Phase8LPilotMode.LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L.value
    payload["decision"] = Phase8LPilotDecision.PASS.value

    validation = validate_pilot_artifact_payload(payload)

    assert validation.decision is Phase8LPilotDecision.BLOCKED
    assert "mode" in validation.blocking_fields
    assert "decision" in validation.blocking_fields


def test_artifact_validation_blocks_unknown_mode_with_pass_decision() -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)
    payload["mode"] = "surprise-live-mode"
    payload["decision"] = Phase8LPilotDecision.PASS.value

    validation = validate_pilot_artifact_payload(payload)

    assert validation.decision is Phase8LPilotDecision.BLOCKED
    assert "mode" in validation.blocking_fields


@pytest.mark.parametrize("mode", [" dry_run_no_contact ", 123])
def test_artifact_validation_blocks_malformed_or_non_string_mode(mode: Any) -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)
    payload["mode"] = mode

    validation = validate_pilot_artifact_payload(payload)

    assert validation.decision is Phase8LPilotDecision.BLOCKED
    assert "mode" in validation.blocking_fields


def test_artifact_validation_blocks_unsupported_decision() -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)
    payload["decision"] = "GO"

    validation = validate_pilot_artifact_payload(payload)

    assert validation.decision is Phase8LPilotDecision.BLOCKED
    assert "decision" in validation.blocking_fields


def test_artifact_validation_allows_valid_dry_run_no_contact_pass() -> None:
    payload = dict(_run(_pilot_input(pilot_mode=Phase8LPilotMode.DRY_RUN_NO_CONTACT)).artifact_summary)

    validation = validate_pilot_artifact_payload(payload)

    assert payload["mode"] == Phase8LPilotMode.DRY_RUN_NO_CONTACT.value
    assert payload["decision"] == Phase8LPilotDecision.PASS.value
    assert validation.decision is Phase8LPilotDecision.PASS


def test_artifact_validation_allows_valid_fake_backed_rehearsal_pass() -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)

    validation = validate_pilot_artifact_payload(payload)

    assert payload["mode"] == Phase8LPilotMode.FAKE_BACKED_REHEARSAL.value
    assert payload["decision"] == Phase8LPilotDecision.PASS.value
    assert validation.decision is Phase8LPilotDecision.PASS


def test_artifact_validation_allows_live_unsupported_mode_with_blocked_decision() -> None:
    payload = dict(
        _run(_pilot_input(pilot_mode=Phase8LPilotMode.LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L)).artifact_summary
    )

    validation = validate_pilot_artifact_payload(payload)

    assert payload["mode"] == Phase8LPilotMode.LIVE_READ_ONLY_UNSUPPORTED_IN_PHASE_8L.value
    assert payload["decision"] == Phase8LPilotDecision.BLOCKED.value
    assert validation.decision is Phase8LPilotDecision.PASS


def test_artifact_validation_rejects_case_variant_nested_live_evidence_claim() -> None:
    payload = dict(_run(_pilot_input()).artifact_summary)
    payload["query_result_summary"] = {
        **dict(payload["query_result_summary"]),
        "responses": [{"Live_Contact_Attempted": True}],
    }

    validation = validate_pilot_artifact_payload(payload)

    assert validation.decision is Phase8LPilotDecision.NO_GO


def test_nested_evidence_claim_is_scrubbed_from_rejected_artifact() -> None:
    result = _run(_pilot_input(runtime_handle_summary=_runtime_summary(live_contact_attempted=True)))

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.artifact_summary["redaction_status"] == "rejected"
    assert not _artifact_has_forbidden_true_flag(result.artifact_summary)


def test_case_variant_nested_evidence_claim_is_scrubbed_from_rejected_artifact() -> None:
    result = _run(_pilot_input(runtime_handle_summary=_runtime_summary(Live_Contact_Attempted=True)))

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.artifact_summary["redaction_status"] == "rejected"
    assert not _artifact_has_forbidden_true_flag(result.artifact_summary)


def test_nested_evidence_claim_is_scrubbed_from_blocked_artifact() -> None:
    result = _run(
        _pilot_input(
            approval_reference="",
            runtime_handle_summary=_runtime_summary(live_contact_attempted=True),
        )
    )

    assert result.decision is Phase8LPilotDecision.BLOCKED
    assert result.artifact_summary["redaction_status"] == "rejected"
    assert not _artifact_has_forbidden_true_flag(result.artifact_summary)


def test_rejected_retryable_artifact_clears_retry_and_maps_no_go() -> None:
    inputs = _pilot_input(runtime_handle_summary=_runtime_summary(live_contact_attempted=True))
    transport = _single_fixture(
        inputs,
        status=ReadOnlyTransportStatus.TIMEOUT,
        error_category=ReadOnlyTransportErrorCategory.TIMEOUT,
        timeout=True,
        retryable=True,
    )

    result = _run(inputs, fake_transport=transport)

    assert result.decision is Phase8LPilotDecision.NO_GO
    assert result.retry_allowed is False
    assert result.artifact_summary["retry_allowed"] is False
    assert result.artifact_summary["redaction_status"] == "rejected"
    assert not _artifact_has_forbidden_true_flag(result.artifact_summary)


def test_optional_artifact_writing_uses_explicit_tmp_path_only(tmp_path: Path) -> None:
    result = _run(_pilot_input())

    write_result = write_pilot_artifact(result, tmp_path)

    assert write_result.decision is Phase8LPilotDecision.PASS
    assert write_result.path is not None
    payload = json.loads(write_result.path.read_text(encoding="utf-8"))
    assert payload["phase"] == "8L"
    assert payload["live_contact_attempted"] is False
    assert ".release" not in _summary_text(payload)


def test_artifact_write_failure_is_classified_conservatively(tmp_path: Path) -> None:
    result = _run(_pilot_input())
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("already a file", encoding="utf-8")

    write_result = write_pilot_artifact(result, not_a_directory)

    assert write_result.decision is Phase8LPilotDecision.NO_GO
    assert write_result.path is None


def test_artifact_write_serialization_failure_is_classified_conservatively(tmp_path: Path) -> None:
    result = _run(_pilot_input())
    unsafe_result = replace(result, artifact_summary={**dict(result.artifact_summary), "non_json_value": object()})

    write_result = write_pilot_artifact(unsafe_result, tmp_path)

    assert write_result.decision is Phase8LPilotDecision.NO_GO
    assert write_result.path is None
    assert write_result.first_blocking_reason is not None
    assert "artifact write failed" in write_result.first_blocking_reason


def test_artifact_writer_rejects_release_path_without_writing() -> None:
    result = _run(_pilot_input())

    write_result = write_pilot_artifact(result, Path(".release"))

    assert write_result.decision is Phase8LPilotDecision.BLOCKED
    assert write_result.path is None


def test_artifact_writer_rejects_release_marker_path_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(_pilot_input())
    write_attempted = False

    def fail_on_write(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        nonlocal write_attempted
        write_attempted = True
        raise OSError("write should not be attempted for release marker paths")

    monkeypatch.setattr(Path, "write_text", fail_on_write)

    write_result = write_pilot_artifact(result, tmp_path / "foo.release" / "artifacts")

    assert write_result.decision is Phase8LPilotDecision.BLOCKED
    assert write_result.path is None
    assert write_attempted is False


def test_artifact_writer_rejects_symlink_resolving_to_release_path_before_mkdir(tmp_path: Path) -> None:
    result = _run(_pilot_input())
    release_target = tmp_path / ".release"
    release_target.mkdir()
    safe_link = tmp_path / "safe-link"
    safe_link.symlink_to(release_target, target_is_directory=True)

    write_result = write_pilot_artifact(result, safe_link / "artifacts")

    assert write_result.decision is Phase8LPilotDecision.BLOCKED
    assert write_result.path is None
    assert not (release_target / "artifacts").exists()


def test_artifact_writer_rejects_existing_symlink_filename_without_overwrite(tmp_path: Path) -> None:
    result = _run(_pilot_input())
    target = tmp_path / "target.json"
    target.write_text("existing target", encoding="utf-8")
    artifact_path = tmp_path / "phase8l-read-only-preflight-pilot-rehearsal.json"
    artifact_path.symlink_to(target)

    write_result = write_pilot_artifact(result, tmp_path)

    assert write_result.decision is Phase8LPilotDecision.NO_GO
    assert write_result.path is None
    assert target.read_text(encoding="utf-8") == "existing target"


def test_artifact_writer_rejects_repo_committed_paths_without_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(_pilot_input())
    write_attempted = False

    def fail_on_write(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        nonlocal write_attempted
        write_attempted = True
        raise OSError("write should not be attempted for repo paths")

    monkeypatch.setattr(Path, "write_text", fail_on_write)

    write_result = write_pilot_artifact(result, _REPO_ROOT)

    assert write_result.decision is Phase8LPilotDecision.BLOCKED
    assert write_result.path is None
    assert write_attempted is False


@pytest.mark.parametrize("filename", ["../escape.json", "/tmp/escape.json", "nested/escape.json", "token=unsafe.json"])
def test_artifact_writer_rejects_unsafe_filenames(tmp_path: Path, filename: str) -> None:
    result = _run(_pilot_input())

    write_result = write_pilot_artifact(result, tmp_path, filename=filename)

    assert write_result.decision is Phase8LPilotDecision.BLOCKED
    assert write_result.path is None


def test_artifact_writer_rechecks_resolved_symlink_directory(tmp_path: Path) -> None:
    result = _run(_pilot_input())
    unsafe_target = tmp_path / ".release"
    unsafe_target.mkdir()
    link = tmp_path / "safe-link"
    link.symlink_to(unsafe_target, target_is_directory=True)

    write_result = write_pilot_artifact(result, link)

    assert write_result.decision is Phase8LPilotDecision.BLOCKED
    assert write_result.path is None
    assert not (unsafe_target / "phase8l-read-only-preflight-pilot-rehearsal.json").exists()


def test_no_production_schema_finalization() -> None:
    artifact = _run(_pilot_input()).artifact_summary

    assert artifact["artifact_version"].startswith("provisional")
    assert artifact.get("production_json_schema_finalized") is not True


def test_agent_instructions_remain_non_live() -> None:
    content = _AGENT_DOC_PATH.read_text(encoding="utf-8")

    assert "Agent live behavior" not in content
    assert "live mode is supported" not in content
    heading = "The Agent must not:"
    assert heading in content
    must_not_section = content.split(heading, 1)[1]
    assert "- Claim `live_certification_evidence=true`." in must_not_section


def test_normal_suite_boundary_remains_non_live() -> None:
    result = _run(_pilot_input())

    assert result.artifact_summary["live_contact_attempted"] is False
    assert result.artifact_summary["live_certification_evidence"] is False
    assert result.artifact_summary["mutation_attempted"] is False
    assert result.artifact_summary["mode"] == Phase8LPilotMode.FAKE_BACKED_REHEARSAL.value
    assert "release-framework-live" not in lab_controller_cli.SUPPORTED_MODES
