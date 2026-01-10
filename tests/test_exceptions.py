"""
Tests for the exception module hierarchy and behavior.
"""

import pytest

from lib.exceptions import (
    SwitchoverError,
    TransientError,
    FatalError,
    ConfigurationError,
    ValidationError,
    SecurityValidationError,
)


@pytest.mark.unit
class TestExceptionHierarchy:
    """Test the exception class inheritance chain."""

    def test_switchover_error_is_base_exception(self):
        """SwitchoverError should be the root of our exception hierarchy."""
        assert issubclass(SwitchoverError, Exception)
        exc = SwitchoverError("test error")
        assert isinstance(exc, Exception)

    def test_transient_error_extends_switchover_error(self):
        """TransientError should extend SwitchoverError."""
        assert issubclass(TransientError, SwitchoverError)
        exc = TransientError("network timeout")
        assert isinstance(exc, SwitchoverError)
        assert isinstance(exc, Exception)

    def test_fatal_error_extends_switchover_error(self):
        """FatalError should extend SwitchoverError."""
        assert issubclass(FatalError, SwitchoverError)
        exc = FatalError("missing resource")
        assert isinstance(exc, SwitchoverError)
        assert isinstance(exc, Exception)

    def test_configuration_error_extends_fatal_error(self):
        """ConfigurationError should extend FatalError."""
        assert issubclass(ConfigurationError, FatalError)
        assert issubclass(ConfigurationError, SwitchoverError)
        exc = ConfigurationError("invalid config")
        assert isinstance(exc, FatalError)
        assert isinstance(exc, SwitchoverError)

    def test_validation_error_extends_configuration_error(self):
        """ValidationError should extend ConfigurationError."""
        assert issubclass(ValidationError, ConfigurationError)
        assert issubclass(ValidationError, FatalError)
        assert issubclass(ValidationError, SwitchoverError)
        exc = ValidationError("invalid input")
        assert isinstance(exc, ConfigurationError)
        assert isinstance(exc, FatalError)
        assert isinstance(exc, SwitchoverError)

    def test_security_validation_error_extends_validation_error(self):
        """SecurityValidationError should extend ValidationError."""
        assert issubclass(SecurityValidationError, ValidationError)
        assert issubclass(SecurityValidationError, ConfigurationError)
        assert issubclass(SecurityValidationError, FatalError)
        assert issubclass(SecurityValidationError, SwitchoverError)
        exc = SecurityValidationError("path traversal detected")
        assert isinstance(exc, ValidationError)
        assert isinstance(exc, ConfigurationError)
        assert isinstance(exc, FatalError)
        assert isinstance(exc, SwitchoverError)


@pytest.mark.unit
class TestExceptionMessages:
    """Test exception message handling."""

    def test_switchover_error_message(self):
        """SwitchoverError should preserve message."""
        msg = "Something went wrong"
        exc = SwitchoverError(msg)
        assert str(exc) == msg
        assert exc.args == (msg,)

    def test_transient_error_message(self):
        """TransientError should preserve message."""
        msg = "Network timeout after 30s"
        exc = TransientError(msg)
        assert str(exc) == msg

    def test_fatal_error_message(self):
        """FatalError should preserve message."""
        msg = "Resource not found: ManagedCluster/prod-cluster"
        exc = FatalError(msg)
        assert str(exc) == msg

    def test_configuration_error_message(self):
        """ConfigurationError should preserve message."""
        msg = "Invalid kubeconfig path"
        exc = ConfigurationError(msg)
        assert str(exc) == msg

    def test_validation_error_message(self):
        """ValidationError should preserve message."""
        msg = "Context 'missing-ctx' not found in kubeconfig"
        exc = ValidationError(msg)
        assert str(exc) == msg

    def test_security_validation_error_message(self):
        """SecurityValidationError should preserve message."""
        msg = "Path traversal attempt: ../../../etc/passwd"
        exc = SecurityValidationError(msg)
        assert str(exc) == msg


@pytest.mark.unit
class TestExceptionCatching:
    """Test that exceptions can be caught at appropriate levels."""

    def test_catch_transient_as_switchover_error(self):
        """TransientError should be catchable as SwitchoverError."""
        with pytest.raises(SwitchoverError):
            raise TransientError("timeout")

    def test_catch_fatal_as_switchover_error(self):
        """FatalError should be catchable as SwitchoverError."""
        with pytest.raises(SwitchoverError):
            raise FatalError("missing resource")

    def test_catch_validation_as_fatal_error(self):
        """ValidationError should be catchable as FatalError."""
        with pytest.raises(FatalError):
            raise ValidationError("invalid input")

    def test_catch_security_validation_as_validation_error(self):
        """SecurityValidationError should be catchable as ValidationError."""
        with pytest.raises(ValidationError):
            raise SecurityValidationError("injection attempt")

    def test_transient_not_caught_as_fatal(self):
        """TransientError should NOT be catchable as FatalError."""
        with pytest.raises(TransientError):
            try:
                raise TransientError("network error")
            except FatalError:
                pytest.fail("TransientError should not be caught as FatalError")

    def test_fatal_not_caught_as_transient(self):
        """FatalError should NOT be catchable as TransientError."""
        with pytest.raises(FatalError):
            try:
                raise FatalError("missing config")
            except TransientError:
                pytest.fail("FatalError should not be caught as TransientError")


@pytest.mark.unit
class TestExceptionUseCases:
    """Test typical use cases for each exception type."""

    def test_transient_error_for_retryable_issues(self):
        """TransientError is appropriate for retryable issues."""
        # Simulating network timeout that might resolve on retry
        exc = TransientError("Connection timed out after 30s")
        assert "timed out" in str(exc).lower()
        assert isinstance(exc, SwitchoverError)
        # Should NOT be a FatalError since it might resolve
        assert not isinstance(exc, FatalError)

    def test_fatal_error_for_unrecoverable_issues(self):
        """FatalError is appropriate for unrecoverable issues."""
        exc = FatalError("Required namespace 'open-cluster-management' not found")
        assert isinstance(exc, SwitchoverError)
        # Should NOT be a TransientError since retry won't help
        assert not isinstance(exc, TransientError)

    def test_configuration_error_for_bad_config(self):
        """ConfigurationError is appropriate for configuration issues."""
        exc = ConfigurationError("Kubeconfig path does not exist: /invalid/path")
        assert isinstance(exc, FatalError)
        # Configuration issues are fatal - user must fix config

    def test_validation_error_for_input_validation(self):
        """ValidationError is appropriate for input validation failures."""
        exc = ValidationError("--primary-context is required")
        assert isinstance(exc, ConfigurationError)
        # Validation errors are configuration errors

    def test_security_validation_error_for_security_issues(self):
        """SecurityValidationError is appropriate for security-related validation."""
        exc = SecurityValidationError(
            "State file path contains path traversal: '../../../etc/passwd'"
        )
        assert isinstance(exc, ValidationError)
        # Security issues are validation errors with extra severity
