"""Shared validation parity fixture for the Ansible collection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
    validate_operation_inputs,
    validate_report_artifact_path,
    validate_safe_path,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_input_validate import (
    build_input_validation_results,
    summarize_input_validation,
)


def _find_repo_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    raise RuntimeError(f"Could not find repository root from {Path(__file__)}")


FIXTURE_PATH = _find_repo_root() / "tests" / "fixtures" / "validation_parity_cases.yml"


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


def _run_hub_context_case(case_input: dict) -> tuple[bool, str]:
    operation = dict(case_input.get("operation", {}))
    hub_contexts = case_input.get("hub_contexts", {})
    restore_only = operation.get("restore_only", False)
    if restore_only and operation.get("old_hub_action") is None:
        operation.pop("old_hub_action")
    results = build_input_validation_results(
        {
            "hubs": {
                "primary": {
                    "context": hub_contexts.get("primary", "" if restore_only else "primary-hub"),
                    "kubeconfig": "" if restore_only else "./kubeconfigs/primary",
                },
                "secondary": {
                    "context": hub_contexts.get("secondary", "secondary-hub"),
                    "kubeconfig": "./kubeconfigs/secondary",
                },
            },
            "operation": operation,
            "execution": case_input.get("execution", {}),
            "features": case_input.get("features", {}),
        }
    )
    summary = summarize_input_validation(results)
    if summary["passed"]:
        return True, ""
    return False, "\n".join(item["message"] for item in results if item["status"] in {"fail", "error"})


def _run_path_case(case_input: dict) -> tuple[bool, str]:
    try:
        validate_safe_path(case_input["path"])
    except ValidationError as exc:
        return False, str(exc)
    return True, ""


def _run_artifact_path_case(case_input: dict) -> tuple[bool, str]:
    try:
        validate_report_artifact_path(case_input["path"])
    except ValidationError as exc:
        return False, str(exc)
    return True, ""


@pytest.mark.parametrize(
    "case",
    [case for case in _load_cases() if case["kind"] != "hub_identity"],
    ids=lambda case: case["name"],
)
def test_collection_validation_matches_shared_parity_fixture(case: dict) -> None:
    if case["kind"] == "operation":
        passed, message = _run_operation_case(case["input"])
    elif case["kind"] == "hub_contexts":
        passed, message = _run_hub_context_case(case["input"])
    elif case["kind"] == "path":
        passed, message = _run_path_case(case["input"])
    elif case["kind"] == "artifact_path":
        passed, message = _run_artifact_path_case(case["input"])
    else:
        raise AssertionError(f"Unsupported validation parity case kind: {case['kind']}")

    expected = case["expected"]
    assert passed is expected["passed"], message
    if not expected["passed"]:
        assert expected["contains"] in message
