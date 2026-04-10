"""ManagedCluster condition summarization helpers."""

from __future__ import annotations


def summarize_cluster(cluster: dict) -> dict:
    conditions = cluster.get("status", {}).get("conditions", [])
    return {
        "name": cluster.get("metadata", {}).get("name", "unknown"),
        "joined": any(item.get("type") == "ManagedClusterJoined" and item.get("status") == "True" for item in conditions),
        "available": any(
            item.get("type") == "ManagedClusterConditionAvailable" and item.get("status") == "True"
            for item in conditions
        ),
    }
