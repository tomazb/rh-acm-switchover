"""Scenario tests verifying checkpoint resume behavior through operator YAML surfaces."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    build_operation_identity,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_ANSIBLE_PY314_COMPAT_PATH = _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/tests/support/python314_ast_compat"
_CHECKPOINT_SURFACE_FILES = [
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml",
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/roles/primary_prep/defaults/main.yml",
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/roles/activation/defaults/main.yml",
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/roles/post_activation/defaults/main.yml",
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/roles/finalization/defaults/main.yml",
    _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml",
]
_SWITCHOVER_PLAYBOOK = _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml"
_SEEDED_CHECKPOINT_TIMESTAMPS = {
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}


def _task_pattern(role: str, task_name: str, result: str) -> str:
    return rf"tomazb\.acm_switchover\.{role} : {re.escape(task_name)}.*\n.*{result}"


def _expected_checkpoint(phase: str, completed_phases: list[str]) -> dict:
    # Intentionally seeds a schema 1.0 file to verify that dry_run/validate
    # mode never mutates or migrates a legacy checkpoint.
    return {
        "schema_version": "1.0",
        "phase": phase,
        "completed_phases": completed_phases,
        "operational_data": {},
        "errors": [],
        "report_refs": [],
        **_SEEDED_CHECKPOINT_TIMESTAMPS,
    }


def _ansible_env(tmp_path: Path) -> dict:
    local_tmp = tmp_path / "ansible-local"
    remote_tmp = tmp_path / "ansible-remote"
    local_tmp.mkdir(parents=True, exist_ok=True)
    remote_tmp.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "ANSIBLE_COLLECTIONS_PATH": f"{_REPO_ROOT}:{os.path.expanduser('~/.ansible/collections')}",
        "ANSIBLE_LOCAL_TEMP": str(local_tmp),
        "ANSIBLE_REMOTE_TMP": str(remote_tmp),
    }
    pythonpaths = [str(_ANSIBLE_PY314_COMPAT_PATH)]
    if os.environ.get("PYTHONPATH"):
        pythonpaths.append(os.environ["PYTHONPATH"])
    env["PYTHONPATH"] = ":".join(pythonpaths)
    return env


def _run_checkpoint_check_mode_playbook(tmp_path: Path, checkpoint_path: Path) -> subprocess.CompletedProcess[str]:
    playbook = tmp_path / "checkpoint-check-mode.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "vars": {
                        "acm_switchover_execution": {"mode": "execute"},
                        "acm_switchover_hubs": {},
                        "acm_switchover_operation": {"method": "passive"},
                    },
                    "tasks": [
                        {
                            "name": "Mark checkpoint pass in check mode",
                            "tomazb.acm_switchover.checkpoint_phase": {
                                "phase": "activation",
                                "checkpoint": {
                                    "enabled": True,
                                    "backend": "file",
                                    "path": str(checkpoint_path),
                                },
                                "status": "pass",
                            },
                            "register": "checkpoint_result",
                        },
                        {
                            "name": "Assert checkpoint action stayed non-mutating",
                            "ansible.builtin.assert": {
                                "that": [
                                    "checkpoint_result.changed == false",
                                    "checkpoint_result.check_mode == true",
                                    "'activation' not in checkpoint_result.checkpoint.completed_phases",
                                ]
                            },
                        },
                    ],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            "ansible-playbook",
            str(playbook),
            "-i",
            "localhost,",
            "--connection",
            "local",
            "--check",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_env(tmp_path),
        timeout=120,
    )


def test_checkpoint_operator_surface_exposes_reset_from_and_rescue_pruning():
    for surface_path in _CHECKPOINT_SURFACE_FILES:
        payload = yaml.safe_load(surface_path.read_text()) or {}
        checkpoint = payload["acm_switchover_execution"]["checkpoint"]
        assert checkpoint.get("reset_from") == "", (
            f"{surface_path.relative_to(_REPO_ROOT)} should expose " "acm_switchover_execution.checkpoint.reset_from"
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

    assert completed.returncode == 0, completed.stdout + completed.stderr
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
            _task_pattern(resumed_phase, "Mark checkpoint phase completion", "skipping"),
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

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Stop after preflight when mode is validate" in completed.stdout
    assert checkpoint == _expected_checkpoint("primary_prep", seeded_phases)

    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        vars_overrides={"acm_switchover_execution": {"mode": "validate"}},
        checkpoint_name="checkpoint-missing.json",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert checkpoint == {}, "validate mode should not create a checkpoint file"


def test_ansible_check_mode_with_execute_mode_does_not_create_or_mutate_checkpoint_path(tmp_path):
    seeded_phases = ["preflight", "primary_prep"]
    existing_checkpoint = tmp_path / "checkpoint-existing-check-mode.json"
    checkpoint_record = {
        **_expected_checkpoint("primary_prep", seeded_phases),
        "schema_version": "2.0",
        "operation_identity": build_operation_identity(hubs={}, operation={"method": "passive"}),
    }
    existing_checkpoint.write_text(
        json.dumps(checkpoint_record, indent=2),
        encoding="utf-8",
    )
    original_bytes = existing_checkpoint.read_bytes()

    completed = _run_checkpoint_check_mode_playbook(tmp_path, existing_checkpoint)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert existing_checkpoint.read_bytes() == original_bytes

    missing_checkpoint = tmp_path / "checkpoint-missing-check-mode.json"
    completed = _run_checkpoint_check_mode_playbook(tmp_path, missing_checkpoint)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not missing_checkpoint.exists(), "Ansible check mode should not create a checkpoint file"


def test_resume_after_preflight_uses_phase_local_discovery_facts(
    run_checkpoint_fixture,
):
    completed, checkpoint = run_checkpoint_fixture(
        "preflight_completed_without_preflight_facts.yml",
        pre_completed_phases=["preflight"],
        checkpoint_schema_version="2.0",
    )

    assert completed.returncode == 0, completed.stderr
    assert checkpoint["schema_version"] == "2.0"
    assert checkpoint["phase"] == "preflight"
    assert checkpoint["completed_phases"] == ["preflight"]
    assert re.search(
        _task_pattern("primary_prep", "Pause BackupSchedule on primary hub", "included"),
        completed.stdout,
    ), "primary_prep should resume without preflight-owned BackupSchedule facts"
