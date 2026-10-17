"""#352: real checkpoint reload and post-activation retry over synthetic transport.

A legacy marker must not suppress the current gate. The fixture's provenance is
in fixtures/legacy_klusterlet_checkpoint.md. No Kubernetes connection is allowed.
"""

import atexit
import base64
import copy
import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException

import acm_switchover as cli
import modules.post_activation as post
from lib.migration_journal import validate_migration_journal
from lib.migration_post_activation import require_completed_restore
from lib.operation_runners import SwitchoverRunnerHooks, run_switchover_impl
from lib.strict_read import StrictReadOutcome
from lib.utils import Phase, StateManager

LEGACY = "verify_klusterlet_connections"
CURRENT = "verify_klusterlet_connections_v2"
NAMES = ["cluster-a", "cluster-b"]
NEW = "https://api.new-hub.example:6443"
OLD = "https://api.old-hub.example:6443"
FIXTURE = Path(__file__).parent / "fixtures" / "legacy_klusterlet_checkpoint.json"
LOGGER = logging.getLogger("acm_switchover")
pytestmark = pytest.mark.unit


def _encoded_config(server):
    raw = yaml.safe_dump(
        {"apiVersion": "v1", "kind": "Config", "clusters": [{"name": "hub", "cluster": {"server": server}}]}
    )
    return base64.b64encode(raw.encode()).decode()


class FinalizationSelected(Exception):
    """Stop before any finalization work or old-hub operation."""


class SyntheticHub:
    def __init__(self, context="secondary-ctx", *, fallback=False):
        self.context = context
        self.fallback = fallback
        self.inventory_reads = 0
        self.import_reads = 0
        self.inventory_error = False
        self.import_error = False
        self.cleanup_error = False
        self.items = [
            {
                "metadata": {"name": name, "uid": f"synthetic-uid-{name}"},
                "spec": {"managedClusterClientConfigs": [{"url": f"https://api.{name}.example:6443"}]},
            }
            for name in NAMES
        ]
        self.outcome = None
        self.inventory_exception = None
        self.reads = []
        self.events = []

    def get_cluster_identity(self):
        return {"context": self.context, "cluster_uid": f"synthetic-physical-{self.context}"}

    def list_managed_clusters_strict(self):
        self.inventory_reads += 1
        self.events.append("inventory")
        if self.inventory_exception is not None:
            self.reads.append(self.inventory_exception)
            raise self.inventory_exception
        if self.outcome is not None:
            self.reads.append(self.outcome)
            return self.outcome
        if self.inventory_error or (self.cleanup_error and self.inventory_reads >= 3):
            return StrictReadOutcome.error("synthetic denied inventory")
        ready = not (self.fallback and self.inventory_reads == 1)
        outcome = StrictReadOutcome.from_items(
            [
                {
                    **copy.deepcopy(item),
                    "status": {
                        "conditions": [
                            {"type": "ManagedClusterConditionAvailable", "status": "True" if ready else "False"},
                            {"type": "ManagedClusterJoined", "status": "True"},
                        ]
                    },
                }
                for item in self.items
            ]
        )
        self.reads.append(outcome)
        return outcome

    def get_secret(self, namespace, name):
        assert namespace in NAMES and name == f"{namespace}-import"
        self.import_reads += 1
        if self.import_error:
            raise ApiException(status=403, reason="SyntheticDenied")
        manifest = yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "bootstrap-hub-kubeconfig", "namespace": "open-cluster-management-agent"},
                "data": {"kubeconfig": _encoded_config(NEW)},
            }
        )
        return {"data": {"import.yaml": base64.b64encode(manifest.encode()).decode()}}


class Retry:
    """One persisted retry input, with fakes only at external I/O boundaries."""

    def __init__(self, tmp_path, monkeypatch, *, fallback=False, marker=True):
        self.path = tmp_path / "state.json"
        self.original = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert validate_migration_journal(self.original["config"]["migration_backups"])
        assert require_completed_restore(self.original["config"]["migration_backups"])
        if fallback:
            self.original["completed_steps"] = [
                s for s in self.original["completed_steps"] if s["name"] != "verify_clusters_connected"
            ]
        if not marker:
            self.original["completed_steps"] = [s for s in self.original["completed_steps"] if s["name"] != LEGACY]
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        self.kubeconfig = tmp_path / "kubeconfig.yaml"
        self.kubeconfig.write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "v1",
                    "kind": "Config",
                    "clusters": [
                        {"name": name, "cluster": {"server": f"https://api.{name}.example:6443"}} for name in NAMES
                    ],
                    "contexts": [{"name": name, "context": {"cluster": name, "user": "fixture"}} for name in NAMES],
                    "users": [{"name": "fixture", "user": {"token": "synthetic-test-only-token"}}],
                }
            ),
            encoding="utf-8",
        )
        self.hub = SyntheticHub(fallback=fallback)
        self.spoke_reads = []
        self.patches = []
        self.restarts = []
        self.classifications = []
        self.polls = []
        self.hub_state = "wrong"
        self.repair = "denied"
        self.finalization = False
        self.identity_bound = False
        self.state = None
        self.after_classification = None
        monkeypatch.setenv("KUBECONFIG", str(self.kubeconfig))
        monkeypatch.setattr(socket.socket, "connect", self.forbid_network)
        monkeypatch.setattr(client.CoreV1Api, "read_namespaced_secret", lambda api, **kw: self.read_secret(api, **kw))
        monkeypatch.setattr(client.CoreV1Api, "patch_namespaced_secret", lambda api, **kw: self.patch_secret(api, **kw))
        monkeypatch.setattr(client.AppsV1Api, "patch_namespaced_deployment", lambda api, **kw: self.restart(api, **kw))
        monkeypatch.setattr(post, "wait_for_condition", self.poll_once)
        classify = post.classify_hub

        def observe_classification(expected, observed):
            result = classify(expected, observed)
            self.classifications.append(result[0])
            self.hub.events.append("classification")
            return result

        monkeypatch.setattr(post, "classify_hub", observe_classification)
        classify_targets = post.PostActivationVerification._check_klusterlet_targets

        def observe_batch(verification, *args):
            result = classify_targets(verification, *args)
            if self.after_classification is not None:
                self.after_classification()
            return result

        monkeypatch.setattr(post.PostActivationVerification, "_check_klusterlet_targets", observe_batch)

    @staticmethod
    def forbid_network(*_args, **_kwargs):
        raise AssertionError("network forbidden in non-live regression")

    def poll_once(self, _description=None, condition_fn=None, **kwargs):
        done = condition_fn().done
        self.polls.append((_description or kwargs.get("description"), done, len(self.spoke_reads)))
        return done

    @staticmethod
    def no_prior_phase(*_args):
        raise AssertionError("post-activation retry must not replay an earlier phase")

    def select_finalization(self, *_args):
        self.finalization = True
        raise FinalizationSelected

    def spoke_name(self, api):
        host = api.api_client.configuration.host
        assert host in [f"https://api.{name}.example:6443" for name in NAMES]
        return next(name for name in NAMES if f"api.{name}." in host)

    def read_secret(self, api, *, name, namespace, **_kwargs):
        spoke = self.spoke_name(api)
        assert namespace == "open-cluster-management-agent"
        assert name in ("hub-kubeconfig-secret", "bootstrap-hub-kubeconfig")
        self.spoke_reads.append((spoke, name))
        if (
            self.hub_state == "failed_read"
            or (self.repair == "recheck-fails" and spoke in self.restarts)
            or (self.repair == "partial" and spoke == "cluster-a" and name == "bootstrap-hub-kubeconfig")
        ):
            raise ApiException(status=403, reason="SyntheticDenied")
        converged = self.hub_state == "correct" or (self.repair == "converged" and spoke in self.restarts)
        return SimpleNamespace(data={"kubeconfig": _encoded_config(NEW if converged else OLD)})

    def patch_secret(self, api, *, name, namespace, body, **_kwargs):
        spoke = self.spoke_name(api)
        assert namespace == "open-cluster-management-agent" and name == "bootstrap-hub-kubeconfig"
        self.patches.append(spoke)
        self.hub.events.append("patch")
        if self.repair == "denied" or (self.repair == "partial" and spoke == "cluster-b"):
            raise ApiException(status=403, reason="SyntheticDenied")
        return SimpleNamespace(data=body["data"])

    def restart(self, api, *, name, namespace, body, **_kwargs):
        assert name == "klusterlet" and namespace == "open-cluster-management-agent"
        self.restarts.append(self.spoke_name(api))
        self.hub.events.append("restart")
        return SimpleNamespace()

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def run(self, *, dry_run=False):
        self.finalization = False
        self.state = StateManager(str(self.path))
        args = SimpleNamespace(dry_run=dry_run, validate_only=False, force=False, min_managed_clusters=None)
        hooks = SwitchoverRunnerHooks(
            preflight_handler=self.no_prior_phase,
            primary_prep_handler=self.no_prior_phase,
            activation_handler=self.no_prior_phase,
            post_activation_handler=cli._run_phase_post_activation,
            finalization_handler=self.select_finalization,
            fail_phase=cli._fail_phase,
            fail_unexpected_phase_state=cli._fail_unexpected_phase_state,
            on_phase_failure=lambda *_args: None,
        )
        primary = SyntheticHub("primary-ctx")
        try:
            self.state.ensure_contexts("primary-ctx", "secondary-ctx")
            cli._bind_runtime_hub_identities(args, self.state, primary, self.hub)
            self.identity_bound = True
            return run_switchover_impl(args, self.state, primary, self.hub, LOGGER, hooks=hooks)
        except FinalizationSelected:
            return True
        finally:
            self.state._release_run_lock()


def _names(stored):
    return [s["name"] for s in stored["completed_steps"]]


def _assert_history_preserved(retry, stored):
    for key in ("version", "tool_version", "created_at", "hub_identities", "contexts"):
        assert stored[key] == retry.original[key]
    assert stored["completed_steps"][: len(retry.original["completed_steps"])] == retry.original["completed_steps"]
    assert stored["errors"][: len(retry.original["errors"])] == retry.original["errors"]
    for key, value in retry.original["config"].items():
        if key != "migration_backups":
            assert stored["config"][key] == value


@pytest.mark.parametrize(
    "fallback", [False, True], ids=["completed-connectivity-direct-gate", "initial-timeout-fallback"]
)
def test_legacy_marker_reaches_wrong_hub_gate_before_any_completion(tmp_path, monkeypatch, fallback):
    retry = Retry(tmp_path, monkeypatch, fallback=fallback)
    assert LEGACY in _names(retry.stored()) and CURRENT not in _names(retry.stored())

    result = retry.run()

    stored = retry.stored()
    # RED on the original implementation: no spoke/import/patch reads, a new journal
    # completion, and finalization selected even though both synthetic spokes are wrong.
    assert retry.spoke_reads, {
        "result": result,
        "finalization_selected": retry.finalization,
        "post_activation": stored["config"]["migration_backups"]["post_activation"],
        "spoke_reads": retry.spoke_reads,
        "import_reads": retry.hub.import_reads,
    }
    assert retry.identity_bound
    assert result is False and retry.finalization is False
    assert set(retry.classifications) == {"wrong_hub"}
    assert sorted(retry.patches) == NAMES
    assert CURRENT not in _names(stored)
    assert stored["config"]["migration_backups"]["post_activation"]["completed_at"] is None


@pytest.mark.parametrize("hub_state", ["wrong", "correct"])
def test_first_ready_poll_precedes_legacy_gate_and_cannot_authorize_wrong_hub(tmp_path, monkeypatch, hub_state):
    retry = Retry(tmp_path, monkeypatch)
    raw = retry.stored()
    raw["completed_steps"] = [step for step in raw["completed_steps"] if step["name"] != "verify_clusters_connected"]
    assert LEGACY in _names(raw) and CURRENT not in _names(raw)
    retry.path.write_text(json.dumps(raw), encoding="utf-8")
    retry.original = copy.deepcopy(raw)
    retry.hub_state = hub_state

    result = retry.run()

    # The initial real readiness predicate ran and accepted the strict inventory
    # before any spoke was inspected. Joined/Available alone cannot earn v2.
    assert retry.polls[0] == ("ManagedCluster connections", True, 0)
    assert "verify_clusters_connected" in _names(retry.stored())
    if hub_state == "wrong":
        assert result is False
        assert len(retry.spoke_reads) == 4
        assert set(retry.classifications) == {"wrong_hub"}
        _assert_refused(retry)
    else:
        assert result is True and retry.finalization
        assert len(retry.spoke_reads) == 2
        assert retry.classifications == ["verified", "verified"]
        assert retry.patches == retry.restarts == []
        assert _names(retry.stored()).count(CURRENT) == 1
        assert retry.polls[1] == ("ManagedCluster connections", True, 2)
        assert retry.stored()["config"]["migration_backups"]["post_activation"]["completed_at"]


def _assert_refused(retry):
    stored = retry.stored()
    _assert_history_preserved(retry, stored)
    assert retry.finalization is False
    assert CURRENT not in _names(stored)
    assert LEGACY in _names(stored)
    assert stored["config"]["migration_backups"]["post_activation"]["completed_at"] is None
    assert stored["config"]["migration_backups"]["backups"] == retry.original["config"]["migration_backups"]["backups"]
    assert stored["config"]["migration_backups"]["restore"] == retry.original["config"]["migration_backups"]["restore"]


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("marker", [False, True], ids=["new-checkpoint", "legacy-checkpoint"])
def test_current_gate_success_is_durable_once_and_retains_history(tmp_path, monkeypatch, fallback, marker):
    retry = Retry(tmp_path, monkeypatch, fallback=fallback, marker=marker)
    retry.hub_state = "correct"
    assert retry.run() is True
    stored = retry.stored()
    _assert_history_preserved(retry, stored)
    assert retry.finalization
    assert len(retry.spoke_reads) == 2  # Fallback must not invoke the gate a second time.
    assert retry.patches == retry.restarts == []
    assert retry.classifications == ["verified", "verified"]
    assert _names(stored).count(CURRENT) == 1
    assert stored["completed_steps"][: len(retry.original["completed_steps"])] == retry.original["completed_steps"]
    assert stored["version"] == "1.0"
    journal = stored["config"]["migration_backups"]
    assert journal["schema_version"] == 2 and journal["post_activation"]["completed_at"]
    for key, value in retry.original["config"]["migration_backups"].items():
        if key != "post_activation":
            assert journal[key] == value
    assert (
        journal["post_activation"]["names_verified_at"]
        == retry.original["config"]["migration_backups"]["post_activation"]["names_verified_at"]
    )


@pytest.mark.parametrize("fallback", [False, True], ids=["direct", "timeout-fallback"])
@pytest.mark.parametrize(
    "fault",
    ["endpoint", "uid", "missing-uid", "invalid-name", "missing-peer", "read-error", "incomplete-read", "exception"],
)
def test_legacy_retry_requires_fresh_inventory_before_any_spoke_write(tmp_path, monkeypatch, caplog, fallback, fault):
    retry = Retry(tmp_path, monkeypatch, fallback=fallback)
    retry.repair = "converged"
    sentinel = "synthetic-credential-must-not-escape"
    applied = []

    def change_authoritative_inventory():
        assert retry.classifications == ["wrong_hub", "wrong_hub"]
        assert retry.patches == retry.restarts == []
        if fault == "endpoint":
            retry.hub.items[0]["spec"]["managedClusterClientConfigs"][0]["url"] = "https://replacement.example:6443"
        elif fault == "uid":
            retry.hub.items[0]["metadata"]["uid"] = "synthetic-replacement-uid"
        elif fault == "missing-uid":
            retry.hub.items[0]["metadata"].pop("uid")
        elif fault == "invalid-name":
            retry.hub.items[0]["metadata"]["name"] = ""
        elif fault == "missing-peer":
            retry.hub.items.pop()
        elif fault in ("read-error", "incomplete-read"):
            retry.hub.outcome = StrictReadOutcome.error(f"synthetic {fault} {sentinel}")
        else:
            retry.hub.inventory_exception = RuntimeError(sentinel)
        applied.append((copy.deepcopy(retry.hub.items), retry.hub.outcome, retry.hub.inventory_exception))
        retry.hub.events.append("fault-applied")

    retry.after_classification = change_authoritative_inventory
    caplog.set_level(logging.DEBUG, logger="acm_switchover")
    result = retry.run()

    # On A before B integration this observes actual Secret/Deployment writes and
    # false v2/completion evidence. The fault is applied to the fake authoritative
    # hub only after both real classifications; the gate itself is never replaced.
    assert applied
    assert retry.patches == retry.restarts == [], {"writes": retry.patches, "events": retry.hub.events}
    assert result is False
    assert retry.hub.events[-2:] == ["fault-applied", "inventory"]
    final_read = retry.hub.reads[-1]
    items, outcome, exception = applied[0]
    if exception is not None:
        assert final_read is exception
    elif outcome is not None:
        assert final_read is outcome and not final_read.is_success and final_read.items == []
    else:
        assert [item["metadata"] for item in final_read.items] == [item["metadata"] for item in items]
        assert [item["spec"] for item in final_read.items] == [item["spec"] for item in items]
    _assert_refused(retry)
    assert retry.stored()["config"]["migration_backups"] == retry.original["config"]["migration_backups"]
    assert "ManagedCluster inventory changed or could not be proven" in str(retry.stored()["errors"])
    assert sentinel not in caplog.text
    assert sentinel not in retry.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("fallback", [False, True], ids=["direct", "timeout-fallback"])
def test_legacy_retry_reloads_after_inventory_refusal_and_earns_v2_once(tmp_path, monkeypatch, fallback):
    retry = Retry(tmp_path, monkeypatch, fallback=fallback)
    retry.repair = "converged"
    original_items = copy.deepcopy(retry.hub.items)

    def replace_identity():
        retry.hub.items[0]["metadata"]["uid"] = "synthetic-replacement-uid"

    retry.after_classification = replace_identity
    assert retry.run() is False
    _assert_refused(retry)
    assert retry.patches == retry.restarts == []
    first_state = retry.state
    retry.hub.items = original_items
    retry.after_classification = None
    retry.hub.events.clear()

    assert retry.run() is True
    assert retry.state is not first_state  # Actual on-disk reload, not in-memory recovery.
    assert sorted(retry.patches) == sorted(retry.restarts) == NAMES
    first_write = retry.hub.events.index("patch")
    assert retry.hub.events[first_write - 3 : first_write] == ["classification", "classification", "inventory"]
    stored = retry.stored()
    _assert_history_preserved(retry, stored)
    assert _names(stored).count(CURRENT) == 1
    assert stored["config"]["migration_backups"]["post_activation"]["completed_at"]
    assert retry.finalization


def test_failure_after_new_marker_keeps_idempotent_skip_on_later_retry(tmp_path, monkeypatch):
    retry = Retry(tmp_path, monkeypatch)
    retry.hub_state = "correct"
    retry.hub.cleanup_error = True
    assert retry.run() is False
    first = retry.stored()
    assert CURRENT in _names(first)
    assert first["config"]["migration_backups"]["post_activation"]["completed_at"] is None
    reads = len(retry.spoke_reads)
    imports = retry.hub.import_reads

    retry.hub.cleanup_error = False
    # Option A preserves skip behavior after the new gate completed. It is not a
    # fresh observation on every retry, even if the transport would now be wrong.
    retry.hub_state = "wrong"
    assert retry.run() is True
    assert retry.finalization
    assert len(retry.spoke_reads) == reads and retry.hub.import_reads == imports
    assert _names(retry.stored()).count(CURRENT) == 1
    assert retry.stored()["config"]["migration_backups"]["post_activation"]["completed_at"]


@pytest.mark.parametrize("kind", ["missing", "unreadable", "yaml_invalid", "structure_invalid"])
def test_legacy_marker_does_not_hide_unusable_local_kubeconfig(tmp_path, monkeypatch, kind):
    retry = Retry(tmp_path, monkeypatch)
    if kind == "missing":
        retry.kubeconfig.unlink()
    elif kind == "unreadable":
        # Inject a real open failure only for the input file. chmod is unreliable
        # for root, and replacing the loader would bypass the contract being tested.
        import builtins

        original_open = builtins.open

        def unreadable(path, *args, **kwargs):
            if str(path) == str(retry.kubeconfig):
                raise PermissionError("synthetic unreadable kubeconfig")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", unreadable)
    else:
        retry.kubeconfig.write_text(
            "clusters: [" if kind == "yaml_invalid" else "clusters: wrong-shape\n", encoding="utf-8"
        )
    assert retry.run() is False
    _assert_refused(retry)
    assert retry.spoke_reads == retry.patches == retry.restarts == []
    assert retry.hub.import_reads == 0
    assert f"kubeconfig_load_failed ({kind})" in str(retry.stored()["errors"])


@pytest.mark.parametrize("failed_read", ["inventory", "import", "spoke"])
def test_legacy_marker_does_not_hide_failed_strict_reads(tmp_path, monkeypatch, failed_read):
    retry = Retry(tmp_path, monkeypatch)
    retry.hub.inventory_error = failed_read == "inventory"
    retry.hub.import_error = failed_read == "import"
    retry.hub_state = "failed_read" if failed_read == "spoke" else "wrong"
    assert retry.run() is False
    _assert_refused(retry)
    assert retry.patches == retry.restarts == []
    if failed_read == "inventory":
        assert retry.spoke_reads == [] and retry.hub.import_reads == 0
    elif failed_read == "import":
        assert retry.hub.import_reads == 2 and retry.spoke_reads == []
    else:
        assert len(retry.spoke_reads) == 2


@pytest.mark.parametrize("repair", ["denied", "accepted-still-wrong", "recheck-fails", "partial"])
def test_wrong_hub_repair_must_be_proven_before_marker_or_completion(tmp_path, monkeypatch, repair, caplog):
    retry = Retry(tmp_path, monkeypatch)
    retry.repair = repair
    caplog.set_level(logging.DEBUG, logger="acm_switchover")
    assert retry.run() is False
    _assert_refused(retry)
    assert sorted(retry.patches) == NAMES
    assert "wrong_hub" in retry.classifications
    if repair == "denied":
        assert retry.restarts == []
    else:
        assert sorted(retry.restarts) == ([] if repair == "partial" else NAMES)
        assert "Patched bootstrap-hub-kubeconfig secret on cluster-a" in caplog.text
        if repair == "partial":
            assert "cluster-a changed the cluster before it was reported as failed" in caplog.text

    # Correction after a failed/partial attempt must earn the new marker by
    # executing again over a newly loaded state, retaining old journal facts.
    previous_reads = len(retry.spoke_reads)
    retry.hub_state = "correct"
    retry.repair = "denied"
    assert retry.run() is True
    assert len(retry.spoke_reads) == previous_reads + 2
    assert CURRENT in _names(retry.stored())
    assert LEGACY in _names(retry.stored())


@pytest.mark.parametrize("fault", ["marker", "completion"])
def test_actual_state_write_failure_cannot_advance_to_finalization(tmp_path, monkeypatch, fault):
    retry = Retry(tmp_path, monkeypatch)
    retry.hub_state = "correct"
    replace = os.replace
    attempted = []

    def fail_selected_write(source, destination):
        if str(destination) != str(retry.path):
            return replace(source, destination)
        candidate = json.loads(Path(source).read_text(encoding="utf-8"))
        is_completion = bool(candidate["config"]["migration_backups"]["post_activation"]["completed_at"])
        if (fault == "marker" and CURRENT in _names(candidate)) or (fault == "completion" and is_completion):
            # Record the actual fsynced temporary file presented for atomic replacement.
            attempted.append(copy.deepcopy(candidate))
            raise OSError("synthetic durable write failure")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_selected_write)
    with pytest.raises(OSError, match="synthetic durable write failure"):
        retry.run()
    assert attempted and CURRENT in _names(attempted[0])
    assert bool(attempted[0]["config"]["migration_backups"]["post_activation"]["completed_at"]) is (
        fault == "completion"
    )
    assert retry.state._dirty  # An indeterminate write remains pending, never successful.
    assert retry.finalization is False
    # Simulate process loss, without the test process's later atexit flushing the
    # failed in-memory candidate after the injected I/O fault has been removed.
    atexit.unregister(retry.state._flush_on_exit)
    reloaded = StateManager(str(retry.path))
    try:
        assert reloaded.state == retry.stored()
        stored = reloaded.state
        _assert_history_preserved(retry, stored)
        assert (CURRENT in _names(stored)) is (fault == "completion")
        assert LEGACY in _names(stored)
        assert stored["config"]["migration_backups"]["post_activation"]["completed_at"] is None
        assert stored["config"]["migration_backups"] == retry.original["config"]["migration_backups"]
    finally:
        reloaded._release_run_lock()


@pytest.mark.parametrize("kind", ["missing", "invalid", "restore-incomplete"])
def test_historical_journal_refusals_stay_distinct_from_usable_legacy_bypass(tmp_path, monkeypatch, kind):
    retry = Retry(tmp_path, monkeypatch)
    raw = retry.stored()
    if kind == "missing":
        raw["config"].pop("migration_backups")
    elif kind == "invalid":
        raw["config"]["migration_backups"]["schema_version"] = 999
    else:
        raw["config"]["migration_backups"]["restore"]["completed_at"] = None
        raw["config"]["migration_backups"]["post_activation"]["names_verified_at"] = None
    retry.path.write_text(json.dumps(raw), encoding="utf-8")
    assert retry.run() is False
    assert retry.hub.inventory_reads == retry.hub.import_reads == 0
    assert retry.spoke_reads == retry.patches == []
    assert CURRENT not in _names(retry.stored())
    assert (
        "journal_required" if kind == "missing" else "invalid" if kind == "invalid" else "restore_not_completed"
    ) in str(retry.stored()["errors"])


def test_legacy_checkpoint_dry_run_writes_no_new_evidence_or_transport(tmp_path, monkeypatch):
    retry = Retry(tmp_path, monkeypatch)
    state = StateManager(str(retry.path))
    before = retry.path.read_bytes()
    try:
        verification = post.PostActivationVerification(retry.hub, state, False, dry_run=True)
        assert verification.verify() is True
        assert retry.path.read_bytes() == before
        _assert_history_preserved(retry, state.state)
        assert CURRENT not in _names(state.state)
        assert retry.hub.inventory_reads == retry.hub.import_reads == 0
        assert retry.spoke_reads == retry.patches == retry.restarts == []
    finally:
        state._release_run_lock()


def test_no_matching_context_remains_a_documented_nonfatal_gate_skip(tmp_path, monkeypatch, caplog):
    retry = Retry(tmp_path, monkeypatch)
    retry.kubeconfig.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [],
                "contexts": [],
                "users": [],
            }
        ),
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO, logger="acm_switchover")
    assert retry.run() is True
    assert CURRENT in _names(retry.stored())
    assert retry.spoke_reads == retry.patches == []
    assert "Klusterlet verification skipped for 2 cluster(s)" in caplog.text


@pytest.mark.parametrize("phase", [Phase.FINALIZATION, Phase.COMPLETED, Phase.FAILED])
def test_versioned_marker_does_not_rewind_out_of_phase_legacy_checkpoints(tmp_path, monkeypatch, phase):
    retry = Retry(tmp_path, monkeypatch)
    raw = retry.stored()
    raw["current_phase"] = phase.value
    raw["last_updated"] = datetime.now(timezone.utc).isoformat()
    raw["config"]["migration_backups"]["post_activation"]["completed_at"] = "2026-10-16T13:27:00+00:00"
    if phase == Phase.FAILED:
        raw["errors"][-1]["phase"] = Phase.FINALIZATION.value
    retry.path.write_text(json.dumps(raw), encoding="utf-8")
    assert retry.run() is True
    assert retry.spoke_reads == retry.patches == []
    assert retry.hub.inventory_reads == 0
    assert CURRENT not in _names(retry.stored())
    assert retry.stored()["config"]["migration_backups"] == raw["config"]["migration_backups"]
    assert retry.finalization is (phase != Phase.COMPLETED)
    assert retry.stored()["current_phase"] == (Phase.FINALIZATION.value if phase == Phase.FAILED else phase.value)


def test_finalization_without_journal_completion_still_refuses_without_rewind(tmp_path, monkeypatch):
    retry = Retry(tmp_path, monkeypatch)
    raw = retry.stored()
    raw["current_phase"] = Phase.FINALIZATION.value
    retry.path.write_text(json.dumps(raw), encoding="utf-8")
    assert retry.run() is False
    assert retry.finalization is False
    assert retry.hub.inventory_reads == 0
    assert CURRENT not in _names(retry.stored())
    assert "post_activation_not_completed" in str(retry.stored()["errors"])
    assert retry.stored()["config"]["migration_backups"] == raw["config"]["migration_backups"]
