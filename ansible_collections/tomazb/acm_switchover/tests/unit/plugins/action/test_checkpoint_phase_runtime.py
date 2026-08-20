# SPDX-License-Identifier: MIT
"""Tests for checkpoint_phase and artifact runtime helpers."""

import json
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

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


def _canonical_normal_operation_identity():
    task_vars = _task_vars_with_operation_identity(collection_version="9.8.7")
    return build_operation_identity(
        hubs=task_vars["acm_switchover_hubs"],
        operation=task_vars["acm_switchover_operation"],
        collection_version=task_vars["acm_switchover_collection_version"],
        hub_identities=task_vars["acm_switchover_hub_identities"],
    )


def _namespace_result(uid):
    return {
        "changed": False,
        "resources": [
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "kube-system", "uid": uid},
            }
        ],
    }


def _identity_barrier_args(tmp_path, *, mode="execute", enabled=True, restore_only=False):
    hubs = {
        "secondary": {
            "context": "secondary-hub",
            "kubeconfig": "./kubeconfigs/secondary",
            "cluster_uid": "INJECTED-HUB-SECONDARY",
            "unrelated": "INJECTED-ARBITRARY-SECONDARY",
        }
    }
    if not restore_only:
        hubs["primary"] = {
            "context": "primary-hub",
            "kubeconfig": "./kubeconfigs/primary",
            "cluster_uid": "INJECTED-HUB-PRIMARY",
            "unrelated": "INJECTED-ARBITRARY-PRIMARY",
        }
    return {
        "identity_barrier": True,
        "phase": "preflight",
        "status": "enter",
        "checkpoint": {
            "enabled": enabled,
            "backend": "file",
            "path": str(tmp_path / "checkpoint.json"),
        },
        "hubs": hubs,
        "operation": {
            "method": "full" if restore_only else "passive",
            "activation_method": "patch",
            "restore_only": restore_only,
            "old_hub_action": "none" if restore_only else "secondary",
        },
        "execution": {"mode": mode},
        "test_overrides": {},
        "collection_version": "9.8.7",
    }


def _run_barrier_with_live_uids(action, task_vars, primary_uid="LIVE-PRIMARY", secondary_uid="LIVE-SECONDARY"):
    action._execute_module = MagicMock(side_effect=[_namespace_result(primary_uid), _namespace_result(secondary_uid)])
    return action.run(task_vars=task_vars)


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
                "operation_identity": _canonical_normal_operation_identity(),
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
                "operation_identity": _canonical_normal_operation_identity(),
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
                "operation_identity": _canonical_normal_operation_identity(),
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
                "operation_identity": _canonical_normal_operation_identity(),
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
                "operation_identity": _canonical_normal_operation_identity(),
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
                "operation_identity": _canonical_normal_operation_identity(),
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
        "operation_identity": _canonical_normal_operation_identity(),
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

    action._play_context.check_mode = True
    result = action.run(task_vars={**_task_vars_for_mode("execute"), "ansible_check_mode": False})

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
        "operation_identity": _canonical_normal_operation_identity(),
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
        "operation_identity": _canonical_normal_operation_identity(),
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

    action._play_context.check_mode = True
    result = action.run(task_vars={**_task_vars_for_mode("dry_run"), "ansible_check_mode": False})

    assert result["changed"] is False
    assert result["check_mode"] is True
    assert result["dry_run"] is True
    assert checkpoint_file.read_bytes() == original_bytes


def test_identity_barrier_check_mode_does_not_create_missing_checkpoint(tmp_path):
    checkpoint_file = tmp_path / "missing-checkpoint.json"
    args = _identity_barrier_args(tmp_path)
    args["checkpoint"]["path"] = str(checkpoint_file)
    action = _make_checkpoint_action(args)
    action._execute_module = MagicMock(
        side_effect=[
            _namespace_result("LIVE-PRIMARY"),
            _namespace_result("LIVE-SECONDARY"),
        ]
    )

    action._play_context.check_mode = True
    result = action.run(task_vars={**_task_vars_for_mode("execute"), "ansible_check_mode": False})

    assert result["changed"] is False
    assert result["checkpoint"]["operation_identity"] is None
    assert result["hub_identities"]["primary"]["cluster_uid"] == "LIVE-PRIMARY"
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
    args = _identity_barrier_args(tmp_path)
    action = _make_checkpoint_action(args)

    result = _run_barrier_with_live_uids(action, _task_vars_with_operation_identity())

    assert result["checkpoint"]["operation_identity"] == build_operation_identity(
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
        operation=args["operation"],
        collection_version="9.8.7",
        hub_identities={
            "primary": {"cluster_uid": "LIVE-PRIMARY"},
            "secondary": {"cluster_uid": "LIVE-SECONDARY"},
        },
    )


def test_action_module_enter_rejects_missing_identity_with_completed_phases(
    tmp_path,
):
    """Schema 2.0 checkpoint with completed phases but no operation_identity must fail closed."""
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

    assert result["failed"] is True
    assert "no operation identity" in result["msg"].lower()
    assert "checkpoint.reset" in result["msg"]


def test_action_module_enter_backfills_identity_for_fresh_checkpoint(
    tmp_path,
):
    """The initial barrier may safely backfill a fresh schema 2.0 checkpoint."""
    import json

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": [],
                "operational_data": {},
                "operation_identity": None,
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    args = _identity_barrier_args(tmp_path)
    args["checkpoint"]["path"] = str(checkpoint_file)
    action = _make_checkpoint_action(args)

    result = _run_barrier_with_live_uids(action, _task_vars_with_operation_identity())

    assert result.get("failed") is not True
    assert result["checkpoint"]["operation_identity"] == build_operation_identity(
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
        operation=args["operation"],
        collection_version="9.8.7",
        hub_identities={
            "primary": {"cluster_uid": "LIVE-PRIMARY"},
            "secondary": {"cluster_uid": "LIVE-SECONDARY"},
        },
    )


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


def test_action_module_enter_persists_resume_start_phase_when_resuming(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": _canonical_normal_operation_identity(),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    action = _make_checkpoint_action(
        {
            "phase": "primary_prep",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))

    assert result["skipped_phase"] is False
    assert result["checkpoint"]["operational_data"]["resume_summary"] == {
        "resume_start_phase": "primary_prep",
    }
    saved = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert saved["operational_data"]["resume_summary"] == {
        "resume_start_phase": "primary_prep",
    }


def test_action_module_enter_recovers_from_non_mapping_operational_data(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": "bad-data",
                "operation_identity": _canonical_normal_operation_identity(),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    action = _make_checkpoint_action(
        {
            "phase": "primary_prep",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))

    assert result["skipped_phase"] is False
    assert result["checkpoint"]["operational_data"]["resume_summary"] == {
        "resume_start_phase": "primary_prep",
    }


def test_action_module_dry_run_pass_does_not_mutate_checkpoint_file(tmp_path):
    import json

    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operational_data": {"argocd_run_id": "run-1"},
        "operation_identity": _canonical_normal_operation_identity(),
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
        "operation_identity": _canonical_normal_operation_identity(),
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


def test_action_module_later_enter_carries_context_identity_from_checkpoint(tmp_path):
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
                    hub_identities={
                        "primary": {"cluster_uid": "uid-primary"},
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

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result.get("failed") is not True
    assert result["checkpoint"]["operation_identity"]["secondary_context"] == "other-secondary"


def test_action_module_later_enter_carries_cluster_uid_from_checkpoint(tmp_path):
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

    assert result.get("failed") is not True
    assert result["checkpoint"]["operation_identity"]["primary_cluster_uid"] == "uid-retargeted"


def test_action_module_later_pass_preserves_checkpoint_identity_without_task_var_revalidation(
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
            hub_identities={
                "primary": {"cluster_uid": "uid-primary"},
                "secondary": {"cluster_uid": "uid-secondary"},
            },
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

    assert result.get("failed") is not True
    assert result["checkpoint"]["operation_identity"] == original["operation_identity"]
    assert json.loads(checkpoint_file.read_text())["operation_identity"] == original["operation_identity"]


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


def test_action_module_rejects_checkpoint_path_relative_symlink_escape(tmp_path, monkeypatch):
    """Relative checkpoint paths must not escape the artifact tree through symlinked parents."""
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    os.symlink(outside, workspace / "escape")
    monkeypatch.chdir(workspace)

    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": "escape/checkpoint.json",
            },
            "status": "enter",
        }
    )

    result = action.run(task_vars=_task_vars_for_mode("execute"))

    assert result["failed"] is True
    assert "symlink" in result["msg"]


def test_action_module_returns_actionable_error_for_unwritable_checkpoint_path(
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

    with patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.open",
        side_effect=PermissionError("Permission denied"),
        create=True,
    ):
        result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result["failed"] is True
    assert "cannot write checkpoint file" in result["msg"].lower()
    assert str(checkpoint_file) in result["msg"]
    assert "permission denied" in result["msg"].lower()


def test_action_module_enter_skips_already_completed_phase_without_changes(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "activation",
        "completed_phases": ["preflight", "activation"],
        "operational_data": {"existing": "keep"},
        "operation_identity": build_operation_identity(
            hubs=task_vars["acm_switchover_hubs"],
            operation=task_vars["acm_switchover_operation"],
        ),
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
            "status": "enter",
        }
    )

    result = action.run(task_vars=task_vars)

    assert result["changed"] is False
    assert result["skipped_phase"] is True
    assert result["checkpoint"]["completed_phases"] == ["preflight", "activation"]
    assert checkpoint_file.read_bytes() == original_bytes


def test_action_module_enter_normalizes_legacy_kubeconfig_identity_fields(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    expected_identity = build_operation_identity(
        hubs=task_vars["acm_switchover_hubs"],
        operation=task_vars["acm_switchover_operation"],
    )
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": {
                    **expected_identity,
                    "primary_kubeconfig": "./legacy/primary",
                    "secondary_kubeconfig": "./legacy/secondary",
                },
                "errors": [],
                "report_refs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
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

    fixed_now = datetime(2026, 5, 30, 13, 20, 0, tzinfo=timezone.utc)

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return fixed_now

    with patch(
        "ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase.datetime",
        FixedDateTime,
    ):
        result = action.run(task_vars=task_vars)

    assert result.get("failed") is not True
    assert result["checkpoint"]["operation_identity"] == expected_identity
    saved = json.loads(checkpoint_file.read_text())
    assert saved["operation_identity"] == expected_identity
    assert saved["updated_at"] == fixed_now.isoformat()
    assert "primary_kubeconfig" not in saved["operation_identity"]
    assert "secondary_kubeconfig" not in saved["operation_identity"]


def test_action_module_pass_with_existing_report_ref_is_noop(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    report_ref = {
        "phase": "preflight",
        "path": "/tmp/preflight-report.json",
        "kind": "json-report",
    }
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "preflight",
        "phase_status": "pass",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "operation_identity": build_operation_identity(
            hubs=task_vars["acm_switchover_hubs"],
            operation=task_vars["acm_switchover_operation"],
        ),
        "errors": [],
        "report_refs": [report_ref],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original, indent=2))
    original_bytes = checkpoint_file.read_bytes()
    action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
            "report_ref": "/tmp/preflight-report.json",
        }
    )

    result = action.run(task_vars=task_vars)

    assert result["changed"] is False
    assert result["checkpoint"]["report_refs"] == [report_ref]
    assert checkpoint_file.read_bytes() == original_bytes


def test_action_module_reset_from_prunes_target_phase_and_downstream_phase(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "activation",
                "completed_phases": ["preflight", "primary_prep", "activation"],
                "operational_data": {},
                "operation_identity": build_operation_identity(
                    hubs=task_vars["acm_switchover_hubs"],
                    operation=task_vars["acm_switchover_operation"],
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

    result = action.run(task_vars=task_vars)

    assert result["checkpoint"]["completed_phases"] == ["preflight"]
    assert "primary_prep" not in result["checkpoint"]["completed_phases"]
    assert "activation" not in result["checkpoint"]["completed_phases"]
    saved = json.loads(checkpoint_file.read_text())
    assert saved["completed_phases"] == ["preflight"]


def test_action_module_check_mode_enter_leaves_checkpoint_file_unchanged(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    checkpoint_file = tmp_path / "checkpoint.json"
    original = {
        "schema_version": "2.0",
        "phase": "activation",
        "completed_phases": ["preflight", "primary_prep", "activation"],
        "operational_data": {"existing": "keep"},
        "operation_identity": build_operation_identity(
            hubs=task_vars["acm_switchover_hubs"],
            operation=task_vars["acm_switchover_operation"],
        ),
        "errors": [],
        "report_refs": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_file.write_text(json.dumps(original, indent=2))
    original_bytes = checkpoint_file.read_bytes()
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

    action._play_context.check_mode = True
    result = action.run(task_vars={**task_vars, "ansible_check_mode": False})

    assert result["changed"] is False
    assert checkpoint_file.read_bytes() == original_bytes


def test_action_module_pass_does_not_duplicate_completed_phase_entries(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": build_operation_identity(
                    hubs=task_vars["acm_switchover_hubs"],
                    operation=task_vars["acm_switchover_operation"],
                ),
                "errors": [],
                "report_refs": [],
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    first_result = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
        }
    ).run(task_vars=task_vars)
    second_result = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {
                "enabled": True,
                "backend": "file",
                "path": str(checkpoint_file),
            },
            "status": "pass",
        }
    ).run(task_vars=task_vars)

    assert first_result["checkpoint"]["completed_phases"] == ["preflight", "activation"]
    assert second_result["checkpoint"]["completed_phases"].count("activation") == 1
    saved = json.loads(checkpoint_file.read_text())
    assert saved["completed_phases"].count("activation") == 1


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
    op_identity = _canonical_normal_operation_identity()
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


def _write_resumable_checkpoint(tmp_path, task_vars, completed=("preflight",)):
    """Persist a schema-2.0 checkpoint with completed phases for resume tests."""
    identity = build_operation_identity(
        hubs=task_vars.get("acm_switchover_hubs") or {},
        operation=task_vars.get("acm_switchover_operation") or {},
        collection_version=task_vars.get("acm_switchover_collection_version"),
        hub_identities=task_vars.get("acm_switchover_hub_identities") or {},
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    record = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": list(completed),
        "operational_data": {"argocd_run_id": "run-7"},
        "operation_identity": identity,
        "errors": [],
        "report_refs": [],
        "created_at": "2026-08-06T00:00:00+00:00",
        "updated_at": "2026-08-06T00:00:00+00:00",
    }
    checkpoint_path.write_text(json.dumps(record))
    return str(checkpoint_path)


def test_enter_returns_named_facts(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    path = _write_resumable_checkpoint(tmp_path, task_vars)
    action = _make_checkpoint_action(
        {
            "phase": "primary_prep",
            "status": "enter",
            "checkpoint": {"enabled": True, "path": path},
        }
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert result["facts"]["argocd_run_id"] == "run-7"
    assert result["facts"]["auto_import_strategy_changed"] is False
    assert result["facts"]["resume_start_phase"] == "primary_prep"


def test_resumed_enter_replaces_resume_summary_and_flags_process(tmp_path):
    """First non-completed enter of a process replaces resume_summary wholesale."""
    task_vars = _task_vars_with_operation_identity()
    path = _write_resumable_checkpoint(tmp_path, task_vars)
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["operational_data"]["resume_summary"] = {
        "resume_start_phase": "preflight",
        "stale": "yes",
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)

    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "status": "enter",
            "checkpoint": {"enabled": True, "path": path},
        }
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert result["ansible_facts"] == {
        "_acm_switchover_resume_recorded": str(os.getpid())
    }, "sentinel must carry the controller PID so a stale cached fact cannot fence a later process"
    with open(path, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["operational_data"]["resume_summary"] == {"resume_start_phase": "activation"}


def test_same_process_later_enter_does_not_overwrite_resume_summary(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    task_vars["_acm_switchover_resume_recorded"] = str(os.getpid())
    path = _write_resumable_checkpoint(tmp_path, task_vars)
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["operational_data"]["resume_summary"] = {"resume_start_phase": "primary_prep"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)

    action = _make_checkpoint_action(
        {
            "phase": "post_activation",
            "status": "enter",
            "checkpoint": {"enabled": True, "path": path},
        }
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert "ansible_facts" not in result
    with open(path, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["operational_data"]["resume_summary"] == {"resume_start_phase": "primary_prep"}


def test_fresh_run_records_no_resume_summary(tmp_path):
    """Empty completed_phases = not a resume: no resume_summary, no process flag."""
    task_vars = _task_vars_with_operation_identity()
    action = _make_checkpoint_action(_identity_barrier_args(tmp_path))
    result = _run_barrier_with_live_uids(action, task_vars)
    assert result.get("failed") is not True
    assert "ansible_facts" not in result
    assert result["facts"]["resume_start_phase"] == ""


def test_stale_cached_sentinel_from_other_process_does_not_fence(tmp_path):
    """A sentinel persisted by fact caching from a previous ansible-playbook
    process carries that process's PID; the current process must still replace
    resume_summary (external review, PR #224)."""
    task_vars = _task_vars_with_operation_identity()
    task_vars["_acm_switchover_resume_recorded"] = "99999999-stale"
    path = _write_resumable_checkpoint(tmp_path, task_vars)
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "status": "enter",
            "checkpoint": {"enabled": True, "path": path},
        }
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert result["ansible_facts"] == {"_acm_switchover_resume_recorded": str(os.getpid())}
    with open(path, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["operational_data"]["resume_summary"] == {"resume_start_phase": "activation"}


@pytest.mark.parametrize(
    ("phase", "status"),
    [
        ("activation", "enter"),
        ("preflight", "pass"),
        ("primary_prep", "reset"),
    ],
)
def test_identity_barrier_rejects_non_literal_phase_or_status_before_discovery(tmp_path, phase, status):
    args = _identity_barrier_args(tmp_path)
    args.update({"phase": phase, "status": status})
    action = _make_checkpoint_action(args)
    action._execute_module = MagicMock()

    result = action.run(task_vars={"acm_switchover_execution": {"mode": "validate"}})

    assert result == {
        "failed": True,
        "msg": "identity_barrier requires phase=preflight and status=enter.",
    }
    action._execute_module.assert_not_called()


def test_identity_barrier_disabled_still_reads_distinct_live_uids_without_file_access(
    tmp_path,
):
    args = _identity_barrier_args(tmp_path, enabled=False)
    action = _make_checkpoint_action(args)
    task_vars = {"acm_switchover_execution": {"mode": "validate"}}
    with patch.object(action, "_load_checkpoint") as load_checkpoint, patch.object(
        action, "_save_checkpoint"
    ) as save_checkpoint:
        result = _run_barrier_with_live_uids(action, task_vars)

    assert result == {
        "changed": False,
        "skipped_phase": False,
        "facts": {},
        "hub_identities": {
            "primary": {"cluster_uid": "LIVE-PRIMARY"},
            "secondary": {"cluster_uid": "LIVE-SECONDARY"},
        },
    }
    assert action._execute_module.call_args_list == [
        call(
            module_name="kubernetes.core.k8s_info",
            module_args={
                "api_version": "v1",
                "kind": "Namespace",
                "name": "kube-system",
                "kubeconfig": "./kubeconfigs/primary",
                "context": "primary-hub",
            },
            task_vars=task_vars,
            tmp=None,
        ),
        call(
            module_name="kubernetes.core.k8s_info",
            module_args={
                "api_version": "v1",
                "kind": "Namespace",
                "name": "kube-system",
                "kubeconfig": "./kubeconfigs/secondary",
                "context": "secondary-hub",
            },
            task_vars=task_vars,
            tmp=None,
        ),
    ]
    load_checkpoint.assert_not_called()
    save_checkpoint.assert_not_called()


def test_identity_barrier_rejects_same_context_before_live_reads(tmp_path):
    args = _identity_barrier_args(tmp_path, enabled=False)
    args["hubs"]["secondary"]["context"] = "primary-hub"
    action = _make_checkpoint_action(args)
    action._execute_module = MagicMock()

    result = action.run(task_vars={})

    assert result == {
        "failed": True,
        "msg": "Primary and secondary Kubernetes context names must differ for a normal two-hub switchover.",
    }
    action._execute_module.assert_not_called()


def test_identity_barrier_rejects_equal_trimmed_live_uids(tmp_path):
    action = _make_checkpoint_action(_identity_barrier_args(tmp_path, enabled=False))
    injected = {
        "acm_switchover_hub_identities": {
            "primary": {"cluster_uid": "FAKE-DISTINCT-PRIMARY"},
            "secondary": {"cluster_uid": "FAKE-DISTINCT-SECONDARY"},
        },
        "_acm_switchover_verified_hub_identities": {
            "primary": {"cluster_uid": "PRIVATE-DISTINCT-PRIMARY"},
            "secondary": {"cluster_uid": "PRIVATE-DISTINCT-SECONDARY"},
        },
        "acm_switchover_distinct_hubs_verified": True,
    }

    result = _run_barrier_with_live_uids(action, injected, primary_uid=" LIVE-SAME ", secondary_uid="LIVE-SAME")

    assert result == {
        "failed": True,
        "msg": (
            "Primary and secondary hubs resolve to the same physical Kubernetes cluster. "
            "Refusing the normal two-hub switchover."
        ),
    }
    assert action._execute_module.call_count == 2


def test_restore_only_identity_barrier_reads_secondary_only(tmp_path):
    args = _identity_barrier_args(tmp_path, enabled=False, restore_only=True)
    action = _make_checkpoint_action(args)
    action._execute_module = MagicMock(return_value=_namespace_result(" RESTORE-SECONDARY "))
    task_vars = {"primary_failure_sentinel": "MUST-NOT-BE-READ"}

    result = action.run(task_vars=task_vars)

    assert result["hub_identities"] == {"secondary": {"cluster_uid": "RESTORE-SECONDARY"}}
    action._execute_module.assert_called_once_with(
        module_name="kubernetes.core.k8s_info",
        module_args={
            "api_version": "v1",
            "kind": "Namespace",
            "name": "kube-system",
            "kubeconfig": "./kubeconfigs/secondary",
            "context": "secondary-hub",
        },
        task_vars=task_vars,
        tmp=None,
    )


def test_restore_only_identity_barrier_rejects_stored_secondary_uid_drift(tmp_path):
    args = _identity_barrier_args(tmp_path, restore_only=True)
    args["hubs"]["primary"] = {
        "context": "PRIMARY-POISON-CONTEXT",
        "kubeconfig": "PRIMARY-POISON-KUBECONFIG",
    }
    stored_identity = build_operation_identity(
        hubs={"secondary": {"context": "secondary-hub"}},
        operation=args["operation"],
        collection_version="9.8.7",
        hub_identities={"secondary": {"cluster_uid": "STORED-SECONDARY"}},
    )
    (tmp_path / "checkpoint.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": stored_identity,
                "errors": [],
                "report_refs": [],
            }
        )
    )
    action = _make_checkpoint_action(args)
    action._execute_module = MagicMock(return_value=_namespace_result("LIVE-SECONDARY"))

    result = action.run(task_vars={"primary_failure_sentinel": "MUST-NOT-BE-READ"})

    assert result["failed"] is True
    assert "Checkpoint operation identity does not match the current execution." in result["msg"]
    action._execute_module.assert_called_once()
    module_args = action._execute_module.call_args.kwargs["module_args"]
    assert module_args["context"] == "secondary-hub"
    assert "PRIMARY-POISON" not in json.dumps(module_args)


def test_restore_only_identity_barrier_accepts_matching_stored_secondary_uid(tmp_path):
    args = _identity_barrier_args(tmp_path, restore_only=True)
    args["hubs"]["primary"] = {
        "context": "PRIMARY-POISON-CONTEXT",
        "kubeconfig": "PRIMARY-POISON-KUBECONFIG",
    }
    stored_identity = build_operation_identity(
        hubs={"secondary": {"context": "secondary-hub"}},
        operation=args["operation"],
        collection_version="9.8.7",
        hub_identities={"secondary": {"cluster_uid": "LIVE-SECONDARY"}},
    )
    (tmp_path / "checkpoint.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": stored_identity,
                "errors": [],
                "report_refs": [],
            }
        )
    )
    action = _make_checkpoint_action(args)
    action._execute_module = MagicMock(return_value=_namespace_result("LIVE-SECONDARY"))

    result = action.run(task_vars={"primary_failure_sentinel": "MUST-NOT-BE-READ"})

    assert result["skipped_phase"] is True
    assert result["facts"]["argocd_run_id"] == ""
    assert result["facts"]["expected_managed_cluster_count"] is None
    assert result["checkpoint"]["operation_identity"] == stored_identity
    action._execute_module.assert_called_once()
    module_args = action._execute_module.call_args.kwargs["module_args"]
    assert module_args["context"] == "secondary-hub"
    assert "PRIMARY-POISON" not in json.dumps(module_args)


MALFORMED_NAMESPACE_RESULTS = [
    None,
    {},
    {"failed": True},
    {"resources": []},
    {"resources": [{"metadata": {"uid": "one"}}, {"metadata": {"uid": "two"}}]},
    {"resources": [{}]},
    {"resources": [{"metadata": "not-a-mapping"}]},
    {"resources": [{"metadata": {}}]},
    {"resources": [{"metadata": {"uid": 123}}]},
    {"resources": [{"metadata": {"uid": ""}}]},
    {"resources": [{"metadata": {"uid": "   "}}]},
]


@pytest.mark.parametrize("role", ["primary", "secondary"])
@pytest.mark.parametrize("malformed_result", MALFORMED_NAMESPACE_RESULTS)
def test_identity_barrier_rejects_malformed_namespace_results_without_leaking(tmp_path, capsys, role, malformed_result):
    sentinels = [
        "API-BODY-SECRET-BD73",
        "API-PATH-SECRET-PT72",
        "CONTEXT-SECRET-CT71",
        "TOKEN-SECRET-TK70",
        "CREDENTIAL-SECRET-CR69",
        "RAW-ERROR-SECRET-ER68",
        "UID-SECRET-UI67",
    ]
    args = _identity_barrier_args(tmp_path, enabled=False)
    args["hubs"][role]["context"] = "CONTEXT-SECRET-CT71"
    args["hubs"][role]["kubeconfig"] = "CREDENTIAL-SECRET-CR69"
    if isinstance(malformed_result, dict):
        malformed_result = {
            **malformed_result,
            "msg": "API-BODY-SECRET-BD73 API-PATH-SECRET-PT72 TOKEN-SECRET-TK70",
            "exception": "RAW-ERROR-SECRET-ER68 UID-SECRET-UI67",
        }
    action = _make_checkpoint_action(args)
    if role == "primary":
        action._execute_module = MagicMock(return_value=malformed_result)
    else:
        action._execute_module = MagicMock(side_effect=[_namespace_result("LIVE-PRIMARY"), malformed_result])
    malicious_task_vars = {
        "acm_switchover_hub_identities": {
            "primary": {"cluster_uid": "PUBLIC-PRIMARY"},
            "secondary": {"cluster_uid": "PUBLIC-SECONDARY"},
        },
        "_checkpoint_enter": {"hub_identities": {role: {"cluster_uid": "UID-SECRET-UI67"}}},
    }

    result = action.run(task_vars=malicious_task_vars)

    expected_message = (
        f"Unable to verify the {role} hub physical identity from the live kube-system Namespace UID. "
        "Refusing the normal two-hub switchover."
    )
    assert result == {"failed": True, "msg": expected_message}
    captured = capsys.readouterr()
    combined_output = json.dumps(result, sort_keys=True) + captured.out + captured.err
    for sentinel in sentinels:
        assert sentinel not in combined_output


@pytest.mark.parametrize("mode", ["validate", "dry_run"])
def test_non_live_override_is_used_only_for_validate_and_dry_run(tmp_path, mode):
    args = _identity_barrier_args(tmp_path, mode=mode, enabled=True)
    args["test_overrides"] = {
        "non_live_hub_identities": {
            "primary": {"cluster_uid": "OVERRIDE-PRIMARY"},
            "secondary": {"cluster_uid": "OVERRIDE-SECONDARY"},
        }
    }
    action = _make_checkpoint_action(args)
    action._execute_module = MagicMock(side_effect=AssertionError("non-live override should avoid API reads"))

    result = action.run(task_vars={"acm_switchover_hub_identities": {"primary": {"cluster_uid": "PUBLIC"}}})

    assert result.get("failed") is not True
    assert result["hub_identities"] == {
        "primary": {"cluster_uid": "OVERRIDE-PRIMARY"},
        "secondary": {"cluster_uid": "OVERRIDE-SECONDARY"},
    }
    assert not (tmp_path / "checkpoint.json").exists()
    action._execute_module.assert_not_called()


@pytest.mark.parametrize("native_check", [False, True])
def test_execute_ignores_all_preseed_and_override_identity_channels(tmp_path, native_check):
    args = _identity_barrier_args(tmp_path, mode="execute", enabled=True)
    args["test_overrides"] = {
        "non_live_hub_identities": {
            "primary": {"cluster_uid": "OVERRIDE-PRIMARY"},
            "secondary": {"cluster_uid": "OVERRIDE-SECONDARY"},
        }
    }
    action = _make_checkpoint_action(args)
    action._play_context.check_mode = native_check
    injected = {
        "ansible_check_mode": not native_check,
        "acm_switchover_execution": {"mode": "validate"},
        "acm_switchover_hub_identities": {
            "primary": {"cluster_uid": "PUBLIC-PRIMARY"},
            "secondary": {"cluster_uid": "PUBLIC-SECONDARY"},
        },
        "_acm_switchover_verified_hub_identities": {
            "primary": {"cluster_uid": "PRIVATE-PRIMARY"},
            "secondary": {"cluster_uid": "PRIVATE-SECONDARY"},
        },
        "_checkpoint_enter": {"hub_identities": {"primary": {"cluster_uid": "REGISTERED"}}},
        "acm_input_validation": {"passed": True},
        "_acm_identity_barrier_result": {"passed": True},
        "acm_switchover_distinct_hubs_verified": True,
    }

    result = _run_barrier_with_live_uids(action, injected)

    assert result.get("failed") is not True
    assert result["hub_identities"] == {
        "primary": {"cluster_uid": "LIVE-PRIMARY"},
        "secondary": {"cluster_uid": "LIVE-SECONDARY"},
    }
    assert action._execute_module.call_count == 2
    assert (tmp_path / "checkpoint.json").exists() is (not native_check)


def test_validate_without_override_still_reads_fresh_namespace_uids(tmp_path):
    action = _make_checkpoint_action(_identity_barrier_args(tmp_path, mode="validate", enabled=False))

    result = _run_barrier_with_live_uids(
        action,
        {"acm_switchover_hub_identities": {"primary": {"cluster_uid": "PUBLIC-PRESEED"}}},
    )

    assert result.get("failed") is not True
    assert action._execute_module.call_count == 2


def test_identity_barrier_checkpoint_identity_uses_only_trusted_local_uids(tmp_path):
    args = _identity_barrier_args(tmp_path)
    action = _make_checkpoint_action(args)
    task_vars = {
        "acm_switchover_hubs": {
            "primary": {
                "context": "TASKVAR-CONTEXT",
                "cluster_uid": "TASKVAR-HUB-PRIMARY",
            },
            "secondary": {
                "context": "TASKVAR-CONTEXT",
                "cluster_uid": "TASKVAR-HUB-SECONDARY",
            },
        },
        "acm_switchover_hub_identities": {
            "primary": {"cluster_uid": "TASKVAR-PUBLIC-PRIMARY"},
            "secondary": {"cluster_uid": "TASKVAR-PUBLIC-SECONDARY"},
        },
    }

    result = _run_barrier_with_live_uids(action, task_vars)

    identity = result["checkpoint"]["operation_identity"]
    assert identity == {
        "primary_context": "primary-hub",
        "secondary_context": "secondary-hub",
        "primary_cluster_uid": "LIVE-PRIMARY",
        "secondary_cluster_uid": "LIVE-SECONDARY",
        "method": "passive",
        "activation_method": "patch",
        "restore_only": False,
        "old_hub_action": "secondary",
        "collection_version": "9.8.7",
    }
    serialized = json.dumps(identity)
    assert "INJECTED-HUB" not in serialized
    assert "INJECTED-ARBITRARY" not in serialized
    assert "TASKVAR" not in serialized


@pytest.mark.parametrize("drift_role", ["primary", "secondary"])
def test_identity_barrier_preserves_checkpoint_drift_validation_against_live_uids(tmp_path, drift_role):
    args = _identity_barrier_args(tmp_path)
    stored_uids = {"primary": "STORED-PRIMARY", "secondary": "STORED-SECONDARY"}
    stored_identity = build_operation_identity(
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
        operation=args["operation"],
        collection_version="9.8.7",
        hub_identities={role: {"cluster_uid": uid} for role, uid in stored_uids.items()},
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": stored_identity,
                "errors": [],
                "report_refs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    live_uids = dict(stored_uids)
    live_uids[drift_role] = f"LIVE-DRIFT-{drift_role.upper()}"
    action = _make_checkpoint_action(args)
    injected = _task_vars_with_operation_identity()
    injected["acm_switchover_hub_identities"] = {role: {"cluster_uid": uid} for role, uid in stored_uids.items()}
    injected["acm_switchover_hubs"][drift_role]["cluster_uid"] = stored_uids[drift_role]

    result = _run_barrier_with_live_uids(
        action,
        injected,
        primary_uid=live_uids["primary"],
        secondary_uid=live_uids["secondary"],
    )

    assert result["failed"] is True
    assert "Checkpoint operation identity does not match the current execution." in result["msg"]


def test_completed_preflight_still_rereads_live_identity_before_skip(tmp_path):
    args = _identity_barrier_args(tmp_path)
    trusted_identity = build_operation_identity(
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
        operation=args["operation"],
        collection_version="9.8.7",
        hub_identities={
            "primary": {"cluster_uid": "LIVE-PRIMARY"},
            "secondary": {"cluster_uid": "LIVE-SECONDARY"},
        },
    )
    (tmp_path / "checkpoint.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {"argocd_run_id": "run-1"},
                "operation_identity": trusted_identity,
                "errors": [],
                "report_refs": [],
            }
        )
    )
    action = _make_checkpoint_action(args)

    result = _run_barrier_with_live_uids(action, {})

    assert action._execute_module.call_count == 2
    assert result["skipped_phase"] is True
    assert result["facts"]["argocd_run_id"] == "run-1"


def test_ordinary_later_transition_carries_checkpoint_identity_instead_of_task_vars(
    tmp_path,
):
    stored_identity = build_operation_identity(
        hubs={
            "primary": {"context": "stored-primary"},
            "secondary": {"context": "stored-secondary"},
        },
        operation={"method": "passive"},
        hub_identities={
            "primary": {"cluster_uid": "STORED-PRIMARY"},
            "secondary": {"cluster_uid": "STORED-SECONDARY"},
        },
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": ["preflight"],
                "operational_data": {},
                "operation_identity": stored_identity,
                "errors": [],
                "report_refs": [],
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "status": "pass",
            "checkpoint": {"enabled": True, "path": str(checkpoint_path)},
        }
    )
    malicious_task_vars = _task_vars_with_operation_identity()
    malicious_task_vars["acm_switchover_hubs"]["primary"]["cluster_uid"] = "TASKVAR-PRIMARY"

    result = action.run(task_vars=malicious_task_vars)

    assert result.get("failed") is not True
    assert result["checkpoint"]["operation_identity"] == stored_identity


def test_ordinary_execute_transition_without_established_checkpoint_identity_fails_closed(
    tmp_path,
):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "preflight",
                "completed_phases": [],
                "operational_data": {},
                "operation_identity": None,
                "errors": [],
                "report_refs": [],
            }
        )
    )
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "status": "pass",
            "checkpoint": {"enabled": True, "path": str(checkpoint_path)},
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity())

    assert result == {
        "failed": True,
        "msg": "Checkpoint has no established operation identity; run the preflight identity barrier first.",
    }


def test_build_reset_from_checkpoint_still_replaces_identity_with_supplied_expected_identity():
    action = ActionModule.__new__(ActionModule)
    checkpoint = {
        "schema_version": "2.0",
        "completed_phases": ["preflight", "primary_prep", "activation"],
        "operational_data": {"keep": True},
        "operation_identity": {"primary_cluster_uid": "STORED"},
        "errors": [],
        "report_refs": [],
    }
    supplied_expected_identity = {"primary_cluster_uid": "SUPPLIED-EXPECTED"}

    result = action._build_reset_from_checkpoint(checkpoint, "primary_prep", supplied_expected_identity)

    assert result["operation_identity"] == supplied_expected_identity
    assert result["completed_phases"] == ["preflight"]


def test_initial_identity_barrier_reset_from_uses_trusted_expected_identity(tmp_path):
    args = _identity_barrier_args(tmp_path)
    args["checkpoint"]["reset_from"] = "preflight"
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "activation",
                "completed_phases": ["preflight", "primary_prep", "activation"],
                "operational_data": {},
                "operation_identity": {"primary_cluster_uid": "OLD"},
                "errors": [],
                "report_refs": [],
            }
        )
    )
    action = _make_checkpoint_action(args)

    result = _run_barrier_with_live_uids(action, _task_vars_with_operation_identity())

    assert result.get("failed") is not True
    assert result["checkpoint"]["operation_identity"]["primary_cluster_uid"] == "LIVE-PRIMARY"
    assert result["checkpoint"]["operation_identity"]["secondary_cluster_uid"] == "LIVE-SECONDARY"
    assert result["checkpoint"]["completed_phases"] == []


def test_native_check_barrier_then_preflight_pass_succeeds_without_checkpoint_file(tmp_path):
    barrier_args = _identity_barrier_args(tmp_path, mode="execute", enabled=True)
    barrier = _make_checkpoint_action(barrier_args)
    barrier._play_context.check_mode = True
    barrier._execute_module = MagicMock(
        side_effect=[_namespace_result("LIVE-PRIMARY"), _namespace_result("LIVE-SECONDARY")]
    )
    task_vars = _task_vars_with_operation_identity(mode="execute")

    barrier_result = barrier.run(task_vars=task_vars)

    assert barrier_result.get("failed") is not True
    assert barrier_result["changed"] is False
    assert barrier_result["checkpoint"]["operation_identity"] is None
    checkpoint_path = tmp_path / "checkpoint.json"
    assert not checkpoint_path.exists()

    pass_action = _make_checkpoint_action(
        {
            "phase": "preflight",
            "status": "pass",
            "checkpoint": {"enabled": True, "path": str(checkpoint_path)},
        }
    )
    pass_action._play_context.check_mode = True

    pass_result = pass_action.run(task_vars=task_vars)

    assert pass_result.get("failed") is not True
    assert pass_result["changed"] is False
    assert pass_result["check_mode"] is True
    assert pass_result["checkpoint"]["operation_identity"] is None
    assert not checkpoint_path.exists()


MALFORMED_PERSISTED_OPERATION_IDENTITIES = [
    pytest.param(None, id="none"),
    pytest.param("not-a-mapping", id="non-mapping"),
    pytest.param({}, id="empty-mapping"),
    pytest.param(
        {
            "primary_context": "primary-hub",
            "secondary_context": "secondary-hub",
            "primary_cluster_uid": "UID-PRIMARY",
            "secondary_cluster_uid": "UID-SECONDARY",
            "restore_only": False,
        },
        id="missing-canonical-fields",
    ),
    pytest.param(
        {**_canonical_normal_operation_identity(), "primary_cluster_uid": ""},
        id="missing-primary-uid",
    ),
    pytest.param(
        {**_canonical_normal_operation_identity(), "secondary_context": ""},
        id="missing-secondary-context",
    ),
]


@pytest.mark.parametrize("persisted_identity", MALFORMED_PERSISTED_OPERATION_IDENTITIES)
def test_ordinary_execute_transition_rejects_malformed_persisted_identity_without_writing(tmp_path, persisted_identity):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": ["preflight"],
        "operational_data": {},
        "operation_identity": persisted_identity,
        "errors": [],
        "report_refs": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
    original_bytes = checkpoint_path.read_bytes()
    action = _make_checkpoint_action(
        {
            "phase": "activation",
            "status": "pass",
            "checkpoint": {"enabled": True, "path": str(checkpoint_path)},
        }
    )

    result = action.run(task_vars=_task_vars_with_operation_identity(mode="execute"))

    assert result["failed"] is True
    assert "operation identity" in result["msg"].lower()
    assert checkpoint_path.read_bytes() == original_bytes
