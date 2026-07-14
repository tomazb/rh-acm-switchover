"""Property-based tests for checkpoint and resume safety semantics."""

from __future__ import annotations

import copy
import importlib
import json
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    KNOWN_PHASES,
    SCHEMA_VERSION,
    CheckpointIdentityMismatch,
    build_checkpoint_record,
    build_operation_identity,
    is_unsafe_legacy_checkpoint,
    normalize_operation_identity,
    reset_completed_phases_from,
    should_resume_phase,
    validate_operation_identity,
)
from lib.utils import (
    Phase,
    StateIdentityMismatch,
    StateManager,
)
from tests.properties.strategies import (
    IDENTITY_MISMATCH_FIELDS,
    completed_phase_lists,
    context_pair_cases,
    hub_identity_cases,
    json_native_values,
    legacy_operation_identities,
    mismatched_operation_identities,
    normalized_operation_identities,
    operation_identity_cases,
    readable_step_names,
    semantic_checkpoints,
    state_manager_operation_sequences,
)


def _load_action_helpers():
    """Import the action helper without requiring ansible-core in root test jobs."""
    module_name = "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase"
    try:
        module = importlib.import_module(module_name)
        return module.ActionModule, module.build_phase_transition
    except ModuleNotFoundError as exc:
        if exc.name not in {"ansible", "ansible.plugins", "ansible.plugins.action"}:
            raise

    ansible_module = types.ModuleType("ansible")
    plugins_module = types.ModuleType("ansible.plugins")
    action_module = types.ModuleType("ansible.plugins.action")

    class _ActionBase:  # pragma: no cover - used only when ansible-core is absent.
        pass

    action_module.ActionBase = _ActionBase
    ansible_module.plugins = plugins_module
    plugins_module.action = action_module
    sys.modules.setdefault("ansible", ansible_module)
    sys.modules.setdefault("ansible.plugins", plugins_module)
    sys.modules.setdefault("ansible.plugins.action", action_module)
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    return module.ActionModule, module.build_phase_transition


ActionModule, build_phase_transition = _load_action_helpers()

# Hypothesis reuses pytest's function-scoped tmp_path across examples.  Every
# example below creates a unique child directory, so no durable state leaks
# between examples; suppressing only this fixture warning is therefore safe.
STATE_MANAGER_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


def _contains_value(candidate: object, expected: object) -> bool:
    """Return whether a nested JSON-like value contains ``expected``."""
    if candidate == expected:
        return True
    if isinstance(candidate, dict):
        return any(
            _contains_value(key, expected) or _contains_value(value, expected) for key, value in candidate.items()
        )
    if isinstance(candidate, list):
        return any(_contains_value(value, expected) for value in candidate)
    return False


def _fresh_state_path(tmp_path: Path) -> Path:
    """Return a unique state path beneath pytest's managed temporary root."""
    return Path(tempfile.mkdtemp(prefix="checkpoint-property-", dir=tmp_path)) / "state.json"


@pytest.mark.property
@given(operation_identity_cases())
def test_build_operation_identity_sanitizes_paths_and_preserves_semantics(case) -> None:
    """Built identities retain semantic fields without retaining kubeconfig material."""
    hubs_before = copy.deepcopy(case.hubs)
    operation_before = copy.deepcopy(case.operation)

    identity = build_operation_identity(
        hubs=case.hubs,
        operation=case.operation,
        collection_version=case.collection_version,
        hub_identities=case.hub_identities,
    )

    assert not (
        {"primary_kubeconfig", "secondary_kubeconfig", "kubeconfig"} & identity.keys()
    ), f"identity retained a kubeconfig key for {case!r}: {identity!r}"
    for canary in case.kubeconfig_canaries:
        assert not _contains_value(identity, canary), f"identity retained kubeconfig canary {canary!r}: {identity!r}"
    assert (
        identity == case.expected_identity
    ), f"identity defaults or semantic fields drifted for {case!r}: {identity!r}"
    assert (
        case.hubs == hubs_before
    ), f"build_operation_identity mutated hubs: before={hubs_before!r}, after={case.hubs!r}"
    assert (
        case.operation == operation_before
    ), f"build_operation_identity mutated operation: before={operation_before!r}, after={case.operation!r}"


@pytest.mark.property
@given(legacy_operation_identities())
def test_normalize_operation_identity_is_exact_idempotent_and_non_mutating(case) -> None:
    """Normalization removes only the two legacy kubeconfig fields."""
    before = copy.deepcopy(case.identity)

    normalized = normalize_operation_identity(case.identity)

    assert normalized == case.normalized, f"normalization changed retained fields for {case!r}: {normalized!r}"
    assert set(case.identity) - set(normalized) == {
        "primary_kubeconfig",
        "secondary_kubeconfig",
    }, f"normalization removed an unexpected field for {case!r}: {normalized!r}"
    assert case.identity == before, f"normalization mutated its input: before={before!r}, after={case.identity!r}"
    assert normalize_operation_identity(normalized) == normalized, f"normalization was not idempotent: {case!r}"


@pytest.mark.property
@given(legacy_operation_identities())
def test_legacy_kubeconfig_differences_do_not_break_identity_validation(case) -> None:
    """Equivalent normalized identities validate despite different legacy paths."""
    expected = {
        **case.normalized,
        "primary_kubeconfig": case.kubeconfig_canaries[0] + "-current",
        "secondary_kubeconfig": case.kubeconfig_canaries[1] + "-current",
    }
    checkpoint = {"operation_identity": case.identity}

    assert (
        validate_operation_identity(checkpoint, expected) is True
    ), f"normalized-equivalent identities did not validate: checkpoint={checkpoint!r}, expected={expected!r}"


@pytest.mark.property
@pytest.mark.parametrize("field", IDENTITY_MISMATCH_FIELDS)
@given(data=st.data())
def test_each_normalized_identity_field_mismatch_fails_closed(field: str, data: st.DataObject) -> None:
    """Every one-field normalized mismatch raises without mutating inputs."""
    case = data.draw(mismatched_operation_identities(field), label=field)
    checkpoint_before = copy.deepcopy(case.checkpoint)
    expected_before = copy.deepcopy(case.expected_identity)

    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity(case.checkpoint, case.expected_identity)

    assert case.checkpoint == checkpoint_before, f"mismatch validation mutated checkpoint for {field}: {case!r}"
    assert (
        case.expected_identity == expected_before
    ), f"mismatch validation mutated expected identity for {field}: {case!r}"


@pytest.mark.property
@given(normalized_operation_identities())
def test_missing_operation_identity_is_fail_closed_unless_explicitly_allowed(expected_identity: dict) -> None:
    """Missing identity raises by default and returns False only for allow_missing."""
    checkpoint: dict = {}

    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity(checkpoint, expected_identity)
    assert validate_operation_identity(checkpoint, expected_identity, allow_missing=True) is False
    assert checkpoint == {}, f"missing-identity validation manufactured identity data: {checkpoint!r}"


@pytest.mark.property
@given(completed_phase_lists(duplicates=True), st.sampled_from(KNOWN_PHASES))
def test_reset_completed_phases_from_matches_independent_order_model(completed: list[str], phase: str) -> None:
    """Reset removes the requested/downstream phases and preserves stable upstream entries."""
    before = list(completed)
    reset_index = tuple(KNOWN_PHASES).index(phase)
    downstream = set(tuple(KNOWN_PHASES)[reset_index:])
    expected = [candidate for candidate in completed if candidate not in downstream]

    result = reset_completed_phases_from(completed, phase)

    assert (
        result == expected
    ), f"ordered reset drifted for phase={phase!r}, completed={completed!r}: expected={expected!r}, result={result!r}"
    assert completed == before, f"ordered reset mutated input list: before={before!r}, after={completed!r}"


@pytest.mark.property
@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz_-", min_size=1, max_size=24).map(lambda value: "unknown-" + value))
def test_reset_completed_phases_from_rejects_unknown_phase(unknown_phase: str) -> None:
    """Unknown reset boundaries always fail explicitly."""
    with pytest.raises(ValueError):
        reset_completed_phases_from(list(KNOWN_PHASES), unknown_phase)


@pytest.mark.property
@given(semantic_checkpoints(), st.sampled_from(KNOWN_PHASES))
def test_should_resume_phase_is_exact_completed_membership(checkpoint: dict, phase: str) -> None:
    """Resume runs exactly the phases absent from completed progress."""
    expected = phase not in checkpoint["completed_phases"]
    assert (
        should_resume_phase(checkpoint, phase) is expected
    ), f"resume membership drifted for phase={phase!r}, checkpoint={checkpoint!r}"


@pytest.mark.property
@given(completed_phase_lists(duplicates=True), st.sampled_from(KNOWN_PHASES))
def test_should_resume_phase_ignores_duplicate_completion_entries(completed: list[str], phase: str) -> None:
    """Duplicate progress entries do not change set-like resume membership."""
    checkpoint = {"completed_phases": completed}

    assert should_resume_phase(checkpoint, phase) is (phase not in set(completed))


@pytest.mark.property
@given(st.sampled_from(KNOWN_PHASES))
def test_should_resume_phase_defaults_missing_completed_phases_to_empty(phase: str) -> None:
    """An absent completed list means no phase is skipped."""
    assert should_resume_phase({}, phase) is True


@pytest.mark.property
@given(st.sampled_from(("1.0", "2.0", "3.0")), completed_phase_lists())
@example("1.0", [])
@example("1.0", [KNOWN_PHASES[0]])
@example("2.0", [])
@example("2.0", [KNOWN_PHASES[0]])
def test_unsafe_legacy_classification_has_narrow_exact_contract(schema_version: str, completed: list[str]) -> None:
    """Only schema 1.0 checkpoints with progress classify as unsafe legacy state."""
    checkpoint = {"schema_version": schema_version, "completed_phases": completed}
    expected = schema_version == "1.0" and bool(completed)
    assert (
        is_unsafe_legacy_checkpoint(checkpoint) is expected
    ), f"legacy classification drifted for checkpoint={checkpoint!r}, expected={expected!r}"
    assert is_unsafe_legacy_checkpoint({"schema_version": schema_version}) is False


@pytest.mark.property
@given(
    st.sampled_from(KNOWN_PHASES),
    st.dictionaries(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12), json_native_values(), max_size=4
    ),
    normalized_operation_identities(),
)
def test_build_checkpoint_record_has_fresh_json_serializable_shape(
    phase: str, operational_data: dict, operation_identity: dict
) -> None:
    """Each checkpoint record has the current schema and independent mutable collections."""
    first = build_checkpoint_record(phase, operational_data, operation_identity)
    second = build_checkpoint_record(phase, operational_data, operation_identity)
    required = {
        "schema_version",
        "phase",
        "completed_phases",
        "operational_data",
        "operation_identity",
        "errors",
        "report_refs",
        "created_at",
        "updated_at",
    }

    assert required <= first.keys(), f"checkpoint record omitted required fields: {first!r}"
    assert first["schema_version"] == SCHEMA_VERSION
    assert first["phase"] == phase
    assert first["operational_data"] == operational_data
    assert first["operation_identity"] == operation_identity
    assert first["completed_phases"] == first["errors"] == first["report_refs"] == []
    assert first["completed_phases"] is not second["completed_phases"]
    assert first["errors"] is not second["errors"]
    assert first["report_refs"] is not second["report_refs"]
    first["completed_phases"].append(phase)
    first["errors"].append("canary")
    first["report_refs"].append({"canary": True})
    assert second["completed_phases"] == second["errors"] == second["report_refs"] == []
    assert json.loads(json.dumps(first)) == first, f"checkpoint record was not JSON round-trip safe: {first!r}"
    for timestamp_key in ("created_at", "updated_at"):
        timestamp = datetime.fromisoformat(first[timestamp_key])
        assert timestamp.tzinfo is not None and timestamp.utcoffset() == timezone.utc.utcoffset(
            timestamp
        ), f"checkpoint {timestamp_key} is not an ISO-8601 UTC timestamp: {first[timestamp_key]!r}"


@pytest.mark.property
@given(completed_phase_lists(), st.sampled_from(KNOWN_PHASES))
def test_passing_phase_records_it_exactly_once_without_reordering_others(completed: list[str], phase: str) -> None:
    """Pass is idempotent and preserves unrelated completion order."""
    checkpoint = {"completed_phases": list(completed), "sentinel": {"keep": True}}
    before = copy.deepcopy(checkpoint)
    expected_unrelated = [candidate for candidate in completed if candidate != phase]

    transition = build_phase_transition(checkpoint, phase, "pass")
    repeated = build_phase_transition({**checkpoint, **transition}, phase, "pass")

    assert (
        transition["completed_phases"].count(phase) == 1
    ), f"pass did not record exactly one {phase!r}: completed={completed!r}, transition={transition!r}"
    assert [candidate for candidate in transition["completed_phases"] if candidate != phase] == expected_unrelated
    assert transition["phase_status"] == "pass"
    assert repeated == transition, (
        f"repeated pass was not idempotent for phase={phase!r}, completed={completed!r}: "
        f"first={transition!r}, repeated={repeated!r}"
    )
    assert checkpoint == before, f"pass transition mutated input checkpoint: before={before!r}, after={checkpoint!r}"


@pytest.mark.property
@given(completed_phase_lists(duplicates=True), st.sampled_from(KNOWN_PHASES), st.sampled_from(("fail", "reset")))
def test_fail_and_reset_remove_only_requested_phase_occurrences(completed: list[str], phase: str, status: str) -> None:
    """Fail/reset remove every target occurrence without downstream pruning."""
    checkpoint = {"completed_phases": list(completed)}
    before = copy.deepcopy(checkpoint)
    expected = [candidate for candidate in completed if candidate != phase]

    transition = build_phase_transition(checkpoint, phase, status)

    assert (
        transition["completed_phases"] == expected
    ), f"{status} removed unrelated progress for phase={phase!r}, completed={completed!r}: {transition!r}"
    assert transition["phase_status"] == status
    assert checkpoint == before, f"{status} transition mutated input checkpoint: {checkpoint!r}"


@pytest.mark.property
@given(completed_phase_lists(duplicates=True), st.sampled_from(KNOWN_PHASES))
def test_enter_leaves_completed_phases_unchanged(completed: list[str], phase: str) -> None:
    """Enter records status without rewriting completion progress."""
    checkpoint = {"completed_phases": list(completed)}
    before = copy.deepcopy(checkpoint)

    transition = build_phase_transition(checkpoint, phase, "enter")

    assert transition == {"completed_phases": completed, "phase_status": "enter"}
    assert checkpoint == before, f"enter transition mutated input checkpoint: {checkpoint!r}"


def _normalize_checkpoint(
    checkpoint: dict,
    *,
    phase: str,
    expected_identity: dict,
    reset_from: str | None = None,
    has_explicit_reset: bool = False,
) -> tuple[dict, bool]:
    """Invoke only the action plugin's normalization boundary."""
    action = ActionModule.__new__(ActionModule)
    return action._normalize_checkpoint_data(
        checkpoint_data=checkpoint,
        phase=phase,
        status="enter",
        reset_from=reset_from,
        has_explicit_reset=has_explicit_reset,
        expected_operation_identity=expected_identity,
    )


@pytest.mark.property
@given(normalized_operation_identities(), completed_phase_lists(min_size=1), st.sampled_from(KNOWN_PHASES))
def test_action_normalization_rejects_schema_two_progress_without_identity(
    expected_identity: dict, completed: list[str], phase: str
) -> None:
    """Schema 2.0 progress without hub binding fails closed."""
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "completed_phases": completed,
        "operation_identity": None,
    }

    result, changed = _normalize_checkpoint(checkpoint, phase=phase, expected_identity=expected_identity)

    assert (
        result.get("failed") is True
    ), f"schema 2.0 progress without identity was accepted: checkpoint={checkpoint!r}, result={result!r}"
    assert "no operation identity" in result["msg"].lower()
    assert changed is False


@pytest.mark.property
@given(normalized_operation_identities(), st.sampled_from(KNOWN_PHASES))
def test_action_normalization_backfills_only_fresh_schema_two_checkpoint(expected_identity: dict, phase: str) -> None:
    """A schema 2.0 checkpoint with no progress may bind the expected identity."""
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "completed_phases": [],
        "operation_identity": None,
    }

    result, changed = _normalize_checkpoint(checkpoint, phase=phase, expected_identity=expected_identity)

    assert result.get("failed") is not True
    assert result["operation_identity"] == expected_identity
    assert result["completed_phases"] == []
    assert changed is True


@pytest.mark.property
@given(data=st.data(), phase=st.sampled_from(KNOWN_PHASES))
def test_action_normalization_rejects_present_mismatching_identity(data: st.DataObject, phase: str) -> None:
    """A present one-field mismatch cannot pass the action boundary without reset."""
    field = data.draw(st.sampled_from(IDENTITY_MISMATCH_FIELDS), label="mismatch field")
    case = data.draw(mismatched_operation_identities(field), label="mismatching identities")
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "completed_phases": [KNOWN_PHASES[0]],
        **case.checkpoint,
    }

    result, changed = _normalize_checkpoint(checkpoint, phase=phase, expected_identity=case.expected_identity)

    assert result.get("failed") is True, f"action boundary accepted mismatch field {field!r}: {result!r}"
    assert "operation identity" in result["msg"].lower()
    assert changed is False


@pytest.mark.property
@given(normalized_operation_identities(), completed_phase_lists(min_size=1), st.sampled_from(KNOWN_PHASES))
def test_action_normalization_rejects_legacy_progress_without_reset(
    expected_identity: dict, completed: list[str], phase: str
) -> None:
    """Legacy schema 1.0 progress cannot resume implicitly."""
    checkpoint = {"schema_version": "1.0", "completed_phases": completed}

    result, changed = _normalize_checkpoint(checkpoint, phase=phase, expected_identity=expected_identity)

    assert result.get("failed") is True, f"unsafe legacy checkpoint was accepted: {checkpoint!r}"
    assert "schema 1.0" in result["msg"]
    assert changed is False


@pytest.mark.property
@given(legacy_operation_identities(), st.sampled_from(KNOWN_PHASES))
def test_action_normalization_removes_legacy_identity_paths_before_persistence(case, phase: str) -> None:
    """Action normalization persists only the sanitized identity."""
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "completed_phases": [],
        "operation_identity": case.identity,
    }

    result, changed = _normalize_checkpoint(checkpoint, phase=phase, expected_identity=case.normalized)

    assert result.get("failed") is not True
    assert result["operation_identity"] == case.normalized
    assert not ({"primary_kubeconfig", "secondary_kubeconfig"} & result["operation_identity"].keys())
    assert changed is True


@pytest.mark.property
@given(normalized_operation_identities(), st.sampled_from(KNOWN_PHASES))
def test_action_explicit_legacy_reset_discards_unsafe_completed_progress(expected_identity: dict, phase: str) -> None:
    """An explicit reset rebuilds unsafe legacy state with no completed progress."""
    checkpoint = {
        "schema_version": "1.0",
        "completed_phases": list(KNOWN_PHASES),
        "operation_identity": None,
        "operational_data": {"stale": True},
    }

    result, changed = _normalize_checkpoint(
        checkpoint,
        phase=phase,
        expected_identity=expected_identity,
        has_explicit_reset=True,
    )

    assert result.get("failed") is not True
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["completed_phases"] == []
    assert result["operation_identity"] == expected_identity
    assert changed is False


@pytest.mark.property
@given(normalized_operation_identities(), st.sampled_from(KNOWN_PHASES))
def test_action_reset_from_prunes_unsafe_progress_and_rebinds_identity(
    expected_identity: dict, reset_from: str
) -> None:
    """Explicit reset_from keeps only stable upstream progress and replaces hub binding."""
    mismatching_identity = {**expected_identity, "secondary_cluster_uid": "different-live-hub"}
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "phase": KNOWN_PHASES[-1],
        "completed_phases": list(KNOWN_PHASES),
        "operation_identity": mismatching_identity,
        "operational_data": {},
        "errors": [],
        "report_refs": [],
    }
    reset_index = tuple(KNOWN_PHASES).index(reset_from)
    expected_completed = list(tuple(KNOWN_PHASES)[:reset_index])

    result, changed = _normalize_checkpoint(
        checkpoint,
        phase=reset_from,
        expected_identity=expected_identity,
        reset_from=reset_from,
        has_explicit_reset=True,
    )

    assert result.get("failed") is not True
    assert (
        result["completed_phases"] == expected_completed
    ), f"reset_from={reset_from!r} preserved unsafe progress: {result!r}"
    assert result["operation_identity"] == expected_identity
    assert changed is False


def _completed_step_names(manager: StateManager) -> list[str]:
    """Return the durable semantic step-name sequence without volatile metadata."""
    return [entry["name"] for entry in manager.state["completed_steps"]]


def _apply_state_operations(manager: StateManager, operations: list) -> dict:
    """Apply generated commands while maintaining an independent semantic model."""
    model = {"phase": Phase.INIT, "steps": [], "config": {}}
    captured_state = None
    captured_model = None

    for operation in operations:
        if operation.name == "set_phase":
            manager.set_phase(operation.phase)
            model["phase"] = operation.phase
        elif operation.name == "mark_step":
            manager.mark_step_completed(operation.key)
            if operation.key not in model["steps"]:
                model["steps"].append(operation.key)
        elif operation.name == "clear_step":
            manager.clear_step_completed(operation.key)
            model["steps"] = [step for step in model["steps"] if step != operation.key]
        elif operation.name == "set_config":
            manager.set_config(operation.key, operation.value)
            model["config"][operation.key] = copy.deepcopy(operation.value)
        elif operation.name == "capture_snapshot":
            captured_state = manager.capture_state_snapshot()
            captured_model = copy.deepcopy(model)
        elif operation.name == "restore_snapshot":
            assert (
                captured_state is not None and captured_model is not None
            ), f"strategy emitted restore before capture: {operations!r}"
            manager.restore_state_snapshot(captured_state)
            model = copy.deepcopy(captured_model)
        else:  # pragma: no cover - strategy exhaustiveness guard.
            raise AssertionError(f"unknown generated StateManager operation: {operation!r}")
    return model


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(state_manager_operation_sequences())
def test_state_manager_generated_histories_round_trip_durably(tmp_path: Path, operations: list) -> None:
    """A fresh StateManager matches the independent model after any valid history."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))

    model = _apply_state_operations(manager, operations)
    manager.flush_state()
    reloaded = StateManager(str(state_path))

    assert (
        reloaded.get_current_phase() is model["phase"]
    ), f"durable phase drifted for operations={operations!r}: model={model!r}, state={reloaded.state!r}"
    assert (
        _completed_step_names(reloaded) == model["steps"]
    ), f"durable steps drifted for operations={operations!r}: model={model!r}, state={reloaded.state!r}"
    assert (
        reloaded.state["config"] == model["config"]
    ), f"durable config drifted for operations={operations!r}: model={model!r}, state={reloaded.state!r}"


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(readable_step_names(), st.integers(min_value=1, max_value=5), st.booleans())
def test_state_manager_step_mark_and_clear_are_durable_and_idempotent(
    tmp_path: Path, step_name: str, repetitions: int, clear_after: bool
) -> None:
    """Marking is set-like, clearing is an idempotent durable removal."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))

    for _ in range(repetitions):
        manager.mark_step_completed(step_name)
    if clear_after:
        manager.clear_step_completed(step_name)
        manager.clear_step_completed(step_name)

    expected = not clear_after
    assert manager.is_step_completed(step_name) is expected
    assert _completed_step_names(manager).count(step_name) == int(expected)
    reloaded = StateManager(str(state_path))
    assert (
        reloaded.is_step_completed(step_name) is expected
    ), f"step durability drifted for step={step_name!r}, repetitions={repetitions}, clear={clear_after}"


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(
    st.sampled_from(("method", "versions", "flags", "retry_count")),
    json_native_values(),
)
@example("method", None)
def test_state_manager_setting_same_config_value_does_not_write_again(tmp_path: Path, key: str, value: object) -> None:
    """An unchanged JSON-native config assignment has no durable write or semantic effect."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    manager.set_config(key, value)
    durable_before = state_path.read_bytes()

    with patch.object(manager, "_write_state", wraps=manager._write_state) as write_spy:
        manager.set_config(key, copy.deepcopy(value))

    assert write_spy.call_count == 0, f"unchanged config triggered a write for key={key!r}, value={value!r}"
    assert state_path.read_bytes() == durable_before
    reloaded = StateManager(str(state_path))
    assert reloaded.get_config(key) == value


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(
    hub_identity_cases(),
    st.sampled_from(tuple(Phase)),
    readable_step_names(),
    json_native_values(),
)
def test_state_manager_snapshot_is_deep_isolated_and_restores_every_durable_field(
    tmp_path: Path,
    identity_case,
    phase: Phase,
    step_name: str,
    config_value: object,
) -> None:
    """Complete snapshots restore identity-bound durable state and the captured timestamp."""
    state_path = _fresh_state_path(tmp_path)
    identities = identity_case.identities
    manager = StateManager(str(state_path))
    manager.ensure_contexts(identities["primary"]["context"], identities["secondary"]["context"])
    manager.ensure_hub_identities(identities)
    manager.set_phase(phase)
    manager.mark_step_completed(step_name)
    manager.set_config("generated", config_value)
    manager.add_error("captured-error", phase.value)

    snapshot = manager.capture_state_snapshot()
    expected_snapshot = copy.deepcopy(snapshot)
    manager.set_phase(Phase.FAILED)
    manager.clear_step_completed(step_name)
    manager.set_config("generated", {"mutated": True})
    manager.add_error("later-error", Phase.FAILED.value)
    manager.state["contexts"] = {"primary": "mutated-primary", "secondary": "mutated-secondary"}
    manager.state["hub_identities"] = {"primary": {"context": "mutated-primary", "cluster_uid": "mutated-uid"}}
    manager.flush_state()

    assert snapshot == expected_snapshot, f"post-capture mutations changed snapshot: {snapshot!r}"
    manager.restore_state_snapshot(snapshot)
    restored_before_caller_mutation = copy.deepcopy(manager.state)

    assert manager.state == expected_snapshot
    assert manager.state["last_updated"] == expected_snapshot["last_updated"]
    snapshot["config"]["caller-mutation"] = True
    snapshot["contexts"]["primary"] = "caller-mutated"
    assert manager.state == restored_before_caller_mutation, "caller mutation leaked into restored StateManager state"

    reloaded = StateManager(str(state_path))
    assert (
        reloaded.state == expected_snapshot
    ), f"restored snapshot was not durable: expected={expected_snapshot!r}, actual={reloaded.state!r}"


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(context_pair_cases())
def test_state_manager_context_changes_reset_progress_before_rebinding(tmp_path: Path, case) -> None:
    """Matching contexts retain progress; either changed role resets stale state."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    manager.ensure_contexts(case.stored["primary"], case.stored["secondary"])
    manager.set_phase(Phase.ACTIVATION)
    manager.mark_step_completed("activate_restore")
    manager.set_config("stale", True)
    manager.add_error("stale-error", Phase.ACTIVATION.value)

    reloaded = StateManager(str(state_path))
    reloaded.ensure_contexts(case.current["primary"], case.current["secondary"])

    if case.changed_role is None:
        assert reloaded.get_current_phase() is Phase.ACTIVATION
        assert reloaded.is_step_completed("activate_restore") is True
        assert reloaded.get_config("stale") is True
        assert reloaded.get_errors(), f"matching contexts discarded progress for {case!r}"
    else:
        assert reloaded.get_current_phase() is Phase.INIT
        assert reloaded.state["completed_steps"] == []
        assert reloaded.state["config"] == {}
        assert reloaded.state["errors"] == []
    assert reloaded.state["contexts"] == case.current
    assert StateManager(str(state_path)).state["contexts"] == case.current


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(context_pair_cases())
def test_state_manager_missing_stored_contexts_reset_in_progress_state(tmp_path: Path, case) -> None:
    """In-progress state without stored contexts resets before binding desired contexts."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    manager.set_phase(Phase.PRIMARY_PREP)
    manager.mark_step_completed("pause_backups")
    manager.set_config("stale", True)
    manager.add_error("stale-error", Phase.PRIMARY_PREP.value)
    manager.state.pop("contexts", None)
    manager.flush_state()

    reloaded = StateManager(str(state_path))
    reloaded.ensure_contexts(case.current["primary"], case.current["secondary"])

    assert reloaded.get_current_phase() is Phase.INIT
    assert reloaded.state["completed_steps"] == []
    assert reloaded.state["config"] == {}
    assert reloaded.state["errors"] == []
    assert reloaded.state["contexts"] == case.current


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@pytest.mark.parametrize("role", ("primary", "secondary"))
@given(hub_identity_cases(), st.sampled_from(("", " ", "\t\n")))
def test_state_manager_requires_nonempty_uid_for_every_supplied_hub(
    tmp_path: Path, role: str, case, missing_uid: str
) -> None:
    """Any supplied role lacking a live UID is rejected before binding."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    invalid = copy.deepcopy(case.identities)
    invalid[role]["cluster_uid"] = missing_uid

    with pytest.raises(StateIdentityMismatch, match="missing a live cluster UID"):
        manager.ensure_hub_identities(invalid)

    assert (
        manager.state["hub_identities"] == {}
    ), f"missing UID partially bound identities for role={role}: {manager.state!r}"


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@pytest.mark.parametrize("role", ("primary", "secondary"))
@given(hub_identity_cases())
def test_state_manager_uid_mismatch_fails_without_rewriting_persisted_identity(tmp_path: Path, role: str, case) -> None:
    """Either hub UID changing blocks resume and leaves disk binding untouched."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    manager.ensure_hub_identities(case.identities)
    persisted_before = copy.deepcopy(manager.state["hub_identities"])
    live = copy.deepcopy(case.identities)
    live[role]["cluster_uid"] += "-different"

    with pytest.raises(StateIdentityMismatch):
        StateManager(str(state_path)).ensure_hub_identities(live)

    persisted_after = StateManager(str(state_path)).state["hub_identities"]
    assert (
        persisted_after == persisted_before
    ), f"UID mismatch rewrote persisted binding for role={role}: before={persisted_before!r}, after={persisted_after!r}"


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(hub_identity_cases())
def test_state_manager_legacy_progress_requires_opt_in_before_identity_backfill(tmp_path: Path, case) -> None:
    """Legacy progress fails closed, while explicit verified backfill persists."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    manager.ensure_contexts(
        case.identities["primary"]["context"],
        case.identities["secondary"]["context"],
    )
    manager.set_phase(Phase.PRIMARY_PREP)

    reloaded = StateManager(str(state_path))
    with pytest.raises(StateIdentityMismatch, match="missing hub identity"):
        reloaded.ensure_hub_identities(case.identities)
    assert StateManager(str(state_path)).state["hub_identities"] == {}

    reloaded.ensure_hub_identities(case.identities, allow_legacy_backfill=True)
    assert StateManager(str(state_path)).state["hub_identities"] == case.identities


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(hub_identity_cases())
def test_state_manager_persist_false_validates_without_writing(tmp_path: Path, case) -> None:
    """Read-only identity validation never creates a persisted binding."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    durable_before = state_path.read_bytes()

    manager.ensure_hub_identities(case.identities, persist=False)

    assert manager.state["hub_identities"] == {}
    assert state_path.read_bytes() == durable_before
    assert StateManager(str(state_path)).state["hub_identities"] == {}


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@pytest.mark.parametrize("role", ("primary", "secondary"))
@given(hub_identity_cases())
def test_state_manager_context_only_identity_change_is_not_uid_mismatch(tmp_path: Path, role: str, case) -> None:
    """Identity binding compares UIDs; ensure_contexts owns context-name changes."""
    state_path = _fresh_state_path(tmp_path)
    manager = StateManager(str(state_path))
    manager.ensure_hub_identities(case.identities)
    live = copy.deepcopy(case.identities)
    live[role]["context"] += "-renamed"

    manager.ensure_hub_identities(live, persist=False)

    assert StateManager(str(state_path)).state["hub_identities"] == case.identities
