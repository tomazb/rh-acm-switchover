"""
Pre-flight validation module for ACM switchover.
"""

import logging
from typing import Dict, Tuple

from lib.constants import OBSERVABILITY_NAMESPACE
from lib.kube_client import KubeClient
from lib.rbac_validator import validate_rbac_permissions

from .preflight_validators import (
    AutoImportStrategyValidator,
    BackupScheduleValidator,
    BackupValidator,
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

logger = logging.getLogger("acm_switchover")


class PreflightValidator:
    """Coordinates modular pre-flight validation checks."""

    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        method: str = "passive",
        skip_rbac_validation: bool = False,
    ) -> None:
        self.primary = primary_client
        self.secondary = secondary_client
        self.method = method
        self.skip_rbac_validation = skip_rbac_validation

        self.reporter = ValidationReporter()
        self.kubeconfig_validator = KubeconfigValidator(self.reporter)
        self.namespace_validator = NamespaceValidator(self.reporter)
        self.version_validator = VersionValidator(self.reporter)
        self.hub_component_validator = HubComponentValidator(self.reporter)
        self.backup_validator = BackupValidator(self.reporter)
        self.backup_schedule_validator = BackupScheduleValidator(self.reporter)
        self.cluster_deployment_validator = ClusterDeploymentValidator(self.reporter)
        self.managed_cluster_backup_validator = ManagedClusterBackupValidator(self.reporter)
        self.passive_sync_validator = PassiveSyncValidator(self.reporter)
        self.observability_detector = ObservabilityDetector(self.reporter)
        self.observability_prereq_validator = ObservabilityPrereqValidator(self.reporter)
        self.tooling_validator = ToolingValidator(self.reporter)

    def validate_all(self) -> Tuple[bool, Dict[str, object]]:
        """Run all validation checks and return pass/fail with detected config."""

        logger.info("Starting pre-flight validation...")

        # RBAC validation (unless explicitly skipped)
        if not self.skip_rbac_validation:
            try:
                logger.info("Validating RBAC permissions...")
                # Check if observability namespace exists on either hub
                # If not installed, skip observability permission checks
                primary_has_obs = self.primary.namespace_exists(OBSERVABILITY_NAMESPACE)
                secondary_has_obs = (
                    self.secondary.namespace_exists(OBSERVABILITY_NAMESPACE) if self.secondary else False
                )
                skip_obs = not (primary_has_obs or secondary_has_obs)
                if skip_obs:
                    logger.info(
                        "Observability namespace not found on either hub, " "skipping observability permission checks"
                    )

                validate_rbac_permissions(
                    primary_client=self.primary,
                    secondary_client=self.secondary,
                    include_decommission=False,  # Checked separately if needed
                    skip_observability=skip_obs,
                )
                self.reporter.add_result(
                    "RBAC Permissions",
                    True,
                    "All required RBAC permissions validated",
                    critical=True,
                )
            except Exception as e:
                self.reporter.add_result(
                    "RBAC Permissions",
                    False,
                    f"RBAC validation failed: {str(e)}",
                    critical=True,
                )
                logger.warning(
                    "RBAC validation failed. You can skip this check with --skip-rbac-validation "
                    "if you're confident you have the required permissions."
                )
        else:
            logger.info("RBAC validation skipped (--skip-rbac-validation specified)")

        # Kubeconfig structure and token validation
        self.kubeconfig_validator.run(self.primary, self.secondary)

        self.tooling_validator.run()
        self.namespace_validator.run(self.primary, self.secondary)
        primary_version, secondary_version = self.version_validator.run(
            self.primary,
            self.secondary,
        )

        # Auto-import strategy (detect-only, ACM 2.14+)
        AutoImportStrategyValidator(self.reporter).run(
            self.primary,
            self.secondary,
            primary_version,
            secondary_version,
        )

        for label, client in (("primary", self.primary), ("secondary", self.secondary)):
            self.hub_component_validator.run(client, label)

        self.backup_validator.run(self.primary)
        self.backup_schedule_validator.run(self.primary)
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
