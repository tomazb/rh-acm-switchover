"""Helpers for fixture-driven preflight integration tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import (
    _ansible_env,
    _materialize_fixture_kubeconfigs,
    _merge_test_vars,
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


def _write_preflight_fixture_kubeconfig(kubeconfig_path: Path, context: str, server: str) -> None:
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


def _materialize_preflight_fixture_kubeconfigs(vars_payload: dict, tmp_path: Path) -> None:
    test_overrides = vars_payload.get("acm_switchover_test_overrides")
    if not isinstance(test_overrides, dict) or "fixture_kubeconfig_server" not in test_overrides:
        _materialize_fixture_kubeconfigs(vars_payload, tmp_path)
        return

    hubs = vars_payload.get("acm_switchover_hubs")
    if not isinstance(hubs, dict):
        return

    kubeconfig_dir = tmp_path / "kubeconfigs"
    kubeconfig_server = str(test_overrides["fixture_kubeconfig_server"])
    for hub_name in ("primary", "secondary"):
        hub = hubs.get(hub_name)
        if not isinstance(hub, dict) or not hub.get("kubeconfig"):
            continue
        context = str(hub.get("context") or f"{hub_name}-hub")
        kubeconfig_path = kubeconfig_dir / f"{hub_name}.kubeconfig"
        _write_preflight_fixture_kubeconfig(kubeconfig_path, context, kubeconfig_server)
        hub["kubeconfig"] = str(kubeconfig_path)


def _prepare_execution_vars(vars_payload: dict, tmp_path: Path) -> Path:
    execution = vars_payload.setdefault("acm_switchover_execution", {})
    report_dir = execution.get("report_dir")
    if report_dir:
        effective_report_dir = _materialize_report_dir(str(report_dir), tmp_path)
        execution["report_dir"] = str(effective_report_dir)
    else:
        effective_report_dir = tmp_path / "artifacts"
        execution["report_dir"] = str(effective_report_dir)
    _materialize_preflight_fixture_kubeconfigs(vars_payload, tmp_path)
    return effective_report_dir


@pytest.fixture
def run_preflight_fixture(tmp_path):
    def _run(
        fixture_name: str,
        overrides: dict | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        repo_root = _find_repo_root()
        fixture_path = (
            repo_root / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/preflight" / fixture_name
        )
        vars_payload = yaml.safe_load(fixture_path.read_text()) or {}
        if overrides:
            _merge_test_vars(vars_payload, overrides)
        _seed_fixture_defaults(vars_payload)
        report_dir = _prepare_execution_vars(vars_payload, tmp_path)

        vars_file = tmp_path / "vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))

        env = _ansible_env(repo_root, tmp_path)

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
def run_argocd_discovery_fixture(tmp_path):
    def _run(
        *,
        server: str | None = None,
        advisory: bool = False,
        mock_apps: list[dict] | None = None,
        check_mode: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        repo_root = _find_repo_root()
        hubs = {
            "primary": {
                "context": "primary-hub",
                "kubeconfig": "",
            },
            "secondary": {
                "context": "secondary-hub",
                "kubeconfig": "",
            },
        }
        if server:
            kubeconfig_path = tmp_path / "argocd-discovery.kubeconfig"
            _write_preflight_fixture_kubeconfig(kubeconfig_path, "primary-hub", server)
            hubs["primary"]["kubeconfig"] = str(kubeconfig_path)

        vars_payload = {
            "acm_switchover_hubs": hubs,
            "acm_switchover_argocd": {"mode": "discover"},
            "acm_switchover_argocd_mode_override": "discover",
            "acm_switchover_argocd_advisory": advisory,
            "_argocd_discover_hub": "primary",
        }
        if mock_apps is not None:
            vars_payload["acm_switchover_argocd_mock_apps"] = mock_apps

        vars_file = tmp_path / "argocd-discovery-vars.yml"
        vars_file.write_text(yaml.safe_dump(vars_payload, sort_keys=False))
        playbook_path = tmp_path / "argocd-discovery.yml"
        playbook_path.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "tasks": [
                            {
                                "name": "Run Argo CD discovery task path",
                                "ansible.builtin.include_role": {
                                    "name": "tomazb.acm_switchover.argocd_manage",
                                    "tasks_from": "discover",
                                },
                            },
                            {
                                "name": "Publish safe discovery result",
                                "ansible.builtin.debug": {
                                    "msg": (
                                        "DISCOVERY_STATUS="
                                        "{{ acm_switchover_argocd_discovery_status.status }} "
                                        "COUNT={{ acm_switchover_argocd_acm_apps | length }}"
                                    )
                                },
                            },
                        ],
                    }
                ],
                sort_keys=False,
            )
        )

        command = [
            "ansible-playbook",
            str(playbook_path),
            "-i",
            "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
            "-e",
            f"@{vars_file}",
        ]
        if check_mode:
            command.append("--check")
        return subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=_ansible_env(repo_root, tmp_path),
            timeout=300,
        )

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

        env = _ansible_env(repo_root, tmp_path)

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

        env = _ansible_env(repo_root, tmp_path)

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
