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


class StateLoadError(FatalError):
    """State file could not be loaded due to corruption or I/O failure.

    The tool must not continue with an automatically-generated fresh state
    when a state file already existed, because doing so risks replaying
    mutations that were already applied to a real hub.

    Raise this exception instead of silently resetting state. The caller
    (acm_switchover.py) should surface a clear operator-facing message and
    abort unless an explicit --reset-state or --force flag is provided.
    """


class StateLockError(FatalError):
    """Another process is already using the same switchover state file.

    This protects hub mutations from concurrent invocations that would
    otherwise race on both the state file and the cluster resources being
    modified. The caller should abort with a clear operator-facing message.
    """
