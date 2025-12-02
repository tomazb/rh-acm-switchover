"""
Pre-flight validation module for ACM switchover.
"""

import logging
from typing import Dict, Tuple

from lib.exceptions import ValidationError
from lib.kube_client import KubeClient

from .preflight_validators import (
    BackupValidator,
    ClusterDeploymentValidator,
    HubComponentValidator,
    ManagedClusterBackupValidator,
    NamespaceValidator,
    ObservabilityDetector,
    ObservabilityPrereqValidator,
    PassiveSyncValidator,
    ToolingValidator,
    ValidationReporter,
    VersionValidator,
)

logger = logging.getLogger("acm_switchover")


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
        self.managed_cluster_backup_validator = ManagedClusterBackupValidator(self.reporter)
        self.passive_sync_validator = PassiveSyncValidator(self.reporter)
        self.observability_detector = ObservabilityDetector(self.reporter)
        self.observability_prereq_validator = ObservabilityPrereqValidator(self.reporter)
        self.tooling_validator = ToolingValidator(self.reporter)

    def validate_all(self) -> Tuple[bool, Dict[str, object]]:
        """Run all validation checks and return pass/fail with detected config."""

        logger.info("Starting pre-flight validation...")

        self.tooling_validator.run()
        self.namespace_validator.run(self.primary, self.secondary)
        primary_version, secondary_version = self.version_validator.run(
            self.primary,
            self.secondary,
        )

        for label, client in (("primary", self.primary), ("secondary", self.secondary)):
            self.hub_component_validator.run(client, label)

        self.backup_validator.run(self.primary)
        self.cluster_deployment_validator.run(self.primary)
        self.managed_cluster_backup_validator.run(self.primary)

        if self.method == "passive":
            self.passive_sync_validator.run(self.secondary)

        primary_observability, secondary_observability = self.observability_detector.detect(
            self.primary,
            self.secondary,
        )

        if secondary_observability:
            self.observability_prereq_validator.run(self.secondary)

        self.reporter.print_summary()

        config = {
            "primary_version": primary_version,
            "secondary_version": secondary_version,
            "primary_observability_detected": primary_observability,
            "secondary_observability_detected": secondary_observability,
            "has_observability": primary_observability or secondary_observability,
        }

        critical_failures = self.reporter.critical_failures()
        return len(critical_failures) == 0, config
