"""Tests for the acm_backup_schedule collection module."""

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_backup_schedule import (
    backup_schedule_pause_mode,
    build_backup_schedule_operation,
    main,
)


def _run_module(
    monkeypatch,
    *,
    acm_version: str = "2.13.2",
    intent: str = "pause",
    schedules: list[dict] | None = None,
    saved_schedule: dict | None = None,
    check_mode: bool = False,
) -> dict:
    captured: dict = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "acm_version": acm_version,
                "intent": intent,
                "schedules": schedules or [],
                "saved_schedule": saved_schedule or {},
            }
            self.check_mode = check_mode

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs
            raise SystemExit(0)

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs
            raise SystemExit(1)

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_backup_schedule.AnsibleModule",
        FakeModule,
    )

    try:
        main()
    except SystemExit:
        pass
    return captured


def test_pause_mode_uses_delete_for_acm_211():
    assert backup_schedule_pause_mode("2.11.6") == "delete"


def test_pause_mode_uses_spec_paused_for_acm_212_plus():
    assert backup_schedule_pause_mode("2.12.0") == "pause"


def test_build_pause_operation_for_spec_paused_mode():
    operation = build_backup_schedule_operation(
        acm_version="2.13.2",
        intent="pause",
        schedules=[{"metadata": {"name": "acm-hub-backup"}, "spec": {"paused": False}}],
    )
    assert operation["action"] == "patch"
    assert operation["patch"]["spec"]["paused"] is True


def test_build_rejects_multiple_backup_schedules():
    with pytest.raises(ValueError, match="Multiple BackupSchedules"):
        build_backup_schedule_operation(
            acm_version="2.13.2",
            intent="pause",
            schedules=[
                {"metadata": {"name": "schedule-a"}, "spec": {"paused": False}},
                {"metadata": {"name": "schedule-b"}, "spec": {"paused": True}},
            ],
        )


def test_build_returns_none_when_schedules_empty():
    for intent in ("pause", "enable"):
        operation = build_backup_schedule_operation(
            acm_version="2.13.2",
            intent=intent,
            schedules=[],
        )
        assert operation["action"] == "none"


def test_build_enable_creates_saved_schedule_when_missing_on_secondary():
    operation = build_backup_schedule_operation(
        acm_version="2.11.6",
        intent="enable",
        schedules=[],
        saved_schedule={
            "apiVersion": "cluster.open-cluster-management.io/v1beta1",
            "kind": "BackupSchedule",
            "metadata": {
                "name": "acm-hub-backup",
                "namespace": "open-cluster-management-backup",
            },
            "spec": {"veleroSchedule": "*/30 * * * *", "paused": True},
        },
    )
    assert operation["action"] == "create"
    assert operation["body"]["metadata"]["name"] == "acm-hub-backup"
    assert operation["body"]["spec"]["paused"] is False


def test_build_returns_none_when_already_paused():
    operation = build_backup_schedule_operation(
        acm_version="2.13.2",
        intent="pause",
        schedules=[{"spec": {"paused": True}}],
    )
    assert operation["action"] == "none"


def test_build_returns_none_when_already_enabled():
    operation = build_backup_schedule_operation(
        acm_version="2.13.2",
        intent="enable",
        schedules=[{"spec": {"paused": False}}],
    )
    assert operation["action"] == "none"


def test_build_enable_patches_paused_schedule():
    operation = build_backup_schedule_operation(
        acm_version="2.13.2",
        intent="enable",
        schedules=[{"spec": {"paused": True}}],
    )
    assert operation["action"] == "patch"
    assert operation["patch"]["spec"]["paused"] is False


def test_build_pause_delete_mode_for_acm_211():
    operation = build_backup_schedule_operation(
        acm_version="2.11.6",
        intent="pause",
        schedules=[{"metadata": {"name": "acm-hub-backup"}, "spec": {"paused": False}}],
    )
    assert operation["action"] == "delete"
    assert operation["mode"] == "delete"


def test_pause_mode_raises_on_prerelease_version():
    """Pre-release versions like 2.14.3-rc1 should raise ValueError."""
    with pytest.raises(ValueError, match="Invalid ACM version format"):
        backup_schedule_pause_mode("2.14.3-rc1")


def test_pause_mode_raises_on_single_segment_version():
    """Single segment versions should raise ValueError."""
    with pytest.raises(ValueError, match="Invalid ACM version format"):
        backup_schedule_pause_mode("2")


def test_pause_mode_raises_on_empty_version():
    """Empty version should raise ValueError."""
    with pytest.raises(ValueError, match="Invalid ACM version format"):
        backup_schedule_pause_mode("")


def test_run_module_check_mode_returns_planned_pause_without_change(monkeypatch):
    schedules = [{"metadata": {"name": "acm-hub-backup"}, "spec": {"paused": False}}]

    result = _run_module(monkeypatch, schedules=schedules, check_mode=True)

    assert result["exit"]["changed"] is False
    assert result["exit"]["operation"]["action"] == "patch"
    assert result["exit"]["operation"]["patch"] == {"spec": {"paused": True}}
    assert schedules == [
        {"metadata": {"name": "acm-hub-backup"}, "spec": {"paused": False}}
    ]


def test_run_module_pause_when_already_paused_is_idempotent(monkeypatch):
    result = _run_module(
        monkeypatch,
        schedules=[{"metadata": {"name": "acm-hub-backup"}, "spec": {"paused": True}}],
    )

    assert result["exit"] == {
        "changed": False,
        "operation": {"action": "none", "mode": "pause"},
    }


def test_run_module_enable_when_already_enabled_is_idempotent(monkeypatch):
    result = _run_module(
        monkeypatch,
        intent="enable",
        schedules=[{"metadata": {"name": "acm-hub-backup"}, "spec": {"paused": False}}],
    )

    assert result["exit"] == {
        "changed": False,
        "operation": {"action": "none", "mode": "pause"},
    }
