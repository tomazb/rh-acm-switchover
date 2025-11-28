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


class ValidationError(FatalError):
    """Pre-flight validation failure."""


class ConfigurationError(FatalError):
    """Invalid configuration or arguments."""
