#!/usr/bin/env python3
"""
Test cases for input validation in ACM switchover automation.

This module tests the comprehensive validation functionality to ensure
security, reliability, and proper error handling.
"""

import pytest

from lib.exceptions import ConfigurationError
from lib.validation import (
    InputValidator,
    SecurityValidationError,
    ValidationError,
)


class MockArgs:
    """Mock arguments object for testing."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class TestCLIArgumentValidation:
    """Test CLI argument validation."""

    def test_valid_context_names(self):
        """Test valid context names."""
        valid_names = [
            "primary-hub",
            "secondary_hub",
            "my-cluster-123",
            "prod.acm.example.com",
            "dev-hub-2024",
            # oc login style contexts with slashes and colons
            "admin/api-ci-aws",
            "default/api.example.com:6443/admin",
            "system:admin/api-ocp-cluster:6443",
            "user/api.cluster.local:6443/kube:admin",
            # Single character names
            "a",
            "Z",
            "9",
        ]

        for name in valid_names:
            InputValidator.validate_context_name(name)

    def test_invalid_context_names(self):
        """Test invalid context names."""
        invalid_names = [
            "",  # empty
            "my cluster",  # spaces
            "my@cluster",  # invalid character
            "/admin",  # starts with slash
            "admin/",  # ends with slash
            ":6443",  # starts with colon
            "cluster:",  # ends with colon
            "a" * 129,  # too long
        ]

        for name in invalid_names:
            with pytest.raises(ValidationError):
                InputValidator.validate_context_name(name)

    def test_valid_cli_methods(self):
        """Test valid CLI methods."""
        valid_methods = ["passive", "full"]

        for method in valid_methods:
            InputValidator.validate_cli_method(method)

    def test_invalid_cli_methods(self):
        """Test invalid CLI methods."""
        invalid_methods = ["invalid", "passive-sync", "", "PASSIVE"]

        for method in invalid_methods:
            with pytest.raises(ValidationError):
                InputValidator.validate_cli_method(method)

    def test_valid_activation_methods(self):
        """Test valid activation methods."""
        for method in ["patch", "restore"]:
            InputValidator.validate_cli_activation_method(method)

    def test_invalid_activation_methods(self):
        """Test invalid activation methods."""
        for method in ["invalid", "", "PATCH"]:
            with pytest.raises(ValidationError):
                InputValidator.validate_cli_activation_method(method)

    def test_valid_old_hub_actions(self):
        """Test valid old hub actions."""
        valid_actions = ["secondary", "decommission", "none"]

        for action in valid_actions:
            InputValidator.validate_cli_old_hub_action(action)

    def test_invalid_old_hub_actions(self):
        """Test invalid old hub actions."""
        invalid_actions = ["keep", "remove", "", "SECONDARY"]

        for action in invalid_actions:
            with pytest.raises(ValidationError):
                InputValidator.validate_cli_old_hub_action(action)

    def test_valid_log_formats(self):
        """Test valid log formats."""
        valid_formats = ["text", "json"]

        for fmt in valid_formats:
            InputValidator.validate_cli_log_format(fmt)

    def test_invalid_log_formats(self):
        """Test invalid log formats."""
        invalid_formats = ["xml", "html", "", "JSON"]

        for fmt in invalid_formats:
            with pytest.raises(ValidationError):
                InputValidator.validate_cli_log_format(fmt)

    def test_validate_all_cli_args_success(self):
        """Test successful validation of all CLI args."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
        )

        # Should not raise any exceptions
        InputValidator.validate_all_cli_args(args)

    def test_validate_all_cli_args_invalid_context(self):
        """Test validation failure with invalid context."""
        args = MockArgs(
            primary_context="invalid context!",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
        )

        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_validate_all_cli_args_missing_secondary(self):
        """Test validation failure with missing secondary context."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context=None,
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
        )

        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_validate_activation_method_restore_requires_passive(self):
        """--activation-method=restore should only be valid with passive."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="full",
            activation_method="restore",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
        )

        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_disable_observability_requires_secondary_action(self):
        """--disable-observability-on-secondary requires old_hub_action=secondary."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            activation_method="patch",
            old_hub_action="none",
            disable_observability_on_secondary=True,
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
        )

        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_argocd_resume_only_requires_secondary_context(self):
        """--argocd-resume-only requires --secondary-context to resolve state file."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context=None,
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            decommission=False,
            argocd_resume_only=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_all_cli_args(args)
        assert "argocd-resume-only" in str(exc_info.value).lower()
        assert "secondary-context" in str(exc_info.value).lower()

    def test_argocd_resume_only_with_secondary_context_passes(self):
        """--argocd-resume-only with --secondary-context passes validation."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            argocd_resume_only=True,
        )

        InputValidator.validate_all_cli_args(args)

    def test_argocd_resume_only_rejects_validate_only(self):
        """--argocd-resume-only cannot be combined with --validate-only."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            argocd_resume_only=True,
            validate_only=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_all_cli_args(args)
        assert "argocd-resume-only" in str(exc_info.value).lower()
        assert "validate-only" in str(exc_info.value).lower()

    def test_argocd_manage_rejects_validate_only(self):
        """--argocd-manage cannot be combined with --validate-only."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            argocd_manage=True,
            validate_only=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_all_cli_args(args)
        assert "argocd-manage" in str(exc_info.value).lower()
        assert "validate-only" in str(exc_info.value).lower()

    def test_argocd_manage_rejects_resume_only(self):
        """--argocd-manage cannot be combined with --argocd-resume-only."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            argocd_manage=True,
            argocd_resume_only=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_all_cli_args(args)
        assert "argocd-manage" in str(exc_info.value).lower()
        assert "argocd-resume-only" in str(exc_info.value).lower()

    def test_argocd_resume_after_requires_manage(self):
        """--argocd-resume-after-switchover requires --argocd-manage."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            argocd_resume_after_switchover=True,
            argocd_manage=False,
        )

        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_all_cli_args(args)
        assert "argocd-resume-after-switchover" in str(exc_info.value).lower()
        assert "argocd-manage" in str(exc_info.value).lower()

    def test_argocd_resume_after_rejects_resume_only(self):
        """--argocd-resume-after-switchover cannot be combined with --argocd-resume-only."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            argocd_resume_after_switchover=True,
            argocd_manage=True,
            argocd_resume_only=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_all_cli_args(args)
        assert "argocd-resume-after-switchover" in str(exc_info.value).lower()
        assert "argocd-resume-only" in str(exc_info.value).lower()

    def test_argocd_resume_after_rejects_validate_only(self):
        """--argocd-resume-after-switchover cannot be combined with --validate-only."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            argocd_resume_after_switchover=True,
            argocd_manage=True,
            validate_only=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            InputValidator.validate_all_cli_args(args)
        assert "argocd-resume-after-switchover" in str(exc_info.value).lower()
        assert "validate-only" in str(exc_info.value).lower()


class TestKubernetesResourceValidation:
    """Test Kubernetes resource name validation."""

    def test_valid_kubernetes_names(self):
        """Test valid Kubernetes resource names."""
        valid_names = [
            "my-pod",
            "my-pod-123",
            "my.pod.name",
            "my-pod-name",
            "a",  # single letter
            "1",  # single digit (valid per DNS-1123 subdomain)
            "123pod",  # starts with digit (valid per DNS-1123 subdomain)
            "a" + "b" * 252,  # max length (253 chars, starts with letter)
        ]

        for name in valid_names:
            InputValidator.validate_kubernetes_name(name)

    def test_invalid_kubernetes_names(self):
        """Test invalid Kubernetes resource names."""
        invalid_names = [
            "",  # empty
            "My-Pod",  # uppercase
            "my pod",  # space
            "my@pod",  # invalid character
            # Note: "123pod" is valid per DNS-1123 subdomain rules (can start with alphanumeric)
            "my-pod-",  # ends with hyphen
            "a" * 254,  # too long
            "my..pod",  # consecutive dots
            "-my-pod",  # starts with hyphen
        ]

        for name in invalid_names:
            with pytest.raises(ValidationError):
                InputValidator.validate_kubernetes_name(name)

    def test_valid_namespaces(self):
        """Test valid Kubernetes namespace names."""
        valid_namespaces = [
            "default",
            "kube-system",
            "my-namespace",
            "my-namespace-123",
            "a",  # single letter
            "a" + "b" * 62,  # max length (63 chars, starts with letter)
        ]

        for namespace in valid_namespaces:
            InputValidator.validate_kubernetes_namespace(namespace)

    def test_invalid_namespaces(self):
        """Test invalid Kubernetes namespace names."""
        invalid_namespaces = [
            "",  # empty
            "My-Namespace",  # uppercase
            "my namespace",  # space
            "my@namespace",  # invalid character
            "123namespace",  # starts with number
            "my-namespace-",  # ends with hyphen
            "a" * 64,  # too long
            "my..namespace",  # consecutive dots
            "-my-namespace",  # starts with hyphen
        ]

        for namespace in invalid_namespaces:
            with pytest.raises(ValidationError):
                InputValidator.validate_kubernetes_namespace(namespace)

    def test_valid_label_keys(self):
        """Test valid Kubernetes label keys."""
        valid_keys = [
            "app",
            "app.kubernetes.io/name",
            "my-label",
            "my-label-123",
            "a",  # single letter
            "a" + "b" * 62,  # max length (63 chars, starts with letter)
            # Per K8s spec, uppercase and starting with digits ARE valid
            "My-Label",
            "MY_LABEL",
            "123label",
            "1",
        ]

        for key in valid_keys:
            InputValidator.validate_kubernetes_label_key(key)

    def test_invalid_label_keys(self):
        """Test invalid Kubernetes label keys."""
        # Note: Per K8s spec, label keys CAN contain uppercase and CAN start with digits
        # Only the following are truly invalid:
        invalid_keys = [
            "",  # empty
            "my label",  # space
            "my@label",  # invalid character
            "my-label-",  # ends with hyphen
            "a" * 64,  # too long
            "-my-label",  # starts with hyphen
            "_my-label",  # starts with underscore
            "my-label_",  # ends with underscore
            ".my-label",  # starts with dot
            "my-label.",  # ends with dot
        ]

        for key in invalid_keys:
            with pytest.raises(ValidationError):
                InputValidator.validate_kubernetes_label_key(key)

    def test_valid_label_values(self):
        """Test valid Kubernetes label values."""
        valid_values = [
            "",  # empty string is valid per K8s spec
            "value",
            "my-value",
            "my-value-123",
            "a",  # single character
            "a" + "b" * 62,  # max length (63 chars)
            # Per K8s spec, uppercase and starting with digits ARE valid
            "My-Value",
            "MY_VALUE",
            "123value",
            "1",
        ]

        for value in valid_values:
            InputValidator.validate_kubernetes_label_value(value)

    def test_invalid_label_values(self):
        """Test invalid Kubernetes label values."""
        # Note: Per K8s spec, label values CAN contain uppercase
        # Only the following are truly invalid:
        invalid_values = [
            "my value",  # space
            "my@value",  # invalid character
            "-my-value",  # starts with hyphen
            "my-value-",  # ends with hyphen
            "a" * 64,  # too long
            "_my-value",  # starts with underscore
            "my-value_",  # ends with underscore
            ".my-value",  # starts with dot
            "my-value.",  # ends with dot
        ]

        for value in invalid_values:
            with pytest.raises(ValidationError):
                InputValidator.validate_kubernetes_label_value(value)

    def test_label_value_none_raises(self):
        """Test that None label value raises ValidationError."""
        with pytest.raises(ValidationError):
            InputValidator.validate_kubernetes_label_value(None)


class TestFilesystemValidation:
    """Test filesystem path validation."""

    def test_valid_filesystem_paths(self):
        """Test valid filesystem paths."""
        valid_paths = [
            "state-file.json",
            ".state/switchover-state.json",
            "relative/path/to/file",
            "/tmp/valid-file",
            "/var/log/app.log",
            "my_file_123.txt",
        ]

        for path in valid_paths:
            InputValidator.validate_safe_filesystem_path(path, "test")

    def test_invalid_filesystem_paths(self):
        """Test invalid filesystem paths."""
        invalid_paths = [
            ("../malicious", "path traversal"),
            ("~/home/user", "home directory"),
            ("$HOME/file", "environment variable"),
            ("{malicious}", "curly braces"),
            ("file|command", "pipe"),
            ("file&command", "ampersand"),
            ("file;command", "semicolon"),
            ("<script>", "angle bracket"),
            ("`command`", "backtick"),
            ("/etc/passwd", "absolute path outside allowed"),
            ("/root/.ssh", "absolute path outside allowed"),
        ]

        for path, reason in invalid_paths:
            with pytest.raises(SecurityValidationError):
                InputValidator.validate_safe_filesystem_path(path, "test")

    def test_empty_filesystem_path(self):
        """Test empty filesystem path."""
        with pytest.raises(ValidationError):
            InputValidator.validate_safe_filesystem_path("", "test")


class TestStringValidation:
    """Test string validation utilities."""

    def test_valid_non_empty_strings(self):
        """Test valid non-empty strings."""
        valid_strings = [
            "valid",
            "valid string",
            "123",
            "a" * 100,
        ]

        for string in valid_strings:
            InputValidator.validate_non_empty_string(string, "test")

    def test_invalid_non_empty_strings(self):
        """Test invalid non-empty strings."""
        invalid_strings = [
            "",
            "   ",
            "\t",
            "\n",
            " \t\n ",
        ]

        for string in invalid_strings:
            with pytest.raises(ValidationError):
                InputValidator.validate_non_empty_string(string, "test")


class TestSanitization:
    """Test sanitization utilities."""

    def test_sanitize_context_identifier(self):
        """Test context identifier sanitization."""
        test_cases = [
            ("normal-context", "normal-context"),
            ("my.context-123", "my.context-123"),
            ("my context with spaces", "my_context_with_spaces"),
            ("my@context!with@special!chars", "my_context_with_special_chars"),
            ("", "unknown"),
            ("My-Context", "My-Context"),
            ("context/with/slashes", "context_with_slashes"),
        ]

        for input_str, expected in test_cases:
            result = InputValidator.sanitize_context_identifier(input_str)
            assert result == expected, f"Expected '{expected}', got '{result}' for input '{input_str}'"


class TestErrorHandling:
    """Test error handling and exception types."""

    def test_validation_error_inheritance(self):
        """Test that ValidationError inherits from ConfigurationError."""
        assert issubclass(ValidationError, ConfigurationError)
        assert issubclass(SecurityValidationError, ValidationError)
        assert issubclass(SecurityValidationError, ConfigurationError)

    def test_security_validation_error_type(self):
        """Test that security errors are properly typed."""
        with pytest.raises(SecurityValidationError):
            InputValidator.validate_safe_filesystem_path("../malicious", "test")

        with pytest.raises(SecurityValidationError):
            InputValidator.validate_safe_filesystem_path("file;command", "test")

    def test_validation_error_messages(self):
        """Test that validation errors have descriptive messages."""
        try:
            InputValidator.validate_context_name("invalid context!")
        except ValidationError as e:
            assert "invalid context!" in str(e).lower()
            assert "alphanumeric" in str(e).lower()

        try:
            InputValidator.validate_safe_filesystem_path("../malicious", "state-file")
        except SecurityValidationError as e:
            assert "security" in str(e).lower()
            assert "path traversal" in str(e).lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
