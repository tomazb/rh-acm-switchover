"""Managed cluster group verification helpers."""

from __future__ import annotations


def summarize_cluster_group(clusters: list[dict], min_managed_clusters: int) -> dict:
    pending = [item["name"] for item in clusters if not (item["joined"] and item["available"])]
    return {
        "passed": len(clusters) >= min_managed_clusters and not pending,
        "total": len(clusters),
        "pending": pending,
    }
