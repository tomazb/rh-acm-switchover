"""Helpers for fixture-driven preflight integration tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import (
    _materialize_fixture_kubeconfigs,
    _seed_fixture_defaults,
)


def _find_repo_root() -> Path:
    """Walk upward from this file to find the repository root (contains .git)."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    raise RuntimeError("Could not find repository root from %s" % Path(__file__))


def _materialize_report_dir(report_dir: str, tmp_path: Path) -> Path:
    materialized = Path(report_dir.replace("__TMP_PATH__", str(tmp_path)))
    if "__TMP_PATH__" not in report_dir and not materialized.is_absolute():
        return tmp_path / materialized
    return materialized


def _prepare_execution_vars(vars_payload: dict, tmp_path: Path) -> Path:
    execution = vars_payload.setdefault("acm_switchover_execution", {})
    report_dir = execution.get("report_dir")
    if report_dir:
        effective_report_dir = _materialize_report_dir(str(report_dir), tmp_path)
        execution["report_dir"] = str(effective_report_dir)
    else:
        effective_report_dir = tmp_path / "artifacts"
        execution["report_dir"] = str(effective_report_dir)
    _materialize_fixture_kubeconfigs(vars_payload, tmp_path)
    return effective_report_dir


@pytest.fixture
def run_preflight_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _find_repo_root()
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)
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

        completed = subprocess.run(
            [
                "ansible-playbook",
                "ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml",
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

        report_path = report_dir / "preflight-report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        return completed, report

    return _run


@pytest.fixture
def run_argocd_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _find_repo_root()
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/argocd" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}

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

        summary_path = tmp_path / "summary.json"

        completed = subprocess.run(
            [
                "ansible-playbook",
                "ansible_collections/tomazb/acm_switchover/playbooks/argocd_manage_test.yml",
                "-i",
                "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                "-e",
                f"@{vars_file}",
                "-e",
                f"summary_path={summary_path}",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=300,
        )

        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        return completed, summary

    return _run


@pytest.fixture
def run_noncore_fixture(tmp_path):
    def _run(fixture_name: str, playbook_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _find_repo_root()
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/noncore" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}

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

        summary_path = tmp_path / "summary.json"

        completed = subprocess.run(
            [
                "ansible-playbook",
                f"ansible_collections/tomazb/acm_switchover/playbooks/{playbook_name}.yml",
                "-i",
                "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                "-e",
                f"@{vars_file}",
                "-e",
                f"summary_path={summary_path}",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=300,
        )

        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        return completed, summary

    return _run
