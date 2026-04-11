"""Tests for the acm_rbac_bootstrap collection module."""

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_bootstrap import select_rbac_assets


def test_select_rbac_assets_for_decommission():
    assets = select_rbac_assets(role="operator", include_decommission=True)
    assert "deploy/rbac/clusterrole.yaml" in assets
    assert any("decommission" in path for path in assets)


def test_base_assets_returned_without_decommission():
    """Base assets should be returned when decommission is False."""
    assets = select_rbac_assets(role="operator", include_decommission=False)
    assert len(assets) == 6
    assert "deploy/rbac/namespace.yaml" in assets
    assert "deploy/rbac/serviceaccount.yaml" in assets
    assert "deploy/rbac/role.yaml" in assets
    assert "deploy/rbac/rolebinding.yaml" in assets
    assert "deploy/rbac/clusterrole.yaml" in assets
    assert "deploy/rbac/clusterrolebinding.yaml" in assets
    # Decommission assets should not be present
    assert all("decommission" not in path for path in assets)


def test_validator_role_returns_base_assets():
    assets = select_rbac_assets(role="validator", include_decommission=False)
    assert len(assets) == 6
    assert "deploy/rbac/serviceaccount.yaml" in assets


def test_invalid_role_is_rejected():
    with pytest.raises(ValueError, match="Invalid RBAC role"):
        select_rbac_assets(role="admin", include_decommission=False)
