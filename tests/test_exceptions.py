"""
Tests for the exception module hierarchy and behavior.
"""

import pytest

from lib.exceptions import (
    ConfigurationError,
    FatalError,
    SecurityValidationError,
    SwitchoverError,
    TransientError,
    ValidationError,
)


@pytest.mark.unit
class TestExceptionHierarchy:
    """Test the exception class inheritance chain."""

    @pytest.mark.parametrize(
        "exc_class, expected_parents",
        [
            (SwitchoverError, [Exception]),
            (TransientError, [SwitchoverError, Exception]),
            (FatalError, [SwitchoverError, Exception]),
            (ConfigurationError, [FatalError, SwitchoverError]),
            (ValidationError, [ConfigurationError, FatalError, SwitchoverError]),
            (SecurityValidationError, [ValidationError, ConfigurationError, FatalError, SwitchoverError]),
        ],
        ids=[
            "SwitchoverError-is-Exception",
            "TransientError-extends-SwitchoverError",
            "FatalError-extends-SwitchoverError",
            "ConfigurationError-extends-FatalError",
            "ValidationError-extends-ConfigurationError",
            "SecurityValidationError-extends-ValidationError",
        ],
    )
    def test_subclass_and_isinstance(self, exc_class, expected_parents):
        """Each exception class is a subclass of and instanceof its expected parents."""
        for parent in expected_parents:
            assert issubclass(exc_class, parent)
        exc = exc_class("test")
        for parent in expected_parents:
            assert isinstance(exc, parent)


@pytest.mark.unit
class TestExceptionMessages:
    """Test exception message handling."""

    @pytest.mark.parametrize(
        "exc_class, msg",
        [
            (SwitchoverError, "Something went wrong"),
            (TransientError, "Network timeout after 30s"),
            (FatalError, "Resource not found: ManagedCluster/prod-cluster"),
            (ConfigurationError, "Invalid kubeconfig path"),
            (ValidationError, "Context 'missing-ctx' not found in kubeconfig"),
            (SecurityValidationError, "Path traversal attempt: ../../../etc/passwd"),
        ],
        ids=[
            "SwitchoverError",
            "TransientError",
            "FatalError",
            "ConfigurationError",
            "ValidationError",
            "SecurityValidationError",
        ],
    )
    def test_message_preservation(self, exc_class, msg):
        """Each exception should preserve its message."""
        exc = exc_class(msg)
        assert str(exc) == msg
        assert exc.args == (msg,)


@pytest.mark.unit
class TestExceptionCatching:
    """Test that exceptions can be caught at appropriate levels."""

    @pytest.mark.parametrize(
        "raise_class, catch_class, msg",
        [
            (TransientError, SwitchoverError, "timeout"),
            (FatalError, SwitchoverError, "missing resource"),
            (ValidationError, FatalError, "invalid input"),
            (SecurityValidationError, ValidationError, "injection attempt"),
        ],
        ids=[
            "TransientError-caught-as-SwitchoverError",
            "FatalError-caught-as-SwitchoverError",
            "ValidationError-caught-as-FatalError",
            "SecurityValidationError-caught-as-ValidationError",
        ],
    )
    def test_catchable_as_parent(self, raise_class, catch_class, msg):
        """Subclass exceptions should be catchable by parent exception handlers."""
        with pytest.raises(catch_class):
            raise raise_class(msg)

    @pytest.mark.parametrize(
        "raise_class, not_catch_class, msg",
        [
            (TransientError, FatalError, "network error"),
            (FatalError, TransientError, "missing config"),
        ],
        ids=[
            "TransientError-not-caught-as-FatalError",
            "FatalError-not-caught-as-TransientError",
        ],
    )
    def test_not_catchable_as_sibling(self, raise_class, not_catch_class, msg):
        """Sibling exception branches should not catch each other."""
        with pytest.raises(raise_class):
            try:
                raise raise_class(msg)
            except not_catch_class:
                pytest.fail(f"{raise_class.__name__} should not be caught as {not_catch_class.__name__}")


@pytest.mark.unit
class TestExceptionUseCases:
    """Test typical use cases for each exception type."""

    @pytest.mark.parametrize(
        "exc_class, msg, expected_types, not_expected_types",
        [
            (
                TransientError,
                "Connection timed out after 30s",
                [SwitchoverError],
                [FatalError],
            ),
            (
                FatalError,
                "Required namespace 'open-cluster-management' not found",
                [SwitchoverError],
                [TransientError],
            ),
            (
                ConfigurationError,
                "Kubeconfig path does not exist: /invalid/path",
                [FatalError],
                [],
            ),
            (
                ValidationError,
                "--primary-context is required",
                [ConfigurationError],
                [],
            ),
            (
                SecurityValidationError,
                "State file path contains path traversal: '../../../etc/passwd'",
                [ValidationError],
                [],
            ),
        ],
        ids=[
            "TransientError-retryable-not-fatal",
            "FatalError-unrecoverable-not-transient",
            "ConfigurationError-is-fatal",
            "ValidationError-is-configuration",
            "SecurityValidationError-is-validation",
        ],
    )
    def test_use_case_classification(self, exc_class, msg, expected_types, not_expected_types):
        """Each exception type correctly classifies for its intended use case."""
        exc = exc_class(msg)
        for expected in expected_types:
            assert isinstance(exc, expected)
        for not_expected in not_expected_types:
            assert not isinstance(exc, not_expected)
