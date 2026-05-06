"""Tests for bounded klusterlet probe and remediation modules."""

from __future__ import annotations

import base64
import threading

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_klusterlet_probe import (
    probe_klusterlet_connections,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_klusterlet_remediate import (
    remediate_klusterlets,
)


class FakeApiError(Exception):
    def __init__(self, status: int, reason: str = ""):
        super().__init__(reason or f"status={status}")
        self.status = status
        self.reason = reason


class FakeCoreClient:
    def __init__(self, secrets: dict[tuple[str, str], dict] | None = None, fail_create: bool = False):
        self.secrets = secrets or {}
        self.fail_create = fail_create
        self.deleted: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, dict]] = []

    def read_namespaced_secret(self, name: str, namespace: str):
        secret = self.secrets.get((namespace, name))
        if secret is None:
            raise FakeApiError(404, "Not Found")
        return secret

    def delete_namespaced_secret(self, name: str, namespace: str):
        self.deleted.append((namespace, name))
        if (namespace, name) not in self.secrets:
            raise FakeApiError(404, "Not Found")
        self.secrets.pop((namespace, name), None)

    def create_namespaced_secret(self, namespace: str, body: dict):
        if self.fail_create:
            raise FakeApiError(500, "create failed")
        self.created.append((namespace, body["metadata"]["name"], body))
        self.secrets[(namespace, body["metadata"]["name"])] = body


class FakeAppsClient:
    def __init__(self):
        self.patched: list[tuple[str, str, dict]] = []

    def patch_namespaced_deployment(self, name: str, namespace: str, body: dict):
        self.patched.append((namespace, name, body))


def _b64_yaml(payload: dict) -> str:
    return base64.b64encode(yaml.safe_dump(payload).encode("utf-8")).decode("ascii")


def _import_secret(server: str) -> dict:
    bootstrap = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "bootstrap-hub-kubeconfig",
            "namespace": "open-cluster-management-agent",
        },
        "data": {
            "kubeconfig": _b64_yaml({"clusters": [{"cluster": {"server": server}}]}),
        },
    }
    return {
        "data": {
            "import.yaml": base64.b64encode(yaml.safe_dump_all([bootstrap]).encode("utf-8")).decode("ascii"),
        }
    }


def _hub_secret(server: str, name: str = "hub-kubeconfig-secret") -> dict:
    return {
        "data": {
            "kubeconfig": _b64_yaml({"clusters": [{"cluster": {"server": server}}]}),
        },
        "metadata": {"name": name},
    }


def test_probe_reports_verified_wrong_hub_and_skipped_clusters():
    secondary = FakeCoreClient(
        {
            ("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443"),
            ("cluster-b", "cluster-b-import"): _import_secret("https://new.example:6443"),
        }
    )
    managed = {
        "cluster-a": FakeCoreClient(
            {("open-cluster-management-agent", "hub-kubeconfig-secret"): _hub_secret("https://new.example:6443")}
        ),
        "cluster-b": FakeCoreClient(
            {("open-cluster-management-agent", "hub-kubeconfig-secret"): _hub_secret("https://old.example:6443")}
        ),
    }

    def core_client_factory(kubeconfig: str, context: str | None = None):
        if kubeconfig == "hub":
            return secondary
        return managed[kubeconfig]

    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub", "context": "secondary"},
        managed_clusters={
            "cluster-a": {"kubeconfig": "cluster-a"},
            "cluster-b": {"kubeconfig": "cluster-b"},
            "cluster-c": {},
        },
        candidate_clusters=["cluster-a", "cluster-b", "cluster-c"],
        workers=1,
        core_client_factory=core_client_factory,
    )

    assert result["workers"] == 1
    assert result["verified_clusters"] == ["cluster-a"]
    assert result["wrong_hub_clusters"] == ["cluster-b"]
    assert result["skipped_clusters"] == ["cluster-c"]
    assert {item["cluster"]: item["status"] for item in result["results"]} == {
        "cluster-a": "verified",
        "cluster-b": "wrong_hub",
        "cluster-c": "skipped",
    }


def test_remediation_skips_when_no_pending_clusters():
    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=[],
    )

    assert result["changed"] is False
    assert result["results"] == []
    assert result["workers"] == 10


def test_remediation_check_mode_returns_plan_without_clients():
    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        check_mode=True,
        core_client_factory=lambda kubeconfig, context=None: pytest.fail("check mode must not build clients"),
        apps_client_factory=lambda kubeconfig, context=None: pytest.fail("check mode must not build clients"),
    )

    assert result["changed"] is False
    assert result["planned_clusters"] == ["cluster-a"]
    assert result["results"][0]["status"] == "planned"


def test_remediation_skips_pending_cluster_without_kubeconfig():
    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={},
        pending_clusters=["cluster-a"],
        workers=1,
    )

    assert result["changed"] is False
    assert result["skipped_clusters"] == ["cluster-a"]
    assert result["results"][0]["reason"] == "no_managed_cluster_kubeconfig"


def test_remediation_reports_missing_import_secret_as_best_effort_failure():
    secondary = FakeCoreClient()

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        workers=1,
        strict=False,
        core_client_factory=lambda kubeconfig, context=None: secondary,
        apps_client_factory=lambda kubeconfig, context=None: FakeAppsClient(),
    )

    assert result["failed"] is False
    assert result["failed_clusters"] == ["cluster-a"]
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["steps"]["import_secret_read"] == "missing"


def test_remediation_deletes_reapplies_and_restarts_klusterlet():
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})
    managed = FakeCoreClient(
        {
            ("open-cluster-management-agent", "bootstrap-hub-kubeconfig"): _hub_secret(
                "https://old.example:6443",
                name="bootstrap-hub-kubeconfig",
            )
        }
    )
    apps = FakeAppsClient()

    def core_client_factory(kubeconfig: str, context: str | None = None):
        return secondary if kubeconfig == "hub" else managed

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a", "context": "ctx-a"}},
        pending_clusters=["cluster-a"],
        workers=1,
        core_client_factory=core_client_factory,
        apps_client_factory=lambda kubeconfig, context=None: apps,
    )

    assert result["changed"] is True
    assert result["failed_clusters"] == []
    assert result["results"][0]["status"] == "remediated"
    assert ("open-cluster-management-agent", "bootstrap-hub-kubeconfig") in managed.deleted
    assert managed.created[0][0:2] == ("open-cluster-management-agent", "bootstrap-hub-kubeconfig")
    assert apps.patched[0][0:2] == ("open-cluster-management-agent", "klusterlet")


def test_remediation_preserves_best_effort_partial_failure():
    secondary = FakeCoreClient(
        {
            ("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443"),
            ("cluster-b", "cluster-b-import"): _import_secret("https://new.example:6443"),
        }
    )
    managed_clients = {
        "cluster-a": FakeCoreClient(),
        "cluster-b": FakeCoreClient(fail_create=True),
    }
    apps = FakeAppsClient()

    def core_client_factory(kubeconfig: str, context: str | None = None):
        return secondary if kubeconfig == "hub" else managed_clients[kubeconfig]

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={
            "cluster-a": {"kubeconfig": "cluster-a"},
            "cluster-b": {"kubeconfig": "cluster-b"},
        },
        pending_clusters=["cluster-a", "cluster-b"],
        workers=2,
        strict=False,
        core_client_factory=core_client_factory,
        apps_client_factory=lambda kubeconfig, context=None: apps,
    )

    assert result["changed"] is True
    assert result["failed"] is False
    assert result["failed_clusters"] == ["cluster-b"]
    assert {item["cluster"]: item["status"] for item in result["results"]} == {
        "cluster-a": "remediated",
        "cluster-b": "failed",
    }


def test_remediation_strict_mode_fails_partial_failure():
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        strict=True,
        core_client_factory=lambda kubeconfig, context=None: (
            secondary if kubeconfig == "hub" else FakeCoreClient(fail_create=True)
        ),
        apps_client_factory=lambda kubeconfig, context=None: FakeAppsClient(),
    )

    assert result["failed"] is True
    assert result["failed_clusters"] == ["cluster-a"]


def test_remediation_uses_bounded_worker_threads():
    secondary = FakeCoreClient(
        {
            ("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443"),
            ("cluster-b", "cluster-b-import"): _import_secret("https://new.example:6443"),
        }
    )
    seen_threads: set[int] = set()

    def core_client_factory(kubeconfig: str, context: str | None = None):
        seen_threads.add(threading.get_ident())
        return secondary if kubeconfig == "hub" else FakeCoreClient()

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={
            "cluster-a": {"kubeconfig": "cluster-a"},
            "cluster-b": {"kubeconfig": "cluster-b"},
        },
        pending_clusters=["cluster-a", "cluster-b"],
        workers=1,
        core_client_factory=core_client_factory,
        apps_client_factory=lambda kubeconfig, context=None: FakeAppsClient(),
    )

    assert result["workers"] == 1
    assert result["failed_clusters"] == []
    assert len(seen_threads) == 1
