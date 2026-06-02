"""Input validation utilities for collection modules.

Ported and adapted from lib/validation.py for use in the Ansible Collection.
Operates on dictionary-structured collection params rather than argparse args.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import path_safety
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE,
    ARGOCD_RESUME_ON_FAILURE_VALIDATE_MODE_MESSAGE,
    VALIDATION_ACTIVATION_METHOD_CHOICES,
    VALIDATION_EXECUTION_MODE_CHOICES,
    VALIDATION_METHOD_CHOICES,
    VALIDATION_OLD_HUB_ACTION_CHOICES,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.path_safety import ValidationError

CONTEXT_NAME_MAX_LENGTH = 128
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/@]*[A-Za-z0-9]$|^[A-Za-z0-9]$")


def validate_context_name(context: str) -> None:
    """Validate a Kubernetes context name.

    Raises:
        ValidationError: If the context name is empty, too long, or contains invalid characters.
    """
    if not context:
        raise ValidationError("Context name cannot be empty")

    if len(context) > CONTEXT_NAME_MAX_LENGTH:
        raise ValidationError(
            f"Context name '{context}' exceeds maximum length of {CONTEXT_NAME_MAX_LENGTH} characters"
        )

    if not CONTEXT_NAME_PATTERN.match(context):
        raise ValidationError(
            f"Invalid context name '{context}'. "
            "Must consist of alphanumeric characters, '-', '_', '.', ':', '/', or '@', "
            "and must start and end with an alphanumeric character"
        )


def validate_safe_path(path: str) -> None:
    """Validate that a path is safe (no traversal, no shell metacharacters).

    Raises:
        ValidationError: If the path is empty or unsafe.
    """
    path_safety.validate_safe_path(path)


def validate_report_artifact_path(path: str) -> None:
    """Validate a report artifact path before controller-side writes."""
    path_safety.validate_report_artifact_path(path)


def validate_report_artifact_directory(path: str) -> None:
    """Validate a report artifact directory before the final filename is known."""
    path_safety.validate_report_artifact_directory(path)


def _validate_choice(value: str, valid_choices: Sequence[str], field_name: str) -> None:
    """Validate that a value is one of the allowed choices.

    Raises:
        ValidationError: If the value is not in the allowed choices.
    """
    if value not in valid_choices:
        choices_str = ", ".join(valid_choices)
        raise ValidationError(f"Invalid {field_name} '{value}'. Must be one of: {choices_str}")


def validate_operation_inputs(operation: dict, features: dict, execution: dict | None = None) -> dict:
    """Validate that operation and feature params form a supported combination.

    Returns:
        Normalized dict of validated values.

    Raises:
        ValidationError: If the combination is not supported.
    """
    if not isinstance(operation, dict):
        raise ValidationError("operation must be a dictionary")
    if not isinstance(features, dict):
        raise ValidationError("features must be a dictionary")
    if execution is None:
        execution = {}
    if not isinstance(execution, dict):
        raise ValidationError("execution must be a dictionary")

    min_mc = operation.get("min_managed_clusters")
    if min_mc is not None:
        try:
            min_mc = int(min_mc)
        except (TypeError, ValueError):
            raise ValidationError("min_managed_clusters must be an integer")
        if min_mc < 0:
            raise ValidationError("min_managed_clusters must be a non-negative integer")

    restore_only = operation.get("restore_only", False)
    activation_method = operation.get("activation_method", "patch")
    old_hub_action = operation.get("old_hub_action", "secondary")
    disable_observability_on_secondary = features.get("disable_observability_on_secondary", False)
    argocd = features.get("argocd", {})
    if argocd is None:
        argocd = {}
    if not isinstance(argocd, dict):
        raise ValidationError("features.argocd must be a dictionary")
    argocd_manage = argocd.get("manage", False)
    argocd_resume_on_failure = argocd.get("resume_on_failure", False)
    execution_mode = execution.get("mode", "execute")

    _validate_choice(activation_method, VALIDATION_ACTIVATION_METHOD_CHOICES, "activation_method")
    _validate_choice(execution_mode, VALIDATION_EXECUTION_MODE_CHOICES, "execution.mode")

    if argocd_resume_on_failure and not argocd_manage:
        raise ValidationError(ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE)
    if argocd_resume_on_failure and execution_mode == "validate":
        raise ValidationError(ARGOCD_RESUME_ON_FAILURE_VALIDATE_MODE_MESSAGE)

    if disable_observability_on_secondary and old_hub_action != "secondary":
        raise ValidationError(
            "disable_observability_on_secondary requires old_hub_action=secondary so the old hub remains available"
        )

    if restore_only:
        method = operation.get("method", "full")
        old_hub_action = operation.get("old_hub_action", "none")

        if method != "full":
            raise ValidationError("restore_only requires method=full (passive sync needs a live primary hub)")
        if old_hub_action != "none":
            raise ValidationError("restore_only requires old_hub_action=none (no old hub to manage)")

        return {
            "restore_only": True,
            "method": "full",
            "old_hub_action": "none",
            "activation_method": activation_method,
            "argocd_manage": argocd_manage,
            "argocd_resume_on_failure": argocd_resume_on_failure,
        }

    method = operation.get("method", "passive")

    _validate_choice(method, VALIDATION_METHOD_CHOICES, "method")
    _validate_choice(old_hub_action, VALIDATION_OLD_HUB_ACTION_CHOICES, "old_hub_action")

    if method != "passive" and activation_method == "restore":
        raise ValidationError(
            "activation_method=restore requires method=passive; full restore does not use a passive sync restore"
        )

    return {
        "restore_only": False,
        "method": method,
        "old_hub_action": old_hub_action,
        "activation_method": activation_method,
        "argocd_manage": argocd_manage,
        "argocd_resume_on_failure": argocd_resume_on_failure,
    }
