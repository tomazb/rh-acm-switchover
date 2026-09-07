"""Runtime contracts for the collection MultiClusterObservability teardown."""

from __future__ import annotations

import copy
import pathlib
import sys
from urllib.parse import unquote

import pytest

_UNIT_TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_UNIT_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_UNIT_TESTS_DIR))

from test_decommission_role_contracts import (  # noqa: E402
    _mco_object,
    run_decommission_role,
)

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (  # noqa: E402
    teardown_key,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (  # noqa: E402
    OBSERVABILITY_POD_LABEL_SELECTOR,
)

MCO_KEY = teardown_key(
    "observability.open-cluster-management.io/v1beta2",
    "MultiClusterObservability",
    None,
    "observability",
)
OBSERVABILITY_NAMESPACE_KEY = teardown_key(
    "v1",
    "Namespace",
    None,
    "open-cluster-management-observability",
)


def _record(phase: str, uid: str = "mco-uid-1") -> dict:
    return {"expected_uid": uid, "phase": phase}


def _completed_record() -> dict:
    return {
        **_record("completed"),
        "observed_at": "2026-09-04T00:00:00Z",
        "resource_versions": {"drain_namespace": "old-ns", "drain_pods": "old-pods"},
        "absence_proofs": {"target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY}},
    }


def _mco_deletes(result: dict) -> list:
    return [call for call in result["delete_calls"] if "multiclusterobservabilities" in call["path"]]


@pytest.mark.parametrize("execution_mode, check_mode", [("execute", True), ("dry_run", False)])
def test_preview_reads_and_predicts_without_delete_write_or_drain_wait(execution_mode, check_mode):
    result = run_decommission_role(
        execution_mode=execution_mode,
        check_mode=check_mode,
        observability_pods=["still-running"],
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )

    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] == 0
    assert summary["changed"] is False
    assert summary["would_change"] is True
    assert summary["substeps"] == {}
    assert _mco_deletes(result) == []
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]
    assert not [request for request in result["requests"] if "/pods" in request["path"]]


def test_preview_of_clean_absence_predicts_no_change():
    result = run_decommission_role(
        check_mode=True,
        mco_present=False,
        observability_namespace="absent",
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    assert result["acm_switchover_decommission_result"]["would_change"] is False
    assert _mco_deletes(result) == []


@pytest.mark.parametrize("execution_mode,check_mode", [("execute", True), ("dry_run", False)])
def test_preview_uses_the_guarded_fresh_read_when_target_disappears(execution_mode, check_mode):
    result = run_decommission_role(
        execution_mode=execution_mode,
        check_mode=check_mode,
        mco_named_read_status=404,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] == 0
    assert summary["changed"] is False
    assert summary["would_change"] is False
    assert _mco_deletes(result) == []


def test_execute_delete_carries_the_expected_uid_precondition_and_scoped_selector():
    result = run_decommission_role(
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )

    deletes = _mco_deletes(result)
    assert len(deletes) == 1
    assert deletes[0]["expected_uid"] == "mco-uid-1"
    assert deletes[0]["body"]["preconditions"]["uid"] == "mco-uid-1"
    pod_reads = [request for request in result["requests"] if "/pods" in request["path"]]
    assert pod_reads
    assert all(OBSERVABILITY_POD_LABEL_SELECTOR in unquote(request["query"]) for request in pod_reads)


def test_completed_record_carries_exact_namespace_present_evidence():
    result = run_decommission_role(
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    record = result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY]

    assert record["phase"] == "completed"
    assert set(record["resource_versions"]) == {"drain_namespace", "drain_pods"}
    assert set(record["absence_proofs"]) == {"target_cr"}
    assert record["absence_proofs"]["target_cr"] == {
        "proof_type": "object_absent",
        "resource_key": MCO_KEY,
    }


def test_completed_record_carries_exact_namespace_absent_evidence():
    result = run_decommission_role(
        observability_namespace="absent",
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    record = result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY]

    assert record["phase"] == "completed"
    assert record["resource_versions"] == {}
    assert record["absence_proofs"] == {
        "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY},
        "drain_namespace": {
            "proof_type": "namespace_absent",
            "resource_key": OBSERVABILITY_NAMESPACE_KEY,
        },
    }


def test_failure_after_delete_started_keeps_evidence_absent_and_issues_no_delete():
    result = run_decommission_role(
        mco_named_read_status=500,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    record = result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY]

    assert result["returncode"] != 0
    assert record == _record("delete_started")
    assert _mco_deletes(result) == []


def test_failure_after_accepted_delete_reports_actual_change():
    result = run_decommission_role(
        mco_post_delete_read_status=500,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )

    assert result["returncode"] != 0
    assert result["acm_switchover_decommission_result"]["status"] == "fail"
    assert result["acm_switchover_decommission_result"]["changed"] is True
    assert len(_mco_deletes(result)) == 1


def test_drain_read_error_is_not_retried_into_a_false_completion():
    result = run_decommission_role(
        pod_read_statuses=[403, 200, 200],
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    records = result["checkpoint"]["operational_data"]["decommission_teardown_records"]
    record = records[MCO_KEY]

    assert result["returncode"] != 0
    assert result["acm_switchover_decommission_result"]["status"] == "fail"
    assert record["phase"] == "drain_pending"
    assert "observed_at" not in record
    assert len([request for request in result["requests"] if "/pods" in request["path"]]) == 1


@pytest.mark.parametrize(
    "inventory",
    [
        [_mco_object("observability"), _mco_object("observability", "mco-uid-2")],
        [_mco_object("another-name")],
        [_mco_object("observability"), _mco_object("another-name", "mco-uid-2")],
        [
            {
                "apiVersion": "observability.open-cluster-management.io/v1beta2",
                "kind": "MultiClusterObservability",
                "metadata": {"uid": "mco-uid-1"},
            }
        ],
    ],
    ids=["duplicate-target", "noncanonical", "target-plus-extra", "malformed"],
)
def test_ambiguous_or_noncanonical_inventory_fails_closed(inventory):
    result = run_decommission_role(
        mco_inventory=inventory,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    assert result["returncode"] != 0
    assert _mco_deletes(result) == []
    assert "Fail closed on an ambiguous MultiClusterObservability inventory" in {
        task["name"] for task in result["tasks"] if task["failed"]
    }


def test_unverifiable_inventory_fails_closed_in_check_mode():
    result = run_decommission_role(check_mode=True, observability_read_status=403)
    assert result["returncode"] != 0
    assert result["acm_switchover_decommission_result"]["status"] == "fail"
    assert _mco_deletes(result) == []


def test_delete_started_resume_retries_the_same_uid_delete():
    result = run_decommission_role(
        mco_record=_record("delete_started"),
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    assert result["returncode"] == 0
    assert len(_mco_deletes(result)) == 1


@pytest.mark.parametrize("phase", ["cr_absent", "drain_pending", "drained", "completed"])
def test_post_delete_phase_reappearance_blocks_even_with_the_same_uid(phase):
    record = _completed_record() if phase == "completed" else _record(phase)
    result = run_decommission_role(
        mco_record=record,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    assert result["returncode"] != 0
    assert _mco_deletes(result) == []
    assert "Refuse a completed or post-delete record whose target is present again" in {
        task["name"] for task in result["tasks"] if task["failed"]
    }


@pytest.mark.parametrize("phase", ["cr_absent", "drain_pending", "drained", "recovery_required"])
def test_absent_target_resume_finishes_the_recorded_drain_obligation(phase):
    result = run_decommission_role(
        mco_present=False,
        mco_record=_record(phase),
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    record = result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY]
    assert result["returncode"] == 0
    assert record["phase"] == "completed"
    assert _mco_deletes(result) == []


def test_stale_false_configuration_cannot_skip_a_recorded_drain_obligation():
    result = run_decommission_role(
        mco_present=False,
        mco_record=_record("cr_absent"),
        configured_has_observability=False,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    record = result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY]
    assert result["returncode"] == 0
    assert record["phase"] == "completed"


def test_completed_resume_reproves_without_rewriting_the_record():
    completed = _completed_record()
    result = run_decommission_role(
        mco_present=False,
        mco_record=completed,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )

    stored = result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY]
    assert result["returncode"] == 0
    assert stored == completed
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]
    assert _mco_deletes(result) == []
    assert len([request for request in result["requests"] if "multiclusterobservabilities" in request["path"]]) >= 2
    assert [request for request in result["requests"] if "/pods" in request["path"]]


def test_no_record_half_removed_state_is_refused():
    result = run_decommission_role(
        mco_present=False,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    assert result["returncode"] != 0
    assert _mco_deletes(result) == []
    assert "Refuse a no-record half-removed observability installation" in {
        task["name"] for task in result["tasks"] if task["failed"]
    }


def test_no_record_clean_absence_is_a_precondition_noop():
    result = run_decommission_role(
        mco_present=False,
        observability_namespace="absent",
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    summary = result["acm_switchover_decommission_result"]
    assert result["returncode"] == 0
    assert summary["substeps"]["observability"] == "precondition_noop"
    assert summary["would_change"] is False
    assert _mco_deletes(result) == []
