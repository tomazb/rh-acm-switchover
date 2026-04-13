"""Tests for the acm_restore_info collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info import (
    build_activation_patch,
    build_restore_activation_plan,
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


def test_build_restore_activation_plan_for_passive_patch_mode():
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[
            {
                "metadata": {"name": "restore-acm-passive-sync", "namespace": "open-cluster-management-backup"},
                "spec": {"syncRestoreWithNewBackups": True},
                "status": {"phase": "Enabled"},
            }
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "patch"
    assert plan["operation"]["patch"] == {"spec": {"veleroManagedClustersBackupName": "latest"}}
    assert plan["wait_target"]["name"] == "restore-acm-passive-sync"
    assert plan["wait_target"]["success_phases"] == ["Enabled", "Finished", "Completed"]
    assert plan["wait_target"]["velero_restore_required"] is True
    assert plan["wait_target"]["velero_restore_status_field"] == "veleroManagedClustersRestoreName"
    assert plan["wait_target"]["velero_success_phases"] == ["Completed"]


def test_build_restore_activation_plan_passive_patch_already_applied():
    """When veleroManagedClustersBackupName is already 'latest', skip patch but still set wait_target with Finished accepted."""
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[
            {
                "metadata": {"name": "restore-acm-passive-sync", "namespace": "open-cluster-management-backup"},
                "spec": {"syncRestoreWithNewBackups": True, "veleroManagedClustersBackupName": "latest"},
                "status": {"phase": "Finished"},
            }
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "none"
    assert plan["wait_target"]["name"] == "restore-acm-passive-sync"
    assert "Finished" in plan["wait_target"]["success_phases"]
    assert "Enabled" in plan["wait_target"]["success_phases"]



    plan = build_restore_activation_plan(
        method="passive",
        activation_method="restore",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                    "labels": {"managed-by": "test"},
                    "annotations": {"example": "annotation"},
                },
                "spec": {"syncRestoreWithNewBackups": True},
                "status": {"phase": "Enabled"},
            }
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "delete_and_create"
    assert plan["operation"]["delete_restore"]["name"] == "restore-acm-passive-sync"
    assert plan["operation"]["create_restore"]["metadata"]["name"] == "restore-acm-activate"
    assert plan["operation"]["create_restore"]["spec"]["veleroManagedClustersBackupName"] == "latest"
    assert plan["operation"]["create_restore"]["spec"]["veleroCredentialsBackupName"] == "skip"
    assert plan["operation"]["create_restore"]["spec"]["veleroResourcesBackupName"] == "skip"
    assert plan["operation"]["rollback_restore"]["metadata"]["labels"] == {"managed-by": "test"}
    assert plan["wait_target"]["name"] == "restore-acm-activate"
    assert plan["wait_target"]["success_phases"] == ["Finished", "Completed"]
    # velero_restore_required must NOT be set for activation_method=restore: the Restore CR
    # phase reaching Finished/Completed is sufficient; separate Velero restore polling is
    # only needed for the patch path where the Restore stays "Enabled" indefinitely.
    # Absence of velero_restore_required prevents the Ansible "Publish restore wait result"
    # task from accessing .resources on skipped Velero polling task variables (Bug 14).
    assert not plan["wait_target"].get("velero_restore_required", False)


def test_build_restore_activation_plan_for_full_restore_mode():
    plan = build_restore_activation_plan(
        method="full",
        activation_method="patch",
        restores=[],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "create"
    assert plan["operation"]["create_restore"]["metadata"]["name"] == "restore-acm-full"
    assert plan["operation"]["create_restore"]["spec"]["veleroManagedClustersBackupName"] == "latest"
    assert plan["operation"]["create_restore"]["spec"]["veleroCredentialsBackupName"] == "latest"
    assert plan["operation"]["create_restore"]["spec"]["veleroResourcesBackupName"] == "latest"
    assert plan["wait_target"]["name"] == "restore-acm-full"
    assert plan["wait_target"]["success_phases"] == ["Finished", "Completed"]
