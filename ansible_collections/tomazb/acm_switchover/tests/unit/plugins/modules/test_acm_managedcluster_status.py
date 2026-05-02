"""Tests for the acm_managedcluster_status collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_managedcluster_status import (
    summarize_cluster,
)


def test_summarize_cluster_detects_joined_and_available():
    summary = summarize_cluster(
        {
            "metadata": {"name": "cluster-a"},
            "status": {
                "conditions": [
                    {"type": "ManagedClusterConditionAvailable", "status": "True"},
                    {"type": "ManagedClusterJoined", "status": "True"},
                ]
            },
        }
    )
    assert summary["joined"] is True
    assert summary["available"] is True
