# SPDX-License-Identifier: MIT
"""Unit tests for acm_migration_evidence, the thin adapter over module_utils/migration_activation.

The transaction decisions themselves are held equal to the Python CLI by
tests/test_activation_transaction_parity.py at the repository root.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
import yaml
from ansible.module_utils import basic
from ansible.module_utils.basic import AnsibleModule as RealAnsibleModule

from ansible_collections.tomazb.acm_switchover.plugins.modules import acm_migration_evidence as module


class _Exit(SystemExit):
    def __init__(self, results):
        super().__init__(0)
        self.results = results


def _run(monkeypatch, operation, inputs, *, check_mode=False):
    class Capturing(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise _Exit(kwargs)

        def fail_json(self, **kwargs):
            raise _Exit({**kwargs, "failed": True})

    args = {"operation": operation, "inputs": inputs}
    if check_mode:
        args["_ansible_check_mode"] = True
    monkeypatch.setattr(module, "AnsibleModule", Capturing)
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode("utf-8"))
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    with pytest.raises(_Exit) as exited:
        module.main()
    return exited.value.results


def _mch(version):
    return {"read_status": "ok", "resources": [{"metadata": {"name": "hub"}, "status": {"currentVersion": version}}]}


def test_a_decision_is_returned_unchanged_and_never_reports_a_change(monkeypatch):
    result = _run(monkeypatch, "acm_minor", {"mch_read": _mch("2.14.3")})
    assert result == {"changed": False, "acm_minor": "2.14", "controller_contract": "legacy_2_12_2_16"}


@pytest.mark.parametrize("check_mode", [False, True])
def test_a_refusal_fails_with_the_transaction_message(monkeypatch, check_mode):
    result = _run(monkeypatch, "acm_minor", {"mch_read": _mch("2.18.0")}, check_mode=check_mode)
    assert result["failed"] is True and result["changed"] is False
    assert result["msg"].startswith("Migration evidence refused (unknown_acm_minor:")


def test_an_unverified_read_is_never_an_answer(monkeypatch):
    result = _run(monkeypatch, "acm_minor", {"mch_read": {"read_status": "error", "resources": []}})
    assert result == {"failed": True, "changed": False, "msg": "MultiClusterHub read failed (error)"}


def test_incomplete_inputs_fail_closed(monkeypatch):
    result = _run(monkeypatch, "pre_patch", {"journal": {}})
    assert result["failed"] is True
    assert result["msg"] == "The pre_patch step received incomplete inputs."


def test_the_freeze_mints_a_transaction_identity_unless_one_is_supplied(monkeypatch):
    calls: list = []

    def freeze(**inputs):
        calls.append(inputs)
        return {"candidate": {}}

    monkeypatch.setattr(module.transaction, "freeze", freeze)
    _run(monkeypatch, "freeze", {"kind": "passive_patch"})
    _run(monkeypatch, "freeze", {"kind": "passive_patch", "run_id": "fixed", "resolved_at": "then"})
    assert uuid.UUID(calls[0]["run_id"]).version == 4 and calls[0]["resolved_at"]
    assert (calls[1]["run_id"], calls[1]["resolved_at"]) == ("fixed", "then")


def test_documentation_matches_the_argument_spec_and_operations():
    documentation = yaml.safe_load(module.DOCUMENTATION)
    assert documentation["options"]["operation"]["choices"] == sorted(module._OPERATIONS)
    assert set(documentation["options"]) == {"operation", "inputs"}
    yaml.safe_load(module.EXAMPLES)
    yaml.safe_load(module.RETURN)


def _mc_read(*names):
    return {"read_status": "ok", "resources": [{"metadata": {"name": name}} for name in (*names, "local-cluster")]}


@pytest.mark.parametrize("minimum, enforce", [(2, True), ("2", "True")], ids=["native", "stringified"])
def test_managed_cluster_inputs_survive_the_non_native_templating_floor(monkeypatch, minimum, enforce):
    """ansible-core 2.16 renders scalars nested in module inputs as text; the decision is the same."""
    inputs = {"expected_names": ["a"], "min_managed_clusters": minimum, "enforce": enforce}
    assert _run(monkeypatch, "managed_clusters_poll", {"mc_read": _mc_read("a"), **inputs})["state"] == "pending"
    refused = _run(monkeypatch, "verify_managed_clusters", {"mc_read": _mc_read("a"), **inputs})
    assert refused["failed"] is True and "Expected 2 ManagedCluster(s)" in refused["msg"]


@pytest.mark.parametrize("minimum, enforce", [("-1", True), ("two", True), (1, "yes"), (True, True)])
def test_malformed_managed_cluster_inputs_are_refused(monkeypatch, minimum, enforce):
    inputs = {"expected_names": [], "min_managed_clusters": minimum, "enforce": enforce}
    assert _run(monkeypatch, "verify_managed_clusters", {"mc_read": _mc_read("a"), **inputs})["failed"] is True


# -- R4-04 Task 9: post-activation evidence operations (module_utils/migration_post_activation) --

_VECTORS = {
    case["id"]: case
    for case in json.loads(
        (
            Path(__file__).resolve().parents[7] / "tests" / "fixtures" / "r4_04_migration_evidence_vectors.json"
        ).read_text(encoding="utf-8")
    )["cases"]
}
_COMPLETED = _VECTORS["post-activation-journal-completed"]["input"]["journal"]
_REQUEST = {"scope": "post_activation", "actor": "operator@example.com", "reason": "stale", "request_id": None}


def _ready_read(*names):
    ready = [
        {"type": "ManagedClusterConditionAvailable", "status": "True"},
        {"type": "ManagedClusterJoined", "status": "True"},
    ]
    return {
        "read_status": "ok",
        "resources": [
            {"metadata": {"name": name}, "status": {"conditions": ready}} for name in (*names, "local-cluster")
        ],
    }


@pytest.mark.parametrize("minimum, waived", [(2, False), ("2", "False")], ids=["native", "stringified"])
def test_readiness_inputs_survive_the_non_native_templating_floor(monkeypatch, minimum, waived):
    inputs = {"expected_names": ["a", "b"], "min_managed_clusters": minimum, "names_waived": waived}
    result = _run(monkeypatch, "managed_cluster_readiness", {"mc_read": _ready_read("a", "b"), **inputs})
    assert result["ready"] is True and result["count"] == 2 and result["changed"] is False


def test_an_unverified_inventory_is_never_zero_clusters(monkeypatch):
    inputs = {"expected_names": [], "min_managed_clusters": 0, "names_waived": False}
    result = _run(
        monkeypatch, "managed_cluster_readiness", {"mc_read": {"read_status": "error", "resources": []}, **inputs}
    )
    assert result == {"failed": True, "changed": False, "msg": "ManagedCluster inventory read failed (error)"}


def test_a_post_activation_refusal_fails_with_the_python_message(monkeypatch):
    result = _run(monkeypatch, "require_completed_restore", {})
    assert result["failed"] is True and result["changed"] is False
    assert result["msg"].startswith("Migration evidence refused (journal_required:")


def test_the_post_activation_waiver_is_journaled_with_the_collection_flag_and_a_time(monkeypatch):
    inputs = {"journal": _COMPLETED, "requested": _REQUEST, "expected_names": ["a"]}
    result = _run(monkeypatch, "post_activation_waiver", inputs)
    assert result["record"] is True
    assert result["waiver"]["flag"] == "acm_switchover_operation.skip_managed_cluster_expectations"
    assert result["waiver"]["outcome"] == "waived" and result["waiver"]["journaled_at"]
    assert _run(monkeypatch, "post_activation_waiver", {"journal": _COMPLETED, "expected_names": ["a"]}) == {
        "changed": False,
        "waiver": None,
        "record": False,
    }


def test_the_evidence_candidates_are_timestamped_and_returned_for_the_checkpoint(monkeypatch):
    readiness = {"ready": True, "code": None, "names_waived": False}
    named = _run(monkeypatch, "post_activation_names_evidence", {"journal": _COMPLETED, "readiness": readiness})
    assert named["write"] is True and named["candidate"]["post_activation"]["names_verified_at"]
    done = _run(monkeypatch, "post_activation_completion", {"journal": named["candidate"]})
    assert done["write"] is True and done["candidate"]["post_activation"]["completed_at"]


# -- R4-04 Task 10: the Restore-cleanup steps ------------------------------------------------------


def _journal(case_id):
    return _VECTORS[case_id]["input"]["candidate"]


@pytest.mark.parametrize(
    ("read", "expected"),
    [
        ({"read_status": "not_found", "resources": []}, {"changed": False}),
        (
            {
                "read_status": "ok",
                "resources": [
                    {
                        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                        "kind": "Restore",
                        "metadata": {
                            "name": "acm-restore",
                            "namespace": "open-cluster-management-backup",
                            "uid": "u",
                            "resourceVersion": "9",
                        },
                        "spec": {},
                    }
                ],
            },
            "locator_occupied",
        ),
        (
            {"read_status": "ok", "resources": [{"metadata": {"uid": "u", "resourceVersion": "9"}}]},
            "malformed_restore_read",
        ),
        ({"read_status": "error", "resources": []}, "Restore acm-restore could not be read (error)"),
    ],
    ids=["proven-absent", "occupied", "truncated", "unreadable"],
)
def test_a_cleanup_step_reads_the_locator_strictly(monkeypatch, read, expected):
    result = _run(
        monkeypatch, "cleanup_locator_absent", {"journal": _journal("journal-cleanup-completed"), "live_read": read}
    )
    if isinstance(expected, dict):
        assert result == expected
    else:
        assert result["failed"] is True and expected in result["msg"]


@pytest.mark.parametrize(
    "inputs, expected",
    [
        ({"live_read": {"read_status": "not_found", "resources": []}}, {"changed": False}),
        (
            {"live_read": {"read_status": "ok", "resources": [{"metadata": {"uid": "u", "resourceVersion": "9"}}]}},
            "malformed_restore_read",
        ),
        ({"live_read": {"read_status": "error", "resources": []}}, "Restore acm-restore could not be read (error)"),
        ({"journal": None, "live_read": {"read_status": "not_found", "resources": []}}, "journal_required"),
    ],
    ids=["proven-absent", "truncated", "unreadable", "no-journal"],
)
def test_the_old_hub_gate_reads_the_locator_strictly(monkeypatch, inputs, expected):
    """Plan Task 11: the gate consumes one strict locator read; unreadable is never absent."""
    journal = {} if inputs.get("journal", True) is None else {"journal": _journal("journal-cleanup-completed")}
    result = _run(monkeypatch, "old_hub_evidence", {**journal, "live_read": inputs["live_read"]})
    if isinstance(expected, dict):
        assert result == expected
    else:
        assert result["failed"] is True and expected in result["msg"]


def test_cleanup_intent_mints_one_operation_id_unless_supplied(monkeypatch):
    journal = _journal("journal-teardown-revalidated")
    minted = _run(monkeypatch, "cleanup_intent", {"journal": journal})["candidate"]["cleanup"]
    assert uuid.UUID(minted["operation_id"]) and minted["state"] == "intent_persisted"
    supplied = "11111111-2222-4333-8444-555555555555"
    pinned = _run(monkeypatch, "cleanup_intent", {"journal": journal, "operation_id": supplied})
    assert pinned["candidate"]["cleanup"]["operation_id"] == supplied


def test_the_delete_attempt_survives_the_text_rendering_of_the_core_floor(monkeypatch):
    result = _run(
        monkeypatch,
        "cleanup_delete_outcome",
        {
            "journal": _journal("journal-cleanup-intent-persisted"),
            "result": {"accepted": False, "reason": "precondition_failed"},
            "resource_version": "2002",
            "attempt": "4",
        },
    )
    assert result["failed"] is True
    assert result["msg"].startswith("Migration evidence refused (cleanup_conflict_retries_exhausted:")


def test_the_repair_flag_survives_the_text_rendering_of_the_core_floor(monkeypatch):
    result = _run(
        monkeypatch,
        "cleanup_step",
        {"journal": _journal("journal-cleanup-recovery-required"), "repair_requested": "True"},
    )
    assert result == {"changed": False, "step": "repair"}


# -- Task 10 review round 2: the polling deadline holds at classification ---------------------------


@pytest.mark.parametrize("operation", ["cleanup_absence_poll", "cleanup_final_absence"])
def test_a_poll_decision_past_the_deadline_is_refused(monkeypatch, operation):
    """Edge 1: the decision itself re-checks elapsed time; a cached earlier check never authorizes it."""
    result = _run(
        monkeypatch,
        operation,
        {
            "journal": _journal("journal-cleanup-delete-accepted"),
            "live_read": {"read_status": "not_found", "resources": []},
            "deadline": str(time.time() - 0.1),
        },
    )
    assert result["failed"] is True
    assert result["msg"].startswith("Migration evidence refused (poll_deadline_passed:")


def test_a_final_decision_within_the_deadline_completes(monkeypatch):
    result = _run(
        monkeypatch,
        "cleanup_final_absence",
        {
            "journal": _journal("journal-cleanup-delete-accepted"),
            "live_read": {"read_status": "not_found", "resources": []},
            "deadline": str(time.time() + 60),
        },
    )
    assert result["state"] == "completed"


@pytest.mark.parametrize(("remaining", "expected"), [(0.2, None), (1.7, 1), (299.5, 30)])
def test_the_poll_read_timeout_is_the_floored_remaining_budget(monkeypatch, remaining, expected):
    """Edge 2: under a second left, no read is allowed."""
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)
    result = _run(monkeypatch, "cleanup_poll_read_timeout", {"deadline": str(1000.0 + remaining)})
    assert result == {"changed": False, "request_timeout": expected}
