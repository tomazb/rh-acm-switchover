"""Shared fixtures for switchover integration and scenario tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _materialize_report_dir(report_dir: str, tmp_path: Path) -> Path:
    return Path(report_dir.replace("__TMP_PATH__", str(tmp_path)))


def _prepare_execution_vars(vars_payload: dict, tmp_path: Path) -> Path:
    execution = vars_payload.setdefault("acm_switchover_execution", {})
    report_dir = execution.get("report_dir")
    if report_dir:
        effective_report_dir = _materialize_report_dir(str(report_dir), tmp_path)
        execution["report_dir"] = str(effective_report_dir)
    else:
        effective_report_dir = tmp_path / "artifacts"
        execution["report_dir"] = str(effective_report_dir)
    return effective_report_dir


@pytest.fixture
def run_switchover_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        report_dir = _prepare_execution_vars(vars_payload, tmp_path)

        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

        env = {
            **os.environ,
            "ANSIBLE_COLLECTIONS_PATH": ":".join(
                [
                    str(repo_root),
                    os.path.expanduser("~/.ansible/collections"),
                ]
            ),
            "ANSIBLE_LOCAL_TEMP": "/tmp/ansible-local",
            "ANSIBLE_REMOTE_TMP": "/tmp/ansible-remote",
        }

        try:
            completed = subprocess.run(
                [
                    "ansible-playbook",
                    "ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml",
                    "-i",
                    "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                    "-e",
                    f"@{vars_file}",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"ansible-playbook timed out after 300s: {exc}")

        report_path = report_dir / "switchover-report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        return completed, report

    return _run


@pytest.fixture
def run_restore_only_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/restore_only" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        report_dir = _prepare_execution_vars(vars_payload, tmp_path)

        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

        env = {
            **os.environ,
            "ANSIBLE_COLLECTIONS_PATH": ":".join(
                [
                    str(repo_root),
                    os.path.expanduser("~/.ansible/collections"),
                ]
            ),
            "ANSIBLE_LOCAL_TEMP": "/tmp/ansible-local",
            "ANSIBLE_REMOTE_TMP": "/tmp/ansible-remote",
        }

        try:
            completed = subprocess.run(
                [
                    "ansible-playbook",
                    "ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml",
                    "-i",
                    "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                    "-e",
                    f"@{vars_file}",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"ansible-playbook timed out after 300s: {exc}")

        report_path = report_dir / "restore-only-report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        return completed, report

    return _run


@pytest.fixture
def run_checkpoint_fixture(tmp_path):
    def _run(
        fixture_name: str,
        pre_completed_phases: list[str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}

        checkpoint_path = tmp_path / "checkpoint.json"
        report_dir = _prepare_execution_vars(vars_payload, tmp_path)
        vars_payload["acm_switchover_execution"].setdefault("checkpoint", {})
        vars_payload["acm_switchover_execution"]["checkpoint"]["path"] = str(checkpoint_path)

        if pre_completed_phases:
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "completed_phases": pre_completed_phases,
                        "phase_status": "pass",
                        "operational_data": {},
                        "errors": [],
                        "report_refs": [],
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    },
                    indent=2,
                )
            )

        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

        env = {
            **os.environ,
            "ANSIBLE_COLLECTIONS_PATH": ":".join(
                [
                    str(repo_root),
                    os.path.expanduser("~/.ansible/collections"),
                ]
            ),
            "ANSIBLE_LOCAL_TEMP": "/tmp/ansible-local",
            "ANSIBLE_REMOTE_TMP": "/tmp/ansible-remote",
        }

        try:
            completed = subprocess.run(
                [
                    "ansible-playbook",
                    "ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml",
                    "-i",
                    "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                    "-e",
                    f"@{vars_file}",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"ansible-playbook timed out after 300s: {exc}")

        checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
        return completed, checkpoint

    return _run
