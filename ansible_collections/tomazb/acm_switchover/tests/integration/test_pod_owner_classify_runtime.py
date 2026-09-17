# SPDX-License-Identifier: MIT
"""Runtime tests for the shipped acm_pod_owner_classify module.

The unit tests prove the decisions through a fake client. These prove that the module Ansible
actually packages and runs reaches the same decisions through the real kubernetes.core client
over real HTTP: camelCase owner references as the dynamic client returns them, the strict read
statuses the server's answers map to, and a read-only request surface.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from ansible_collections.tomazb.acm_switchover.tests.conftest import _ansible_env
from ansible_collections.tomazb.acm_switchover.tests.integration.argocd_fake_api import write_kubeconfig
from ansible_collections.tomazb.acm_switchover.tests.integration.pod_owner_classify_fake_api import (
    ACM_NS,
    CSV_GROUP_VERSION,
    FakePodOwnerAPI,
)

MCH_KEY = "operator.open-cluster-management.io/v1/MultiClusterHub/open-cluster-management/multiclusterhub"
DEPLOYMENT_UID = "uid-deployment-recorded"
_NOT_FOUND = (404, {"kind": "Status", "code": 404, "reason": "NotFound"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _run(tmp_path: Path, api: FakePodOwnerAPI, extra_vars: dict, *, check_mode: bool = False) -> dict:
    kubeconfig = tmp_path / "poc.kubeconfig"
    write_kubeconfig(kubeconfig, context="source-hub", server=api.url, token="fixture-token")
    result_path = tmp_path / "poc-result.json"
    vars_file = tmp_path / "poc-vars.yml"
    vars_file.write_text(
        yaml.safe_dump(
            {
                "poc_kubeconfig": str(kubeconfig),
                "poc_context": "source-hub",
                "poc_result_path": str(result_path),
                **extra_vars,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    command = [
        "ansible-playbook",
        "ansible_collections/tomazb/acm_switchover/tests/integration/playbooks/run_pod_owner_classify.yml",
        "-i",
        "localhost,",
        "-e",
        f"@{vars_file}",
    ]
    if check_mode:
        command.append("--check")
    repo_root = _repo_root()
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=_ansible_env(repo_root, tmp_path),
        timeout=120,
    )
    if not result_path.exists():
        raise AssertionError(f"no module outcome captured\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
    return json.loads(result_path.read_text())


def _csv() -> dict:
    return {
        "apiVersion": CSV_GROUP_VERSION,
        "kind": "ClusterServiceVersion",
        "metadata": {"name": "advanced-cluster-management.v2.13.0", "namespace": ACM_NS, "uid": "uid-csv"},
        "spec": {
            "customresourcedefinitions": {"owned": [{"name": "multiclusterhubs.operator.open-cluster-management.io"}]},
            "install": {"strategy": "deployment", "spec": {"deployments": [{"name": "multiclusterhub-operator"}]}},
        },
        "status": {"phase": "Succeeded"},
    }


def _deployment(uid: str | None) -> dict:
    metadata = {"name": "multiclusterhub-operator", "namespace": ACM_NS, "resourceVersion": "deploy-7"}
    if uid is not None:
        metadata["uid"] = uid
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata}


def _csv_routes(deployment: dict) -> dict:
    csv = _csv()
    base = f"/apis/{CSV_GROUP_VERSION}/namespaces/{ACM_NS}/clusterserviceversions"
    csv_with_revision = {**csv, "metadata": {**csv["metadata"], "resourceVersion": "csv-3"}}
    return {
        base: (
            200,
            {
                "kind": "ClusterServiceVersionList",
                "metadata": {"resourceVersion": "csv-list-3"},
                "items": [csv_with_revision],
            },
        ),
        f"{base}/advanced-cluster-management.v2.13.0": (200, csv_with_revision),
        f"/apis/apps/v1/namespaces/{ACM_NS}/deployments/multiclusterhub-operator": (200, deployment),
    }


def _capture_vars() -> dict:
    return {"poc_operation": "capture_identity", "poc_mch_teardown_key": MCH_KEY, "poc_mch_expected_uid": "uid-mch"}


def _recorded_identity() -> dict:
    return {
        "namespace": ACM_NS,
        "name": "multiclusterhub-operator",
        "uid": DEPLOYMENT_UID,
        "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
        "captured_at": "2026-09-17T00:00:00+00:00",
        "csv": {
            "namespace": ACM_NS,
            "name": "advanced-cluster-management.v2.13.0",
            "uid": "uid-csv",
            "owned_crd": "multiclusterhubs.operator.open-cluster-management.io",
        },
        "mch_teardown_key": MCH_KEY,
        "mch_expected_uid": "uid-mch",
    }


def _classify_routes(*, namespace=(200, None)) -> dict:
    owned = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "multiclusterhub-operator-7d9f-abcde",
            "namespace": ACM_NS,
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "name": "multiclusterhub-operator-7d9f",
                    "uid": "uid-rs",
                    "controller": True,
                }
            ],
        },
    }
    spoof = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "multiclusterhub-operator-spoof", "namespace": ACM_NS},
    }
    replicaset = {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": {
            "name": "multiclusterhub-operator-7d9f",
            "namespace": ACM_NS,
            "uid": "uid-rs",
            "resourceVersion": "rs-5",
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "multiclusterhub-operator",
                    "uid": DEPLOYMENT_UID,
                    "controller": True,
                }
            ],
        },
    }
    namespace_status, _ = namespace
    namespace_route = (
        (200, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ACM_NS, "resourceVersion": "ns-9"}})
        if namespace_status == 200
        else _NOT_FOUND
    )
    return {
        f"/api/v1/namespaces/{ACM_NS}": namespace_route,
        f"/api/v1/namespaces/{ACM_NS}/pods": (
            200,
            {"kind": "PodList", "metadata": {"resourceVersion": "pods-11"}, "items": [owned, spoof]},
        ),
        f"/apis/apps/v1/namespaces/{ACM_NS}/replicasets/multiclusterhub-operator-7d9f": (200, replicaset),
        f"/apis/apps/v1/namespaces/{ACM_NS}/deployments/multiclusterhub-operator": (200, _deployment(DEPLOYMENT_UID)),
    }


def test_the_invoking_fixture_task_declares_no_log():
    playbook = yaml.safe_load(
        (Path(__file__).parent / "playbooks" / "run_pod_owner_classify.yml").read_text(encoding="utf-8")
    )
    task = next(task for task in playbook[0]["tasks"] if "tomazb.acm_switchover.acm_pod_owner_classify" in task)
    assert task.get("no_log") is True


def test_capture_returns_the_identity_through_the_real_client(tmp_path):
    api = FakePodOwnerAPI(_csv_routes(_deployment(DEPLOYMENT_UID)))
    try:
        result = _run(tmp_path, api, _capture_vars())
    finally:
        api.close()

    assert result.get("failed") is not True, result
    assert result["changed"] is False
    assert result["capture_status"] == "operator_deployment"
    assert result["operator_deployment"]["uid"] == DEPLOYMENT_UID
    assert result["operator_deployment"]["csv"]["uid"] == "uid-csv"
    assert {r["method"] for r in api.requests} == {"GET"}


def test_a_deployment_body_without_uid_is_deployment_identity_incomplete_on_the_real_path(tmp_path):
    """E5 M4 ruling, pinned on the real client: the read succeeds, the identity is incomplete."""
    api = FakePodOwnerAPI(_csv_routes(_deployment(None)))
    try:
        result = _run(tmp_path, api, _capture_vars())
    finally:
        api.close()

    assert result["capture_status"] == "operator_identity_unavailable", result
    assert result["operator_identity_unavailable"]["reason"] == "deployment_identity_incomplete"


def test_classify_excludes_only_the_owned_pod_through_the_real_client(tmp_path):
    api = FakePodOwnerAPI(_classify_routes())
    try:
        result = _run(tmp_path, api, {"poc_operation": "classify", "poc_operator_deployment": _recorded_identity()})
        check = _run(
            tmp_path,
            api,
            {"poc_operation": "classify", "poc_operator_deployment": _recorded_identity()},
            check_mode=True,
        )
    finally:
        api.close()

    for outcome in (result, check):
        assert outcome.get("failed") is not True, outcome
        assert outcome["changed"] is False
        assert outcome["read_status"] == "ok"
        assert outcome["deployment_status"] == "matched"
        assert {d["name"]: d["decision"] for d in outcome["decisions"]} == {
            "multiclusterhub-operator-7d9f-abcde": "operator_owned",
            "multiclusterhub-operator-spoof": "drain_blocking",
        }
        assert outcome["blocking_count"] == 1
        assert (outcome["namespace_resource_version"], outcome["pods_resource_version"]) == ("ns-9", "pods-11")
        assert outcome["deployment_resource_version"] == "deploy-7"
    assert {r["method"] for r in api.requests} == {"GET"}


def test_a_positively_absent_namespace_reads_nothing_else(tmp_path):
    api = FakePodOwnerAPI(_classify_routes(namespace=(404, None)))
    try:
        result = _run(tmp_path, api, {"poc_operation": "classify", "poc_operator_deployment": _recorded_identity()})
    finally:
        api.close()

    assert result["read_status"] == "namespace_absent", result
    object_paths = [r["path"] for r in api.requests if f"/namespaces/{ACM_NS}/" in r["path"]]
    assert object_paths == [], "no Pod, Deployment or ReplicaSet read follows a positive namespace absence"
