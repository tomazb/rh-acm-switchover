"""Tests for the acm_restore_info collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    PASSIVE_RESTORE_CONVENTIONAL_NAME_FALLBACK_REASON,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info import (
    build_activation_patch,
    build_restore_activation_plan,
    main,
    passive_restore_ready_for_preflight,
    passive_restore_ready_reason,
    select_passive_sync_restore,
)


def _run_module(
    monkeypatch,
    *,
    restores: list[dict] | None = None,
    method: str = "passive",
    activation_method: str = "patch",
    backup_name: str = "latest",
    allow_conventional_name_fallback: bool | None = None,
    check_mode: bool = False,
) -> dict:
    captured: dict = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            argument_spec = kwargs["argument_spec"]
            self.params = {
                "restores": restores or [],
                "method": method,
                "activation_method": activation_method,
                "backup_name": backup_name,
                "allow_conventional_name_fallback": (
                    argument_spec["allow_conventional_name_fallback"]["default"]
                    if allow_conventional_name_fallback is None
                    else allow_conventional_name_fallback
                ),
            }
            self.check_mode = check_mode

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs
            raise SystemExit(1)

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info.AnsibleModule",
        FakeModule,
    )

    try:
        main()
    except SystemExit:
        pass
    return captured


def test_select_passive_sync_restore_prefers_sync_enabled_resource():
    restore, diagnostics = select_passive_sync_restore(
        [
            {
                "metadata": {
                    "name": "restore-old",
                    "creationTimestamp": "2026-04-10T10:00:00Z",
                },
                "spec": {},
            },
            {
                "metadata": {
                    "name": "restore-passive",
                    "creationTimestamp": "2026-04-10T11:00:00Z",
                },
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
        [
            {
                "metadata": {"name": "r1", "creationTimestamp": "2026-04-10T10:00:00Z"},
                "spec": {},
            }
        ]
    )
    assert restore is None
    assert diagnostics["restore_count"] == 1
    assert diagnostics["sync_enabled_count"] == 0
    assert diagnostics["reason"] == "no_sync_restore"


def test_select_passive_sync_restore_rejects_conventional_name_by_default():
    restore, diagnostics = select_passive_sync_restore(
        [
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "creationTimestamp": "2026-04-10T10:00:00Z",
                },
                "spec": {},
            }
        ]
    )
    assert restore is None
    assert diagnostics["restore_count"] == 1
    assert diagnostics["sync_enabled_count"] == 0
    assert diagnostics["reason"] == "no_sync_restore"


def test_select_passive_sync_restore_falls_back_to_conventional_name_when_explicitly_enabled():
    restore, diagnostics = select_passive_sync_restore(
        [
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "creationTimestamp": "2026-04-10T10:00:00Z",
                },
                "spec": {},
            }
        ],
        allow_conventional_name_fallback=True,
    )
    assert restore["metadata"]["name"] == "restore-acm-passive-sync"
    assert diagnostics["restore_count"] == 1
    assert diagnostics["sync_enabled_count"] == 0
    assert diagnostics["reason"] == PASSIVE_RESTORE_CONVENTIONAL_NAME_FALLBACK_REASON


def test_select_passive_sync_restore_rejects_conventional_name_when_explicitly_disabled():
    restore, diagnostics = select_passive_sync_restore(
        [
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "creationTimestamp": "2026-04-10T10:00:00Z",
                },
                "spec": {},
            }
        ],
        allow_conventional_name_fallback=False,
    )
    assert restore is None
    assert diagnostics["restore_count"] == 1
    assert diagnostics["sync_enabled_count"] == 0
    assert diagnostics["reason"] == "no_sync_restore"


def test_select_passive_sync_restore_rejects_explicit_sync_false_conventional_name():
    restore, diagnostics = select_passive_sync_restore(
        [
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "creationTimestamp": "2026-04-10T10:00:00Z",
                },
                "spec": {"syncRestoreWithNewBackups": False},
            }
        ]
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
                "metadata": {
                    "name": "restore-new",
                    "creationTimestamp": "2026-04-10T11:00:00Z",
                },
                "spec": {"syncRestoreWithNewBackups": True},
            },
        ]
    )
    assert restore["metadata"]["name"] == "restore-new"
    assert diagnostics["sync_enabled_count"] == 2


def test_build_activation_patch_targets_latest_backup():
    patch = build_activation_patch("latest")
    assert patch == {"spec": {"veleroManagedClustersBackupName": "latest"}}


def test_build_activation_patch_includes_resource_version_when_supplied():
    patch = build_activation_patch("latest", resource_version="12345")
    assert patch == {
        "metadata": {"resourceVersion": "12345"},
        "spec": {"veleroManagedClustersBackupName": "latest"},
    }


def test_build_restore_activation_plan_for_passive_patch_mode():
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                    "resourceVersion": "42",
                },
                "spec": {"syncRestoreWithNewBackups": True},
                "status": {
                    "phase": "Enabled",
                    "veleroManagedClustersRestoreName": "velero-mc-old",
                },
            }
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "patch"
    assert plan["operation"]["patch"] == {
        "metadata": {"resourceVersion": "42"},
        "spec": {"veleroManagedClustersBackupName": "latest"},
    }
    assert plan["wait_target"]["name"] == "restore-acm-passive-sync"
    assert plan["wait_target"]["success_phases"] == ["Enabled", "Finished", "Completed"]
    assert plan["wait_target"]["velero_restore_required"] is True
    assert plan["wait_target"]["velero_restore_status_field"] == "veleroManagedClustersRestoreName"
    assert plan["wait_target"]["velero_success_phases"] == ["Completed"]
    assert plan["wait_target"]["previous_velero_restore_name"] == "velero-mc-old"
    assert plan["restore_ready"] is True
    assert plan["restore_ready_reason"] == "Passive Restore phase Enabled is ready."
    assert plan["restore_phase"] == "Enabled"


def test_run_module_check_mode_reports_would_change_for_planned_operation(monkeypatch):
    result = _run_module(
        monkeypatch,
        check_mode=True,
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                    "resourceVersion": "42",
                },
                "spec": {
                    "syncRestoreWithNewBackups": True,
                    "veleroManagedClustersBackupName": "older-backup",
                },
                "status": {"phase": "Enabled"},
            }
        ],
        backup_name="latest",
    )

    assert result["exit"]["changed"] is True
    assert result["exit"]["operation"]["action"] == "patch"
    assert result["exit"]["operation"]["patch"] == {
        "metadata": {"resourceVersion": "42"},
        "spec": {"veleroManagedClustersBackupName": "latest"},
    }


def test_run_module_rejects_conventional_passive_restore_by_default(monkeypatch):
    result = _run_module(
        monkeypatch,
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {},
                "status": {"phase": "Enabled"},
            }
        ],
    )

    assert result["exit"]["restore"] is None
    assert result["exit"]["reason"] == "no_sync_restore"
    assert result["exit"]["operation"]["action"] == "none"


def test_build_restore_activation_plan_defaults_passive_patch_to_latest_backup():
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {"syncRestoreWithNewBackups": True},
                "status": {"phase": "Enabled"},
            }
        ],
        backup_name=None,
    )

    assert plan["operation"]["action"] == "patch"
    assert plan["operation"]["patch"] == {"spec": {"veleroManagedClustersBackupName": "latest"}}
    assert plan["patch"] == {"spec": {"veleroManagedClustersBackupName": "latest"}}


def test_build_restore_activation_plan_marks_unknown_passive_restore_not_ready():
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {"syncRestoreWithNewBackups": True},
                "status": {"phase": "Unknown"},
            }
        ],
        backup_name="latest",
    )

    assert plan["restore"] is not None
    assert plan["restore_phase"] == "Unknown"
    assert plan["restore_ready"] is False
    assert plan["restore_ready_reason"] == "Passive Restore phase Unknown is not activation-ready."


def test_build_restore_activation_plan_normalizes_missing_passive_restore_phase():
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {"syncRestoreWithNewBackups": True},
                "status": {},
            }
        ],
        backup_name="latest",
    )

    assert plan["restore_phase"] == "Unknown"
    assert plan["restore_ready_reason"] == "Passive Restore phase Unknown is not activation-ready."


def test_build_restore_activation_plan_exposes_missing_restore_ready_reason():
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[],
        backup_name="latest",
    )

    assert plan["restore"] is None
    assert plan["restore_ready"] is False
    assert plan["restore_ready_reason"] == "No passive Restore resource was selected."


def test_passive_restore_ready_accepts_benign_finished_with_errors():
    restore = {
        "status": {
            "phase": "FinishedWithErrors",
            "messages": [
                "ManagedCluster cluster-a already available",
                "ManagedCluster cluster-b already available",
            ],
        }
    }

    assert passive_restore_ready_for_preflight(restore) is True
    assert (
        passive_restore_ready_reason(restore)
        == "Passive Restore FinishedWithErrors only contains already-available messages."
    )


def test_passive_restore_ready_accepts_only_anchored_benign_messages():
    restore = {
        "status": {
            "phase": "FinishedWithErrors",
            "messages": ["ManagedCluster cluster-a not already available"],
        }
    }

    assert passive_restore_ready_for_preflight(restore) is False


def test_passive_restore_ready_rejects_non_benign_finished_with_errors():
    restore = {
        "status": {
            "phase": "FinishedWithErrors",
            "messages": ["Velero restore failed validation"],
        }
    }

    assert passive_restore_ready_for_preflight(restore) is False
    assert passive_restore_ready_reason(restore) == "Passive Restore FinishedWithErrors contains non-benign errors."


def test_passive_restore_ready_rejects_non_string_finished_with_errors_messages():
    restore = {
        "status": {
            "phase": "FinishedWithErrors",
            "messages": [
                "ManagedCluster cluster-a already available",
                {"message": "already available"},
            ],
        }
    }

    assert passive_restore_ready_for_preflight(restore) is False
    assert passive_restore_ready_reason(restore) == "Passive Restore FinishedWithErrors contains non-benign errors."


def test_passive_restore_ready_reason_reports_hard_failure_phase():
    restore = {"status": {"phase": "Failed"}}

    assert passive_restore_ready_for_preflight(restore) is False
    assert passive_restore_ready_reason(restore) == "Passive Restore phase Failed failed."


def test_build_restore_activation_plan_passive_patch_already_applied():
    """When veleroManagedClustersBackupName is already 'latest', skip patch but still set wait_target with Finished accepted."""
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="patch",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {
                    "syncRestoreWithNewBackups": True,
                    "veleroManagedClustersBackupName": "latest",
                },
                "status": {
                    "phase": "Finished",
                    "veleroManagedClustersRestoreName": "velero-mc-existing",
                },
            }
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "none"
    assert plan["wait_target"]["name"] == "restore-acm-passive-sync"
    assert "Finished" in plan["wait_target"]["success_phases"]
    assert "Enabled" in plan["wait_target"]["success_phases"]
    assert "previous_velero_restore_name" not in plan["wait_target"]


def test_build_restore_activation_plan_passive_restore_activation():
    """When activation_method is restore, plan should delete existing passive sync restore and create activation restore."""
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
    assert plan["wait_target"]["velero_restore_required"] is True
    assert plan["wait_target"]["velero_restore_status_field"] == "veleroManagedClustersRestoreName"
    assert plan["wait_target"]["velero_success_phases"] == ["Completed"]


def test_build_restore_activation_plan_passive_restore_activation_cleans_stale_passive_on_resume():
    """Resume must still remove passive sync restore after activation Restore was created."""
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="restore",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                    "labels": {"managed-by": "test"},
                },
                "spec": {"syncRestoreWithNewBackups": True},
                "status": {"phase": "Enabled"},
            },
            {
                "metadata": {
                    "name": "restore-acm-activate",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {"veleroManagedClustersBackupName": "latest"},
                "status": {
                    "phase": "Finished",
                    "veleroManagedClustersRestoreName": "velero-managed-clusters",
                },
            },
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "delete"
    assert plan["operation"]["delete_restore"]["name"] == "restore-acm-passive-sync"
    assert "create_restore" not in plan["operation"]
    assert plan["wait_target"]["name"] == "restore-acm-activate"


def test_build_restore_activation_plan_passive_restore_activation_resume_no_passive_sync():
    """Resume: only restore-acm-activate present, no passive sync restore → action=none, wait on activation restore."""
    plan = build_restore_activation_plan(
        method="passive",
        activation_method="restore",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-activate",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {"veleroManagedClustersBackupName": "latest"},
                "status": {
                    "phase": "Finished",
                    "veleroManagedClustersRestoreName": "velero-mc-restore",
                },
            }
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "none"
    assert plan["restore"]["metadata"]["name"] == "restore-acm-activate"
    assert plan["wait_target"]["name"] == "restore-acm-activate"
    assert plan["wait_target"]["velero_restore_required"] is True
    assert "Finished" in plan["wait_target"]["success_phases"]


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
    assert plan["wait_target"]["velero_restore_required"] is False
    assert plan["wait_target"]["managed_cluster_presence_required"] is True


def test_build_restore_activation_plan_defaults_full_restore_to_latest_backup():
    plan = build_restore_activation_plan(
        method="full",
        activation_method="patch",
        restores=[],
        backup_name=None,
    )

    spec = plan["operation"]["create_restore"]["spec"]
    assert plan["operation"]["action"] == "create"
    assert spec["veleroManagedClustersBackupName"] == "latest"
    assert spec["veleroCredentialsBackupName"] == "latest"
    assert spec["veleroResourcesBackupName"] == "latest"


def test_build_restore_activation_plan_for_full_restore_preserves_passive_restore_for_rollback():
    plan = build_restore_activation_plan(
        method="full",
        activation_method="patch",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                    "labels": {"managed-by": "test"},
                    "annotations": {"example": "annotation"},
                },
                "spec": {
                    "syncRestoreWithNewBackups": True,
                    "veleroManagedClustersBackupName": "latest",
                },
                "status": {"phase": "Enabled"},
            }
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "delete_and_create"
    assert plan["operation"]["delete_restore"]["name"] == "restore-acm-passive-sync"
    assert plan["operation"]["create_restore"]["metadata"]["name"] == "restore-acm-full"
    assert plan["operation"]["rollback_restore"]["metadata"]["name"] == "restore-acm-passive-sync"
    assert plan["operation"]["rollback_restore"]["metadata"]["labels"] == {"managed-by": "test"}


def test_build_restore_activation_plan_for_full_restore_cleans_stale_passive_on_resume():
    """Resume must still remove passive sync restore after full Restore was created."""
    plan = build_restore_activation_plan(
        method="full",
        activation_method="patch",
        restores=[
            {
                "metadata": {
                    "name": "restore-acm-passive-sync",
                    "namespace": "open-cluster-management-backup",
                    "labels": {"managed-by": "test"},
                },
                "spec": {
                    "syncRestoreWithNewBackups": True,
                    "veleroManagedClustersBackupName": "latest",
                },
                "status": {"phase": "Enabled"},
            },
            {
                "metadata": {
                    "name": "restore-acm-full",
                    "namespace": "open-cluster-management-backup",
                },
                "spec": {"veleroManagedClustersBackupName": "latest"},
                "status": {
                    "phase": "Finished",
                    "veleroManagedClustersRestoreName": "velero-managed-clusters",
                },
            },
        ],
        backup_name="latest",
    )

    assert plan["operation"]["action"] == "delete"
    assert plan["operation"]["delete_restore"]["name"] == "restore-acm-passive-sync"
    assert "create_restore" not in plan["operation"]
    assert plan["wait_target"]["name"] == "restore-acm-full"
