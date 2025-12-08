"""
RBAC validation module for ACM Switchover.

This module provides functions to validate that the current user or service account
has the required RBAC permissions to execute ACM switchover operations.
"""

import logging
from typing import Dict, List, Optional, Tuple

from lib import KubeClient
from lib.exceptions import ValidationError

logger = logging.getLogger("acm_switchover")


class RBACValidator:
    """Validates RBAC permissions for ACM switchover operations."""

    # Required cluster-scoped permissions
    CLUSTER_PERMISSIONS = [
        ("", "namespaces", ["get"]),
        ("", "nodes", ["get", "list"]),  # For cluster health validation per runbook
        ("config.openshift.io", "clusteroperators", ["get", "list"]),  # For OpenShift health
        ("config.openshift.io", "clusterversions", ["get", "list"]),  # For upgrade status check
        ("cluster.open-cluster-management.io", "managedclusters", ["get", "list", "patch"]),
        ("hive.openshift.io", "clusterdeployments", ["get", "list"]),
        ("operator.open-cluster-management.io", "multiclusterhubs", ["get", "list"]),
        ("observability.open-cluster-management.io", "multiclusterobservabilities", ["get", "list"]),
    ]

    # Required namespace-scoped permissions by namespace
    NAMESPACE_PERMISSIONS = {
        "open-cluster-management-backup": [
            ("", "configmaps", ["get", "list", "create", "patch", "delete"]),
            ("", "secrets", ["get"]),
            ("", "pods", ["get", "list"]),  # For Velero pod health checks
            ("cluster.open-cluster-management.io", "backupschedules", ["get", "list", "create", "patch", "delete"]),
            ("cluster.open-cluster-management.io", "restores", ["get", "list", "create", "patch", "delete"]),
            ("velero.io", "backups", ["get", "list"]),
            ("velero.io", "restores", ["get", "list"]),  # For monitoring restore status
            ("velero.io", "backupstoragelocations", ["get", "list"]),  # For storage health check
            ("oadp.openshift.io", "dataprotectionapplications", ["get", "list"]),
        ],
        "open-cluster-management": [
            ("", "pods", ["get", "list"]),  # For ACM pod health checks
        ],
        "open-cluster-management-agent": [
            ("", "secrets", ["create", "delete"]),  # For klusterlet reconnection
            ("apps", "deployments", ["patch"]),  # For klusterlet restart
        ],
        "open-cluster-management-observability": [
            ("", "pods", ["get", "list"]),
            ("", "secrets", ["get"]),  # For Thanos object storage config
            ("apps", "deployments", ["get", "patch"]),
            ("apps", "statefulsets", ["get", "patch"]),
            ("route.openshift.io", "routes", ["get"]),  # For Grafana route access
        ],
        "multicluster-engine": [
            ("", "configmaps", ["get", "list", "create", "patch"]),
        ],
    }

    # Permissions required for decommission operation
    DECOMMISSION_PERMISSIONS = [
        ("cluster.open-cluster-management.io", "managedclusters", ["delete"]),
        ("operator.open-cluster-management.io", "multiclusterhubs", ["delete"]),
        ("observability.open-cluster-management.io", "multiclusterobservabilities", ["delete"]),
    ]

    def __init__(self, client: KubeClient):
        """
        Initialize RBAC validator.

        Args:
            client: KubeClient instance to use for validation
        """
        self.client = client

    def check_permission(
        self, api_group: str, resource: str, verb: str, namespace: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Check if current user/service account has specific permission.

        Args:
            api_group: API group (empty string for core)
            resource: Resource type (plural form)
            verb: Permission verb (get, list, create, patch, delete, etc.)
            namespace: Namespace for namespaced resources, None for cluster-scoped

        Returns:
            Tuple of (has_permission, error_message)
        """
        try:
            # Use kubectl auth can-i equivalent via Kubernetes API
            # SelfSubjectAccessReview to check permissions
            from kubernetes import client as k8s_client

            api_instance = k8s_client.AuthorizationV1Api(self.client.core_v1.api_client)

            # Construct resource attributes
            resource_attrs = k8s_client.V1ResourceAttributes(
                verb=verb,
                resource=resource,
                group=api_group if api_group else None,
                namespace=namespace,
            )

            # Create SelfSubjectAccessReview
            body = k8s_client.V1SelfSubjectAccessReview(
                spec=k8s_client.V1SelfSubjectAccessReviewSpec(
                    resource_attributes=resource_attrs
                )
            )

            # Check permission
            response = api_instance.create_self_subject_access_review(body)

            if response.status.allowed:
                return True, ""
            else:
                reason = response.status.reason or "Permission denied"
                return False, reason

        except Exception as e:
            logger.warning("Failed to check permission %s/%s: %s", resource, verb, e)
            return False, f"Error checking permission: {str(e)}"

    def validate_cluster_permissions(
        self, include_decommission: bool = False, skip_observability: bool = False
    ) -> Tuple[bool, List[str]]:
        """
        Validate cluster-scoped permissions.

        Args:
            include_decommission: Whether to check decommission permissions
            skip_observability: Whether to skip observability permission checks

        Returns:
            Tuple of (all_valid, list of error messages)
        """
        errors = []
        all_valid = True

        logger.info("Validating cluster-scoped RBAC permissions...")

        # Check standard cluster permissions
        for api_group, resource, verbs in self.CLUSTER_PERMISSIONS:
            # Skip observability permissions if requested
            if skip_observability and "observability" in api_group:
                logger.info("Skipping observability permission: %s/%s", api_group, resource)
                continue

            for verb in verbs:
                has_perm, error = self.check_permission(api_group, resource, verb)
                if not has_perm:
                    all_valid = False
                    group_name = api_group if api_group else "core"
                    error_msg = f"Missing permission: {verb} {group_name}/{resource}"
                    if error:
                        error_msg += f" - {error}"
                    errors.append(error_msg)
                    logger.error(error_msg)

        # Check decommission permissions if requested
        if include_decommission:
            for api_group, resource, verbs in self.DECOMMISSION_PERMISSIONS:
                for verb in verbs:
                    has_perm, error = self.check_permission(api_group, resource, verb)
                    if not has_perm:
                        all_valid = False
                        group_name = api_group if api_group else "core"
                        error_msg = (
                            f"Missing decommission permission: {verb} {group_name}/{resource}"
                        )
                        if error:
                            error_msg += f" - {error}"
                        errors.append(error_msg)
                        logger.error(error_msg)

        if all_valid:
            logger.info("✓ All cluster-scoped permissions validated")
        else:
            logger.error("✗ Cluster-scoped permission validation failed")

        return all_valid, errors

    def validate_namespace_permissions(
        self, skip_observability: bool = False
    ) -> Tuple[bool, List[str]]:
        """
        Validate namespace-scoped permissions.

        Args:
            skip_observability: Whether to skip observability namespace checks

        Returns:
            Tuple of (all_valid, list of error messages)
        """
        errors = []
        all_valid = True

        logger.info("Validating namespace-scoped RBAC permissions...")

        for namespace, permissions in self.NAMESPACE_PERMISSIONS.items():
            # Skip observability if requested
            if skip_observability and "observability" in namespace:
                logger.info("Skipping observability namespace: %s", namespace)
                continue

            # Check if namespace exists first
            if not self.client.namespace_exists(namespace):
                warning = f"Namespace {namespace} does not exist - skipping permission checks"
                logger.warning(warning)
                errors.append(warning)
                all_valid = False
                continue

            logger.info("Checking permissions in namespace: %s", namespace)

            for api_group, resource, verbs in permissions:
                for verb in verbs:
                    has_perm, error = self.check_permission(
                        api_group, resource, verb, namespace
                    )
                    if not has_perm:
                        all_valid = False
                        group_name = api_group if api_group else "core"
                        error_msg = (
                            f"Missing permission in {namespace}: "
                            f"{verb} {group_name}/{resource}"
                        )
                        if error:
                            error_msg += f" - {error}"
                        errors.append(error_msg)
                        logger.error(error_msg)

        if all_valid:
            logger.info("✓ All namespace-scoped permissions validated")
        else:
            logger.error("✗ Namespace-scoped permission validation failed")

        return all_valid, errors

    def validate_all_permissions(
        self, include_decommission: bool = False, skip_observability: bool = False
    ) -> Tuple[bool, Dict[str, List[str]]]:
        """
        Validate all required RBAC permissions.

        Args:
            include_decommission: Whether to check decommission permissions
            skip_observability: Whether to skip observability checks

        Returns:
            Tuple of (all_valid, dict of errors by category)
        """
        all_errors: Dict[str, List[str]] = {}

        # Validate cluster permissions
        cluster_valid, cluster_errors = self.validate_cluster_permissions(
            include_decommission, skip_observability
        )
        if cluster_errors:
            all_errors["cluster"] = cluster_errors

        # Validate namespace permissions
        namespace_valid, namespace_errors = self.validate_namespace_permissions(
            skip_observability
        )
        if namespace_errors:
            all_errors["namespaces"] = namespace_errors

        all_valid = cluster_valid and namespace_valid

        if all_valid:
            logger.info("✓ All RBAC permissions validated successfully")
        else:
            logger.error("✗ RBAC permission validation failed")
            logger.error("Error summary:")
            for category, error_list in all_errors.items():
                logger.error("  %s: %d errors", category, len(error_list))

        return all_valid, all_errors

    def generate_permission_report(
        self, include_decommission: bool = False, skip_observability: bool = False
    ) -> str:
        """
        Generate a detailed permission validation report.

        Args:
            include_decommission: Whether to check decommission permissions
            skip_observability: Whether to skip observability checks

        Returns:
            Formatted report string
        """
        all_valid, all_errors = self.validate_all_permissions(
            include_decommission, skip_observability
        )

        report = ["=" * 80]
        report.append("RBAC PERMISSION VALIDATION REPORT")
        report.append("=" * 80)
        report.append("")

        if all_valid:
            report.append("✓ STATUS: ALL PERMISSIONS VALIDATED")
            report.append("")
            report.append("The current user/service account has all required permissions")
            report.append("to execute ACM switchover operations.")
        else:
            report.append("✗ STATUS: PERMISSION VALIDATION FAILED")
            report.append("")
            report.append("The following permissions are missing:")
            report.append("")

            for category, error_list in all_errors.items():
                report.append(f"{category.upper()} PERMISSIONS:")
                for error in error_list:
                    report.append(f"  - {error}")
                report.append("")

            report.append("REMEDIATION:")
            report.append("")
            report.append("To fix these issues:")
            report.append("  1. Apply RBAC manifests from deploy/rbac/ directory")
            report.append("  2. Use Kustomize: kubectl apply -k deploy/kustomize/base/")
            report.append("  3. Use Helm: helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/")
            report.append("")
            report.append("For more information, see docs/RBAC_REQUIREMENTS.md")

        report.append("=" * 80)
        return "\n".join(report)


def validate_rbac_permissions(
    primary_client: KubeClient,
    secondary_client: Optional[KubeClient] = None,
    include_decommission: bool = False,
    skip_observability: bool = False,
) -> None:
    """
    Validate RBAC permissions on primary and optionally secondary hub.

    Args:
        primary_client: KubeClient for primary hub
        secondary_client: Optional KubeClient for secondary hub
        include_decommission: Whether to check decommission permissions
        skip_observability: Whether to skip observability checks

    Raises:
        ValidationError: If RBAC validation fails
    """
    logger.info("Starting RBAC permission validation...")

    # Validate primary hub
    logger.info("Validating RBAC permissions on primary hub...")
    primary_validator = RBACValidator(primary_client)
    primary_valid, primary_errors = primary_validator.validate_all_permissions(
        include_decommission=include_decommission, skip_observability=skip_observability
    )

    if not primary_valid:
        report = primary_validator.generate_permission_report(
            include_decommission, skip_observability
        )
        logger.error("\n%s", report)
        raise ValidationError(
            "RBAC permission validation failed on primary hub. "
            "See report above for details."
        )

    # Validate secondary hub if provided
    if secondary_client:
        logger.info("Validating RBAC permissions on secondary hub...")
        secondary_validator = RBACValidator(secondary_client)
        secondary_valid, secondary_errors = secondary_validator.validate_all_permissions(
            include_decommission=False,  # Decommission only on primary
            skip_observability=skip_observability,
        )

        if not secondary_valid:
            report = secondary_validator.generate_permission_report(
                include_decommission=False, skip_observability=skip_observability
            )
            logger.error("\n%s", report)
            raise ValidationError(
                "RBAC permission validation failed on secondary hub. "
                "See report above for details."
            )

    logger.info("✓ RBAC permission validation completed successfully")
