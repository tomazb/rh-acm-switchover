"""#362: authoritative inventory drift must stop the real spoke mutation boundary.

Only hub/spoke transports and the convergence clock are fake. Context discovery,
classification, credential capture, the Kubernetes client loader and mutation methods run.
"""

import copy

import pytest
import yaml

from lib.endpoint_identity import parse_endpoint
from lib.exceptions import SwitchoverError
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager
from modules.post_activation import PostActivationVerification
from tests.test_post_activation_endpoint_identity import NEW_HUB, OLD_HUB, STEP, FakeSpoke, _import_secret, _kubeconfig

SPOKE = "https://api.a.example:443"
REPLACEMENT = "https://api.replacement.example:443"


def _mc(name="a", url=SPOKE, uid="original-uid"):
    return {
        "metadata": {"name": name, "uid": uid, "resourceVersion": "10"},
        "spec": {"managedClusterClientConfigs": [{"url": url}]},
    }


class Hub:
    context = "secondary-ctx"

    def __init__(self, items):
        self.items = items
        self.outcome = None
        self.reads = []
        self.events = []

    def list_managed_clusters_strict(self):
        self.events.append("inventory-read")
        outcome = self.outcome or StrictReadOutcome.from_items(copy.deepcopy(self.items))
        self.reads.append(outcome)
        return outcome

    def get_secret(self, namespace, name):
        return _import_secret(NEW_HUB)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    def make(items=None, observed=None, dry_run=False):
        items = items if items is not None else [_mc()]
        observed = observed or {"a": OLD_HUB}
        path = tmp_path / "kubeconfig.yaml"
        path.write_text(
            yaml.safe_dump(_kubeconfig([(f"ctx-{name}", name, f"https://api.{name}.example:443") for name in observed]))
        )
        monkeypatch.setenv("KUBECONFIG", str(path))
        hub = Hub(items)
        state = StateManager(str(tmp_path / "state.json"))
        pav = PostActivationVerification(hub, state, False, dry_run=dry_run)
        spokes = {name: FakeSpoke(server) for name, server in observed.items()}

        def client(api_client):
            configuration = api_client.configuration
            assert configuration.verify_ssl is True
            assert configuration.auth_settings()["BearerToken"]["value"] == "Bearer t"
            host = parse_endpoint(configuration.host)[0].host
            return spokes[host.split(".")[1]]

        monkeypatch.setattr("modules.post_activation.client.CoreV1Api", client)
        monkeypatch.setattr("modules.post_activation.client.AppsV1Api", client)
        monkeypatch.setattr(
            "modules.post_activation.wait_for_condition",
            lambda description, condition_fn, **kwargs: condition_fn().done,
        )

        def run():
            with pav._step(STEP) as should_run:
                if should_run:
                    pav._verify_klusterlet_connections()

        return pav, hub, state, spokes, run

    return make


def _inject(monkeypatch, pav, stage, change):
    method = "_klusterlet_cluster_targets" if stage == "selection" else "_check_klusterlet_targets"
    original = getattr(pav, method)

    def wrapped(*args):
        result = original(*args)
        if stage == "classification":
            assert result[1], "Real classification must identify a wrong-hub candidate"
        monkeypatch.setattr(pav, method, original)
        change()
        return result

    monkeypatch.setattr(pav, method, wrapped)


def _assert_refusal(run, state, spokes):
    # Capture the error so the first RED assertion shows actual mutation, rather than
    # only reporting an absent exception from a safety helper.
    error = None
    try:
        run()
    except SwitchoverError as exc:
        error = exc
    assert {name: spoke.mutations for name, spoke in spokes.items()} == {name: [] for name in spokes}
    assert not state.is_step_completed(STEP)
    assert error is not None
    return str(error)


@pytest.mark.parametrize("stage", ["selection", "classification"])
@pytest.mark.parametrize("fault", ["endpoint", "uid", "disappear", "missing_url", "read_failure"])
def test_authoritative_drift_refuses_before_any_spoke_write(scenario, monkeypatch, stage, fault):
    pav, hub, state, spokes, run = scenario()

    def change():
        if fault == "endpoint":
            hub.items[0]["spec"]["managedClusterClientConfigs"][0]["url"] = REPLACEMENT
            assert hub.items[0]["spec"]["managedClusterClientConfigs"][0]["url"] == REPLACEMENT
        elif fault == "uid":
            hub.items[0]["metadata"]["uid"] = "replacement-uid"
            assert hub.items[0]["metadata"]["uid"] == "replacement-uid"
        elif fault == "disappear":
            hub.items.clear()
            assert not hub.items
        elif fault == "missing_url":
            del hub.items[0]["spec"]["managedClusterClientConfigs"]
            assert hub.items[0]["spec"] == {}
        else:
            hub.outcome = StrictReadOutcome.error("incomplete_inventory")
            assert not hub.outcome.is_success
        hub.events.append("fault-present")

    _inject(monkeypatch, pav, stage, change)
    _assert_refusal(run, state, spokes)
    assert hub.events[-2:] == ["fault-present", "inventory-read"]
    assert len(hub.reads) == 2
    if fault == "read_failure":
        assert hub.reads[-1] is hub.outcome  # The failing read was actually exercised.


@pytest.mark.parametrize("peer_hub", [OLD_HUB, NEW_HUB])
def test_drifted_peer_blocks_the_whole_batch_including_verified_peers(scenario, monkeypatch, peer_hub):
    pav, hub, state, spokes, run = scenario(
        [_mc(), _mc("b", "https://api.b.example:443", "b-uid")], {"a": OLD_HUB, "b": peer_hub}
    )

    def change():
        hub.items[1]["metadata"]["uid"] = "b-replaced"
        assert hub.items[1]["metadata"]["uid"] == "b-replaced"

    _inject(monkeypatch, pav, "classification", change)
    _assert_refusal(run, state, spokes)
    assert all(spoke.reads == 1 for spoke in spokes.values())


def test_stable_inventory_allows_real_remediation_and_reverification(scenario):
    _, hub, state, spokes, run = scenario()
    run()
    assert spokes["a"].mutations == [("patch_secret", "bootstrap-hub-kubeconfig"), ("restart", "klusterlet")]
    assert spokes["a"].hub_server == NEW_HUB
    assert state.is_step_completed(STEP)
    assert len(hub.reads) == 2


def test_initial_selection_bypasses_a_preseeded_cache(scenario):
    pav, hub, state, spokes, run = scenario()
    pav._get_managed_clusters()
    hub.items = [_mc(url=REPLACEMENT)]
    assert hub.items[0]["spec"]["managedClusterClientConfigs"][0]["url"] == REPLACEMENT
    run()  # No matching context for the current target is the documented skip.
    assert spokes["a"].reads == 0
    assert spokes["a"].mutations == []
    assert state.is_step_completed(STEP)


def test_dry_run_does_not_read_inventory_write_or_checkpoint(scenario):
    _, hub, state, spokes, run = scenario(dry_run=True)
    run()
    assert hub.reads == []
    assert spokes["a"].mutations == []
    assert not state.is_step_completed(STEP)


@pytest.mark.parametrize("uid", [None, "", "   ", 17, {"invalid": "uid"}])
@pytest.mark.parametrize("stage", ["initial", "classification"])
def test_unavailable_uid_never_authorizes_a_mutating_batch(scenario, monkeypatch, uid, stage):
    pav, hub, state, spokes, run = scenario()

    def change():
        hub.items[0]["metadata"]["uid"] = uid
        assert hub.items[0]["metadata"]["uid"] == uid

    if stage == "initial":
        change()
    else:
        _inject(monkeypatch, pav, stage, change)
    _assert_refusal(run, state, spokes)


@pytest.mark.parametrize("observed", [NEW_HUB, "no-context"])
def test_no_remediation_does_not_newly_require_a_uid(scenario, observed):
    mapped = {"a": NEW_HUB} if observed == NEW_HUB else {"b": NEW_HUB}
    _, hub, state, spokes, run = scenario([_mc(uid=None)], mapped)
    run()
    assert all(not spoke.mutations for spoke in spokes.values())
    assert len(hub.reads) == 1
    assert state.is_step_completed(STEP)


@pytest.mark.parametrize(
    "fault",
    [
        "added",
        "duplicate",
        "missing_name",
        "bad_metadata",
        "missing_uid",
        "malformed_url",
        "changed_port",
        "changed_path",
        "changed_scheme",
    ],
)
def test_invalid_final_inventory_refuses_the_batch_without_echoing_input(scenario, monkeypatch, caplog, fault):
    pav, hub, state, spokes, run = scenario()

    def change():
        if fault == "added":
            hub.items.append(_mc("b", "https://api.b.example", "b-uid"))
            assert len(hub.items) == 2
        elif fault == "duplicate":
            hub.items.append(copy.deepcopy(hub.items[0]))
            assert hub.items[0] == hub.items[1]
        elif fault == "missing_name":
            del hub.items[0]["metadata"]["name"]
            assert "name" not in hub.items[0]["metadata"]
        elif fault == "bad_metadata":
            hub.items[0]["metadata"] = "private-metadata"
            assert hub.items[0]["metadata"] == "private-metadata"
        elif fault == "missing_uid":
            del hub.items[0]["metadata"]["uid"]
            assert "uid" not in hub.items[0]["metadata"]
        else:
            url = {
                "malformed_url": "https://private-user:private-password@api.a.example",
                "changed_port": "https://api.a.example:6443",
                "changed_path": "https://api.a.example/proxy",
                "changed_scheme": "http://api.a.example:443",
            }[fault]
            hub.items[0]["spec"]["managedClusterClientConfigs"][0]["url"] = url
            assert hub.items[0]["spec"]["managedClusterClientConfigs"][0]["url"] == url

    _inject(monkeypatch, pav, "classification", change)
    message = _assert_refusal(run, state, spokes)
    assert "retry" in message
    assert "private-" not in message + caplog.text


@pytest.mark.parametrize(
    "outcome", [StrictReadOutcome.error("list_failed"), StrictReadOutcome.crd_absent("api_absent")]
)
def test_unsuccessful_fresh_read_is_exercised_and_refuses_all_writes(scenario, monkeypatch, outcome):
    pav, hub, state, spokes, run = scenario()

    def change():
        hub.outcome = outcome
        assert not hub.outcome.is_success

    _inject(monkeypatch, pav, "classification", change)
    _assert_refusal(run, state, spokes)
    assert hub.reads[-1] is outcome


def test_exception_from_final_transport_is_sanitized(scenario, monkeypatch, caplog):
    pav, hub, state, spokes, run = scenario()
    original_read = hub.list_managed_clusters_strict

    def fail_after_first_read():
        if hub.reads:
            hub.events.append("failed-read-exercised")
            raise RuntimeError("private-transport-credential")
        return original_read()

    monkeypatch.setattr(hub, "list_managed_clusters_strict", fail_after_first_read)
    message = _assert_refusal(run, state, spokes)
    assert hub.events[-1] == "failed-read-exercised"
    assert "private-" not in message + caplog.text


def test_harmless_normalization_status_revision_order_and_local_churn_are_accepted(scenario, monkeypatch):
    pav, hub, state, spokes, run = scenario(
        [_mc(), _mc("b", "https://api.b.example:443", "b-uid"), _mc("local-cluster")],
        {"a": OLD_HUB, "b": NEW_HUB},
    )

    def change():
        hub.items[0]["spec"]["managedClusterClientConfigs"][0]["url"] = "https://API.A.EXAMPLE/"
        hub.items[0]["metadata"].update(resourceVersion="50", annotations={"arbitrary": "new"})
        hub.items[0]["status"] = {"conditions": []}
        hub.items[-1] = _mc("local-cluster", REPLACEMENT, "local-replaced")
        hub.items.reverse()
        assert hub.items[-1]["metadata"]["resourceVersion"] == "50"
        assert hub.items[-1]["spec"]["managedClusterClientConfigs"][0]["url"] == "https://API.A.EXAMPLE/"

    _inject(monkeypatch, pav, "classification", change)
    run()
    assert spokes["a"].hub_server == NEW_HUB
    assert len(spokes["a"].mutations) == 2
    assert spokes["b"].mutations == []
    assert state.is_step_completed(STEP)


def test_existing_retry_rediscovers_after_a_refused_batch(scenario, monkeypatch):
    pav, hub, state, spokes, run = scenario()

    def change():
        hub.items[0]["metadata"]["uid"] = "replacement-uid"
        assert hub.items[0]["metadata"]["uid"] == "replacement-uid"

    _inject(monkeypatch, pav, "classification", change)
    _assert_refusal(run, state, spokes)
    assert pav._spoke_material_dir is None
    run()  # A new selection proves the replacement object, retaining the same endpoint.
    assert len(spokes["a"].mutations) == 2
    assert state.is_step_completed(STEP)
    assert len(hub.reads) == 4


def test_a_no_context_peer_still_participates_in_mutation_authorization(scenario, monkeypatch):
    pav, hub, state, spokes, run = scenario([_mc(), _mc("b", "https://api.b.example", "b-uid")])

    def change():
        hub.items[1]["spec"]["managedClusterClientConfigs"][0]["url"] = REPLACEMENT
        assert hub.items[1]["spec"]["managedClusterClientConfigs"][0]["url"] == REPLACEMENT

    _inject(monkeypatch, pav, "classification", change)
    _assert_refusal(run, state, spokes)


@pytest.mark.parametrize("fault", ["duplicate", "missing_name", "bad_metadata"])
def test_invalid_initial_inventory_cannot_authorize_a_batch(scenario, fault):
    items = [_mc()]
    if fault == "duplicate":
        items.append(_mc())
        assert items[0] == items[1]
    elif fault == "missing_name":
        items.append({"metadata": {"uid": "bad-object"}})
        assert "name" not in items[-1]["metadata"]
    else:
        items.append({"metadata": "private-metadata"})
        assert items[-1]["metadata"] == "private-metadata"
    _, _, state, spokes, run = scenario(items)
    _assert_refusal(run, state, spokes)
