"""Shared validation parity fixture for the Python CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lib import validation
from lib.exceptions import SecurityValidationError, ValidationError
from lib.report_artifacts import validate_report_artifact_path
from lib.validation import InputValidator

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "validation_parity_cases.yml"

_EXACT_HUB_REFUSAL_MESSAGES = {
    "normal two hub rejects same context": (
        "Primary and secondary Kubernetes context names must differ for a normal two-hub switchover."
    ),
    "equal physical hub identities are refused": (
        "Primary and secondary hubs resolve to the same physical Kubernetes cluster. "
        "Refusing the normal two-hub switchover."
    ),
}
for _role in ("primary", "secondary"):
    _EXACT_HUB_REFUSAL_MESSAGES.update(
        {
            f"missing {_role} physical identity is refused": (
                f"Unable to verify the {_role} hub physical identity from the live kube-system Namespace UID. "
                "Refusing the normal two-hub switchover."
            ),
            f"malformed {_role} physical identity is refused": (
                f"Unable to verify the {_role} hub physical identity from the live kube-system Namespace UID. "
                "Refusing the normal two-hub switchover."
            ),
            f"non-string {_role} physical identity is refused": (
                f"Unable to verify the {_role} hub physical identity from the live kube-system Namespace UID. "
                "Refusing the normal two-hub switchover."
            ),
            f"empty {_role} physical identity is refused": (
                f"Unable to verify the {_role} hub physical identity from the live kube-system Namespace UID. "
                "Refusing the normal two-hub switchover."
            ),
            f"whitespace-only {_role} physical identity is refused": (
                f"Unable to verify the {_role} hub physical identity from the live kube-system Namespace UID. "
                "Refusing the normal two-hub switchover."
            ),
        }
    )


class MockArgs:
    """Minimal argparse-like object for CLI validation tests."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _load_cases() -> list[dict]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_shared_fixture_pins_every_distinct_hub_refusal_message() -> None:
    """The shared parity fixture covers every role/evidence failure exactly."""
    cases_by_name = {case["name"]: case for case in _load_cases()}

    assert set(_EXACT_HUB_REFUSAL_MESSAGES).issubset(cases_by_name)
    for name, expected_message in _EXACT_HUB_REFUSAL_MESSAGES.items():
        assert cases_by_name[name]["expected"]["message"] == expected_message


def _python_args(case_input: dict) -> MockArgs:
    operation = case_input.get("operation", {})
    execution = case_input.get("execution", {})
    features = case_input.get("features", {})
    argocd = features.get("argocd") or {}
    restore_only = bool(operation.get("restore_only", False))
    hub_contexts = case_input.get("hub_contexts") or {}

    return MockArgs(
        primary_context=hub_contexts.get("primary", None if restore_only else "primary-hub"),
        secondary_context=hub_contexts.get("secondary", "secondary-hub"),
        method=operation.get("method", "passive"),
        activation_method=operation.get("activation_method", "patch"),
        old_hub_action=operation.get("old_hub_action"),
        log_format="text",
        state_file=".state/switchover-state.json",
        decommission=bool(operation.get("decommission", False)),
        setup=bool(operation.get("setup", False)),
        restore_only=restore_only,
        validate_only=execution.get("mode") == "validate",
        dry_run=execution.get("mode") == "dry_run",
        argocd_manage=bool(argocd.get("manage", False)),
        argocd_resume_on_failure=bool(argocd.get("resume_on_failure", False)),
        argocd_resume_only=bool(argocd.get("resume_only", False)),
        admin_kubeconfig=operation.get("admin_kubeconfig"),
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


def _run_artifact_path_case(case_input: dict) -> tuple[bool, str]:
    try:
        validate_report_artifact_path(case_input["path"], "parity artifact")
    except (SecurityValidationError, ValidationError) as exc:
        return False, str(exc)
    return True, ""


def _run_hub_identity_case(case_input: dict) -> tuple[bool, str]:
    try:
        validation.validate_distinct_hub_identities(case_input["hub_identities"])
    except (SecurityValidationError, ValidationError) as exc:
        return False, str(exc)
    return True, ""


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["name"])
def test_python_validation_matches_shared_parity_fixture(case: dict) -> None:
    if case["kind"] == "operation":
        passed, message = _run_operation_case(case["input"])
    elif case["kind"] == "hub_contexts":
        passed, message = _run_operation_case(case["input"])
    elif case["kind"] == "hub_identity":
        passed, message = _run_hub_identity_case(case["input"])
    elif case["kind"] == "path":
        passed, message = _run_path_case(case["input"])
    elif case["kind"] == "artifact_path":
        passed, message = _run_artifact_path_case(case["input"])
    else:
        raise AssertionError(f"Unsupported validation parity case kind: {case['kind']}")

    expected = case["expected"]
    assert passed is expected["passed"], message
    if not expected["passed"]:
        if "message" in expected:
            assert message == expected["message"]
        else:
            assert expected["contains"] in message
