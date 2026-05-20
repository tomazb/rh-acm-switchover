"""Input validation utilities for collection modules.

Ported and adapted from lib/validation.py for use in the Ansible Collection.
Operates on dictionary-structured collection params rather than argparse args.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE,
    ARGOCD_RESUME_ON_FAILURE_VALIDATE_MODE_MESSAGE,
    VALIDATION_ACTIVATION_METHOD_CHOICES,
    VALIDATION_EXECUTION_MODE_CHOICES,
    VALIDATION_METHOD_CHOICES,
    VALIDATION_OLD_HUB_ACTION_CHOICES,
)

CONTEXT_NAME_MAX_LENGTH = 128
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/@]*[A-Za-z0-9]$|^[A-Za-z0-9]$")

UNSAFE_PATH_CHARS = ["~", "$", "{", "}", "|", "&", ";", "<", ">", "`"]


class ValidationError(Exception):
    """Raised when input validation fails."""


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
    if not path:
        raise ValidationError("Path cannot be empty")

    if ".." in path.split("/"):
        raise ValidationError(f"Path traversal attempt detected in '{path}'. The '..' sequence is not allowed.")

    if any(char in path for char in UNSAFE_PATH_CHARS):
        raise ValidationError(
            f"Path '{path}' contains unsafe characters. " f"Disallowed: {', '.join(UNSAFE_PATH_CHARS)}"
        )

    # Validate absolute paths against allowed prefixes with symlink-aware resolution
    if path.startswith("/"):
        if os.path.exists(path):
            resolved_path = os.path.realpath(path)
        else:
            ancestor = path
            missing_parts: list[str] = []
            while ancestor and not os.path.exists(ancestor):
                ancestor, name = os.path.split(ancestor)
                if name:
                    missing_parts.insert(0, name)

            if not ancestor or not os.path.exists(ancestor):
                raise ValidationError(f"Absolute path '{path}' cannot be resolved against an existing directory.")
            if not os.path.isdir(ancestor):
                raise ValidationError(f"Absolute path '{path}' resolves through a non-directory ancestor.")

            resolved_path = os.path.join(os.path.realpath(ancestor), *missing_parts)

        home_dir = os.path.expanduser("~")
        allowed_roots = ["/tmp", "/var", os.path.realpath(home_dir)]
        cwd = os.getcwd()
        if cwd:
            allowed_roots.append(os.path.realpath(cwd))

        if not any(os.path.commonpath([resolved_path, allowed_root]) == allowed_root for allowed_root in allowed_roots):
            raise ValidationError(
                f"Absolute path '{path}' is outside allowed directories. "
                f"Allowed prefixes: /tmp/, /var/, {home_dir}/"
            )


def _commonpath_is_parent(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def _allowed_artifact_root(path: str) -> str:
    """Return the most specific allowed root for an absolute artifact path."""
    home_dir = os.path.realpath(os.path.expanduser("~"))
    allowed_roots = ["/tmp", "/var", home_dir]
    cwd = os.getcwd()
    if cwd:
        allowed_roots.append(os.path.realpath(cwd))

    matching_roots = [root for root in allowed_roots if _commonpath_is_parent(os.path.abspath(path), root)]
    if not matching_roots:
        raise ValidationError(
            f"Artifact path '{path}' is outside allowed directories. " f"Allowed prefixes: /tmp/, /var/, {home_dir}/"
        )
    return max(matching_roots, key=len)


def _reject_symlink_escape(path: str, root: str) -> None:
    root = os.path.realpath(root)
    absolute_path = os.path.abspath(path)
    try:
        relative_path = os.path.relpath(absolute_path, root)
    except ValueError:
        raise ValidationError(f"Artifact path '{path}' resolves outside the allowed root '{root}'.")
    if relative_path == os.pardir or relative_path.startswith(os.pardir + os.sep):
        raise ValidationError(f"Artifact path '{path}' resolves outside the allowed root '{root}'.")

    current = root
    parts = [part for part in relative_path.split(os.sep) if part and part != os.curdir]
    for part in parts[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current) and not _commonpath_is_parent(os.path.realpath(current), root):
            raise ValidationError(f"Artifact path '{path}' contains a symlink that escapes '{root}'.")


def validate_report_artifact_path(path: str) -> None:
    """Validate a report artifact path before controller-side writes."""
    validate_safe_path(path)

    if os.path.islink(path):
        raise ValidationError(f"Artifact path '{path}' must not be a symlink.")

    if os.path.isabs(path):
        root = _allowed_artifact_root(path)
        absolute_path = path
    else:
        root = os.path.realpath(os.getcwd())
        absolute_path = os.path.abspath(path)

    _reject_symlink_escape(absolute_path, root)
    parent = os.path.dirname(absolute_path) or root
    if os.path.exists(parent) and not _commonpath_is_parent(os.path.realpath(parent), root):
        raise ValidationError(f"Artifact directory '{parent}' resolves outside '{root}'.")


def validate_report_artifact_directory(path: str) -> None:
    """Validate a report artifact directory before the final filename is known."""
    validate_report_artifact_path(os.path.join(path, ".artifact-path-check"))


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
