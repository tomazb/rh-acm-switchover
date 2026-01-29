"""
GitOps marker detection for ACM switchover.

This module provides utilities to detect GitOps-managed resources (ArgoCD, Flux)
and collect warnings to help operators coordinate changes with their GitOps tooling.
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("acm_switchover")

# Maximum number of resources to display per kind before summarizing
MAX_DISPLAY_PER_KIND = 10


def detect_gitops_markers(metadata: Dict) -> List[str]:
    """Detect common GitOps markers on a resource.

    This helper performs lightweight substring checks to identify
    GitOps-related labels/annotations. It does not sanitize or
    transform URLs or other user input and must not be used for
    security-sensitive filtering.

    Args:
        metadata: The metadata dict from a Kubernetes resource

    Returns:
        List of marker strings in format "label:key" or "annotation:key"
    """
    markers: List[str] = []
    labels = metadata.get("labels") or {}
    annotations = metadata.get("annotations") or {}

    def _scan(source: Dict[str, str], source_name: str) -> None:
        for key, value in source.items():
            # Defensive: convert to string in case of unexpected types
            combined = f"{key}={str(value)}".lower()
            if "argocd" in combined or "argoproj.io" in combined:
                markers.append(f"{source_name}:{key}")
            elif "fluxcd.io" in combined or "toolkit.fluxcd.io" in combined:
                markers.append(f"{source_name}:{key}")
            elif key == "app.kubernetes.io/managed-by" and str(value).lower() in ("argocd", "fluxcd", "flux"):
                markers.append(f"{source_name}:{key}")

    _scan(labels, "label")
    _scan(annotations, "annotation")
    return markers


class GitOpsCollector:
    """Singleton collector for GitOps-managed resource detections.

    Records detected GitOps markers during workflow execution and prints
    a consolidated end-of-run report so operators can coordinate with
    their GitOps tooling to avoid drift.
    """

    _instance: Optional["GitOpsCollector"] = None

    def __new__(cls) -> "GitOpsCollector":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        # Structure: {context: {(namespace, kind, name): [markers]}}
        self._records: Dict[str, Dict[Tuple[str, str, str], List[str]]] = defaultdict(dict)
        self._enabled = True
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "GitOpsCollector":
        """Get the singleton instance of GitOpsCollector."""
        return cls()

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (primarily for testing)."""
        if cls._instance is not None:
            cls._instance._records = defaultdict(dict)
            cls._instance._enabled = True
            cls._instance._initialized = False

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable GitOps detection.

        Args:
            enabled: If False, record() calls will be ignored
        """
        self._enabled = enabled

    def is_enabled(self) -> bool:
        """Check if GitOps detection is enabled."""
        return self._enabled

    def record(
        self,
        context: str,
        namespace: str,
        kind: str,
        name: str,
        markers: List[str],
    ) -> None:
        """Record detected GitOps markers for a resource.

        Args:
            context: Kubernetes context (e.g., "primary", "secondary")
            namespace: Resource namespace (use "" for cluster-scoped resources)
            kind: Resource kind (e.g., "BackupSchedule", "ManagedCluster")
            name: Resource name
            markers: List of detected markers (e.g., ["label:app.kubernetes.io/managed-by"])
        """
        if not self._enabled or not markers:
            return

        key = (namespace, kind, name)
        self._records[context][key] = markers

    def has_detections(self) -> bool:
        """Check if any GitOps markers were detected."""
        return any(bool(records) for records in self._records.values())

    def get_detection_count(self) -> int:
        """Get total number of resources with detected GitOps markers."""
        return sum(len(records) for records in self._records.values())

    def get_records(self) -> Dict[str, Dict[Tuple[str, str, str], List[str]]]:
        """Get all recorded detections (for testing)."""
        return dict(self._records)

    def print_report(self) -> None:
        """Print consolidated report of all detected GitOps-managed resources.

        Output format:
        === GitOps-managed objects detected (N warnings) ===
        [primary] open-cluster-management-backup/BackupSchedule/acm-backup-schedule
          → label:app.kubernetes.io/managed-by=argocd
        [secondary] open-cluster-management/ManagedCluster/cluster1
          → annotation:argocd.argoproj.io/sync-wave
        ... and 15 more ManagedClusters
        """
        if not self._enabled:
            return

        if not self.has_detections():
            return

        count = self.get_detection_count()
        logger.warning("")
        logger.warning("=" * 60)
        logger.warning("GitOps-managed objects detected (%d warning%s)", count, "s" if count != 1 else "")
        logger.warning("=" * 60)
        logger.warning(
            "Coordinate changes with GitOps to avoid drift after switchover."
        )
        logger.warning("")

        for context, records in sorted(self._records.items()):
            if not records:
                continue

            # Group by kind for summarization
            by_kind: Dict[str, List[Tuple[str, str, List[str]]]] = defaultdict(list)
            for (namespace, kind, name), markers in sorted(records.items()):
                by_kind[kind].append((namespace, name, markers))

            for kind, items in sorted(by_kind.items()):
                displayed = 0
                for namespace, name, markers in items:
                    if displayed >= MAX_DISPLAY_PER_KIND:
                        remaining = len(items) - displayed
                        logger.warning(
                            "  ... and %d more %s(s)",
                            remaining,
                            kind,
                        )
                        break

                    # Build resource path
                    if namespace:
                        resource_path = f"{namespace}/{kind}/{name}"
                    else:
                        resource_path = f"{kind}/{name}"

                    logger.warning("[%s] %s", context, resource_path)
                    for marker in markers:
                        logger.warning("  → %s", marker)
                    displayed += 1

        logger.warning("")


def record_gitops_markers(
    context: str,
    namespace: str,
    kind: str,
    name: str,
    metadata: Dict,
) -> List[str]:
    """Convenience function to detect and record GitOps markers in one call.

    Args:
        context: Kubernetes context (e.g., "primary", "secondary")
        namespace: Resource namespace
        kind: Resource kind
        name: Resource name
        metadata: Resource metadata dict

    Returns:
        List of detected markers (empty if none)
    """
    markers = detect_gitops_markers(metadata)
    if markers:
        GitOpsCollector.get_instance().record(context, namespace, kind, name, markers)
    return markers
