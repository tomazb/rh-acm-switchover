"""
Custom exceptions for ACM switchover automation.
"""


class SwitchoverError(Exception):
    """Base class for all switchover errors."""


class TransientError(SwitchoverError):
    """
    Error that might be resolved by retrying.
    Examples: Network timeouts, 503 Service Unavailable.
    """


class FatalError(SwitchoverError):
    """
    Error that cannot be resolved by retrying.
    Examples: Invalid configuration, missing permissions, 404 Not Found (when expected).
    """


class ConfigurationError(FatalError):
    """Invalid configuration or arguments."""


class ValidationError(ConfigurationError):
    """Input validation failure.

    This exception is raised when input validation fails, providing
    detailed error messages to help users understand what went wrong
    and how to fix it.
    """


class SecurityValidationError(ValidationError):
    """Security-related validation failure.

    This exception is raised when validation fails due to potential
    security issues (e.g., path traversal attempts, command injection).
    """
