"""Tests for the acm_backup_schedule collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_backup_schedule import (
    backup_schedule_pause_mode,
    build_backup_schedule_operation,
)


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


def test_build_returns_none_when_schedules_empty():
    for intent in ("pause", "enable"):
        operation = build_backup_schedule_operation(
            acm_version="2.13.2",
            intent=intent,
            schedules=[],
        )
        assert operation["action"] == "none"


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
