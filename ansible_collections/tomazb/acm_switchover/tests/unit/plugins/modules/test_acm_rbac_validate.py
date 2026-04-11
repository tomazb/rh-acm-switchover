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


def test_validator_role_has_readonly_managedcluster_permission():
    """Validator role should only have get/list, not patch on managedclusters."""
    permissions = expand_rbac_requirements(
        role="validator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    # Validator should NOT have patch on managedclusters
    assert ("cluster.open-cluster-management.io", "managedclusters", "patch", None) not in permissions
    # Validator should have get/list on managedclusters
    assert ("cluster.open-cluster-management.io", "managedclusters", "get", None) in permissions
    assert ("cluster.open-cluster-management.io", "managedclusters", "list", None) in permissions


def test_validator_role_no_write_on_backupschedules():
    """Validator role should not have create/patch/delete on backupschedules."""
    permissions = expand_rbac_requirements(
        role="validator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    # Filter to backup namespace permissions
    backup_perms = [p for p in permissions if p[3] == "open-cluster-management-backup" and p[1] == "backupschedules"]
    # Should only have get/list, no write operations
    verbs = {p[2] for p in backup_perms}
    assert verbs == {"get", "list"}
    assert "create" not in verbs
    assert "patch" not in verbs
    assert "delete" not in verbs


def test_operator_role_has_patch_on_managedclusters():
    """Operator role should have patch on managedclusters for activation."""
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    assert ("cluster.open-cluster-management.io", "managedclusters", "patch", None) in permissions


def test_operator_role_has_write_on_backupschedules():
    """Operator role should have write permissions on backupschedules."""
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    # Filter to backup namespace permissions
    backup_perms = [p for p in permissions if p[3] == "open-cluster-management-backup" and p[1] == "backupschedules"]
    verbs = {p[2] for p in backup_perms}
    assert "create" in verbs
    assert "patch" in verbs
    assert "delete" in verbs
