"""
Library package for ACM switchover automation.
"""

__version__ = "1.4.13"
__version_date__ = "2026-01-27"

from .exceptions import (
    ConfigurationError,
    FatalError,
    SecurityValidationError,
    SwitchoverError,
    TransientError,
    ValidationError,
)
from .kube_client import KubeClient
from .rbac_validator import RBACValidator, validate_rbac_permissions
from .utils import (
    Phase,
    StateManager,
    confirm_action,
    format_duration,
    is_acm_version_ge,
    parse_acm_version,
    setup_logging,
)

__all__ = [
    "__version__",
    "__version_date__",
    "KubeClient",
    "SwitchoverError",
    "TransientError",
    "FatalError",
    "ValidationError",
    "SecurityValidationError",
    "ConfigurationError",
    "Phase",
    "StateManager",
    "setup_logging",
    "parse_acm_version",
    "is_acm_version_ge",
    "format_duration",
    "confirm_action",
    "RBACValidator",
    "validate_rbac_permissions",
]
