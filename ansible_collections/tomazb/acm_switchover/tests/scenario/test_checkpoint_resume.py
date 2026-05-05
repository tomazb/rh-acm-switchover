"""Scenario tests verifying checkpoint resume behavior through operator YAML surfaces."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

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
    _REPO_ROOT
    / "ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml",
]
_SWITCHOVER_PLAYBOOK = (
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml"
)
_SEEDED_CHECKPOINT_TIMESTAMPS = {
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}


def _task_pattern(role: str, task_name: str, result: str) -> str:
    return rf"tomazb\.acm_switchover\.{role} : {re.escape(task_name)}.*\n.*{result}"


def _expected_checkpoint(phase: str, completed_phases: list[str]) -> dict:
    return {
        "schema_version": "1.0",
        "phase": phase,
        "completed_phases": completed_phases,
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        **_SEEDED_CHECKPOINT_TIMESTAMPS,
    }


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


def test_dry_run_reset_from_primary_prep_prunes_downstream_phases_via_playbook(
    run_checkpoint_fixture,
):
    expected_phases = [
        "preflight",
        "primary_prep",
        "activation",
        "post_activation",
        "finalization",
    ]

    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        pre_completed_phases=expected_phases,
    )

    assert completed.returncode == 0, completed.stderr
    assert checkpoint == _expected_checkpoint("finalization", expected_phases)
    stdout = completed.stdout

    assert re.search(
        _task_pattern("preflight", "Mark checkpoint phase completion", "skipping"),
        stdout,
    ), "preflight should stay skipped after reset_from prunes downstream phases"

    for resumed_phase in (
        "primary_prep",
        "activation",
        "post_activation",
        "finalization",
    ):
        assert re.search(
            _task_pattern(resumed_phase, "Mark checkpoint phase completion", "ok"),
            stdout,
        ), f"{resumed_phase} should rerun through the playbook path"
        assert not re.search(
            _task_pattern(
                resumed_phase, "Mark checkpoint phase completion", "skipping"
            ),
            stdout,
        ), f"{resumed_phase} should not remain skipped after reset_from"
        assert not re.search(
            _task_pattern(resumed_phase, "Mark checkpoint phase completion", "changed"),
            stdout,
        ), f"{resumed_phase} should not persist checkpoint completion during dry-run"


def test_validate_mode_with_checkpoint_enabled_does_not_create_or_mutate_checkpoint_path(
    run_checkpoint_fixture,
):
    seeded_phases = ["preflight", "primary_prep"]

    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        pre_completed_phases=seeded_phases,
        vars_overrides={"acm_switchover_execution": {"mode": "validate"}},
        checkpoint_name="checkpoint-existing.json",
    )

    assert completed.returncode == 0, completed.stderr
    assert "Stop after preflight when mode is validate" in completed.stdout
    assert checkpoint == _expected_checkpoint("primary_prep", seeded_phases)

    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        vars_overrides={"acm_switchover_execution": {"mode": "validate"}},
        checkpoint_name="checkpoint-missing.json",
    )
    assert completed.returncode == 0, completed.stderr
    assert checkpoint == {}, "validate mode should not create a checkpoint file"
