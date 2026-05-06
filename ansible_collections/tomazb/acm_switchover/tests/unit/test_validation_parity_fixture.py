"""Shared validation parity fixture for the Ansible collection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_operation_inputs,
    validate_safe_path,
)

FIXTURE_PATH = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "validation_parity_cases.yml"


def _load_cases() -> list[dict]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def _run_operation_case(case_input: dict) -> tuple[bool, str]:
    try:
        validate_operation_inputs(
            operation=case_input.get("operation", {}),
            features=case_input.get("features", {}),
            execution=case_input.get("execution", {}),
        )
    except ValidationError as exc:
        return False, str(exc)
    return True, ""


def _run_path_case(case_input: dict) -> tuple[bool, str]:
    try:
        validate_safe_path(case_input["path"])
    except ValidationError as exc:
        return False, str(exc)
    return True, ""


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["name"])
def test_collection_validation_matches_shared_parity_fixture(case: dict) -> None:
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
