"""Property-based tests for validation behavior and form-factor parity."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from hypothesis import example, given

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    VALIDATION_ACTIVATION_METHOD_CHOICES as COLLECTION_ACTIVATION_METHOD_CHOICES,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    VALIDATION_METHOD_CHOICES as COLLECTION_METHOD_CHOICES,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    VALIDATION_OLD_HUB_ACTION_CHOICES as COLLECTION_OLD_HUB_ACTION_CHOICES,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError as CollectionValidationError,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    _validate_choice as collection_validate_choice,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    validate_context_name as collection_validate_context_name,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    validate_operation_inputs,
)
from lib.constants import VALIDATION_ACTIVATION_METHOD_CHOICES as PYTHON_ACTIVATION_METHOD_CHOICES
from lib.constants import VALIDATION_METHOD_CHOICES as PYTHON_METHOD_CHOICES
from lib.constants import VALIDATION_OLD_HUB_ACTION_CHOICES as PYTHON_OLD_HUB_ACTION_CHOICES
from lib.exceptions import ValidationError as PythonValidationError
from lib.validation import InputValidator
from tests.properties.strategies import (
    ASCII_ALNUM,
    LABEL_INTERIOR,
    LOWER_ALNUM,
    SAFE_IDENTIFIER_CHARS,
    activation_method_candidates,
    context_identifier_inputs,
    context_name_candidates,
    kubernetes_label_key_candidates,
    kubernetes_label_value_candidates,
    kubernetes_name_candidates,
    kubernetes_namespace_candidates,
    method_candidates,
    non_empty_string_candidates,
    old_hub_action_candidates,
)

CONTEXT_ALLOWED = set(ASCII_ALNUM + "_.:-/@")
DNS_ALLOWED = set(LOWER_ALNUM + "-")
LABEL_ALLOWED = set(LABEL_INTERIOR)


def _python_outcome(validator: Callable[[Any], Any], candidate: Any) -> tuple[bool, Any]:
    """Classify only the Python validator's documented rejection exception."""
    try:
        result = validator(candidate)
    except PythonValidationError:
        return False, None
    return True, result


def _collection_outcome(validator: Callable[[Any], Any], candidate: Any) -> tuple[bool, Any]:
    """Classify only the collection validator's documented rejection exception."""
    try:
        result = validator(candidate)
    except CollectionValidationError:
        return False, None
    return True, result


def _bounded_components_sound(
    candidate: str,
    *,
    max_length: int,
    separator: str | None,
    allowed: set[str],
    boundary: set[str],
) -> bool:
    """Independent character/boundary oracle, intentionally not a regex copy."""
    if not 1 <= len(candidate) <= max_length:
        return False
    components = candidate.split(separator) if separator else [candidate]
    return all(
        component
        and component[0] in boundary
        and component[-1] in boundary
        and all(character in allowed for character in component)
        for component in components
    )


def _kubernetes_name_sound(candidate: str) -> bool:
    return _bounded_components_sound(
        candidate,
        max_length=253,
        separator=".",
        allowed=DNS_ALLOWED,
        boundary=set(LOWER_ALNUM),
    )


def _namespace_sound(candidate: str) -> bool:
    return _bounded_components_sound(
        candidate,
        max_length=63,
        separator=None,
        allowed=DNS_ALLOWED,
        boundary=set(LOWER_ALNUM),
    ) and candidate[0] in set("abcdefghijklmnopqrstuvwxyz")


def _label_key_sound(candidate: str) -> bool:
    """Model the shipped total-key-length and component-shape behavior.

    The implementation applies one 63-character limit to the whole key and
    uses the same component shape on both sides of an optional slash.  It does
    not independently enforce Kubernetes' broader DNS-prefix length rules.
    """
    if candidate.count("/") > 1:
        return False
    return _bounded_components_sound(
        candidate,
        max_length=63,
        separator="/",
        allowed=LABEL_ALLOWED,
        boundary=set(ASCII_ALNUM),
    )


def _label_value_sound(candidate: str | None) -> bool:
    if candidate is None or len(candidate) > 63:
        return False
    if candidate == "":
        return True
    return (
        candidate[0] in set(ASCII_ALNUM)
        and candidate[-1] in set(ASCII_ALNUM)
        and all(character in LABEL_ALLOWED for character in candidate)
    )


def _context_name_sound(candidate: str) -> bool:
    return (
        1 <= len(candidate) <= 128
        and candidate[0] in set(ASCII_ALNUM)
        and candidate[-1] in set(ASCII_ALNUM)
        and all(character in CONTEXT_ALLOWED for character in candidate)
    )


def _assert_python_contract(
    *,
    validator_name: str,
    validator: Callable[[Any], None],
    candidate: Any,
    expected_acceptance: bool,
) -> None:
    accepted, result = _python_outcome(validator, candidate)
    assert accepted is expected_acceptance, (
        f"validator={validator_name}, candidate={candidate!r}, " f"accepted={accepted}, expected={expected_acceptance}"
    )
    if accepted:
        assert result is None, f"validator={validator_name}, candidate={candidate!r}, returned={result!r}"


@pytest.mark.property
@example(candidate="a")
@example(candidate="a" * 63)
@example(candidate="a" * 253)
@example(candidate="a" * 254)
@example(candidate="-name")
@example(candidate="name.")
@given(candidate=kubernetes_name_candidates())
def test_kubernetes_name_acceptance_is_sound(candidate: str) -> None:
    _assert_python_contract(
        validator_name="validate_kubernetes_name",
        validator=InputValidator.validate_kubernetes_name,
        candidate=candidate,
        expected_acceptance=_kubernetes_name_sound(candidate),
    )


@pytest.mark.property
@example(candidate="a")
@example(candidate="a" * 63)
@example(candidate="a" * 64)
@example(candidate="1namespace")
@example(candidate="namespace-")
@given(candidate=kubernetes_namespace_candidates())
def test_kubernetes_namespace_acceptance_is_sound(candidate: str) -> None:
    _assert_python_contract(
        validator_name="validate_kubernetes_namespace",
        validator=InputValidator.validate_kubernetes_namespace,
        candidate=candidate,
        expected_acceptance=_namespace_sound(candidate),
    )


@pytest.mark.property
@example(candidate="")
@example(candidate="a")
@example(candidate="a" * 63)
@example(candidate="a" * 64)
@example(candidate="app.kubernetes.io/name")
@example(candidate="prefix/name")
@given(candidate=kubernetes_label_key_candidates())
def test_kubernetes_label_key_matches_documented_implementation(candidate: str) -> None:
    _assert_python_contract(
        validator_name="validate_kubernetes_label_key",
        validator=InputValidator.validate_kubernetes_label_key,
        candidate=candidate,
        expected_acceptance=_label_key_sound(candidate),
    )


@pytest.mark.property
@example(candidate=None)
@example(candidate="")
@example(candidate="a")
@example(candidate="a" * 63)
@example(candidate="a" * 64)
@example(candidate="-value")
@given(candidate=kubernetes_label_value_candidates())
def test_kubernetes_label_value_matches_documented_implementation(candidate: str | None) -> None:
    _assert_python_contract(
        validator_name="validate_kubernetes_label_value",
        validator=InputValidator.validate_kubernetes_label_value,
        candidate=candidate,
        expected_acceptance=_label_value_sound(candidate),
    )


@pytest.mark.property
@example(candidate="a")
@example(candidate="default/api.example.com:6443/admin")
@example(candidate="a" * 128)
@example(candidate="a" * 129)
@example(candidate="/admin")
@example(candidate="admin/")
@given(candidate=context_name_candidates())
def test_context_name_validators_agree(candidate: str) -> None:
    python_accepted, python_result = _python_outcome(InputValidator.validate_context_name, candidate)
    collection_accepted, collection_result = _collection_outcome(collection_validate_context_name, candidate)
    expected = _context_name_sound(candidate)

    assert (python_accepted, collection_accepted) == (expected, expected), (
        f"validator=context_name, candidate={candidate!r}, expected={expected}, "
        f"python_accepted={python_accepted}, collection_accepted={collection_accepted}"
    )
    if python_accepted:
        assert python_result is None
        assert collection_result is None


@pytest.mark.property
@example(candidate="value")
@example(candidate="value with spaces")
@example(candidate="")
@example(candidate=" \t\n ")
@given(candidate=non_empty_string_candidates())
def test_non_empty_string_rejects_only_empty_or_whitespace(candidate: str) -> None:
    _assert_python_contract(
        validator_name="validate_non_empty_string",
        validator=lambda value: InputValidator.validate_non_empty_string(value, "property field"),
        candidate=candidate,
        expected_acceptance=bool(candidate and candidate.strip()),
    )


def _collection_choice_path(field: str, candidate: str) -> None:
    """Vary one explicit choice while keeping every unrelated field valid."""
    operation = {
        "restore_only": False,
        "method": "passive",
        "activation_method": "patch",
        "old_hub_action": "secondary",
        "min_managed_clusters": 0,
    }
    operation[field] = candidate
    validate_operation_inputs(
        operation=operation,
        features={
            "disable_observability_on_secondary": False,
            "argocd": {"manage": False, "resume_on_failure": False},
        },
        execution={"mode": "execute"},
    )


def _assert_choice_parity(
    *,
    field: str,
    candidate: str,
    python_choices: Sequence[str],
    collection_choices: Sequence[str],
    python_validator: Callable[[str], None],
) -> None:
    python_expected = candidate in python_choices
    collection_expected = candidate in collection_choices
    python_accepted, python_result = _python_outcome(python_validator, candidate)
    direct_accepted, direct_result = _collection_outcome(
        lambda value: collection_validate_choice(value, collection_choices, field),
        candidate,
    )
    adapter_accepted, adapter_result = _collection_outcome(
        lambda value: _collection_choice_path(field, value),
        candidate,
    )

    assert (python_accepted, direct_accepted, adapter_accepted) == (
        python_expected,
        collection_expected,
        collection_expected,
    ), (
        f"field={field}, candidate={candidate!r}, python_expected={python_expected}, "
        f"collection_expected={collection_expected}, "
        f"python_accepted={python_accepted}, collection_direct_accepted={direct_accepted}, "
        f"collection_adapter_accepted={adapter_accepted}"
    )
    assert python_accepted is adapter_accepted, (
        f"field={field}, candidate={candidate!r}, python_accepted={python_accepted}, "
        f"collection_accepted={adapter_accepted}"
    )
    if python_accepted:
        assert python_result is None
    if direct_accepted:
        assert direct_result is None
        assert adapter_result is None


@pytest.mark.property
@example(candidate="passive")
@example(candidate="full")
@example(candidate="PASSIVE")
@example(candidate="")
@given(candidate=method_candidates())
def test_method_choice_validators_agree(candidate: str) -> None:
    _assert_choice_parity(
        field="method",
        candidate=candidate,
        python_choices=PYTHON_METHOD_CHOICES,
        collection_choices=COLLECTION_METHOD_CHOICES,
        python_validator=InputValidator.validate_cli_method,
    )


@pytest.mark.property
@example(candidate="patch")
@example(candidate="restore")
@example(candidate="PATCH")
@example(candidate="")
@given(candidate=activation_method_candidates())
def test_activation_method_choice_validators_agree(candidate: str) -> None:
    _assert_choice_parity(
        field="activation_method",
        candidate=candidate,
        python_choices=PYTHON_ACTIVATION_METHOD_CHOICES,
        collection_choices=COLLECTION_ACTIVATION_METHOD_CHOICES,
        python_validator=InputValidator.validate_cli_activation_method,
    )


@pytest.mark.property
@example(candidate="secondary")
@example(candidate="decommission")
@example(candidate="none")
@example(candidate="SECONDARY")
@example(candidate="")
@given(candidate=old_hub_action_candidates())
def test_old_hub_action_choice_validators_agree(candidate: str) -> None:
    _assert_choice_parity(
        field="old_hub_action",
        candidate=candidate,
        python_choices=PYTHON_OLD_HUB_ACTION_CHOICES,
        collection_choices=COLLECTION_OLD_HUB_ACTION_CHOICES,
        python_validator=InputValidator.validate_cli_old_hub_action,
    )


@pytest.mark.property
@example(value="")
@example(value="default/api.example.com:6443/admin")
@example(value="my context")
@given(value=context_identifier_inputs())
def test_context_identifier_sanitizer_is_idempotent_and_safe(value: str) -> None:
    sanitized = InputValidator.sanitize_context_identifier(value)

    assert (
        InputValidator.sanitize_context_identifier(sanitized) == sanitized
    ), f"sanitizer input={value!r}, first_result={sanitized!r}"
    assert sanitized, f"sanitizer input={value!r} produced an empty result"
    assert set(sanitized) <= set(SAFE_IDENTIFIER_CHARS), f"sanitizer input={value!r}, unsafe_output={sanitized!r}"
    if value == "":
        assert sanitized == "unknown"
