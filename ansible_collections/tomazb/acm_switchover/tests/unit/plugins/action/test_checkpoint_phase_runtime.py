# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for checkpoint_phase and write_artifact action plugin runtime helpers."""

from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
    build_phase_transition,
)
from ansible_collections.tomazb.acm_switchover.plugins.action.write_artifact import (
    build_report_ref,
)


def test_build_phase_transition_marks_completion():
    transition = build_phase_transition(
        checkpoint={"completed_phases": ["preflight"]},
        phase="activation",
        status="pass",
    )
    assert transition["completed_phases"] == ["preflight", "activation"]
    assert transition["phase_status"] == "pass"


def test_build_phase_transition_does_not_mark_on_fail():
    transition = build_phase_transition(
        checkpoint={"completed_phases": ["preflight"]},
        phase="activation",
        status="fail",
    )
    assert transition["completed_phases"] == ["preflight"]
    assert transition["phase_status"] == "fail"


def test_build_phase_transition_handles_missing_completed_phases():
    transition = build_phase_transition(checkpoint={}, phase="preflight", status="pass")
    assert transition["completed_phases"] == ["preflight"]


def test_build_report_ref_returns_expected_keys():
    ref = build_report_ref(path="/reports/activation.json", phase="activation")
    assert ref == {"phase": "activation", "path": "/reports/activation.json", "kind": "json-report"}


def test_action_module_persists_phase_status_on_pass(tmp_path):
    """Verify the ActionModule writes phase_status into the checkpoint dict."""
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import ActionModule

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(json.dumps({
        "schema_version": "1.0",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }))

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "activation",
        "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_file)},
        "status": "pass",
    }

    action = ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )

    result = action.run()
    assert result["checkpoint"]["phase_status"] == "pass"
    assert "activation" in result["checkpoint"]["completed_phases"]

    saved = json.loads(checkpoint_file.read_text())
    assert saved["phase_status"] == "pass"


def test_action_module_persists_phase_status_on_fail(tmp_path):
    """Verify phase_status is 'fail' when status is fail."""
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import ActionModule

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(json.dumps({
        "schema_version": "1.0",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }))

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "activation",
        "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_file)},
        "status": "fail",
        "error": "test error",
    }

    action = ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )

    result = action.run()
    assert result["checkpoint"]["phase_status"] == "fail"
    assert "activation" not in result["checkpoint"]["completed_phases"]


def test_build_report_ref_accepts_custom_kind():
    ref = build_report_ref(path="/reports/out.yaml", phase="preflight", kind="yaml-report")
    assert ref["kind"] == "yaml-report"
