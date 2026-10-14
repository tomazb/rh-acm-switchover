"""Tests for the acm_restore_info collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    PASSIVE_RESTORE_CONVENTIONAL_NAME_FALLBACK_REASON,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info import (
    analyze_passive_restores,
    main,
    passive_restore_ready_for_preflight,
    passive_restore_ready_reason,
    select_passive_sync_restore,
)


def _run_module(
    monkeypatch,
    *,
    restores: list[dict] | None = None,
    allow_conventional_name_fallback: bool | None = None,
    check_mode: bool = False,
) -> dict:
    captured: dict = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            argument_spec = kwargs["argument_spec"]
            self.params = {
                "restores": restores or [],
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


def test_the_module_offers_no_activation_planning_or_rollback(monkeypatch):
    """R4-04 PR C Task 8: activation plans from the migration journal; the rollback that
    recreated a deleted passive sync Restore after a failed create is gone."""
    captured = {}

    class SpecModule:
        def __init__(self, *args, **kwargs):
            captured["spec"] = kwargs["argument_spec"]
            raise SystemExit(0)

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info.AnsibleModule", SpecModule
    )
    try:
        main()
    except SystemExit:
        pass
    assert set(captured["spec"]) == {"restores", "allow_conventional_name_fallback"}
    restores = [
        {
            "metadata": {"name": "restore-acm-passive-sync", "namespace": "open-cluster-management-backup"},
            "spec": {"syncRestoreWithNewBackups": True},
            "status": {"phase": "Enabled"},
        }
    ]
    result = _run_module(monkeypatch, restores=restores)["exit"]
    assert set(result) == {
        "changed",
        "restore",
        "restore_phase",
        "restore_ready",
        "restore_ready_reason",
        "restore_count",
        "sync_enabled_count",
        "reason",
    }
    assert result["changed"] is False


def test_the_analysis_reports_selection_and_readiness():
    restores = [
        {
            "metadata": {"name": "restore-acm-passive-sync", "namespace": "open-cluster-management-backup"},
            "spec": {"syncRestoreWithNewBackups": True},
            "status": {"phase": "Enabled"},
        }
    ]
    analysis = analyze_passive_restores(restores)
    assert analysis["restore"]["metadata"]["name"] == "restore-acm-passive-sync"
    assert (analysis["restore_phase"], analysis["restore_ready"]) == ("Enabled", True)
    assert analysis["restore_ready_reason"] == "Passive Restore phase Enabled is ready."
    assert (analysis["restore_count"], analysis["sync_enabled_count"], analysis["reason"]) == (1, 1, None)


def test_the_analysis_reports_a_missing_restore_not_ready():
    analysis = analyze_passive_restores([])
    assert analysis["restore"] is None and analysis["restore_ready"] is False
    assert analysis["restore_ready_reason"] == "No passive Restore resource was selected."
    assert analysis["reason"] == "no_restores_found"


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
