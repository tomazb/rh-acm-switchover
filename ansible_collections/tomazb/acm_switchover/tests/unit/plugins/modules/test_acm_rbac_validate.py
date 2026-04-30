"""Tests for the acm_rbac_validate collection module."""

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate import (
    expand_rbac_requirements,
    main,
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


def test_check_mode_adds_argocd_read_permissions_only():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="check",
        argocd_install_type="operator",
    )

    assert ("argoproj.io", "applications", "get", None) in permissions
    assert ("argoproj.io", "applications", "list", None) in permissions
    assert ("argoproj.io", "argocds", "get", None) in permissions
    assert ("argoproj.io", "argocds", "list", None) in permissions
    assert ("apiextensions.k8s.io", "customresourcedefinitions", "get", None) in permissions
    assert ("argoproj.io", "applications", "patch", None) not in permissions


def test_argocd_none_install_type_skips_all_argocd_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="check",
        argocd_install_type="none",
    )

    assert ("argoproj.io", "applications", "get", None) not in permissions
    assert ("argoproj.io", "applications", "list", None) not in permissions
    assert ("argoproj.io", "argocds", "get", None) not in permissions
    assert ("apiextensions.k8s.io", "customresourcedefinitions", "get", None) not in permissions


def test_decommission_adds_delete_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=True,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    assert ("cluster.open-cluster-management.io", "managedclusters", "delete", None) in permissions


@pytest.mark.parametrize("role", ["operator", "validator"])
def test_hub_validation_requires_namespace_list_for_preflight_discovery(role):
    permissions = expand_rbac_requirements(
        role=role,
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )

    assert ("", "namespaces", "get", None) in permissions
    assert ("", "namespaces", "list", None) in permissions


def test_decommission_only_excludes_switchover_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=True,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
        decommission_only=True,
    )

    assert ("cluster.open-cluster-management.io", "managedclusters", "delete", None) in permissions
    assert ("cluster.open-cluster-management.io", "managedclusters", "patch", None) not in permissions
    assert (
        "cluster.open-cluster-management.io",
        "backupschedules",
        "get",
        "open-cluster-management-backup",
    ) not in permissions
    assert (
        "operator.open-cluster-management.io",
        "multiclusterhubs",
        "delete",
        "open-cluster-management",
    ) in permissions


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
    assert summary["critical_failures"] == 1


def test_summary_reports_pass_when_all_permissions_allowed():
    summary = summarize_rbac_results(hub="secondary", denied_permissions=[])
    assert summary["passed"] is True
    assert summary["critical_failures"] == 0
    assert summary["results"][0]["status"] == "pass"


def test_main_maps_invalid_role_combination_to_fail_json(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "hub": "primary",
                "role": "validator",
                "include_decommission": True,
                "decommission_only": False,
                "skip_observability": False,
                "argocd_mode": "none",
                "argocd_install_type": "unknown",
                "denied_permissions": [],
            }

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["fail"] == {"msg": "include_decommission is only valid for the operator role"}


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


def test_hub_validation_surface_excludes_managed_cluster_namespace_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )

    managed_cluster_perms = [p for p in permissions if p[3] == "open-cluster-management-agent"]
    assert managed_cluster_perms == []


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


def test_validator_role_rejects_decommission_permissions():
    with pytest.raises(ValueError, match="include_decommission"):
        expand_rbac_requirements(
            role="validator",
            include_decommission=True,
            skip_observability=False,
            argocd_mode="none",
            argocd_install_type="unknown",
        )


def test_validator_role_rejects_argocd_manage_permissions():
    with pytest.raises(ValueError, match="validator.*manage"):
        expand_rbac_requirements(
            role="validator",
            include_decommission=False,
            skip_observability=False,
            argocd_mode="manage",
            argocd_install_type="operator",
        )


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
