"""Integrated proof that decommission check mode and operator dry-run compose.

Native ``ansible-playbook --check`` and ``acm_switchover_execution.mode=dry_run``
are different layers. Both are exercised through ``run_decommission_role``, the
one B4.1 harness. ``run_role_check_mode`` only forces ``check_mode=True``.
"""

from __future__ import annotations

import json

import pytest
from test_decommission_role_contracts import (
    CHECK_MODE_NATIVE_MODULES,
    _kube_system_reads,
    _task_actions,
    decommission_task_files,
    mutating_tasks,
    run_decommission_role,
)
from yaml_contract_helpers import _when_text

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    GATE_REASON_DESTINATION_ABSENT,
)

_GUARDED_DELETE = "tomazb.acm_switchover.acm_uid_guarded_delete"
_GUARDED_DELETE_TASKS = (
    "Delete the recorded MultiClusterObservability",
    "Delete the recorded ManagedCluster",
    "Delete the recorded MultiClusterHub",
)
_ENTER = "Enter the standalone decommission checkpoint phase"
_PASS = "Pass the standalone decommission checkpoint phase"
_SUMMARY_KEYS = frozenset(
    {
        "phase",
        "mode",
        "status",
        "substeps",
        "has_observability",
        "changed",
        "would_change",
    }
)
_EVIDENCE_FIELDS = ("observed_at", "resource_versions", "absence_proofs")


def run_role_check_mode(**options):
    """Native check mode through the canonical harness. Not a second result shape."""
    return run_decommission_role(check_mode=True, **options)


def _summary(result):
    return result["acm_switchover_decommission_result"]


def _ran(result, name):
    return [task for task in result["tasks"] if task["name"] == name and not task["skipped"]]


def _named(result, name):
    return [task for task in result["tasks"] if task["name"] == name]


def _gets(result, fragment):
    return [request for request in result["requests"] if request["method"] == "GET" and fragment in request["path"]]


def _assert_preview_persists_nothing(result):
    """Durable checkpoint bytes are unchanged, including completion evidence."""
    checkpoint = result["checkpoint"]
    assert checkpoint["operational_data"] == checkpoint["before_operational_data"]
    serialized = json.dumps(checkpoint["operational_data"])
    for field in _EVIDENCE_FIELDS:
        assert field not in serialized
    assert "operator_deployment" not in serialized
    assert "operator_identity_unavailable" not in serialized
    assert "decommission" not in checkpoint["completed_phases"]
    seeded_identity = result["seeded_operation_identity"]
    if seeded_identity is None:
        assert not result["operation_identity"]
    else:
        assert result["operation_identity"] == seeded_identity
    assert result["delete_calls"] == []


def _assert_guarded_deletes_ran_without_deleting(result):
    """Each family reaches the guarded-delete module, which reads and does not delete."""
    for name in _GUARDED_DELETE_TASKS:
        ran = _ran(result, name)
        assert ran, f"{name} did not run; a no-delete assertion would be vacuous"
        assert all(task["module"] == _GUARDED_DELETE for task in ran)
        for task in ran:
            # no_log censors the module return, so prediction is judged on the
            # published summary and on the callback changed flag, not the body.
            assert task["changed"] is False
    assert _gets(result, "multiclusterobservabilities"), "MCO preview must read the target"
    assert _gets(result, "managedclusters"), "ManagedCluster preview must read the target"
    assert _gets(result, "multiclusterhubs"), "MultiClusterHub preview must read the target"


def _assert_prediction_is_separate(result, *, mode):
    summary = _summary(result)
    assert set(summary) == _SUMMARY_KEYS
    assert "preview" not in summary
    assert summary["mode"] == mode
    assert summary["changed"] is False
    assert summary["would_change"] is True
    assert summary["substeps"] == {}
    assert summary["status"] == "pass"
    assert result["returncode"] == 0
    if mode == "execute":
        # Native check mode skips the summary writer. Operator dry-run does not:
        # with a summary path the artifact module honestly reports changed.
        assert not [task for task in result["tasks"] if task["changed"]]


def _assert_namespace_detection_ran(result, *, read_returns_resources: bool):
    """Automatic observability detection stays in the run.

    Operator dry-run receives the Namespace from ``k8s_info``. Native check mode
    still executes the task; ``k8s_info`` may return no resource body under
    ``--check``, so the live reads proved for that layer are the guarded-delete
    GETs.
    """
    tasks = _ran(result, "Detect observability namespace when configured for auto")
    assert tasks, "automatic observability detection did not run"
    if not read_returns_resources:
        return
    resources = tasks[0]["result"].get("resources") or []
    names = [(item.get("metadata") or {}).get("name") for item in resources if isinstance(item, dict)]
    assert "open-cluster-management-observability" in names, names


def _assert_rbac_validation_is_explicitly_skipped(result):
    """The canonical harness sets ``skip_rbac_validation``. SSARs must not run."""
    skipped = _named(result, "Validate RBAC before decommission")
    assert skipped, "the RBAC include is missing; skip assertions would be vacuous"
    assert all(task["skipped"] for task in skipped)
    assert not [request for request in result["requests"] if "selfsubjectaccessreviews" in request["path"]]


@pytest.fixture(scope="module")
def native_check():
    """One role-level ``--check`` run with every family present."""
    return run_role_check_mode()


@pytest.fixture(scope="module")
def operator_dry_run():
    """Operator dry-run, not native check mode, with every family present."""
    return run_decommission_role(execution_mode="dry_run")


def test_check_mode_writes_no_checkpoint_transition(native_check):
    """Check mode reaches the delete modules and writes no teardown transition."""
    _assert_guarded_deletes_ran_without_deleting(native_check)
    _assert_preview_persists_nothing(native_check)
    assert native_check["checkpoint"]["operational_data"].get("decommission_teardown_records") is None


def test_check_mode_writes_no_completion_evidence(native_check):
    """A revision may be read. It is not stored as completion evidence."""
    _assert_guarded_deletes_ran_without_deleting(native_check)
    _assert_preview_persists_nothing(native_check)


def test_check_mode_reports_no_change_and_predicts_separately(native_check):
    _assert_prediction_is_separate(native_check, mode="execute")


def test_no_task_reports_changed_true_in_check_mode(native_check):
    assert _ran(native_check, _GUARDED_DELETE_TASKS[0])
    assert not [task for task in native_check["tasks"] if task["changed"]]


def test_every_mutating_task_declares_check_mode_handling():
    """Mutators are either native check-mode modules or skipped unless executing.

    ``mutating_tasks`` is an inverted allowlist, so a new task is included until
    it is classified. Native guarded deletes must declare ``check_mode`` so an
    operator dry-run reaches the module's check-mode branch without ``--check``.
    """
    mutators = mutating_tasks(decommission_task_files)
    assert mutators, "the role must own a mutation-capable task"
    native_deletes = []
    for task in mutators:
        actions = set(_task_actions(task))
        if actions & set(CHECK_MODE_NATIVE_MODULES):
            assert "check_mode" in task, task.get("name")
            if _GUARDED_DELETE in actions:
                native_deletes.append(task)
        else:
            assert "not ansible_check_mode" in _when_text(task), task.get("name")
    assert len(native_deletes) >= 3


def test_operator_dry_run_is_not_native_check_mode(operator_dry_run):
    """Dry-run still runs the guarded deletes via the task ``check_mode`` parameter.

    The published mode stays ``dry_run``. Native check mode publishes ``execute``.
    """
    _assert_guarded_deletes_ran_without_deleting(operator_dry_run)
    _assert_preview_persists_nothing(operator_dry_run)
    _assert_prediction_is_separate(operator_dry_run, mode="dry_run")
    _assert_namespace_detection_ran(operator_dry_run, read_returns_resources=True)
    _assert_rbac_validation_is_explicitly_skipped(operator_dry_run)


def test_check_mode_keeps_namespace_detection_and_skips_rbac(native_check):
    _assert_namespace_detection_ran(native_check, read_returns_resources=False)
    _assert_rbac_validation_is_explicitly_skipped(native_check)


def test_standalone_check_mode_skips_checkpoint_transitions_and_still_previews():
    result = run_role_check_mode(standalone_playbook=True)
    for name in (_ENTER, _PASS):
        tasks = _named(result, name)
        assert tasks, f"{name} is not in the play; skip assertions would be vacuous"
        assert all(task["skipped"] for task in tasks)
    _assert_guarded_deletes_ran_without_deleting(result)
    _assert_preview_persists_nothing(result)
    _assert_prediction_is_separate(result, mode="execute")
    assert _kube_system_reads(result["requests"]) == []


def test_standalone_dry_run_runs_checkpoint_transitions_without_persisting():
    result = run_decommission_role(standalone_playbook=True, execution_mode="dry_run")
    assert [task["name"] for task in result["checkpoint"]["phases"]] == [_ENTER, _PASS]
    _assert_preview_persists_nothing(result)
    _assert_guarded_deletes_ran_without_deleting(result)
    _assert_prediction_is_separate(result, mode="dry_run")
    assert _kube_system_reads(result["requests"]) == []


def test_operator_dry_run_reports_a_predicted_destination_blocker():
    """The destination gate is a preview result. It blocks before any delete."""
    result = run_decommission_role(
        execution_mode="dry_run",
        destination_mco="absent",
        destination_namespace="absent",
    )
    assert result["returncode"] != 0
    assert result["gate"] == {
        "decision": "blocked",
        "reason": GATE_REASON_DESTINATION_ABSENT,
    }
    assert result["destination_requests"], "the gate must read the destination"
    assert _gets(result, "multiclusterobservabilities"), "the source target must be read before the gate"
    _assert_preview_persists_nothing(result)
    assert _summary(result)["changed"] is False
    # The gate include fails the play before any guarded delete. A changed-flag
    # filter would pass even if those tasks were absent.
    for name in _GUARDED_DELETE_TASKS:
        assert not _ran(result, name), f"{name} ran after the destination gate blocked"
    assert _summary(result)["substeps"] == {}
