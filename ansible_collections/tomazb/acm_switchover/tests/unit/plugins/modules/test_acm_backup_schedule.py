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
