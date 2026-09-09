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
    GATE_REASON_ACK_NOT_APPLICABLE,
    GATE_REASON_DESTINATION_ABSENT,
    GATE_REASON_DESTINATION_UNVERIFIABLE,
    GATE_REASON_SOURCE_AMBIGUOUS,
    GATE_REASON_SOURCE_UNVERIFIABLE,
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


# --------------------------------------------------------------------------------
# Task C5: the destination-observability gate, one row per Python decision-table case.
# --------------------------------------------------------------------------------


def _gated(**kwargs) -> dict:
    """One integrated run with a destination hub, quiet about the other two families."""
    kwargs.setdefault("managed_clusters_outcome", "precondition_noop")
    kwargs.setdefault("multiclusterhub_outcome", "precondition_noop")
    return run_decommission_role(**kwargs)


def _blocked(result: dict, reason: str) -> None:
    """Every blocked case: the reason, no DELETE, and no durable write at all."""
    assert result["returncode"] != 0
    assert result["gate"] == {"decision": "blocked", "reason": reason}
    assert _mco_deletes(result) == []
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]
    assert result["acm_switchover_decommission_result"]["substeps"]["observability"] == "failed"


def test_a_migrated_destination_lets_the_teardown_proceed():
    result = _gated(destination_mco="present", destination_namespace="present")

    assert result["returncode"] == 0
    assert result["gate"] == {"decision": "proceed", "reason": ""}
    assert len(_mco_deletes(result)) == 1


@pytest.mark.parametrize("destination_mco", ["absent", "absent_crd"], ids=["empty-inventory", "crd-absent"])
def test_a_destination_without_observability_blocks_without_the_acknowledgement(destination_mco):
    """An empty-but-complete inventory is absence too: the source clean-skip rule is not reused."""
    _blocked(
        _gated(destination_mco=destination_mco, destination_namespace="absent"),
        GATE_REASON_DESTINATION_ABSENT,
    )


def test_a_destination_without_observability_proceeds_with_the_acknowledgement():
    result = _gated(
        destination_mco="absent",
        destination_namespace="absent",
        acknowledge_observability_not_migrated=True,
    )

    assert result["returncode"] == 0
    assert result["gate"] == {"decision": "proceed", "reason": ""}
    assert len(_mco_deletes(result)) == 1


@pytest.mark.parametrize(
    "destination_mco, destination_namespace",
    [("unverifiable", "absent"), ("absent", "unverifiable")],
    ids=["inventory-unreadable", "namespace-unreadable"],
)
def test_an_unverifiable_destination_blocks_even_with_the_acknowledgement(destination_mco, destination_namespace):
    _blocked(
        _gated(
            destination_mco=destination_mco,
            destination_namespace=destination_namespace,
            acknowledge_observability_not_migrated=True,
        ),
        GATE_REASON_DESTINATION_UNVERIFIABLE,
    )


@pytest.mark.parametrize(
    "destination_mco, destination_namespace",
    [("present", "absent"), ("absent", "present")],
    ids=["cr-without-namespace", "namespace-without-cr"],
)
def test_a_readable_but_mixed_destination_is_unverifiable_not_absent(destination_mco, destination_namespace):
    """Partial presence proves neither coherent presence nor complete absence."""
    _blocked(
        _gated(
            destination_mco=destination_mco,
            destination_namespace=destination_namespace,
            acknowledge_observability_not_migrated=True,
        ),
        GATE_REASON_DESTINATION_UNVERIFIABLE,
    )


def test_the_two_destination_blocking_reasons_are_never_conflated():
    absent = _gated(destination_mco="absent", destination_namespace="absent")
    unverifiable = _gated(destination_mco="unverifiable", destination_namespace="absent")

    assert absent["gate"]["reason"] != unverifiable["gate"]["reason"]


def test_the_acknowledgement_is_refused_when_the_gate_would_pass_anyway():
    _blocked(
        _gated(
            destination_mco="present",
            destination_namespace="present",
            acknowledge_observability_not_migrated=True,
        ),
        GATE_REASON_ACK_NOT_APPLICABLE,
    )


def test_a_half_removed_source_blocks_and_reads_no_destination():
    """CR present, namespace gone: the gate refuses before it looks at the destination."""
    result = _gated(
        mco_present=True,
        observability_namespace="absent",
        destination_mco="present",
        destination_namespace="present",
    )

    _blocked(result, GATE_REASON_SOURCE_AMBIGUOUS)
    assert result["destination_requests"] == []


def test_an_unverifiable_source_never_reads_as_nothing_to_delete():
    """The gate's own fresh source read fails after the phase machine's read succeeded."""
    result = _gated(
        mco_read_statuses=[200, 403],
        destination_mco="present",
        destination_namespace="present",
    )

    _blocked(result, GATE_REASON_SOURCE_UNVERIFIABLE)
    assert result["destination_requests"] == []


def test_a_proven_absent_source_is_not_applicable_and_reads_no_destination():
    result = _gated(
        mco_present=False,
        observability_namespace="absent",
        destination_mco="absent",
        destination_namespace="absent",
    )

    assert result["returncode"] == 0
    assert result["gate"] == {"decision": "not_applicable", "reason": ""}
    assert result["acm_switchover_decommission_result"]["substeps"]["observability"] == "precondition_noop"
    assert result["destination_requests"] == []


@pytest.mark.parametrize("phase", ["cr_absent", "drain_pending", "drained", "recovery_required"])
def test_a_nonterminal_record_with_an_absent_source_keeps_its_obligations(phase):
    """NOT_APPLICABLE with a record is not a clean no-op; the drain proof still runs."""
    result = _gated(
        mco_present=False,
        observability_namespace="absent",
        mco_record=_record(phase),
        destination_mco="absent",
        destination_namespace="absent",
    )
    record = result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY]

    assert result["returncode"] == 0
    assert result["gate"] == {"decision": "not_applicable", "reason": ""}
    assert record["phase"] == "completed"
    assert _mco_deletes(result) == []
    assert result["destination_requests"] == []


def test_a_completed_record_reproves_unaffected_by_a_destination_that_would_block():
    """A completed record has no DELETE to authorize, so no destination read gates it."""
    completed = _completed_record()
    result = _gated(
        mco_present=False,
        observability_namespace="absent",
        mco_record=completed,
        destination_mco="absent",
        destination_namespace="absent",
    )

    assert result["returncode"] == 0
    assert result["checkpoint"]["operational_data"]["decommission_teardown_records"][MCO_KEY] == completed
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]
    assert result["destination_requests"] == []


@pytest.mark.parametrize("execution_mode, check_mode", [("execute", True), ("dry_run", False)])
def test_a_preview_evaluates_the_gate_and_fails_on_a_predicted_blocker(execution_mode, check_mode):
    """A predicted blocker is a real preview result; no writer and no DELETE run."""
    result = _gated(
        execution_mode=execution_mode,
        check_mode=check_mode,
        destination_mco="absent",
        destination_namespace="absent",
    )

    assert result["returncode"] != 0
    assert result["gate"] == {"decision": "blocked", "reason": GATE_REASON_DESTINATION_ABSENT}
    assert _mco_deletes(result) == []
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]
    assert result["destination_requests"]


def test_a_configuration_that_deletes_nothing_is_never_gated():
    """has_observability=false makes the substep NOT_REQUESTED in Python, which never gates."""
    result = _gated(
        configured_has_observability=False,
        destination_mco="absent",
        destination_namespace="absent",
    )

    assert result["returncode"] == 0
    assert result["gate"] is None
    assert result["acm_switchover_decommission_result"]["substeps"]["observability"] == "not_requested"
    assert result["destination_requests"] == []


def test_the_standalone_playbook_never_reaches_the_gate():
    """No `acm_switchover_hubs.secondary` means the gate file is not included at all."""
    result = run_decommission_role(
        standalone_playbook=True,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )
    gate_task_names = {
        "Read the destination MultiClusterObservability inventory",
        "Read the destination observability namespace",
        "Require an explicit destination hub for the observability gate",
    }

    assert result["returncode"] == 0
    assert result["gate"] is None
    assert gate_task_names.isdisjoint({task["name"] for task in result["tasks"]})
    assert len(_mco_deletes(result)) == 1


def test_the_integrated_decommission_disposition_gates_through_handle_old_hub():
    """The REAL integrated path: handle_old_hub.yml -> the decommission role -> the gate."""
    result = run_decommission_role(
        integrated_finalization=True,
        destination_mco="absent",
        destination_namespace="absent",
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )

    assert result["returncode"] != 0
    assert result["gate"] == {"decision": "blocked", "reason": GATE_REASON_DESTINATION_ABSENT}
    assert _mco_deletes(result) == []
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]


def test_the_integrated_decommission_disposition_honours_the_acknowledgement():
    """old_hub_action=decommission is the ONLY path where the acknowledgement is valid.

    Scope of the proof: the acknowledgement reaches the gate and converts a proven
    absent destination on the real integrated path. It does NOT isolate
    `handle_old_hub.yml`'s embedded-settings rebuild, because the harness supplies
    `acm_switchover_decommission` as an extra var, which outranks the `vars:` on that
    `include_role`. The rebuild itself is pinned statically by
    test_embedded_finalization_decommission_passes_scoped_confirmation.
    """
    result = run_decommission_role(
        integrated_finalization=True,
        destination_mco="absent",
        destination_namespace="absent",
        acknowledge_observability_not_migrated=True,
        managed_clusters_outcome="precondition_noop",
        multiclusterhub_outcome="precondition_noop",
    )

    assert result["returncode"] == 0
    assert result["gate"] == {"decision": "proceed", "reason": ""}
    deletes = _mco_deletes(result)
    assert len(deletes) == 1
    assert deletes[0]["body"]["preconditions"]["uid"] == "mco-uid-1"


# --------------------------------------------------------------------------------
# Task C5: the finalization secondary-disposition adapter, through the REAL role file.
# --------------------------------------------------------------------------------


def _adapter(**kwargs) -> dict:
    kwargs.setdefault("destination_mco", "present")
    kwargs.setdefault("destination_namespace", "present")
    return run_decommission_role(integrated_finalization_secondary=True, **kwargs)


def _adapter_result(result: dict) -> dict:
    return result["facts"]["acm_switchover_disable_old_hub_observability_result"]


def test_the_adapter_deletes_through_the_guarded_module_with_the_recorded_uid():
    result = _adapter()
    deletes = _mco_deletes(result)

    assert result["returncode"] == 0
    assert len(deletes) == 1
    assert deletes[0]["body"]["preconditions"]["uid"] == "mco-uid-1"
    assert _adapter_result(result) == {
        "changed": True,
        "deleted_mcos": ["observability"],
        "status": "pass",
    }


def test_the_adapter_preserves_the_finalization_checkpoint_phase_and_identity():
    """The shared task writes teardown DATA only; it owns no phase lifecycle here."""
    result = _adapter()

    assert result["checkpoint"]["completed_phases"] == ["preflight"]
    assert result["operation_identity"] == result["seeded_operation_identity"]
    assert result["checkpoint"]["phases"] == []


def test_the_adapter_warns_about_gitops_managed_observability_before_deleting():
    result = _adapter()
    warning = "Warn that GitOps-managed MultiClusterObservability deletion must be coordinated"
    # Skipped tasks are recorded too, so "the task exists" proves nothing; it must run.
    executed = [task["name"] for task in result["tasks"] if not task["skipped"]]

    assert warning in executed
    assert executed.index(warning) < executed.index("Delete the recorded MultiClusterObservability")


def test_the_adapter_suppresses_the_gitops_warning_when_the_check_is_skipped():
    result = _adapter(skip_gitops_check=True)
    warned = [
        task
        for task in result["tasks"]
        if task["name"] == "Warn that GitOps-managed MultiClusterObservability deletion must be coordinated"
        and not task["skipped"]
    ]

    assert warned == []
    assert len(_mco_deletes(result)) == 1


def test_the_adapter_previews_without_deleting_and_reports_skipped():
    result = _adapter(execution_mode="dry_run")

    assert result["returncode"] == 0
    assert _mco_deletes(result) == []
    assert _adapter_result(result) == {"changed": False, "deleted_mcos": [], "status": "skipped"}
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]


def test_the_adapter_runs_the_destination_gate_and_refuses_a_blocked_teardown():
    result = _adapter(destination_mco="absent", destination_namespace="absent")

    assert result["returncode"] != 0
    assert result["gate"] == {"decision": "blocked", "reason": GATE_REASON_DESTINATION_ABSENT}
    assert _mco_deletes(result) == []
    assert result["checkpoint"]["operational_data"] == result["checkpoint"]["before_operational_data"]


def test_the_adapter_refuses_execute_mode_without_durable_checkpoint_state():
    """The shipped finalization default is checkpoint.enabled=false; fail closed, not mid-teardown."""
    result = _adapter(checkpoint_available=False)
    failed = {task["name"] for task in result["tasks"] if task["failed"]}

    assert result["returncode"] != 0
    assert "Require durable checkpoint state before the old hub observability teardown" in failed
    assert _mco_deletes(result) == []


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
