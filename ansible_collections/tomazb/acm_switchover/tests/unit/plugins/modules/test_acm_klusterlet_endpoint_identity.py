# SPDX-License-Identifier: MIT
"""R4-06 PR 2 acceptance for the collection klusterlet modules (amendment 2026-10-15 §2.3-§2.8).

A mapped cluster is inspected only after the invocation proves which cluster the mapped
context reaches; every refusal before that point builds no spoke client, and nothing
unproven - nor any genuine wrong-hub peer in the same batch - is ever mutated.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME,
    HUB_KUBECONFIG_SECRET_NAME,
    KLUSTERLET_MAX_WORKERS,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.klusterlet import (
    normalize_workers,
    probe_klusterlet_connections,
    remediate_klusterlets,
)

NEW_HUB = "https://api.new-hub.example:6443"
OLD_HUB = "https://api.old-hub.example:6443"
SPOKE = "https://api.spoke-a.example:6443"


def _b64_yaml(payload) -> str:
    return base64.b64encode(yaml.safe_dump(payload).encode()).decode()


def _hub_secret(server) -> dict:
    return {"data": {"kubeconfig": _b64_yaml({"clusters": [{"cluster": {"server": server}}]})}}


def _import_secret(server) -> dict:
    bootstrap = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME, "namespace": MANAGED_CLUSTER_AGENT_NAMESPACE},
        "data": {"kubeconfig": _b64_yaml({"clusters": [{"cluster": {"server": server}}]})},
    }
    return {"data": {"import.yaml": base64.b64encode(yaml.safe_dump_all([bootstrap]).encode()).decode()}}


def _managed_cluster(url) -> dict:
    return {"metadata": {"name": "a"}, "spec": {"managedClusterClientConfigs": [{"url": url}] if url else []}}


class Spoke:
    """A managed cluster's API as seen through one client: secrets, reads and writes."""

    def __init__(self, hub_server=OLD_HUB, host=SPOKE):
        self.api_client = SimpleNamespace(configuration=SimpleNamespace(host=host))
        self.secrets = {}
        if hub_server is not None:
            self.secrets[(MANAGED_CLUSTER_AGENT_NAMESPACE, HUB_KUBECONFIG_SECRET_NAME)] = _hub_secret(hub_server)
        self.reads = 0
        self.writes = []

    def read_namespaced_secret(self, name, namespace, **_kwargs):
        self.reads += 1
        secret = self.secrets.get((namespace, name))
        if secret is None:
            raise _NotFound()
        return secret

    def patch_namespaced_secret(self, name, namespace, body, **_kwargs):
        self.writes.append(("patch_secret", namespace, name))
        if (namespace, name) not in self.secrets:
            raise _NotFound()
        self.secrets[(namespace, name)] = body

    def create_namespaced_secret(self, namespace, body, **_kwargs):
        self.writes.append(("create_secret", namespace, body["metadata"]["name"]))
        self.secrets[(namespace, body["metadata"]["name"])] = body

    def patch_namespaced_deployment(self, name, namespace, body, **_kwargs):
        self.writes.append(("restart", namespace, name))


class _NotFound(Exception):
    status = 404


class Hub:
    """The secondary hub: import secrets and ManagedCluster objects, read fresh per call."""

    def __init__(self, urls=None, imports=None):
        self.urls = dict(urls or {"a": SPOKE})
        self.imports = dict(imports or {})
        self.managed_cluster_reads = []
        self.api_client = SimpleNamespace(configuration=SimpleNamespace(host=NEW_HUB))

    def read_namespaced_secret(self, name, namespace, **_kwargs):
        secret = self.imports.get(namespace, _import_secret(NEW_HUB))
        if secret is None:
            raise _NotFound()
        return secret

    def reader_factory(self, kubeconfig, context, request_timeout):
        def read(name):
            self.managed_cluster_reads.append(name)
            value = self.urls.get(name, "__absent__")
            if value == "__absent__":
                return None
            if isinstance(value, Exception):
                raise value
            return _managed_cluster(value)

        return read


class Fleet:
    """Spoke clients by mapped kubeconfig path, and every construction made."""

    def __init__(self, hub, spokes):
        self.hub = hub
        self.spokes = spokes
        self.constructions = []

    def core(self, kubeconfig, context=None):
        if kubeconfig == "hub":
            return self.hub
        self.constructions.append((kubeconfig, context))
        return self.spokes[kubeconfig]

    def apps(self, kubeconfig, context=None):
        self.constructions.append((kubeconfig, context))
        return self.spokes[kubeconfig]

    def reads(self):
        return sum(spoke.reads for spoke in self.spokes.values())

    def writes(self):
        return [write for spoke in self.spokes.values() for write in spoke.writes]


def _kubeconfig(server=SPOKE, contexts=("spoke-admin",), current="spoke-admin", extra=None):
    document = {
        "apiVersion": "v1",
        "clusters": [{"name": "spoke", "cluster": {"server": server}}],
        "contexts": [{"name": name, "context": {"cluster": "spoke", "user": "admin"}} for name in contexts],
        "users": [{"name": "admin", "user": {"token": "t"}}],
    }
    if current is not None:
        document["current-context"] = current
    document.update(extra or {})
    return document


@pytest.fixture
def mapped(tmp_path):
    def _write(name="a.kubeconfig", document=None, text=None):
        path = tmp_path / name
        path.write_text(text if text is not None else yaml.safe_dump(document or _kubeconfig()))
        return str(path)

    return _write


def _probe(fleet, managed_clusters, candidates=("a",)):
    return probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters=managed_clusters,
        candidate_clusters=list(candidates),
        workers=2,
        core_client_factory=fleet.core,
        managed_cluster_reader_factory=fleet.hub.reader_factory,
    )


def _remediate(fleet, managed_clusters, pending=("a",), strict=False):
    return remediate_klusterlets(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters=managed_clusters,
        pending_clusters=list(pending),
        workers=2,
        strict=strict,
        core_client_factory=fleet.core,
        apps_client_factory=fleet.apps,
        managed_cluster_reader_factory=fleet.hub.reader_factory,
    )


def _status(result, cluster="a"):
    item = next(item for item in result["results"] if item["cluster"] == cluster)
    return item["status"], item.get("reason")


# ---------------------------------------------------------------- hub classification (acceptance 2)


@pytest.mark.parametrize(
    "observed",
    [
        "https://api.new-hub.example:443",
        "https://api.new-hub.example",
        "http://api.new-hub.example:6443",
        "https://api.new-hub.example:6443/proxy",
    ],
)
def test_a_same_host_mismatch_is_unverified_and_never_remediated(mapped, observed):
    path = mapped()
    fleet = Fleet(Hub(), {path: Spoke(hub_server=observed)})

    probe = _probe(fleet, {"a": {"kubeconfig": path}})
    assert _status(probe) == ("unverified", "endpoint_mismatch_unverified")
    assert probe["failed"] is True and probe["failed_clusters"] == ["a"] and probe["unverified_clusters"] == ["a"]

    remediation = _remediate(fleet, {"a": {"kubeconfig": path}})
    assert _status(remediation) == ("unverified", "endpoint_mismatch_unverified")
    assert remediation["changed"] is False and fleet.writes() == []


def test_a_genuine_different_host_still_remediates(mapped):
    path = mapped()
    fleet = Fleet(Hub(), {path: Spoke(hub_server=OLD_HUB)})

    remediation = _remediate(fleet, {"a": {"kubeconfig": path}})

    assert _status(remediation) == ("remediated", None)
    assert ("restart", MANAGED_CLUSTER_AGENT_NAMESPACE, "klusterlet") in fleet.writes()


def test_default_port_and_case_differences_verify(mapped):
    path = mapped()
    fleet = Fleet(Hub(imports={"a": _import_secret("https://API.New-Hub.example:6443/")}), {path: Spoke(NEW_HUB)})

    assert _status(_probe(fleet, {"a": {"kubeconfig": path}})) == ("verified", None)


# ---------------------------------------------------------------- URL and identity (acceptance 3, 4, 7)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("__absent__", "endpoint_unavailable"),
        (None, "endpoint_unavailable"),
        ("api.spoke-a.example:6443", "endpoint_malformed"),
        ("https://user:hunter2@api.spoke-a.example:6443", "endpoint_malformed"),
    ],
)
def test_no_usable_url_refuses_before_opening_the_mapped_file(url, reason):
    """The mapped path does not even exist: the URL check comes first (amendment §2.6 rule 2)."""
    fleet = Fleet(Hub(urls={"a": url}), {})

    probe = _probe(fleet, {"a": {"kubeconfig": "/nonexistent/a.kubeconfig"}})
    remediation = _remediate(fleet, {"a": {"kubeconfig": "/nonexistent/a.kubeconfig"}})

    assert _status(probe) == ("failed", reason)
    assert _status(remediation) == ("failed", reason)
    assert fleet.constructions == [] and fleet.writes() == []
    assert "hunter2" not in repr(probe) + repr(remediation)


def test_a_failed_managed_cluster_read_is_a_failure_not_an_absent_url(mapped):
    path = mapped()
    error = RuntimeError("hub unreachable")
    fleet = Fleet(Hub(urls={"a": error}), {path: Spoke()})

    status, reason = _status(_probe(fleet, {"a": {"kubeconfig": path}}))

    assert (status, reason) == ("failed", "RuntimeError")
    assert fleet.constructions == []


@pytest.mark.parametrize(
    ("server", "reason"),
    [
        ("https://api.other.example:6443", "target_identity_mismatch"),
        ("https://api.spoke-a.example:443", "endpoint_mismatch_unverified"),
        ("http://api.spoke-a.example:6443", "endpoint_mismatch_unverified"),
        ("api.spoke-a.example:6443", "endpoint_malformed"),
    ],
)
def test_the_mapped_server_must_prove_target_identity(mapped, server, reason):
    path = mapped(document=_kubeconfig(server=server))
    fleet = Fleet(Hub(), {path: Spoke()})

    assert _status(_probe(fleet, {"a": {"kubeconfig": path}})) == ("failed", reason)
    assert _status(_remediate(fleet, {"a": {"kubeconfig": path}})) == ("failed", reason)
    assert fleet.constructions == [] and fleet.writes() == []


def test_a_context_named_like_the_cluster_proves_nothing(mapped):
    """Decision 3: a name-only match never selects a target."""
    path = mapped(document=_kubeconfig(server="https://api.other.example:6443", contexts=("a",), current="a"))
    fleet = Fleet(Hub(), {path: Spoke()})

    assert _status(_probe(fleet, {"a": {"kubeconfig": path, "context": "a"}})) == ("failed", "target_identity_mismatch")
    assert fleet.constructions == []


@pytest.mark.parametrize(
    ("document", "text", "context", "reason"),
    [
        (None, "clusters: [\n", None, "kubeconfig_load_failed (yaml_invalid)"),
        (None, "clusters: []\nclusters: []\n", None, "kubeconfig_load_failed (duplicate_yaml_key)"),
        (
            _kubeconfig(
                extra={
                    "clusters": [
                        {"name": "spoke", "cluster": {"server": SPOKE}},
                        {"name": "spoke", "cluster": {"server": "https://api.evil.example:6443"}},
                    ]
                }
            ),
            None,
            None,
            "kubeconfig_duplicate_conflict",
        ),
        (_kubeconfig(), None, "missing-context", "context_missing"),
        (_kubeconfig(current=None), None, None, "context_ambiguous"),
    ],
)
def test_an_invalid_or_ambiguous_mapped_file_fails_that_cluster_uninspected(mapped, document, text, context, reason):
    path = mapped(document=document, text=text)
    managed = {"kubeconfig": path}
    if context:
        managed["context"] = context
    fleet = Fleet(Hub(), {path: Spoke()})

    assert _status(_probe(fleet, {"a": managed})) == ("failed", reason)
    assert fleet.constructions == []


def test_a_path_with_a_colon_is_refused(mapped):
    path = mapped()
    fleet = Fleet(Hub(), {})

    assert _status(_probe(fleet, {"a": {"kubeconfig": f"{path}:{path}"}})) == ("failed", "kubeconfig_path_unsupported")
    assert fleet.constructions == []


@pytest.mark.parametrize(
    ("imports", "reason"),
    [
        ({"a": None}, "expected_endpoint_unavailable"),
        (
            {"a": {"data": {"import.yaml": base64.b64encode(b"kind: ConfigMap\n").decode()}}},
            "expected_endpoint_unavailable",
        ),
        ({"a": _import_secret("api.new-hub.example:6443")}, "expected_endpoint_malformed"),
    ],
)
def test_an_unusable_expected_endpoint_builds_no_spoke_client(mapped, imports, reason):
    path = mapped()
    fleet = Fleet(Hub(imports=imports), {path: Spoke()})

    assert _status(_probe(fleet, {"a": {"kubeconfig": path}})) == ("unverified", reason)
    assert fleet.constructions == []


# ---------------------------------------------------------------- client binding (acceptance 8)


def test_a_client_bound_elsewhere_is_never_read_or_written(mapped):
    path = mapped()
    spoke = Spoke(hub_server=OLD_HUB, host="https://api.elsewhere.example:6443")
    fleet = Fleet(Hub(), {path: spoke})

    assert _status(_probe(fleet, {"a": {"kubeconfig": path}})) == ("unverified", "client_endpoint_mismatch")
    assert _status(_remediate(fleet, {"a": {"kubeconfig": path}})) == ("unverified", "client_endpoint_mismatch")
    assert spoke.reads == 0 and spoke.writes == []


def test_the_selected_context_is_passed_explicitly_to_the_client_loader(mapped):
    path = mapped(document=_kubeconfig(contexts=("spoke-admin", "other"), current="spoke-admin"))
    fleet = Fleet(Hub(), {path: Spoke(NEW_HUB)})

    _probe(fleet, {"a": {"kubeconfig": path}})

    assert fleet.constructions == [(path, "spoke-admin")]


# ---------------------------------------------------------------- remediation barrier (acceptance 5, 7)


def test_an_unproven_cluster_blocks_a_genuine_wrong_hub_peer(mapped):
    """Amendment §2.8: any refusal in the batch means zero mutation for every cluster."""
    path_a = mapped("a.kubeconfig")
    path_b = mapped("b.kubeconfig", document=_kubeconfig(server="https://api.spoke-b.example:6443"))
    hub = Hub(urls={"a": SPOKE, "b": "https://api.spoke-b.example:6443"})
    fleet = Fleet(
        hub,
        {
            path_a: Spoke(hub_server="https://api.new-hub.example:443"),
            path_b: Spoke(hub_server=OLD_HUB, host="https://api.spoke-b.example:6443"),
        },
    )
    managed = {"a": {"kubeconfig": path_a}, "b": {"kubeconfig": path_b}}

    result = _remediate(fleet, managed, pending=("a", "b"))

    assert _status(result, "a") == ("unverified", "endpoint_mismatch_unverified")
    assert _status(result, "b") == ("failed", "remediation_blocked_by_unproven_cluster")
    assert result["changed"] is False and fleet.writes() == []
    assert result["failed_clusters"] == ["a", "b"]


def test_bare_names_authorize_nothing(mapped):
    """A direct call naming a verified cluster mutates nothing: only a fresh `wrong_hub` does."""
    path = mapped()
    fleet = Fleet(Hub(), {path: Spoke(hub_server=NEW_HUB)})

    result = _remediate(fleet, {"a": {"kubeconfig": path}, "u": {}}, pending=("a", "u", "not-mapped"))

    assert _status(result, "a")[0] == "verified"
    assert _status(result, "u") == ("skipped", "no_managed_cluster_kubeconfig")
    assert _status(result, "not-mapped") == ("skipped", "no_managed_cluster_kubeconfig")
    assert fleet.writes() == [] and result["changed"] is False


def test_a_pending_cluster_without_a_hub_secret_is_not_remediated(mapped):
    path = mapped()
    fleet = Fleet(Hub(), {path: Spoke(hub_server=None)})

    result = _remediate(fleet, {"a": {"kubeconfig": path}})

    assert _status(result) == ("skipped", "current_hub_secret_missing")
    assert fleet.writes() == []


def test_remediation_reads_the_url_afresh_and_refuses_a_stale_target(mapped):
    """The ManagedCluster URL changed between the probe and remediate invocations."""
    path = mapped()
    hub = Hub()
    fleet = Fleet(hub, {path: Spoke(hub_server=OLD_HUB)})
    assert _status(_probe(fleet, {"a": {"kubeconfig": path}}))[0] == "wrong_hub"

    hub.urls["a"] = "https://api.replacement.example:6443"
    result = _remediate(fleet, {"a": {"kubeconfig": path}})

    assert _status(result) == ("failed", "target_identity_mismatch")
    assert fleet.writes() == []
    assert hub.managed_cluster_reads == ["a", "a"]


# ---------------------------------------------------------------- sanitization and worker cap (acceptance 9, 10)


def test_results_carry_only_canonical_endpoints(mapped):
    path = mapped()
    fleet = Fleet(Hub(), {path: Spoke(hub_server="https://alice:hunter2@api.old-hub.example:6443")})

    probe = _probe(fleet, {"a": {"kubeconfig": path}})
    item = probe["results"][0]

    assert (item["status"], item["reason"]) == ("unverified", "endpoint_malformed")
    assert item["current_hub_server"] == "<rejected:userinfo>"
    assert item["expected_hub_server"] == "https://api.new-hub.example:6443"
    assert "hunter2" not in repr(probe) and "alice" not in repr(probe)


def test_the_worker_count_is_capped():
    assert normalize_workers(KLUSTERLET_MAX_WORKERS) == KLUSTERLET_MAX_WORKERS
    with pytest.raises(ValueError, match=rf"at most {KLUSTERLET_MAX_WORKERS}"):
        normalize_workers(KLUSTERLET_MAX_WORKERS + 1)


def test_the_cap_bounds_a_large_batch(mapped):
    """Many mapped clusters run through at most the capped number of workers."""
    names = [f"c{index}" for index in range(KLUSTERLET_MAX_WORKERS * 2)]
    hub = Hub(urls={name: SPOKE for name in names})
    spokes = {}
    managed = {}
    for name in names:
        path = mapped(f"{name}.kubeconfig")
        spokes[path] = Spoke(NEW_HUB)
        managed[name] = {"kubeconfig": path}
    fleet = Fleet(hub, spokes)

    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters=managed,
        workers=KLUSTERLET_MAX_WORKERS,
        core_client_factory=fleet.core,
        managed_cluster_reader_factory=hub.reader_factory,
    )

    assert result["workers"] == KLUSTERLET_MAX_WORKERS
    assert sorted(result["verified_clusters"]) == sorted(names)


def test_unreadable_hub_kubeconfig_yaml_is_failed_without_parser_text(mapped):
    """The collection keeps `failed` for unreadable embedded YAML, with a fixed reason (no parser text)."""
    path = mapped()
    spoke = Spoke(hub_server=None)
    spoke.secrets[(MANAGED_CLUSTER_AGENT_NAMESPACE, HUB_KUBECONFIG_SECRET_NAME)] = {
        "data": {"kubeconfig": base64.b64encode(b"token: s3cr3t-value\n  bad: [\n").decode()}
    }
    fleet = Fleet(Hub(), {path: spoke})

    probe = _probe(fleet, {"a": {"kubeconfig": path}})

    assert _status(probe) == ("failed", "hub_kubeconfig_unreadable")
    assert "s3cr3t" not in repr(probe)


def test_an_unreadable_import_manifest_is_an_unavailable_expected_endpoint(mapped):
    path = mapped()
    unreadable = {"data": {"import.yaml": base64.b64encode(b"token: s3cr3t-value\n  bad: [\n").decode()}}
    fleet = Fleet(Hub(imports={"a": unreadable}), {path: Spoke()})

    probe = _probe(fleet, {"a": {"kubeconfig": path}})

    assert _status(probe) == ("unverified", "expected_endpoint_unavailable")
    assert "s3cr3t" not in repr(probe) and fleet.constructions == []


def test_a_refused_batch_fails_the_module_even_without_strict(mapped):
    path = mapped()
    fleet = Fleet(Hub(), {path: Spoke(hub_server="https://api.new-hub.example:443")})

    result = _remediate(fleet, {"a": {"kubeconfig": path}}, strict=False)

    assert result["failed"] is True and "a" in result["msg"]
    assert fleet.writes() == []


def test_a_loader_error_never_reaches_the_result():
    """The client loader re-reads files; a constructor error quotes the offending credential."""

    def leaking_factory(kubeconfig, context=None):
        if kubeconfig == "hub":
            return Hub()
        raise ValueError("could not determine a constructor for token SECRETLEAK")

    hub = Hub()
    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"a": {"kubeconfig": "/nonexistent"}},
        workers=1,
        core_client_factory=leaking_factory,
        managed_cluster_reader_factory=hub.reader_factory,
    )
    assert "SECRETLEAK" not in repr(result)


def test_a_spoke_loader_error_is_a_sanitized_construction_failure(mapped):
    path = mapped()
    hub = Hub()

    def factory(kubeconfig, context=None):
        if kubeconfig == "hub":
            return hub
        raise ValueError("token: SECRETLEAK")

    result = probe_klusterlet_connections(
        secondary_hub={"kubeconfig": "hub"},
        managed_clusters={"a": {"kubeconfig": path}},
        workers=1,
        core_client_factory=factory,
        managed_cluster_reader_factory=hub.reader_factory,
    )

    assert _status(result) == ("failed", "client_construction_failed (ValueError)")
    assert "SECRETLEAK" not in repr(result)


def test_a_refusal_carries_location_provenance_without_content(mapped):
    path = mapped(
        text=yaml.safe_dump(
            {
                "clusters": [
                    {"name": "spoke", "cluster": {"server": SPOKE}},
                    {"name": "spoke", "cluster": {"server": "https://api.evil.example:6443"}},
                ],
                "contexts": [{"name": "c", "context": {"cluster": "spoke"}}],
                "current-context": "c",
            }
        )
    )
    fleet = Fleet(Hub(), {})

    item = _probe(fleet, {"a": {"kubeconfig": path}})["results"][0]

    assert (item["status"], item["reason"]) == ("failed", "kubeconfig_duplicate_conflict")
    assert item["detail"] == f"{path}: cluster 'spoke' at [0] (variant 1), [1] (variant 2)"
    assert "evil" not in repr(item)


def test_a_home_relative_mapped_path_is_expanded_for_validation_and_construction(mapped, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".kube").mkdir(parents=True)
    (home / ".kube" / "spoke").write_text(yaml.safe_dump(_kubeconfig()))
    monkeypatch.setenv("HOME", str(home))
    expanded = str(home / ".kube" / "spoke")
    fleet = Fleet(Hub(), {expanded: Spoke(NEW_HUB)})

    assert _status(_probe(fleet, {"a": {"kubeconfig": "~/.kube/spoke"}})) == ("verified", None)
    assert fleet.constructions == [(expanded, "spoke-admin")]
