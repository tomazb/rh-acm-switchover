"""RBAC self-validation module for collection preflight."""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.result import ValidationResult

VALID_ROLES = ("operator", "validator")
VALID_ARGOCD_MODES = ("none", "check", "manage")

# Cluster-scoped permissions for operator role
OPERATOR_CLUSTER_PERMISSIONS = [
    ("", "namespaces", ["get"]),
    ("", "nodes", ["get", "list"]),
    ("config.openshift.io", "clusteroperators", ["get", "list"]),
    ("config.openshift.io", "clusterversions", ["get", "list"]),
    ("cluster.open-cluster-management.io", "managedclusters", ["get", "list", "patch"]),
    ("hive.openshift.io", "clusterdeployments", ["get", "list"]),
    ("operator.open-cluster-management.io", "multiclusterhubs", ["get", "list"]),
    ("observability.open-cluster-management.io", "multiclusterobservabilities", ["get", "list"]),
]

# Cluster-scoped permissions for validator role (read-only)
VALIDATOR_CLUSTER_PERMISSIONS = [
    ("", "namespaces", ["get"]),
    ("", "nodes", ["get", "list"]),
    ("config.openshift.io", "clusteroperators", ["get", "list"]),
    ("config.openshift.io", "clusterversions", ["get", "list"]),
    ("cluster.open-cluster-management.io", "managedclusters", ["get", "list"]),
    ("hive.openshift.io", "clusterdeployments", ["get", "list"]),
    ("operator.open-cluster-management.io", "multiclusterhubs", ["get", "list"]),
    ("observability.open-cluster-management.io", "multiclusterobservabilities", ["get", "list"]),
]

# Hub namespace-scoped permissions for operator role
OPERATOR_HUB_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    "open-cluster-management-backup": [
        ("", "configmaps", ["get", "list", "create", "patch", "delete"]),
        ("", "secrets", ["get"]),
        ("", "pods", ["get", "list"]),
        ("cluster.open-cluster-management.io", "backupschedules", ["get", "list", "create", "patch", "delete"]),
        ("cluster.open-cluster-management.io", "restores", ["get", "list", "create", "patch", "delete"]),
        ("velero.io", "backups", ["get", "list"]),
        ("velero.io", "restores", ["get", "list"]),
        ("velero.io", "backupstoragelocations", ["get", "list"]),
        ("oadp.openshift.io", "dataprotectionapplications", ["get", "list"]),
    ],
    "open-cluster-management": [
        ("", "pods", ["get", "list"]),
    ],
    "open-cluster-management-observability": [
        ("", "pods", ["get", "list"]),
        ("", "secrets", ["get"]),
        ("apps", "deployments", ["get", "patch"]),
        ("apps", "statefulsets", ["get", "patch"]),
        ("apps", "statefulsets/scale", ["get", "patch"]),
        ("route.openshift.io", "routes", ["get"]),
    ],
    "multicluster-engine": [
        ("", "configmaps", ["get", "list", "create", "patch", "delete"]),
    ],
}

# Hub namespace-scoped permissions for validator role (read-only)
VALIDATOR_HUB_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    "open-cluster-management-backup": [
        ("", "configmaps", ["get", "list"]),
        ("", "secrets", ["get"]),
        ("", "pods", ["get", "list"]),
        ("cluster.open-cluster-management.io", "backupschedules", ["get", "list"]),
        ("cluster.open-cluster-management.io", "restores", ["get", "list"]),
        ("velero.io", "backups", ["get", "list"]),
        ("velero.io", "restores", ["get", "list"]),
        ("velero.io", "backupstoragelocations", ["get", "list"]),
        ("oadp.openshift.io", "dataprotectionapplications", ["get", "list"]),
    ],
    "open-cluster-management": [
        ("", "pods", ["get", "list"]),
    ],
    "open-cluster-management-observability": [
        ("", "pods", ["get", "list"]),
        ("", "secrets", ["get"]),
        ("apps", "deployments", ["get", "list"]),
        ("apps", "statefulsets", ["get", "list"]),
        ("route.openshift.io", "routes", ["get"]),
    ],
    "multicluster-engine": [
        ("", "configmaps", ["get", "list"]),
    ],
}

# Managed-cluster namespace permissions for operator role
OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    "open-cluster-management-agent": [
        ("", "secrets", ["get", "create", "delete"]),
        ("apps", "deployments", ["get", "patch"]),
    ],
}

# Managed-cluster namespace permissions for validator role (read-only)
VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    "open-cluster-management-agent": [
        ("", "secrets", ["get"]),
        ("apps", "deployments", ["get"]),
    ],
}

DECOMMISSION_PERMISSIONS = [
    ("cluster.open-cluster-management.io", "managedclusters", ["delete"]),
    ("operator.open-cluster-management.io", "multiclusterhubs", ["delete"]),
    ("observability.open-cluster-management.io", "multiclusterobservabilities", ["delete"]),
]

# F9: Argo CD permissions split into base and operator-install-only
ARGOCD_BASE_CLUSTER_PERMISSIONS = [
    ("argoproj.io", "applications", ["get", "list"]),
    ("apiextensions.k8s.io", "customresourcedefinitions", ["get"]),
]

ARGOCD_OPERATOR_CLUSTER_PERMISSIONS = [
    ("argoproj.io", "argocds", ["get", "list"]),
]

ARGOCD_MANAGE_EXTRA_CLUSTER_PERMISSIONS = [
    ("argoproj.io", "applications", ["patch"]),
]


def _expand_permission_list(
    entries: list[tuple[str, str, list[str]]],
    namespace: str | None = None,
) -> list[tuple[str, str, str, str | None]]:
    """Expand (api_group, resource, [verbs]) entries into flat (group, resource, verb, ns) tuples."""
    result = []
    for api_group, resource, verbs in entries:
        for verb in verbs:
            result.append((api_group, resource, verb, namespace))
    return result


def expand_rbac_requirements(
    role: str,
    include_decommission: bool,
    skip_observability: bool,
    argocd_mode: str,
    argocd_install_type: str,
) -> list[tuple[str, str, str, str | None]]:
    """Return the full flat list of (api_group, resource, verb, namespace) tuples for a given configuration.

    This mirrors the permission matrix from lib/rbac_validator.py and is used by
    the role's RBAC validation tasks to enumerate what to check via SelfSubjectAccessReview.
    """
    cluster_perms = OPERATOR_CLUSTER_PERMISSIONS if role == "operator" else VALIDATOR_CLUSTER_PERMISSIONS
    hub_ns_perms = OPERATOR_HUB_NAMESPACE_PERMISSIONS if role == "operator" else VALIDATOR_HUB_NAMESPACE_PERMISSIONS
    managed_ns_perms = (
        OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS
        if role == "operator"
        else VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS
    )

    permissions: list[tuple[str, str, str, str | None]] = []

    # Cluster-scoped
    filtered_cluster = [
        (g, r, v)
        for g, r, v in cluster_perms
        if not (skip_observability and g == "observability.open-cluster-management.io")
    ]
    permissions.extend(_expand_permission_list(filtered_cluster))

    # Hub namespace-scoped
    for namespace, ns_perms in hub_ns_perms.items():
        if skip_observability and namespace == "open-cluster-management-observability":
            continue
        permissions.extend(_expand_permission_list(ns_perms, namespace=namespace))

    # Managed-cluster namespace-scoped
    for namespace, ns_perms in managed_ns_perms.items():
        permissions.extend(_expand_permission_list(ns_perms, namespace=namespace))

    # Argo CD
    if argocd_mode in {"check", "manage"}:
        permissions.extend(_expand_permission_list(ARGOCD_BASE_CLUSTER_PERMISSIONS))
        if argocd_install_type != "vanilla":
            permissions.extend(_expand_permission_list(ARGOCD_OPERATOR_CLUSTER_PERMISSIONS))
        if argocd_mode == "manage" and role == "operator":
            permissions.extend(_expand_permission_list(ARGOCD_MANAGE_EXTRA_CLUSTER_PERMISSIONS))

    # Decommission extras
    if include_decommission:
        permissions.extend(_expand_permission_list(DECOMMISSION_PERMISSIONS))

    return permissions


def summarize_rbac_results(hub: str, denied_permissions: list[dict]) -> dict:
    if denied_permissions:
        result = ValidationResult(
            id=f"preflight-rbac-{hub}",
            severity="critical",
            status="fail",
            message=f"missing required RBAC permissions on {hub} hub",
            details={"denied_permissions": denied_permissions},
            recommended_action="Grant the documented collection RBAC role before running preflight again",
        ).to_dict()
        return {"passed": False, "results": [result]}

    result = ValidationResult(
        id=f"preflight-rbac-{hub}",
        severity="info",
        status="pass",
        message=f"all required RBAC permissions validated on {hub} hub",
    ).to_dict()
    return {"passed": True, "results": [result]}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "hub": {"type": "str", "required": True},
            "role": {"type": "str", "default": "operator"},
            "include_decommission": {"type": "bool", "default": False},
            "skip_observability": {"type": "bool", "default": False},
            "argocd_mode": {"type": "str", "default": "none"},
            "argocd_install_type": {"type": "str", "default": "unknown"},
            "denied_permissions": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )

    permissions = expand_rbac_requirements(
        role=module.params["role"],
        include_decommission=module.params["include_decommission"],
        skip_observability=module.params["skip_observability"],
        argocd_mode=module.params["argocd_mode"],
        argocd_install_type=module.params["argocd_install_type"],
    )
    summary = summarize_rbac_results(module.params["hub"], module.params["denied_permissions"])
    module.exit_json(changed=False, permissions=permissions, **summary)


if __name__ == "__main__":
    main()
