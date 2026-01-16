"""Centralized constants for ACM switchover."""

import os

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_INTERRUPT = 130

# Timeouts (in seconds)
RESTORE_WAIT_TIMEOUT = 1800
RESTORE_POLL_INTERVAL = 30

CLUSTER_VERIFY_TIMEOUT = 600
CLUSTER_VERIFY_INTERVAL = 30

OBSERVABILITY_TERMINATE_TIMEOUT = 300
OBSERVABILITY_TERMINATE_INTERVAL = 10

DECOMMISSION_POD_TIMEOUT = 1200
DECOMMISSION_POD_INTERVAL = 30

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
OBSERVABILITY_NAMESPACE = "open-cluster-management-observability"
ACM_NAMESPACE = "open-cluster-management"
# MCE (used for auto-import strategy ConfigMap)
MCE_NAMESPACE = "multicluster-engine"
# Managed cluster agent namespace (on spoke clusters)
MANAGED_CLUSTER_AGENT_NAMESPACE = "open-cluster-management-agent"

# Secrets (these are Kubernetes secret names, not passwords)
THANOS_OBJECT_STORAGE_SECRET = "thanos-object-storage"  # nosec B105

# ACM Resource Names
RESTORE_PASSIVE_SYNC_NAME = "restore-acm-passive-sync"
RESTORE_FULL_NAME = "restore-acm-full"
BACKUP_SCHEDULE_DEFAULT_NAME = "acm-hub-backup"

# Observability Components
THANOS_COMPACTOR_STATEFULSET = "observability-thanos-compact"
THANOS_COMPACTOR_LABEL_SELECTOR = "app=thanos-compact"
OBSERVATORIUM_API_DEPLOYMENT = "observability-observatorium-api"

# ACM Spec Field Names
SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME = "veleroManagedClustersBackupName"
SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS = "syncRestoreWithNewBackups"
SPEC_USE_MANAGED_SERVICE_ACCOUNT = "useManagedServiceAccount"

# ACM Spec Field Values
VELERO_BACKUP_LATEST = "latest"
VELERO_BACKUP_SKIP = "skip"

# Patch verification settings
PATCH_VERIFY_MAX_RETRIES = 5
PATCH_VERIFY_RETRY_DELAY = 1  # seconds between retries

# Auto-import strategy (ACM 2.14+)
IMPORT_CONTROLLER_CONFIGMAP = "import-controller-config"
AUTO_IMPORT_STRATEGY_KEY = "autoImportStrategy"
AUTO_IMPORT_STRATEGY_DEFAULT = "ImportOnly"
AUTO_IMPORT_STRATEGY_SYNC = "ImportAndSync"

# Local cluster name (hub's self-managed cluster, excluded from counts)
LOCAL_CLUSTER_NAME = "local-cluster"

# Stale state detection threshold (15 minutes = half of minimum switchover time)
STALE_STATE_THRESHOLD = 900

# Backup verification settings
BACKUP_VERIFY_TIMEOUT = 600
BACKUP_POLL_INTERVAL = 30

# MultiClusterHub verification settings
MCH_VERIFY_TIMEOUT = 300
MCH_VERIFY_INTERVAL = 10

# BackupSchedule deletion wait (for recreation)
BACKUP_SCHEDULE_DELETE_WAIT = 5

# Thanos scale-down wait
THANOS_SCALE_DOWN_WAIT = 5

# Initial cluster connection wait timeout
INITIAL_CLUSTER_WAIT_TIMEOUT = 120

# Pod readiness tolerance (allow 20% pods not ready)
POD_READINESS_TOLERANCE = 0.8
