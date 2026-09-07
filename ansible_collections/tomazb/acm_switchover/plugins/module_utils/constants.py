# SPDX-License-Identifier: MIT
"""Shared constants for collection modules and plugins."""

from __future__ import annotations

ACM_NAMESPACE = "open-cluster-management"
BACKUP_NAMESPACE = "open-cluster-management-backup"
OBSERVABILITY_NAMESPACE = "open-cluster-management-observability"
MCE_NAMESPACE = "multicluster-engine"
MANAGED_CLUSTER_AGENT_NAMESPACE = "open-cluster-management-agent"
LOCAL_CLUSTER_NAME = "local-cluster"
SECRET_VISIBILITY_TIMEOUT = 10
SECRET_VISIBILITY_INTERVAL = 1
KLUSTERLET_RECHECK_TIMEOUT = 300
KLUSTERLET_RECHECK_INTERVAL = 10
KLUSTERLET_DEFAULT_WORKERS = 10
KLUSTERLET_REQUEST_TIMEOUT = 30
KLUSTERLET_WORKER_TIMEOUT = 180
WORKER_TIMEOUT_REASON_TEMPLATE = "worker_timeout_after_{timeout}s"
HUB_KUBECONFIG_SECRET_NAME = "hub-kubeconfig-secret"  # nosec B105
BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME = "bootstrap-hub-kubeconfig"  # nosec B105

VALIDATION_METHOD_CHOICES = ("passive", "full")
VALIDATION_OLD_HUB_ACTION_CHOICES = ("secondary", "decommission", "none")
VALIDATION_ACTIVATION_METHOD_CHOICES = ("patch", "restore")
VALIDATION_EXECUTION_MODE_CHOICES = ("execute", "validate", "dry_run")
ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE = "argocd.resume_on_failure requires argocd.manage=true"
ARGOCD_RESUME_ON_FAILURE_VALIDATE_MODE_MESSAGE = "argocd.resume_on_failure is not valid with execution.mode=validate"

PASSIVE_SYNC_RESTORE_NAME = "restore-acm-passive-sync"
ACTIVATION_RESTORE_NAME = "restore-acm-activate"
FULL_RESTORE_NAME = "restore-acm-full"
BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN = r"^ManagedCluster [^ ]+ already available$"
PASSIVE_RESTORE_CONVENTIONAL_NAME_FALLBACK_REASON = "conventional_name_fallback"
NO_MANAGED_CLUSTERS_PENDING_REASON = "no-managed-clusters"

VELERO_BACKUP_LATEST = "latest"
VELERO_BACKUP_SKIP = "skip"
CLEANUP_BEFORE_RESTORE_VALUE = "CleanupRestored"
WAIT_FAILURE_PHASES = ["FinishedWithErrors", "Error", "Failed", "PartiallyFailed"]

CONFIG_OPENSHIFT_IO = "config.openshift.io"
CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO = "cluster.open-cluster-management.io"
HIVE_OPENSHIFT_IO = "hive.openshift.io"
HIVE_CLUSTERDEPLOYMENT_RESOURCE = "clusterdeployments"
OPERATOR_OPEN_CLUSTER_MANAGEMENT_IO = "operator.open-cluster-management.io"
OBSERVABILITY_OPEN_CLUSTER_MANAGEMENT_IO = "observability.open-cluster-management.io"
VELERO_IO = "velero.io"
OADP_OPENSHIFT_IO = "oadp.openshift.io"
APPS = "apps"
ROUTE_OPENSHIFT_IO = "route.openshift.io"
ARGOCD_IO = "argoproj.io"
APIEXTENSIONS_K8S_IO = "apiextensions.k8s.io"

ARGOCD_PAUSED_BY_ANNOTATION = "acm-switchover.argoproj.io/paused-by"
ARGOCD_ORIGINAL_SYNC_POLICY_ANNOTATION = "acm-switchover.argoproj.io/original-sync-policy"
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

RBAC_VALID_ROLES = ("operator", "validator", "both")
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
OBSERVABILITY_POD_LABEL_SELECTOR = "observability.open-cluster-management.io/name=observability"

# Ownership marker written atomically with the ImportAndSync ConfigMap patch
# (issue #214, audit C3). The cluster is the collection's register: finalization
# discharges the reset obligation when this marker is observed, regardless of
# checkpoint state.
AUTO_IMPORT_MARKER_ANNOTATION = "acm-switchover.open-cluster-management.io/import-strategy-set-by"
AUTO_IMPORT_MARKER_VALUE = "acm-switchover"

# R4-03 strict-read reason codes
STRICT_READ_REASON_KIND_NOT_SERVED = "kind_not_served"
STRICT_READ_REASON_NAMESPACE_NOT_FOUND = "namespace_not_found"
STRICT_READ_REASON_OBJECT_NOT_FOUND = "object_not_found"
STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE = "discovery_unverifiable"
STRICT_READ_REASON_INVENTORY_INCOMPLETE = "inventory_incomplete"
STRICT_READ_REASON_MALFORMED_RESPONSE = "malformed_response"
STRICT_READ_REASON_READ_FAILED = "read_failed"

# R4-03 strict-read bounds
STRICT_READ_PAGE_LIMIT = 500
STRICT_READ_MAX_PAGES = 100
STRICT_READ_MAX_RESTARTS = 1
# Collection-only: the collection module has no client instance carrying a timeout.
# Value mirrors KubeClient's per-instance request_timeout default (lib/kube_client.py:210).
STRICT_READ_REQUEST_TIMEOUT = 30

# R4-03 decommission teardown records (plan §10.2). These mirror lib/teardown_record.py
# and lib/constants.py; the collection shares no runtime code with the Python CLI, so
# tests/test_constants_parity.py holds every one of them equal to its Python owner.
# The MCH operator identity is discovered through the OLM CSV that owns the
# MultiClusterHub CRD, then through that CSV's install-strategy Deployment.
OPERATOR_IDENTITY_DISCOVERY_METHOD = "olm_csv_owned_mch_crd_install_deployment_v1"
OPERATOR_IDENTITY_UNAVAILABLE_REASONS = (
    "csv_absent",
    "csv_ambiguous",
    "csv_not_succeeded",
    "csv_owned_crd_mismatch",
    "install_deployment_absent",
    "install_deployment_ambiguous",
    "deployment_read_failed",
    "deployment_identity_incomplete",
)

# How one decommission substep ended in this invocation. Mirrors the FIVE values
# of lib/decommission_outcome.SubstepOutcome; the root parity test
# tests/test_constants_parity.py compares the enum values against this tuple.
DECOMMISSION_SUBSTEP_OUTCOMES = (
    "not_requested",
    "precondition_noop",
    "completed",
    "refused",
    "failed",
)

# The teardown lifecycle of one deleted object (lib/teardown_record.TeardownPhase).
TEARDOWN_PHASES = frozenset(
    {
        "delete_started",
        "cr_absent",
        "drain_pending",
        "drained",
        "completed",
        "recovery_required",
    }
)

# Kinds whose teardown has a drain scope, and kinds that carry operator identity.
DRAIN_SCOPED_KINDS = frozenset({"MultiClusterObservability", "MultiClusterHub"})
IDENTITY_BEARING_KINDS = frozenset({"MultiClusterHub"})

# Section 10.2.1c closed vocabularies.
RESOURCE_VERSION_LABELS = frozenset({"drain_namespace", "drain_pods", "operator_deployment"})
ABSENCE_PROOF_KEYS = frozenset({"target_cr", "drain_namespace"})
ABSENCE_PROOF_TYPES = frozenset({"object_absent", "crd_absent", "namespace_absent"})
ABSENCE_PROOF_TYPES_BY_KEY = {
    "target_cr": frozenset({"object_absent", "crd_absent"}),
    "drain_namespace": frozenset({"namespace_absent"}),
}

# The fixed drain namespace each drain-scoped family tears down. A
# `drain_namespace` absence proof must name exactly this namespace.
DRAIN_NAMESPACE_BY_KIND = {
    "MultiClusterObservability": OBSERVABILITY_NAMESPACE,
    "MultiClusterHub": ACM_NAMESPACE,
}

# The CRD whose owning CSV identifies the MultiClusterHub operator Deployment.
MCH_OWNED_CRD = "multiclusterhubs.operator.open-cluster-management.io"

NAMESPACE_API_VERSION = "v1"
NAMESPACE_KIND = "Namespace"
