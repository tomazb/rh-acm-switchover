"""Collection raw checkpoint facts and independent Backup chronology decisions."""

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import backup_continuity as owner
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    backup_continuity_facts,
    checkpoint_facts,
)

VECTORS = Path(__file__).resolve().parents[5] / "tests/fixtures/backup_continuity_compatibility.json"


@pytest.mark.parametrize("case", json.loads(VECTORS.read_text()), ids=lambda c: c["id"])
def test_collection_boundary_creation_vectors(case):
    before = copy.deepcopy(case)
    now = datetime.fromisoformat(case["observed_at"].replace("Z", "+00:00"))
    try:
        boundary = owner.resolve_enable_boundary(case["inputs"], observed_at=now)
        owner.validate_backup(case["backup"], enabled_at=boundary, observed_at=now)
    except owner.BackupContinuityError as exc:
        assert exc.reason == case["collection_reason"]
    else:
        assert case["collection_reason"] is None
    assert case == before


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}, "not-a-time"])
def test_raw_fact_presence_survives_falsey_or_malformed_value(value):
    checkpoint = {
        "operational_data": {"backup_schedule_enabled_at": value},
        "completed_phases": ["preflight", "finalization"],
        "phase": "finalization",
        "phase_status": "passed",
        "operation_identity": {"restore_only": False},
    }
    before = copy.deepcopy(checkpoint)
    facts = backup_continuity_facts(checkpoint)
    assert facts["enabled_at_present"] is True and facts["enabled_at"] == value
    assert facts["finalization_completed"] is True
    assert checkpoint_facts(checkpoint)["backup_continuity"] == facts
    assert checkpoint == before


@pytest.mark.parametrize("checkpoint", [None, {"operational_data": []}, {"completed_phases": "finalization"}])
def test_malformed_checkpoint_fact_containers_refuse(checkpoint):
    with pytest.raises(owner.BackupContinuityError, match="invalid_backup_evidence"):
        owner.resolve_enable_boundary(
            backup_continuity_facts(checkpoint), observed_at=datetime.fromisoformat("2026-10-17T11:00:00+00:00")
        )


@pytest.mark.parametrize("case", ["pending", "boundary", "completed", "passed", "malformed", "terminal", "absent"])
def test_pre_enable_cleanup_retry_requires_canonical_pending_cleanup_and_raw_absence(case):
    vectors = json.loads((VECTORS.parent / "r4_04_migration_evidence_vectors.json").read_text())
    name = "journal-cleanup-completed" if case == "terminal" else "journal-cleanup-intent-persisted"
    journal = copy.deepcopy(next(c["input"]["candidate"] for c in vectors["cases"] if c["id"] == name))
    checkpoint: dict = {
        "phase": "finalization",
        "phase_status": "fail",
        "completed_phases": [],
        "operational_data": {"migration_backups": journal},
    }
    if case == "boundary":
        checkpoint["operational_data"]["backup_schedule_enabled_at"] = None
    elif case == "completed":
        checkpoint["completed_phases"] = ["finalization"]
    elif case == "passed":
        checkpoint["phase_status"] = "invalid-status"
    elif case == "malformed":
        journal["cleanup"]["uid"] = "another-owner"
    elif case == "absent":
        checkpoint["operational_data"] = {}
    original = copy.deepcopy(checkpoint)
    assert backup_continuity_facts(checkpoint)["pre_enable_cleanup_retry"] == (case == "pending")
    assert checkpoint == original


def test_strict_transport_creation_bound_positive():
    from ansible_collections.tomazb.acm_switchover.tests.unit.test_k8s_read_outcome import (
        _FakeClient,
        _FakeDynamicClient,
        _ResolvedResource,
    )

    case = json.loads(VECTORS.read_text())[0]
    good = copy.deepcopy(case["backup"])
    good["metadata"].update(
        name="acm-managed-clusters-current",
        uid="uid-1",
        resourceVersion="12",
        creationTimestamp="2026-10-17T10:30:00+00:00",
    )
    good["status"] = {"phase": "Completed", "errors": 0}
    route = _ResolvedResource("backups", namespaced=True, group_version="velero.io/v1")
    discovery = {
        "kind": "APIResourceList",
        "groupVersion": "velero.io/v1",
        "resources": [{"name": "backups", "kind": "Backup", "namespaced": True}],
    }
    api = _FakeClient(
        resource=route,
        pages=[{"items": [good], "metadata": {"resourceVersion": "100"}}, good],
        dynamic=_FakeDynamicClient(discovery),
    )
    proof = owner.verify_backup_continuity(
        api, enabled_at=datetime.fromisoformat("2026-10-17T10:00:00+00:00"), timeout=1, poll_interval=0
    )
    assert proof["name"] == good["metadata"]["name"] and proof["phase"] == "Completed"
    assert api.get_calls == 2 and api.get_params[1]["name"] == proof["name"]


@pytest.fixture
def strict_transport(monkeypatch):
    from ansible_collections.tomazb.acm_switchover.tests.unit.test_k8s_read_outcome import (
        _FakeClient,
        _FakeDynamicClient,
        _ResolvedResource,
    )

    class Clock:
        at = 0.0

        def monotonic(self):
            self.at += 0.01
            return self.at

        def sleep(self, seconds):
            self.at += seconds

    monkeypatch.setattr(owner, "time", Clock())
    discovery = {
        "kind": "APIResourceList",
        "groupVersion": "velero.io/v1",
        "resources": [{"name": "backups", "kind": "Backup", "namespaced": True}],
    }

    def make(pages, *, served=True, discovery_error=None):
        return _FakeClient(
            resource=_ResolvedResource("backups", namespaced=True, group_version="velero.io/v1"),
            pages=pages,
            dynamic=_FakeDynamicClient(
                discovery=discovery if served else {**discovery, "resources": []}, discovery_error=discovery_error
            ),
        )

    return make


def current_backup(**changes):
    backup: dict = {
        "apiVersion": "velero.io/v1",
        "kind": "Backup",
        "metadata": {
            "name": "acm-managed-clusters-current",
            "uid": "uid-current",
            "resourceVersion": "12",
            "creationTimestamp": "2024-01-01T00:01:00Z",
        },
        "status": {"phase": "Completed", "errors": 0},
    }
    for section, value in changes.items():
        backup[section].update(value)
    return backup


def complete_page(items, **metadata):
    return {"items": items, "metadata": {"resourceVersion": "100", **metadata}}


def prove(api):
    return owner.verify_backup_continuity(
        api, enabled_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"), timeout=0.1, poll_interval=0.01
    )


@pytest.mark.parametrize("fault", [404, 403, "transport", "discovery", "unserved", "malformed", "page404", "revision"])
def test_failed_initial_strict_transport_cannot_become_empty_baseline(strict_transport, fault):
    from kubernetes.client.rest import ApiException

    good = current_backup()
    errors = {
        404: ApiException(status=404),
        403: ApiException(status=403),
        "transport": TimeoutError("SENSITIVE transport detail"),
        "malformed": {"metadata": {"resourceVersion": "1"}},
        "page404": complete_page([], **{"continue": "next"}),
        "revision": {"items": [], "metadata": {}},
    }
    initial = errors.get(fault, complete_page([]))
    pages = [initial, ApiException(status=404)] if fault == "page404" else [initial, complete_page([good]), good]
    api = strict_transport(
        pages, served=fault != "unserved", discovery_error=TimeoutError("SENSITIVE") if fault == "discovery" else None
    )
    with pytest.raises(owner.BackupContinuityError, match="backup_inventory_unavailable") as refused:
        prove(api)
    assert "SENSITIVE" not in str(refused.value)
    assert not any("name" in p for p in api.get_params)


@pytest.mark.parametrize("first_poll_fails", [False, True])
def test_positive_empty_then_current_new_completes_with_strict_identity(strict_transport, first_poll_fails):
    good = current_backup()
    pages = (
        [complete_page([])] + ([TimeoutError("transient")] if first_poll_fails else []) + [complete_page([good]), good]
    )
    api = strict_transport(pages)
    assert prove(api)["uid"] == "uid-current"
    assert api.get_params[-1]["name"] == good["metadata"]["name"]


@pytest.mark.parametrize("failed_poll", [False, True])
def test_recovered_old_inventory_never_certifies_continuity(strict_transport, failed_poll):
    old = current_backup(metadata={"creationTimestamp": "2023-01-01T00:00:00Z"})
    pages = [complete_page([])] + ([TimeoutError("transient")] if failed_poll else []) + [complete_page([old])] * 12
    api = strict_transport(pages)
    with pytest.raises(owner.BackupContinuityError, match="verification timeout"):
        prove(api)
    assert not any("name" in p for p in api.get_params)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"metadata": {"uid": "replacement"}}, "backup_identity_changed"),
        ({"metadata": {"creationTimestamp": "2023-01-01T00:00:00Z"}}, "backup_predates_enable"),
        ({"metadata": {"creationTimestamp": "invalid"}}, "invalid_backup_creation"),
        ({"metadata": {"name": "unrelated"}}, "backup_identity_changed"),
        ({"status": {"phase": "Failed"}}, "backup_not_completed"),
        ({"status": {"phase": "PartiallyFailed"}}, "backup_not_completed"),
        ({"status": {"errors": 1}}, "backup_errors"),
    ],
)
def test_terminal_read_revalidates_identity_chronology_and_health(strict_transport, change, reason):
    api = strict_transport([complete_page([current_backup()]), current_backup(**change)])
    with pytest.raises(owner.BackupContinuityError, match=reason):
        prove(api)


def test_current_pending_completion_and_disappearance(strict_transport):
    from kubernetes.client.rest import ApiException

    pending = current_backup(status={"phase": "InProgress"})
    api = strict_transport([complete_page([pending]), pending, current_backup()])
    assert prove(api)["phase"] == "Completed"
    missing = strict_transport([complete_page([pending]), ApiException(status=404)])
    with pytest.raises(owner.BackupContinuityError, match="recorded_backup_unavailable"):
        prove(missing)


def test_retry_exhaustion_cannot_promote_prior_selection(strict_transport):
    api = strict_transport([complete_page([])] + [TimeoutError("unreadable")] * 12)
    with pytest.raises(owner.BackupContinuityError, match="verification timeout"):
        prove(api)
    assert not any("name" in p for p in api.get_params)


@pytest.mark.parametrize("older_failed", [False, True])
def test_latest_fresh_creation_is_selected_and_only_its_health_authorizes_success(strict_transport, older_failed):
    older = current_backup(status={"phase": "Failed" if older_failed else "Completed"})
    latest = current_backup(
        metadata={
            "name": "acm-managed-clusters-latest",
            "uid": "uid-latest",
            "creationTimestamp": "2024-01-01T00:02:00Z",
        }
    )
    api = strict_transport([complete_page([older, latest]), latest])
    assert prove(api)["uid"] == "uid-latest"
    assert api.get_params[-1]["name"] == "acm-managed-clusters-latest"


def test_latest_unhealthy_never_falls_back_to_an_older_healthy_backup(strict_transport):
    older = current_backup()
    latest = current_backup(
        metadata={
            "name": "acm-managed-clusters-latest",
            "uid": "uid-latest",
            "creationTimestamp": "2024-01-01T00:02:00Z",
        },
        status={"phase": "Failed"},
    )
    api = strict_transport([complete_page([older, latest]), latest])
    with pytest.raises(owner.BackupContinuityError, match="backup_not_completed"):
        prove(api)
