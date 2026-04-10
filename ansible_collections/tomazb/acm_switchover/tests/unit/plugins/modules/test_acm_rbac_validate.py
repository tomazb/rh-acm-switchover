"""Tests for the acm_rbac_validate collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate import (
    expand_rbac_requirements,
    summarize_rbac_results,
)


def test_manage_mode_adds_application_patch_permission():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="manage",
        argocd_install_type="operator",
    )
    assert ("argoproj.io", "applications", "patch", None) in permissions


def test_decommission_adds_delete_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=True,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    assert ("cluster.open-cluster-management.io", "managedclusters", "delete", None) in permissions


def test_summary_reports_failure_when_permission_missing():
    summary = summarize_rbac_results(
        hub="primary",
        denied_permissions=[
            {
                "permission": "patch argoproj.io/applications",
                "scope": "cluster",
                "reason": "Forbidden",
            }
        ],
    )
    assert summary["passed"] is False
    assert any(item["id"] == "preflight-rbac-primary" for item in summary["results"])


def test_summary_reports_pass_when_all_permissions_allowed():
    summary = summarize_rbac_results(hub="secondary", denied_permissions=[])
    assert summary["passed"] is True
    assert summary["results"][0]["status"] == "pass"
