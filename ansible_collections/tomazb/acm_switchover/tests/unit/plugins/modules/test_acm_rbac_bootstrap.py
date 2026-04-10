from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_bootstrap import select_rbac_assets


def test_select_rbac_assets_for_operator_and_decommission():
    assets = select_rbac_assets(role="operator", include_decommission=True)
    assert "deploy/rbac/clusterrole.yaml" in assets
    assert any("decommission" in path for path in assets)
