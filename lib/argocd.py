"""
Argo CD detection and management for ACM switchover.

Supports both operator install (argocds.argoproj.io) and vanilla Argo CD
(applications.argoproj.io only). Pause/resume auto-sync only for Applications
that touch ACM namespaces/kinds to prevent GitOps drift during switchover.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kubernetes.client.rest import ApiException

from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    GLOBAL_SET_NAMESPACE,
    LOCAL_CLUSTER_NAME,
    MCE_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
)
from lib.kube_client import KubeClient
from lib.utils import dry_run_skip

logger = logging.getLogger("acm_switchover")

# Argo CD Application CRD
ARGOCD_APP_GROUP = "argoproj.io"
ARGOCD_APP_VERSION = "v1alpha1"
ARGOCD_APP_PLURAL = "applications"
ARGOCD_INSTANCE_CRD_PLURAL = "argocds"

# Annotation key for our pause marker (must match scripts/argocd-manage.sh)
ARGOCD_PAUSED_BY_ANNOTATION = "acm-switchover.argoproj.io/paused-by"

# ACM namespace regex (must match scripts/lib-common.sh)
# Built from lib.constants to stay in sync with canonical namespace definitions.
ARGOCD_ACM_NS_REGEX = re.compile(
    r"^("
    + re.escape(ACM_NAMESPACE)
    + r"($|-)|"
    + re.escape(BACKUP_NAMESPACE)
    + r"$|"
    + re.escape(OBSERVABILITY_NAMESPACE)
    + r"$|"
    + re.escape(GLOBAL_SET_NAMESPACE)
    + r"$|"
    + re.escape(MCE_NAMESPACE)
    + r"$|"
    + re.escape(LOCAL_CLUSTER_NAME)
    + r")$"
)

# ACM kinds that matter for switchover (must match scripts/lib-common.sh)
ARGOCD_ACM_KINDS = frozenset(
    {
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
)


@dataclass
class ArgocdDiscoveryResult:
    """Result of Argo CD installation detection."""

    has_applications_crd: bool
    has_argocds_crd: bool
    argocd_instances: List[Dict[str, str]] = field(default_factory=list)
    install_type: str = "none"  # "operator" | "vanilla" | "none"


@dataclass
class AppImpact:
    """An Application that touches ACM resources."""

    namespace: str
    name: str
    resource_count: int
    app: Dict[str, Any]


@dataclass
class PauseResult:
    """Result of pausing one Application."""

    namespace: str
    name: str
    original_sync_policy: Dict[str, Any]
    patched: bool


@dataclass
class ResumeResult:
    """Result of resuming one Application."""

    namespace: str
    name: str
    restored: bool
    skip_reason: Optional[str] = None


def detect_argocd_installation(client: KubeClient) -> ArgocdDiscoveryResult:
    """
    Detect Argo CD installation (operator and/or vanilla).

    Checks for applications.argoproj.io CRD (required for any check) and
    optionally argocds.argoproj.io for operator install.

    Args:
        client: KubeClient for the cluster.

    Returns:
        ArgocdDiscoveryResult with CRD presence and instance list.
    """
    has_app = False
    has_argocds = False
    instances: List[Dict[str, str]] = []

    try:
        app_crd = client.get_custom_resource(
            group="apiextensions.k8s.io",
            version="v1",
            plural="customresourcedefinitions",
            name="applications.argoproj.io",
        )
        has_app = app_crd is not None
        argocds_crd = client.get_custom_resource(
            group="apiextensions.k8s.io",
            version="v1",
            plural="customresourcedefinitions",
            name="argocds.argoproj.io",
        )
        has_argocds = argocds_crd is not None
    except Exception as e:
        logger.debug("Failed to check CRDs for Argo CD detection: %s", e)
        return ArgocdDiscoveryResult(
            has_applications_crd=False,
            has_argocds_crd=False,
            install_type="none",
        )

    if not has_app:
        return ArgocdDiscoveryResult(
            has_applications_crd=False,
            has_argocds_crd=has_argocds,
            install_type="none",
        )

    if has_argocds:
        try:
            argocds = client.list_custom_resources(
                group=ARGOCD_APP_GROUP,
                version=ARGOCD_APP_VERSION,
                plural=ARGOCD_INSTANCE_CRD_PLURAL,
                namespace=None,
            )
            for a in argocds:
                meta = a.get("metadata", {})
                instances.append({"namespace": meta.get("namespace", ""), "name": meta.get("name", "")})
        except Exception as e:
            logger.debug("Failed to list ArgoCD instances: %s", e)
        install_type = "operator"
    else:
        install_type = "vanilla"

    return ArgocdDiscoveryResult(
        has_applications_crd=True,
        has_argocds_crd=has_argocds,
        argocd_instances=instances,
        install_type=install_type,
    )


def _application_namespaces(client: KubeClient) -> List[str]:
    """Return namespaces that contain Argo CD Applications (operator or all)."""
    try:
        argocds_crd = client.get_custom_resource(
            group="apiextensions.k8s.io",
            version="v1",
            plural="customresourcedefinitions",
            name="argocds.argoproj.io",
        )
    except Exception:
        argocds_crd = None
    if not argocds_crd:
        # Vanilla: get all Applications and collect namespaces
        try:
            apps = client.list_custom_resources(
                group=ARGOCD_APP_GROUP,
                version=ARGOCD_APP_VERSION,
                plural=ARGOCD_APP_PLURAL,
                namespace=None,
            )
            return list({a.get("metadata", {}).get("namespace", "") for a in apps if a.get("metadata")})
        except Exception as e:
            logger.debug("Failed to list Applications cluster-wide: %s", e)
            return []
    # Operator: use ArgoCD instance namespaces
    try:
        argocds = client.list_custom_resources(
            group=ARGOCD_APP_GROUP,
            version=ARGOCD_APP_VERSION,
            plural=ARGOCD_INSTANCE_CRD_PLURAL,
            namespace=None,
        )
        return [a.get("metadata", {}).get("namespace", "") for a in argocds if a.get("metadata", {}).get("namespace")]
    except Exception as e:
        logger.debug("Failed to list ArgoCD instances for namespaces: %s", e)
        return []


def list_argocd_applications(
    client: KubeClient,
    namespaces: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    List Argo CD Application resources.

    Args:
        client: KubeClient for the cluster.
        namespaces: If set, list only from these namespaces; else discover (operator or cluster-wide).

    Returns:
        List of Application resource dicts.
    """
    if namespaces is not None:
        result: List[Dict[str, Any]] = []
        for ns in namespaces:
            if not ns:
                continue
            try:
                items = client.list_custom_resources(
                    group=ARGOCD_APP_GROUP,
                    version=ARGOCD_APP_VERSION,
                    plural=ARGOCD_APP_PLURAL,
                    namespace=ns,
                )
                result.extend(items)
            except Exception as e:
                logger.debug("Failed to list Applications in %s: %s", ns, e)
        return result
    # Cluster-wide (works for namespaced Applications)
    try:
        return client.list_custom_resources(
            group=ARGOCD_APP_GROUP,
            version=ARGOCD_APP_VERSION,
            plural=ARGOCD_APP_PLURAL,
            namespace=None,
        )
    except Exception as e:
        logger.debug("Failed to list Applications: %s", e)
        return []


def _resource_touches_acm(resource: Dict[str, Any]) -> bool:
    """Return True if this status.resources[] entry touches ACM (namespace or kind)."""
    kind = resource.get("kind") or ""
    ns = (resource.get("namespace") or "").strip()
    if kind in ARGOCD_ACM_KINDS:
        return True
    if ns and ARGOCD_ACM_NS_REGEX.match(ns):
        return True
    return False


def find_acm_touching_apps(apps: List[Dict[str, Any]]) -> List[AppImpact]:
    """
    Filter Applications to those that touch ACM namespaces/kinds (per status.resources).

    Args:
        apps: List of Application resource dicts.

    Returns:
        List of AppImpact for apps that have at least one ACM-touching resource.
    """
    result: List[AppImpact] = []
    for app in apps:
        meta = app.get("metadata", {})
        ns = meta.get("namespace", "")
        name = meta.get("name", "")
        resources = app.get("status", {}).get("resources") or []
        if not isinstance(resources, list):
            continue
        acm_count = sum(1 for r in resources if isinstance(r, dict) and _resource_touches_acm(r))
        if acm_count > 0:
            result.append(AppImpact(namespace=ns, name=name, resource_count=acm_count, app=app))
    return result


@dry_run_skip(
    message="Skipping Argo CD Application patch in dry-run",
    return_value=lambda client, app, run_id: PauseResult(
        namespace=(app.get("metadata", {}) or {}).get("namespace", ""),
        name=(app.get("metadata", {}) or {}).get("name", ""),
        original_sync_policy=dict((app.get("spec", {}) or {}).get("syncPolicy") or {}),
        patched="automated" in ((app.get("spec", {}) or {}).get("syncPolicy") or {}),
    ),
)
def pause_autosync(
    client: KubeClient,
    app: Dict[str, Any],
    run_id: str,
) -> PauseResult:
    """
    Pause auto-sync for one Application (remove spec.syncPolicy.automated, add marker).

    Args:
        client: KubeClient for the cluster.
        app: Full Application resource dict.
        run_id: Run identifier for the pause marker.

    Returns:
        PauseResult with original sync policy and whether a patch was applied.
    """
    meta = app.get("metadata", {})
    ns = meta.get("namespace", "")
    name = meta.get("name", "")
    spec = app.get("spec", {})
    sync_policy = spec.get("syncPolicy") or {}
    original = dict(sync_policy)
    if "automated" not in sync_policy:
        return PauseResult(namespace=ns, name=name, original_sync_policy=original, patched=False)
    # Remove automated, keep rest; add annotation
    new_sync = {k: v for k, v in sync_policy.items() if k != "automated"}
    patch: Dict[str, Any] = {
        "metadata": {"annotations": {ARGOCD_PAUSED_BY_ANNOTATION: run_id}},
        "spec": {"syncPolicy": new_sync},
    }
    client.patch_custom_resource(
        group=ARGOCD_APP_GROUP,
        version=ARGOCD_APP_VERSION,
        plural=ARGOCD_APP_PLURAL,
        name=name,
        patch=patch,
        namespace=ns or None,
    )
    return PauseResult(namespace=ns, name=name, original_sync_policy=original, patched=True)


@dry_run_skip(
    message="Would resume auto-sync",
    return_value=lambda client, namespace, name, original_sync_policy, run_id: ResumeResult(
        namespace=namespace,
        name=name,
        restored=True,
    ),
)
def resume_autosync(
    client: KubeClient,
    namespace: str,
    name: str,
    original_sync_policy: Dict[str, Any],
    run_id: str,
) -> ResumeResult:
    """
    Restore auto-sync for one Application (only if our marker matches run_id).

    Args:
        client: KubeClient for the cluster.
        namespace: Application namespace.
        name: Application name.
        original_sync_policy: Previously saved spec.syncPolicy to restore.
        run_id: Run identifier; only restore if annotation matches.

    Returns:
        ResumeResult with restored=True if patch was applied.
    """
    try:
        current = client.get_custom_resource(
            group=ARGOCD_APP_GROUP,
            version=ARGOCD_APP_VERSION,
            plural=ARGOCD_APP_PLURAL,
            name=name,
            namespace=namespace or None,
        )
    except Exception as e:
        logger.debug("Failed to get Application %s/%s: %s", namespace, name, e)
        return ResumeResult(namespace=namespace, name=name, restored=False, skip_reason="not found")
    if not current:
        return ResumeResult(namespace=namespace, name=name, restored=False, skip_reason="not found")
    ann = (current.get("metadata") or {}).get("annotations") or {}
    marker = ann.get(ARGOCD_PAUSED_BY_ANNOTATION)
    if marker != run_id:
        return ResumeResult(
            namespace=namespace,
            name=name,
            restored=False,
            skip_reason="marker mismatch or not paused by this run",
        )
    patch = {
        "metadata": {"annotations": {ARGOCD_PAUSED_BY_ANNOTATION: None}},
        "spec": {"syncPolicy": original_sync_policy},
    }
    try:
        client.patch_custom_resource(
            group=ARGOCD_APP_GROUP,
            version=ARGOCD_APP_VERSION,
            plural=ARGOCD_APP_PLURAL,
            name=name,
            patch=patch,
            namespace=namespace or None,
        )
    except ApiException as e:
        status = getattr(e, "status", None)
        reason = getattr(e, "reason", None)
        detail = f"{status} {reason}".strip() if status or reason else str(e)
        logger.warning(
            "Failed to patch Application %s/%s to resume auto-sync: %s",
            namespace,
            name,
            detail,
        )
        return ResumeResult(
            namespace=namespace,
            name=name,
            restored=False,
            skip_reason=f"patch failed: {detail}",
        )
    except Exception as e:
        detail = str(e)
        logger.warning(
            "Failed to patch Application %s/%s to resume auto-sync: %s",
            namespace,
            name,
            detail,
        )
        return ResumeResult(
            namespace=namespace,
            name=name,
            restored=False,
            skip_reason=f"patch failed: {detail}",
        )
    return ResumeResult(namespace=namespace, name=name, restored=True)


def run_id_or_new(existing: Optional[str] = None) -> str:
    """Return existing run_id or a new one (e.g. for state)."""
    return existing or f"{uuid.uuid4().hex[:12]}"
