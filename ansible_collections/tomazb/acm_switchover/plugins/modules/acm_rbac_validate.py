# SPDX-License-Identifier: MIT

from __future__ import annotations

DOCUMENTATION = r"""
---
module: acm_rbac_validate
short_description: Validate RBAC permissions for switchover roles
description:
  - Expands the required RBAC permission matrix for a given role configuration and
    summarizes denied permissions into a structured validation result. Does not make
    Kubernetes API calls; callers supply the C(denied_permissions) list from
    SelfSubjectAccessReview results.
author:
  - ACM Switchover Contributors (@tomazb)
options:
  hub:
    description: Hub identifier label used in result IDs (e.g. C(primary)).
    required: true
    type: str
  role:
    description: Role profile to expand. C(operator) includes write permissions; C(validator) is read-only.
    type: str
    default: operator
  include_decommission:
    description: Whether to include delete permissions required for hub decommission.
    type: bool
    default: false
  decommission_only:
    description:
      - Whether to expand only the standalone decommission permission surface.
      - Intended for the dedicated decommission role, not switchover preflight.
    type: bool
    default: false
  skip_observability:
    description: Whether to omit observability-related permissions from the matrix.
    type: bool
    default: false
  argocd_mode:
    description: Argo CD integration mode affecting which Argo CD permissions are required.
    type: str
    default: none
  argocd_install_type:
    description: Argo CD installation type; affects operator-specific permissions.
    type: str
    default: unknown
  denied_permissions:
    description: List of permission dicts that were denied by SelfSubjectAccessReview.
    type: list
    elements: dict
    default: []
"""

EXAMPLES = r"""
- name: Validate operator RBAC on primary hub
  tomazb.acm_switchover.acm_rbac_validate:
    hub: primary
    role: operator
    denied_permissions: "{{ sar_denied_results }}"
  register: rbac_result
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ACM_NAMESPACE,
    APIEXTENSIONS_K8S_IO,
    APPS,
    ARGOCD_IO,
    BACKUP_NAMESPACE,
    CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO,
    CONFIG_OPENSHIFT_IO,
    HIVE_OPENSHIFT_IO,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MCE_NAMESPACE,
    OADP_OPENSHIFT_IO,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO,
    OPERATOR_OPEN_CLUSTER_MANAGEMENT_IO,
    ROUTE_OPENSHIFT_IO,
    VELERO_IO,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.result import ValidationResult

VALID_ROLES = ("operator", "validator")
VALID_ARGOCD_MODES = ("none", "check", "manage")

# Cluster-scoped permissions for operator role
OPERATOR_CLUSTER_PERMISSIONS = [
    ("", "namespaces", ["get", "list"]),
    ("", "nodes", ["get", "list"]),
    (CONFIG_OPENSHIFT_IO, "clusteroperators", ["get", "list"]),
    (CONFIG_OPENSHIFT_IO, "clusterversions", ["get", "list"]),
    (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "managedclusters", ["get", "list", "patch"]),
    (HIVE_OPENSHIFT_IO, "clusterdeployments", ["get", "list"]),
    (OPERATOR_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterhubs", ["get", "list"]),
    (OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterobservabilities", ["get", "list"]),
]

# Cluster-scoped permissions for validator role (read-only)
VALIDATOR_CLUSTER_PERMISSIONS = [
    ("", "namespaces", ["get", "list"]),
    ("", "nodes", ["get", "list"]),
    (CONFIG_OPENSHIFT_IO, "clusteroperators", ["get", "list"]),
    (CONFIG_OPENSHIFT_IO, "clusterversions", ["get", "list"]),
    (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "managedclusters", ["get", "list"]),
    (HIVE_OPENSHIFT_IO, "clusterdeployments", ["get", "list"]),
    (OPERATOR_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterhubs", ["get", "list"]),
    (OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterobservabilities", ["get", "list"]),
]

# Hub namespace-scoped permissions for operator role
OPERATOR_HUB_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    BACKUP_NAMESPACE: [
        ("", "configmaps", ["get", "list", "create", "patch", "delete"]),
        ("", "secrets", ["get"]),
        ("", "pods", ["get", "list"]),
        (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "backupschedules", ["get", "list", "create", "patch", "delete"]),
        (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "restores", ["get", "list", "create", "patch", "delete"]),
        (VELERO_IO, "backups", ["get", "list"]),
        (VELERO_IO, "restores", ["get", "list"]),
        (VELERO_IO, "backupstoragelocations", ["get", "list"]),
        (OADP_OPENSHIFT_IO, "dataprotectionapplications", ["get", "list"]),
    ],
    ACM_NAMESPACE: [
        ("", "pods", ["get", "list"]),
    ],
    OBSERVABILITY_NAMESPACE: [
        ("", "pods", ["get", "list"]),
        ("", "secrets", ["get"]),
        (APPS, "deployments", ["get", "patch"]),
        (APPS, "statefulsets", ["get", "patch"]),
        (APPS, "statefulsets/scale", ["get", "patch"]),
        (ROUTE_OPENSHIFT_IO, "routes", ["get"]),
    ],
    MCE_NAMESPACE: [
        ("", "configmaps", ["get", "list", "create", "patch", "delete"]),
    ],
}

# Hub namespace-scoped permissions for validator role (read-only)
VALIDATOR_HUB_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    BACKUP_NAMESPACE: [
        ("", "configmaps", ["get", "list"]),
        ("", "secrets", ["get"]),
        ("", "pods", ["get", "list"]),
        (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "backupschedules", ["get", "list"]),
        (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "restores", ["get", "list"]),
        (VELERO_IO, "backups", ["get", "list"]),
        (VELERO_IO, "restores", ["get", "list"]),
        (VELERO_IO, "backupstoragelocations", ["get", "list"]),
        (OADP_OPENSHIFT_IO, "dataprotectionapplications", ["get", "list"]),
    ],
    ACM_NAMESPACE: [
        ("", "pods", ["get", "list"]),
    ],
    OBSERVABILITY_NAMESPACE: [
        ("", "pods", ["get", "list"]),
        ("", "secrets", ["get"]),
        (APPS, "deployments", ["get", "list"]),
        (APPS, "statefulsets", ["get", "list"]),
        (ROUTE_OPENSHIFT_IO, "routes", ["get"]),
    ],
    MCE_NAMESPACE: [
        ("", "configmaps", ["get", "list"]),
    ],
}

# Managed-cluster namespace permissions for operator role
OPERATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    MANAGED_CLUSTER_AGENT_NAMESPACE: [
        ("", "secrets", ["get", "create", "delete"]),
        (APPS, "deployments", ["get", "patch"]),
    ],
}

# Managed-cluster namespace permissions for validator role (read-only)
VALIDATOR_MANAGED_CLUSTER_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    MANAGED_CLUSTER_AGENT_NAMESPACE: [
        ("", "secrets", ["get"]),
        (APPS, "deployments", ["get"]),
    ],
}

DECOMMISSION_PERMISSIONS = [
    (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "managedclusters", ["delete"]),
    (OPERATOR_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterhubs", ["delete"]),
    (OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterobservabilities", ["delete"]),
]

DECOMMISSION_CLUSTER_PERMISSIONS = [
    ("", "namespaces", ["get"]),
    (CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO, "managedclusters", ["list", "delete"]),
    (OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterobservabilities", ["list", "delete"]),
]

DECOMMISSION_NAMESPACE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    ACM_NAMESPACE: [
        ("", "pods", ["get", "list"]),
        (OPERATOR_OPEN_CLUSTER_MANAGEMENT_IO, "multiclusterhubs", ["list", "delete"]),
    ],
    OBSERVABILITY_NAMESPACE: [
        ("", "pods", ["get", "list"]),
    ],
}

# F9: Argo CD permissions split into base and operator-install-only
ARGOCD_BASE_CLUSTER_PERMISSIONS = [
    (ARGOCD_IO, "applications", ["get", "list"]),
    (APIEXTENSIONS_K8S_IO, "customresourcedefinitions", ["get"]),
]

ARGOCD_OPERATOR_CLUSTER_PERMISSIONS = [
    (ARGOCD_IO, "argocds", ["get", "list"]),
]

ARGOCD_MANAGE_EXTRA_CLUSTER_PERMISSIONS = [
    (ARGOCD_IO, "applications", ["patch"]),
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
    decommission_only: bool = False,
) -> list[tuple[str, str, str, str | None]]:
    """Return the full flat list of (api_group, resource, verb, namespace) tuples for a given configuration.

    This mirrors the permission matrix from lib/rbac_validator.py and is used by
    the role's RBAC validation tasks to enumerate what to check via SelfSubjectAccessReview.
    """
    if decommission_only:
        if role != "operator":
            raise ValueError("decommission_only is only valid for the operator role")

        permissions: list[tuple[str, str, str, str | None]] = []
        filtered_cluster = [
            (g, r, v)
            for g, r, v in DECOMMISSION_CLUSTER_PERMISSIONS
            if not (skip_observability and g == OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO)
        ]
        permissions.extend(_expand_permission_list(filtered_cluster))

        for namespace, ns_perms in DECOMMISSION_NAMESPACE_PERMISSIONS.items():
            if skip_observability and namespace == OBSERVABILITY_NAMESPACE:
                continue
            permissions.extend(_expand_permission_list(ns_perms, namespace=namespace))

        return permissions

    if include_decommission and role != "operator":
        raise ValueError("include_decommission is only valid for the operator role")

    if role == "validator" and argocd_mode == "manage":
        raise ValueError("validator role cannot use argocd_mode=manage")

    cluster_perms = OPERATOR_CLUSTER_PERMISSIONS if role == "operator" else VALIDATOR_CLUSTER_PERMISSIONS
    hub_ns_perms = OPERATOR_HUB_NAMESPACE_PERMISSIONS if role == "operator" else VALIDATOR_HUB_NAMESPACE_PERMISSIONS

    permissions: list[tuple[str, str, str, str | None]] = []

    # Cluster-scoped
    filtered_cluster = [
        (g, r, v)
        for g, r, v in cluster_perms
        if not (skip_observability and g == OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO)
    ]
    permissions.extend(_expand_permission_list(filtered_cluster))

    # Hub namespace-scoped
    for namespace, ns_perms in hub_ns_perms.items():
        if skip_observability and namespace == OBSERVABILITY_NAMESPACE:
            continue
        permissions.extend(_expand_permission_list(ns_perms, namespace=namespace))

    # Argo CD
    if argocd_mode in {"check", "manage"}:
        if argocd_install_type != "none":
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
        return {"passed": False, "critical_failures": 1, "results": [result]}

    result = ValidationResult(
        id=f"preflight-rbac-{hub}",
        severity="info",
        status="pass",
        message=f"all required RBAC permissions validated on {hub} hub",
    ).to_dict()
    return {"passed": True, "critical_failures": 0, "results": [result]}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "hub": {"type": "str", "required": True},
            "role": {"type": "str", "default": "operator", "choices": list(VALID_ROLES)},
            "include_decommission": {"type": "bool", "default": False},
            "decommission_only": {"type": "bool", "default": False},
            "skip_observability": {"type": "bool", "default": False},
            "argocd_mode": {"type": "str", "default": "none", "choices": list(VALID_ARGOCD_MODES)},
            "argocd_install_type": {"type": "str", "default": "unknown"},
            "denied_permissions": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )

    try:
        permissions = expand_rbac_requirements(
            role=module.params["role"],
            include_decommission=module.params["include_decommission"],
            skip_observability=module.params["skip_observability"],
            argocd_mode=module.params["argocd_mode"],
            argocd_install_type=module.params["argocd_install_type"],
            decommission_only=module.params["decommission_only"],
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))
        return

    summary = summarize_rbac_results(module.params["hub"], module.params["denied_permissions"])
    module.exit_json(changed=False, permissions=permissions, **summary)


if __name__ == "__main__":
    main()
