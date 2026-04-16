"""Input validation utilities for collection modules.

Ported and adapted from lib/validation.py for use in the Ansible Collection.
Operates on dictionary-structured collection params rather than argparse args.
"""

from __future__ import annotations

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


def validate_operation_inputs(operation: dict, features: dict) -> dict:
    """Validate that operation and feature params form a supported combination.

    Returns:
        Normalized dict of validated values.

    Raises:
        ValidationError: If the combination is not supported.
    """
    restore_only = operation.get("restore_only", False)
    activation_method = operation.get("activation_method", "patch")
    argocd = features.get("argocd", {})
    argocd_manage = argocd.get("manage", False)

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

    if method != "passive" and activation_method == "restore":
        raise ValidationError(
            "activation_method=restore requires method=passive; full restore does not use a passive sync restore"
        )

    return {
        "restore_only": False,
        "method": method,
        "activation_method": activation_method,
        "argocd_manage": argocd_manage,
    }
