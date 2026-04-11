"""Tests for the acm_restore_info collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info import (
    build_activation_patch,
    select_passive_sync_restore,
)


def test_select_passive_sync_restore_prefers_sync_enabled_resource():
    restore, diagnostics = select_passive_sync_restore(
        [
            {"metadata": {"name": "restore-old", "creationTimestamp": "2026-04-10T10:00:00Z"}, "spec": {}},
            {
                "metadata": {"name": "restore-passive", "creationTimestamp": "2026-04-10T11:00:00Z"},
                "spec": {"syncRestoreWithNewBackups": True},
            },
        ]
    )
    assert restore["metadata"]["name"] == "restore-passive"
    assert diagnostics["restore_count"] == 2
    assert diagnostics["sync_enabled_count"] == 1
    assert "reason" not in diagnostics


def test_select_passive_sync_restore_empty_list():
    restore, diagnostics = select_passive_sync_restore([])
    assert restore is None
    assert diagnostics["restore_count"] == 0
    assert diagnostics["sync_enabled_count"] == 0
    assert diagnostics["reason"] == "no_restores_found"


def test_select_passive_sync_restore_no_sync_enabled():
    restore, diagnostics = select_passive_sync_restore(
        [{"metadata": {"name": "r1", "creationTimestamp": "2026-04-10T10:00:00Z"}, "spec": {}}]
    )
    assert restore is None
    assert diagnostics["restore_count"] == 1
    assert diagnostics["sync_enabled_count"] == 0
    assert diagnostics["reason"] == "no_sync_restore"


def test_select_passive_sync_restore_handles_null_creation_timestamp():
    restore, diagnostics = select_passive_sync_restore(
        [
            {
                "metadata": {"name": "restore-null", "creationTimestamp": None},
                "spec": {"syncRestoreWithNewBackups": True},
            },
            {
                "metadata": {"name": "restore-new", "creationTimestamp": "2026-04-10T11:00:00Z"},
                "spec": {"syncRestoreWithNewBackups": True},
            },
        ]
    )
    assert restore["metadata"]["name"] == "restore-new"
    assert diagnostics["sync_enabled_count"] == 2


def test_build_activation_patch_targets_latest_backup():
    patch = build_activation_patch("latest")
    assert patch == {"spec": {"veleroManagedClustersBackupName": "latest"}}
