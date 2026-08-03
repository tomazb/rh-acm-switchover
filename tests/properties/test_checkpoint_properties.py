"""Property-based tests for checkpoint and resume safety semantics."""

from __future__ import annotations

import atexit
import copy
import importlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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

    missing = object()
    parent_name, _, parent_attribute = module_name.rpartition(".")
    managed_module_names = ("ansible", "ansible.plugins", "ansible.plugins.action", module_name)
    previous_modules = {name: sys.modules.get(name, missing) for name in managed_module_names}
    parent_module = sys.modules.get(parent_name)
    previous_parent_attribute = (
        getattr(parent_module, parent_attribute, missing) if parent_module is not None else missing
    )
    ansible_module = types.ModuleType("ansible")
    plugins_module = types.ModuleType("ansible.plugins")
    action_module = types.ModuleType("ansible.plugins.action")

    class _ActionBase:  # pragma: no cover - used only when ansible-core is absent.
        pass

    action_module.ActionBase = _ActionBase
    ansible_module.plugins = plugins_module
    plugins_module.action = action_module
    try:
        sys.modules["ansible"] = ansible_module
        sys.modules["ansible.plugins"] = plugins_module
        sys.modules["ansible.plugins.action"] = action_module
        sys.modules.pop(module_name, None)
        if parent_module is not None and hasattr(parent_module, parent_attribute):
            delattr(parent_module, parent_attribute)
        module = importlib.import_module(module_name)
        return module.ActionModule, module.build_phase_transition
    finally:
        current_parent_module = sys.modules.get(parent_name)
        if current_parent_module is not None:
            if previous_parent_attribute is missing:
                if hasattr(current_parent_module, parent_attribute):
                    delattr(current_parent_module, parent_attribute)
            else:
                setattr(current_parent_module, parent_attribute, previous_parent_attribute)
        for name, previous_module in previous_modules.items():
            if previous_module is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module


ActionModule, build_phase_transition = _load_action_helpers()

# Hypothesis reuses pytest's function-scoped tmp_path across examples.  Every
# example below creates a unique child directory, so no durable state leaks
# between examples; suppressing only this fixture warning is therefore safe.
STATE_MANAGER_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


def test_absent_ansible_fallback_is_transactional_in_isolated_subprocess() -> None:
    """Fallback helpers stay usable without leaking synthetic import state."""
    script = r"""
import importlib
import importlib.abc
import sys

module_name = "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase"
parent_name = module_name.rpartition(".")[0]
temporary_names = ("ansible", "ansible.plugins", "ansible.plugins.action")
for name in list(sys.modules):
    if name in temporary_names or name == module_name:
        sys.modules.pop(name, None)
parent = importlib.import_module(parent_name)
if hasattr(parent, "checkpoint_phase"):
    delattr(parent, "checkpoint_phase")

class BlockAnsible(importlib.abc.MetaPathFinder):
    def __init__(self):
        self.calls = []

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ansible" or fullname.startswith("ansible."):
            self.calls.append(fullname)
            raise ModuleNotFoundError(f"deliberately blocked {fullname}", name=fullname)
        return None

blocker = BlockAnsible()
sys.meta_path.insert(0, blocker)
import tests.properties.test_checkpoint_properties as tested

assert tested.build_phase_transition({"completed_phases": []}, "preflight", "pass") == {
    "completed_phases": ["preflight"],
    "phase_status": "pass",
}
action = tested.ActionModule.__new__(tested.ActionModule)
normalized, changed = action._normalize_checkpoint_data(
    checkpoint_data={"schema_version": "2.0", "completed_phases": [], "operation_identity": None},
    phase="preflight",
    status="enter",
    reset_from=None,
    has_explicit_reset=False,
    expected_operation_identity={"primary_context": "primary"},
)
assert normalized["operation_identity"] == {"primary_context": "primary"}
assert changed is True
assert all(name not in sys.modules for name in temporary_names)
assert module_name not in sys.modules
assert not hasattr(parent, "checkpoint_phase")

try:
    importlib.import_module("ansible")
except ModuleNotFoundError as exc:
    assert exc.name == "ansible"
else:
    raise AssertionError("later unrelated Ansible import reused a leaked fallback module")
assert blocker.calls == ["ansible", "ansible"]

real_import_module = importlib.import_module

def install_preexisting_modules(label):
    modules = {name: type(sys)(f"{name}.{label}") for name in temporary_names}
    target_module = type(sys)(f"{module_name}.{label}")
    parent_attribute = object()
    sys.modules.update(modules)
    sys.modules[module_name] = target_module
    parent.checkpoint_phase = parent_attribute
    return modules, target_module, parent_attribute

preexisting, preexisting_target, preexisting_parent_attribute = install_preexisting_modules("preexisting")
import_attempts = 0

def force_fallback_once(name, package=None):
    global import_attempts
    if name == module_name:
        import_attempts += 1
        if import_attempts == 1:
            raise ModuleNotFoundError("forced missing action dependency", name="ansible.plugins.action")
    return real_import_module(name, package)

importlib.import_module = force_fallback_once
preserved_action, preserved_transition = tested._load_action_helpers()
assert preserved_transition({"completed_phases": []}, "activation", "pass")["completed_phases"] == ["activation"]
assert preserved_action.__name__ == "ActionModule"
assert all(sys.modules[name] is module for name, module in preexisting.items())
assert sys.modules[module_name] is preexisting_target
assert parent.checkpoint_phase is preexisting_parent_attribute

preexisting, preexisting_target, preexisting_parent_attribute = install_preexisting_modules("failure")
import_attempts = 0

def fail_during_fallback(name, package=None):
    global import_attempts
    if name == module_name:
        import_attempts += 1
        missing_name = "ansible.plugins.action" if import_attempts == 1 else "unrelated_dependency"
        raise ModuleNotFoundError(f"forced missing {missing_name}", name=missing_name)
    return real_import_module(name, package)

importlib.import_module = fail_during_fallback
try:
    tested._load_action_helpers()
except ModuleNotFoundError as exc:
    assert exc.name == "unrelated_dependency"
else:
    raise AssertionError("unrelated fallback import failure was suppressed")
assert all(sys.modules[name] is module for name, module in preexisting.items())
assert sys.modules[module_name] is preexisting_target
assert parent.checkpoint_phase is preexisting_parent_attribute
"""
    environment = {
        **os.environ,
        "ANSIBLE_LOCAL_TEMP": "/tmp/ansible-local-pbt05",
        "ANSIBLE_REMOTE_TMP": "/tmp/ansible-remote-pbt05",
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, f"isolated fallback probe failed:\nstdout={result.stdout}\nstderr={result.stderr}"


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


@contextmanager
def _state_manager_scope() -> Iterator[Callable[[Path], StateManager]]:
    """Create StateManagers whose process-lifetime resources are always released."""
    managers: list[StateManager] = []
    signal_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def make_manager(state_path: Path) -> StateManager:
        manager = StateManager(str(state_path))
        managers.append(manager)
        return manager

    try:
        yield make_manager
    finally:
        for manager in reversed(managers):
            for callback in (manager._release_run_lock, manager._flush_on_exit, manager._cleanup_temp_files):
                atexit.unregister(callback)
            manager._flush_on_exit()
            manager._cleanup_temp_files()
            manager._release_run_lock()
        for sig, previous_handler in signal_handlers.items():
            signal.signal(sig, previous_handler)


def test_state_manager_scope_releases_process_lifetime_resources(tmp_path: Path) -> None:
    """The property-test scope releases locks, callbacks, and signal handlers."""
    from lib import utils as utils_module

    registry_before = dict(utils_module._RUN_LOCK_REGISTRY)
    signal_handlers_before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    registered_callbacks = []
    unregistered_callbacks = []
    real_register = atexit.register
    real_unregister = atexit.unregister

    def track_register(callback):
        registered_callbacks.append(callback)
        return real_register(callback)

    def track_unregister(callback):
        unregistered_callbacks.append(callback)
        return real_unregister(callback)

    with patch.object(atexit, "register", side_effect=track_register), patch.object(
        atexit, "unregister", side_effect=track_unregister
    ):
        with _state_manager_scope() as make_manager:
            make_manager(_fresh_state_path(tmp_path))

    assert utils_module._RUN_LOCK_REGISTRY == registry_before
    assert {sig: signal.getsignal(sig) for sig in signal_handlers_before} == signal_handlers_before
    assert [callback.__name__ for callback in registered_callbacks] == [
        "_release_run_lock",
        "_flush_on_exit",
        "_cleanup_temp_files",
    ]
    assert [callback.__name__ for callback in unregistered_callbacks] == [
        "_release_run_lock",
        "_flush_on_exit",
        "_cleanup_temp_files",
    ]


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


@pytest.mark.parametrize("role", ("primary", "secondary"))
def test_build_operation_identity_prefers_hub_uid_over_distinct_fallback(role: str) -> None:
    """A live hub UID wins when the fallback identity records a different UID."""
    hubs = {
        "primary": {"context": "primary-context"},
        "secondary": {"context": "secondary-context"},
    }
    hubs[role]["cluster_uid"] = f"uid-{role}-hub"
    hub_identities = {
        "primary": {"cluster_uid": "uid-primary-fallback"},
        "secondary": {"cluster_uid": "uid-secondary-fallback"},
    }

    identity = build_operation_identity(hubs, {}, hub_identities=hub_identities)

    assert identity[f"{role}_cluster_uid"] == f"uid-{role}-hub"


@pytest.mark.parametrize("role", ("primary", "secondary"))
def test_build_operation_identity_uses_fallback_uid_when_hub_uid_is_absent(role: str) -> None:
    """The recorded hub identity supplies the UID when live hub data omits it."""
    hubs = {
        "primary": {"context": "primary-context"},
        "secondary": {"context": "secondary-context"},
    }
    hub_identities = {
        "primary": {"cluster_uid": "uid-primary-fallback"},
        "secondary": {"cluster_uid": "uid-secondary-fallback"},
    }

    identity = build_operation_identity(hubs, {}, hub_identities=hub_identities)

    assert identity[f"{role}_cluster_uid"] == f"uid-{role}-fallback"


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
    actual_identity = case.checkpoint["operation_identity"]
    differing_fields = {
        key
        for key in actual_identity.keys() | case.expected_identity.keys()
        if actual_identity.get(key) != case.expected_identity.get(key)
    }
    checkpoint_before = copy.deepcopy(case.checkpoint)
    expected_before = copy.deepcopy(case.expected_identity)

    assert differing_fields == {field}, f"mismatch strategy changed unexpected fields for {case!r}"
    assert actual_identity[field] != case.expected_identity[field], f"mismatch strategy produced equality for {case!r}"
    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity(case.checkpoint, case.expected_identity)

    assert case.checkpoint == checkpoint_before, f"mismatch validation mutated checkpoint for {field}: {case!r}"
    assert (
        case.expected_identity == expected_before
    ), f"mismatch validation mutated expected identity for {field}: {case!r}"


def test_retained_extension_collision_uses_guaranteed_different_value_type() -> None:
    """The exact historical collision cannot produce an equal extension value."""
    from tests.properties.strategies import retained_extension_mismatch_value

    expected = {"mismatch": "forced-different"}

    actual = retained_extension_mismatch_value(expected, "forced-different")

    assert actual == "forced-different-type-mismatch"
    assert actual != expected


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
@pytest.mark.parametrize("field", IDENTITY_MISMATCH_FIELDS)
@given(data=st.data(), phase=st.sampled_from(KNOWN_PHASES))
def test_action_normalization_rejects_present_mismatching_identity(field: str, data: st.DataObject, phase: str) -> None:
    """A present one-field mismatch cannot pass the action boundary without reset."""
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
        elif operation.name == "_set_config":
            manager._set_config(operation.key, operation.value)
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
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)

        model = _apply_state_operations(manager, operations)
        manager.flush_state()
        reloaded = make_manager(state_path)

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
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)

        for _ in range(repetitions):
            manager.mark_step_completed(step_name)
        if clear_after:
            manager.clear_step_completed(step_name)
            manager.clear_step_completed(step_name)

        expected = not clear_after
        assert manager.is_step_completed(step_name) is expected
        assert _completed_step_names(manager).count(step_name) == int(expected)
        reloaded = make_manager(state_path)
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
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        manager._set_config(key, value)
        durable_before = state_path.read_bytes()

        with patch.object(manager, "_write_state", wraps=manager._write_state) as write_spy:
            manager._set_config(key, copy.deepcopy(value))

        assert write_spy.call_count == 0, f"unchanged config triggered a write for key={key!r}, value={value!r}"
        assert state_path.read_bytes() == durable_before
        reloaded = make_manager(state_path)
        assert reloaded._get_config(key) == value


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
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        manager.ensure_contexts(identities["primary"]["context"], identities["secondary"]["context"])
        manager.ensure_hub_identities(identities)
        manager.set_phase(phase)
        manager.mark_step_completed(step_name)
        manager._set_config("generated", config_value)
        manager.add_error("captured-error", phase.value)

        snapshot = manager.capture_state_snapshot()
        expected_snapshot = copy.deepcopy(snapshot)
        manager.set_phase(Phase.FAILED)
        manager.clear_step_completed(step_name)
        manager._set_config("generated", {"mutated": True})
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
        assert (
            manager.state == restored_before_caller_mutation
        ), "caller mutation leaked into restored StateManager state"

        reloaded = make_manager(state_path)
        assert (
            reloaded.state == expected_snapshot
        ), f"restored snapshot was not durable: expected={expected_snapshot!r}, actual={reloaded.state!r}"


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(context_pair_cases())
def test_state_manager_context_changes_reset_progress_before_rebinding(tmp_path: Path, case) -> None:
    """Matching contexts retain progress; either changed role resets stale state."""
    state_path = _fresh_state_path(tmp_path)
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        manager.ensure_contexts(case.stored["primary"], case.stored["secondary"])
        manager.set_phase(Phase.ACTIVATION)
        manager.mark_step_completed("activate_restore")
        manager._set_config("stale", True)
        manager.add_error("stale-error", Phase.ACTIVATION.value)
        expected_errors = copy.deepcopy(manager.get_errors())

        reloaded = make_manager(state_path)
        reloaded.ensure_contexts(case.current["primary"], case.current["secondary"])

        if case.changed_role is None:
            assert reloaded.get_current_phase() is Phase.ACTIVATION
            assert reloaded.is_step_completed("activate_restore") is True
            assert reloaded._get_config("stale") is True
            assert (
                reloaded.get_errors() == expected_errors
            ), f"matching contexts discarded or corrupted errors for {case!r}"
        else:
            assert reloaded.get_current_phase() is Phase.INIT
            assert reloaded.state["completed_steps"] == []
            assert reloaded.state["config"] == {}
            assert reloaded.state["errors"] == []
        assert reloaded.state["contexts"] == case.current
        assert make_manager(state_path).state["contexts"] == case.current


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(context_pair_cases())
def test_state_manager_missing_stored_contexts_reset_in_progress_state(tmp_path: Path, case) -> None:
    """In-progress state without stored contexts resets before binding desired contexts."""
    state_path = _fresh_state_path(tmp_path)
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        manager.set_phase(Phase.PRIMARY_PREP)
        manager.mark_step_completed("pause_backups")
        manager._set_config("stale", True)
        manager.add_error("stale-error", Phase.PRIMARY_PREP.value)
        manager.state.pop("contexts", None)
        manager.flush_state()

        reloaded = make_manager(state_path)
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
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
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
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        manager.ensure_hub_identities(case.identities)
        persisted_before = copy.deepcopy(manager.state["hub_identities"])
        live = copy.deepcopy(case.identities)
        live[role]["cluster_uid"] += "-different"

        with pytest.raises(StateIdentityMismatch):
            make_manager(state_path).ensure_hub_identities(live)

        persisted_after = make_manager(state_path).state["hub_identities"]
        assert persisted_after == persisted_before, (
            f"UID mismatch rewrote persisted binding for role={role}: "
            f"before={persisted_before!r}, after={persisted_after!r}"
        )


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(hub_identity_cases())
def test_state_manager_legacy_progress_requires_opt_in_before_identity_backfill(tmp_path: Path, case) -> None:
    """Legacy progress fails closed, while explicit verified backfill persists."""
    state_path = _fresh_state_path(tmp_path)
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        manager.ensure_contexts(
            case.identities["primary"]["context"],
            case.identities["secondary"]["context"],
        )
        manager.set_phase(Phase.PRIMARY_PREP)

        reloaded = make_manager(state_path)
        with pytest.raises(StateIdentityMismatch, match="missing hub identity"):
            reloaded.ensure_hub_identities(case.identities)
        assert make_manager(state_path).state["hub_identities"] == {}

        reloaded.ensure_hub_identities(case.identities, allow_legacy_backfill=True)
        assert make_manager(state_path).state["hub_identities"] == case.identities


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@given(hub_identity_cases())
def test_state_manager_persist_false_validates_without_writing(tmp_path: Path, case) -> None:
    """Read-only identity validation never creates a persisted binding."""
    state_path = _fresh_state_path(tmp_path)
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        durable_before = state_path.read_bytes()

        manager.ensure_hub_identities(case.identities, persist=False)

        assert manager.state["hub_identities"] == {}
        assert state_path.read_bytes() == durable_before
        assert make_manager(state_path).state["hub_identities"] == {}


@pytest.mark.property
@STATE_MANAGER_SETTINGS
@pytest.mark.parametrize("role", ("primary", "secondary"))
@given(hub_identity_cases())
def test_state_manager_context_only_identity_change_is_not_uid_mismatch(tmp_path: Path, role: str, case) -> None:
    """Identity binding compares UIDs; ensure_contexts owns context-name changes."""
    state_path = _fresh_state_path(tmp_path)
    with _state_manager_scope() as make_manager:
        manager = make_manager(state_path)
        manager.ensure_hub_identities(case.identities)
        live = copy.deepcopy(case.identities)
        live[role]["context"] += "-renamed"

        manager.ensure_hub_identities(live, persist=False)

        assert make_manager(state_path).state["hub_identities"] == case.identities
