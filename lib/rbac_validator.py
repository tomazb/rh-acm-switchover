"""
RBAC validation module for ACM Switchover.

This module provides functions to validate that the current user or service account
has the required RBAC permissions to execute ACM switchover operations.

The module supports two roles:
- operator: Full permissions for executing switchover operations
- validator: Read-only permissions for validation and dry-run operations
"""

import logging
from copy import deepcopy
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple, Union

from kubernetes.client.rest import ApiException

from lib import KubeClient
from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    HIVE_CLUSTERDEPLOYMENT_API_GROUP,
    HIVE_CLUSTERDEPLOYMENT_PLURAL,
    HUB_ROLE_PRIMARY,
    HUB_ROLE_SECONDARY,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_PLURAL,
    MCE_NAMESPACE,
    MULTICLUSTEROBSERVABILITIES_PLURAL,
    OBSERVABILITY_API_GROUP,
    OBSERVABILITY_NAMESPACE,
    RBAC_CACHE_KEY_ALL_PERMISSIONS,
    RBAC_CACHE_KEY_DECOMMISSION_PERMISSIONS,
)
from lib.exceptions import ValidationError

logger = logging.getLogger("acm_switchover")

# Valid roles for RBAC validation
VALID_ROLES = ("operator", "validator")
# Valid Argo CD RBAC validation modes
VALID_ARGOCD_MODES = ("none", "check", "manage")


def _validate_argocd_mode(argocd_mode: str) -> None:
    """Validate Argo CD RBAC mode."""
    if argocd_mode not in VALID_ARGOCD_MODES:
        raise ValueError(f"Invalid argocd_mode '{argocd_mode}'. Must be one of: {VALID_ARGOCD_MODES}")


# Verbs that mutate cluster state. Single source for validator-table derivation
# and RBACValidator._is_write_verb().
MUTATING_VERBS = frozenset({"create", "update", "patch", "delete"})

# The one intentional operator-vs-validator cluster-table difference: the
# operator activates managed clusters during switchover via "patch"; the
# read-only validator role must never receive it. Any other mutating verb added
# to OPERATOR_CLUSTER_PERMISSIONS must be recorded here deliberately.
VALIDATOR_CLUSTER_VERB_EXCEPTIONS: Dict[Tuple[str, str], FrozenSet[str]] = {
    (MANAGED_CLUSTER_API_GROUP, MANAGED_CLUSTER_PLURAL): frozenset({"patch"}),
}


def _format_verb_removals(removals: Dict[Tuple[str, str], FrozenSet[str]]) -> Dict[Tuple[str, str], List[str]]:
    """Render a removals mapping with deterministic ordering for error messages."""
    return {key: sorted(verbs) for key, verbs in sorted(removals.items())}


def _derive_read_only_permissions(
    operator_permissions: List[Tuple[str, str, List[str]]],
    expected_removals: Dict[Tuple[str, str], FrozenSet[str]],
) -> List[Tuple[str, str, List[str]]]:
    """Derive a read-only permission table by stripping mutating verbs.

    Fails fast (at import time for the class tables) when the stripped verbs do
    not exactly match the documented exception data, so validator-table changes
    are always a conscious decision rather than silent drift.
    """
    derived: List[Tuple[str, str, List[str]]] = []
    removed: Dict[Tuple[str, str], FrozenSet[str]] = {}
    for api_group, resource, verbs in operator_permissions:
        read_verbs = [verb for verb in verbs if verb not in MUTATING_VERBS]
        stripped = frozenset(verbs) - frozenset(read_verbs)
        if stripped:
            removed[(api_group, resource)] = stripped
        if read_verbs:
            derived.append((api_group, resource, read_verbs))
    if removed != expected_removals:
        raise ValueError(
            "Validator cluster permission derivation drifted from the documented exceptions: "
            f"removed {_format_verb_removals(removed)!r}, "
            f"expected {_format_verb_removals(expected_removals)!r}. "
            "Update VALIDATOR_CLUSTER_VERB_EXCEPTIONS deliberately when changing "
            "mutating verbs in OPERATOR_CLUSTER_PERMISSIONS."
        )
    return derived


class RBACValidator:
    """Validates RBAC permissions for ACM switchover operations."""

    # Required cluster-scoped permissions for OPERATOR role
    OPERATOR_CLUSTER_PERMISSIONS = [
        ("", "namespaces", ["get", "list"]),
        ("", "nodes", ["get", "list"]),  # For cluster health validation per runbook
        (
            "config.openshift.io",
            "clusteroperators",
            ["get", "list"],
        ),  # For OpenShift health
        (
            "config.openshift.io",
            "clusterversions",
            ["get", "list"],
        ),  # For upgrade status check
        (
            "cluster.open-cluster-management.io",
            "managedclusters",
            ["get", "list", "patch"],
        ),
        ("hive.openshift.io", "clusterdeployments", ["get", "list"]),
        ("operator.open-cluster-management.io", "multiclusterhubs", ["get", "list"]),
        (
            "observability.open-cluster-management.io",
            "multiclusterobservabilities",
            ["get", "list"],
        ),
    ]

    # Required cluster-scoped permissions for VALIDATOR role (read-only),
    # derived from the operator table by stripping mutating verbs. The only
    # verb stripped today is managedclusters "patch"
    # (see VALIDATOR_CLUSTER_VERB_EXCEPTIONS).
    VALIDATOR_CLUSTER_PERMISSIONS = _derive_read_only_permissions(
        OPERATOR_CLUSTER_PERMISSIONS, VALIDATOR_CLUSTER_VERB_EXCEPTIONS
    )

    # Alias for backwards compatibility
    CLUSTER_PERMISSIONS = OPERATOR_CLUSTER_PERMISSIONS

    # Required namespace-scoped permissions for OPERATOR role on HUB clusters
    OPERATOR_HUB_NAMESPACE_PERMISSIONS = {
        BACKUP_NAMESPACE: [
            ("", "configmaps", ["get", "list", "create", "patch", "delete"]),
            ("", "secrets", ["get"]),
            ("", "pods", ["get", "list"]),  # For Velero pod health checks
            (
                "cluster.open-cluster-management.io",
                "backupschedules",
                ["get", "list", "create", "patch", "delete"],
            ),
            (
                "cluster.open-cluster-management.io",
                "restores",
                ["get", "list", "create", "patch", "delete"],
            ),
            ("velero.io", "backups", ["get", "list"]),
            ("velero.io", "restores", ["get", "list"]),  # For monitoring restore status
            (
                "velero.io",
                "backupstoragelocations",
                ["get", "list"],
            ),  # For storage health check
            ("oadp.openshift.io", "dataprotectionapplications", ["get", "list"]),
        ],
        ACM_NAMESPACE: [
            ("", "pods", ["get", "list"]),  # For ACM pod health checks
        ],
        OBSERVABILITY_NAMESPACE: [
            ("", "pods", ["get", "list"]),
            ("", "secrets", ["get"]),  # For Thanos object storage config
            ("apps", "deployments", ["get", "patch"]),
            ("apps", "statefulsets", ["get", "patch"]),
            (
                "apps",
                "statefulsets/scale",
                ["get", "patch"],
            ),  # For Thanos compactor scaling
            ("route.openshift.io", "routes", ["get"]),  # For Grafana route access
        ],
        MCE_NAMESPACE: [
            ("", "configmaps", ["get", "list", "create", "patch", "delete"]),
        ],
    }

    # Required namespace-scoped permissions for VALIDATOR role on HUB clusters (read-only)
    VALIDATOR_HUB_NAMESPACE_PERMISSIONS = {
        BACKUP_NAMESPACE: [
            ("", "configmaps", ["get", "list"]),
            ("", "secrets", ["get"]),  # For Thanos config validation
            ("", "pods", ["get", "list"]),
            ("cluster.open-cluster-management.io", "backupschedules", ["get", "list"]),
            ("cluster.open-cluster-management.io", "restores", ["get", "list"]),
            ("velero.io", "backups", ["get", "list"]),
            ("velero.io", "restores", ["get", "list"]),
            ("velero.io", "backupstoragelocations", ["get", "list"]),
            ("oadp.openshift.io", "dataprotectionapplications", ["get", "list"]),
        ],
        ACM_NAMESPACE: [
            ("", "pods", ["get", "list"]),
        ],
        OBSERVABILITY_NAMESPACE: [
            ("", "pods", ["get", "list"]),
            ("", "secrets", ["get"]),
            ("apps", "deployments", ["get", "list"]),  # No patch for validator
            ("apps", "statefulsets", ["get", "list"]),  # No patch for validator
            ("route.openshift.io", "routes", ["get"]),
        ],
        MCE_NAMESPACE: [
            ("", "configmaps", ["get", "list"]),  # No create/patch/delete for validator
        ],
    }

    # Required namespace-scoped permissions for OPERATOR role on MANAGED clusters
    # These are only needed when connecting to managed clusters for klusterlet operations
    OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS = {
        MANAGED_CLUSTER_AGENT_NAMESPACE: [
            ("", "secrets", ["get", "create", "patch"]),  # For klusterlet reconnection
            ("apps", "deployments", ["get", "patch"]),  # For klusterlet restart
        ],
    }

    # Required namespace-scoped permissions for VALIDATOR role on MANAGED clusters (read-only)
    VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS = {
        MANAGED_CLUSTER_AGENT_NAMESPACE: [
            ("", "secrets", ["get"]),  # Read-only for validation
            ("apps", "deployments", ["get"]),  # Read-only for validation
        ],
    }

    # Alias for backwards compatibility - combines hub permissions only
    # (agent namespace is on managed clusters, not hubs)
    NAMESPACE_PERMISSIONS = OPERATOR_HUB_NAMESPACE_PERMISSIONS

    # Permissions required for decommission operation (operator only)
    DECOMMISSION_PERMISSIONS = [
        ("cluster.open-cluster-management.io", "managedclusters", ["delete"]),
        ("operator.open-cluster-management.io", "multiclusterhubs", ["delete"]),
        (
            "observability.open-cluster-management.io",
            "multiclusterobservabilities",
            ["delete"],
        ),
    ]

    # Permissions required by normal finalization when the old hub remains as
    # secondary and observability was detected there.
    OLD_HUB_FINALIZATION_PERMISSIONS = [
        (
            OBSERVABILITY_API_GROUP,
            MULTICLUSTEROBSERVABILITIES_PLURAL,
            ["delete"],
        ),
    ]

    # Standalone decommission only needs the teardown surface used by modules/decommission.py.
    DECOMMISSION_CLUSTER_PERMISSIONS = [
        ("", "namespaces", ["get"]),
        (HIVE_CLUSTERDEPLOYMENT_API_GROUP, HIVE_CLUSTERDEPLOYMENT_PLURAL, ["list"]),
        ("cluster.open-cluster-management.io", "managedclusters", ["list", "delete"]),
        ("operator.open-cluster-management.io", "multiclusterhubs", ["list", "delete"]),
        (
            "observability.open-cluster-management.io",
            "multiclusterobservabilities",
            ["list", "delete"],
        ),
    ]

    DECOMMISSION_NAMESPACE_PERMISSIONS = {
        ACM_NAMESPACE: [
            ("", "pods", ["get", "list"]),
        ],
        OBSERVABILITY_NAMESPACE: [
            ("", "pods", ["get", "list"]),
        ],
    }

    # F9 fix: Argo CD RBAC is split into base permissions (always needed)
    # and operator-install-only permissions (argocds get/list).
    # Base permissions needed for any Argo CD install type (vanilla or operator)
    ARGOCD_BASE_CLUSTER_PERMISSIONS = [
        ("argoproj.io", "applications", ["get", "list"]),
        ("apiextensions.k8s.io", "customresourcedefinitions", ["get"]),
    ]

    # Additional permissions only required for operator-installed Argo CD
    ARGOCD_OPERATOR_CLUSTER_PERMISSIONS = [
        ("argoproj.io", "argocds", ["get", "list"]),
    ]

    # Backwards-compatible alias (includes all permissions)
    ARGOCD_CHECK_CLUSTER_PERMISSIONS = ARGOCD_BASE_CLUSTER_PERMISSIONS + ARGOCD_OPERATOR_CLUSTER_PERMISSIONS

    # Additional Argo CD write permissions required for --argocd-manage (operator role)
    ARGOCD_MANAGE_EXTRA_CLUSTER_PERMISSIONS = [
        ("argoproj.io", "applications", ["patch"]),
    ]

    def __init__(self, client: KubeClient, role: str = "operator"):
        """
        Initialize RBAC validator.

        Args:
            client: KubeClient instance to use for validation
            role: Role to validate for ("operator" or "validator")
        """
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {VALID_ROLES}")
        self.client = client
        self.role = role
        self._permission_cache: Dict[Tuple[str, str, str, Optional[str]], Union[Tuple[bool, str], str]] = {}
        self._namespace_exists_cache: Dict[str, bool] = {}
        self._validation_result_cache: Dict[Tuple[Any, ...], Tuple[bool, Dict[str, List[str]]]] = {}

    def _cached_validation_result(
        self,
        cache_key: Tuple[Any, ...],
        builder: Callable[[], Tuple[bool, Dict[str, List[str]]]],
    ) -> Tuple[bool, Dict[str, List[str]]]:
        """Cache validation summaries and return deep-copied results to callers."""
        if cache_key not in self._validation_result_cache:
            self._validation_result_cache[cache_key] = builder()
        return deepcopy(self._validation_result_cache[cache_key])

    def _namespace_exists_cached(self, namespace: str) -> bool:
        """Return namespace existence, caching results for repeated validation passes."""
        if namespace not in self._namespace_exists_cache:
            self._namespace_exists_cache[namespace] = self.client.namespace_exists(namespace)
        return self._namespace_exists_cache[namespace]

    def _get_cluster_permissions(self) -> List[Tuple[str, str, List[str]]]:
        """Get cluster permissions based on role."""
        if self.role == "validator":
            return self.VALIDATOR_CLUSTER_PERMISSIONS
        return self.OPERATOR_CLUSTER_PERMISSIONS

    def _get_hub_namespace_permissions(
        self,
    ) -> Dict[str, List[Tuple[str, str, List[str]]]]:
        """Get hub namespace permissions based on role."""
        if self.role == "validator":
            return self.VALIDATOR_HUB_NAMESPACE_PERMISSIONS
        return self.OPERATOR_HUB_NAMESPACE_PERMISSIONS

    def _get_managed_cluster_namespace_permissions(
        self,
    ) -> Dict[str, List[Tuple[str, str, List[str]]]]:
        """Get managed cluster namespace permissions based on role."""
        if self.role == "validator":
            return self.VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS
        return self.OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS

    def _is_write_verb(self, verb: str) -> bool:
        """Check if a verb is a write operation."""
        return verb in MUTATING_VERBS

    def _get_argocd_cluster_permissions(
        self, argocd_mode: str, argocd_install_type: str = "unknown"
    ) -> List[Tuple[str, str, List[str]]]:
        """Get Argo CD cluster permissions based on mode, role, and install type.

        Args:
            argocd_mode: 'none', 'check', or 'manage'
            argocd_install_type: 'vanilla', 'operator', or 'unknown'. When 'vanilla',
                argocds permissions are omitted since the CRD does not exist.
        """
        _validate_argocd_mode(argocd_mode)

        if argocd_mode == "manage" and self.role == "validator":
            raise ValueError("validator role cannot use argocd_mode='manage'")
        if argocd_mode == "none":
            return []
        if argocd_install_type == "none":
            return []

        permissions = list(self.ARGOCD_BASE_CLUSTER_PERMISSIONS)
        # F9: Only require argocds permissions when operator-installed Argo CD
        # is present. Vanilla installs have no argocds CRD.
        if argocd_install_type != "vanilla":
            permissions.extend(self.ARGOCD_OPERATOR_CLUSTER_PERMISSIONS)
        if argocd_mode == "manage" and self.role == "operator":
            permissions.extend(self.ARGOCD_MANAGE_EXTRA_CLUSTER_PERMISSIONS)
        return permissions

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

        Raises:
            ValidationError: If the permission self-check itself cannot be completed
        """
        cache_key = (api_group, resource, verb, namespace)
        cached = self._permission_cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, str):
                raise ValidationError(cached)
            return cached

        group_name = api_group if api_group else "core"
        scope = f"namespace '{namespace}'" if namespace else "cluster scope"
        try:
            # Use kubectl auth can-i equivalent via Kubernetes API
            # SelfSubjectAccessReview to check permissions
            from kubernetes import client as k8s_client

            api_instance = k8s_client.AuthorizationV1Api(self.client.core_v1.api_client)

            # Construct resource attributes. Kubernetes SSAR expects subresources
            # such as statefulsets/scale to be split into separate fields.
            resource_name = resource
            subresource = None
            if "/" in resource:
                resource_name, subresource = resource.split("/", 1)

            resource_attrs = k8s_client.V1ResourceAttributes(
                verb=verb,
                resource=resource_name,
                subresource=subresource,
                group=api_group if api_group else None,
                namespace=namespace,
            )

            # Create SelfSubjectAccessReview
            body = k8s_client.V1SelfSubjectAccessReview(
                spec=k8s_client.V1SelfSubjectAccessReviewSpec(resource_attributes=resource_attrs)
            )

            # Check permission
            response = api_instance.create_self_subject_access_review(body)

            if response.status.allowed:
                result: Tuple[bool, str] = (True, "")
            else:
                reason = response.status.reason or "Permission denied"
                result = (False, reason)
            self._permission_cache[cache_key] = result
            return result

        except ApiException as e:
            error_message = (
                f"Unable to check permission {verb} {group_name}/{resource} on {scope}: {e.status} {e.reason}"
            )
            self._permission_cache[cache_key] = error_message
            raise ValidationError(error_message) from e
        except Exception as e:
            error_message = f"Unable to check permission {verb} {group_name}/{resource} on {scope}: {e}"
            self._permission_cache[cache_key] = error_message
            raise ValidationError(error_message) from e

    def validate_cluster_permissions(
        self,
        include_decommission: bool = False,
        include_old_hub_finalization: bool = False,
        skip_observability: bool = False,
        argocd_mode: str = "none",
        argocd_install_type: str = "unknown",
    ) -> Tuple[bool, List[str]]:
        """
        Validate cluster-scoped permissions based on role.

        Args:
            include_decommission: Whether to check decommission permissions (operator only)
            include_old_hub_finalization: Whether to check old-hub finalization
                permissions used when normal finalization deletes MCO
            skip_observability: Whether to skip observability permission checks
            argocd_mode: Argo CD RBAC mode ('none', 'check', or 'manage')
            argocd_install_type: 'vanilla', 'operator', or 'unknown'

        Returns:
            Tuple of (all_valid, list of error messages)

        Raises:
            ValidationError: If permission checks cannot be completed due to API or client errors
        """
        errors = []
        all_valid = True

        logger.info("Validating cluster-scoped RBAC permissions for role: %s", self.role)

        # Get permissions based on role
        cluster_permissions = self._get_cluster_permissions()

        # Check standard cluster permissions
        for api_group, resource, verbs in cluster_permissions:
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

        # Check Argo CD permissions if requested
        argocd_permissions = self._get_argocd_cluster_permissions(argocd_mode, argocd_install_type)
        if argocd_permissions:
            logger.info("Including Argo CD RBAC checks (mode: %s)", argocd_mode)
            for api_group, resource, verbs in argocd_permissions:
                for verb in verbs:
                    has_perm, error = self.check_permission(api_group, resource, verb)
                    if not has_perm:
                        all_valid = False
                        group_name = api_group if api_group else "core"
                        error_msg = f"Missing Argo CD permission: {verb} {group_name}/{resource}"
                        if error:
                            error_msg += f" - {error}"
                        errors.append(error_msg)
                        logger.error(error_msg)

        checked_extra_permissions = set()

        # Check decommission permissions if requested (operator role only)
        if include_decommission:
            if self.role != "operator":
                # F7 fix: Reject this combination explicitly instead of silently skipping.
                raise ValueError(
                    "include_decommission=True is not valid for the validator role. "
                    "Decommission permissions are only applicable to the operator role."
                )

            for api_group, resource, verbs in self.DECOMMISSION_PERMISSIONS:
                if skip_observability and "observability" in api_group:
                    logger.info("Skipping observability decommission permission: %s/%s", api_group, resource)
                    continue

                for verb in verbs:
                    permission_key = (api_group, resource, verb)
                    if permission_key in checked_extra_permissions:
                        continue
                    checked_extra_permissions.add(permission_key)

                    has_perm, error = self.check_permission(api_group, resource, verb)
                    if not has_perm:
                        all_valid = False
                        group_name = api_group if api_group else "core"
                        error_msg = f"Missing decommission permission: {verb} {group_name}/{resource}"
                        if error:
                            error_msg += f" - {error}"
                        errors.append(error_msg)
                        logger.error(error_msg)

        if include_old_hub_finalization:
            if self.role != "operator":
                raise ValueError(
                    "include_old_hub_finalization=True is not valid for the validator role. "
                    "Old-hub finalization delete permissions are only applicable to the operator role."
                )

            if not skip_observability:
                for api_group, resource, verbs in self.OLD_HUB_FINALIZATION_PERMISSIONS:
                    for verb in verbs:
                        permission_key = (api_group, resource, verb)
                        if permission_key in checked_extra_permissions:
                            continue
                        checked_extra_permissions.add(permission_key)

                        has_perm, error = self.check_permission(api_group, resource, verb)
                        if not has_perm:
                            all_valid = False
                            group_name = api_group if api_group else "core"
                            error_msg = f"Missing old-hub finalization permission: {verb} {group_name}/{resource}"
                            if error:
                                error_msg += f" - {error}"
                            errors.append(error_msg)
                            logger.error(error_msg)

        if all_valid:
            logger.info("✓ All cluster-scoped permissions validated for role: %s", self.role)
        else:
            logger.error("✗ Cluster-scoped permission validation failed for role: %s", self.role)

        return all_valid, errors

    def validate_namespace_permissions(
        self, skip_observability: bool = False, skip_agent_namespace: bool = True
    ) -> Tuple[bool, List[str]]:
        """
        Validate namespace-scoped permissions on hub clusters.

        Args:
            skip_observability: Whether to skip observability namespace checks
            skip_agent_namespace: Whether to skip open-cluster-management-agent namespace
                                  (it exists on managed clusters, not hubs). Default True.

        Returns:
            Tuple of (all_valid, list of error messages)

        Raises:
            ValidationError: If permission checks cannot be completed due to API or client errors
        """
        errors = []
        all_valid = True

        logger.info("Validating namespace-scoped RBAC permissions for role: %s", self.role)

        # Get permissions based on role
        namespace_permissions = self._get_hub_namespace_permissions()

        for namespace, permissions in namespace_permissions.items():
            # Skip observability namespace if requested
            if skip_observability and "observability" in namespace:
                logger.info("Skipping observability namespace: %s", namespace)
                continue

            # Skip agent namespace on hubs (it exists on managed clusters)
            if skip_agent_namespace and namespace == MANAGED_CLUSTER_AGENT_NAMESPACE:
                logger.info(
                    "Skipping agent namespace: %s (exists on managed clusters only)",
                    namespace,
                )
                continue

            # Check if namespace exists first
            if not self._namespace_exists_cached(namespace):
                warning = f"Namespace {namespace} does not exist - skipping permission checks"
                logger.warning(warning)
                errors.append(warning)
                all_valid = False
                continue

            logger.info("Checking permissions in namespace: %s", namespace)

            for api_group, resource, verbs in permissions:
                for verb in verbs:
                    has_perm, error = self.check_permission(api_group, resource, verb, namespace)
                    if not has_perm:
                        all_valid = False
                        group_name = api_group if api_group else "core"
                        error_msg = f"Missing permission in {namespace}: " f"{verb} {group_name}/{resource}"
                        if error:
                            error_msg += f" - {error}"
                        errors.append(error_msg)
                        logger.error(error_msg)

        if all_valid:
            logger.info("✓ All namespace-scoped permissions validated")
        else:
            logger.error("✗ Namespace-scoped permission validation failed")

        return all_valid, errors

    def validate_managed_cluster_permissions(self) -> Tuple[bool, List[str]]:
        """
        Validate namespace-scoped permissions on managed clusters.

        This validates permissions in the open-cluster-management-agent namespace,
        which exists on managed clusters (not hubs) and is used for klusterlet
        reconnection operations during switchover.

        Returns:
            Tuple of (all_valid, list of error messages)
        """
        errors = []
        all_valid = True

        logger.info("Validating managed cluster RBAC permissions for role: %s", self.role)

        # Get managed cluster permissions based on role
        namespace_permissions = self._get_managed_cluster_namespace_permissions()

        for namespace, permissions in namespace_permissions.items():
            # Check if namespace exists first
            if not self._namespace_exists_cached(namespace):
                warning = f"Namespace {namespace} does not exist - this may not be a managed cluster"
                logger.warning(warning)
                errors.append(warning)
                all_valid = False
                continue

            logger.info("Checking permissions in namespace: %s", namespace)

            for api_group, resource, verbs in permissions:
                for verb in verbs:
                    has_perm, error = self.check_permission(api_group, resource, verb, namespace)
                    if not has_perm:
                        all_valid = False
                        group_name = api_group if api_group else "core"
                        error_msg = f"Missing permission in {namespace}: {verb} {group_name}/{resource}"
                        if error:
                            error_msg += f" - {error}"
                        errors.append(error_msg)
                        logger.error(error_msg)

        if all_valid:
            logger.info("✓ All managed cluster permissions validated")
        else:
            logger.error("✗ Managed cluster permission validation failed")

        return all_valid, errors

    def validate_all_permissions(
        self,
        include_decommission: bool = False,
        include_old_hub_finalization: bool = False,
        skip_observability: bool = False,
        argocd_mode: str = "none",
        argocd_install_type: str = "unknown",
    ) -> Tuple[bool, Dict[str, List[str]]]:
        """
        Validate all required RBAC permissions.

        Args:
            include_decommission: Whether to check decommission permissions
            include_old_hub_finalization: Whether to check old-hub finalization
                permissions used when normal finalization deletes MCO
            skip_observability: Whether to skip observability checks
            argocd_mode: Argo CD RBAC mode ('none', 'check', or 'manage')
            argocd_install_type: 'vanilla', 'operator', or 'unknown'

        Returns:
            Tuple of (all_valid, dict of errors by category)

        Raises:
            ValidationError: If permission checks cannot be completed due to API or client errors
        """
        cache_key = (
            RBAC_CACHE_KEY_ALL_PERMISSIONS,
            include_decommission,
            include_old_hub_finalization,
            skip_observability,
            argocd_mode,
            argocd_install_type,
        )

        def builder() -> Tuple[bool, Dict[str, List[str]]]:
            all_errors: Dict[str, List[str]] = {}

            # Validate cluster permissions
            cluster_valid, cluster_errors = self.validate_cluster_permissions(
                include_decommission=include_decommission,
                include_old_hub_finalization=include_old_hub_finalization,
                skip_observability=skip_observability,
                argocd_mode=argocd_mode,
                argocd_install_type=argocd_install_type,
            )
            if cluster_errors:
                all_errors["cluster"] = cluster_errors

            # Validate namespace permissions
            namespace_valid, namespace_errors = self.validate_namespace_permissions(skip_observability)
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

        return self._cached_validation_result(cache_key, builder)

    def validate_decommission_permissions(
        self,
        skip_observability: bool = False,
    ) -> Tuple[bool, Dict[str, List[str]]]:
        """Validate only the permissions exercised by standalone decommission."""
        if self.role != "operator":
            raise ValueError("Decommission permissions are only applicable to the operator role.")

        cache_key = (RBAC_CACHE_KEY_DECOMMISSION_PERMISSIONS, skip_observability)

        def builder() -> Tuple[bool, Dict[str, List[str]]]:
            all_valid = True
            all_errors: Dict[str, List[str]] = {}
            cluster_errors: List[str] = []
            namespace_errors: List[str] = []

            logger.info("Validating standalone decommission RBAC permissions for role: %s", self.role)

            check_observability = not skip_observability
            if check_observability and not self._namespace_exists_cached(OBSERVABILITY_NAMESPACE):
                logger.info(
                    "Namespace %s does not exist - skipping observability decommission permission checks",
                    OBSERVABILITY_NAMESPACE,
                )
                check_observability = False

            for api_group, resource, verbs in self.DECOMMISSION_CLUSTER_PERMISSIONS:
                if not check_observability and "observability" in api_group:
                    logger.info("Skipping observability permission: %s/%s", api_group, resource)
                    continue

                for verb in verbs:
                    has_perm, error = self.check_permission(api_group, resource, verb, None)
                    if not has_perm:
                        all_valid = False
                        group_name = api_group if api_group else "core"
                        error_msg = f"Missing decommission permission: {verb} {group_name}/{resource}"
                        if error:
                            error_msg += f" - {error}"
                        cluster_errors.append(error_msg)
                        logger.error(error_msg)

            if cluster_errors:
                all_errors["cluster"] = cluster_errors

            for namespace, permissions in self.DECOMMISSION_NAMESPACE_PERMISSIONS.items():
                if namespace == OBSERVABILITY_NAMESPACE and not check_observability:
                    continue

                if not self._namespace_exists_cached(namespace):
                    if namespace == ACM_NAMESPACE:
                        # ACM namespace removal is expected after successful decommission.
                        # Treat as success to allow idempotent reruns.
                        logger.info(
                            "Namespace %s does not exist — ACM already removed, "
                            "skipping decommission permission checks for this namespace",
                            namespace,
                        )
                        continue
                    warning = f"Namespace {namespace} does not exist - skipping decommission permission checks"
                    logger.warning(warning)
                    namespace_errors.append(warning)
                    all_valid = False
                    continue

                logger.info("Checking decommission permissions in namespace: %s", namespace)

                for api_group, resource, verbs in permissions:
                    for verb in verbs:
                        has_perm, error = self.check_permission(api_group, resource, verb, namespace)
                        if not has_perm:
                            all_valid = False
                            group_name = api_group if api_group else "core"
                            error_msg = (
                                f"Missing decommission permission in {namespace}: {verb} {group_name}/{resource}"
                            )
                            if error:
                                error_msg += f" - {error}"
                            namespace_errors.append(error_msg)
                            logger.error(error_msg)

            if namespace_errors:
                all_errors["namespaces"] = namespace_errors

            if all_valid:
                logger.info("✓ Standalone decommission RBAC permissions validated successfully")
            else:
                logger.error("✗ Standalone decommission RBAC permission validation failed")
                logger.error("Error summary:")
                for category, error_list in all_errors.items():
                    logger.error("  %s: %d errors", category, len(error_list))

            return all_valid, all_errors

        return self._cached_validation_result(cache_key, builder)

    def generate_permission_report(
        self,
        include_decommission: bool = False,
        include_old_hub_finalization: bool = False,
        skip_observability: bool = False,
        argocd_mode: str = "none",
        argocd_install_type: str = "unknown",
    ) -> str:
        """
        Generate a detailed permission validation report.

        Args:
            include_decommission: Whether to check decommission permissions
            include_old_hub_finalization: Whether to check old-hub finalization
                permissions used when normal finalization deletes MCO
            skip_observability: Whether to skip observability checks
            argocd_mode: Argo CD RBAC mode ('none', 'check', or 'manage')
            argocd_install_type: 'vanilla', 'operator', or 'unknown'

        Returns:
            Formatted report string
        """
        all_valid, all_errors = self.validate_all_permissions(
            include_decommission=include_decommission,
            include_old_hub_finalization=include_old_hub_finalization,
            skip_observability=skip_observability,
            argocd_mode=argocd_mode,
            argocd_install_type=argocd_install_type,
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
            report.append("  1. Apply the baseline RBAC manifests under deploy/rbac/")
            if include_decommission:
                report.append("  2. Apply the opt-in decommission extension under deploy/rbac/extensions/decommission/")
                report.append(
                    "  3. Or use Helm with --set rbac.includeDecommissionClusterRole=true for operator teardown access"
                )
                report.append("  4. Or use Kustomize for the baseline and add the decommission manifests separately")
            else:
                report.append("  2. Use Kustomize: kubectl apply -k deploy/kustomize/base/")
                report.append("  3. Use Helm: helm install acm-switchover-rbac deploy/helm/acm-switchover-rbac/")
            report.append("")
            report.append("For more information, see docs/deployment/rbac-requirements.md")

        report.append("=" * 80)
        return "\n".join(report)


def _validate_hub(
    hub_role: str,
    client: KubeClient,
    *,
    include_decommission: bool,
    include_old_hub_finalization: bool,
    skip_observability: bool,
    argocd_mode: str,
    argocd_install_type: str,
    include_error_count: bool,
) -> None:
    """Validate RBAC permissions on one hub, preserving per-hub message shapes.

    include_error_count encodes the secondary-only failure-message behavior;
    every other primary/secondary asymmetry is passed in explicitly by the
    caller's hub table.
    """
    logger.info("Validating RBAC permissions on %s hub...", hub_role)
    validator = RBACValidator(client)
    try:
        valid, errors = validator.validate_all_permissions(
            include_decommission=include_decommission,
            include_old_hub_finalization=include_old_hub_finalization,
            skip_observability=skip_observability,
            argocd_mode=argocd_mode,
            argocd_install_type=argocd_install_type,
        )
    except ValidationError as exc:
        raise ValidationError(f"RBAC permission validation could not be completed on {hub_role} hub: {exc}") from exc

    if valid:
        return

    report = validator.generate_permission_report(
        include_decommission=include_decommission,
        include_old_hub_finalization=include_old_hub_finalization,
        skip_observability=skip_observability,
        argocd_mode=argocd_mode,
        argocd_install_type=argocd_install_type,
    )
    logger.error("\n%s", report)
    if include_error_count:
        error_count = sum(len(errs) for errs in errors.values())
        raise ValidationError(
            f"RBAC permission validation failed on {hub_role} hub ({error_count} error(s)). "
            "See report above for details."
        )
    raise ValidationError(f"RBAC permission validation failed on {hub_role} hub. See report above for details.")


def validate_rbac_permissions(
    primary_client: Optional[KubeClient] = None,
    secondary_client: Optional[KubeClient] = None,
    include_decommission: bool = False,
    include_old_hub_finalization: bool = False,
    skip_observability: bool = False,
    argocd_mode: str = "none",
    argocd_install_type: str = "unknown",
    secondary_argocd_install_type: Optional[str] = None,
) -> None:
    """
    Validate RBAC permissions on primary and/or secondary hub.

    At least one of primary_client or secondary_client must be provided.
    When primary_client is None (e.g. restore-only mode), only secondary
    hub permissions are validated.

    Args:
        primary_client: Optional KubeClient for primary hub
        secondary_client: Optional KubeClient for secondary hub
        include_decommission: Whether to check decommission permissions (requires primary_client)
        include_old_hub_finalization: Whether to check old-hub finalization
            permissions used when normal finalization deletes MCO on the primary hub
        skip_observability: Whether to skip observability checks
        argocd_mode: Argo CD RBAC mode ('none', 'check', or 'manage')
        argocd_install_type: 'vanilla', 'operator', or 'unknown'
        secondary_argocd_install_type: Secondary hub install type override.
            Falls back to argocd_install_type when not provided.

    Raises:
        ValidationError: If RBAC validation fails
        ValueError: If both clients are None or include_decommission used without primary
    """
    if primary_client is None and secondary_client is None:
        raise ValueError("At least one of primary_client or secondary_client must be provided")
    if include_decommission and primary_client is None:
        raise ValueError("include_decommission requires primary_client")

    logger.info("Starting RBAC permission validation...")
    _validate_argocd_mode(argocd_mode)

    # Per-hub validation table. Asymmetries are explicit data:
    # - decommission/old-hub-finalization checks apply to the primary hub only;
    # - the secondary hub honors secondary_argocd_install_type when provided;
    # - only the secondary failure message includes the error count.
    hub_validations = (
        (
            HUB_ROLE_PRIMARY,
            primary_client,
            include_decommission,
            include_old_hub_finalization,
            argocd_install_type,
            False,
        ),
        (
            HUB_ROLE_SECONDARY,
            secondary_client,
            False,
            False,
            secondary_argocd_install_type or argocd_install_type,
            True,
        ),
    )

    for (
        hub_role,
        client,
        hub_include_decommission,
        hub_include_old_hub_finalization,
        hub_argocd_install_type,
        include_error_count,
    ) in hub_validations:
        if hub_role == HUB_ROLE_PRIMARY and client is None:
            logger.info("Primary hub not available; skipping primary RBAC validation")
            continue
        if hub_role == HUB_ROLE_SECONDARY and client is None:
            continue
        _validate_hub(
            hub_role,
            client,
            include_decommission=hub_include_decommission,
            include_old_hub_finalization=hub_include_old_hub_finalization,
            skip_observability=skip_observability,
            argocd_mode=argocd_mode,
            argocd_install_type=hub_argocd_install_type,
            include_error_count=include_error_count,
        )

    logger.info("✓ RBAC permission validation completed successfully")


def validate_decommission_permissions(
    primary_client: KubeClient,
    skip_observability: bool = False,
) -> None:
    """Validate only the RBAC permissions used by standalone decommission."""
    logger.info("Starting decommission RBAC permission validation...")

    validator = RBACValidator(primary_client)
    try:
        all_valid, all_errors = validator.validate_decommission_permissions(
            skip_observability=skip_observability,
        )
    except ValidationError as exc:
        raise ValidationError(f"Decommission RBAC validation could not be completed on primary hub: {exc}") from exc

    if not all_valid:
        report = ["=" * 80]
        report.append("DECOMMISSION RBAC PERMISSION VALIDATION REPORT")
        report.append("=" * 80)
        report.append("")
        report.append("✗ STATUS: PERMISSION VALIDATION FAILED")
        report.append("")
        report.append("The following decommission permissions are missing:")
        report.append("")
        for category, error_list in all_errors.items():
            report.append(f"{category.upper()} PERMISSIONS:")
            for error in error_list:
                report.append(f"  - {error}")
            report.append("")
        report.append("REMEDIATION:")
        report.append("")
        report.append("  1. Apply the opt-in decommission extension under deploy/rbac/extensions/decommission/")
        report.append(
            "  2. Or use Helm with --set rbac.includeDecommissionClusterRole=true for operator teardown access"
        )
        report.append("  3. Or use Kustomize for the baseline and add the decommission manifests separately")
        report.append("")
        report.append("For more information, see docs/deployment/rbac-requirements.md")
        report.append("=" * 80)

        logger.error("\n%s", "\n".join(report))
        raise ValidationError(
            "Decommission RBAC permission validation failed on primary hub. See report above for details."
        )

    logger.info("✓ Decommission RBAC permission validation completed successfully")
