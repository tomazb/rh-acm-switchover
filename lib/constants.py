"""Centralized constants for ACM switchover."""

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

# Observability pod readiness timeout
OBSERVABILITY_POD_TIMEOUT = 300

# Velero restore wait timeout
VELERO_RESTORE_TIMEOUT = 300

# Secret visibility wait (for klusterlet bootstrap secret)
SECRET_VISIBILITY_TIMEOUT = 10
SECRET_VISIBILITY_INTERVAL = 1

# Parallel cluster verification settings
CLUSTER_VERIFY_MAX_WORKERS = 10

# Namespaces
BACKUP_NAMESPACE = "open-cluster-management-backup"
OBSERVABILITY_NAMESPACE = "open-cluster-management-observability"
ACM_NAMESPACE = "open-cluster-management"
# MCE (used for auto-import strategy ConfigMap)
MCE_NAMESPACE = "multicluster-engine"

# Secrets (these are Kubernetes secret names, not passwords)
THANOS_OBJECT_STORAGE_SECRET = "thanos-object-storage"  # nosec B105

# ACM Resource Names
RESTORE_PASSIVE_SYNC_NAME = "restore-acm-passive-sync"
RESTORE_FULL_NAME = "restore-acm-full"
BACKUP_SCHEDULE_DEFAULT_NAME = "acm-hub-backup"

# Observability Components
THANOS_COMPACTOR_STATEFULSET = "observability-thanos-compact"
THANOS_COMPACTOR_LABEL_SELECTOR = "app=thanos-compact"

# ACM Spec Field Names
SPEC_VELERO_MANAGED_CLUSTERS_BACKUP_NAME = "veleroManagedClustersBackupName"
SPEC_SYNC_RESTORE_WITH_NEW_BACKUPS = "syncRestoreWithNewBackups"

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
