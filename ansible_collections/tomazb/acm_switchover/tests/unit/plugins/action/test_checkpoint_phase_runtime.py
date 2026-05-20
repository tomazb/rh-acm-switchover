# SPDX-License-Identifier: MIT
"""Tests for checkpoint_phase and artifact runtime helpers."""

import json
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, call, mock_open, patch

from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
    ActionModule,
    build_phase_transition,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.artifacts import (
    build_report_ref,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    build_operation_identity,
)


def _make_checkpoint_action(task_args):
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    task = MagicMock()
    task.async_val = 0
    task.args = task_args
    play_context = MagicMock()
    play_context.check_mode = False
    return ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=play_context,
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )


def _task_vars_for_mode(mode):
    return {"acm_switchover_execution": {"mode": mode}}


def _task_vars_with_operation_identity(mode="execute", collection_version=None):
    task_vars = {
        "acm_switchover_execution": {"mode": mode},
        "acm_switchover_hubs": {
            "primary": {
                "context": "primary-hub",
                "kubeconfig": "./kubeconfigs/primary",
                "cluster_uid": "uid-primary",
            },
            "secondary": {
                "context": "secondary-hub",
                "kubeconfig": "./kubeconfigs/secondary",
                "cluster_uid": "uid-secondary",
            },
        },
        "acm_switchover_operation": {
            "method": "passive",
            "activation_method": "patch",
            "restore_only": False,
            "old_hub_action": "secondary",
        },
        "acm_switchover_hub_identities": {
            "primary": {"cluster_uid": "uid-primary"},
            "secondary": {"cluster_uid": "uid-secondary"},
        },
    }
    if collection_version is not None:
        task_vars["acm_switchover_collection_version"] = collection_version
    return task_vars


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


def test_build_phase_transition_removes_failed_phase_from_completed_phases():
    transition = build_phase_transition(
        checkpoint={"completed_phases": ["preflight", "primary_prep", "activation"]},
        phase="activation",
        status="fail",
    )
    assert transition["completed_phases"] == ["preflight", "primary_prep"]
    assert transition["phase_status"] == "fail"


def test_build_phase_transition_fail_preserves_unrelated_completed_phases():
    transition = build_phase_transition(
        checkpoint={
            "completed_phases": [
                "preflight",
                "primary_prep",
                "activation",
                "post_activation",
            ]
        },
        phase="activation",
        status="fail",
    )
    assert transition["completed_phases"] == [
        "preflight",
        "primary_prep",
        "post_activation",
    ]


def test_build_phase_transition_resets_completed_phase():
    transition = build_phase_transition(
        checkpoint={"completed_phases": ["preflight", "primary_prep", "activation"]},
        phase="primary_prep",
        status="reset",
    )
    assert transition["completed_phases"] == ["preflight", "activation"]
    assert transition["phase_status"] == "reset"


def test_build_phase_transition_handles_missing_completed_phases():
    transition = build_phase_transition(checkpoint={}, phase="preflight", status="pass")
    assert transition["completed_phases"] == ["preflight"]


def test_build_report_ref_returns_expected_keys():
    ref = build_report_ref(path="/reports/activation.json", phase="activation")
    assert ref == {
        "phase": "activation",
        "path": "/reports/activation.json",
        "kind": "json-report",
    }


def test_action_module_persists_phase_status_on_pass(tmp_path):
    """Verify the ActionModule writes phase_status into the checkpoint dict."""
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": build_operation_identity(hubs={}, operation={}),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "activation",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
        },
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

    result = action.run(task_vars=_task_vars_for_mode("execute"))
    assert result["checkpoint"]["phase_status"] == "pass"
    assert "activation" in result["checkpoint"]["completed_phases"]

    saved = json.loads(checkpoint_file.read_text())
    assert saved["phase_status"] == "pass"


def test_action_module_merges_operational_data_on_pass(tmp_path):
    """checkpoint_phase should merge operational_data updates into persisted state."""
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "completed_phases": ["preflight"],
                "operational_data": {"existing": "keep"},
                "operation_identity": build_operation_identity(hubs={}, operation={}),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "activation",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
        },
        "status": "pass",
        "operational_data": {"backup_schedule_enabled_at": "2026-04-16T10:00:00Z"},
    }

    action = ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))
    assert result["checkpoint"]["operational_data"] == {
        "existing": "keep",
        "backup_schedule_enabled_at": "2026-04-16T10:00:00Z",
    }

    saved = json.loads(checkpoint_file.read_text())
    assert saved["operational_data"] == {
        "existing": "keep",
        "backup_schedule_enabled_at": "2026-04-16T10:00:00Z",
    }


def test_action_module_does_not_overwrite_operational_data_with_empty_strings(tmp_path):
    """checkpoint_phase should ignore empty-string operational_data updates."""
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "completed_phases": ["preflight"],
                "operational_data": {"backup_schedule_enabled_at": "2026-04-16T10:00:00Z"},
                "operation_identity": build_operation_identity(hubs={}, operation={}),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "activation",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
        },
        "status": "fail",
        "error": "dry-run failure",
        "operational_data": {"backup_schedule_enabled_at": ""},
    }

    action = ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))
    assert result["checkpoint"]["operational_data"]["backup_schedule_enabled_at"] == "2026-04-16T10:00:00Z"

    saved = json.loads(checkpoint_file.read_text())
    assert saved["operational_data"]["backup_schedule_enabled_at"] == "2026-04-16T10:00:00Z"


def test_action_module_persists_phase_status_on_fail(tmp_path):
    """Verify phase_status is 'fail' when status is fail."""
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": build_operation_identity(hubs={}, operation={}),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "activation",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
        },
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

    result = action.run(task_vars=_task_vars_for_mode("execute"))
    assert result["checkpoint"]["phase_status"] == "fail"
    assert "activation" not in result["checkpoint"]["completed_phases"]


def test_action_module_fail_prunes_previously_completed_phase(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "completed_phases": ["preflight", "primary_prep", "activation"],
                "operational_data": {},
                "operation_identity": build_operation_identity(hubs={}, operation={}),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "fail",
            "error": "activation retry failed",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))

    assert result["checkpoint"]["phase_status"] == "fail"
    assert result["checkpoint"]["completed_phases"] == ["preflight", "primary_prep"]
    saved = json.loads(checkpoint_file.read_text())
    assert saved["completed_phases"] == ["preflight", "primary_prep"]


def test_action_module_persists_checkpoint_reset_without_error(tmp_path):
    """status=reset should remove the phase from completed_phases and persist it."""
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "completed_phases": ["preflight", "primary_prep", "activation"],
                "operational_data": {"argocd_run_id": "run-1"},
                "operation_identity": build_operation_identity(hubs={}, operation={}),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "primary_prep",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
        },
        "status": "reset",
        "error": "reset must not append errors",
    }

    action = ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))
    assert result["checkpoint"]["phase_status"] == "reset"
    assert result["checkpoint"]["completed_phases"] == ["preflight", "activation"]
    assert result["checkpoint"]["errors"] == []

    saved = json.loads(checkpoint_file.read_text())
    assert saved["phase_status"] == "reset"
    assert saved["completed_phases"] == ["preflight", "activation"]
    assert saved["errors"] == []


def test_action_module_check_mode_pass_leaves_existing_checkpoint_unchanged(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "operation_identity": build_operation_identity(hubs={}, operation={}),
        "errors": [],
        "report_refs": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original, indent=2))
    original_bytes = checkpoint_file.read_bytes()
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
        }
    )

    result = action.run(task_vars={**_task_vars_for_mode("execute"), "ansible_check_mode": True})

    assert result["changed"] is False
    assert result["check_mode"] is True
    assert result["checkpoint"]["completed_phases"] == ["preflight"]
    assert checkpoint_file.read_bytes() == original_bytes


def test_action_module_play_context_check_mode_fail_leaves_existing_checkpoint_unchanged(
    tmp_path,
):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "activation",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "operation_identity": build_operation_identity(hubs={}, operation={}),
        "errors": [],
        "report_refs": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original, indent=2))
    original_bytes = checkpoint_file.read_bytes()
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "fail",
            "error": "planned failure",
        }
    )
    action._play_context.check_mode = True

    result = action.run(task_vars=_task_vars_for_mode("execute"))

    assert result["changed"] is False
    assert result["check_mode"] is True
    assert result["checkpoint"]["errors"] == []
    assert "phase_status" not in result["checkpoint"]
    assert checkpoint_file.read_bytes() == original_bytes


def test_action_module_check_mode_and_dry_run_flags_are_non_exclusive(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "operation_identity": build_operation_identity(hubs={}, operation={}),
        "errors": [],
        "report_refs": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original, indent=2))
    original_bytes = checkpoint_file.read_bytes()
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
        }
    )

    result = action.run(task_vars={**_task_vars_for_mode("dry_run"), "ansible_check_mode": True})

    assert result["changed"] is False
    assert result["check_mode"] is True
    assert result["dry_run"] is True
    assert checkpoint_file.read_bytes() == original_bytes


def test_action_module_check_mode_does_not_create_missing_checkpoint(tmp_path):
    checkpoint_file = tmp_path / "missing-checkpoint.json"
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
        }
    )

    result = action.run(task_vars={**_task_vars_for_mode("execute"), "ansible_check_mode": True})

    assert result["changed"] is False
    assert result["check_mode"] is True
    assert not checkpoint_file.exists()


def test_action_module_rejects_missing_phase(tmp_path):
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(tmp_path / "checkpoint.json"),
        },
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

    result = action.run(task_vars=_task_vars_for_mode("execute"))
    assert result["failed"] is True
    assert "Missing required checkpoint phase" in result["msg"]


def test_action_module_rejects_unknown_phase(tmp_path):
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "bogus-phase",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(tmp_path / "checkpoint.json"),
        },
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

    result = action.run(task_vars=_task_vars_for_mode("execute"))
    assert result["failed"] is True
    assert "Invalid checkpoint phase" in result["msg"]


def test_action_module_reset_discards_previous_checkpoint_state_on_preflight_enter(
    tmp_path,
):
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "phase": "activation",
                "completed_phases": ["preflight", "activation"],
                "operational_data": {"stale": True},
                "errors": [{"phase": "activation", "error": "boom"}],
                "report_refs": [
                    {
                        "phase": "activation",
                        "path": "/tmp/out.json",
                        "kind": "json-report",
                    }
                ],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "preflight",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
            "reset": True,
        },
        "status": "enter",
    }

    action = ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())
    assert result["checkpoint"]["phase"] == "preflight"
    assert result["checkpoint"]["completed_phases"] == []
    assert result["checkpoint"]["operation_identity"] == build_operation_identity(
        hubs=_task_vars_with_operation_identity()["acm_switchover_hubs"],
        operation=_task_vars_with_operation_identity()["acm_switchover_operation"],
    )
    assert result["skipped_phase"] is False


def test_action_module_new_checkpoint_includes_operation_identity(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
        }
    )

    task_vars = _task_vars_with_operation_identity()
    result = action.run(task_vars=task_vars)

    assert result["checkpoint"]["operation_identity"] == build_operation_identity(
        hubs=task_vars["acm_switchover_hubs"],
        operation=task_vars["acm_switchover_operation"],
    )


def test_action_module_enter_persists_backfilled_operation_identity_for_skipped_phase(
    tmp_path,
):
    import json

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": None,
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    task_vars = _task_vars_with_operation_identity()
    result = action.run(task_vars=task_vars)

    assert result["skipped_phase"] is True
    assert result["checkpoint"]["operation_identity"] == build_operation_identity(
        hubs=task_vars["acm_switchover_hubs"],
        operation=task_vars["acm_switchover_operation"],
    )
    saved = json.loads(checkpoint_file.read_text())
    assert saved["operation_identity"] == result["checkpoint"]["operation_identity"]


def test_action_module_reset_is_not_reapplied_after_initial_preflight_enter(tmp_path):
    import json
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "phase": "activation",
                "completed_phases": ["preflight", "activation"],
                "operational_data": {"stale": True},
                "errors": [{"phase": "activation", "error": "boom"}],
                "report_refs": [
                    {
                        "phase": "activation",
                        "path": "/tmp/out.json",
                        "kind": "json-report",
                    }
                ],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    enter_task = MagicMock()
    enter_task.async_val = 0
    enter_task.args = {
        "phase": "preflight",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
            "reset": True,
        },
        "status": "enter",
    }
    enter_action = ActionModule(
        task=enter_task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )
    enter_result = enter_action.run(task_vars=_task_vars_for_mode("execute"))
    assert enter_result["checkpoint"]["completed_phases"] == []

    pass_task = MagicMock()
    pass_task.async_val = 0
    pass_task.args = {
        "phase": "preflight",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
            "reset": True,
        },
        "status": "pass",
    }
    pass_action = ActionModule(
        task=pass_task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )
    pass_result = pass_action.run(task_vars=_task_vars_for_mode("execute"))
    assert pass_result["checkpoint"]["completed_phases"] == ["preflight"]

    activation_enter_task = MagicMock()
    activation_enter_task.async_val = 0
    activation_enter_task.args = {
        "phase": "activation",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": str(checkpoint_file),
            "reset": True,
        },
        "status": "enter",
    }
    activation_enter_action = ActionModule(
        task=activation_enter_task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )
    activation_enter_result = activation_enter_action.run(task_vars=_task_vars_for_mode("execute"))

    assert activation_enter_result["checkpoint"]["completed_phases"] == ["preflight"]
    assert activation_enter_result["skipped_phase"] is False


def test_action_module_dry_run_pass_does_not_mutate_checkpoint_file(tmp_path):
    import json

    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operational_data": {"argocd_run_id": "run-1"},
        "operation_identity": build_operation_identity(hubs={}, operation={}),
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original))
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
            "operational_data": {"dry_run_key": "discard"},
            "report_ref": "/tmp/activation-report.json",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("dry_run"))

    assert result["changed"] is False
    assert "activation" not in result["checkpoint"]["completed_phases"]
    assert json.loads(checkpoint_file.read_text()) == original


def test_action_module_dry_run_fail_does_not_mutate_checkpoint_file(tmp_path):
    import json

    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "activation",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "operation_identity": build_operation_identity(hubs={}, operation={}),
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original))
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "fail",
            "error": "dry-run failure",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("dry_run"))

    assert result["changed"] is False
    assert result["checkpoint"]["errors"] == []
    assert json.loads(checkpoint_file.read_text()) == original


def test_action_module_dry_run_reset_enter_does_not_mutate_checkpoint_file(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "1.0",
        "phase": "activation",
        "completed_phases": ["preflight", "activation"],
        "operational_data": {"stale": True},
        "errors": [{"phase": "activation", "error": "boom"}],
        "report_refs": [
            {
                "phase": "activation",
                "path": "/tmp/out.json",
                "kind": "json-report",
            }
        ],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original))
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
                "reset": True,
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("dry_run"))

    assert result["changed"] is False
    assert result["checkpoint"]["completed_phases"] == []
    assert result["skipped_phase"] is False
    assert json.loads(checkpoint_file.read_text()) == original


def test_action_module_validate_mode_does_not_mutate_checkpoint_file(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operational_data": {"argocd_run_id": "run-1"},
        "operation_identity": build_operation_identity(
            hubs=_task_vars_with_operation_identity()["acm_switchover_hubs"],
            operation=_task_vars_with_operation_identity()["acm_switchover_operation"],
        ),
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original))
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
            "operational_data": {"validate_key": "discard"},
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity(mode="validate"))

    assert result["changed"] is False
    assert result["checkpoint"]["completed_phases"] == ["preflight"]
    assert json.loads(checkpoint_file.read_text()) == original


def test_action_module_validate_mode_does_not_skip_completed_phase_on_enter(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operation_identity": build_operation_identity(
            hubs=_task_vars_with_operation_identity()["acm_switchover_hubs"],
            operation=_task_vars_with_operation_identity()["acm_switchover_operation"],
        ),
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original))
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity(mode="validate"))

    assert result["changed"] is False
    assert result["skipped_phase"] is False
    assert result["checkpoint"]["completed_phases"] == ["preflight"]
    assert json.loads(checkpoint_file.read_text()) == original


def test_action_module_rejects_identity_mismatch_without_explicit_reset(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": build_operation_identity(
                    hubs={
                        "primary": {
                            "context": "primary-hub",
                            "kubeconfig": "./kubeconfigs/primary",
                        },
                        "secondary": {
                            "context": "other-secondary",
                            "kubeconfig": "./kubeconfigs/secondary",
                        },
                    },
                    operation=_task_vars_with_operation_identity()["acm_switchover_operation"],
                ),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["failed"] is True
    assert "operation identity" in result["msg"].lower()
    assert "reset" in result["msg"].lower()


def test_action_module_rejects_same_context_with_different_cluster_uid(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    current_vars = _task_vars_with_operation_identity()
    checkpoint_hubs = {
        "primary": {"context": "primary-hub", "kubeconfig": "./kubeconfigs/primary"},
        "secondary": {
            "context": "secondary-hub",
            "kubeconfig": "./kubeconfigs/secondary",
        },
    }
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": build_operation_identity(
                    hubs=checkpoint_hubs,
                    operation=current_vars["acm_switchover_operation"],
                    hub_identities={
                        "primary": {"cluster_uid": "uid-retargeted"},
                        "secondary": {"cluster_uid": "uid-secondary"},
                    },
                ),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=current_vars)

    assert result["failed"] is True
    assert "operation identity" in result["msg"].lower()


def test_action_module_rejects_identity_mismatch_on_pass_without_explicit_reset(
    tmp_path,
):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "operation_identity": build_operation_identity(
            hubs={
                "primary": {
                    "context": "primary-hub",
                    "kubeconfig": "./kubeconfigs/primary",
                },
                "secondary": {
                    "context": "other-secondary",
                    "kubeconfig": "./kubeconfigs/secondary",
                },
            },
            operation=_task_vars_with_operation_identity()["acm_switchover_operation"],
        ),
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original))
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["failed"] is True
    assert "operation identity" in result["msg"].lower()
    assert json.loads(checkpoint_file.read_text()) == original


def test_action_module_rejects_unsafe_legacy_checkpoint_without_explicit_reset(
    tmp_path,
):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "phase": "activation",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["failed"] is True
    assert "schema 1.0" in result["msg"]
    assert "reset" in result["msg"].lower()


def test_action_module_rejects_unsafe_legacy_checkpoint_on_fail_without_reset(
    tmp_path,
):
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "1.0",
        "phase": "activation",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original))
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "fail",
            "error": "should not persist",
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["failed"] is True
    assert "schema 1.0" in result["msg"]
    assert json.loads(checkpoint_file.read_text()) == original


def test_action_module_reset_from_primary_prep_prunes_downstream_phases(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "finalization",
                "completed_phases": [
                    "preflight",
                    "primary_prep",
                    "activation",
                    "post_activation",
                    "finalization",
                ],
                "operational_data": {},
                "operation_identity": build_operation_identity(
                    hubs=_task_vars_with_operation_identity()["acm_switchover_hubs"],
                    operation=_task_vars_with_operation_identity()["acm_switchover_operation"],
                ),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "primary_prep",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
                "reset_from": "primary_prep",
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result.get("failed") is not True
    assert result["checkpoint"]["completed_phases"] == ["preflight"]
    assert result["skipped_phase"] is False
    assert json.loads(checkpoint_file.read_text())["completed_phases"] == ["preflight"]


def test_action_module_reset_status_with_reset_from_prunes_downstream_phases(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "finalization",
                "completed_phases": [
                    "preflight",
                    "primary_prep",
                    "activation",
                    "post_activation",
                    "finalization",
                ],
                "operational_data": {},
                "operation_identity": build_operation_identity(
                    hubs=_task_vars_with_operation_identity()["acm_switchover_hubs"],
                    operation=_task_vars_with_operation_identity()["acm_switchover_operation"],
                ),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "primary_prep",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
                "reset_from": "primary_prep",
            },
            "status": "reset",
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["checkpoint"]["completed_phases"] == ["preflight"]
    assert result["checkpoint"]["phase_status"] == "reset"
    assert json.loads(checkpoint_file.read_text())["completed_phases"] == ["preflight"]


def test_action_module_quarantines_corrupt_checkpoint_json(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text('{"schema_version": "2.0", bad json')
    fixed_now = datetime(2026, 4, 16, 12, 30, 45, tzinfo=timezone.utc)
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return fixed_now

    with patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.datetime",
        FixedDateTime,
    ):
        result = action.run(task_vars=_task_vars_with_operation_identity())

    quarantined_path = f"{checkpoint_file}.corrupt-{fixed_now.strftime('%Y%m%dT%H%M%SZ')}"
    assert result["failed"] is True
    assert "corrupted" in result["msg"].lower()
    assert "quarantined" in result["msg"].lower()
    assert not checkpoint_file.exists()
    assert os.path.exists(quarantined_path)


def test_action_module_operation_identity_includes_collection_version_when_available(
    tmp_path,
):
    checkpoint_file = tmp_path / "checkpoint.json"
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
                "reset": True,
            },
            "status": "enter",
        }
    )
    task_vars = _task_vars_with_operation_identity(collection_version="1.2.3")

    result = action.run(task_vars=task_vars)

    assert result["checkpoint"]["operation_identity"] == build_operation_identity(
        hubs=task_vars["acm_switchover_hubs"],
        operation=task_vars["acm_switchover_operation"],
        collection_version="1.2.3",
    )


def test_action_module_rejects_unsafe_checkpoint_path_before_file_access():
    """checkpoint_phase must validate checkpoint.path before any filesystem call."""
    from unittest.mock import MagicMock

    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": "preflight",
        "checkpoint": {
            "enabled": True,
            "backend": "file",
            "path": "/etc/passwd",
            "reset": True,
        },
        "status": "enter",
    }

    action = ActionModule(
        task=task,
        connection=MagicMock(),
        play_context=MagicMock(),
        loader=MagicMock(),
        templar=MagicMock(),
        shared_loader_obj=MagicMock(),
    )
    with patch.object(
        ActionModule,
        "_load_checkpoint",
        side_effect=AssertionError("_load_checkpoint should not be called"),
    ) as load_checkpoint, patch.object(
        ActionModule,
        "_save_checkpoint",
        side_effect=AssertionError("_save_checkpoint should not be called"),
    ) as save_checkpoint:
        result = action.run(task_vars=_task_vars_for_mode("execute"))

    assert result["failed"] is True
    assert "outside allowed directories" in result["msg"]
    load_checkpoint.assert_not_called()
    save_checkpoint.assert_not_called()


def test_load_checkpoint_reads_with_utf8_encoding():
    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    action = ActionModule.__new__(ActionModule)

    with patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.path.exists",
        return_value=True,
    ), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.open",
        mock_open(read_data='{"schema_version": "1.0"}'),
        create=True,
    ) as mocked_open:
        result = action._load_checkpoint("/tmp/checkpoint.json")

    assert result["schema_version"] == "1.0"
    mocked_open.assert_called_once_with("/tmp/checkpoint.json", encoding="utf-8")


def test_save_checkpoint_writes_with_utf8_encoding():
    from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
        ActionModule,
    )

    action = ActionModule.__new__(ActionModule)

    with patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.makedirs"
    ) as makedirs, patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.replace"
    ) as mocked_replace, patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.open",
        return_value=77,
    ), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.close"
    ), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.fsync"
    ), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.open",
        mock_open(),
        create=True,
    ) as mocked_open:
        result = action._save_checkpoint("/tmp/state/checkpoint.json", {"schema_version": "1.0"})

    assert result is None
    makedirs.assert_called_once_with("/tmp/state", exist_ok=True)
    temp_path = mocked_open.call_args.args[0]
    assert temp_path != "/tmp/state/checkpoint.json"
    assert os.path.dirname(temp_path) == "/tmp/state"
    mocked_open.assert_called_once_with(temp_path, "w", encoding="utf-8")
    mocked_replace.assert_called_once_with(temp_path, "/tmp/state/checkpoint.json")


def test_save_checkpoint_fsyncs_file_before_replace_and_directory_after_replace():
    action = ActionModule.__new__(ActionModule)
    events: list[tuple[Any, ...]] = []

    with patch("ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.makedirs"), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.replace"
    ) as mocked_replace, patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.open",
        return_value=77,
    ) as mocked_os_open, patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.close"
    ) as mocked_close, patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.fsync"
    ) as mocked_fsync, patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.open",
        mock_open(),
        create=True,
    ) as mocked_open:
        mocked_fsync.side_effect = lambda fd: events.append(("fsync", fd))
        mocked_replace.side_effect = lambda src, dst: events.append(("replace", src, dst))
        result = action._save_checkpoint("/tmp/state/checkpoint.json", {"schema_version": "2.0"})

    assert result is None
    temp_path = mocked_open.call_args.args[0]
    temp_fileno = mocked_open().fileno.return_value
    mocked_replace.assert_called_once_with(temp_path, "/tmp/state/checkpoint.json")
    mocked_os_open.assert_called_once_with("/tmp/state", os.O_RDONLY)
    mocked_fsync.assert_any_call(temp_fileno)
    mocked_fsync.assert_any_call(77)
    assert mocked_fsync.call_args_list.index(call(temp_fileno)) < mocked_fsync.call_args_list.index(call(77))
    file_fsync_index = events.index(("fsync", temp_fileno))
    replace_index = events.index(("replace", temp_path, "/tmp/state/checkpoint.json"))
    dir_fsync_index = events.index(("fsync", 77))
    assert file_fsync_index < replace_index < dir_fsync_index
    mocked_close.assert_called_once_with(77)


def test_save_checkpoint_ignores_unsupported_directory_fsync():
    action = ActionModule.__new__(ActionModule)

    with patch("ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.replace"), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.open",
        return_value=77,
    ), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.close"
    ) as mocked_close, patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.os.fsync",
        side_effect=[None, OSError("directory fsync unsupported")],
    ), patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.open",
        mock_open(),
        create=True,
    ):
        result = action._save_checkpoint("/tmp/state/checkpoint.json", {"schema_version": "2.0"})

    assert result is None
    mocked_close.assert_called_once_with(77)


def test_build_report_ref_accepts_custom_kind():
    ref = build_report_ref(path="/reports/out.yaml", phase="preflight", kind="yaml-report")
    assert ref["kind"] == "yaml-report"


def test_reset_from_does_not_reprune_phases_completed_in_current_run(tmp_path):
    """reset_from must only prune once; phases completed after the initial reset must not be re-pruned.

    Regression test for: reset_from fires on every 'enter', not just the first time.
    """
    checkpoint_file = tmp_path / "checkpoint.json"
    op_identity = build_operation_identity(hubs={}, operation={})
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "completed_phases": ["preflight", "primary_prep"],
                "operational_data": {},
                "operation_identity": op_identity,
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    def _make_action(phase, status):
        task = MagicMock()
        task.async_val = 0
        task.args = {
            "phase": phase,
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
                "reset_from": "primary_prep",
            },
            "status": status,
        }
        return ActionModule(
            task=task,
            connection=MagicMock(),
            play_context=MagicMock(),
            loader=MagicMock(),
            templar=MagicMock(),
            shared_loader_obj=MagicMock(),
        )

    task_vars = _task_vars_for_mode("execute")

    # First enter: reset_from fires because primary_prep is still in completed_phases.
    result = _make_action("activation", "enter").run(task_vars=task_vars)
    assert "primary_prep" not in result["checkpoint"]["completed_phases"]

    # Activation completes successfully in this run.
    result = _make_action("activation", "pass").run(task_vars=task_vars)
    assert "activation" in result["checkpoint"]["completed_phases"]

    # Second enter: reset_from must NOT fire again (primary_prep is gone; nothing left to prune).
    result = _make_action("post_activation", "enter").run(task_vars=task_vars)
    assert (
        "activation" in result["checkpoint"]["completed_phases"]
    ), "activation must not be re-pruned by reset_from on subsequent enter calls"
