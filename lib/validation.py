#!/usr/bin/env python3
"""
Input validation utilities for ACM switchover automation.

This module provides comprehensive validation for CLI arguments, Kubernetes
resource names, context names, and other external inputs to improve security
and reliability.

Features:
- Kubernetes resource name validation (DNS-1123 subdomain rules)
- Kubernetes namespace validation (DNS-1123 label rules)
- Kubernetes label validation
- Context name validation
- CLI argument validation
- Filesystem path validation
- Comprehensive error handling with descriptive messages
"""

import logging
import os
import re
from typing import Pattern

from lib.exceptions import SecurityValidationError, ValidationError

logger = logging.getLogger("acm_switchover")

# Kubernetes resource name validation patterns
# Based on Kubernetes naming conventions: https://kubernetes.io/docs/concepts/overview/working-with-objects/names/
# DNS-1123 subdomain format: contains only lowercase alphanumeric characters, '-' or '.',
# starts with an alphanumeric character, ends with an alphanumeric character
K8S_NAME_PATTERN: Pattern[str] = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
K8S_NAME_MAX_LENGTH = 253

# Kubernetes namespace validation pattern
# RFC 1123 label format: contains only lowercase alphanumeric characters or '-',
# starts with an alphabetic character (Kubernetes requires this), ends with an alphanumeric character
K8S_NAMESPACE_PATTERN: Pattern[str] = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
K8S_NAMESPACE_MAX_LENGTH = 63

# Kubernetes label validation patterns
# Label keys: optional prefix and name, separated by a slash (/),
# where prefix must be a DNS subdomain and name must be a DNS label
K8S_LABEL_KEY_PATTERN: Pattern[str] = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?$"
    r"|^[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?/[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?$"
)
K8S_LABEL_VALUE_PATTERN: Pattern[str] = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-_.]*[a-zA-Z0-9])?$")
K8S_LABEL_MAX_LENGTH = 63

# Context name validation pattern (more permissive than K8s names)
# Allows alphanumeric, hyphens, underscores, dots, forward slashes, and colons
# This accommodates default oc login contexts like 'admin/api-ci-aws' or 'default/api.example.com:6443/admin'
CONTEXT_NAME_PATTERN: Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/]*[A-Za-z0-9]$|^[A-Za-z0-9]$")
CONTEXT_NAME_MAX_LENGTH = 128


class InputValidator:
    """Comprehensive input validation for ACM switchover."""

    @staticmethod
    def validate_kubernetes_name(name: str, resource_type: str = "resource") -> None:
        """
        Validate Kubernetes resource name according to DNS-1123 subdomain rules.

        Args:
            name: The name to validate
            resource_type: Type of resource for error messages

        Raises:
            ValidationError: If name is invalid
        """
        if not name:
            raise ValidationError(f"{resource_type} name cannot be empty")

        if len(name) > K8S_NAME_MAX_LENGTH:
            raise ValidationError(
                f"{resource_type} name '{name}' exceeds maximum length of {K8S_NAME_MAX_LENGTH} characters"
            )

        if not K8S_NAME_PATTERN.match(name):
            raise ValidationError(
                f"Invalid {resource_type} name '{name}'. "
                f"Must consist of lowercase alphanumeric characters, '-', or '.', "
                f"must start and end with an alphanumeric character (DNS-1123 subdomain)"
            )

    @staticmethod
    def validate_kubernetes_namespace(namespace: str) -> None:
        """
        Validate Kubernetes namespace name according to DNS-1123 label rules.

        Args:
            namespace: The namespace to validate

        Raises:
            ValidationError: If namespace is invalid
        """
        if not namespace:
            raise ValidationError("Namespace cannot be empty")

        if len(namespace) > K8S_NAMESPACE_MAX_LENGTH:
            raise ValidationError(
                f"Namespace '{namespace}' exceeds maximum length of {K8S_NAMESPACE_MAX_LENGTH} characters"
            )

        if not K8S_NAMESPACE_PATTERN.match(namespace):
            raise ValidationError(
                f"Invalid namespace '{namespace}'. "
                f"Must consist of lower case alphanumeric characters or '-', "
                f"and must start and end with an alphanumeric character"
            )

    @staticmethod
    def validate_kubernetes_label_key(key: str) -> None:
        """
        Validate Kubernetes label key.

        Args:
            key: The label key to validate

        Raises:
            ValidationError: If label key is invalid
        """
        if not key:
            raise ValidationError("Label key cannot be empty")

        if len(key) > K8S_LABEL_MAX_LENGTH:
            raise ValidationError(f"Label key '{key}' exceeds maximum length of {K8S_LABEL_MAX_LENGTH} characters")

        if not K8S_LABEL_KEY_PATTERN.match(key):
            raise ValidationError(
                f"Invalid label key '{key}'. "
                f"Must be an optional prefix and name, separated by a slash (/), "
                f"where prefix must be a DNS subdomain and name must be a DNS label"
            )

    @staticmethod
    def validate_kubernetes_label_value(value: str) -> None:
        """
        Validate Kubernetes label value.

        Args:
            value: The label value to validate

        Raises:
            ValidationError: If label value is invalid
        """
        # None is not allowed, but empty string is valid per K8s spec
        if value is None:
            raise ValidationError("Label value cannot be None")

        if len(value) > K8S_LABEL_MAX_LENGTH:
            raise ValidationError(f"Label value '{value}' exceeds maximum length of {K8S_LABEL_MAX_LENGTH} characters")

        # Empty string is valid, only check pattern for non-empty values
        if value and not K8S_LABEL_VALUE_PATTERN.match(value):
            raise ValidationError(
                f"Invalid label value '{value}'. "
                f"Must be 63 characters or less and must be empty or begin and end with an alphanumeric character"
            )

    @staticmethod
    def validate_context_name(context: str) -> None:
        """
        Validate Kubernetes context name.

        Args:
            context: The context name to validate

        Raises:
            ValidationError: If context name is invalid
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
                f"Must consist of alphanumeric characters, '-', '_', '.', ':', or '/', "
                f"and must start and end with an alphanumeric character"
            )

    @staticmethod
    def _validate_choice(value: str, valid_choices: list, field_name: str) -> None:
        """Validate that a value is one of the allowed choices.

        Args:
            value: The value to validate
            valid_choices: List of valid choices
            field_name: Name of the field for error messages

        Raises:
            ValidationError: If value is not in valid_choices
        """
        if value not in valid_choices:
            raise ValidationError(f"Invalid {field_name} '{value}'. Must be one of: {', '.join(valid_choices)}")

    @staticmethod
    def validate_cli_method(method: str) -> None:
        """Validate CLI method argument."""
        InputValidator._validate_choice(method, ["passive", "full"], "method")

    @staticmethod
    def validate_cli_old_hub_action(action: str) -> None:
        """Validate CLI old-hub-action argument."""
        InputValidator._validate_choice(action, ["secondary", "decommission", "none"], "old-hub-action")

    @staticmethod
    def validate_cli_activation_method(method: str) -> None:
        """Validate CLI activation-method argument."""
        InputValidator._validate_choice(method, ["patch", "restore"], "activation-method")

    @staticmethod
    def validate_cli_log_format(log_format: str) -> None:
        """Validate CLI log format argument."""
        InputValidator._validate_choice(log_format, ["text", "json"], "log format")

    @staticmethod
    def validate_non_empty_string(value: str, field_name: str) -> None:
        """
        Validate that a string is not empty or whitespace-only.

        Args:
            value: The string to validate
            field_name: Name of the field for error messages

        Raises:
            ValidationError: If string is empty or whitespace-only
        """
        if not value or not value.strip():
            raise ValidationError(f"{field_name} cannot be empty or whitespace-only")

    @staticmethod
    def validate_safe_filesystem_path(path: str, field_name: str) -> None:
        """
        Validate that a path is safe for filesystem operations.

        Args:
            path: The path to validate
            field_name: Name of the field for error messages

        Raises:
            SecurityValidationError: If path contains unsafe characters or patterns
            ValidationError: If path is empty
        """
        if not path:
            raise ValidationError(f"{field_name} path cannot be empty")

        # Prevent path traversal by checking for '..' as a path component
        if ".." in path.split("/"):
            raise SecurityValidationError(
                f"SECURITY: Path traversal attempt detected in {field_name} path '{path}'. "
                f"The '..' sequence is not allowed as a path component."
            )

        # Prevent command injection and other unsafe patterns
        unsafe_chars = ["~", "$", "{", "}", "|", "&", ";", "<", ">", "`"]
        if any(char in path for char in unsafe_chars):
            raise SecurityValidationError(
                f"SECURITY: Invalid characters in {field_name} path '{path}'. "
                f"Path contains unsafe characters that could be used for command injection. "
                f"Disallowed patterns: {', '.join(unsafe_chars)}."
            )

        # Allow absolute paths in safe directories or workspace-relative paths
        # Permit /tmp, /var, and absolute paths under current working directory or $HOME
        if path.startswith("/"):
            # Resolve symlinks to prevent bypass via symlink chains
            # Only resolve if path exists; for new files, validate the parent directory
            if os.path.exists(path):
                resolved_path = os.path.realpath(path)
            else:
                # For non-existent paths, require the parent directory to exist
                # This prevents symlink-based path bypasses via non-existent parent directories
                parent = os.path.dirname(path)
                if parent and os.path.exists(parent):
                    resolved_path = os.path.join(os.path.realpath(parent), os.path.basename(path))
                else:
                    raise SecurityValidationError(
                        f"SECURITY: Absolute path '{path}' for {field_name} has a non-existent parent directory. "
                        f"Creating files in non-existent absolute directories is not allowed to prevent symlink-based path bypasses. "
                        f"Create the parent directory in an allowed location (/tmp, /var, workspace root, or home directory) before using this path."
                    )

            safe_prefixes = ["/tmp/", "/var/"]  # nosec B108 - path validation, not temp file usage
            # Allow paths under current working directory
            cwd = os.getcwd()
            if cwd:
                safe_prefixes.append(os.path.realpath(cwd) + "/")
            # Allow paths under home directory
            home = os.path.expanduser("~")
            if home and home != "~":
                safe_prefixes.append(os.path.realpath(home) + "/")

            if not any(resolved_path.startswith(prefix) for prefix in safe_prefixes):
                raise SecurityValidationError(
                    f"SECURITY: Absolute path '{path}' is not allowed for {field_name}. "
                    f"Use relative paths or paths within /tmp, /var, workspace root, or home directory to prevent filesystem escape attacks."
                )

    @staticmethod
    def sanitize_context_identifier(value: str) -> str:
        """
        Sanitize context string to be filesystem friendly.

        Args:
            value: The context string to sanitize

        Returns:
            Sanitized string safe for filesystem use
        """
        if not value:
            return "unknown"

        # Replace any character that's not alphanumeric, dot, underscore, or dash with underscore
        return re.sub(r"[^A-Za-z0-9._-]", "_", value)

    @staticmethod
    def validate_all_cli_args(args: object) -> None:
        """
        Validate all CLI arguments comprehensively.

        Args:
            args: Parsed CLI arguments object

        Raises:
            ValidationError: If any argument validation fails

        Note:
            TODO: This function has high cyclomatic complexity (C901: 18).
            Consider refactoring into smaller validation functions grouped by
            argument type (contexts, methods, file paths, etc.) in a future PR
            focused on maintainability improvements.
        """
        # Validate required context arguments
        if hasattr(args, "primary_context") and args.primary_context:
            InputValidator.validate_context_name(args.primary_context)
            InputValidator.validate_non_empty_string(args.primary_context, "primary-context")

        if hasattr(args, "secondary_context") and args.secondary_context:
            InputValidator.validate_context_name(args.secondary_context)
            InputValidator.validate_non_empty_string(args.secondary_context, "secondary-context")

        # Validate method
        if hasattr(args, "method") and args.method:
            InputValidator.validate_cli_method(args.method)

        # Validate activation method
        if hasattr(args, "activation_method") and args.activation_method:
            InputValidator.validate_cli_activation_method(args.activation_method)

        # Validate old-hub-action
        if hasattr(args, "old_hub_action") and args.old_hub_action:
            InputValidator.validate_cli_old_hub_action(args.old_hub_action)

        # Validate log format
        if hasattr(args, "log_format") and args.log_format:
            InputValidator.validate_cli_log_format(args.log_format)

        # Validate state file path if provided
        if hasattr(args, "state_file") and args.state_file:
            InputValidator.validate_safe_filesystem_path(args.state_file, "state-file")

        # Validate that secondary context is provided when not in decommission or setup mode
        is_decommission = hasattr(args, "decommission") and args.decommission
        is_setup = hasattr(args, "setup") and args.setup
        if not is_decommission and not is_setup:
            if hasattr(args, "secondary_context") and not args.secondary_context:
                raise ValidationError("secondary-context is required for switchover operations")

        # Validate activation-method is only used with passive switchover
        if hasattr(args, "method") and hasattr(args, "activation_method") and args.activation_method:
            if args.method != "passive":
                raise ValidationError("--activation-method can only be used with --method passive")

        # Validate that --non-interactive only makes sense with --decommission
        if hasattr(args, "non_interactive") and args.non_interactive:
            if not is_decommission:
                raise ValidationError("--non-interactive can only be used with --decommission")

        # Validate disable-observability-on-secondary flag
        if hasattr(args, "disable_observability_on_secondary") and args.disable_observability_on_secondary:
            if is_decommission:
                raise ValidationError("--disable-observability-on-secondary cannot be used with --decommission")
            if hasattr(args, "old_hub_action") and args.old_hub_action != "secondary":
                raise ValidationError("--disable-observability-on-secondary requires --old-hub-action secondary")

        # Validate setup-specific arguments
        if is_setup:
            # --admin-kubeconfig is required for setup
            if not (hasattr(args, "admin_kubeconfig") and args.admin_kubeconfig):
                raise ValidationError("--admin-kubeconfig is required for --setup mode")
            # Validate admin-kubeconfig path
            InputValidator.validate_safe_filesystem_path(args.admin_kubeconfig, "admin-kubeconfig")
            # Validate role if provided
            if hasattr(args, "role") and args.role:
                if args.role not in ("operator", "validator", "both"):
                    raise ValidationError("--role must be one of: operator, validator, both")
            # Validate token-duration format (basic check for number + unit)
            if hasattr(args, "token_duration") and args.token_duration:
                if not re.match(r"^\d+[hms]$", args.token_duration):
                    raise ValidationError("--token-duration must be in format like '48h', '30m', or '3600s'")
            # Validate output-dir if provided
            if hasattr(args, "output_dir") and args.output_dir:
                InputValidator.validate_safe_filesystem_path(args.output_dir, "output-dir")
