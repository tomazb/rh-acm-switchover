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
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_checkpoint import (
    main,
)


def test_build_operation_identity_captures_hub_and_operation_inputs():
    identity = build_operation_identity(
        hubs={
            "primary": {
                "context": "hub-a",
                "kubeconfig": "/kubeconfigs/primary",
                "cluster_uid": "uid-primary",
            },
            "secondary": {
                "context": "hub-b",
                "kubeconfig": "/kubeconfigs/secondary",
                "cluster_uid": "uid-secondary",
            },
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
        "primary_cluster_uid": "uid-primary",
        "secondary_cluster_uid": "uid-secondary",
        "method": "passive",
        "activation_method": "restore",
        "restore_only": False,
        "old_hub_action": "keep",
        "collection_version": "1.2.3",
    }


def test_build_operation_identity_canonicalizes_sparse_inputs_to_defaults():
    expected_identity = {
        "primary_context": "",
        "secondary_context": "",
        "primary_cluster_uid": "",
        "secondary_cluster_uid": "",
        "method": "passive",
        "activation_method": "patch",
        "restore_only": False,
        "old_hub_action": "secondary",
        "collection_version": "",
    }

    assert build_operation_identity(hubs={}, operation={}) == expected_identity
    assert (
        build_operation_identity(
            hubs={
                "primary": {"context": "", "kubeconfig": "", "cluster_uid": ""},
                "secondary": {"context": "", "kubeconfig": "", "cluster_uid": ""},
            },
            operation={
                "method": "passive",
                "activation_method": "patch",
                "restore_only": False,
                "old_hub_action": "secondary",
            },
            collection_version="",
        )
        == expected_identity
    )


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


def test_validate_operation_identity_rejects_same_context_with_different_cluster_uid():
    expected_identity = build_operation_identity(
        hubs={
            "primary": {
                "context": "hub-a",
                "kubeconfig": "/kubeconfigs/primary",
                "cluster_uid": "uid-a",
            },
            "secondary": {
                "context": "hub-b",
                "kubeconfig": "/kubeconfigs/secondary",
                "cluster_uid": "uid-b",
            },
        },
        operation={"method": "passive"},
    )
    checkpoint = {
        "operation_identity": {
            **expected_identity,
            "primary_cluster_uid": "uid-a-retargeted",
        }
    }

    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity(checkpoint, expected_identity)


def test_validate_operation_identity_accepts_legacy_kubeconfig_fields():
    expected_identity = build_operation_identity(
        hubs={
            "primary": {
                "context": "hub-a",
                "kubeconfig": "/new/primary",
                "cluster_uid": "uid-a",
            },
            "secondary": {
                "context": "hub-b",
                "kubeconfig": "/new/secondary",
                "cluster_uid": "uid-b",
            },
        },
        operation={"method": "passive"},
    )
    checkpoint = {
        "operation_identity": {
            **expected_identity,
            "primary_kubeconfig": "/legacy/primary",
            "secondary_kubeconfig": "/legacy/secondary",
        }
    }

    assert validate_operation_identity(checkpoint, expected_identity) is True


def test_validate_operation_identity_raises_when_identity_is_missing_by_default():
    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity({}, {"method": "passive"})


def test_validate_operation_identity_allows_missing_identity_when_requested():
    assert validate_operation_identity({}, {"method": "passive"}, allow_missing=True) is False


def test_build_operation_identity_restore_only_defaults_method_full_and_old_hub_none():
    identity = build_operation_identity(
        hubs={},
        operation={"restore_only": True},
    )
    assert identity["method"] == "full"
    assert identity["old_hub_action"] == "none"
    assert identity["restore_only"] is True


def test_build_operation_identity_restore_only_sparse_equals_fully_populated():
    sparse = build_operation_identity(hubs={}, operation={"restore_only": True})
    fully_populated = build_operation_identity(
        hubs={
            "primary": {"context": "", "kubeconfig": ""},
            "secondary": {"context": "", "kubeconfig": ""},
        },
        operation={
            "method": "full",
            "activation_method": "patch",
            "restore_only": True,
            "old_hub_action": "none",
        },
        collection_version="",
    )
    assert sparse == fully_populated


def test_is_unsafe_legacy_checkpoint_requires_reset_for_completed_legacy_state():
    assert is_unsafe_legacy_checkpoint({"schema_version": "1.0", "completed_phases": ["preflight"]}) is True
    assert is_unsafe_legacy_checkpoint({"schema_version": "1.0", "completed_phases": []}) is False
    assert is_unsafe_legacy_checkpoint({"schema_version": "2.0", "completed_phases": ["preflight"]}) is False


def test_acm_checkpoint_check_mode_returns_record_without_change(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            assert kwargs["supports_check_mode"] is True
            self.params = {
                "phase": "activation",
                "operational_data": {"method": "passive"},
            }
            self.check_mode = True

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_checkpoint.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["exit"]["changed"] is False
    assert captured["exit"]["checkpoint"]["phase"] == "activation"
    assert captured["exit"]["checkpoint"]["operational_data"] == {"method": "passive"}
