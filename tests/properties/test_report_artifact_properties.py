"""Property-based contracts for Python and collection report artifacts."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import artifacts as collection_artifacts
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.artifacts import (
    ArtifactWriteError,
    _parse_file_mode,
    build_report_ref,
    write_json_artifact,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.result import ValidationResult
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError as CollectionValidationError,
)
from lib.constants import (
    REPORT_DEFAULT_CHECK,
    REPORT_PHASE_PREFLIGHT,
    REPORT_SCHEMA_VERSION,
    REPORT_STATUS_FAIL,
    REPORT_STATUS_PASS,
)
from lib.exceptions import SecurityValidationError
from lib.report_artifacts import (
    _normalise_validation_result,
    _summarize_state,
    build_operation_report,
    write_json_report_artifact,
)
from tests.properties.strategies import (
    CheckModeCase,
    ValidFileModeCase,
    artifact_relative_paths,
    check_mode_cases,
    collection_preflight_cases,
    invalid_file_modes,
    json_native_values,
    legacy_validation_results,
    phase_summary_dictionaries,
    preflight_result_lists,
    report_args_cases,
    report_artifact_traversal_paths,
    report_state_cases,
    report_text,
    structured_validation_results,
    valid_file_modes,
)

SCHEMA_KEYS = {"id", "severity", "status", "message", "details", "recommended_action"}
PYTHON_REPORT_KEYS = {
    "schema_version",
    "generated_at",
    "source",
    "type",
    "status",
    "summary",
    "hubs",
    "operation",
    "errors",
}
COLLECTION_REPORT_KEYS = {
    "schema_version",
    "generated_at",
    "source",
    "phase",
    "status",
    "summary",
    "hubs",
    "results",
}
WRITER_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


def _load_preflight_helpers():
    """Transactionally load pure preflight helpers without requiring ansible-core."""
    module_name = "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report"
    parent_name, _, parent_attribute = module_name.rpartition(".")
    managed_names = ("ansible", "ansible.module_utils", "ansible.module_utils.basic", module_name)
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
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name not in {"ansible", "ansible.module_utils", "ansible.module_utils.basic"}:
                raise
            ansible_module = types.ModuleType("ansible")
            module_utils = types.ModuleType("ansible.module_utils")
            basic_module = types.ModuleType("ansible.module_utils.basic")

            class _AnsibleModule:  # pragma: no cover - used only without ansible-core.
                pass

            basic_module.AnsibleModule = _AnsibleModule
            ansible_module.module_utils = module_utils
            module_utils.basic = basic_module
            sys.modules["ansible"] = ansible_module
            sys.modules["ansible.module_utils"] = module_utils
            sys.modules["ansible.module_utils.basic"] = basic_module
            sys.modules.pop(module_name, None)
            if parent_module is not None and hasattr(parent_module, parent_attribute):
                delattr(parent_module, parent_attribute)
            module = importlib.import_module(module_name)
        return (
            module.summarize_preflight_results,
            module.sanitize_report_hubs,
            module.build_preflight_report,
            module.write_report,
        )
    finally:
        restore()


(
    summarize_preflight_results,
    sanitize_report_hubs,
    build_preflight_report,
    write_report,
) = _load_preflight_helpers()


def _assert_utc_timestamp(value: str) -> None:
    timestamp = datetime.fromisoformat(value)
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)


def _assert_json_native(value: object) -> None:
    assert json.loads(json.dumps(value)) == value


def _expected_normalized_result(result: dict) -> dict:
    """Model the documented normalization contract independently of production."""
    if {"id", "severity", "status", "message"}.issubset(result):
        return result
    check_name = str(result.get("check", REPORT_DEFAULT_CHECK)).strip() or REPORT_DEFAULT_CHECK
    check_id = check_name.lower().translate(str.maketrans({" ": "-", "_": "-"}))
    return {
        "id": f"preflight-{check_id}",
        "severity": {True: "critical", False: "warning"}[bool(result.get("critical", True))],
        "status": {True: "pass", False: "fail"}[bool(result.get("passed"))],
        "message": result.get("message", ""),
        "details": {"check": check_name},
        "recommended_action": None,
    }


def _contains_value(candidate: object, expected: object) -> bool:
    if candidate == expected:
        return True
    if isinstance(candidate, dict):
        return any(
            _contains_value(key, expected) or _contains_value(value, expected) for key, value in candidate.items()
        )
    if isinstance(candidate, list):
        return any(_contains_value(value, expected) for value in candidate)
    return False


def _create_required_symlink(link: Path, target: Path, *, target_is_directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.fail(f"required symlink fixture could not be created: {exc}")


def _files_beneath(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def test_absent_ansible_preflight_import_is_transactional_in_isolated_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrow fallback restores modules, cache, and package attributes on all paths."""
    test_file = Path(__file__).resolve()
    expected_suffix = Path("tests/properties/test_report_artifact_properties.py")
    if len(test_file.parents) < 3:
        raise ValueError(f"Cannot resolve repository root from PBT-06 test path: {test_file}")
    repo_root = test_file.parents[2]
    if (repo_root / expected_suffix).resolve() != test_file:
        raise ValueError(f"PBT-06 test is not located under the expected repository layout: {test_file}")

    decoy_root = tmp_path / "ambient-pythonpath-decoy"
    decoy_test = decoy_root / expected_suffix
    decoy_collection = decoy_root / "ansible_collections/tomazb/acm_switchover/plugins/module_utils/artifacts.py"
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

expected_root = Path(os.environ["PBT06_EXPECTED_REPO_ROOT"]).resolve()
decoy_root = Path(os.environ["PBT06_DECOY_PYTHONPATH"]).resolve()
assert os.environ["PYTHONPATH"] == str(expected_root)
assert "PYTHONHOME" not in os.environ
assert os.environ["PYTHONSAFEPATH"] == "1"
assert all(Path(entry).resolve() != decoy_root for entry in sys.path if entry)

module_name = "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report"
parent_name = module_name.rpartition(".")[0]
temporary_names = ("ansible", "ansible.module_utils", "ansible.module_utils.basic")
for name in (*temporary_names, module_name):
    sys.modules.pop(name, None)
parent = importlib.import_module(parent_name)
if hasattr(parent, "acm_preflight_report"):
    delattr(parent, "acm_preflight_report")

class BlockAnsible(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ansible" or fullname.startswith("ansible."):
            raise ModuleNotFoundError(f"deliberately blocked {fullname}", name=fullname)
        return None

blocker = BlockAnsible()
sys.meta_path.insert(0, blocker)
import tests.properties.test_report_artifact_properties as tested
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import artifacts as imported_collection

tested_path = Path(tested.__file__).resolve()
collection_path = Path(imported_collection.__file__).resolve()
assert tested_path == expected_root / "tests/properties/test_report_artifact_properties.py"
assert tested_path.is_relative_to(expected_root)
assert collection_path.is_relative_to(expected_root)
print(f"PBT06_TEST_MODULE_PATH={tested_path}")
print(f"PBT06_COLLECTION_MODULE_PATH={collection_path}")
assert tested.summarize_preflight_results([]) == {
    "passed": True, "critical_failures": 0, "warning_failures": 0,
}
assert all(name not in sys.modules for name in temporary_names)
assert module_name not in sys.modules
assert not hasattr(parent, "acm_preflight_report")

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
parent.acm_preflight_report = preexisting_attribute
attempts = 0

def fail_fallback(name, package=None):
    global attempts
    if name == module_name:
        attempts += 1
        missing = "ansible.module_utils.basic" if attempts == 1 else "unrelated_dependency"
        raise ModuleNotFoundError(f"forced missing {missing}", name=missing)
    return real_import(name, package)

importlib.import_module = fail_fallback
try:
    tested._load_preflight_helpers()
except ModuleNotFoundError as exc:
    assert exc.name == "unrelated_dependency"
else:
    raise AssertionError("an unrelated import failure was suppressed")
assert all(sys.modules[name] is module for name, module in preexisting.items())
assert sys.modules[module_name] is preexisting_target
assert parent.acm_preflight_report is preexisting_attribute

preexisting = {name: type(sys)(f"{name}.success") for name in temporary_names}
preexisting_target = type(sys)(f"{module_name}.success")
preexisting_attribute = object()
sys.modules.update(preexisting)
sys.modules[module_name] = preexisting_target
parent.acm_preflight_report = preexisting_attribute
attempts = 0

def force_fallback_once(name, package=None):
    global attempts
    if name == module_name:
        attempts += 1
        if attempts == 1:
            raise ModuleNotFoundError("forced missing action dependency", name="ansible.module_utils.basic")
    return real_import(name, package)

importlib.import_module = force_fallback_once
helpers = tested._load_preflight_helpers()
assert helpers[0]([]) == {"passed": True, "critical_failures": 0, "warning_failures": 0}
assert all(sys.modules[name] is module for name, module in preexisting.items())
assert sys.modules[module_name] is preexisting_target
assert parent.acm_preflight_report is preexisting_attribute
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.update(
        {
            "PYTHONPATH": str(repo_root),
            "PYTHONSAFEPATH": "1",
            "PBT06_EXPECTED_REPO_ROOT": str(repo_root),
            "PBT06_DECOY_PYTHONPATH": str(decoy_root),
            "ANSIBLE_LOCAL_TEMP": "/tmp/ansible-local-pbt06-import",
            "ANSIBLE_REMOTE_TMP": "/tmp/ansible-remote-pbt06-import",
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
    assert f"PBT06_TEST_MODULE_PATH={test_file}" in result.stdout
    collection_path = (
        repo_root / "ansible_collections/tomazb/acm_switchover/plugins/module_utils/artifacts.py"
    ).resolve()
    assert f"PBT06_COLLECTION_MODULE_PATH={collection_path}" in result.stdout


@pytest.mark.property
@given(legacy_validation_results())
@example({"check": "", "passed": False, "critical": True, "message": "boundary"})
@example({"check": "Mixed CASE_value", "passed": True, "critical": False, "message": "preserved"})
def test_legacy_normalization_has_exact_schema_mapping_and_no_mutation(result: dict) -> None:
    before = copy.deepcopy(result)
    expected = _expected_normalized_result(result)

    normalized = _normalise_validation_result(result)

    assert set(normalized) == SCHEMA_KEYS
    assert normalized == expected
    assert result == before
    _assert_json_native(normalized)


@pytest.mark.property
@given(structured_validation_results(include_extensions=True))
def test_structured_normalization_returns_entry_unchanged_without_mutation(result: dict) -> None:
    before = copy.deepcopy(result)

    normalized = _normalise_validation_result(result)

    assert normalized == before
    assert result == before
    assert SCHEMA_KEYS <= set(normalized)
    _assert_json_native(normalized)


@pytest.mark.property
@given(report_state_cases(), st.sampled_from(("pass", "fail", "error", "warning")))
def test_state_summary_uses_exact_counts_status_and_empty_collection_semantics(case, status: str) -> None:
    before = copy.deepcopy(case.snapshot)
    expected_errors = case.snapshot["errors"] or []
    expected_steps = case.snapshot["completed_steps"] or []

    summary = _summarize_state(case.snapshot, status)

    assert summary == {
        "passed": status == REPORT_STATUS_PASS,
        "completed_steps": len(expected_steps),
        "error_count": len(expected_errors),
        "current_phase": case.snapshot["current_phase"],
    }
    assert case.snapshot == before


def test_state_summary_defaults_missing_collections_to_empty() -> None:
    state = {"current_phase": None}
    before = copy.deepcopy(state)

    summary = _summarize_state(state, REPORT_STATUS_FAIL)

    assert summary == {"passed": False, "completed_steps": 0, "error_count": 0, "current_phase": None}
    assert state == before


@pytest.mark.property
@given(
    st.sampled_from(("switchover", "decommission", "restore-only")),
    st.sampled_from(("pass", "fail", "error")),
    report_text(min_size=1, max_size=24),
    report_args_cases(),
    report_state_cases(),
    phase_summary_dictionaries(),
)
def test_python_operation_report_schema_fields_round_trip_and_exclude_sensitive_config(
    report_type: str,
    status: str,
    source: str,
    args_case,
    state_case,
    phases: dict,
) -> None:
    args = SimpleNamespace(**args_case.values)
    args_before = copy.deepcopy(vars(args))
    state_before = copy.deepcopy(state_case.snapshot)
    phases_before = copy.deepcopy(phases)

    report = build_operation_report(report_type, status, source, args, state_case.snapshot, phases)

    assert PYTHON_REPORT_KEYS <= report.keys()
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["source"] == source
    assert report["type"] == report_type
    assert report["status"] == status
    errors = state_case.snapshot.get("errors") or []
    completed_steps = state_case.snapshot.get("completed_steps") or []
    assert report["summary"] == {
        "passed": status == REPORT_STATUS_PASS,
        "completed_steps": len(completed_steps),
        "error_count": len(errors),
        "current_phase": state_case.snapshot.get("current_phase"),
    }
    assert isinstance(report["hubs"], dict)
    assert isinstance(report["operation"], dict)
    assert report["errors"] == errors
    _assert_utc_timestamp(report["generated_at"])
    expected_hubs = {
        role: {"context": args_case.values[f"{role}_context"]}
        for role in ("primary", "secondary")
        if args_case.values[f"{role}_context"]
    }
    assert report["hubs"] == expected_hubs
    assert report["operation"] == args_case.expected_operation
    assert ("phases" in report) is bool(phases)
    if phases:
        assert report["phases"] == phases
    assert "phase" not in report and "results" not in report
    assert json.loads(json.dumps(report)) == report
    for canary in state_case.secret_canaries:
        assert not _contains_value(report, canary)
    assert vars(args) == args_before
    assert state_case.snapshot == state_before
    assert phases == phases_before


@pytest.mark.property
@given(report_args_cases(), report_state_cases())
def test_python_preflight_report_includes_phase_and_normalized_results(args_case, state_case) -> None:
    raw_results = state_case.snapshot["config"]["preflight_results"]
    args = SimpleNamespace(**args_case.values)
    args_before = copy.deepcopy(vars(args))
    state_before = copy.deepcopy(state_case.snapshot)
    report = build_operation_report(
        REPORT_PHASE_PREFLIGHT,
        REPORT_STATUS_PASS,
        "python-cli",
        args,
        state_case.snapshot,
    )

    assert report["phase"] == REPORT_PHASE_PREFLIGHT
    assert report["results"] == [_expected_normalized_result(item) for item in raw_results]
    assert all(SCHEMA_KEYS <= set(item) for item in report["results"])
    assert vars(args) == args_before
    assert state_case.snapshot == state_before


@pytest.mark.property
@given(report_args_cases(), report_state_cases())
def test_python_argocd_section_has_exact_trigger_and_paused_count(args_case, state_case) -> None:
    config = state_case.snapshot["config"]
    run_id = config["argocd_run_id"]
    paused_apps = config["argocd_paused_apps"] or []
    state_before = copy.deepcopy(state_case.snapshot)
    report = build_operation_report(
        "switchover",
        "pass",
        "python-cli",
        SimpleNamespace(**args_case.values),
        state_case.snapshot,
    )

    assert ("argocd" in report) is bool(run_id or paused_apps)
    if run_id or paused_apps:
        assert report["argocd"] == {
            "run_id": run_id or "",
            "summary": {"paused": len(paused_apps), "restored": 0},
        }
    assert state_case.snapshot == state_before


@pytest.mark.property
@given(structured_validation_results())
def test_validation_result_to_dict_has_exact_preserved_json_schema(values: dict) -> None:
    before = copy.deepcopy(values)
    result = ValidationResult(**values)

    serialized = result.to_dict()

    assert set(serialized) == SCHEMA_KEYS
    assert serialized == values
    _assert_json_native(serialized)
    assert values == before


def test_validation_result_default_details_are_independent() -> None:
    first = ValidationResult("first", "info", "pass", "ok")
    second = ValidationResult("second", "info", "pass", "ok")

    first.details["canary"] = True

    assert second.details == {}
    assert first.details is not second.details


@pytest.mark.property
@given(preflight_result_lists())
def test_collection_preflight_summary_counts_only_documented_failures(results: list[dict]) -> None:
    before = copy.deepcopy(results)
    critical = sum(item["severity"] == "critical" and item["status"] in {"fail", "error"} for item in results)
    warnings = sum(item["severity"] == "warning" and item["status"] in {"fail", "error"} for item in results)

    summary = summarize_preflight_results(results)

    assert summary == {
        "passed": critical == 0,
        "critical_failures": critical,
        "warning_failures": warnings,
    }
    assert results == before


@pytest.mark.property
@given(collection_preflight_cases())
def test_collection_preflight_report_schema_precedence_round_trip_and_secret_exclusion(case) -> None:
    hubs_before = copy.deepcopy(case.hubs)
    identities_before = copy.deepcopy(case.hub_identities)
    results_before = copy.deepcopy(case.results)

    sanitized = sanitize_report_hubs(case.hubs, case.hub_identities)
    report = build_preflight_report(case.phase, case.results, case.hubs, case.hub_identities)

    assert sanitized == case.expected_hubs
    assert set(sanitized) == set(case.expected_hubs)
    assert all(set(hub) == {"context", "cluster_uid"} for hub in sanitized.values())
    assert set(report) == COLLECTION_REPORT_KEYS
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["source"] == "tomazb.acm_switchover"
    assert report["phase"] == case.phase
    assert report["results"] == case.results
    assert report["hubs"] == case.expected_hubs
    critical = sum(item["severity"] == "critical" and item["status"] in {"fail", "error"} for item in case.results)
    warnings = sum(item["severity"] == "warning" and item["status"] in {"fail", "error"} for item in case.results)
    expected_summary = {
        "passed": critical == 0,
        "critical_failures": critical,
        "warning_failures": warnings,
    }
    assert report["summary"] == expected_summary
    assert report["status"] == ("pass" if critical == 0 else "fail")
    _assert_utc_timestamp(report["generated_at"])
    _assert_json_native(report)
    for canary in case.secret_canaries:
        assert not _contains_value(report, canary)
    assert case.hubs == hubs_before
    assert case.hub_identities == identities_before
    assert case.results == results_before


@pytest.mark.property
@given(
    report_text(max_size=32),
    artifact_relative_paths(),
    report_text(max_size=24),
)
def test_build_report_ref_is_exact_deterministic_and_non_mutating(phase: str, path: str, kind: str) -> None:
    expected = {"phase": phase, "path": path, "kind": kind}

    first = build_report_ref(path=path, phase=phase, kind=kind)
    second = build_report_ref(path=path, phase=phase, kind=kind)

    assert first == second == expected
    assert first is not second


@pytest.mark.property
@given(valid_file_modes())
@example(ValidFileModeCase(value="0600", expected=0o600))
@example(ValidFileModeCase(value="0o777", expected=0o777))
def test_parse_file_mode_accepts_supported_octal_representations(case) -> None:
    assert 0 <= case.expected <= 0o777
    assert case.expected & 0o600 == 0o600
    assert _parse_file_mode(case.value) == case.expected


@pytest.mark.property
@given(st.integers())
@example(0)
@example(0o600)
@example(0o777)
@example(0o1000)
def test_parse_file_mode_complete_integer_domain_matches_manageable_oracle(mode: int) -> None:
    manageable = 0 <= mode <= 0o777 and mode & 0o600 == 0o600
    if manageable:
        assert _parse_file_mode(mode) == mode
    else:
        with pytest.raises(ArtifactWriteError, match="Invalid report artifact mode"):
            _parse_file_mode(mode)


@pytest.mark.property
@WRITER_SETTINGS
@given(mode=invalid_file_modes())
@example(mode=0)
def test_invalid_file_modes_raise_before_path_or_filesystem_access(tmp_path: Path, mode: str | int) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="invalid-mode-") as example_directory:
        example_root = Path(example_directory)
        destination = example_root / "nested" / "report.json"
        report = {"status": "pass", "details": [mode]}
        before = copy.deepcopy(report)
        root_mode = example_root.stat().st_mode & 0o777
        root_tree = _files_beneath(example_root)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("invalid mode reached path or filesystem access")

        with patch.object(collection_artifacts, "validate_report_artifact_path", side_effect=forbidden), patch.object(
            collection_artifacts, "Path", side_effect=forbidden
        ), patch.object(collection_artifacts.os, "open", side_effect=forbidden), patch.object(
            collection_artifacts.os, "read", side_effect=forbidden
        ), patch.object(
            collection_artifacts.os, "stat", side_effect=forbidden
        ), patch.object(
            collection_artifacts.os, "mkdir", side_effect=forbidden
        ), patch.object(
            collection_artifacts.os, "chmod", side_effect=forbidden
        ):
            with pytest.raises(ArtifactWriteError, match="Invalid report artifact mode") as exc_info:
                write_json_artifact(report, str(destination), mode=mode)

        assert "mode" in str(exc_info.value).lower()
        try:
            parsed_mode = mode if isinstance(mode, int) else int(mode, 8)
        except ValueError:
            parsed_mode = None
        if parsed_mode is not None and 0 <= parsed_mode <= 0o777:
            assert "owner read and write permissions" in str(exc_info.value)
        assert not os.path.exists(destination)
        assert not os.path.exists(destination.parent)
        assert _files_beneath(example_root) == root_tree
        assert example_root.stat().st_mode & 0o777 == root_mode
        assert report == before
    assert not example_root.exists()


@pytest.mark.property
@WRITER_SETTINGS
@given(json_native_values(), artifact_relative_paths())
def test_python_writer_safe_nested_destination_round_trips_with_one_final_newline(
    tmp_path: Path, report: object, relative: str
) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="python-report-example-") as example_directory:
        example_root = Path(example_directory)
        destination = example_root / relative
        before = copy.deepcopy(report)

        written = write_json_report_artifact(report, str(destination))

        content = destination.read_text(encoding="utf-8")
        assert written == str(destination)
        assert destination.is_relative_to(tmp_path)
        assert destination.parent.is_dir()
        assert content.endswith("\n") and not content.endswith("\n\n")
        assert json.loads(content) == report
        assert report == before
    assert not example_root.exists()


@pytest.mark.property
@WRITER_SETTINGS
@given(json_native_values(), report_artifact_traversal_paths())
def test_python_writer_rejects_traversal_without_creating_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: object, candidate: str
) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="python-traversal-") as example_directory:
        example_root = Path(example_directory)
        workspace = example_root / "workspace"
        workspace.mkdir()
        with monkeypatch.context() as example_monkeypatch:
            example_monkeypatch.chdir(workspace)
            before = _files_beneath(tmp_path)

            with pytest.raises(SecurityValidationError):
                write_json_report_artifact(report, candidate)

            assert _files_beneath(tmp_path) == before
    assert not example_root.exists()


@pytest.mark.parametrize("writer", (write_json_report_artifact, write_json_artifact))
def test_report_writers_reject_parent_symlink_escape_without_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    _create_required_symlink(workspace / "escape", outside, target_is_directory=True)
    monkeypatch.chdir(workspace)

    with pytest.raises((SecurityValidationError, CollectionValidationError)):
        writer({"status": "pass"}, "escape/report.json")

    assert not (outside / "report.json").exists()


@pytest.mark.parametrize("writer", (write_json_report_artifact, write_json_artifact))
def test_report_writers_reject_final_symlink_without_modifying_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "target.json"
    target.write_text("sentinel\n", encoding="utf-8")
    link = workspace / "report.json"
    _create_required_symlink(link, target, target_is_directory=False)
    monkeypatch.chdir(workspace)

    with pytest.raises((SecurityValidationError, CollectionValidationError)):
        writer({"status": "pass"}, link.name)

    assert target.read_text(encoding="utf-8") == "sentinel\n"


@pytest.mark.property
@WRITER_SETTINGS
@given(json_native_values(), artifact_relative_paths(), valid_file_modes())
def test_collection_writer_safe_write_round_trips_and_enforces_exact_mode(
    tmp_path: Path, report: object, relative: str, mode_case
) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="collection-write-") as example_directory:
        example_root = Path(example_directory)
        destination = example_root / relative
        before = copy.deepcopy(report)

        written, changed = write_json_artifact(report, str(destination), mode=mode_case.value)

        assert written == str(destination)
        assert changed is True
        assert destination.is_relative_to(tmp_path)
        assert destination.stat().st_mode & 0o777 == mode_case.expected
        content = destination.read_text(encoding="utf-8")
        assert content.endswith("\n") and not content.endswith("\n\n")
        assert json.loads(content) == report
        assert report == before
    assert not example_root.exists()


@pytest.mark.property
@WRITER_SETTINGS
@given(json_native_values(), valid_file_modes())
@example(
    None,
    ValidFileModeCase(value="0600", expected=0o600),
)
def test_collection_writer_changed_contract_for_repeat_content_and_mode_updates(
    tmp_path: Path, first_report: object, first_mode
) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="changed-contract-") as example_directory:
        example_root = Path(example_directory)
        destination = example_root / "report.json"
        content_update = {"pbt_content_update": first_report}
        second_mode = first_mode.expected ^ 0o040
        combined_update = {"pbt_combined_update": content_update}
        inputs_before = copy.deepcopy((first_report, content_update, combined_update))

        _, first_changed = write_json_artifact(first_report, str(destination), mode=first_mode.value)
        _, repeated_changed = write_json_artifact(first_report, str(destination), mode=first_mode.value)
        _, content_changed = write_json_artifact(content_update, str(destination), mode=first_mode.value)
        _, mode_changed = write_json_artifact(content_update, str(destination), mode=second_mode)
        _, combined_changed = write_json_artifact(combined_update, str(destination), mode=first_mode.expected)
        _, final_repeat_changed = write_json_artifact(combined_update, str(destination), mode=first_mode.expected)

        assert first_changed is True
        assert repeated_changed is False
        assert content_changed is True
        assert mode_changed is True
        assert combined_changed is True
        assert final_repeat_changed is False
        assert destination.stat().st_mode & 0o777 == first_mode.expected
        content = destination.read_text(encoding="utf-8")
        assert content.endswith("\n") and not content.endswith("\n\n")
        assert json.loads(content) == combined_update
        assert (first_report, content_update, combined_update) == inputs_before
    assert not example_root.exists()


def _seed_artifact(path: Path, report: object, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)


@pytest.mark.property
@WRITER_SETTINGS
@given(check_mode_cases())
@example(CheckModeCase("absent", None, None, 0o600, 0o600, False, True))
@example(CheckModeCase("identical", None, None, 0o600, 0o600, True, False))
@example(CheckModeCase("content-only", 0, False, 0o600, 0o600, True, True))
@example(CheckModeCase("mode-only", None, None, 0o600, 0o640, True, True))
@example(CheckModeCase("combined", 0, False, 0o600, 0o640, True, True))
def test_collection_check_mode_predicts_execute_and_never_writes_or_chmods(
    tmp_path: Path,
    case: CheckModeCase,
) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix=f"check-mode-{case.scenario}-") as example_directory:
        example_root = Path(example_directory)
        check_path = example_root / "check-parent" / "report.json"
        execute_path = example_root / "execute-parent" / "report.json"
        if case.initially_exists:
            _seed_artifact(check_path, case.existing_report, case.existing_mode)
            _seed_artifact(execute_path, case.existing_report, case.existing_mode)
        check_bytes = check_path.read_bytes() if check_path.exists() else None
        check_mode_before = check_path.stat().st_mode & 0o777 if check_path.exists() else None
        desired_bytes = (json.dumps(case.desired_report, indent=2, sort_keys=True) + "\n").encode("utf-8")
        desired_before = copy.deepcopy(case.desired_report)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("check mode attempted a write-side filesystem mutation")

        with patch.object(collection_artifacts.os, "open", side_effect=forbidden), patch.object(
            collection_artifacts.Path, "mkdir", side_effect=forbidden
        ), patch.object(collection_artifacts.Path, "chmod", side_effect=forbidden):
            _, predicted = write_json_artifact(
                case.desired_report, str(check_path), check_mode=True, mode=case.desired_mode
            )
        _, executed = write_json_artifact(
            case.desired_report, str(execute_path), check_mode=False, mode=case.desired_mode
        )

        assert predicted is executed is case.expected_changed
        assert check_path.exists() is case.initially_exists
        if case.initially_exists:
            assert check_path.read_bytes() == check_bytes
            assert check_path.stat().st_mode & 0o777 == check_mode_before
        else:
            assert not check_path.parent.exists()
        assert execute_path.exists()
        assert execute_path.read_bytes() == desired_bytes
        assert execute_path.stat().st_mode & 0o777 == case.desired_mode
        assert case.desired_report == desired_before
    assert not example_root.exists()


@pytest.mark.property
@WRITER_SETTINGS
@given(json_native_values(), report_artifact_traversal_paths())
def test_collection_writer_rejects_traversal_without_creating_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: object, candidate: str
) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="collection-traversal-") as example_directory:
        example_root = Path(example_directory)
        workspace = example_root / "workspace"
        workspace.mkdir()
        with monkeypatch.context() as example_monkeypatch:
            example_monkeypatch.chdir(workspace)
            before = _files_beneath(tmp_path)

            with pytest.raises(CollectionValidationError):
                write_json_artifact(report, candidate)

            assert _files_beneath(tmp_path) == before
    assert not example_root.exists()


@pytest.mark.property
@WRITER_SETTINGS
@given(json_native_values(), artifact_relative_paths())
def test_preflight_write_report_uses_real_writer_and_preserves_inputs(
    tmp_path: Path, report: object, relative: str
) -> None:
    with tempfile.TemporaryDirectory(dir=tmp_path, prefix="preflight-wrapper-") as example_directory:
        example_root = Path(example_directory)
        destination = example_root / relative
        before = copy.deepcopy(report)

        path, changed, error = write_report(report, str(destination), check_mode=False)

        assert (path, changed, error) == (str(destination), True, None)
        assert destination.is_relative_to(tmp_path)
        assert json.loads(destination.read_text(encoding="utf-8")) == report
        assert report == before
    assert not example_root.exists()


def test_preflight_write_report_returns_validation_error_without_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "preflight-error"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    path, changed, error = write_report({"status": "fail"}, "reports/../outside.json")

    assert path == "reports/../outside.json"
    assert changed is False
    assert error is not None
    assert "Path traversal attempt" in error
    assert _files_beneath(workspace) == set()
