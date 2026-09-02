"""Runtime tests for fail-closed compactor drain verification."""

from __future__ import annotations

import re
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

VERIFICATION_FAILURE = (
    "Unable to verify Thanos compactor pod termination after scale-down; " "verify API access and retry"
)
POD_PATH = "/api/v1/namespaces/open-cluster-management-observability/pods"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _sleep_shim(tmp_path: Path) -> Path:
    shim_dir = tmp_path / "sleep-shim"
    shim_dir.mkdir()
    (shim_dir / "sitecustomize.py").write_text(
        "\n".join(
            [
                "import ast",
                "import time",
                "for _legacy_name in ('Str', 'Num', 'Bytes', 'NameConstant', 'Ellipsis'):",
                "    if not hasattr(ast, _legacy_name):",
                "        setattr(ast, _legacy_name, ast.Constant)",
                "_original_sleep = time.sleep",
                "def _r3_02_sleep(seconds):",
                "    return _original_sleep(min(float(seconds), 0.001))",
                "time.sleep = _r3_02_sleep",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return shim_dir


def _run_role(
    tmp_path: Path,
    api: FakeR302API,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, str]]]:
    repo_root = _repo_root()
    kubeconfig = tmp_path / "r3-02-primary.kubeconfig"
    write_kubeconfig(
        kubeconfig,
        context="r3-02-primary",
        server=api.url,
        token="fixture-token",
    )
    vars_file = tmp_path / "r3-02-primary-vars.yml"
    vars_file.write_text(
        yaml.safe_dump({"r3_02_kubeconfig": str(kubeconfig)}, sort_keys=False),
        encoding="utf-8",
    )
    sleep_shim = _sleep_shim(tmp_path)
    env = _ansible_env(repo_root, tmp_path)
    env["PYTHONPATH"] = f"{sleep_shim}:{env['PYTHONPATH']}"
    completed = subprocess.run(
        [
            "ansible-playbook",
            "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/" "run_r3_02_primary_prep.yml",
            "-i",
            "localhost,",
            "-e",
            f"@{vars_file}",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )
    return completed, api.requests


def _output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


def _assert_no_mutation_after_verification(
    requests: list[dict[str, str]],
    *,
    require_pod_read: bool = True,
) -> None:
    pod_read_indexes = [
        index for index, request in enumerate(requests) if request["method"] == "GET" and request["path"] == POD_PATH
    ]
    write_indexes = [
        index for index, request in enumerate(requests) if request["method"] in {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert write_indexes, "fixture must prove scale mutation happened before verification"
    if require_pod_read:
        assert pod_read_indexes, requests
    if pod_read_indexes:
        assert max(write_indexes) < min(pod_read_indexes), requests
    assert all(
        request["method"] == "PATCH" and request["path"].endswith("/statefulsets/thanos-compactor/scale")
        for request in (requests[index] for index in write_indexes)
    ), requests


def _assert_verification_refusal(
    completed: subprocess.CompletedProcess[str],
    requests: list[dict[str, str]],
    *,
    require_pod_read: bool = True,
) -> None:
    output = _output(completed)
    assert completed.returncode != 0, output
    assert VERIFICATION_FAILURE in output
    assert SENTINEL not in output
    _assert_no_mutation_after_verification(
        requests,
        require_pod_read=require_pod_read,
    )


def test_verified_empty_compactor_list_succeeds(tmp_path):
    api = FakeR302API()
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "R302_COMPACTOR_VERIFICATION=ok" in output
    assert "RESOURCE_VERSION=1" in output
    assert any(request["path"] == POD_PATH for request in requests)


def test_persistent_nonempty_compactor_list_fails_with_count(tmp_path):
    api = FakeR302API(
        pod_list_body={
            "apiVersion": "v1",
            "kind": "PodList",
            "metadata": {"resourceVersion": "1"},
            "items": [
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": "thanos-compactor-0",
                        "namespace": "open-cluster-management-observability",
                    },
                }
            ],
        }
    )
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode != 0, output
    assert re.search(
        r"Thanos compactor still has\s+1\s+pod\(s\) running after scale-down",
        output,
    )
    assert sum(request["path"] == POD_PATH for request in requests) >= 30
    _assert_no_mutation_after_verification(requests)


def test_bad_request_compactor_read_fails_closed(tmp_path):
    api = FakeR302API(
        pod_list_status=400,
        pod_list_body=status_payload(400),
    )
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_verification_refusal(completed, requests)


def test_forbidden_compactor_read_fails_closed_and_sanitized(tmp_path):
    api = FakeR302API(
        pod_list_status=403,
        pod_list_body=status_payload(403, message=f"token={SENTINEL}"),
    )
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_verification_refusal(completed, requests)


def test_transport_compactor_read_fails_closed(tmp_path):
    api = FakeR302API(close_after_scale=True)
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_verification_refusal(
        completed,
        requests,
        require_pod_read=False,
    )


def test_malformed_compactor_read_fails_closed(tmp_path):
    api = FakeR302API(
        pod_list_body={
            "apiVersion": "v1",
            "kind": "PodList",
            "items": "not-a-list",
        }
    )
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_verification_refusal(completed, requests)
