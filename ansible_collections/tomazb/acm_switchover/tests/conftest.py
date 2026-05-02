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


def _write_fixture_kubeconfig(kubeconfig_path: Path, context: str) -> None:
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
                            "server": "https://127.0.0.1:9",
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

    kubeconfig_dir = tmp_path / "kubeconfigs"
    for hub_name in ("primary", "secondary"):
        hub = hubs.get(hub_name)
        if not isinstance(hub, dict) or not hub.get("kubeconfig"):
            continue

        context = str(hub.get("context") or f"{hub_name}-hub")
        kubeconfig_path = kubeconfig_dir / f"{hub_name}.kubeconfig"
        _write_fixture_kubeconfig(kubeconfig_path, context)
        hub["kubeconfig"] = str(kubeconfig_path)


def _seed_fixture_defaults(vars_payload: dict) -> None:
    vars_payload.setdefault("acm_switchover_features", {}).setdefault(
        "token_expiry_warning_hours", 4
    )
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

    for schedule in vars_payload.get("acm_primary_backup_schedules_info", {}).get(
        "resources", []
    ):
        schedule.setdefault("spec", {}).setdefault("useManagedServiceAccount", True)
    for cluster_deployment in vars_payload.get(
        "acm_primary_cluster_deployments_info", {}
    ).get("resources", []):
        cluster_deployment.setdefault("spec", {}).setdefault("preserveOnDelete", True)


def _ansible_env(repo_root: Path, tmp_path: Path) -> dict:
    local_tmp = tmp_path / "ansible-local"
    remote_tmp = tmp_path / "ansible-remote"
    local_tmp.mkdir(parents=True, exist_ok=True)
    remote_tmp.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "ANSIBLE_COLLECTIONS_PATH": ":".join(
            [
                str(repo_root),
                os.path.expanduser("~/.ansible/collections"),
            ]
        ),
        "ANSIBLE_LOCAL_TEMP": str(local_tmp),
        "ANSIBLE_REMOTE_TMP": str(remote_tmp),
    }


@pytest.fixture
def run_switchover_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root
            / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover"
            / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)
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
            repo_root
            / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/restore_only"
            / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)
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
            repo_root
            / "ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint"
            / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        _seed_fixture_defaults(vars_payload)

        checkpoint_path = tmp_path / "checkpoint.json"
        report_dir = _prepare_execution_vars(vars_payload, tmp_path)
        vars_payload["acm_switchover_execution"].setdefault("checkpoint", {})
        vars_payload["acm_switchover_execution"]["checkpoint"]["path"] = str(
            checkpoint_path
        )

        if pre_completed_phases:
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "phase": pre_completed_phases[-1],
                        "completed_phases": pre_completed_phases,
                        "operational_data": {},
                        "errors": [],
                        "report_refs": [],
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    },
                    indent=2,
                )
            )

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
            pytest.fail(f"ansible-playbook timed out after 300s: {exc}")

        checkpoint = (
            json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
        )
        return completed, checkpoint

    return _run
