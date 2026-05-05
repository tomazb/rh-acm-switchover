"""Tests for checkpoint helper utilities."""

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    CheckpointIdentityMismatch,
    build_checkpoint_record,
    build_operation_identity,
    is_unsafe_legacy_checkpoint,
    reset_completed_phases_from,
    should_resume_phase,
    validate_operation_identity,
)


def test_build_operation_identity_captures_hub_and_operation_inputs():
    identity = build_operation_identity(
        hubs={
            "primary": {"context": "hub-a", "kubeconfig": "/kubeconfigs/primary"},
            "secondary": {"context": "hub-b", "kubeconfig": "/kubeconfigs/secondary"},
        },
        operation={
            "method": "passive",
            "activation_method": "restore",
            "restore_only": False,
            "old_hub_action": "keep",
        },
        collection_version="1.2.3",
    )

    assert identity == {
        "primary_context": "hub-a",
        "secondary_context": "hub-b",
        "primary_kubeconfig": "/kubeconfigs/primary",
        "secondary_kubeconfig": "/kubeconfigs/secondary",
        "method": "passive",
        "activation_method": "restore",
        "restore_only": False,
        "old_hub_action": "keep",
        "collection_version": "1.2.3",
    }


def test_build_checkpoint_record_sets_schema_phase_and_operation_identity():
    operation_identity = build_operation_identity(
        hubs={
            "primary": {"context": "hub-a", "kubeconfig": "/kubeconfigs/primary"},
            "secondary": {"context": "hub-b", "kubeconfig": "/kubeconfigs/secondary"},
        },
        operation={"method": "passive"},
    )

    record = build_checkpoint_record(
        "activation",
        {"method": "passive"},
        operation_identity=operation_identity,
    )

    assert record["schema_version"] == "2.0"
    assert record["phase"] == "activation"
    assert record["operation_identity"] == operation_identity


def test_build_checkpoint_record_contains_all_schema_fields():
    record = build_checkpoint_record("preflight", {})
    for key in (
        "schema_version",
        "phase",
        "completed_phases",
        "operational_data",
        "operation_identity",
        "errors",
        "report_refs",
        "created_at",
        "updated_at",
    ):
        assert key in record, f"Missing field: {key}"
    assert record["completed_phases"] == []
    assert record["errors"] == []
    assert record["report_refs"] == []


def test_should_resume_phase_skips_completed_phase():
    assert (
        should_resume_phase(
            checkpoint={"completed_phases": ["preflight", "primary_prep"]},
            phase="primary_prep",
        )
        is False
    )


def test_should_resume_phase_returns_true_for_new_phase():
    assert (
        should_resume_phase(
            checkpoint={"completed_phases": ["preflight"]},
            phase="activation",
        )
        is True
    )


def test_reset_completed_phases_from_prunes_requested_phase_and_downstream():
    assert reset_completed_phases_from(
        ["preflight", "primary_prep", "activation", "post_activation", "finalization"],
        "activation",
    ) == ["preflight", "primary_prep"]


def test_validate_operation_identity_raises_for_mismatch():
    expected_identity = build_operation_identity(
        hubs={
            "primary": {"context": "hub-a", "kubeconfig": "/kubeconfigs/primary"},
            "secondary": {"context": "hub-b", "kubeconfig": "/kubeconfigs/secondary"},
        },
        operation={"method": "passive", "restore_only": False},
    )
    checkpoint = {"operation_identity": {**expected_identity, "restore_only": True}}

    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity(checkpoint, expected_identity)


def test_validate_operation_identity_raises_when_identity_is_missing_by_default():
    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity({}, {"method": "passive"})


def test_validate_operation_identity_allows_missing_identity_when_requested():
    assert (
        validate_operation_identity({}, {"method": "passive"}, allow_missing=True)
        is False
    )


def test_is_unsafe_legacy_checkpoint_requires_reset_for_completed_legacy_state():
    assert (
        is_unsafe_legacy_checkpoint(
            {"schema_version": "1.0", "completed_phases": ["preflight"]}
        )
        is True
    )
    assert (
        is_unsafe_legacy_checkpoint({"schema_version": "1.0", "completed_phases": []})
        is False
    )
    assert (
        is_unsafe_legacy_checkpoint(
            {"schema_version": "2.0", "completed_phases": ["preflight"]}
        )
        is False
    )
