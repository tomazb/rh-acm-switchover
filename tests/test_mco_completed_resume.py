"""Regression coverage for persisted completed MCO teardown resumes."""

from unittest.mock import Mock

import pytest

from lib.constants import OBSERVABILITY_NAMESPACE, OBSERVABILITY_POD_LABEL_SELECTOR
from lib.decommission_outcome import SubstepExecution, SubstepOutcome
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome
from lib.teardown_record import TeardownPhase, teardown_key
from lib.utils import StateManager
from modules.decommission import Decommission
from modules.finalization import Finalization

pytestmark = pytest.mark.unit

MCO_KEY = teardown_key(
    "observability.open-cluster-management.io/v1beta2",
    "MultiClusterObservability",
    None,
    "observability",
)


def _mco(uid="uid-1"):
    return {
        "apiVersion": "observability.open-cluster-management.io/v1beta2",
        "kind": "MultiClusterObservability",
        "metadata": {"name": "observability", "uid": uid, "resourceVersion": "cr-before"},
    }


def _namespace(resource_version):
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": OBSERVABILITY_NAMESPACE, "resourceVersion": resource_version},
    }


def _complete_once_and_reload(tmp_path, drain_mode):
    """Create completion through the real writer, then reload it from disk."""
    state_path = tmp_path / "state.json"
    state = StateManager(str(state_path))
    # The run lock is released in `finally` so a failing assertion above cannot leave
    # it held: the reload below, and every later test that opens a StateManager on this
    # path, would otherwise be blocked by a lock this helper never gave back.
    try:
        client = Mock()
        client.get_custom_resource_strict = Mock(
            side_effect=[
                StrictReadOutcome.from_resource(_mco(), resource_version="cr-before"),
                StrictReadOutcome.object_absent("deleted"),
                StrictReadOutcome.object_absent("final proof"),
            ]
        )
        client.delete_custom_resource_preconditioned = Mock(return_value=None)

        if drain_mode == "namespace_absent":
            client.get_namespace_strict = Mock(
                side_effect=[
                    StrictReadOutcome.namespace_absent("deleted with observability"),
                    StrictReadOutcome.namespace_absent("final proof"),
                ]
            )
            client.list_pods_strict = Mock()
        else:
            client.get_namespace_strict = Mock(
                side_effect=[
                    StrictReadOutcome.from_resource(_namespace("ns-drain"), resource_version="ns-drain"),
                    StrictReadOutcome.from_resource(_namespace("ns-final"), resource_version="ns-final"),
                ]
            )
            client.list_pods_strict = Mock(
                side_effect=[
                    StrictReadOutcome.from_items([], resource_version="pods-drain"),
                    StrictReadOutcome.from_items([], resource_version="pods-final"),
                ]
            )

        execution = Decommission(client, True, run_record=RunRecord(state)).teardown_observability()

        assert execution == SubstepExecution(SubstepOutcome.COMPLETED, changed=True)
        client.delete_custom_resource_preconditioned.assert_called_once()
        completed = RunRecord(state).teardown_record(MCO_KEY)
        assert completed is not None
        assert completed.phase is TeardownPhase.COMPLETED
        persisted_bytes = state_path.read_bytes()

    finally:
        state._release_run_lock()

    reloaded_state = StateManager(str(state_path))
    reloaded_record = RunRecord(reloaded_state)
    assert reloaded_record.teardown_record(MCO_KEY) == completed
    return state_path, persisted_bytes, completed, reloaded_state, reloaded_record


def _completed_client(*, cr, namespace=None, pods=None):
    client = Mock()
    client.get_custom_resource_strict = Mock(return_value=cr)
    client.get_namespace_strict = Mock(return_value=namespace)
    client.list_pods_strict = Mock(return_value=pods)
    client.delete_custom_resource_preconditioned = Mock()
    return client


@pytest.mark.parametrize("drain_mode", ["namespace_absent", "namespace_present"])
def test_completed_resume_reproves_live_state_without_rewriting_evidence(tmp_path, drain_mode):
    """Removing the completed guard would rewrite its immutable evidence and fail this test."""
    state_path, before, completed, state, run_record = _complete_once_and_reload(tmp_path, drain_mode)
    if drain_mode == "namespace_absent":
        namespace = StrictReadOutcome.namespace_absent("fresh completed-run proof")
        pods = None
    else:
        namespace = StrictReadOutcome.from_resource(_namespace("ns-rerun"), resource_version="ns-rerun")
        pods = StrictReadOutcome.from_items([], resource_version="pods-rerun")
    client = _completed_client(
        cr=StrictReadOutcome.object_absent("fresh completed-run proof"),
        namespace=namespace,
        pods=pods,
    )
    writer = Mock(wraps=run_record.record_teardown_phase)
    run_record.record_teardown_phase = writer
    decommission = Decommission(client, True, run_record=run_record)

    first = decommission.teardown_observability()
    assert first == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
    second = decommission.teardown_observability()

    assert second == SubstepExecution(SubstepOutcome.COMPLETED, changed=False)
    assert run_record.teardown_record(MCO_KEY) == completed
    assert state_path.read_bytes() == before
    writer.assert_not_called()
    client.delete_custom_resource_preconditioned.assert_not_called()
    assert client.get_custom_resource_strict.call_count == 2
    assert client.get_namespace_strict.call_count == 2
    if drain_mode == "namespace_present":
        assert client.list_pods_strict.call_count == 2
        client.list_pods_strict.assert_called_with(
            OBSERVABILITY_NAMESPACE,
            label_selector=OBSERVABILITY_POD_LABEL_SELECTOR,
        )
    else:
        client.list_pods_strict.assert_not_called()


def test_finalization_adapter_accepts_a_reproved_completed_record(tmp_path):
    """Finalization must consume the shared completed-resume result without rewriting it."""
    state_path, before, completed, state, run_record = _complete_once_and_reload(tmp_path, "namespace_absent")
    client = _completed_client(
        cr=StrictReadOutcome.object_absent("fresh completed-run proof"),
        namespace=StrictReadOutcome.namespace_absent("fresh completed-run proof"),
    )
    finalization = Finalization(
        secondary_client=Mock(),
        state_manager=state,
        acm_version="2.14.0",
        primary_client=client,
        primary_has_observability=True,
        old_hub_action="secondary",
    )

    finalization._disable_observability_on_old_hub()

    assert run_record.teardown_record(MCO_KEY) == completed
    assert state_path.read_bytes() == before
    client.delete_custom_resource_preconditioned.assert_not_called()


@pytest.mark.parametrize(
    ("cr", "namespace", "pods"),
    [
        (StrictReadOutcome.from_resource(_mco(), resource_version="same-object"), None, None),
        (StrictReadOutcome.from_resource(_mco(uid="replacement"), resource_version="replacement"), None, None),
        (StrictReadOutcome.error("CR read denied"), None, None),
        (StrictReadOutcome.object_absent("fresh proof"), StrictReadOutcome.error("namespace read denied"), None),
        (
            StrictReadOutcome.object_absent("fresh proof"),
            StrictReadOutcome.from_resource(_namespace("ns-rerun"), resource_version="ns-rerun"),
            StrictReadOutcome.error("Pod list denied"),
        ),
        (
            StrictReadOutcome.object_absent("fresh proof"),
            StrictReadOutcome.from_resource(_namespace("ns-rerun"), resource_version="ns-rerun"),
            StrictReadOutcome.from_items(
                [{"metadata": {"name": "observability-blocker"}}], resource_version="pods-rerun"
            ),
        ),
    ],
    ids=["same-target", "replacement", "cr-unreadable", "namespace-unreadable", "pods-unreadable", "pods-remain"],
)
def test_completed_resume_failure_preserves_the_completed_record(tmp_path, cr, namespace, pods):
    """Weakening any fresh completed-run predicate must fail closed without a phase write."""
    state_path, before, completed, state, run_record = _complete_once_and_reload(tmp_path, "namespace_present")
    client = _completed_client(cr=cr, namespace=namespace, pods=pods)
    writer = Mock(wraps=run_record.record_teardown_phase)
    run_record.record_teardown_phase = writer

    execution = Decommission(client, True, run_record=run_record).teardown_observability()

    assert execution == SubstepExecution(SubstepOutcome.FAILED, changed=False)
    assert run_record.teardown_record(MCO_KEY) == completed
    assert state_path.read_bytes() == before
    writer.assert_not_called()
    client.delete_custom_resource_preconditioned.assert_not_called()
