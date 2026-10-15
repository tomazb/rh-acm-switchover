"""R4-06 PR 2 acceptance for the Python klusterlet path (amendment 2026-10-15 §2, §7 PR 2).

These drive the real KUBECONFIG loader, the real spoke-context discovery and the real
expected-hub extraction over a fake spoke that counts client constructions, reads and
mutations. They prove that no unproven cluster is inspected past the point of refusal,
that nothing unproven is ever remediated - including a genuine wrong-hub peer in the same
run - and that a repair writing the import-secret server re-verifies.
"""

import base64
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml

from lib.exceptions import SwitchoverError
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager
from modules.post_activation import PostActivationVerification

NEW_HUB = "https://api.new-hub.example:6443"
OLD_HUB = "https://api.old-hub.example:6443"
STEP = "verify_klusterlet_connections"


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _hub_kubeconfig(server):
    return yaml.safe_dump({"clusters": [{"name": "hub", "cluster": {"server": server}}]})


def _import_secret(server):
    manifest = yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "bootstrap-hub-kubeconfig", "namespace": "open-cluster-management-agent"},
            "data": {"kubeconfig": _b64(_hub_kubeconfig(server))},
        }
    )
    return {"data": {"import.yaml": _b64(manifest)}}


class FakeSpoke:
    """One managed cluster: its klusterlet hub secret, and every call made against it."""

    def __init__(self, hub_server):
        self.hub_server = hub_server
        self.reads = 0
        self.mutations = []

    def read_namespaced_secret(self, name, namespace, **_kwargs):
        self.reads += 1
        if name == "hub-kubeconfig-secret":
            return SimpleNamespace(data={"kubeconfig": _b64(_hub_kubeconfig(self.hub_server))})
        return SimpleNamespace(data={})

    def patch_namespaced_secret(self, name, namespace, body, **_kwargs):
        self.mutations.append(("patch_secret", name))
        # The klusterlet re-bootstraps against the server the bootstrap secret now names.
        embedded = yaml.safe_load(base64.b64decode(body["data"]["kubeconfig"]))
        self.hub_server = embedded["clusters"][0]["cluster"]["server"]

    def create_namespaced_secret(self, namespace, body, **_kwargs):
        self.mutations.append(("create_secret", namespace))

    def patch_namespaced_deployment(self, name, namespace, body, **_kwargs):
        self.mutations.append(("restart", name))


class Fleet:
    """The spokes, keyed by kubeconfig context, plus a construction log."""

    def __init__(self, spokes):
        self.spokes = spokes
        self.constructions = []

    def build(self, context_name, api_url):
        self.constructions.append((context_name, api_url))
        spoke = self.spokes[context_name]
        return spoke, spoke

    def total_reads(self):
        return sum(spoke.reads for spoke in self.spokes.values())

    def total_mutations(self):
        return sum(len(spoke.mutations) for spoke in self.spokes.values())


def _kubeconfig(entries):
    """entries: (context, cluster_entry, server[, user]) tuples, in merge order."""
    clusters, contexts, users = [], [], [{"name": "admin", "user": {"token": "t"}}]
    seen = set()
    for entry in entries:
        context, cluster, server = entry[:3]
        user = entry[3] if len(entry) > 3 else "admin"
        if cluster not in seen:
            clusters.append({"name": cluster, "cluster": {"server": server}})
            seen.add(cluster)
        contexts.append({"name": context, "context": {"cluster": cluster, "user": user}})
        if user not in {u["name"] for u in users}:
            users.append({"name": user, "user": {"token": "t2"}})
    return {"apiVersion": "v1", "clusters": clusters, "contexts": contexts, "users": users}


def _managed_cluster(name, url=None, configs=None):
    spec = {"managedClusterClientConfigs": configs if configs is not None else ([{"url": url}] if url else [])}
    return {"metadata": {"name": name}, "spec": spec}


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Build a verifier over a real kubeconfig file, a ManagedCluster list and import secrets."""

    def _run(kubeconfig, managed_clusters, imports=None):
        path = tmp_path / "kubeconfig.yaml"
        path.write_text(yaml.safe_dump(kubeconfig))
        monkeypatch.setenv("KUBECONFIG", str(path))
        secondary = Mock()
        secondary.context = "secondary-ctx"
        secondary.list_managed_clusters_strict.return_value = StrictReadOutcome.from_items(managed_clusters)
        imports = imports if imports is not None else {}
        secondary.get_secret.side_effect = lambda namespace, name: imports.get(namespace, _import_secret(NEW_HUB))
        state = StateManager(str(tmp_path / "state.json"))
        pav = PostActivationVerification(secondary_client=secondary, state_manager=state, has_observability=False)
        return pav, state

    return _run


def _verify(pav, fleet):
    with patch.object(pav, "_build_managed_cluster_clients", side_effect=fleet.build), patch(
        "modules.post_activation.wait_for_condition",
        side_effect=lambda description, condition_fn, **kwargs: condition_fn().done,
    ):
        pav._verify_cluster_connections_klusterlet_only()


@pytest.fixture(autouse=True)
def klusterlet_only(monkeypatch):
    """Run only the klusterlet half of the connection step, inside its real checkpointed step."""

    def _only(self):
        with self.state.step(STEP, logger) as should_run:
            if should_run:
                self._verify_klusterlet_connections()

    logger = logging.getLogger("acm_switchover")
    monkeypatch.setattr(PostActivationVerification, "_verify_cluster_connections_klusterlet_only", _only, raising=False)


# ---------------------------------------------------------------- acceptance 2 and 5


@pytest.mark.parametrize(
    "observed",
    [
        "https://api.new-hub.example:443",
        "https://api.new-hub.example",
        "http://api.new-hub.example:6443",
        "https://api.new-hub.example:6443/proxy",
    ],
)
def test_a_same_host_mismatch_is_unverified_and_blocks_a_genuine_wrong_hub_peer(run, observed):
    """Decision 2 + amendment §2.8: operator review, and zero mutation for every cluster in the run."""
    kubeconfig = _kubeconfig([("ctx-a", "ka", "https://api.a:6443"), ("ctx-b", "kb", "https://api.b:6443")])
    pav, state = run(
        kubeconfig, [_managed_cluster("a", "https://api.a:6443"), _managed_cluster("b", "https://api.b:6443")]
    )
    fleet = Fleet({"ctx-a": FakeSpoke(observed), "ctx-b": FakeSpoke(OLD_HUB)})

    with pytest.raises(SwitchoverError, match=r"a \(endpoint_mismatch_unverified\)"):
        _verify(pav, fleet)

    assert fleet.total_mutations() == 0
    assert not state.is_step_completed(STEP)


def test_a_genuine_wrong_hub_alone_still_remediates_and_reverifies(run):
    """The existing contract for a different host is kept, and the import-secret server re-verifies."""
    kubeconfig = _kubeconfig([("ctx-a", "ka", "https://api.a:6443")])
    pav, state = run(kubeconfig, [_managed_cluster("a", "https://api.a:6443")])
    fleet = Fleet({"ctx-a": FakeSpoke(OLD_HUB)})

    _verify(pav, fleet)

    assert ("patch_secret", "bootstrap-hub-kubeconfig") in fleet.spokes["ctx-a"].mutations
    assert fleet.spokes["ctx-a"].hub_server == NEW_HUB
    assert state.is_step_completed(STEP)


def test_the_import_secret_server_is_the_expected_hub_even_when_the_operator_uses_another_form(run):
    """Amendment §2.4: an operator reaching the hub at https://api.hub while the import secret says :6443."""
    kubeconfig = _kubeconfig(
        [("ctx-a", "ka", "https://api.a:6443"), ("secondary-ctx", "hub", "https://api.new-hub.example")]
    )
    pav, state = run(kubeconfig, [_managed_cluster("a", "https://api.a:6443")])
    fleet = Fleet({"ctx-a": FakeSpoke(NEW_HUB)})

    _verify(pav, fleet)

    assert fleet.total_mutations() == 0
    assert state.is_step_completed(STEP)


# ---------------------------------------------------------------- acceptance 3 and 4


@pytest.mark.parametrize(
    ("managed_cluster", "reason"),
    [
        (_managed_cluster("a"), "endpoint_unavailable"),
        (_managed_cluster("a", configs=[{"url": ""}]), "endpoint_unavailable"),
        (
            {"metadata": {"name": "a"}, "spec": {"managedClusterClientConfigs": "https://api.a:6443"}},
            "endpoint_unavailable",
        ),
        (_managed_cluster("a", "api.a:6443"), "endpoint_malformed"),
        (_managed_cluster("a", "https://user:hunter2@api.a:6443"), "endpoint_malformed"),
    ],
)
def test_no_usable_api_url_fails_with_zero_lookups_constructions_and_reads(run, managed_cluster, reason, caplog):
    """Decision 3: a context named exactly like the cluster is never selected."""
    kubeconfig = _kubeconfig([("a", "ka", "https://api.a:6443")])
    pav, state = run(kubeconfig, [managed_cluster])
    fleet = Fleet({"a": FakeSpoke(OLD_HUB)})
    caplog.set_level(logging.DEBUG, logger="acm_switchover")

    with patch("modules.post_activation.select_spoke_context") as select:
        with pytest.raises(SwitchoverError, match=rf"a \({reason}\)") as excinfo:
            _verify(pav, fleet)

    select.assert_not_called()
    pav.secondary.get_secret.assert_not_called()
    assert fleet.constructions == [] and fleet.total_reads() == 0 and fleet.total_mutations() == 0
    assert not state.is_step_completed(STEP)
    assert "hunter2" not in caplog.text and "hunter2" not in str(excinfo.value)


def test_a_retry_re_runs_the_step_and_passes_once_the_url_is_registered(run):
    kubeconfig = _kubeconfig([("ctx-a", "ka", "https://api.a:6443")])
    pav, state = run(kubeconfig, [_managed_cluster("a")])
    fleet = Fleet({"ctx-a": FakeSpoke(NEW_HUB)})
    with pytest.raises(SwitchoverError, match="endpoint_unavailable"):
        _verify(pav, fleet)
    assert not state.is_step_completed(STEP)

    # A retry is a fresh invocation over the same state file.
    retry, retry_state = run(kubeconfig, [_managed_cluster("a", "https://api.a:6443")])
    _verify(retry, fleet)
    assert retry_state.is_step_completed(STEP)


@pytest.mark.parametrize(
    ("entries", "reason"),
    [
        # Two distinct cluster entries for one server: different CA/TLS material, one URL.
        ([("ctx-1", "k1", "https://api.a:6443"), ("ctx-2", "k2", "https://api.a:6443")], "context_ambiguous"),
        # Same canonical host, different port: never a near-match selection.
        ([("ctx-1", "k1", "https://api.a:443")], "endpoint_mismatch_unverified"),
        ([("ctx-1", "k1", "http://api.a:6443")], "endpoint_mismatch_unverified"),
    ],
)
def test_discovery_refusals_never_construct_a_client(run, entries, reason):
    pav, state = run(_kubeconfig(entries), [_managed_cluster("a", "https://api.a:6443")])
    fleet = Fleet({})
    with pytest.raises(SwitchoverError, match=rf"a \({reason}\)"):
        _verify(pav, fleet)

    assert fleet.constructions == []
    pav.secondary.get_secret.assert_not_called()


def test_multiple_users_on_one_cluster_entry_select_the_first_context(run):
    """`managed1:operator` / `managed1:validator` style per-role contexts keep working."""
    entries = [
        ("a:operator", "ka", "https://api.a:6443", "operator"),
        ("a:validator", "ka", "https://api.a:6443", "validator"),
    ]
    pav, state = run(_kubeconfig(entries), [_managed_cluster("a", "https://API.a:6443/")])
    fleet = Fleet({"a:operator": FakeSpoke(NEW_HUB), "a:validator": FakeSpoke(NEW_HUB)})

    _verify(pav, fleet)

    assert fleet.constructions == [("a:operator", "https://API.a:6443/")]
    assert state.is_step_completed(STEP)


def test_an_unrelated_ipv6_literal_is_no_context_not_a_near_match(run, caplog):
    """The old regex collapsed every IPv6 literal to `[2001`; discovery compares canonical hosts."""
    entries = [("ctx-other", "k-other", "https://[2001:db8::2]:6443")]
    pav, state = run(_kubeconfig(entries), [_managed_cluster("a", "https://[2001:db8::1]:6443")])
    fleet = Fleet({})
    caplog.set_level(logging.INFO, logger="acm_switchover")

    _verify(pav, fleet)

    assert fleet.constructions == []
    assert "skipped for 1 cluster(s)" in caplog.text
    assert state.is_step_completed(STEP)


def test_a_conflicting_duplicate_refuses_the_whole_merge(run, tmp_path, monkeypatch):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(yaml.safe_dump(_kubeconfig([("ctx-a", "ka", "https://api.a:6443")])))
    second.write_text(yaml.safe_dump(_kubeconfig([("ctx-a", "ka", "https://api.evil:6443")])))
    pav, state = run(_kubeconfig([]), [_managed_cluster("a", "https://api.a:6443")])
    monkeypatch.setenv("KUBECONFIG", f"{first}:{second}")
    fleet = Fleet({"ctx-a": FakeSpoke(OLD_HUB)})

    with pytest.raises(SwitchoverError, match="kubeconfig_duplicate_conflict"):
        _verify(pav, fleet)

    assert fleet.constructions == [] and fleet.total_mutations() == 0


@pytest.mark.parametrize(
    ("imports", "reason"),
    [
        ({"a": None}, "expected_endpoint_unavailable"),
        ({"a": {"data": {"import.yaml": _b64("kind: ConfigMap\n")}}}, "expected_endpoint_unavailable"),
        ({"a": _import_secret("api.new-hub.example:6443")}, "expected_endpoint_malformed"),
    ],
)
def test_an_unusable_expected_endpoint_is_decided_before_any_spoke_client(run, imports, reason):
    pav, state = run(
        _kubeconfig([("ctx-a", "ka", "https://api.a:6443")]), [_managed_cluster("a", "https://api.a:6443")], imports
    )
    fleet = Fleet({"ctx-a": FakeSpoke(OLD_HUB)})

    with pytest.raises(SwitchoverError, match=rf"a \({reason}\)"):
        _verify(pav, fleet)

    assert fleet.constructions == [] and fleet.total_reads() == 0


# ---------------------------------------------------------------- acceptance 8 and 9


def test_a_client_bound_elsewhere_is_unverified_with_zero_reads(run):
    pav, state = run(
        _kubeconfig([("ctx-a", "ka", "https://api.a:6443")]), [_managed_cluster("a", "https://api.a:6443")]
    )
    spoke = FakeSpoke(OLD_HUB)
    api_client = Mock()
    api_client.configuration.host = "https://api.elsewhere:6443"

    with patch("modules.post_activation.config.new_client_from_config", return_value=api_client), patch(
        "modules.post_activation.client.CoreV1Api", return_value=spoke
    ) as core:
        with pytest.raises(SwitchoverError, match=r"a \(client_endpoint_mismatch\)"):
            pav._verify_cluster_connections_klusterlet_only()

    core.assert_not_called()
    assert spoke.reads == 0 and spoke.mutations == []


def test_reconnect_never_writes_through_a_client_bound_elsewhere(run):
    """The remediation path builds its own client; it is checked before any write (PR 2 interim)."""
    pav, _state = run(_kubeconfig([]), [])
    spoke = FakeSpoke(OLD_HUB)
    api_client = Mock()
    api_client.configuration.host = "https://api.elsewhere:6443"
    pav.secondary.get_secret.side_effect = None
    pav.secondary.get_secret.return_value = _import_secret(NEW_HUB)

    with patch("modules.post_activation.config.new_client_from_config", return_value=api_client), patch(
        "modules.post_activation.client.CoreV1Api", return_value=spoke
    ), patch("modules.post_activation.client.AppsV1Api", return_value=spoke):
        assert pav._force_klusterlet_reconnect("a", "ctx-a", "https://api.a:6443", NEW_HUB) is False

    assert spoke.reads == 0 and spoke.mutations == []


def test_endpoint_logs_carry_only_canonical_values(run, caplog):
    pav, _state = run(
        _kubeconfig([("ctx-a", "ka", "https://api.a:6443")]), [_managed_cluster("a", "https://api.a:6443")]
    )
    fleet = Fleet({"ctx-a": FakeSpoke("https://api.new-hub.example:443")})
    caplog.set_level(logging.DEBUG, logger="acm_switchover")

    with pytest.raises(SwitchoverError):
        _verify(pav, fleet)

    hub_lines = [record.args for record in caplog.records if record.msg.startswith("Cluster %s klusterlet hub")]
    assert hub_lines == [
        (
            "a",
            "https://api.new-hub.example:443",
            "https://api.new-hub.example:6443",
            "unverified",
            " (endpoint_mismatch_unverified)",
        )
    ]


@pytest.mark.parametrize(
    "replacement",
    [
        _import_secret("https://alice:hunter2@api.new-hub.example:6443"),
        _import_secret(OLD_HUB),
        {"data": {"import.yaml": _b64("kind: ConfigMap\n")}},
    ],
)
def test_an_import_secret_replaced_after_verification_is_never_applied(run, replacement, caplog):
    """Codex PR 2 review: the reconnect path re-reads the manifest; it must still carry the validated hub."""
    pav, _state = run(_kubeconfig([]), [])
    spoke = FakeSpoke(OLD_HUB)
    pav.secondary.get_secret.side_effect = None
    pav.secondary.get_secret.return_value = replacement
    caplog.set_level(logging.DEBUG, logger="acm_switchover")

    with patch.object(pav, "_build_managed_cluster_clients", return_value=(spoke, spoke)) as build:
        assert pav._force_klusterlet_reconnect("a", "ctx-a", "https://api.a:6443", NEW_HUB) is False

    build.assert_not_called()
    assert spoke.mutations == []
    assert "hunter2" not in caplog.text


def test_a_loader_error_on_reconnect_is_logged_without_its_text(run, caplog):
    """The client loader re-reads files; a constructor error quotes the offending credential."""
    pav, _state = run(_kubeconfig([]), [])
    pav.secondary.get_secret.side_effect = None
    pav.secondary.get_secret.return_value = _import_secret(NEW_HUB)
    caplog.set_level(logging.DEBUG, logger="acm_switchover")

    with patch.object(pav, "_build_managed_cluster_clients", side_effect=ValueError("token: SECRETLEAK")):
        assert pav._force_klusterlet_reconnect("a", "ctx-a", "https://api.a:6443", NEW_HUB) is False

    assert "SECRETLEAK" not in caplog.text
    assert "ValueError" in caplog.text


def test_the_import_manifest_scan_stops_at_the_bootstrap_secret():
    """code-review PR 2: the rest of the manifest is never parsed for the endpoint lookup."""
    from modules.post_activation import _bootstrap_server_from_import

    bootstrap = base64.b64decode(_import_secret(NEW_HUB)["data"]["import.yaml"]).decode()
    manifest = bootstrap + "\n---\nkind: Deployment\nspec: [unclosed\n"

    assert _bootstrap_server_from_import(manifest) == (True, NEW_HUB)
    assert _bootstrap_server_from_import("kind: [unclosed\n---\n" + bootstrap) == (False, None)
