"""Tests for the acm_cluster_verify collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_cluster_verify import (
    summarize_cluster_group,
)


def test_cluster_group_fails_when_threshold_not_met():
    summary = summarize_cluster_group(
        [
            {"name": "cluster-a", "joined": True, "available": True},
            {"name": "cluster-b", "joined": False, "available": False},
        ],
        min_managed_clusters=2,
    )
    assert summary["passed"] is False
    assert "cluster-b" in summary["pending"]
