"""
Pre-flight validation module for ACM switchover.
"""

import logging
from typing import Dict, Tuple

from lib.kube_client import KubeClient
from .preflight_validators import (
    BackupValidator,
    ClusterDeploymentValidator,
    HubComponentValidator,
    NamespaceValidator,
    ObservabilityDetector,
    PassiveSyncValidator,
    ValidationReporter,
    VersionValidator,
)

logger = logging.getLogger("acm_switchover")


class ValidationError(Exception):
    """Validation check failed.
    
    Raised when a critical pre-flight validation check fails,
    indicating that the switchover should not proceed.
    """
    pass


class PreflightValidator:
    """Coordinates modular pre-flight validation checks."""

    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        method: str = "passive",
    ) -> None:
        self.primary = primary_client
        self.secondary = secondary_client
        self.method = method

        self.reporter = ValidationReporter()
        self.namespace_validator = NamespaceValidator(self.reporter)
        self.version_validator = VersionValidator(self.reporter)
        self.hub_component_validator = HubComponentValidator(self.reporter)
        self.backup_validator = BackupValidator(self.reporter)
        self.cluster_deployment_validator = ClusterDeploymentValidator(self.reporter)
        self.passive_sync_validator = PassiveSyncValidator(self.reporter)
        self.observability_detector = ObservabilityDetector(self.reporter)

    def validate_all(self) -> Tuple[bool, Dict[str, object]]:
        """Run all validation checks and return pass/fail with detected config."""

        logger.info("Starting pre-flight validation...")

        self.namespace_validator.run(self.primary, self.secondary)
        primary_version, secondary_version = self.version_validator.run(
            self.primary,
            self.secondary,
        )

        for label, client in (("primary", self.primary), ("secondary", self.secondary)):
            self.hub_component_validator.run(client, label)

        self.backup_validator.run(self.primary)
        self.cluster_deployment_validator.run(self.primary)

        if self.method == "passive":
            self.passive_sync_validator.run(self.secondary)

        has_observability = self.observability_detector.detect(
            self.primary,
            self.secondary,
        )

        self.reporter.print_summary()

        config = {
            "primary_version": primary_version,
            "secondary_version": secondary_version,
            "has_observability": has_observability,
        }

        critical_failures = self.reporter.critical_failures()
        return len(critical_failures) == 0, config
