"""Pre-flight validation module for ACM switchover."""

# Runbook: Step 0 (pre-flight validation)

import logging
from typing import Tuple, TypedDict

from kubernetes.client.rest import ApiException

from lib import argocd as argocd_lib
from lib.constants import OBSERVABILITY_NAMESPACE
from lib.exceptions import ValidationError
from lib.kube_client import KubeClient
from lib.rbac_validator import validate_rbac_permissions

from .preflight import (
    AutoImportStrategyValidator,
    BackupScheduleValidator,
    BackupStorageLocationValidator,
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


class PreflightConfig(TypedDict):
    primary_version: str
    secondary_version: str
    primary_observability_detected: bool
    secondary_observability_detected: bool
    has_observability: bool


class PreflightValidator:
    """Coordinates modular pre-flight validation checks."""

    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        method: str = "passive",
        skip_rbac_validation: bool = False,
        include_decommission: bool = False,
        argocd_check: bool = False,
        argocd_manage: bool = False,
    ) -> None:
        self.primary = primary_client
        self.secondary = secondary_client
        self.method = method
        self.skip_rbac_validation = skip_rbac_validation
        self.include_decommission = include_decommission
        self.argocd_check = argocd_check
        self.argocd_manage = argocd_manage

        self.reporter = ValidationReporter()
        self.kubeconfig_validator = KubeconfigValidator(self.reporter)
        self.namespace_validator = NamespaceValidator(self.reporter)
        self.version_validator = VersionValidator(self.reporter)
        self.hub_component_validator = HubComponentValidator(self.reporter)
        self.backup_validator = BackupValidator(self.reporter)
        self.backup_schedule_validator = BackupScheduleValidator(self.reporter)
        self.backup_storage_location_validator = BackupStorageLocationValidator(self.reporter)
        self.cluster_deployment_validator = ClusterDeploymentValidator(self.reporter)
        self.managed_cluster_backup_validator = ManagedClusterBackupValidator(self.reporter)
        self.passive_sync_validator = PassiveSyncValidator(self.reporter)
        self.observability_detector = ObservabilityDetector(self.reporter)
        self.observability_prereq_validator = ObservabilityPrereqValidator(self.reporter)
        self.tooling_validator = ToolingValidator(self.reporter)

    def _get_argocd_rbac_mode(self) -> str:
        """Get Argo CD RBAC validation mode based on requested CLI behavior."""
        if self.argocd_manage:
            return "manage"
        if self.argocd_check:
            return "check"
        return "none"

    def _get_effective_argocd_rbac_mode(self) -> Tuple[str, str]:
        """Require Argo CD RBAC only when Applications CRD exists on at least one hub.

        Returns:
            Tuple of (argocd_mode, argocd_install_type). install_type is 'vanilla',
            'operator', or 'unknown'.
        """
        requested_mode = self._get_argocd_rbac_mode()
        if requested_mode == "none":
            return "none", "unknown"

        for client, hub_label in (
            (self.primary, "primary"),
            (self.secondary, "secondary"),
        ):
            if client is None:
                continue
            try:
                discovery = argocd_lib.detect_argocd_installation(client)
            except ApiException as exc:
                if exc.status in (401, 403):
                    logger.info(
                        "Unable to inspect Argo CD CRDs on %s hub (%s %s); deferring to RBAC validation.",
                        hub_label,
                        exc.status,
                        exc.reason,
                    )
                    return requested_mode, "unknown"
                raise
            if discovery.has_applications_crd:
                return requested_mode, discovery.install_type
            logger.info("Argo CD Applications CRD not found on %s hub", hub_label)

        logger.info("Argo CD Applications CRD not found on either hub, skipping Argo CD RBAC permission checks")
        return "none", "unknown"

    def validate_all(self) -> Tuple[bool, PreflightConfig]:
        """Run all validation checks and return pass/fail with detected config."""

        logger.info("Starting pre-flight validation...")

        # RBAC validation (unless explicitly skipped)
        if not self.skip_rbac_validation:
            logger.info("Validating RBAC permissions...")
            try:
                # F6 fix: Wrap the entire RBAC block so routine API failures
                # (from namespace_exists, Argo CD discovery, or RBAC checks)
                # are converted into structured validation results instead of
                # escaping as uncaught exceptions.
                # Check if observability namespace exists on either hub
                # If not installed, skip observability permission checks
                primary_has_obs = self.primary.namespace_exists(OBSERVABILITY_NAMESPACE)
                secondary_has_obs = (
                    self.secondary.namespace_exists(OBSERVABILITY_NAMESPACE) if self.secondary else False
                )
                skip_obs = not (primary_has_obs or secondary_has_obs)
                if skip_obs:
                    logger.info(
                        "Observability namespace not found on either hub, "
                        "skipping observability permission checks"
                    )

                effective_argocd_mode, argocd_install_type = self._get_effective_argocd_rbac_mode()
                validate_rbac_permissions(
                    primary_client=self.primary,
                    secondary_client=self.secondary,
                    include_decommission=self.include_decommission,
                    skip_observability=skip_obs,
                    argocd_mode=effective_argocd_mode,
                    argocd_install_type=argocd_install_type,
                )
            except ValidationError as e:
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
            except ApiException as e:
                self.reporter.add_result(
                    "RBAC Permissions",
                    False,
                    f"RBAC validation failed due to API error: {e.status} {e.reason}",
                    critical=True,
                )
                logger.warning(
                    "RBAC validation could not complete due to an API error (%s %s). "
                    "You can skip this check with --skip-rbac-validation.",
                    e.status,
                    e.reason,
                )
            else:
                self.reporter.add_result(
                    "RBAC Permissions",
                    True,
                    "All required RBAC permissions validated",
                    critical=True,
                )
        else:
            logger.info("RBAC validation skipped (--skip-rbac-validation specified)")

        # Kubeconfig structure and token validation
        self.kubeconfig_validator.run(self.primary, self.secondary, method=self.method)

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
        self.backup_storage_location_validator.run(self.primary, "primary")
        self.backup_storage_location_validator.run(self.secondary, "secondary")
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

        config: PreflightConfig = {
            "primary_version": primary_version,
            "secondary_version": secondary_version,
            "primary_observability_detected": primary_observability,
            "secondary_observability_detected": secondary_observability,
            "has_observability": primary_observability or secondary_observability,
        }

        critical_failures = self.reporter.critical_failures()
        return len(critical_failures) == 0, config
