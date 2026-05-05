"""Scenario tests verifying checkpoint resume behavior through operator YAML surfaces."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
    ActionModule,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_CHECKPOINT_SURFACE_FILES = [
    _REPO_ROOT
    / "ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml",
    _REPO_ROOT
    / "ansible_collections/tomazb/acm_switchover/roles/primary_prep/defaults/main.yml",
    _REPO_ROOT
    / "ansible_collections/tomazb/acm_switchover/roles/activation/defaults/main.yml",
    _REPO_ROOT
    / "ansible_collections/tomazb/acm_switchover/roles/post_activation/defaults/main.yml",
    _REPO_ROOT
    / "ansible_collections/tomazb/acm_switchover/roles/finalization/defaults/main.yml",
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml",
]
_SWITCHOVER_PLAYBOOK = (
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml"
)


def _make_checkpoint_action(*, phase: str, checkpoint: dict, status: str = "enter"):
    task = MagicMock()
    task.async_val = 0
    task.args = {
        "phase": phase,
        "checkpoint": checkpoint,
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


def _task_vars_from_fixture(payload: dict, *, mode: str | None = None) -> dict:
    task_vars = {
        "acm_switchover_execution": {
            "mode": mode or payload["acm_switchover_execution"]["mode"]
        },
        "acm_switchover_hubs": payload["acm_switchover_hubs"],
        "acm_switchover_operation": payload["acm_switchover_operation"],
    }
    return task_vars


def _load_vars_file(command: list[str]) -> dict:
    vars_arg = command[command.index("-e") + 1]
    assert vars_arg.startswith("@"), "scenario harness should pass vars file via -e @<path>"
    vars_path = Path(vars_arg[1:])
    return yaml.safe_load(vars_path.read_text(encoding="utf-8")) or {}


def test_checkpoint_operator_surface_exposes_reset_from_and_rescue_pruning():
    for surface_path in _CHECKPOINT_SURFACE_FILES:
        payload = yaml.safe_load(surface_path.read_text()) or {}
        checkpoint = payload["acm_switchover_execution"]["checkpoint"]
        assert checkpoint.get("reset_from") == "", (
            f"{surface_path.relative_to(_REPO_ROOT)} should expose "
            "acm_switchover_execution.checkpoint.reset_from"
        )

    playbook = _SWITCHOVER_PLAYBOOK.read_text(encoding="utf-8")
    assert re.search(
        r"checkpoint:\s*\"\{\{\s*acm_switchover_execution\.checkpoint\s*\|\s*combine\(\{'reset_from': 'primary_prep'\}\)\s*\}\}\"",
        playbook,
    ), "switchover rescue should reset from primary_prep through checkpoint config"


def test_reset_from_primary_prep_prunes_downstream_phases_from_fixture_yaml(
    monkeypatch,
    run_checkpoint_fixture,
):
    expected_phases = [
        "preflight",
        "primary_prep",
        "activation",
        "post_activation",
        "finalization",
    ]

    def fake_run(command, **kwargs):
        payload = _load_vars_file(command)
        checkpoint = payload["acm_switchover_execution"]["checkpoint"]
        assert checkpoint["reset_from"] == "primary_prep"

        preflight_result = _make_checkpoint_action(
            phase="preflight",
            checkpoint=checkpoint,
        ).run(task_vars=_task_vars_from_fixture(payload))
        primary_prep_result = _make_checkpoint_action(
            phase="primary_prep",
            checkpoint=checkpoint,
        ).run(task_vars=_task_vars_from_fixture(payload))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(
                {
                    "preflight": preflight_result,
                    "primary_prep": primary_prep_result,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        pre_completed_phases=expected_phases,
    )

    assert completed.returncode == 0, completed.stderr
    results = json.loads(completed.stdout)
    assert results["preflight"]["skipped_phase"] is True
    assert results["preflight"]["checkpoint"]["completed_phases"] == ["preflight"]
    assert results["primary_prep"]["skipped_phase"] is False
    assert results["primary_prep"]["checkpoint"]["completed_phases"] == ["preflight"]
    assert checkpoint["completed_phases"] == expected_phases


def test_validate_mode_with_checkpoint_enabled_does_not_create_or_mutate_checkpoint_path(
    monkeypatch,
    run_checkpoint_fixture,
):
    seeded_phases = ["preflight", "primary_prep"]

    def fake_run(command, **kwargs):
        payload = _load_vars_file(command)
        checkpoint = payload["acm_switchover_execution"]["checkpoint"]
        result = _make_checkpoint_action(
            phase="preflight",
            checkpoint=checkpoint,
        ).run(task_vars=_task_vars_from_fixture(payload, mode="validate"))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(result),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        pre_completed_phases=seeded_phases,
        vars_overrides={"acm_switchover_execution": {"mode": "validate"}},
        checkpoint_name="checkpoint-existing.json",
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["changed"] is False
    assert result["skipped_phase"] is True
    assert checkpoint["completed_phases"] == seeded_phases

    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        vars_overrides={"acm_switchover_execution": {"mode": "validate"}},
        checkpoint_name="checkpoint-missing.json",
    )
    assert completed.returncode == 0, completed.stderr
    assert checkpoint == {}, "validate mode should not create a checkpoint file"
