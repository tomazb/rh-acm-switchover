#!/bin/bash
#
# Constants for ACM Switchover Scripts
#
# This file contains shared constants used by preflight-check.sh and postflight-check.sh.
# Sourcing this file ensures consistency across scripts.

# =============================================================================
# Script Version
# =============================================================================
# Version format: MAJOR.MINOR.PATCH
# - MAJOR: Breaking changes to script behavior or output format
# - MINOR: New features, new checks, significant improvements
# - PATCH: Bug fixes, minor improvements
export SCRIPT_VERSION="1.4.6"
export SCRIPT_VERSION_DATE="2025-12-25"

# Namespaces
export ACM_NAMESPACE="open-cluster-management"
export BACKUP_NAMESPACE="open-cluster-management-backup"
export OBSERVABILITY_NAMESPACE="open-cluster-management-observability"

# Resource Names
export MCH_NAME="multiclusterhub" # Default MCH name, though scripts often find it dynamically
export LOCAL_CLUSTER_NAME="local-cluster"
export THANOS_OBJECT_STORAGE_SECRET="thanos-object-storage"

# Observability Pods (Prefixes)
export OBS_GRAFANA_POD="observability-grafana"
export OBS_API_POD="observability-observatorium-api"
export OBS_THANOS_QUERY_POD="observability-thanos-query"
export OBS_THANOS_COMPACT_POD="observability-thanos-compact"

# Backup/Restore
# Note: BackupSchedule and DataProtectionApplication names are often dynamic or specific to environment,
# but we can define defaults or common patterns here if needed.
# Currently scripts detect some of these dynamically.
export RESTORE_PASSIVE_SYNC_NAME="restore-acm-passive-sync"

# =============================================================================
# Fully Qualified API Resource Types
# =============================================================================
# Use these constants instead of short names to avoid ambiguity
# and ensure consistency with the runbook documentation.

# ACM Resources
export RES_MCH="multiclusterhub.operator.open-cluster-management.io"
export RES_MCO="multiclusterobservability.observability.open-cluster-management.io"
export RES_MANAGED_CLUSTER="managedcluster.cluster.open-cluster-management.io"
export RES_BACKUP_SCHEDULE="backupschedule.cluster.open-cluster-management.io"
export RES_RESTORE="restore.cluster.open-cluster-management.io"

# Velero/OADP Resources
export RES_DPA="dataprotectionapplication.oadp.openshift.io"
export RES_BSL="backupstoragelocation.velero.io"
export RES_BACKUP="backup.velero.io"

# Hive Resources
export RES_CLUSTER_DEPLOYMENT="clusterdeployment.hive.openshift.io"

# Auto-Import Strategy (ACM 2.14+)
export MCE_NAMESPACE="multicluster-engine"
export IMPORT_CONTROLLER_CONFIGMAP="import-controller-config"
export AUTO_IMPORT_STRATEGY_KEY="autoImportStrategy"
export AUTO_IMPORT_STRATEGY_DEFAULT="ImportOnly"
export AUTO_IMPORT_STRATEGY_SYNC="ImportAndSync"
export AUTO_IMPORT_STRATEGY_DOC_URL="https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.14/html-single/clusters/index#custom-auto-import-strat"

# RBAC Constants
export SWITCHOVER_NAMESPACE="acm-switchover"
export OPERATOR_SA="acm-switchover-operator"
export VALIDATOR_SA="acm-switchover-validator"

# Managed Cluster Constants
# Used when generating kubeconfigs for managed clusters (--managed-cluster flag)
export MANAGED_CLUSTER_NAMESPACE="open-cluster-management-agent"
export MANAGED_CLUSTER_SA="klusterlet"

# Timeouts / Thresholds
export RESTORE_AGE_WARNING_SECONDS=3600 # 1 hour

# Exit Codes
export EXIT_SUCCESS=0
export EXIT_FAILURE=1
export EXIT_INVALID_ARGS=2
