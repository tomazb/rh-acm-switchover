"""Execute the unchanged shipped Backup verification tasks over non-live HTTP (#367)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env, _write_fixture_kubeconfig

REPO_ROOT = Path(__file__).resolve().parents[5]
TASK_FILE = REPO_ROOT / "ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/verify_backups.yml"
BACKUP_PATH = "/apis/velero.io/v1/namespaces/open-cluster-management-backup/backups"
BACKUP_NAME = "acm-resources-cadence-fixture"


@contextmanager
def backup_api(*, phase="Completed", errors=0, denied=False):
    """Serve discovery and one fresh Backup, recording actual client requests."""
    requests = []
    backup = {
        "apiVersion": "velero.io/v1",
        "kind": "Backup",
        "metadata": {"name": BACKUP_NAME, "creationTimestamp": "2026-10-17T12:01:00Z"},
        "status": {"phase": phase, "errors": errors},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            requests.append(path)
            responses = {
                "/version": {"major": "1", "minor": "29", "gitVersion": "v1.29.0"},
                "/api": {"kind": "APIVersions", "versions": ["v1"]},
                "/api/v1": {"kind": "APIResourceList", "groupVersion": "v1", "resources": []},
                "/apis": {
                    "kind": "APIGroupList",
                    "groups": [
                        {
                            "name": "velero.io",
                            "versions": [{"groupVersion": "velero.io/v1", "version": "v1"}],
                            "preferredVersion": {"groupVersion": "velero.io/v1", "version": "v1"},
                        }
                    ],
                },
                "/apis/velero.io/v1": {
                    "kind": "APIResourceList",
                    "groupVersion": "velero.io/v1",
                    "resources": [
                        {
                            "name": "backups",
                            "singularName": "backup",
                            "namespaced": True,
                            "kind": "Backup",
                            "verbs": ["get", "list"],
                        }
                    ],
                },
                BACKUP_PATH: {"apiVersion": "velero.io/v1", "kind": "BackupList", "items": [backup]},
                f"{BACKUP_PATH}/{BACKUP_NAME}": backup,
            }
            forbidden = denied and path.startswith(BACKUP_PATH)
            status = 403 if forbidden else (200 if path in responses else 404)
            payload = {"kind": "Status", "status": "Failure", "reason": "Forbidden", "code": 403}
            body = json.dumps(payload if forbidden else responses.get(path, {})).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_backup_verification(tmp_path, server, *, cron="*/5 * * * *", malformed_schedule=False, bounded_failure=False):
    """Positive route directly includes TASK_FILE; only negative polling is shortened."""
    kubeconfig = tmp_path / "fixture.kubeconfig"
    _write_fixture_kubeconfig(kubeconfig, "cadence-fixture", server)
    result_path = tmp_path / "result.json"
    task_file = TASK_FILE
    if bounded_failure:
        # A transport failure otherwise waits the production minimum of ten minutes.
        # Preserve every task/expression; shorten only retry scheduling for this control.
        tasks = yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))
        for task in tasks:
            if "retries" in task:
                task.update(retries=0, delay=0)
        task_file = tmp_path / "bounded-negative-tasks.yml"
        task_file.write_text(yaml.safe_dump(tasks), encoding="utf-8")
    variables = {
        "acm_switchover_execution": {"mode": "execute"},
        "acm_switchover_operation": {"restore_only": False},
        "acm_switchover_hubs": {"secondary": {"kubeconfig": str(kubeconfig), "context": "cadence-fixture"}},
        "acm_switchover_backup_schedule_enabled_at": "2026-10-17T12:00:00Z",
        "acm_finalization_backup_schedules_info": {
            "resources": [{"spec": "invalid" if malformed_schedule else {"veleroSchedule": cron}}]
        },
    }
    playbook = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": variables,
            "tasks": [
                {
                    "block": [{"ansible.builtin.include_tasks": str(task_file)}],
                    "always": [
                        {
                            "ansible.builtin.copy": {
                                "dest": str(result_path),
                                "content": "{{ {'result': acm_switchover_verify_backups_result | default({}), "
                                "'timeout': _acm_secondary_backup_verify_timeout_seconds | default(0)} | to_json }}",
                            }
                        }
                    ],
                }
            ],
        }
    ]
    playbook_path = tmp_path / "cadence.yml"
    playbook_path.write_text(yaml.safe_dump(playbook), encoding="utf-8")
    (tmp_path / "task-provenance.json").write_text(
        json.dumps(
            {
                "shipped_task": str(TASK_FILE),
                "shipped_sha256": hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
                "included_task": str(task_file),
                "included_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
                "bounded_polling_copy": bounded_failure,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["ansible-playbook", str(playbook_path), "-i", "localhost,"],
        cwd=REPO_ROOT,
        env=_ansible_env(REPO_ROOT, tmp_path),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    (tmp_path / "ansible.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert result_path.exists(), completed.stdout + completed.stderr
    return completed, json.loads(result_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("cron", "seconds"),
    [
        ("*/5 * * * *", 900),
        ("*/1 * * * *", 660),
        ("*/0 * * * *", 600),
        ("5 */2 * * *", 7800),
        ("5 2 */3 * *", 259800),
        ("5 2 * * *", 87000),
        ("5 2 * * 1", 605400),
        ("5 2 1 * *", 2592600),
        ("* * * * *", 600),
        ("not a cron expression", 600),
        ("", 600),
    ],
)
def test_shipped_execute_approved_cadences_reach_backup_discovery_health_and_pass(tmp_path, cron, seconds):
    with backup_api() as (server, requests):
        completed, facts = run_backup_verification(tmp_path, server, cron=cron)
    (tmp_path / "requests.json").write_text(json.dumps(requests), encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert int(facts["timeout"]) == seconds
    assert facts["result"]["status"] == "pass"
    assert facts["result"]["backup_name"] == BACKUP_NAME
    assert facts["result"]["backup_phase"] == "Completed"
    assert BACKUP_PATH in requests
    assert f"{BACKUP_PATH}/{BACKUP_NAME}" in requests


@pytest.mark.parametrize(("phase", "errors"), [("Failed", 0), ("PartiallyFailed", 1), ("Completed", 1)])
def test_shipped_execute_tasks_refuse_unhealthy_backup(tmp_path, phase, errors):
    with backup_api(phase=phase, errors=errors) as (server, requests):
        completed, facts = run_backup_verification(tmp_path, server)
    assert completed.returncode != 0
    assert "with errors" in completed.stdout
    assert facts["result"] == {}
    assert f"{BACKUP_PATH}/{BACKUP_NAME}" in requests


def test_transport_denial_does_not_publish_pass_with_bounded_polling(tmp_path):
    with backup_api(denied=True) as (server, requests):
        completed, facts = run_backup_verification(tmp_path, server, bounded_failure=True)
    assert completed.returncode != 0
    assert "Forbidden" in completed.stdout
    assert facts["result"] == {}
    assert BACKUP_PATH in requests


def test_malformed_schedule_fails_before_backup_requests_or_pass(tmp_path):
    with backup_api() as (server, requests):
        completed, facts = run_backup_verification(tmp_path, server, malformed_schedule=True)
    assert completed.returncode != 0
    assert "invalid" in completed.stdout or "get" in completed.stdout
    assert facts["result"] == {}
    assert not requests
