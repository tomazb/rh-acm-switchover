"""Runtime tests for the shipped acm_k8s_read_outcome module."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import (
    write_kubeconfig,
)
from ansible_collections.tomazb.acm_switchover.tests.integration.r3_02_fake_api import (
    SENTINEL,
    FakeR302API,
    status_payload,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _run_module(
    tmp_path: Path,
    *,
    server: str,
    read_mode: str,
    kind: str,
    resource_name: str,
    name: str = "",
    check_mode: bool = False,
) -> subprocess.CompletedProcess[str]:
    repo_root = _repo_root()
    kubeconfig = tmp_path / "r3-02.kubeconfig"
    write_kubeconfig(
        kubeconfig,
        context="r3-02",
        server=server,
        token="fixture-token",
    )
    vars_file = tmp_path / "r3-02-vars.yml"
    vars_file.write_text(
        yaml.safe_dump(
            {
                "r3_02_kubeconfig": str(kubeconfig),
                "r3_02_context": "r3-02",
                "r3_02_read_mode": read_mode,
                "r3_02_kind": kind,
                "r3_02_resource_name": resource_name,
                "r3_02_name": name,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    command = [
        "ansible-playbook",
        "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/" "run_k8s_read_outcome.yml",
        "-i",
        "localhost,",
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
        timeout=60,
    )


def _output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


def test_runtime_list_empty_is_ok(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=ok COUNT=0 CHANGED=False" in output, api.requests


def test_runtime_named_get_is_ok(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=ok COUNT=1 CHANGED=False" in output


def test_runtime_named_404_is_not_found(tmp_path):
    api = FakeR302API(
        configmap_status=404,
        configmap_body=status_payload(404),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="get",
            kind="ConfigMap",
            resource_name="configmaps",
            name="test-config",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=not_found COUNT=0 CHANGED=False" in output


def test_runtime_list_404_is_error(tmp_path):
    api = FakeR302API(
        pod_list_status=404,
        pod_list_body=status_payload(404),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output


def test_runtime_bad_request_is_error(tmp_path):
    api = FakeR302API(
        pod_list_status=400,
        pod_list_body=status_payload(400),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output


def test_runtime_forbidden_is_sanitized_error(tmp_path):
    api = FakeR302API(
        pod_list_status=403,
        pod_list_body=status_payload(403, message=f"token={SENTINEL}"),
    )
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output
    assert SENTINEL not in output


def test_runtime_connection_failure_is_error(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unavailable_url = f"http://127.0.0.1:{sock.getsockname()[1]}"

    completed = _run_module(
        tmp_path,
        server=unavailable_url,
        read_mode="list",
        kind="Pod",
        resource_name="pods",
    )

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=error COUNT=0 CHANGED=False" in output


def test_runtime_discovery_failure_is_kind_not_served(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="NotARealKind",
            resource_name="notarealkinds",
        )
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=kind_not_served COUNT=0 CHANGED=False" in output


def test_runtime_check_mode_still_reads_without_writes(tmp_path):
    api = FakeR302API()
    try:
        completed = _run_module(
            tmp_path,
            server=api.url,
            read_mode="list",
            kind="Pod",
            resource_name="pods",
            check_mode=True,
        )
        requests = api.requests
        writes = api.writes
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "READ_STATUS=ok COUNT=0 CHANGED=False" in output
    assert {"method": "GET", "path": "/api/v1/namespaces/test-ns/pods"} in requests
    assert writes == []
    assert "changed=0" in output
