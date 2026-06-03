"""Tests for standalone checkpoint identity validation module."""

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_checkpoint_identity_validate import (
    validate_checkpoint_identity,
)


def _checkpoint_identity(**overrides):
    identity = {
        "primary_context": "hub-a",
        "secondary_context": "hub-b",
        "primary_cluster_uid": "uid-a",
        "secondary_cluster_uid": "uid-b",
        "method": "passive",
        "activation_method": "patch",
        "restore_only": False,
        "old_hub_action": "secondary",
        "collection_version": "",
    }
    identity.update(overrides)
    return identity


def _hubs():
    return {
        "primary": {"context": "hub-a"},
        "secondary": {"context": "hub-b"},
    }


def _hub_identities():
    return {
        "primary": {"context": "hub-a", "cluster_uid": "uid-a"},
        "secondary": {"context": "hub-b", "cluster_uid": "uid-b"},
    }


def test_validate_checkpoint_identity_accepts_matching_live_hubs():
    result = validate_checkpoint_identity(
        checkpoint={"operation_identity": _checkpoint_identity()},
        hubs=_hubs(),
        operation={"method": "passive"},
        hub_identities=_hub_identities(),
    )

    assert result["matched_mapping"] == "normal"
    assert result["operation_identity"]["primary_cluster_uid"] == "uid-a"


def test_validate_checkpoint_identity_rejects_uid_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        validate_checkpoint_identity(
            checkpoint={"operation_identity": _checkpoint_identity()},
            hubs=_hubs(),
            operation={"method": "passive"},
            hub_identities={
                "primary": {"context": "hub-a", "cluster_uid": "uid-retargeted"},
                "secondary": {"context": "hub-b", "cluster_uid": "uid-b"},
            },
        )


def test_validate_checkpoint_identity_rejects_missing_checkpoint_identity():
    with pytest.raises(ValueError, match="missing operation identity"):
        validate_checkpoint_identity(
            checkpoint={},
            hubs=_hubs(),
            operation={"method": "passive"},
            hub_identities=_hub_identities(),
        )


def test_validate_checkpoint_identity_rejects_missing_live_uid():
    with pytest.raises(ValueError, match="Unable to determine primary hub cluster identity"):
        validate_checkpoint_identity(
            checkpoint={"operation_identity": _checkpoint_identity()},
            hubs=_hubs(),
            operation={"method": "passive"},
            hub_identities={
                "primary": {"context": "hub-a", "cluster_uid": ""},
                "secondary": {"context": "hub-b", "cluster_uid": "uid-b"},
            },
        )


def test_validate_checkpoint_identity_accepts_swapped_two_hub_mapping():
    result = validate_checkpoint_identity(
        checkpoint={"operation_identity": _checkpoint_identity()},
        hubs={
            "primary": {"context": "hub-b"},
            "secondary": {"context": "hub-a"},
        },
        operation={"method": "passive"},
        hub_identities={
            "primary": {"context": "hub-b", "cluster_uid": "uid-b"},
            "secondary": {"context": "hub-a", "cluster_uid": "uid-a"},
        },
    )

    assert result["matched_mapping"] == "swapped"
