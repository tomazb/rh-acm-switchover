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
    + r"($|-.*)|"
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
    skip_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ResumeResult:
    """Result of resuming one Application."""

    namespace: str
    name: str
    restored: bool
    skip_reason: Optional[str] = None


RESUME_SKIP_REASON_MARKER_MISSING = "marker missing or already resumed"
RESUME_SKIP_REASON_MARKER_MISMATCH = "marker mismatch (paused by different run)"
PAUSE_SKIP_REASON_AUTOSYNC_DISABLED = "auto-sync not enabled"


@dataclass
class ResumeSummary:
    """Aggregated result of restoring a batch of paused Applications."""

    restored: int = 0
    already_resumed: int = 0
    failed: int = 0


def is_resume_noop(result: ResumeResult) -> bool:
    """Return True when resume did not patch because app is already resumed."""
    return (not result.restored) and (result.skip_reason == RESUME_SKIP_REASON_MARKER_MISSING)


def _get_crd_presence(
    client: KubeClient,
    crd_name: str,
    *,
    required: bool,
) -> Optional[bool]:
    """Return CRD presence, or None if an optional lookup failed unexpectedly."""
    try:
        crd = client.get_custom_resource(
            group="apiextensions.k8s.io",
            version="v1",
            plural="customresourcedefinitions",
            name=crd_name,
        )
        return crd is not None
    except ApiException as e:
        if e.status == 404:
            logger.debug("CRD %s not found (not installed): %s", crd_name, e)
            return False
        logger.warning("Unexpected API error checking CRD %s (status=%s): %s", crd_name, e.status, e)
        if required:
            raise
    except Exception as e:
        logger.warning("Failed to check CRD %s: %s", crd_name, e)
        if required:
            raise
    return None
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
    has_app = _get_crd_presence(client, "applications.argoproj.io", required=True)
    has_argocds_present = _get_crd_presence(client, "argocds.argoproj.io", required=False)
    has_argocds = bool(has_argocds_present)
    instances: List[Dict[str, str]] = []
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
                instances.append(
                    {
                        "namespace": meta.get("namespace", ""),
                        "name": meta.get("name", ""),
                    }
                )
        except ApiException as e:
            if e.status != 404:
                logger.warning(
                    "Failed to list ArgoCD instances (status=%s); instance list may be incomplete", e.status
                )
            else:
                logger.debug("Failed to list ArgoCD instances: %s", e)
        except Exception as e:
            logger.warning("Failed to list ArgoCD instances: %s; instance list may be incomplete", e)
        install_type = "operator"
    else:
        install_type = "vanilla"

    return ArgocdDiscoveryResult(
        has_applications_crd=True,
        has_argocds_crd=has_argocds,
        argocd_instances=instances,
        install_type=install_type,
    )


def _list_argocd_applications_once(client: KubeClient, namespace: Optional[str]) -> List[Dict[str, Any]]:
    """List Argo CD Applications for one namespace scope and surface real errors."""
    scope_label = namespace or "cluster-wide scope"
    try:
        return client.list_custom_resources(
            group=ARGOCD_APP_GROUP,
            version=ARGOCD_APP_VERSION,
            plural=ARGOCD_APP_PLURAL,
            namespace=namespace,
        )
    except ApiException as e:
        if e.status == 404:
            logger.debug("Argo CD Applications not found in %s: %s", scope_label, e)
            return []
        logger.warning("Failed to list Argo CD Applications in %s (status=%s)", scope_label, e.status)
        raise
    except Exception as e:
        logger.warning("Failed to list Argo CD Applications in %s: %s", scope_label, e)
        raise


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

    Raises:
        ApiException: When Argo CD discovery fails for reasons other than 404/not-installed.
    """
    if namespaces is not None:
        result: List[Dict[str, Any]] = []
        for ns in namespaces:
            if not ns:
                continue
            result.extend(_list_argocd_applications_once(client, ns))
        return result
    return _list_argocd_applications_once(client, namespace=None)


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


def resume_recorded_applications(
    paused_apps: List[Any],
    run_id: str,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> ResumeSummary:
    """Restore auto-sync for recorded pause state and return an aggregated summary."""
    summary = ResumeSummary()
    for entry in paused_apps:
        if not isinstance(entry, dict):
            summary.failed += 1
            logger.warning("  Skip entry with unexpected format in Argo CD pause state")
            continue

        hub = entry.get("hub")
        ns = entry.get("namespace")
        name = entry.get("name")
        original_sync_policy = entry.get("original_sync_policy")

        if entry.get("dry_run"):
            summary.failed += 1
            logger.warning("  Skip %s/%s (pause was dry-run only)", ns, name)
            continue
        if not all([hub, ns, name, original_sync_policy is not None]):
            summary.failed += 1
            logger.warning("  Skip entry missing required fields (hub=%s, namespace=%s, name=%s)", hub, ns, name)
            continue

        if hub == "primary":
            client = primary
        elif hub == "secondary":
            client = secondary
        else:
            summary.failed += 1
            logger.warning("  Skip %s/%s (unrecognized hub=%s)", ns, name, hub)
            continue

        if not client:
            summary.failed += 1
            logger.warning("  Skip %s/%s (no client for hub=%s)", ns, name, hub)
            continue

        result = resume_autosync(client, ns, name, original_sync_policy, run_id)
        if result.restored:
            summary.restored += 1
            logger.info("  Resumed %s/%s on %s", ns, name, hub)
        elif is_resume_noop(result):
            summary.already_resumed += 1
            logger.info("  Already resumed %s/%s on %s", ns, name, hub)
        else:
            summary.failed += 1
            logger.warning("  Failed %s/%s: %s", ns, name, result.skip_reason or "not restored")

    return summary


# NOTE: dry_run_skip was designed for instance methods (it reads self.dry_run).
# Applied here to a module-level function, KubeClient takes the "self" slot, so
# dry-run is sourced from client.dry_run.  Callers must ensure the KubeClient
# is constructed with dry_run=True when dry-run mode is intended.
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
        return PauseResult(
            namespace=ns,
            name=name,
            original_sync_policy=original,
            patched=False,
            skip_reason=PAUSE_SKIP_REASON_AUTOSYNC_DISABLED,
        )
    # Remove automated, keep rest; add annotation
    new_sync = {k: v for k, v in sync_policy.items() if k != "automated"}
    patch: Dict[str, Any] = {
        "metadata": {"annotations": {ARGOCD_PAUSED_BY_ANNOTATION: run_id}},
        "spec": {"syncPolicy": new_sync},
    }
    try:
        client.patch_custom_resource(
            group=ARGOCD_APP_GROUP,
            version=ARGOCD_APP_VERSION,
            plural=ARGOCD_APP_PLURAL,
            name=name,
            patch=patch,
            namespace=ns or None,
        )
    except ApiException as e:
        status = getattr(e, "status", None)
        reason = getattr(e, "reason", None)
        detail = f"{status} {reason}".strip() if status or reason else str(e)
        logger.warning(
            "Failed to patch Application %s/%s to pause auto-sync: %s",
            ns,
            name,
            detail,
        )
        return PauseResult(
            namespace=ns,
            name=name,
            original_sync_policy=original,
            patched=False,
            error=detail,
        )
    except Exception as e:
        detail = str(e)
        logger.warning(
            "Failed to patch Application %s/%s to pause auto-sync: %s",
            ns,
            name,
            detail,
        )
        return PauseResult(
            namespace=ns,
            name=name,
            original_sync_policy=original,
            patched=False,
            error=detail,
        )
    return PauseResult(namespace=ns, name=name, original_sync_policy=original, patched=True)


# NOTE: same dry_run_skip / KubeClient-as-self pattern as pause_autosync above.
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
    except ApiException as e:
        if e.status == 404:
            logger.debug("Application %s/%s not found: %s", namespace, name, e)
            return ResumeResult(namespace=namespace, name=name, restored=False, skip_reason="not found")
        logger.warning(
            "API error fetching Application %s/%s (status=%s); leaving paused", namespace, name, e.status
        )
        return ResumeResult(
            namespace=namespace, name=name, restored=False, skip_reason=f"fetch error: {e.status}"
        )
    except Exception as e:
        logger.warning("Unexpected error fetching Application %s/%s: %s", namespace, name, e)
        return ResumeResult(namespace=namespace, name=name, restored=False, skip_reason=f"fetch error: {e}")
    if not current:
        return ResumeResult(namespace=namespace, name=name, restored=False, skip_reason="not found")
    ann = (current.get("metadata") or {}).get("annotations") or {}
    marker = ann.get(ARGOCD_PAUSED_BY_ANNOTATION)
    if marker != run_id:
        if not marker:
            return ResumeResult(
                namespace=namespace,
                name=name,
                restored=False,
                skip_reason=RESUME_SKIP_REASON_MARKER_MISSING,
            )
        return ResumeResult(
            namespace=namespace,
            name=name,
            restored=False,
            skip_reason=RESUME_SKIP_REASON_MARKER_MISMATCH,
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
