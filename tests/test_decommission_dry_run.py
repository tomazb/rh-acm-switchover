"""Integrated Python proof that a decommission dry-run composes the three families.

The aggregator preview is read-only. It is not Ansible check mode, and it does
not evaluate the destination observability gate; that gate is proved on
``teardown_observability`` because that is the path which owns it.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import Mock, call

import pytest

import tests.test_decommission as decommission_tests
from lib.constants import ACM_NAMESPACE, DELETE_REQUEST_TIMEOUT, OBSERVABILITY_NAMESPACE
from lib.decommission_outcome import DecommissionResult, SubstepOutcome

_PREVIEW_REVISION = "88190"
_RESULT_FIELDS = frozenset({"substeps", "not_attempted", "cancelled", "changed", "would_change"})


@pytest.fixture
def mock_primary_client():
    return decommission_tests.mock_primary_client.__wrapped__()


@pytest.fixture
def state_manager(tmp_path):
    return decommission_tests.state_manager.__wrapped__(tmp_path)


@pytest.fixture
def primary_with_all_resources(mock_primary_client):
    return decommission_tests.primary_with_all_resources.__wrapped__(mock_primary_client)


@pytest.fixture
def decommission_dry_run(mock_primary_client, state_manager):
    return decommission_tests.decommission_dry_run.__wrapped__(mock_primary_client, state_manager)


def _stamp_observability_revision(client, revision=_PREVIEW_REVISION):
    """Put a known revision on the MCO the preview actually reads."""
    original = client.get_custom_resource_strict.side_effect

    def _get(*args, **kwargs):
        outcome = original(*args, **kwargs)
        if kwargs.get("plural") == "multiclusterobservabilities" and outcome.resource is not None:
            outcome.resource["metadata"]["resourceVersion"] = revision
        return outcome

    client.get_custom_resource_strict.side_effect = _get


def _snapshot(state_manager):
    return json.dumps(state_manager.capture_state_snapshot(), sort_keys=True, default=str)


def _assert_no_durable_preview(decommission, state_manager, before):
    assert decommission.run_record.all_teardown_records() == {}
    assert decommission.run_record.teardown_record(decommission_tests.MCO_KEY) is None
    snapshot = _snapshot(state_manager)
    assert snapshot == before
    for field in ("observed_at", "resource_versions", "absence_proofs", "operator_deployment"):
        assert field not in snapshot
    assert _PREVIEW_REVISION not in snapshot


@pytest.fixture
def preview(decommission_dry_run, primary_with_all_resources, state_manager):
    _stamp_observability_revision(primary_with_all_resources)
    before = _snapshot(state_manager)
    result = decommission_dry_run.decommission(interactive=False)
    return {
        "result": result,
        "before": before,
        "decommission": decommission_dry_run,
        "client": primary_with_all_resources,
        "state_manager": state_manager,
    }


def test_dry_run_reads_every_family_and_calls_no_guarded_delete(preview):
    client = preview["client"]
    result = preview["result"]
    assert result.would_change is True
    assert result.changed is False
    assert result.substeps == {}
    assert result.not_attempted == ("observability", "managed_clusters", "multiclusterhub")
    client.delete_custom_resource_preconditioned.assert_not_called()
    client.delete_custom_resource.assert_not_called()
    assert client.get_custom_resource_strict.called
    assert client.list_managed_clusters_strict.called
    assert client.list_custom_resources_strict.called
    assert client.get_deployment_strict.called, "MCH preview must walk the operator owner chain"
    mco_reads = [
        call
        for call in client.get_custom_resource_strict.call_args_list
        if call.kwargs.get("plural") == "multiclusterobservabilities"
    ]
    assert mco_reads, "the observability preview must read the MultiClusterObservability"
    assert set(DecommissionResult.__dataclass_fields__) == _RESULT_FIELDS
    assert not hasattr(result, "preview")


def test_dry_run_persists_no_teardown_record(preview):
    _assert_no_durable_preview(preview["decommission"], preview["state_manager"], preview["before"])


def test_dry_run_persists_no_operator_identity(preview):
    assert preview["client"].get_deployment_strict.called
    _assert_no_durable_preview(preview["decommission"], preview["state_manager"], preview["before"])


def test_dry_run_persists_no_completion_evidence(preview):
    _assert_no_durable_preview(preview["decommission"], preview["state_manager"], preview["before"])


def test_a_revision_observed_during_a_preview_is_never_persisted(preview):
    mco_reads = [
        call
        for call in preview["client"].get_custom_resource_strict.call_args_list
        if call.kwargs.get("plural") == "multiclusterobservabilities"
    ]
    assert mco_reads
    assert _PREVIEW_REVISION not in preview["before"]
    assert _PREVIEW_REVISION not in _snapshot(preview["state_manager"])


def test_dry_run_reports_changed_false_and_would_change_only(preview):
    result = preview["result"]
    assert result.changed is False
    assert result.would_change is True
    assert result.succeeded is True


def test_a_live_run_after_a_dry_run_starts_from_no_record(
    decommission_dry_run, decommission_tests_live, state_manager, mock_primary_client
):
    """The live run's reads happen after the preview, on a record the preview did not write."""
    decommission_dry_run.decommission(interactive=False)
    assert decommission_dry_run.run_record.all_teardown_records() == {}
    mock_primary_client.reset_mock()
    result = decommission_tests_live.decommission(interactive=False)
    assert result.succeeded is True
    assert result.changed is True
    mco_read = call.get_custom_resource_strict(
        group="observability.open-cluster-management.io",
        version="v1beta2",
        plural="multiclusterobservabilities",
        name="observability",
        namespace=None,
    )
    assert mock_primary_client.method_calls == [
        mco_read,
        call.delete_custom_resource_preconditioned(
            "observability.open-cluster-management.io",
            "v1beta2",
            "multiclusterobservabilities",
            "observability",
            uid="uid-1",
            namespace=None,
            timeout_seconds=DELETE_REQUEST_TIMEOUT,
        ),
        mco_read,
        call.get_namespace_strict(OBSERVABILITY_NAMESPACE),
        mco_read,
        call.get_namespace_strict(OBSERVABILITY_NAMESPACE),
        call.list_managed_clusters_strict(),
        call.list_custom_resources_strict(
            group="operator.open-cluster-management.io",
            version="v1",
            plural="multiclusterhubs",
            namespace=ACM_NAMESPACE,
        ),
    ]


@pytest.fixture
def decommission_tests_live(mock_primary_client, state_manager):
    return decommission_tests.decommission_with_obs.__wrapped__(mock_primary_client, state_manager)


def test_dry_run_reports_the_predicted_blocker_set(state_manager, caplog):
    """A dry-run observability teardown reports a blocked destination and writes nothing.

    ``Decommission.decommission`` does not evaluate this gate. ``usage.md`` says the
    decommission aggregator preview skips it. The phase machine does not.
    """
    decommission = decommission_tests._GateHarness(
        primary_client=Mock(),
        has_observability=True,
        run_record=decommission_tests.RunRecord(state_manager),
        secondary_client=Mock(),
        acknowledge_observability_not_migrated=False,
        dry_run=True,
    )
    decommission_tests._present_source(decommission)
    decommission_tests._absent_destination(decommission)
    before = _snapshot(state_manager)
    with caplog.at_level(logging.ERROR, logger="modules.decommission"):
        execution = decommission.teardown_observability()
    assert execution == decommission_tests.SubstepExecution(SubstepOutcome.FAILED, changed=False)
    assert "Destination observability gate blocked the teardown" in caplog.text
    assert decommission_tests.GATE_REASON_DESTINATION_ABSENT in caplog.text
    decommission.secondary.list_custom_resources_strict.assert_called()
    decommission.secondary.get_namespace_strict.assert_called()
    decommission.primary.delete_custom_resource_preconditioned.assert_not_called()
    assert decommission.run_record.all_teardown_records() == {}
    assert _snapshot(state_manager) == before
