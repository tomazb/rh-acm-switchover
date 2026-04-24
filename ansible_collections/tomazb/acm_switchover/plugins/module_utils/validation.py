"""Input validation utilities for collection modules.

Ported and adapted from lib/validation.py for use in the Ansible Collection.
Operates on dictionary-structured collection params rather than argparse args.
"""

from __future__ import annotations

import os
import re

CONTEXT_NAME_MAX_LENGTH = 128
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/@]*[A-Za-z0-9]$|^[A-Za-z0-9]$")

UNSAFE_PATH_CHARS = ["$", "{", "}", "|", "&", ";", "<", ">", "`"]


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

    A leading ``~/`` is permitted so that home-relative kubeconfig paths
    such as ``~/.kube/config`` work out of the box.  A bare ``~`` or ``~``
    appearing anywhere else in the path is still rejected.

    Raises:
        ValidationError: If the path is empty or unsafe.
    """
    if not path:
        raise ValidationError("Path cannot be empty")

    if ".." in path.split("/"):
        raise ValidationError(f"Path traversal attempt detected in '{path}'. The '..' sequence is not allowed.")

    # Strip a leading ~/ before the metacharacter scan so that the common
    # ~/.kube/config idiom is accepted, but ~/foo~bar or mid-path ~ is not.
    scan_path = path[2:] if path.startswith("~/") else path
    if "~" in scan_path:
        raise ValidationError(
            f"Path '{path}' contains unsafe characters. " f"Disallowed: ~, {', '.join(UNSAFE_PATH_CHARS)}"
        )

    if any(char in path for char in UNSAFE_PATH_CHARS):
        raise ValidationError(
            f"Path '{path}' contains unsafe characters. " f"Disallowed: ~, {', '.join(UNSAFE_PATH_CHARS)}"
        )

    # Validate absolute paths against allowed prefixes with symlink-aware resolution
    if path.startswith("/"):
        if os.path.exists(path):
            resolved_path = os.path.realpath(path)
        else:
            parent = os.path.dirname(path)
            if parent and os.path.exists(parent):
                resolved_path = os.path.join(os.path.realpath(parent), os.path.basename(path))
            else:
                raise ValidationError(
                    f"Absolute path '{path}' has a non-existent parent directory. "
                    "Create the parent directory in an allowed location before using this path."
                )

        home_dir = os.path.expanduser("~")
        allowed_prefixes = ["/tmp/", "/var/", os.path.realpath(home_dir) + "/"]
        cwd = os.getcwd()
        if cwd:
            allowed_prefixes.append(os.path.realpath(cwd) + "/")

        if not any(resolved_path.startswith(prefix) for prefix in allowed_prefixes):
            raise ValidationError(
                f"Absolute path '{path}' is outside allowed directories. "
                f"Allowed prefixes: /tmp/, /var/, {home_dir}/"
            )


def _validate_choice(value: str, valid_choices: list[str], field_name: str) -> None:
    """Validate that a value is one of the allowed choices.

    Raises:
        ValidationError: If the value is not in the allowed choices.
    """
    if value not in valid_choices:
        choices_str = ", ".join(valid_choices)
        raise ValidationError(f"Invalid {field_name} '{value}'. Must be one of: {choices_str}")


def validate_operation_inputs(operation: dict, features: dict) -> dict:
    """Validate that operation and feature params form a supported combination.

    Returns:
        Normalized dict of validated values.

    Raises:
        ValidationError: If the combination is not supported.
    """
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
    argocd_manage = argocd.get("manage", False)

    _validate_choice(activation_method, ["patch", "restore"], "activation_method")

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
        }

    method = operation.get("method", "passive")

    _validate_choice(method, ["passive", "full"], "method")
    _validate_choice(old_hub_action, ["secondary", "decommission", "none"], "old_hub_action")

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
    }
