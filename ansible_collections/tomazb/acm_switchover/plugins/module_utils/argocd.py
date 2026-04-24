"""Shared Argo CD helpers for ACM switchover collection."""

from __future__ import annotations

import re

ACM_NAMESPACES = {
    "open-cluster-management",
    "open-cluster-management-backup",
    "open-cluster-management-observability",
    "multicluster-engine",
    "open-cluster-management-global-set",
    "local-cluster",
}

ACM_NAMESPACE_REGEX = re.compile(r"^open-cluster-management($|-.*)")

ACM_KINDS = {
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


def is_acm_touching_application(app: dict) -> bool:
    """Return True if any resource in the Application's status touches an ACM namespace or kind."""
    for resource in app.get("status", {}).get("resources", []):
        namespace = resource.get("namespace")
        if namespace in ACM_NAMESPACES or (
            namespace and ACM_NAMESPACE_REGEX.match(namespace)
        ):
            return True
        if resource.get("kind") in ACM_KINDS:
            return True
    return False


def filter_acm_applications(applications: list[dict]) -> list[dict]:
    """Return only applications that manage ACM resources."""
    return [app for app in applications if is_acm_touching_application(app)]


def build_pause_patch(sync_policy: dict, run_id: str) -> dict:
    """Build a patch that removes automated sync and marks the app as paused."""
    sync_policy = dict(sync_policy or {})
    if "automated" in sync_policy:
        sync_policy["automated"] = None
    return {
        "metadata": {"annotations": {"acm-switchover.argoproj.io/paused-by": run_id}},
        "spec": {"syncPolicy": sync_policy},
    }


def has_applicationset_owner(app: dict) -> bool:
    """Return True if app is owned by an ApplicationSet (patching may be reverted by the controller)."""
    for ref in app.get("metadata", {}).get("ownerReferences", []):
        if ref.get("kind") == "ApplicationSet":
            return True
    return False
