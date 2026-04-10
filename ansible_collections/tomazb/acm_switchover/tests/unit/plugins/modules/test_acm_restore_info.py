"""Tests for the acm_restore_info collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info import (
    build_activation_patch,
    select_passive_sync_restore,
)


def test_select_passive_sync_restore_prefers_sync_enabled_resource():
    restore = select_passive_sync_restore(
        [
            {"metadata": {"name": "restore-old", "creationTimestamp": "2026-04-10T10:00:00Z"}, "spec": {}},
            {
                "metadata": {"name": "restore-passive", "creationTimestamp": "2026-04-10T11:00:00Z"},
                "spec": {"syncRestoreWithNewBackups": True},
            },
        ]
    )
    assert restore["metadata"]["name"] == "restore-passive"


def test_build_activation_patch_targets_latest_backup():
    patch = build_activation_patch("latest")
    assert patch == {"spec": {"veleroManagedClustersBackupName": "latest"}}
