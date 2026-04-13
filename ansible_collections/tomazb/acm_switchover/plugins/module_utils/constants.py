# SPDX-License-Identifier: MIT
"""Shared constants for collection modules and plugins."""

from __future__ import annotations

ACM_NAMESPACE = "open-cluster-management"
BACKUP_NAMESPACE = "open-cluster-management-backup"
OBSERVABILITY_NAMESPACE = "open-cluster-management-observability"
MCE_NAMESPACE = "multicluster-engine"
MANAGED_CLUSTER_AGENT_NAMESPACE = "open-cluster-management-agent"

PASSIVE_SYNC_RESTORE_NAME = "restore-acm-passive-sync"
ACTIVATION_RESTORE_NAME = "restore-acm-activate"
FULL_RESTORE_NAME = "restore-acm-full"

VELERO_BACKUP_SKIP = "skip"
CLEANUP_BEFORE_RESTORE_VALUE = "CleanupRestored"
WAIT_FAILURE_PHASES = ["FinishedWithErrors", "Error", "Failed", "PartiallyFailed", "FailedWithErrors"]

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
