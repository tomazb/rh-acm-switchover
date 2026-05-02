# SPDX-License-Identifier: MIT
"""Shared constants for collection modules and plugins."""

from __future__ import annotations

ACM_NAMESPACE = "open-cluster-management"
BACKUP_NAMESPACE = "open-cluster-management-backup"
OBSERVABILITY_NAMESPACE = "open-cluster-management-observability"
MCE_NAMESPACE = "multicluster-engine"
MANAGED_CLUSTER_AGENT_NAMESPACE = "open-cluster-management-agent"
LOCAL_CLUSTER_NAME = "local-cluster"

PASSIVE_SYNC_RESTORE_NAME = "restore-acm-passive-sync"
ACTIVATION_RESTORE_NAME = "restore-acm-activate"
FULL_RESTORE_NAME = "restore-acm-full"

VELERO_BACKUP_LATEST = "latest"
VELERO_BACKUP_SKIP = "skip"
CLEANUP_BEFORE_RESTORE_VALUE = "CleanupRestored"
WAIT_FAILURE_PHASES = ["FinishedWithErrors", "Error", "Failed", "PartiallyFailed"]

CONFIG_OPENSHIFT_IO = "config.openshift.io"
CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO = "cluster.open-cluster-management.io"
HIVE_OPENSHIFT_IO = "hive.openshift.io"
OPERATOR_OPEN_CLUSTER_MANAGEMENT_IO = "operator.open-cluster-management.io"
OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO = "observability.open-cluster-management.io"
VELERO_IO = "velero.io"
OADP_OPENSHIFT_IO = "oadp.openshift.io"
APPS = "apps"
ROUTE_OPENSHIFT_IO = "route.openshift.io"
ARGOCD_IO = "argoproj.io"
APIEXTENSIONS_K8S_IO = "apiextensions.k8s.io"

ARGOCD_PAUSED_BY_ANNOTATION = "acm-switchover.argoproj.io/paused-by"
ARGOCD_ACM_NAMESPACES = {
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
    MCE_NAMESPACE,
    "open-cluster-management-global-set",
    LOCAL_CLUSTER_NAME,
}
ARGOCD_ACM_NAMESPACE_PATTERN = r"^open-cluster-management($|-.*)"
ARGOCD_ACM_KINDS = {
    "MultiClusterHub",
    "MultiClusterEngine",
    "MultiClusterObservability",
    "ManagedCluster",
    "ManagedClusterSet",
    "ManagedClusterSetBinding",
    "Placement",
    "PlacementBinding",
    "Policy",
    "PolicySet",
    "BackupSchedule",
    "Restore",
    "DataProtectionApplication",
    "ClusterDeployment",
}

RBAC_VALID_ROLES = ("operator", "validator")
RBAC_BASE_ASSETS = [
    "deploy/rbac/namespace.yaml",
    "deploy/rbac/serviceaccount.yaml",
    "deploy/rbac/role.yaml",
    "deploy/rbac/rolebinding.yaml",
    "deploy/rbac/clusterrole.yaml",
    "deploy/rbac/clusterrolebinding.yaml",
]
RBAC_DECOMMISSION_ASSETS = [
    "deploy/rbac/extensions/decommission/clusterrole.yaml",
    "deploy/rbac/extensions/decommission/clusterrolebinding.yaml",
]

# Auto-import strategy constants (ACM 2.14+)
IMPORT_CONTROLLER_CONFIG_CM = "import-controller-config"
AUTO_IMPORT_STRATEGY_KEY = "autoImportStrategy"
AUTO_IMPORT_STRATEGY_DEFAULT = "ImportOnly"
AUTO_IMPORT_STRATEGY_SYNC = "ImportAndSync"
IMMEDIATE_IMPORT_ANNOTATION = "import.open-cluster-management.io/immediate-import"
DISABLE_AUTO_IMPORT_ANNOTATION = "import.open-cluster-management.io/disable-auto-import"

# Observability component names
OBSERVATORIUM_API_DEPLOYMENT = "observability-observatorium-api"
THANOS_COMPACTOR_STATEFULSET = "observability-thanos-compact"
THANOS_COMPACTOR_LABEL_SELECTOR = "app.kubernetes.io/name=thanos-compact"
