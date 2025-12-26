"""
Library package for ACM switchover automation.
"""

# Import version from lightweight module (avoids importing heavy deps at build time)
from ._version import __version__, __version_date__

from .exceptions import (
    ConfigurationError,
    FatalError,
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
