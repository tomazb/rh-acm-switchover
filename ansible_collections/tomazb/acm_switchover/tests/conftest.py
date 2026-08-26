"""Shared fixtures for switchover integration and scenario tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import NoReturn

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ANSIBLE_PY314_COMPAT_PATH = _REPO_ROOT / "ansible_collections/tomazb/acm_switchover/tests/support/python314_ast_compat"


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


def _write_fixture_kubeconfig(
    kubeconfig_path: Path,
    context: str,
    server: str = "https://127.0.0.1:9",
) -> None:
    kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
    cluster_name = f"{context}-cluster"
    user_name = f"{context}-user"
    kubeconfig_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {
                        "name": cluster_name,
                        "cluster": {
                            "server": server,
                            "insecure-skip-tls-verify": True,
                        },
                    }
                ],
                "contexts": [
                    {
                        "name": context,
                        "context": {
                            "cluster": cluster_name,
                            "user": user_name,
                        },
                    }
                ],
                "current-context": context,
                "users": [
                    {
                        "name": user_name,
                        "user": {
                            "username": "fixture",
                            "password": "fixture",
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _materialize_fixture_kubeconfigs(vars_payload: dict, tmp_path: Path) -> None:
    hubs = vars_payload.get("acm_switchover_hubs")
    if not isinstance(hubs, dict):
        return

    test_overrides = vars_payload.get("acm_switchover_test_overrides") or {}
    common_server = test_overrides.get("fixture_kubeconfig_server")
    hub_servers = test_overrides.get("fixture_kubeconfig_servers") or {}

    kubeconfig_dir = tmp_path / "kubeconfigs"
    for hub_name in ("primary", "secondary"):
        hub = hubs.get(hub_name)
        if not isinstance(hub, dict) or not hub.get("kubeconfig"):
            continue

        context = str(hub.get("context") or f"{hub_name}-hub")
        kubeconfig_path = kubeconfig_dir / f"{hub_name}.kubeconfig"
        server = hub_servers.get(hub_name) or common_server or "https://127.0.0.1:9"
        _write_fixture_kubeconfig(kubeconfig_path, context, server)
        hub["kubeconfig"] = str(kubeconfig_path)


class _FixtureConnectivityHandler(BaseHTTPRequestHandler):
    """Minimal Kubernetes API for exact default-Namespace connectivity probes."""

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        path = self.path.split("?")[0]
        responses = {
            "/version": {
                "major": "1",
                "minor": "28",
                "gitVersion": "v1.28.0",
            },
            "/api": {
                "kind": "APIVersions",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [],
            },
            "/api/v1": {
                "kind": "APIResourceList",
                "groupVersion": "v1",
                "resources": [
                    {
                        "name": "namespaces",
                        "singularName": "",
                        "namespaced": False,
                        "kind": "Namespace",
                        "verbs": ["get", "list"],
                    }
                ],
            },
            "/api/v1/namespaces/default": {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "default"},
            },
            "/apis": {
                "kind": "APIGroupList",
                "groups": [],
            },
        }
        payload = responses.get(path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def _fixture_connectivity_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureConnectivityHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _ensure_fixture_connectivity_server(vars_payload: dict, server_url: str) -> None:
    test_overrides = vars_payload.get("acm_switchover_test_overrides")
    if not isinstance(test_overrides, dict):
        test_overrides = {}
        vars_payload["acm_switchover_test_overrides"] = test_overrides
    if "fixture_kubeconfig_server" in test_overrides or "fixture_kubeconfig_servers" in test_overrides:
        return
    test_overrides["fixture_kubeconfig_server"] = server_url


def _seed_fixture_defaults(vars_payload: dict) -> None:
    test_overrides = vars_payload.get("acm_switchover_test_overrides")
    if not isinstance(test_overrides, dict):
        test_overrides = {}
        vars_payload["acm_switchover_test_overrides"] = test_overrides
    activation_restores_info = test_overrides.get("activation_restores_info")
    if activation_restores_info is not None:
        vars_payload["acm_activation_restores_info"] = activation_restores_info

    execution = vars_payload.get("acm_switchover_execution")
    execution_mode = execution.get("mode", "dry_run") if isinstance(execution, dict) else "dry_run"
    if execution_mode in {"validate", "dry_run"}:
        test_overrides.setdefault(
            "non_live_hub_identities",
            {
                "primary": {"cluster_uid": "fixture-primary-uid"},
                "secondary": {"cluster_uid": "fixture-secondary-uid"},
            },
        )

    vars_payload.setdefault("acm_switchover_features", {}).setdefault("token_expiry_warning_hours", 4)
    vars_payload.setdefault("acm_secondary_backups_info", {"resources": []})
    vars_payload.setdefault("acm_secondary_backup_schedules_info", {"resources": []})
    velero_pods = {"resources": [{"metadata": {"name": "velero"}}]}
    vars_payload.setdefault("acm_primary_velero_pods_info", velero_pods)
    vars_payload.setdefault("acm_secondary_velero_pods_info", velero_pods)

    reconciled_dpa = {
        "resources": [
            {
                "metadata": {"name": "oadp"},
                "status": {"conditions": [{"type": "Reconciled", "status": "True"}]},
            }
        ]
    }
    vars_payload.setdefault("acm_primary_dpa_info", reconciled_dpa)
    vars_payload.setdefault("acm_secondary_dpa_info", reconciled_dpa)
    vars_payload.setdefault("acm_primary_managed_clusters_info", {"resources": []})
    _seed_phase_local_facts(vars_payload)

    for backup in vars_payload.get("acm_primary_backups_info", {}).get("resources", []):
        status = backup.get("status")
        if not isinstance(status, dict):
            status = {}
            backup["status"] = status
        status.setdefault("phase", "Completed")
    for schedule in vars_payload.get("acm_primary_backup_schedules_info", {}).get("resources", []):
        schedule.setdefault("spec", {}).setdefault("useManagedServiceAccount", True)
    for cluster_deployment in vars_payload.get("acm_primary_cluster_deployments_info", {}).get("resources", []):
        cluster_deployment.setdefault("spec", {}).setdefault("preserveOnDelete", True)


def _seed_phase_local_facts(vars_payload: dict) -> None:
    phase_fact_aliases = {
        "acm_primary_mch_info": "acm_primary_prep_mch_info",
        "acm_primary_backup_schedules_info": "acm_primary_prep_backup_schedules_info",
        "acm_secondary_mch_info": "acm_activation_mch_info",
        "acm_secondary_backup_schedules_info": "acm_finalization_backup_schedules_info",
        "acm_secondary_restores_info": "acm_finalization_restores_info",
        "acm_secondary_restore_info": "acm_finalization_restores_info",
    }

    for source_fact, phase_fact in phase_fact_aliases.items():
        if source_fact in vars_payload and phase_fact not in vars_payload:
            vars_payload[phase_fact] = vars_payload[source_fact]

    if "acm_activation_mch_info" not in vars_payload and "acm_finalization_mch_info" in vars_payload:
        vars_payload["acm_activation_mch_info"] = vars_payload["acm_finalization_mch_info"]
    if "acm_finalization_mch_info" not in vars_payload and "acm_activation_mch_info" in vars_payload:
        vars_payload["acm_finalization_mch_info"] = vars_payload["acm_activation_mch_info"]


def _ansible_env(repo_root: Path, tmp_path: Path, *, extra_pythonpaths: tuple[Path, ...] = ()) -> dict:
    local_tmp = tmp_path / "ansible-local"
    remote_tmp = tmp_path / "ansible-remote"
    local_tmp.mkdir(parents=True, exist_ok=True)
    remote_tmp.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "ANSIBLE_COLLECTIONS_PATH": ":".join(
            [
                str(repo_root),
                os.path.expanduser("~/.ansible/collections"),
            ]
        ),
        # Pin local-connection module execution to the controller interpreter that
        # has collection test dependencies (kubernetes). ansible-core 2.16 auto
        # discovery otherwise selects /usr/bin/python3 and nested k8s_info fails
        # before any live request — breaking SSA-01 identity-barrier integration.
        "ANSIBLE_PYTHON_INTERPRETER": sys.executable,
        "ANSIBLE_LOCAL_TEMP": str(local_tmp),
        "ANSIBLE_REMOTE_TMP": str(remote_tmp),
    }
    pythonpaths = [str(_ANSIBLE_PY314_COMPAT_PATH)]
    pythonpaths.extend(str(path) for path in extra_pythonpaths)
    existing_pythonpath = os.environ.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpaths.append(existing_pythonpath)
    env["PYTHONPATH"] = ":".join(pythonpaths)
    env.pop("ANSIBLE_FORCE_COLOR", None)
    env["ANSIBLE_NOCOLOR"] = "1"
    return env


def _merge_test_vars(base: dict, overrides: dict) -> dict:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_test_vars(base[key], value)
        else:
            base[key] = value
    return base


def _timeout_stream_text(stream: str | bytes | None) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode(errors="replace")
    return stream


def _fail_ansible_playbook_timeout(exc: subprocess.TimeoutExpired, timeout_seconds: int) -> NoReturn:
    pytest.fail(
        f"ansible-playbook timed out after {timeout_seconds}s.\n"
        f"Stdout:\n{_timeout_stream_text(exc.stdout)}\n"
        f"Stderr:\n{_timeout_stream_text(exc.stderr)}"
    )


@pytest.fixture
def run_switchover_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)
        with _fixture_connectivity_api() as server_url:
            _ensure_fixture_connectivity_server(vars_payload, server_url)
            report_dir = _prepare_execution_vars(vars_payload, tmp_path)

            vars_file = tmp_path / "vars.yml"
            vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

            env = _ansible_env(repo_root, tmp_path)

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
                _fail_ansible_playbook_timeout(exc, 300)

            report_path = report_dir / "switchover-report.json"
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
            return completed, report

    return _run


@pytest.fixture
def run_role_fixture(tmp_path):
    def _run(role_name: str, fixture_name: str) -> subprocess.CompletedProcess[str]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/roles" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)
        _prepare_execution_vars(vars_payload, tmp_path)

        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

        playbook_path = tmp_path / f"{role_name}.yml"
        playbook_path.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": f"Run {role_name} role",
                                "ansible.builtin.include_role": {
                                    "name": f"tomazb.acm_switchover.{role_name}",
                                },
                            }
                        ],
                    }
                ],
                sort_keys=False,
            )
        )

        env = _ansible_env(repo_root, tmp_path)

        try:
            return subprocess.run(
                [
                    "ansible-playbook",
                    str(playbook_path),
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
            _fail_ansible_playbook_timeout(exc, 300)

    return _run


@pytest.fixture
def run_restore_only_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root
            / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/restore_only"
            / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)
        with _fixture_connectivity_api() as server_url:
            _ensure_fixture_connectivity_server(vars_payload, server_url)
            report_dir = _prepare_execution_vars(vars_payload, tmp_path)

            vars_file = tmp_path / "vars.yml"
            vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

            env = _ansible_env(repo_root, tmp_path)

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
                _fail_ansible_playbook_timeout(exc, 300)

            report_path = report_dir / "restore-only-report.json"
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
            return completed, report

    return _run


@pytest.fixture
def run_checkpoint_fixture(tmp_path):
    def _run(
        fixture_name: str,
        pre_completed_phases: list[str] | None = None,
        vars_overrides: dict | None = None,
        checkpoint_name: str = "checkpoint.json",
        checkpoint_schema_version: str = "1.0",
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)
        if vars_overrides:
            _merge_test_vars(vars_payload, vars_overrides)

        checkpoint_path = tmp_path / checkpoint_name
        with _fixture_connectivity_api() as server_url:
            _ensure_fixture_connectivity_server(vars_payload, server_url)
            _prepare_execution_vars(vars_payload, tmp_path)
            vars_payload["acm_switchover_execution"].setdefault("checkpoint", {})
            vars_payload["acm_switchover_execution"]["checkpoint"]["path"] = str(checkpoint_path)

            if pre_completed_phases:
                checkpoint_record = {
                    "schema_version": checkpoint_schema_version,
                    "phase": pre_completed_phases[-1],
                    "completed_phases": pre_completed_phases,
                    "operational_data": {},
                    "errors": [],
                    "report_refs": [],
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
                if checkpoint_schema_version == "2.0":
                    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
                        build_operation_identity,
                    )

                    test_overrides = vars_payload.get("acm_switchover_test_overrides") or {}
                    checkpoint_record["operation_identity"] = build_operation_identity(
                        hubs=vars_payload.get("acm_switchover_hubs") or {},
                        operation=vars_payload.get("acm_switchover_operation") or {},
                        collection_version=vars_payload.get("acm_switchover_collection_version"),
                        hub_identities=test_overrides.get("non_live_hub_identities") or {},
                    )
                checkpoint_path.write_text(json.dumps(checkpoint_record, indent=2))

            vars_file = tmp_path / "vars.yml"
            vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

            env = _ansible_env(
                repo_root,
                tmp_path,
            )

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
                _fail_ansible_playbook_timeout(exc, 300)

            report = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
            return completed, report

    return _run
