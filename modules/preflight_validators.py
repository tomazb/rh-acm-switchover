"""Backward compatibility layer for pre-flight validation.

This module provides backward compatibility by importing all validators
from the new modular structure. Existing code using imports from
this module will continue to work without changes.

DEPRECATED: New code should import directly from modules.preflight.*
Removal plan: keep this shim through the next minor release, then remove
once downstream imports are migrated (target removal by 2026-06-30).
"""

import logging
import warnings

# Import all validators from the new modular structure
from .preflight import (
    AutoImportStrategyValidator,
    BackupScheduleValidator,
    BackupValidator,
    BaseValidator,
    ClusterDeploymentValidator,
    HubComponentValidator,
    KubeconfigValidator,
    ManagedClusterBackupValidator,
    NamespaceValidator,
    ObservabilityDetector,
    ObservabilityPrereqValidator,
    PassiveSyncValidator,
    ToolingValidator,
    ValidationReporter,
    VersionValidator,
)

# Re-export everything for backward compatibility
__all__ = [
    "AutoImportStrategyValidator",
    "BackupScheduleValidator",
    "BackupValidator",
    "BaseValidator",
    "ClusterDeploymentValidator",
    "HubComponentValidator",
    "KubeconfigValidator",
    "ManagedClusterBackupValidator",
    "NamespaceValidator",
    "ObservabilityDetector",
    "ObservabilityPrereqValidator",
    "PassiveSyncValidator",
    "ToolingValidator",
    "ValidationReporter",
    "VersionValidator",
]

# Add commonly used module-level attributes for backward compatibility
logger = logging.getLogger("acm_switchover")

# Emit deprecation warning when module is imported
warnings.warn(
    "modules.preflight_validators is deprecated. "
    "Import validators directly from modules.preflight instead.",
    DeprecationWarning,
    stacklevel=2
)
