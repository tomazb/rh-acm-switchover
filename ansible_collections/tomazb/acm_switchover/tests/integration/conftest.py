"""Helpers for fixture-driven preflight integration tests."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import (
    _ansible_env,
    _fail_ansible_playbook_timeout,
    _materialize_fixture_kubeconfigs,
    _merge_test_vars,
    _seed_fixture_defaults,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    FakeArgoCDHub,
    write_kubeconfig,
)


@dataclass(frozen=True)
class DistinctHubPlaybookRun:
    """Captured shipped-playbook result and fake Kubernetes request evidence."""

    completed: subprocess.CompletedProcess[str]
    report: dict
    preflight_report: dict
    checkpoint: dict
    checkpoint_before: bytes | None
    checkpoint_after: bytes | None
    primary_requests: list[dict[str, str]]
    secondary_requests: list[dict[str, str]]
    primary_patches: list[dict]
    secondary_patches: list[dict]


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
    if not isinstance(test_overrides, dict) or not (
        "fixture_kubeconfig_server" in test_overrides or "fixture_kubeconfig_servers" in test_overrides
    ):
        _materialize_fixture_kubeconfigs(vars_payload, tmp_path)
        return

    hubs = vars_payload.get("acm_switchover_hubs")
    if not isinstance(hubs, dict):
        return

    kubeconfig_dir = tmp_path / "kubeconfigs"
    common_server = test_overrides.get("fixture_kubeconfig_server")
    hub_servers = test_overrides.get("fixture_kubeconfig_servers")
    if not isinstance(hub_servers, dict):
        hub_servers = {}
    for hub_name in ("primary", "secondary"):
        hub = hubs.get(hub_name)
        if not isinstance(hub, dict) or not hub.get("kubeconfig"):
            continue
        kubeconfig_server = hub_servers.get(hub_name, common_server)
        if not kubeconfig_server:
            continue
        context = str(hub.get("context") or f"{hub_name}-hub")
        kubeconfig_path = kubeconfig_dir / f"{hub_name}.kubeconfig"
        _write_preflight_fixture_kubeconfig(
            kubeconfig_path,
            context,
            str(kubeconfig_server),
        )
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
def run_distinct_hub_playbook(tmp_path):
    def _run(
        *,
        primary_uid: str = "LIVE-PRIMARY",
        secondary_uid: str = "LIVE-SECONDARY",
        primary_context: str = "primary-hub",
        secondary_context: str = "secondary-hub",
        primary_kubeconfig_name: str = "primary.kubeconfig",
        secondary_kubeconfig_name: str = "secondary.kubeconfig",
        primary_identity_status: int = 200,
        secondary_identity_status: int = 200,
        primary_identity_body: dict | None = None,
        secondary_identity_body: dict | None = None,
        primary_applications: list[dict] | None = None,
        secondary_applications: list[dict] | None = None,
        mode: str = "execute",
        omit_execution_mode: bool = False,
        checkpoint_enabled: bool = True,
        checkpoint_record: dict | None = None,
        variables: dict | None = None,
        native_check: bool = False,
    ) -> DistinctHubPlaybookRun:
        repo_root = _find_repo_root()
        primary = FakeArgoCDHub(
            cluster_uid=primary_uid,
            applications=list(primary_applications or []),
            kube_system_status=primary_identity_status,
            kube_system_body=primary_identity_body,
        )
        secondary = FakeArgoCDHub(
            cluster_uid=secondary_uid,
            applications=list(secondary_applications or []),
            kube_system_status=secondary_identity_status,
            kube_system_body=secondary_identity_body,
        )
        try:
            primary_kubeconfig = tmp_path / primary_kubeconfig_name
            secondary_kubeconfig = tmp_path / secondary_kubeconfig_name
            kubeconfig_credentials = {
                "token": "ssa01-secret-token-TK72",
                "username": "fixture",
                "password": "ssa01-secret-credential-CR77",
            }
            write_kubeconfig(
                primary_kubeconfig,
                context=primary_context,
                server=primary.url,
                **kubeconfig_credentials,
            )
            write_kubeconfig(
                secondary_kubeconfig,
                context=secondary_context,
                server=secondary.url,
                **kubeconfig_credentials,
            )

            fixture_path = (
                repo_root
                / "ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover"
                / "passive_activation_success.yml"
            )
            vars_payload = yaml.safe_load(fixture_path.read_text(encoding="utf-8")) or {}
            vars_payload["acm_switchover_hubs"] = {
                "primary": {
                    "context": primary_context,
                    "kubeconfig": str(primary_kubeconfig),
                },
                "secondary": {
                    "context": secondary_context,
                    "kubeconfig": str(secondary_kubeconfig),
                },
            }
            report_dir = tmp_path / "identity-barrier-artifacts"
            checkpoint_path = tmp_path / "identity-barrier-checkpoint.json"
            vars_payload["acm_switchover_execution"] = {
                "run_id": "identity-barrier-run",
                "report_dir": str(report_dir),
                "checkpoint": {
                    "enabled": checkpoint_enabled,
                    "backend": "file",
                    "path": str(checkpoint_path),
                },
            }
            if not omit_execution_mode:
                vars_payload["acm_switchover_execution"]["mode"] = mode
            vars_payload["acm_switchover_collection_version"] = ""
            if variables:
                _merge_test_vars(vars_payload, variables)
            if omit_execution_mode:
                vars_payload["acm_switchover_execution"].pop("mode", None)
            else:
                vars_payload["acm_switchover_execution"]["mode"] = mode
            vars_payload["acm_switchover_execution"]["report_dir"] = str(report_dir)
            vars_payload["acm_switchover_execution"]["checkpoint"].update(
                {
                    "enabled": checkpoint_enabled,
                    "backend": "file",
                    "path": str(checkpoint_path),
                }
            )
            _seed_fixture_defaults(vars_payload)

            if checkpoint_record is not None:
                checkpoint_path.write_text(
                    json.dumps(checkpoint_record, indent=2),
                    encoding="utf-8",
                )
            checkpoint_before = checkpoint_path.read_bytes() if checkpoint_path.exists() else None

            vars_file = tmp_path / "identity-barrier-vars.yml"
            vars_file.write_text(
                yaml.safe_dump(vars_payload, sort_keys=False),
                encoding="utf-8",
            )
            command = [
                "ansible-playbook",
                "ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml",
                "-i",
                "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                "-e",
                f"@{vars_file}",
            ]
            if native_check:
                command.append("--check")
            try:
                completed = subprocess.run(
                    command,
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    env=_ansible_env(repo_root, tmp_path),
                    timeout=300,
                )
            except subprocess.TimeoutExpired as exc:
                _fail_ansible_playbook_timeout(exc, 300)

            report_path = report_dir / "switchover-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            preflight_report_path = report_dir / "preflight-report.json"
            preflight_report = (
                json.loads(preflight_report_path.read_text(encoding="utf-8")) if preflight_report_path.exists() else {}
            )
            checkpoint_after = checkpoint_path.read_bytes() if checkpoint_path.exists() else None
            checkpoint = json.loads(checkpoint_after) if checkpoint_after is not None else {}
            return DistinctHubPlaybookRun(
                completed=completed,
                report=report,
                preflight_report=preflight_report,
                checkpoint=checkpoint,
                checkpoint_before=checkpoint_before,
                checkpoint_after=checkpoint_after,
                primary_requests=primary.requests,
                secondary_requests=secondary.requests,
                primary_patches=list(primary.patches),
                secondary_patches=list(secondary.patches),
            )
        finally:
            primary.close()
            secondary.close()

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
def run_argocd_scoped_validation(tmp_path):
    def _run(*, query, namespaces) -> subprocess.CompletedProcess[str]:
        repo_root = _find_repo_root()
        vars_file = tmp_path / "argocd-scoped-validation-vars.yml"
        vars_file.write_text(
            yaml.safe_dump(
                {
                    "_argocd_scoped_live_query": query,
                    "_argocd_trusted_discovery_namespaces": namespaces,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        return subprocess.run(
            [
                "ansible-playbook",
                "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/" "argocd_scoped_validation.yml",
                "-i",
                "ansible_collections/tomazb/acm_switchover/examples/inventory.yml",
                "-e",
                f"@{vars_file}",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=_ansible_env(repo_root, tmp_path),
            timeout=300,
        )

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
