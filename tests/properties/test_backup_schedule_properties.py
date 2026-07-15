"""Property-based safety contracts for Python and collection BackupSchedules."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given

from lib.constants import (
    BACKUP_NAMESPACE,
    BACKUP_SCHEDULE_PLURAL,
    CLUSTER_BACKUP_API_GROUP,
    CLUSTER_BACKUP_API_VERSION,
)
from lib.exceptions import SwitchoverError
from modules.backup_schedule import (
    BackupScheduleManager,
)
from modules.backup_schedule import _backup_schedule_names as python_backup_schedule_names
from modules.backup_schedule import (
    acm_supports_backup_schedule_pause,
    fail_on_multiple_backup_schedules,
)
from tests.properties.strategies import (
    AcmVersionCase,
    backup_schedule_lists,
    no_saved_backup_schedule_values,
    parseable_acm_versions,
    saved_backup_schedule_bodies,
    unparseable_acm_versions,
)

RUNTIME_METADATA_FIELDS = {
    "uid",
    "resourceVersion",
    "creationTimestamp",
    "generation",
    "managedFields",
}
COLLECTION_MODULE_NAME = "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_backup_schedule"


def _load_collection_helpers() -> tuple[Any, ...]:
    """Transactionally load pure collection helpers without requiring ansible-core."""
    parent_name, _, parent_attribute = COLLECTION_MODULE_NAME.rpartition(".")
    managed_names = (
        "ansible",
        "ansible.module_utils",
        "ansible.module_utils.basic",
        COLLECTION_MODULE_NAME,
    )
    missing = object()
    previous_modules = {name: sys.modules.get(name, missing) for name in managed_names}
    parent_module = sys.modules.get(parent_name)
    previous_parent_attribute = (
        getattr(parent_module, parent_attribute, missing) if parent_module is not None else missing
    )

    def restore() -> None:
        current_parent = sys.modules.get(parent_name)
        if current_parent is not None:
            if previous_parent_attribute is missing:
                if hasattr(current_parent, parent_attribute):
                    delattr(current_parent, parent_attribute)
            else:
                setattr(current_parent, parent_attribute, previous_parent_attribute)
        for name, previous in previous_modules.items():
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    try:
        try:
            module = importlib.import_module(COLLECTION_MODULE_NAME)
        except ModuleNotFoundError as exc:
            expected_missing = {"ansible", "ansible.module_utils", "ansible.module_utils.basic"}
            if exc.name not in expected_missing:
                raise

            ansible_module = types.ModuleType("ansible")
            module_utils_module = types.ModuleType("ansible.module_utils")
            basic_module = types.ModuleType("ansible.module_utils.basic")

            class _AnsibleModule:  # pragma: no cover - used only without ansible-core.
                pass

            basic_module.AnsibleModule = _AnsibleModule
            ansible_module.module_utils = module_utils_module
            module_utils_module.basic = basic_module
            sys.modules["ansible"] = ansible_module
            sys.modules["ansible.module_utils"] = module_utils_module
            sys.modules["ansible.module_utils.basic"] = basic_module
            sys.modules.pop(COLLECTION_MODULE_NAME, None)
            if parent_module is not None and hasattr(parent_module, parent_attribute):
                delattr(parent_module, parent_attribute)
            module = importlib.import_module(COLLECTION_MODULE_NAME)

        return (
            module._parse_acm_version,
            module.backup_schedule_pause_mode,
            module._backup_schedule_names,
            module._build_saved_schedule_body,
            module.build_backup_schedule_operation,
        )
    finally:
        restore()


(
    collection_parse_acm_version,
    collection_backup_schedule_pause_mode,
    collection_backup_schedule_names,
    collection_build_saved_schedule_body,
    collection_build_backup_schedule_operation,
) = _load_collection_helpers()


class RecordingKubeClient:
    """Minimal client fake that makes every permitted mutation observable."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.patch_calls: list[dict[str, Any]] = []

    def create_custom_resource(self, **kwargs: Any) -> None:
        self.create_calls.append(kwargs)

    def patch_custom_resource(self, **kwargs: Any) -> None:
        self.patch_calls.append(kwargs)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected KubeClient method accessed: {name}")


class RecordingStateManager:
    """State fake that returns the caller-owned value without copying it."""

    def __init__(self, saved_schedule: Any) -> None:
        self.saved_schedule = saved_schedule
        self.get_config_calls: list[str] = []

    def get_config(self, key: str) -> Any:
        self.get_config_calls.append(key)
        return self.saved_schedule


def _expected_pause_support(components: tuple[int, int, int]) -> bool:
    return components >= (2, 12, 0)


def _expected_schedule_names(schedules: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for schedule in schedules:
        metadata = schedule.get("metadata", {})
        rendered.append(metadata.get("name") or "<unnamed>")
    return ", ".join(rendered)


def _expected_recreated_body(saved_schedule: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(saved_schedule)
    metadata = body.get("metadata")
    if metadata:
        for key in RUNTIME_METADATA_FIELDS:
            metadata.pop(key, None)
    body.pop("status", None)
    if "spec" not in body:
        body["spec"] = {}
    body["spec"]["paused"] = False
    return body


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_collection_backup_schedule_import_fallback_is_transactional_in_isolated_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-Ansible fallback stays local and resolves helpers from this checkout."""
    test_file = Path(__file__).resolve()
    expected_suffix = Path("tests/properties/test_backup_schedule_properties.py")
    if len(test_file.parents) < 3:
        raise ValueError(f"Cannot resolve repository root from PBT-07 test path: {test_file}")
    repo_root = test_file.parents[2]
    if (repo_root / expected_suffix).resolve() != test_file:
        raise ValueError(f"PBT-07 test is not located under the expected repository layout: {test_file}")

    decoy_root = tmp_path / "ambient-pythonpath-decoy"
    decoy_test = decoy_root / expected_suffix
    decoy_collection = decoy_root / "ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py"
    for decoy_file in (decoy_test, decoy_collection):
        decoy_file.parent.mkdir(parents=True, exist_ok=True)
        decoy_file.write_text("raise AssertionError('ambient PYTHONPATH decoy imported')\n", encoding="utf-8")
        for package in decoy_file.parents:
            if package == decoy_root:
                break
            (package / "__init__.py").touch()
    monkeypatch.setenv("PYTHONPATH", str(decoy_root))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "ambient-python-home"))

    script = r"""
import importlib
import importlib.abc
import os
import sys
from pathlib import Path

expected_root = Path(os.environ["PBT07_EXPECTED_REPO_ROOT"]).resolve()
decoy_root = Path(os.environ["PBT07_DECOY_PYTHONPATH"]).resolve()
assert os.environ["PYTHONPATH"] == str(expected_root)
assert "PYTHONHOME" not in os.environ
assert os.environ["PYTHONSAFEPATH"] == "1"
assert all(Path(entry).resolve() != decoy_root for entry in sys.path if entry)

module_name = "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_backup_schedule"
parent_name, _, parent_attribute = module_name.rpartition(".")
temporary_names = ("ansible", "ansible.module_utils", "ansible.module_utils.basic")
for name in (*temporary_names, module_name):
    sys.modules.pop(name, None)
parent = importlib.import_module(parent_name)
if hasattr(parent, parent_attribute):
    delattr(parent, parent_attribute)

class BlockAnsible(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ansible" or fullname.startswith("ansible."):
            raise ModuleNotFoundError(f"deliberately blocked {fullname}", name=fullname)
        return None

blocker = BlockAnsible()
sys.meta_path.insert(0, blocker)
import tests.properties.test_backup_schedule_properties as tested

tested_path = Path(tested.__file__).resolve()
assert tested_path == expected_root / "tests/properties/test_backup_schedule_properties.py"
assert tested_path.is_relative_to(expected_root)
assert tested.collection_backup_schedule_pause_mode("2.12.0") == "pause"
assert tested.collection_parse_acm_version("2.11") == (2, 11, 0)
assert all(name not in sys.modules for name in temporary_names)
assert module_name not in sys.modules
assert not hasattr(parent, parent_attribute)

try:
    importlib.import_module("ansible")
except ModuleNotFoundError as exc:
    assert exc.name == "ansible"
else:
    raise AssertionError("a synthetic Ansible module leaked from the fallback")

real_import = importlib.import_module
preexisting = {name: type(sys)(f"{name}.preexisting") for name in temporary_names}
preexisting_target = type(sys)(f"{module_name}.preexisting")
preexisting_attribute = object()
sys.modules.update(preexisting)
sys.modules[module_name] = preexisting_target
setattr(parent, parent_attribute, preexisting_attribute)
attempts = 0

def fail_during_fallback(name, package=None):
    global attempts
    if name == module_name:
        attempts += 1
        missing = "ansible.module_utils.basic" if attempts == 1 else "unrelated_dependency"
        raise ModuleNotFoundError(f"forced missing {missing}", name=missing)
    return real_import(name, package)

importlib.import_module = fail_during_fallback
try:
    tested._load_collection_helpers()
except ModuleNotFoundError as exc:
    assert exc.name == "unrelated_dependency"
else:
    raise AssertionError("an unrelated import failure was suppressed")
assert all(sys.modules[name] is module for name, module in preexisting.items())
assert sys.modules[module_name] is preexisting_target
assert getattr(parent, parent_attribute) is preexisting_attribute

preexisting = {name: type(sys)(f"{name}.success") for name in temporary_names}
preexisting_target = type(sys)(f"{module_name}.success")
preexisting_attribute = object()
sys.modules.update(preexisting)
sys.modules[module_name] = preexisting_target
setattr(parent, parent_attribute, preexisting_attribute)
attempts = 0

def force_fallback_once(name, package=None):
    global attempts
    if name == module_name:
        attempts += 1
        if attempts == 1:
            raise ModuleNotFoundError("forced missing Ansible dependency", name="ansible.module_utils.basic")
    return real_import(name, package)

importlib.import_module = force_fallback_once
helpers = tested._load_collection_helpers()
assert helpers[1]("2.12.0") == "pause"
assert all(sys.modules[name] is module for name, module in preexisting.items())
assert sys.modules[module_name] is preexisting_target
assert getattr(parent, parent_attribute) is preexisting_attribute
print(f"PBT07_TEST_MODULE_PATH={tested_path}")
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.update(
        {
            "PYTHONPATH": str(repo_root),
            "PYTHONSAFEPATH": "1",
            "PBT07_EXPECTED_REPO_ROOT": str(repo_root),
            "PBT07_DECOY_PYTHONPATH": str(decoy_root),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"isolated import probe failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert f"PBT07_TEST_MODULE_PATH={test_file}" in result.stdout


@pytest.mark.property
@given(parseable_acm_versions())
@example(AcmVersionCase("2.11", (2, 11, 0)))
@example(AcmVersionCase("2.11.999", (2, 11, 999)))
@example(AcmVersionCase("2.12", (2, 12, 0)))
@example(AcmVersionCase("2.12.0", (2, 12, 0)))
@example(AcmVersionCase("2.14.3-rc1", (2, 14, 3)))
@example(AcmVersionCase("2.14.3+build", (2, 14, 3)))
def test_python_version_threshold_uses_independent_numeric_oracle(case: AcmVersionCase) -> None:
    assert acm_supports_backup_schedule_pause(case.value) is _expected_pause_support(case.components)


@pytest.mark.property
@given(unparseable_acm_versions())
@example("2")
@example("2.14.3rc1")
def test_invalid_python_versions_fail_closed(version: str) -> None:
    with pytest.raises(SwitchoverError, match="refusing to mutate BackupSchedule"):
        acm_supports_backup_schedule_pause(version)


@pytest.mark.property
@given(parseable_acm_versions())
@example(AcmVersionCase("2.11", (2, 11, 0)))
@example(AcmVersionCase("2.12", (2, 12, 0)))
def test_collection_version_parser_and_threshold_use_independent_oracle(case: AcmVersionCase) -> None:
    expected_mode = "pause" if _expected_pause_support(case.components) else "delete"
    assert collection_parse_acm_version(case.value) == case.components
    assert collection_backup_schedule_pause_mode(case.value) == expected_mode


@pytest.mark.property
@given(unparseable_acm_versions())
@example("2")
@example("2.14.3rc1")
def test_invalid_collection_versions_fail_closed(version: str) -> None:
    with pytest.raises(ValueError):
        collection_parse_acm_version(version)
    with pytest.raises(ValueError):
        collection_backup_schedule_pause_mode(version)


@pytest.mark.property
@given(parseable_acm_versions())
def test_parseable_version_decisions_agree_across_form_factors(case: AcmVersionCase) -> None:
    python_supports_pause = acm_supports_backup_schedule_pause(case.value)
    collection_mode = collection_backup_schedule_pause_mode(case.value)
    assert (python_supports_pause, collection_mode) in {(True, "pause"), (False, "delete")}


@pytest.mark.property
@given(unparseable_acm_versions())
def test_unparseable_versions_are_rejected_by_both_form_factors(version: str) -> None:
    with pytest.raises(SwitchoverError):
        acm_supports_backup_schedule_pause(version)
    with pytest.raises(ValueError):
        collection_backup_schedule_pause_mode(version)


@pytest.mark.property
@given(backup_schedule_lists())
def test_python_multiplicity_rejects_exactly_ambiguous_lists(schedules: list[dict[str, Any]]) -> None:
    if len(schedules) > 1:
        with pytest.raises(SwitchoverError):
            fail_on_multiple_backup_schedules(schedules, "generated hub")
    else:
        fail_on_multiple_backup_schedules(schedules, "generated hub")


@pytest.mark.property
@given(backup_schedule_lists())
def test_collection_multiplicity_rejects_exactly_ambiguous_lists(schedules: list[dict[str, Any]]) -> None:
    if len(schedules) > 1:
        with pytest.raises(ValueError):
            collection_build_backup_schedule_operation("2.12.0", "pause", schedules)
    else:
        collection_build_backup_schedule_operation("2.12.0", "pause", schedules)


@pytest.mark.property
@given(backup_schedule_lists(min_size=2))
@example(
    [
        {"metadata": {"name": "first"}, "spec": {"paused": False}},
        {"metadata": {}, "spec": {"paused": True}},
        {"metadata": {"name": "third"}, "spec": {}},
    ]
)
def test_ambiguity_reporting_preserves_order_and_refuses_selection(
    schedules: list[dict[str, Any]],
) -> None:
    expected_names = _expected_schedule_names(schedules)
    assert python_backup_schedule_names(schedules) == expected_names
    assert collection_backup_schedule_names(schedules) == expected_names

    with pytest.raises(SwitchoverError) as python_error:
        fail_on_multiple_backup_schedules(schedules, "generated hub")
    with pytest.raises(ValueError) as collection_error:
        collection_build_backup_schedule_operation("2.12.0", "enable", schedules)

    for message in (str(python_error.value), str(collection_error.value)):
        assert expected_names in message
        assert "Refusing to choose one automatically" in message


@pytest.mark.property
@given(saved_backup_schedule_bodies())
def test_python_clean_metadata_removes_exact_runtime_fields_and_is_idempotent(
    saved_schedule: dict[str, Any],
) -> None:
    original = copy.deepcopy(saved_schedule)
    working_copy = copy.deepcopy(saved_schedule)
    expected = copy.deepcopy(saved_schedule)
    for key in RUNTIME_METADATA_FIELDS:
        expected["metadata"].pop(key, None)

    BackupScheduleManager._clean_metadata(working_copy)
    assert working_copy == expected
    assert saved_schedule == original

    once_cleaned = copy.deepcopy(working_copy)
    BackupScheduleManager._clean_metadata(working_copy)
    assert working_copy == once_cleaned


@pytest.mark.property
@given(saved_backup_schedule_bodies())
@example(
    {
        "apiVersion": f"{CLUSTER_BACKUP_API_GROUP}/{CLUSTER_BACKUP_API_VERSION}",
        "kind": "BackupSchedule",
        "metadata": {
            "name": "saved-boundary",
            "namespace": BACKUP_NAMESPACE,
            "labels": {"purpose": "pbt"},
            "annotations": {"note": "preserve"},
            "uid": "uid-1",
            "resourceVersion": "7",
            "creationTimestamp": "2026-01-02T03:04:05Z",
            "generation": 3,
            "managedFields": [{"manager": "controller"}],
            "pbtMetadataExtension": {"keep": True},
        },
        "spec": {"paused": True, "veleroSchedule": "0 */6 * * *", "pbtSpecExtension": [1, 2]},
        "status": {"phase": "Enabled"},
        "pbtTopLevelExtension": {"keep": "yes"},
    }
)
def test_python_restore_sanitizes_create_body_without_mutating_state(
    saved_schedule: dict[str, Any],
) -> None:
    before = copy.deepcopy(saved_schedule)
    before_json = _canonical_json(saved_schedule)
    expected_body = _expected_recreated_body(saved_schedule)
    client = RecordingKubeClient()
    state = RecordingStateManager(saved_schedule)
    manager = BackupScheduleManager(client, state, "generated hub")

    manager._restore_saved_schedule()

    assert state.get_config_calls == ["saved_backup_schedule"]
    assert saved_schedule == before
    assert _canonical_json(saved_schedule) == before_json
    assert client.patch_calls == []
    assert client.create_calls == [
        {
            "group": CLUSTER_BACKUP_API_GROUP,
            "version": CLUSTER_BACKUP_API_VERSION,
            "plural": BACKUP_SCHEDULE_PLURAL,
            "body": expected_body,
            "namespace": BACKUP_NAMESPACE,
        }
    ]


@pytest.mark.property
@given(saved_backup_schedule_bodies())
def test_collection_recreated_body_is_sanitized_without_mutating_saved_object(
    saved_schedule: dict[str, Any],
) -> None:
    before = copy.deepcopy(saved_schedule)
    before_json = _canonical_json(saved_schedule)
    expected_body = _expected_recreated_body(saved_schedule)

    body = collection_build_saved_schedule_body(saved_schedule)

    assert body == expected_body
    assert saved_schedule == before
    assert _canonical_json(saved_schedule) == before_json


@pytest.mark.property
@given(saved_backup_schedule_bodies())
def test_recreated_bodies_agree_across_form_factors(saved_schedule: dict[str, Any]) -> None:
    client = RecordingKubeClient()
    state = RecordingStateManager(saved_schedule)
    BackupScheduleManager(client, state, "generated hub")._restore_saved_schedule()

    assert len(client.create_calls) == 1
    assert client.create_calls[0]["body"] == collection_build_saved_schedule_body(saved_schedule)


@pytest.mark.property
@given(no_saved_backup_schedule_values())
@example(None)
@example({})
def test_falsey_saved_schedule_never_plans_or_performs_mutation(saved_schedule: Any) -> None:
    client = RecordingKubeClient()
    state = RecordingStateManager(saved_schedule)

    BackupScheduleManager(client, state, "generated hub")._restore_saved_schedule()

    assert state.get_config_calls == ["saved_backup_schedule"]
    assert client.create_calls == []
    assert client.patch_calls == []
