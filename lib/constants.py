"""Centralized constants for ACM switchover."""

import logging
import math
import os
import re

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_INTERRUPT = 130
STATUS_UNKNOWN = "unknown"

# Timeouts (in seconds)
RESTORE_WAIT_TIMEOUT = 1800
RESTORE_POLL_INTERVAL = 30
RESTORE_FAST_POLL_INTERVAL = 10
RESTORE_FAST_POLL_TIMEOUT = 120

CLUSTER_VERIFY_TIMEOUT = 600
CLUSTER_VERIFY_INTERVAL = 30

OBSERVABILITY_TERMINATE_TIMEOUT = 300
OBSERVABILITY_TERMINATE_INTERVAL = 10

DECOMMISSION_POD_TIMEOUT = 1200
DECOMMISSION_POD_INTERVAL = 30

# API request timeout for delete operations (prevents hanging API calls)
DELETE_REQUEST_TIMEOUT = 30

# ManagedCluster deletion wait (for finalizers to complete before MCH deletion)
MANAGED_CLUSTER_DELETE_TIMEOUT = 300
MANAGED_CLUSTER_DELETE_INTERVAL = 10

# ACM operator pod prefix (these pods remain after MCH deletion)
ACM_OPERATOR_POD_PREFIX = "multiclusterhub-operator"

# Observability pod readiness timeout
OBSERVABILITY_POD_TIMEOUT = 300

# Velero restore wait timeout
VELERO_RESTORE_TIMEOUT = 300

# Secret visibility wait (for klusterlet bootstrap secret)
SECRET_VISIBILITY_TIMEOUT = 10
SECRET_VISIBILITY_INTERVAL = 1

# Klusterlet post-remediation recheck wait (hub-kubeconfig-secret convergence)
KLUSTERLET_RECHECK_TIMEOUT = 300
KLUSTERLET_RECHECK_INTERVAL = 10

# Parallel cluster verification settings
CLUSTER_VERIFY_MAX_WORKERS = 10

# Maximum kubeconfig file size (10MB default) to prevent memory exhaustion
# Can be overridden via ACM_KUBECONFIG_MAX_SIZE environment variable (bytes)
# Set to 0 or negative to disable size checking
DEFAULT_KUBECONFIG_SIZE = 10 * 1024 * 1024  # 10MB
try:
    _env_size = os.environ.get("ACM_KUBECONFIG_MAX_SIZE")
    if _env_size is not None:
        MAX_KUBECONFIG_SIZE = int(_env_size)
    else:
        MAX_KUBECONFIG_SIZE = DEFAULT_KUBECONFIG_SIZE
except (ValueError, TypeError):
    # Invalid value in environment variable, use default
    MAX_KUBECONFIG_SIZE = DEFAULT_KUBECONFIG_SIZE

# Namespaces
BACKUP_NAMESPACE = "open-cluster-management-backup"
OADP_NAMESPACE = "openshift-adp"
OBSERVABILITY_NAMESPACE = "open-cluster-management-observability"
ACM_NAMESPACE = "open-cluster-management"
# MCE (used for auto-import strategy ConfigMap)
MCE_NAMESPACE = "multicluster-engine"
GLOBAL_SET_NAMESPACE = "open-cluster-management-global-set"
# Managed cluster agent namespace (on spoke clusters)
MANAGED_CLUSTER_AGENT_NAMESPACE = "open-cluster-management-agent"

# Secrets (these are Kubernetes secret names, not passwords)
THANOS_OBJECT_STORAGE_SECRET = "thanos-object-storage"  # nosec B105
HUB_KUBECONFIG_SECRET_NAME = "hub-kubeconfig-secret"  # nosec B105
BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME = "bootstrap-hub-kubeconfig"  # nosec B105

# ACM Resource Names
RESTORE_PASSIVE_SYNC_NAME = "restore-acm-passive-sync"
RESTORE_FULL_NAME = "restore-acm-full"
MANAGED_CLUSTER_RESTORE_NAME = "restore-acm-activate"
BACKUP_SCHEDULE_DEFAULT_NAME = "acm-hub-backup"
BACKUP_STORAGE_LOCATION_RESOURCE = "backupstoragelocations"

# ManagedCluster API identifiers
MANAGED_CLUSTER_API_GROUP = "cluster.open-cluster-management.io"
MANAGED_CLUSTER_API_VERSION = "v1"
MANAGED_CLUSTER_PLURAL = "managedclusters"

# MultiClusterObservability API identifiers
OBSERVABILITY_API_GROUP = "observability.open-cluster-management.io"
MULTICLUSTEROBSERVABILITIES_PLURAL = "multiclusterobservabilities"

# Hive ClusterDeployment API identifiers
HIVE_CLUSTERDEPLOYMENT_API_GROUP = "hive.openshift.io"
HIVE_CLUSTERDEPLOYMENT_API_VERSION = "v1"
HIVE_CLUSTERDEPLOYMENT_PLURAL = "clusterdeployments"

# ManagedCluster expectation state keys and modes
EXPECTED_MANAGED_CLUSTER_NAMES_KEY = "expected_managed_cluster_names"
EXPECTED_MANAGED_CLUSTER_COUNT_KEY = "expected_managed_cluster_count"
MANAGED_CLUSTER_EXPECTATION_KEY = "managed_cluster_expectation_mode"
MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY = "restore_only"
MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT = "derived_from_preflight"
MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED = "explicit_empty_allowed"
MANAGED_CLUSTER_EXPECTATION_EXPLICIT_MINIMUM = "explicit_minimum"
PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME = "pre_activation_velero_managed_clusters_restore_name"

# Observability Components
THANOS_COMPACTOR_STATEFULSET = "observability-thanos-compact"
THANOS_COMPACTOR_LABEL_SELECTOR = "app.kubernetes.io/name=thanos-compact"
OBSERVATORIUM_API_DEPLOYMENT = "observability-observatorium-api"

# ACM Spec Field Names
SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME = "veleroManagedClustersBackupName"
SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS = "syncRestoreWithNewBackups"
SPEC_USE_MANAGED_SERVICE_ACCOUNT = "useManagedServiceAccount"

# ACM Spec Field Values
VELERO_BACKUP_LATEST = "latest"
VELERO_BACKUP_SKIP = "skip"
CLEANUP_BEFORE_RESTORE_VALUE = "CleanupRestored"

# Restore status message pattern for clusters that are already available
# (expected when running consecutive switchovers)
BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN = r"^ManagedCluster [^ ]+ already available$"

# Patch verification settings
PATCH_VERIFY_MAX_RETRIES = 5
PATCH_VERIFY_RETRY_DELAY = 1  # seconds between retries

# Auto-import strategy (ACM 2.14+)
IMPORT_CONTROLLER_CONFIG_CM = "import-controller-config"
# Backward-compatible alias (keep existing constant name)
IMPORT_CONTROLLER_CONFIGMAP = IMPORT_CONTROLLER_CONFIG_CM
AUTO_IMPORT_STRATEGY_KEY = "autoImportStrategy"
AUTO_IMPORT_STRATEGY_DEFAULT = "ImportOnly"
AUTO_IMPORT_STRATEGY_SYNC = "ImportAndSync"

# ManagedCluster annotations
DISABLE_AUTO_IMPORT_ANNOTATION = "import.open-cluster-management.io/disable-auto-import"
IMMEDIATE_IMPORT_ANNOTATION = "import.open-cluster-management.io/immediate-import"

# Local cluster name (hub's self-managed cluster, excluded from counts)
LOCAL_CLUSTER_NAME = "local-cluster"

# CLI validation choices
VALIDATION_METHOD_CHOICES = ("passive", "full")
VALIDATION_OLD_HUB_ACTION_CHOICES = ("secondary", "decommission", "none")
VALIDATION_ACTIVATION_METHOD_CHOICES = ("patch", "restore")
VALIDATION_LOG_FORMAT_CHOICES = ("text", "json")
VALIDATION_SETUP_ROLE_CHOICES = ("operator", "validator", "both")

# Argo CD validation messages
ARGOCD_RESUME_ON_FAILURE_REQUIRES_MANAGE_MESSAGE = "--argocd-resume-on-failure requires --argocd-manage"
ARGOCD_RESUME_ON_FAILURE_CONFLICTS_RESUME_ONLY_MESSAGE = (
    "--argocd-resume-on-failure cannot be used with --argocd-resume-only"
)
ARGOCD_RESUME_ON_FAILURE_CONFLICTS_VALIDATE_ONLY_MESSAGE = (
    "--argocd-resume-on-failure cannot be used with --validate-only"
)

# Stale state detection threshold (default: 6 hours)
# Can be overridden via ACM_SWITCHOVER_STALE_HOURS environment variable
DEFAULT_STALE_STATE_THRESHOLD_HOURS = 6
_MAX_STALE_HOURS = 8760  # 1 year - reasonable upper bound
try:
    _env_stale_hours = os.environ.get("ACM_SWITCHOVER_STALE_HOURS")
    if _env_stale_hours is not None:
        _hours_value = float(_env_stale_hours)
        if not math.isfinite(_hours_value):
            raise ValueError("ACM_SWITCHOVER_STALE_HOURS must be finite")
        if _hours_value <= 0:
            raise ValueError("ACM_SWITCHOVER_STALE_HOURS must be positive")
        if _hours_value > _MAX_STALE_HOURS:
            raise ValueError("ACM_SWITCHOVER_STALE_HOURS exceeds maximum")
        STALE_STATE_THRESHOLD = int(_hours_value * 3600)
    else:
        STALE_STATE_THRESHOLD = DEFAULT_STALE_STATE_THRESHOLD_HOURS * 3600
except (ValueError, TypeError, OverflowError) as e:
    logging.getLogger(__name__).warning(
        "Warning: Invalid value for ACM_SWITCHOVER_STALE_HOURS: %s. Using default %s hours.",
        e,
        DEFAULT_STALE_STATE_THRESHOLD_HOURS,
    )
    STALE_STATE_THRESHOLD = DEFAULT_STALE_STATE_THRESHOLD_HOURS * 3600

# Backup verification settings
BACKUP_VERIFY_TIMEOUT = 600
BACKUP_POLL_INTERVAL = 30
BACKUP_INTEGRITY_MAX_AGE_SECONDS = 600
ACM_BACKUP_SCHEDULE_TYPE_LABEL = "cluster.open-cluster-management.io/backup-schedule-type"
ACM_BACKUP_SCHEDULE_TYPES = frozenset({"managedClusters", "credentials", "resources"})
ACM_BACKUP_NAME_RE = re.compile(r"^acm-(managed-clusters|credentials|resources)-")

# MultiClusterHub verification settings
MCH_VERIFY_TIMEOUT = 600
MCH_VERIFY_INTERVAL = 10

# BackupSchedule deletion wait (for recreation)
BACKUP_SCHEDULE_DELETE_TIMEOUT = 30
BACKUP_SCHEDULE_DELETE_INTERVAL = 2

# Thanos scale-down wait
THANOS_SCALE_DOWN_WAIT = 5

# Initial cluster connection wait timeout
INITIAL_CLUSTER_WAIT_TIMEOUT = 120

# Pod readiness tolerance (allow 20% pods not ready)
POD_READINESS_TOLERANCE = 0.8

# Python CLI report artifact schema
REPORT_SCHEMA_VERSION = "1.0"
REPORT_SOURCE_PYTHON_CLI = "python-cli"
REPORT_PHASE_PREFLIGHT = "preflight"
# Report status value, not a password.
REPORT_STATUS_PASS = "pass"  # nosec B105
REPORT_STATUS_FAIL = "fail"
REPORT_SEVERITY_CRITICAL = "critical"
REPORT_SEVERITY_WARNING = "warning"
REPORT_DEFAULT_CHECK = "validation"
REPORT_ID_PREFIX_PREFLIGHT = "preflight-"
