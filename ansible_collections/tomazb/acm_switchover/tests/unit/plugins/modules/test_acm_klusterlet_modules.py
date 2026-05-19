"""Tests for bounded klusterlet probe and remediation modules."""

from __future__ import annotations

import base64
import builtins
import threading
import time
from typing import NoReturn

import pytest
import yaml

import ansible_collections.tomazb.acm_switchover.plugins.module_utils.klusterlet as klusterlet_utils
import ansible_collections.tomazb.acm_switchover.plugins.modules.acm_klusterlet_probe as probe_module
import ansible_collections.tomazb.acm_switchover.plugins.modules.acm_klusterlet_remediate as remediate_module
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    HUB_KUBECONFIG_SECRET_NAME,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.klusterlet import (
    build_apps_v1_client,
    build_core_v1_client,
    ordered_bounded_map,
)
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
    def __init__(
        self,
        secrets: dict[tuple[str, str], dict] | None = None,
        fail_create: bool = False,
    ):
        self.secrets = secrets or {}
        self.fail_create = fail_create
        self.deleted: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, dict]] = []
        self.request_timeouts: list[int | None] = []

    def read_namespaced_secret(self, name: str, namespace: str, **kwargs):
        self.request_timeouts.append(kwargs.get("_request_timeout"))
        secret = self.secrets.get((namespace, name))
        if secret is None:
            raise FakeApiError(404, "Not Found")
        return secret

    def delete_namespaced_secret(self, name: str, namespace: str, **kwargs):
        self.request_timeouts.append(kwargs.get("_request_timeout"))
        self.deleted.append((namespace, name))
        if (namespace, name) not in self.secrets:
            raise FakeApiError(404, "Not Found")
        self.secrets.pop((namespace, name), None)

    def create_namespaced_secret(self, namespace: str, body: dict, **kwargs):
        self.request_timeouts.append(kwargs.get("_request_timeout"))
        if self.fail_create:
            raise FakeApiError(500, "create failed")
        self.created.append((namespace, body["metadata"]["name"], body))
        self.secrets[(namespace, body["metadata"]["name"])] = body


class FakeAppsClient:
    def __init__(self, fail_patch: bool = False):
        self.fail_patch = fail_patch
        self.patched: list[tuple[str, str, dict]] = []
        self.request_timeouts: list[int | None] = []

    def patch_namespaced_deployment(self, name: str, namespace: str, body: dict, **kwargs):
        self.request_timeouts.append(kwargs.get("_request_timeout"))
        if self.fail_patch:
            raise FakeApiError(500, "restart failed")
        self.patched.append((namespace, name, body))


def _fail_client_factory(kubeconfig: str, context: str | None = None) -> NoReturn:
    pytest.fail("check mode must not build clients")


def _new_fake_apps_client(kubeconfig: str, context: str | None = None) -> FakeAppsClient:
    return FakeAppsClient()


def _b64_yaml(payload: dict) -> str:
    return base64.b64encode(yaml.safe_dump(payload).encode("utf-8")).decode("ascii")


def _import_secret(server: str) -> dict:
    bootstrap = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            "namespace": MANAGED_CLUSTER_AGENT_NAMESPACE,
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


def _hub_secret(server: str, name: str = HUB_KUBECONFIG_SECRET_NAME) -> dict:
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
            {
                (
                    MANAGED_CLUSTER_AGENT_NAMESPACE,
                    HUB_KUBECONFIG_SECRET_NAME,
                ): _hub_secret("https://new.example:6443")
            }
        ),
        "cluster-b": FakeCoreClient(
            {
                (
                    MANAGED_CLUSTER_AGENT_NAMESPACE,
                    HUB_KUBECONFIG_SECRET_NAME,
                ): _hub_secret("https://old.example:6443")
            }
        ),
    }
    calls = {"hub": 0}

    def core_client_factory(kubeconfig: str, context: str | None = None):
        if kubeconfig == "hub":
            calls["hub"] += 1
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
    assert calls["hub"] == 1


def test_probe_defaults_to_all_managed_clusters_when_candidates_are_omitted():
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})
    managed = FakeCoreClient(
        {(MANAGED_CLUSTER_AGENT_NAMESPACE, HUB_KUBECONFIG_SECRET_NAME): _hub_secret("https://new.example:6443")}
    )

    def core_client_factory(kubeconfig: str, context: str | None = None):
        return secondary if kubeconfig == "hub" else managed

    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        workers=1,
        core_client_factory=core_client_factory,
    )

    assert result["verified_clusters"] == ["cluster-a"]


def test_probe_waits_until_wrong_hub_secret_converges(monkeypatch):
    """Post-remediation probe polling must tolerate stale hub-kubeconfig-secret reads."""
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})
    attempts = {"count": 0}

    class ConvergingManagedClient(FakeCoreClient):
        def read_namespaced_secret(self, name: str, namespace: str, **kwargs):
            self.request_timeouts.append(kwargs.get("_request_timeout"))
            if name != HUB_KUBECONFIG_SECRET_NAME:
                raise FakeApiError(404, "Not Found")
            attempts["count"] += 1
            if attempts["count"] < 2:
                return _hub_secret("https://old.example:6443")
            return _hub_secret("https://new.example:6443")

    def core_client_factory(kubeconfig: str, context: str | None = None):
        if kubeconfig == "secondary":
            return secondary
        return ConvergingManagedClient()

    if hasattr(klusterlet_utils, "time"):
        monkeypatch.setattr(klusterlet_utils.time, "sleep", lambda _seconds: None)

    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "secondary"},
        managed_clusters={"cluster-a": {"kubeconfig": "managed"}},
        candidate_clusters=["cluster-a"],
        workers=1,
        request_timeout=30,
        future_timeout=30,
        wait_timeout=3,
        wait_interval=1,
        core_client_factory=core_client_factory,
    )

    assert result["verified_clusters"] == ["cluster-a"]
    assert result["wrong_hub_clusters"] == []
    assert attempts["count"] == 2


def test_client_builders_require_explicit_kubeconfig(monkeypatch):
    original_import = builtins.__import__

    def fail_kubernetes_import(name, *args, **kwargs):
        if name == "kubernetes" or name.startswith("kubernetes."):
            pytest.fail("kubeconfig validation must run before importing kubernetes")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_kubernetes_import)

    with pytest.raises(ValueError, match="kubeconfig is required"):
        build_core_v1_client("")
    with pytest.raises(ValueError, match="kubeconfig is required"):
        build_apps_v1_client(None)


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
        core_client_factory=_fail_client_factory,
        apps_client_factory=_fail_client_factory,
    )

    assert result["changed"] is True
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

    def core_client_factory(kubeconfig: str, context: str | None = None) -> FakeCoreClient:
        return secondary

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        workers=1,
        strict=False,
        core_client_factory=core_client_factory,
        apps_client_factory=_new_fake_apps_client,
    )

    assert result["failed"] is False
    assert result["failed_clusters"] == ["cluster-a"]
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["steps"]["import_secret_read"] == "missing"
    assert "pending" not in result["results"][0]["steps"].values()


def test_remediation_deletes_reapplies_and_restarts_klusterlet_in_default_namespace():
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})
    managed = FakeCoreClient(
        {
            (
                MANAGED_CLUSTER_AGENT_NAMESPACE,
                BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            ): _hub_secret(
                "https://old.example:6443",
                name=BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            )
        }
    )
    apps = FakeAppsClient()

    def core_client_factory(kubeconfig: str, context: str | None = None):
        return secondary if kubeconfig == "hub" else managed

    def apps_client_factory(kubeconfig: str, context: str | None = None) -> FakeAppsClient:
        return apps

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a", "context": "ctx-a"}},
        pending_clusters=["cluster-a"],
        workers=1,
        core_client_factory=core_client_factory,
        apps_client_factory=apps_client_factory,
    )

    assert result["changed"] is True
    assert result["failed_clusters"] == []
    assert result["results"][0]["status"] == "remediated"
    assert (
        MANAGED_CLUSTER_AGENT_NAMESPACE,
        BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    ) in managed.deleted
    assert managed.created[0][0:2] == (
        MANAGED_CLUSTER_AGENT_NAMESPACE,
        BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    )
    assert apps.patched[0][0:2] == (MANAGED_CLUSTER_AGENT_NAMESPACE, "klusterlet")


def test_remediation_deletes_bootstrap_secret_from_manifest_namespace():
    custom_namespace = "custom-agent"
    import_secret = _import_secret("https://new.example:6443")
    import_yaml = base64.b64decode(import_secret["data"]["import.yaml"]).decode("utf-8")
    docs = list(yaml.safe_load_all(import_yaml))
    docs[0]["metadata"]["namespace"] = custom_namespace
    import_secret["data"]["import.yaml"] = base64.b64encode(yaml.safe_dump_all(docs).encode("utf-8")).decode("ascii")
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): import_secret})
    managed = FakeCoreClient(
        {(custom_namespace, BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME): _hub_secret("https://old.example:6443")}
    )
    apps = FakeAppsClient()

    def core_client_factory(kubeconfig: str, context: str | None = None):
        return secondary if kubeconfig == "hub" else managed

    def apps_client_factory(kubeconfig: str, context: str | None = None) -> FakeAppsClient:
        return apps

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        workers=1,
        core_client_factory=core_client_factory,
        apps_client_factory=apps_client_factory,
    )

    assert result["failed_clusters"] == []
    assert (custom_namespace, BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME) in managed.deleted
    assert managed.created[0][0:2] == (
        custom_namespace,
        BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    )
    assert apps.patched[0][0:2] == (custom_namespace, "klusterlet")


def test_remediation_reports_restart_failure():
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})
    managed = FakeCoreClient(
        {
            (
                MANAGED_CLUSTER_AGENT_NAMESPACE,
                BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            ): _hub_secret(
                "https://old.example:6443",
                name=BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
            )
        }
    )
    apps = FakeAppsClient(fail_patch=True)

    def core_client_factory(kubeconfig: str, context: str | None = None):
        return secondary if kubeconfig == "hub" else managed

    def apps_client_factory(kubeconfig: str, context: str | None = None) -> FakeAppsClient:
        return apps

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        workers=1,
        strict=False,
        core_client_factory=core_client_factory,
        apps_client_factory=apps_client_factory,
    )

    assert result["failed"] is False
    assert result["failed_clusters"] == ["cluster-a"]
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["steps"]["klusterlet_restarted"] == "failed"
    assert "restart failed" in result["results"][0]["reason"]


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
    calls = {"hub": 0}

    def core_client_factory(kubeconfig: str, context: str | None = None):
        if kubeconfig == "hub":
            calls["hub"] += 1
            return secondary
        return managed_clients[kubeconfig]

    def apps_client_factory(kubeconfig: str, context: str | None = None) -> FakeAppsClient:
        return apps

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
        apps_client_factory=apps_client_factory,
    )

    assert result["changed"] is True
    assert result["failed"] is False
    assert result["failed_clusters"] == ["cluster-b"]
    assert {item["cluster"]: item["status"] for item in result["results"]} == {
        "cluster-a": "remediated",
        "cluster-b": "failed",
    }
    assert calls["hub"] == 1


def test_remediation_strict_mode_fails_partial_failure():
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})

    def core_client_factory(kubeconfig: str, context: str | None = None) -> FakeCoreClient:
        return secondary if kubeconfig == "hub" else FakeCoreClient(fail_create=True)

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        pending_clusters=["cluster-a"],
        strict=True,
        core_client_factory=core_client_factory,
        apps_client_factory=_new_fake_apps_client,
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
    managed_threads: set[int] = set()

    def core_client_factory(kubeconfig: str, context: str | None = None):
        if kubeconfig == "hub":
            return secondary
        managed_threads.add(threading.get_ident())
        return FakeCoreClient()

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={
            "cluster-a": {"kubeconfig": "cluster-a"},
            "cluster-b": {"kubeconfig": "cluster-b"},
        },
        pending_clusters=["cluster-a", "cluster-b"],
        workers=1,
        core_client_factory=core_client_factory,
        apps_client_factory=_new_fake_apps_client,
    )

    assert result["workers"] == 1
    assert result["failed_clusters"] == []
    assert len(managed_threads) == 1


def test_probe_passes_bounded_request_timeout_to_secret_reads():
    secondary = FakeCoreClient({("cluster-a", "cluster-a-import"): _import_secret("https://new.example:6443")})
    managed = FakeCoreClient(
        {
            (
                MANAGED_CLUSTER_AGENT_NAMESPACE,
                HUB_KUBECONFIG_SECRET_NAME,
            ): _hub_secret("https://new.example:6443")
        }
    )

    def core_client_factory(kubeconfig: str, context: str | None = None) -> FakeCoreClient:
        return secondary if kubeconfig == "hub" else managed

    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"cluster-a": {"kubeconfig": "cluster-a"}},
        candidate_clusters=["cluster-a"],
        workers=1,
        request_timeout=7,
        core_client_factory=core_client_factory,
    )

    assert result["failed"] is False
    assert secondary.request_timeouts == [7]
    assert managed.request_timeouts == [7]


def test_worker_future_timeout_surfaces_as_failed_result():
    def slow_worker(cluster_name: str) -> dict:
        time.sleep(0.05)
        return {"cluster": cluster_name, "status": "verified"}

    results = ordered_bounded_map(
        ["cluster-a"],
        workers=2,
        fn=slow_worker,
        future_timeout=0.001,
        timeout_result=lambda cluster: {
            "cluster": cluster,
            "status": "failed",
            "reason": "worker_timeout",
        },
    )

    assert results == [{"cluster": "cluster-a", "status": "failed", "reason": "worker_timeout"}]


def test_single_worker_future_timeout_surfaces_as_failed_result():
    def slow_worker(cluster_name: str) -> dict:
        time.sleep(0.05)
        return {"cluster": cluster_name, "status": "verified"}

    started_at = time.monotonic()
    results = ordered_bounded_map(
        ["cluster-a"],
        workers=1,
        fn=slow_worker,
        future_timeout=0.001,
        timeout_result=lambda cluster: {
            "cluster": cluster,
            "status": "failed",
            "reason": "worker_timeout",
        },
    )

    assert time.monotonic() - started_at < 0.04
    assert results == [{"cluster": "cluster-a", "status": "failed", "reason": "worker_timeout"}]


def test_worker_future_timeout_uses_one_batch_deadline():
    def slow_worker(cluster_name: str) -> dict:
        time.sleep(0.05)
        return {"cluster": cluster_name, "status": "verified"}

    started_at = time.monotonic()
    results = ordered_bounded_map(
        ["cluster-a", "cluster-b", "cluster-c"],
        workers=3,
        fn=slow_worker,
        future_timeout=0.01,
        timeout_result=lambda cluster: {
            "cluster": cluster,
            "status": "failed",
            "reason": "worker_timeout",
        },
    )

    assert time.monotonic() - started_at < 0.04
    assert results == [
        {"cluster": "cluster-a", "status": "failed", "reason": "worker_timeout"},
        {"cluster": "cluster-b", "status": "failed", "reason": "worker_timeout"},
        {"cluster": "cluster-c", "status": "failed", "reason": "worker_timeout"},
    ]


class _ExitJson(Exception):
    def __init__(self, payload: dict):
        self.payload = payload


class _FailJson(Exception):
    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(str(payload))


def test_probe_module_main_allows_omitted_candidate_clusters(monkeypatch):
    captured = {}

    class FakeModule:
        params = {
            "secondary_hub": {"kubeconfig": "hub"},
            "managed_clusters": {"cluster-a": {"kubeconfig": "cluster-a"}},
            "candidate_clusters": None,
            "workers": 10,
        }

        def __init__(self, **kwargs):
            captured["argument_spec"] = kwargs["argument_spec"]

        def exit_json(self, **kwargs):
            raise _ExitJson(kwargs)

        def fail_json(self, **kwargs):
            raise _FailJson(kwargs)

    def fake_probe(**kwargs):
        captured["candidate_clusters"] = kwargs["candidate_clusters"]
        return {"changed": False, "failed": False}

    monkeypatch.setattr(probe_module, "AnsibleModule", FakeModule)
    monkeypatch.setattr(probe_module, "probe_klusterlet_connections", fake_probe)

    with pytest.raises(_ExitJson):
        probe_module.main()

    assert captured["argument_spec"]["candidate_clusters"].get("default") is None
    assert captured["candidate_clusters"] is None


def test_module_entrypoints_map_unexpected_errors_to_fail_json(monkeypatch):
    class FakeModule:
        params = {
            "secondary_hub": {"kubeconfig": "hub"},
            "managed_clusters": {},
            "candidate_clusters": None,
            "pending_clusters": [],
            "workers": 10,
            "strict": False,
        }
        check_mode = False

        def __init__(self, **kwargs):
            pass

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            raise _FailJson(kwargs)

    monkeypatch.setattr(probe_module, "AnsibleModule", FakeModule)
    monkeypatch.setattr(
        probe_module,
        "probe_klusterlet_connections",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(_FailJson, match="boom"):
        probe_module.main()

    monkeypatch.setattr(remediate_module, "AnsibleModule", FakeModule)
    monkeypatch.setattr(
        remediate_module,
        "remediate_klusterlets",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(_FailJson, match="boom"):
        remediate_module.main()
