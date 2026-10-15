# SPDX-License-Identifier: MIT
"""R4-06 PR 3 acceptance for the collection klusterlet modules (amendment 2026-10-15 §6, §7 PR 3).

The real snapshot spoke factories and the real Kubernetes loader build every spoke client;
only the API objects wrapping those clients are fakes, so these tests see exactly what each
client was built with. Credential files are replaced and deleted between remediation's two
phases, inside one ``acm_klusterlet_remediate`` invocation.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Callable

import pytest
import yaml

import ansible_collections.tomazb.acm_switchover.plugins.module_utils.klusterlet as klusterlet_utils
import ansible_collections.tomazb.acm_switchover.plugins.module_utils.kubeconfig_snapshot as snapshot_module
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    HUB_KUBECONFIG_SECRET_NAME,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.klusterlet import (
    probe_klusterlet_connections,
    remediate_klusterlets,
)

NEW_HUB = "https://api.new-hub.example:6443"
OLD_HUB = "https://api.old-hub.example:6443"
SPOKE = "https://api.spoke-a.example:6443"
CA = b"-----BEGIN CERTIFICATE-----\nMIIBcaptured\n-----END CERTIFICATE-----\n"


def _b64_yaml(payload) -> str:
    return base64.b64encode(yaml.safe_dump(payload).encode()).decode()


def _import_secret(server) -> dict:
    bootstrap = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME, "namespace": MANAGED_CLUSTER_AGENT_NAMESPACE},
        "data": {"kubeconfig": _b64_yaml({"clusters": [{"cluster": {"server": server}}]})},
    }
    return {"data": {"import.yaml": base64.b64encode(yaml.safe_dump_all([bootstrap]).encode()).decode()}}


class _NotFound(Exception):
    status = 404


class Hub:
    def __init__(self, urls):
        self.urls = urls

    def read_namespaced_secret(self, name, namespace, **_kwargs):
        return _import_secret(NEW_HUB)

    def reader_factory(self, kubeconfig, context, request_timeout):
        return lambda name: {"spec": {"managedClusterClientConfigs": [{"url": self.urls[name]}]}}


def _hub_factory(urls):
    def build(kubeconfig, context=None):
        return Hub(urls)

    return build


class SpokeApi:
    """One managed cluster's API; every real ApiClient wrapping it is recorded."""

    def __init__(self, hub_server):
        self.secrets = {(MANAGED_CLUSTER_AGENT_NAMESPACE, HUB_KUBECONFIG_SECRET_NAME): self._hub(hub_server)}
        self.writes = []
        self.on_first_read = None
        self.slow_restart = 0.0
        self.material_during_restart = None
        self.material_check: Callable[[], bool | None] = lambda: None

    @staticmethod
    def _hub(server):
        return {"data": {"kubeconfig": _b64_yaml({"clusters": [{"cluster": {"server": server}}]})}}

    def read_namespaced_secret(self, name, namespace, **_kwargs):
        if self.on_first_read:
            hook, self.on_first_read = self.on_first_read, None
            hook()
        secret = self.secrets.get((namespace, name))
        if secret is None:
            raise _NotFound()
        return secret

    def patch_namespaced_secret(self, name, namespace, body, **_kwargs):
        if (namespace, name) not in self.secrets:
            raise _NotFound()
        self.writes.append("patch_secret")
        self.secrets[(namespace, name)] = body

    def create_namespaced_secret(self, namespace, body, **_kwargs):
        self.writes.append("create_secret")
        self.secrets[(namespace, body["metadata"]["name"])] = body

    def patch_namespaced_deployment(self, name, namespace, body, **_kwargs):
        if self.slow_restart:
            time.sleep(self.slow_restart)
            self.material_during_restart = self.material_check()
        self.writes.append("restart")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Real snapshot factories and loader; fake API objects that record the built clients."""
    built = []
    dirs = []
    spokes: dict = {}
    real_new_dir = snapshot_module.new_private_dir

    def new_dir():
        path = real_new_dir()
        dirs.append(path)
        return path

    def wrap(api_client):
        configuration = api_client.configuration
        ca = None
        if configuration.ssl_ca_cert:
            with open(configuration.ssl_ca_cert, "rb") as handle:
                ca = handle.read()
        built.append(
            {
                "host": configuration.host,
                "bearer": configuration.auth_settings().get("BearerToken", {}).get("value"),
                "ca": ca,
                "ca_dir": os.path.dirname(configuration.ssl_ca_cert) if configuration.ssl_ca_cert else None,
            }
        )
        spoke = spokes[configuration.host]
        spoke.api_client = api_client  # the real client the API object wraps
        return spoke

    import kubernetes.client

    monkeypatch.setattr(klusterlet_utils, "new_private_dir", new_dir)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda api_client: wrap(api_client))
    monkeypatch.setattr(kubernetes.client, "AppsV1Api", lambda api_client: wrap(api_client))

    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "ca.crt").write_bytes(CA)
    (creds / "token").write_bytes(b"captured-token\n")

    def kubeconfig(
        name="a.kubeconfig", server=SPOKE, user=None, context="spoke-admin", current="spoke-admin", extra=None
    ):
        document = {
            "clusters": [{"name": "spoke", "cluster": {"server": server, "certificate-authority": "creds/ca.crt"}}],
            "contexts": [{"name": context, "context": {"cluster": "spoke", "user": "admin"}}],
            "users": [{"name": "admin", "user": user or {"tokenFile": "creds/token"}}],
            "current-context": current,
        }
        for key, values in (extra or {}).items():
            document[key] = document[key] + values
        path = tmp_path / name
        path.write_text(yaml.safe_dump(document))
        return str(path)

    return {"built": built, "dirs": dirs, "spokes": spokes, "creds": creds, "kubeconfig": kubeconfig}


def _status(result, cluster="a"):
    item = next(item for item in result["results"] if item["cluster"] == cluster)
    return item["status"], item.get("reason")


def test_every_client_in_one_remediation_uses_the_captured_content(world):
    """PR 3 #1: replace and delete the credential files between selection and mutation."""
    spoke = SpokeApi(OLD_HUB)
    world["spokes"][SPOKE] = spoke
    creds = world["creds"]

    def tamper():
        (creds / "token").write_bytes(b"attacker-token\n")
        (creds / "ca.crt").unlink()

    spoke.on_first_read = tamper
    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"a": {"kubeconfig": world["kubeconfig"]()}},
        pending_clusters=["a"],
        core_client_factory=_hub_factory({"a": SPOKE}),
        managed_cluster_reader_factory=Hub({"a": SPOKE}).reader_factory,
    )

    assert _status(result) == ("remediated", None)
    assert spoke.writes == ["create_secret", "restart"]  # no bootstrap secret yet: patch 404, then create
    assert len(world["built"]) == 2  # the core client (prepare) and the workload client
    for record in world["built"]:
        assert record["bearer"] == "Bearer captured-token"
        assert record["ca"] == CA
        assert record["ca_dir"] == world["dirs"][0]
    assert not os.path.exists(world["dirs"][0])


def test_the_mapped_context_wins_over_current_context(world):
    """PR 3 #2: the context the mapping names is the one the client is built with."""
    other = {"name": "other", "cluster": {"server": "https://api.other.example:6443"}}
    other_context = {"name": "other-admin", "context": {"cluster": "other", "user": "admin"}}
    path = world["kubeconfig"](current="other-admin", extra={"clusters": [other], "contexts": [other_context]})
    world["spokes"][SPOKE] = SpokeApi(NEW_HUB)

    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"a": {"kubeconfig": path, "context": "spoke-admin"}},
        core_client_factory=_hub_factory({"a": SPOKE}),
        managed_cluster_reader_factory=Hub({"a": SPOKE}).reader_factory,
    )

    assert _status(result) == ("verified", None)
    assert [record["host"] for record in world["built"]] == [SPOKE]


@pytest.mark.parametrize(
    ("user", "reason"),
    [
        ({"exec": {"command": "get-token"}}, "exec_credential_unsupported"),
        ({"auth-provider": {"name": "oidc"}}, "auth_provider_unsupported"),
        ({"token": ""}, "credential_empty"),
    ],
)
def test_a_refused_credential_builds_no_client_and_blocks_the_batch(world, user, reason):
    """PR 3 #3: the refused cluster gets no client; a plain peer is built but never mutated."""
    b_server = "https://api.spoke-b.example:6443"
    path_a = world["kubeconfig"]("a.kubeconfig", user=user)
    path_b = world["kubeconfig"]("b.kubeconfig", server=b_server, user={"token": "plain-token"})
    world["spokes"][b_server] = spoke_b = SpokeApi(OLD_HUB)
    urls = {"a": SPOKE, "b": b_server}

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"a": {"kubeconfig": path_a}, "b": {"kubeconfig": path_b}},
        pending_clusters=["a", "b"],
        core_client_factory=_hub_factory(urls),
        managed_cluster_reader_factory=Hub(urls).reader_factory,
    )

    assert _status(result, "a") == ("unverified", reason)
    assert _status(result, "b") == ("failed", "remediation_blocked_by_unproven_cluster")
    assert [record["host"] for record in world["built"]] == [b_server, b_server]
    assert spoke_b.writes == [] and result["failed"] is True
    assert "get-token" not in repr(result) and "plain-token" not in repr(result)


def test_a_late_mutation_is_reported_and_its_material_outlives_it(world):
    """PR 3 #3/#4: a worker past its deadline keeps its credential files until it finishes."""
    spoke = SpokeApi(OLD_HUB)
    spoke.slow_restart = 1.5
    spoke.material_check = lambda: bool(world["dirs"]) and bool(os.listdir(world["dirs"][0]))
    world["spokes"][SPOKE] = spoke

    result = remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"a": {"kubeconfig": world["kubeconfig"]()}},
        pending_clusters=["a"],
        future_timeout=1,
        core_client_factory=_hub_factory({"a": SPOKE}),
        managed_cluster_reader_factory=Hub({"a": SPOKE}).reader_factory,
    )

    item = result["results"][0]
    assert item["status"] == "failed" and item["reason"].startswith("worker_timeout")
    assert item["changed"] is True and item["completed_after_timeout"] is True
    assert item["steps"]["klusterlet_restarted"] == "ok"
    assert result["failed"] is True and result["changed"] is True
    assert spoke.material_during_restart is True
    assert not os.path.exists(world["dirs"][0])
    assert "_prepared" not in item


def test_the_private_directory_is_removed_when_the_probe_fails(world):
    world["spokes"][SPOKE] = SpokeApi("https://api.new-hub.example:443")
    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"a": {"kubeconfig": world["kubeconfig"]()}},
        core_client_factory=_hub_factory({"a": SPOKE}),
        managed_cluster_reader_factory=Hub({"a": SPOKE}).reader_factory,
    )
    assert _status(result) == ("unverified", "endpoint_mismatch_unverified")
    assert world["dirs"] and not os.path.exists(world["dirs"][0])
    assert "captured-token" not in repr(result)
