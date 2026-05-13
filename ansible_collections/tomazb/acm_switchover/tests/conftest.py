"""Shared fixtures for switchover integration and scenario tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

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
    test_overrides = vars_payload.get("acm_switchover_test_overrides")
    if isinstance(test_overrides, dict):
        activation_restores_info = test_overrides.get("activation_restores_info")
        if activation_restores_info is not None:
            vars_payload["acm_activation_restores_info"] = activation_restores_info

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
        "ANSIBLE_LOCAL_TEMP": str(local_tmp),
        "ANSIBLE_REMOTE_TMP": str(remote_tmp),
    }
    pythonpaths = [str(_ANSIBLE_PY314_COMPAT_PATH)]
    pythonpaths.extend(str(path) for path in extra_pythonpaths)
    existing_pythonpath = os.environ.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpaths.append(existing_pythonpath)
    env["PYTHONPATH"] = ":".join(pythonpaths)
    return env


def _merge_test_vars(base: dict, overrides: dict) -> dict:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_test_vars(base[key], value)
        else:
            base[key] = value
    return base


@pytest.fixture
def run_switchover_fixture(tmp_path):
    def _run(fixture_name: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _REPO_ROOT
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover" / fixture_name
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
        report_dir = _prepare_execution_vars(vars_payload, tmp_path)
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

                checkpoint_record["operation_identity"] = build_operation_identity(
                    hubs=vars_payload.get("acm_switchover_hubs") or {},
                    operation=vars_payload.get("acm_switchover_operation") or {},
                    collection_version=vars_payload.get("acm_switchover_collection_version"),
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
            pytest.fail(f"ansible-playbook timed out after 300s: {exc}")

        checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
        return completed, checkpoint

    return _run
