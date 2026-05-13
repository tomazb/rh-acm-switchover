"""Shared Argo CD helpers for ACM switchover collection."""

from __future__ import annotations

import re
from typing import Optional

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ARGOCD_ACM_KINDS,
    ARGOCD_ACM_NAMESPACE_PATTERN,
    ARGOCD_ACM_NAMESPACES,
    ARGOCD_PAUSED_BY_ANNOTATION,
)

ACM_NAMESPACES = ARGOCD_ACM_NAMESPACES
ACM_NAMESPACE_REGEX = re.compile(ARGOCD_ACM_NAMESPACE_PATTERN)
ACM_KINDS = ARGOCD_ACM_KINDS
PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED = "applicationset-managed"
PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT = "unknown-acm-impact"


def _app_identity(app: dict) -> tuple[str, str]:
    metadata = app.get("metadata", {}) or {}
    return metadata.get("namespace", ""), metadata.get("name", "")


def _sync_policy(app: dict) -> dict:
    return dict((app.get("spec", {}) or {}).get("syncPolicy") or {})


def is_autosync_enabled(app: dict) -> bool:
    """Return True when the Application has automated sync configured."""
    return "automated" in _sync_policy(app)


def _status_resources(app: dict) -> Optional[list[dict]]:
    resources = (app.get("status", {}) or {}).get("resources")
    if not isinstance(resources, list) or not resources:
        return None
    return resources


def _status_resources_are_stale(app: dict) -> bool:
    metadata = app.get("metadata", {}) or {}
    status = app.get("status", {}) or {}
    generation = metadata.get("generation")
    observed_generation = status.get("observedGeneration")
    if generation is None or observed_generation is None:
        return False
    try:
        return int(observed_generation) < int(generation)
    except (TypeError, ValueError):
        return True


def _resources_have_unknown_acm_impact(app: dict) -> bool:
    return _status_resources(app) is None or _status_resources_are_stale(app)


def is_acm_touching_application(app: dict) -> bool:
    """Return True if any resource in the Application's status touches an ACM namespace or kind."""
    return count_acm_resources(app) > 0


def count_acm_resources(app: dict) -> int:
    """Return the number of Application status resources that touch ACM."""
    count = 0
    for resource in _status_resources(app) or []:
        namespace = resource.get("namespace")
        if namespace in ACM_NAMESPACES or (
            namespace and ACM_NAMESPACE_REGEX.match(namespace)
        ):
            count += 1
            continue
        if resource.get("kind") in ACM_KINDS:
            count += 1
    return count


def filter_acm_applications(applications: list[dict]) -> list[dict]:
    """Return only applications that manage ACM resources."""
    filtered = []
    for app in applications:
        count = count_acm_resources(app)
        if count > 0:
            metadata = app.get("metadata", {}) or {}
            annotated = dict(app)
            annotated["acm_resource_count"] = count
            annotated["namespace"] = metadata.get("namespace", "")
            annotated["name"] = metadata.get("name", "")
            filtered.append(annotated)
    return filtered


def _applicationset_owner_name(app: dict) -> str:
    for ref in app.get("metadata", {}).get("ownerReferences", []):
        if ref.get("kind") == "ApplicationSet":
            return ref.get("name") or "<unknown>"
    return "<unknown>"


def _unknown_impact_message(namespace: str, name: str) -> str:
    return (
        f"Application {namespace}/{name} has auto-sync enabled but status.resources is empty or stale, "
        "so the tool cannot determine whether it touches ACM resources. Refresh or sync the Application "
        "until Argo CD reports current resources, or inspect and pause it manually before retrying."
    )


def _applicationset_message(namespace: str, name: str, parent: str) -> str:
    return (
        f"Application {namespace}/{name} is managed by ApplicationSet {parent}; patching the child Application "
        "can be reverted by the ApplicationSet controller. Remediate: pause/update the ApplicationSet or its "
        "generator/template, then retry the switchover."
    )


def find_argocd_pause_blockers(applications: list[dict]) -> list[dict]:
    """Return auto-sync Applications that cannot be managed safely by child Application patches."""
    blockers = []
    for app in applications:
        if not is_autosync_enabled(app):
            continue

        namespace, name = _app_identity(app)
        if _resources_have_unknown_acm_impact(app):
            blockers.append(
                {
                    "namespace": namespace,
                    "name": name,
                    "reason": PAUSE_BLOCK_REASON_UNKNOWN_ACM_IMPACT,
                    "message": _unknown_impact_message(namespace, name),
                }
            )
            continue

        if has_applicationset_owner(app) and count_acm_resources(app) > 0:
            blockers.append(
                {
                    "namespace": namespace,
                    "name": name,
                    "reason": PAUSE_BLOCK_REASON_APPLICATIONSET_MANAGED,
                    "message": _applicationset_message(
                        namespace, name, _applicationset_owner_name(app)
                    ),
                }
            )
    return blockers


def build_pause_patch(sync_policy: dict, run_id: str) -> dict:
    """Build a patch that removes automated sync and marks the app as paused."""
    sync_policy = dict(sync_policy or {})
    if "automated" in sync_policy:
        sync_policy["automated"] = None
    return {
        "metadata": {"annotations": {ARGOCD_PAUSED_BY_ANNOTATION: run_id}},
        "spec": {"syncPolicy": sync_policy},
    }


def has_applicationset_owner(app: dict) -> bool:
    """Return True if app is owned by an ApplicationSet (patching may be reverted by the controller)."""
    for ref in app.get("metadata", {}).get("ownerReferences", []):
        if ref.get("kind") == "ApplicationSet":
            return True
    return False
