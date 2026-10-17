"""Backup continuity through real strict readers and durable workflow state.

Only API transports and the polling clock are fake. A failed inventory must never
become an empty baseline, a new Backup name, or a completed finalization step.
"""

import copy
import json
import logging
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

import acm_switchover as cli
import modules.finalization as finalization_module
from lib.constants import BACKUP_NAMESPACE
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.run_record import RunRecord
from lib.utils import Phase, StateManager
from lib.workflow import run_phase_flow
from modules.finalization import Finalization


def backup(name, timestamp, phase="Completed", errors=0):
    return {
        "apiVersion": "velero.io/v1",
        "kind": "Backup",
        "metadata": {
            "name": name,
            "namespace": BACKUP_NAMESPACE,
            "uid": name + "-uid",
            "resourceVersion": "12",
            "creationTimestamp": timestamp,
            "labels": {
                "cluster.open-cluster-management.io/backup-schedule-type": "managedClusters"
            },
        },
        "status": {
            "phase": phase,
            "completionTimestamp": timestamp,
            "errors": errors,
            "warnings": 0,
        },
    }


def page(items, token=None):
    return {"items": items, "metadata": {"resourceVersion": "100", "continue": token}}


class BackupTransport:
    """Record actual transport faults, discovery, pages and named reads."""

    def __init__(self, effects, objects=(), discovery="served"):
        self.effects = list(effects)
        self.objects = list(objects)
        self.discovery = discovery
        self.requests = []

    @staticmethod
    def _next(effects):
        effect = effects.pop(0) if len(effects) > 1 else effects[0]
        if isinstance(effect, Exception):
            raise effect
        return copy.deepcopy(effect)

    def list_namespaced_custom_object(self, **kwargs):
        if kwargs["plural"] == "backupschedules":
            return page([])
        assert (
            kwargs["group"],
            kwargs["version"],
            kwargs["plural"],
            kwargs["namespace"],
        ) == (
            "velero.io",
            "v1",
            "backups",
            BACKUP_NAMESPACE,
        )
        try:
            result = self._next(self.effects)
        except Exception as exc:
            self.requests.append(
                (
                    "LIST",
                    kwargs.get("_continue"),
                    getattr(exc, "status", type(exc).__name__),
                )
            )
            raise
        self.requests.append(("LIST", kwargs.get("_continue"), result))
        return result

    def get_namespaced_custom_object(self, **kwargs):
        self.requests.append(("GET", kwargs["name"]))
        return self._next(self.objects)

    def call_api(self, path, method, **kwargs):
        if path == "/apis/cluster.open-cluster-management.io/v1beta1":
            return SimpleNamespace(
                data=json.dumps(
                    {
                        "kind": "APIResourceList",
                        "groupVersion": "cluster.open-cluster-management.io/v1beta1",
                        "resources": [
                            {
                                "name": "backupschedules",
                                "kind": "BackupSchedule",
                                "namespaced": True,
                            }
                        ],
                    }
                ).encode()
            )
        assert (path, method) == ("/apis/velero.io/v1", "GET")
        self.requests.append(("discovery", path))
        if isinstance(self.discovery, Exception):
            raise self.discovery
        resources = [{"name": "backups", "kind": "Backup", "namespaced": True}]
        return SimpleNamespace(
            data=json.dumps(
                {
                    "kind": "APIResourceList",
                    "groupVersion": "velero.io/v1",
                    "resources": resources if self.discovery == "served" else [],
                }
            ).encode()
        )

    def list_namespaced_pod(self, **kwargs):
        return SimpleNamespace(items=[])


class Clock:
    def __init__(self):
        self.at = 0

    def time(self):
        self.at += 0.1
        return self.at

    def sleep(self, seconds):
        self.at += seconds


@pytest.fixture
def make_finalization(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(finalization_module, "time", clock)
    import modules.backup_continuity as proof_module

    monkeypatch.setattr(proof_module, "time", clock)
    monkeypatch.setattr(finalization_module, "BACKUP_POLL_INTERVAL", 1)
    now = datetime.now(timezone.utc)
    enabled = (now - timedelta(seconds=2)).isoformat()
    old = backup("acm-managed-clusters-old", (now - timedelta(days=3)).isoformat())
    new = backup("acm-managed-clusters-new", (now - timedelta(seconds=1)).isoformat())

    def make(effects, objects=None, discovery="served", **kwargs):
        transport = BackupTransport(
            effects, objects=[new] if objects is None else objects, discovery=discovery
        )
        client = object.__new__(KubeClient)
        client.context = "fake-secondary"
        client.dry_run = False
        client.request_timeout = 1
        client.custom_api = transport
        client._api_client = transport
        client.core_v1 = transport
        state = StateManager(str(tmp_path / "state.json"))
        state.set_phase(Phase.FINALIZATION)
        RunRecord(state).record_backup_watch_started(enabled)
        fin = Finalization(client, state, "2.12.0", old_hub_action="none", **kwargs)
        fin._cached_schedules = []
        return fin, transport

    make.old = old
    make.new = new
    return make


def complete_other_steps(fin):
    """Valid fixture-owned prerequisites, not evidence about unrelated phases."""
    vectors = json.loads(
        (
            Path(__file__).parent / "fixtures/r4_04_migration_evidence_vectors.json"
        ).read_text()
    )
    journal = next(
        v["input"]["candidate"]
        for v in vectors["cases"]
        if v["id"] == "journal-cleanup-completed"
    )
    fin.state._set_config("migration_backups", journal)
    for step in (
        "enable_backup_schedule",
        "verify_backup_schedule_enabled",
        "fix_backup_collision",
        "verify_mch_health",
        "handle_old_hub",
    ):
        fin.state.mark_step_completed(step)


def flow(fin):
    return run_phase_flow(
        Namespace(dry_run=False),
        fin.state,
        None,
        fin.secondary,
        logging.getLogger("test"),
        ((lambda *args: fin.finalize(), (Phase.FINALIZATION,), Phase.FINALIZATION),),
        "test",
        lambda *args: False,
        lambda *args: False,
        lambda *args: None,
        backup_continuity_guard=cli._verify_backup_continuity_entry,
    )


def assert_no_backup_completion(fin):
    assert not fin.state.is_step_completed("verify_new_backups")
    assert not fin.state.is_step_completed("verify_backup_integrity")
    assert fin.state.get_current_phase() == Phase.FINALIZATION


@pytest.mark.parametrize("through_flow", [False, True])
def test_actual_404_baseline_cannot_certify_recovered_old_completed_backup(
    make_finalization, through_flow
):
    old = make_finalization.old
    fin, transport = make_finalization(
        [ApiException(status=404, reason="baseline fault"), page([old])], [old]
    )
    if through_flow:
        complete_other_steps(fin)
        refused = flow(fin) is False
    else:
        refused = False
        try:
            fin._verify_new_backups(timeout=5)
            fin._verify_backup_integrity()
        except SwitchoverError:
            refused = True
    assert (
        "LIST",
        None,
        404,
    ) in transport.requests  # Fault actually reached the reader.
    assert (
        refused
    ), f"Actual baseline 404 reached reader but certified old Backup: {transport.requests!r}"
    assert fin.run_record.new_backup() is None
    assert_no_backup_completion(fin)
    assert not any(request[0] == "GET" for request in transport.requests)


@pytest.mark.parametrize(
    "fault",
    ["403", "transport", "discovery", "unserved", "malformed", "page404", "revision"],
)
def test_invalid_baseline_never_records_a_backup_or_completes_a_step(
    make_finalization, fault
):
    old = make_finalization.old
    effects = {
        "403": [ApiException(status=403)],
        "transport": [RuntimeError("transport")],
        "discovery": [page([])],
        "unserved": [page([])],
        "malformed": [{"items": [old], "metadata": {}}],
        "page404": [page([old], "next"), ApiException(status=404)],
        "revision": [
            page([old], "next"),
            {"items": [], "metadata": {"resourceVersion": "different"}},
        ],
    }[fault]
    discovery = (
        RuntimeError("discovery")
        if fault == "discovery"
        else "unserved" if fault == "unserved" else "served"
    )
    fin, transport = make_finalization(effects, discovery=discovery)
    complete_other_steps(fin)
    assert flow(fin) is False
    assert fin.run_record.new_backup() is None
    assert_no_backup_completion(fin)
    assert transport.requests
    if fault in ("discovery", "unserved"):
        assert not any(request[0] == "LIST" for request in transport.requests)


def test_positive_empty_then_new_clean_completed_backup_completes_flow(
    make_finalization,
):
    new = make_finalization.new
    fin, _ = make_finalization([page([]), page([new])])
    complete_other_steps(fin)
    assert flow(fin) is True
    assert fin.run_record.new_backup() == new["metadata"]["name"]
    assert fin.state.is_step_completed("verify_new_backups")
    assert fin.state.is_step_completed("verify_backup_integrity")
    reloaded = StateManager(str(fin.state.state_file))
    assert RunRecord(reloaded).new_backup() == new["metadata"]["name"]


@pytest.mark.parametrize(
    "poll_fault",
    [None, ApiException(status=404), ApiException(status=403), RuntimeError("poll")],
)
def test_poll_failure_and_recovery_cannot_erase_existing_baseline(
    make_finalization, poll_fault
):
    old = make_finalization.old
    effects = (
        [page([old])] + ([] if poll_fault is None else [poll_fault]) + [page([old])]
    )
    fin, transport = make_finalization(effects)
    with pytest.raises(SwitchoverError, match="No qualifying new backup"):
        fin._verify_new_backups(timeout=5)
    assert fin.run_record.new_backup() is None
    assert_no_backup_completion(fin)
    assert sum(request[0] == "LIST" for request in transport.requests) >= 3


@pytest.mark.parametrize(
    "fault", [ApiException(status=404), ApiException(status=500), RuntimeError("poll")]
)
def test_poll_read_failure_can_recover_to_genuinely_new_backup(
    make_finalization, fault
):
    old, new = make_finalization.old, make_finalization.new
    fin, _ = make_finalization([page([old]), fault, page([old, new])])
    fin._verify_new_backups(timeout=5)
    fin._verify_backup_integrity()
    assert fin.run_record.new_backup() == new["metadata"]["name"]


def test_repeated_poll_failures_exhaust_bound_without_partial_evidence(
    make_finalization,
):
    fin, transport = make_finalization([page([]), ApiException(status=404)])
    with pytest.raises(SwitchoverError, match="No qualifying new backup"):
        fin._verify_new_backups(timeout=5)
    assert 2 <= sum(request[0] == "LIST" for request in transport.requests) <= 7
    assert fin.run_record.new_backup() is None
    assert_no_backup_completion(fin)


@pytest.mark.parametrize("readable", [False, True])
def test_recorded_name_resume_requires_fresh_readable_baseline(
    make_finalization, readable
):
    new = make_finalization.new
    fin, transport = make_finalization(
        [page([new])] if readable else [ApiException(status=404)]
    )
    fin.run_record.record_new_backup(new["metadata"]["name"])
    if readable:
        fin._verify_new_backups(timeout=5)
        fin._verify_backup_integrity()
    else:
        with pytest.raises(SwitchoverError):
            fin._verify_new_backups(timeout=5)
        assert not any(request[0] == "GET" for request in transport.requests)
        assert_no_backup_completion(fin)
    assert (
        fin.run_record.new_backup() == new["metadata"]["name"]
    )  # No silent legacy-state rewrite.


def test_valid_post_enable_baseline_resumes_without_another_backup(make_finalization):
    new = make_finalization.new
    fin, transport = make_finalization([page([new])])
    fin._verify_new_backups(timeout=5)
    fin._verify_backup_integrity()
    assert fin.run_record.new_backup() == new["metadata"]["name"]
    assert sum(request[0] == "LIST" for request in transport.requests) == 2


@pytest.mark.parametrize(
    "phase,errors", [("Failed", 0), ("PartiallyFailed", 0), ("Completed", 1)]
)
def test_unhealthy_new_backup_cannot_complete_finalization(
    make_finalization, phase, errors
):
    unhealthy = copy.deepcopy(make_finalization.new)
    unhealthy["status"].update(phase=phase, errors=errors)
    fin, _ = make_finalization([page([]), page([unhealthy])], [unhealthy])
    # Start at integrity with an existing name, as a prior continuity pass may
    # have detected New/InProgress before the Backup reached this terminal state.
    fin.run_record.record_new_backup(unhealthy["metadata"]["name"])
    complete_other_steps(fin)
    fin.state.mark_step_completed("verify_new_backups")
    assert flow(fin) is False
    assert not fin.state.is_step_completed("verify_backup_integrity")


@pytest.mark.parametrize(
    "fault", [ApiException(status=404), {"items": [], "metadata": {}}]
)
def test_integrity_fallback_requires_complete_strict_inventory(
    make_finalization, fault
):
    effect = fault if isinstance(fault, Exception) else copy.deepcopy(fault)
    fin, _ = make_finalization([effect])
    with pytest.raises(SwitchoverError):
        fin._verify_backup_integrity()
    assert fin.run_record.new_backup() is None


def test_disappeared_recorded_backup_with_empty_fallback_refuses_completion(
    make_finalization,
):
    fin, _ = make_finalization([page([])], [ApiException(status=404)])
    fin.run_record.record_new_backup(make_finalization.new["metadata"]["name"])
    with pytest.raises(SwitchoverError, match="recorded_backup_unavailable"):
        fin._verify_backup_integrity()


def test_dry_run_never_reads_or_records_backup_evidence(make_finalization):
    fin, transport = make_finalization([ApiException(status=404)], dry_run=True)
    fin._verify_new_backups(timeout=5)
    fin._verify_backup_integrity()
    assert transport.requests == []
    assert fin.run_record.new_backup() is None
