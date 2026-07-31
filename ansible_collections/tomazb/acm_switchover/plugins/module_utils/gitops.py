"""
GitOps marker detection helpers for the ACM Switchover Ansible collection.

This is a collection-native port of the marker rules from lib/gitops_detector.py.
Detection is read-only and warning-oriented; no mutations are performed here.
"""

from __future__ import annotations

from typing import Dict, List, Set


def detect_gitops_markers(metadata: Dict) -> List[str]:
    """Detect common GitOps markers on a Kubernetes resource metadata dict.

    Performs lightweight checks to identify ArgoCD and Flux labels/annotations.
    Returns a sorted list of marker strings in the format ``source:key`` or
    ``source:key (UNRELIABLE)`` for ambiguous labels.

    Note: ``app.kubernetes.io/instance`` is flagged as UNRELIABLE because it is
    a generic label not specific to any GitOps tool.
    """
    markers_set: Set[str] = set()
    labels = metadata.get("labels") or {}
    annotations = metadata.get("annotations") or {}

    def _scan(source: Dict[str, str], source_name: str) -> None:
        for key, value in source.items():
            value_str = str(value)
            if key == "app.kubernetes.io/instance":
                markers_set.add(f"{source_name}:{key} (UNRELIABLE)")
                continue
            if key == "argocd.argoproj.io/instance":
                markers_set.add(f"{source_name}:{key}")
                continue
            if key == "app.kubernetes.io/managed-by":
                if value_str.lower() in ("argocd", "fluxcd", "flux"):
                    markers_set.add(f"{source_name}:{key}")
                continue

            key_lower = key.lower()
            key_prefix = key_lower.split("/", 1)[0] if "/" in key_lower else ""
            if key_prefix == "argocd.argoproj.io":
                markers_set.add(f"{source_name}:{key}")
            if key_prefix == "fluxcd.io" or key_prefix.endswith(".fluxcd.io"):
                markers_set.add(f"{source_name}:{key}")

    _scan(labels, "label")
    _scan(annotations, "annotation")
    return sorted(markers_set)
