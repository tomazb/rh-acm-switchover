"""Shared validation parity fixture for the Python CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lib.exceptions import SecurityValidationError, ValidationError
from lib.validation import InputValidator

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "validation_parity_cases.yml"


class MockArgs:
    """Minimal argparse-like object for CLI validation tests."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _load_cases() -> list[dict]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def _python_args(case_input: dict) -> MockArgs:
    operation = case_input.get("operation", {})
    execution = case_input.get("execution", {})
    features = case_input.get("features", {})
    argocd = features.get("argocd") or {}
    restore_only = bool(operation.get("restore_only", False))

    return MockArgs(
        primary_context=None if restore_only else "primary-hub",
        secondary_context="secondary-hub",
        method=operation.get("method", "passive"),
        activation_method=operation.get("activation_method", "patch"),
        old_hub_action=operation.get("old_hub_action"),
        log_format="text",
        state_file=".state/switchover-state.json",
        decommission=False,
        setup=False,
        restore_only=restore_only,
        validate_only=execution.get("mode") == "validate",
        dry_run=execution.get("mode") == "dry_run",
        argocd_manage=bool(argocd.get("manage", False)),
        argocd_resume_on_failure=bool(argocd.get("resume_on_failure", False)),
        argocd_resume_only=False,
    )


def _run_operation_case(case_input: dict) -> tuple[bool, str]:
    try:
        InputValidator.validate_all_cli_args(_python_args(case_input))
    except (SecurityValidationError, ValidationError) as exc:
        return False, str(exc)
    return True, ""


def _run_path_case(case_input: dict) -> tuple[bool, str]:
    try:
        InputValidator.validate_safe_filesystem_path(case_input["path"], "parity")
    except (SecurityValidationError, ValidationError) as exc:
        return False, str(exc)
    return True, ""


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["name"])
def test_python_validation_matches_shared_parity_fixture(case: dict) -> None:
    if case["kind"] == "operation":
        passed, message = _run_operation_case(case["input"])
    elif case["kind"] == "path":
        passed, message = _run_path_case(case["input"])
    else:
        raise AssertionError(f"Unsupported validation parity case kind: {case['kind']}")

    expected = case["expected"]
    assert passed is expected["passed"], message
    if not expected["passed"]:
        assert expected["contains"] in message
