"""#332 rewind fence through real ansible-playbook task workers.

Every task runs in its own forked worker process, so the fence must identify the
invocation, not the worker: a reset_from applied by the first enter is not applied
again by a later enter of the same run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env

IDENTITY = {
    "primary_context": "primary-hub",
    "secondary_context": "secondary-hub",
    "primary_cluster_uid": "uid-primary",
    "secondary_cluster_uid": "uid-secondary",
    "method": "passive",
    "activation_method": "patch",
    "restore_only": False,
    "old_hub_action": "secondary",
    "collection_version": "",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _transition(phase, status):
    return {
        "name": f"{status} {phase}",
        "tomazb.acm_switchover.checkpoint_phase": {
            "phase": phase,
            "checkpoint": "{{ acm_switchover_execution.checkpoint }}",
            "status": status,
        },
    }


def _run(tmp_path, checkpoint_path, steps):
    playbook = tmp_path / "rewind.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {
                        # The reset path rebuilds the identity from these, as a real run supplies them.
                        "acm_switchover_hubs": {
                            "primary": {"context": "primary-hub"},
                            "secondary": {"context": "secondary-hub"},
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
                        "acm_switchover_execution": {
                            "mode": "execute",
                            "checkpoint": {
                                "enabled": True,
                                "backend": "file",
                                "path": str(checkpoint_path),
                                "reset_from": "primary_prep",
                            },
                        },
                    },
                    "tasks": [_transition(phase, status) for phase, status in steps],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        ["ansible-playbook", str(playbook), "-i", "localhost,"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_env(_repo_root(), tmp_path),
        timeout=300,
    )


def test_a_rewind_is_applied_once_per_invocation_across_task_workers(tmp_path):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "phase": "activation",
                "phase_status": "pass",
                "completed_phases": ["preflight", "primary_prep", "activation"],
                "operational_data": {},
                "operation_identity": IDENTITY,
                "errors": [],
                "report_refs": [],
            }
        ),
        encoding="utf-8",
    )
    steps = [("preflight", "enter"), ("primary_prep", "enter"), ("primary_prep", "pass"), ("activation", "enter")]
    completed = _run(tmp_path, checkpoint_path, steps)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    # The activation enter did not prune the primary_prep this invocation completed.
    assert stored["completed_phases"] == ["preflight", "primary_prep"]

    # A later invocation with the same option applies the rewind again.
    completed = _run(tmp_path, checkpoint_path, [("preflight", "enter")])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))["completed_phases"] == ["preflight"]
