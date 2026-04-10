"""Helpers for fixture-driven preflight integration tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def run_preflight_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = Path.cwd()
        fixture_path = (
            repo_root
            / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight"
            / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text())
        vars_payload["acm_switchover_execution"]["report_dir"] = str(tmp_path / "artifacts")

        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

        env = {
            **os.environ,
            "ANSIBLE_COLLECTIONS_PATH": ":".join([
                str(repo_root),
                os.path.expanduser("~/.ansible/collections"),
            ]),
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
        )

        report_path = tmp_path / "artifacts" / "preflight-report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        return completed, report

    return _run
