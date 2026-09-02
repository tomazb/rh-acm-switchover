"""Runtime tests for fail-closed activation auto-import verification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
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

FAILURE_MESSAGE = "Unable to verify autoImportStrategy on the destination hub; " "verify API access and retry."
CONFIGMAP_PATH = "/api/v1/namespaces/multicluster-engine/configmaps/" "import-controller-config"
MANAGED_CLUSTER_LIST_PATH = "/apis/cluster.open-cluster-management.io/v1/managedclusters"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _configmap(*, data_marker=...) -> dict:
    resource = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "import-controller-config",
            "namespace": "multicluster-engine",
            "resourceVersion": "1",
        },
    }
    if data_marker is not ...:
        resource["data"] = data_marker
    return resource


def _run_role(
    tmp_path: Path,
    api: FakeR302API,
    *,
    check_mode: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, str]]]:
    repo_root = _repo_root()
    kubeconfig = tmp_path / "r3-02-secondary.kubeconfig"
    write_kubeconfig(
        kubeconfig,
        context="r3-02-secondary",
        server=api.url,
        token="fixture-token",
    )
    vars_file = tmp_path / "r3-02-activation-vars.yml"
    vars_file.write_text(
        yaml.safe_dump({"r3_02_kubeconfig": str(kubeconfig)}, sort_keys=False),
        encoding="utf-8",
    )
    command = [
        "ansible-playbook",
        "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/" "run_r3_02_activation.yml",
        "-i",
        "localhost,",
        "-e",
        f"@{vars_file}",
    ]
    if check_mode:
        command.append("--check")
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_env(repo_root, tmp_path),
        timeout=60,
    )
    return completed, api.requests


def _output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


def _managed_cluster_requests(
    requests: list[dict[str, str]],
) -> list[dict[str, str]]:
    return [request for request in requests if request["path"].startswith(MANAGED_CLUSTER_LIST_PATH)]


def _assert_failure_barrier(
    completed: subprocess.CompletedProcess[str],
    requests: list[dict[str, str]],
) -> None:
    output = _output(completed)
    assert completed.returncode != 0, output
    assert FAILURE_MESSAGE in output
    assert SENTINEL not in output
    assert any(request["path"] == CONFIGMAP_PATH for request in requests)
    assert _managed_cluster_requests(requests) == []


def test_explicit_configmap_404_uses_default_and_patches(tmp_path):
    api = FakeR302API(
        configmap_status=404,
        configmap_body=status_payload(404),
    )
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "R302_ACTIVATION=ok APPLY=True" in output
    assert any(request["path"] == MANAGED_CLUSTER_LIST_PATH for request in requests)
    assert any(request["method"] == "PATCH" for request in requests)


@pytest.mark.parametrize(
    "data",
    [
        ...,
        None,
        {},
        {"autoImportStrategy": "default"},
        {"autoImportStrategy": "ImportOnly"},
    ],
    ids=["absent", "null", "empty", "default", "import-only"],
)
def test_valid_import_only_configmap_reaches_annotation_path(tmp_path, data):
    api = FakeR302API(configmap_body=_configmap(data_marker=data))
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "R302_ACTIVATION=ok APPLY=True" in output
    assert any(request["path"] == MANAGED_CLUSTER_LIST_PATH for request in requests)
    assert any(request["method"] == "PATCH" for request in requests)


def test_import_and_sync_skips_managed_cluster_work(tmp_path):
    api = FakeR302API(configmap_body=_configmap(data_marker={"autoImportStrategy": "ImportAndSync"}))
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert "R302_ACTIVATION=ok APPLY=False" in output
    assert "RESOURCE_VERSION=1" in output
    assert _managed_cluster_requests(requests) == []


def test_bad_request_fails_before_managed_cluster_list_or_patch(tmp_path):
    api = FakeR302API(
        configmap_status=400,
        configmap_body=status_payload(400),
    )
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_failure_barrier(completed, requests)


def test_forbidden_fails_before_mutation_and_is_sanitized(tmp_path):
    api = FakeR302API(
        configmap_status=403,
        configmap_body=status_payload(403, message=f"token={SENTINEL}"),
    )
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_failure_barrier(completed, requests)


def test_transport_failure_stops_before_managed_cluster_work(tmp_path):
    api = FakeR302API(configmap_transport_error=True)
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_failure_barrier(completed, requests)


@pytest.mark.parametrize(
    "configmap",
    [
        {
            **_configmap(),
            "apiVersion": "v2",
        },
        {
            **_configmap(),
            "kind": "Secret",
        },
        {
            **_configmap(),
            "metadata": {
                "name": "wrong-name",
                "namespace": "multicluster-engine",
                "resourceVersion": "1",
            },
        },
        {
            **_configmap(),
            "metadata": {
                "name": "import-controller-config",
                "namespace": "wrong-namespace",
                "resourceVersion": "1",
            },
        },
        _configmap(data_marker=["not", "a", "mapping"]),
    ],
    ids=["api-version", "kind", "name", "namespace", "data"],
)
def test_malformed_configmap_evidence_stops_before_managed_clusters(
    tmp_path,
    configmap,
):
    api = FakeR302API(configmap_body=configmap)
    try:
        completed, requests = _run_role(tmp_path, api)
    finally:
        api.close()

    _assert_failure_barrier(completed, requests)


def test_check_mode_reads_but_never_patches_managed_clusters(tmp_path):
    api = FakeR302API(configmap_body=_configmap(data_marker={"autoImportStrategy": "ImportOnly"}))
    try:
        completed, requests = _run_role(tmp_path, api, check_mode=True)
    finally:
        api.close()

    output = _output(completed)
    assert completed.returncode == 0, output
    assert any(request["path"] == CONFIGMAP_PATH for request in requests)
    assert any(request["path"] == MANAGED_CLUSTER_LIST_PATH for request in requests)
    assert not any(request["method"] == "PATCH" for request in requests)
