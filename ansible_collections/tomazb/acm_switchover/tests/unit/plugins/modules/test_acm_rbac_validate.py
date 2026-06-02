"""Tests for the acm_rbac_validate collection module."""

import ast
import json
from pathlib import Path

import pytest
import yaml
from ansible.module_utils import basic
from ansible.module_utils.basic import AnsibleModule as RealAnsibleModule
from jinja2 import Environment

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    MANAGED_CLUSTER_AGENT_NAMESPACE,
)
import ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate as acm_rbac_validate_module
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_validate import (
    expand_rbac_requirements,
    main,
    summarize_rbac_results,
)

RUN_SSAR_TASK = Path(__file__).resolve().parents[4] / "roles" / "preflight" / "tasks" / "run_ssar.yml"


class ModuleExit(Exception):
    def __init__(self, results):
        super().__init__("module exited")
        self.results = results


class ModuleFail(Exception):
    def __init__(self, results):
        super().__init__("module failed")
        self.results = results


def _render_denied_permissions_from_run_ssar(results):
    tasks = yaml.safe_load(RUN_SSAR_TASK.read_text(encoding="utf-8"))
    collect_task = next(task for task in tasks if task.get("name") == "Collect denied permissions")
    template = collect_task["ansible.builtin.set_fact"]["_rbac_denied_permissions"]
    rendered = Environment().from_string(template).render(_ssar_results={"results": results})
    return ast.literal_eval(rendered)


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
    assert (
        "apiextensions.k8s.io",
        "customresourcedefinitions",
        "get",
        None,
    ) in permissions
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
    assert (
        "apiextensions.k8s.io",
        "customresourcedefinitions",
        "get",
        None,
    ) not in permissions


def test_decommission_adds_delete_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=True,
        include_old_hub_finalization=False,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    assert (
        "cluster.open-cluster-management.io",
        "managedclusters",
        "delete",
        None,
    ) in permissions


def test_decommission_skips_mco_delete_permission_when_observability_absent():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=True,
        include_old_hub_finalization=False,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
    )

    assert (
        "observability.open-cluster-management.io",
        "multiclusterobservabilities",
        "delete",
        None,
    ) not in permissions


def test_old_hub_finalization_adds_mco_delete_permission():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        include_old_hub_finalization=True,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )

    assert (
        "observability.open-cluster-management.io",
        "multiclusterobservabilities",
        "delete",
        None,
    ) in permissions


def test_decommission_and_old_hub_finalization_do_not_duplicate_mco_delete_permission():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=True,
        include_old_hub_finalization=True,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )

    assert (
        permissions.count(
            (
                "observability.open-cluster-management.io",
                "multiclusterobservabilities",
                "delete",
                None,
            )
        )
        == 1
    )


def test_verified_observability_absence_skips_old_hub_mco_delete_permission():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        include_old_hub_finalization=True,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
    )

    assert (
        "observability.open-cluster-management.io",
        "multiclusterobservabilities",
        "delete",
        None,
    ) not in permissions


@pytest.mark.parametrize("role", ["operator", "validator"])
def test_hub_validation_requires_namespace_list_for_preflight_discovery(role):
    permissions = expand_rbac_requirements(
        role=role,
        include_decommission=False,
        include_old_hub_finalization=False,
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
        include_old_hub_finalization=False,
        skip_observability=True,
        argocd_mode="none",
        argocd_install_type="unknown",
        decommission_only=True,
    )

    assert (
        "cluster.open-cluster-management.io",
        "managedclusters",
        "delete",
        None,
    ) in permissions
    assert (
        "cluster.open-cluster-management.io",
        "managedclusters",
        "patch",
        None,
    ) not in permissions
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
        None,
    ) in permissions
    assert (
        "operator.open-cluster-management.io",
        "multiclusterhubs",
        "delete",
        "open-cluster-management",
    ) not in permissions
    assert (
        "hive.openshift.io",
        "clusterdeployments",
        "list",
        None,
    ) in permissions
    assert (
        "hive.openshift.io",
        "clusterdeployments",
        "get",
        None,
    ) not in permissions


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


def test_summary_counts_each_denied_permission_as_critical_failure():
    summary = summarize_rbac_results(
        hub="primary",
        denied_permissions=[
            {
                "permission": "patch argoproj.io/applications",
                "scope": "cluster",
                "reason": "Forbidden",
            },
            {
                "permission": "delete operator.open-cluster-management.io/multiclusterhubs",
                "scope": "cluster",
            },
        ],
    )

    assert summary["passed"] is False
    assert summary["critical_failures"] == 2


def test_summary_accepts_workflow_specific_metadata():
    summary = summarize_rbac_results(
        hub="primary",
        denied_permissions=[
            {
                "api_group": "operator.open-cluster-management.io",
                "resource": "multiclusterhubs",
                "verb": "delete",
                "namespace": None,
                "reason": "Forbidden",
            }
        ],
        result_id="rbac-bootstrap-primary",
        failure_message="RBAC bootstrap cannot verify required permissions on primary hub",
        success_message="RBAC bootstrap permissions validated on primary hub",
        recommended_action="Run rbac_bootstrap with an account that can grant the documented role",
    )

    result = summary["results"][0]
    assert result["id"] == "rbac-bootstrap-primary"
    assert result["message"] == "RBAC bootstrap cannot verify required permissions on primary hub"
    assert result["recommended_action"] == "Run rbac_bootstrap with an account that can grant the documented role"


def test_summary_reports_pass_when_all_permissions_allowed():
    summary = summarize_rbac_results(hub="secondary", denied_permissions=[])
    assert summary["passed"] is True
    assert summary["critical_failures"] == 0
    assert summary["results"][0]["status"] == "pass"


def test_main_fails_closed_when_denied_permissions_present(monkeypatch):
    captured = {}
    denied_permissions = [
        {
            "api_group": "argoproj.io",
            "resource": "applications",
            "verb": "patch",
            "namespace": None,
            "reason": "Forbidden",
        }
    ]

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "hub": "primary",
                "role": "operator",
                "include_decommission": False,
                "include_old_hub_finalization": False,
                "decommission_only": False,
                "scope": "hub",
                "skip_observability": False,
                "argocd_mode": "none",
                "argocd_install_type": "unknown",
                "denied_permissions": denied_permissions,
                "result_id": None,
                "failure_message": None,
                "success_message": None,
                "recommended_action": None,
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(acm_rbac_validate_module, "AnsibleModule", FakeModule)

    main()

    assert captured["exit"]["changed"] is False
    assert captured["exit"]["passed"] is False
    assert captured["exit"]["critical_failures"] == 1
    assert captured["exit"]["results"][0]["status"] == "fail"
    assert captured["exit"]["results"][0]["details"]["denied_permissions"] == denied_permissions


def test_main_check_mode_returns_validation_without_change(monkeypatch):
    captured = {}
    expected_permissions = [("argoproj.io", "applications", "get", None)]
    expanded = {}

    def fake_expand_rbac_requirements(**kwargs):
        expanded["kwargs"] = kwargs
        return expected_permissions

    class FakeModule:
        def __init__(self, *args, **kwargs):
            assert kwargs["supports_check_mode"] is True
            self.params = {
                "hub": "primary",
                "role": "operator",
                "include_decommission": False,
                "include_old_hub_finalization": False,
                "decommission_only": False,
                "scope": "hub",
                "skip_observability": False,
                "argocd_mode": "none",
                "argocd_install_type": "unknown",
                "denied_permissions": [],
                "result_id": None,
                "failure_message": None,
                "success_message": None,
                "recommended_action": None,
            }
            self.check_mode = True

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(acm_rbac_validate_module, "AnsibleModule", FakeModule)
    monkeypatch.setattr(
        acm_rbac_validate_module,
        "expand_rbac_requirements",
        fake_expand_rbac_requirements,
    )

    main()

    assert expanded["kwargs"] == {
        "role": "operator",
        "include_decommission": False,
        "include_old_hub_finalization": False,
        "skip_observability": False,
        "argocd_mode": "none",
        "argocd_install_type": "unknown",
        "decommission_only": False,
        "scope": "hub",
    }
    assert captured["exit"]["changed"] is False
    assert captured["exit"]["permissions"] == expected_permissions
    assert captured["exit"]["passed"] is True


def test_main_requires_hub_argument_at_module_boundary(monkeypatch):
    class CapturingAnsibleModule(RealAnsibleModule):
        def exit_json(self, **kwargs):
            raise ModuleExit(kwargs)

        def fail_json(self, **kwargs):
            raise ModuleFail(kwargs)

    monkeypatch.setattr(acm_rbac_validate_module, "AnsibleModule", CapturingAnsibleModule)
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": {}}).encode("utf-8"))
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")

    with pytest.raises(ModuleFail) as excinfo:
        main()

    assert excinfo.value.results["msg"] == "missing required arguments: hub"


def test_statefulset_scale_permission_uses_ssar_subresource_split_contract(monkeypatch, capsys):
    # Python module preserves the slash-form; the resource/subresource split occurs in roles/preflight/tasks/run_ssar.yml
    monkeypatch.setattr(
        basic,
        "_ANSIBLE_ARGS",
        json.dumps(
            {
                "ANSIBLE_MODULE_ARGS": {
                    "hub": "primary",
                    "role": "operator",
                    "denied_permissions": [
                        {
                            "api_group": "apps",
                            "resource": "statefulsets/scale",
                            "verb": "patch",
                            "namespace": "open-cluster-management-observability",
                            "reason": "Forbidden",
                        }
                    ],
                }
            }
        ).encode("utf-8"),
    )
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    monkeypatch.setattr(acm_rbac_validate_module, "AnsibleModule", RealAnsibleModule)

    with pytest.raises(SystemExit) as excinfo:
        main()

    result = json.loads(capsys.readouterr().out)

    assert excinfo.value.code == 0
    assert result.get("failed") is not True
    assert result["results"][0]["details"]["denied_permissions"][0]["resource"] == "statefulsets/scale"
    assert [
        "apps",
        "statefulsets/scale",
        "patch",
        "open-cluster-management-observability",
    ] in result["permissions"]


def test_run_ssar_api_failures_are_recorded_and_fail_closed():
    # acm_rbac_validate.py does not call the Kubernetes SSAR API directly.
    # The actual fail-closed translation from a failed SSAR call into denied_permissions
    # happens in roles/preflight/tasks/run_ssar.yml, so render that template here.
    denied_permissions = _render_denied_permissions_from_run_ssar(
        [
            {
                "failed": True,
                "msg": "authorization API unavailable",
                "item": [
                    "apps",
                    "statefulsets/scale",
                    "patch",
                    "open-cluster-management-observability",
                ],
            }
        ]
    )

    expected_denied_permissions = [
        {
            "api_group": "apps",
            "resource": "statefulsets/scale",
            "verb": "patch",
            "namespace": "open-cluster-management-observability",
            "reason": "authorization API unavailable",
        }
    ]

    assert denied_permissions == expected_denied_permissions

    summary = summarize_rbac_results(hub="primary", denied_permissions=denied_permissions)

    assert summary["passed"] is False
    assert summary["critical_failures"] == 1
    assert summary["results"][0]["details"]["denied_permissions"] == expected_denied_permissions


def test_main_maps_invalid_role_combination_to_fail_json(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "hub": "primary",
                "role": "validator",
                "include_decommission": True,
                "include_old_hub_finalization": False,
                "decommission_only": False,
                "scope": "hub",
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
        include_old_hub_finalization=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    # Validator should NOT have patch on managedclusters
    assert (
        "cluster.open-cluster-management.io",
        "managedclusters",
        "patch",
        None,
    ) not in permissions
    # Validator should have get/list on managedclusters
    assert (
        "cluster.open-cluster-management.io",
        "managedclusters",
        "get",
        None,
    ) in permissions
    assert (
        "cluster.open-cluster-management.io",
        "managedclusters",
        "list",
        None,
    ) in permissions


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


def test_managed_cluster_validation_scope_includes_agent_namespace_permissions():
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
        scope="managed_cluster",
    )

    assert (
        "",
        "secrets",
        "create",
        "open-cluster-management-agent",
    ) in permissions
    assert (
        "apps",
        "deployments",
        "patch",
        "open-cluster-management-agent",
    ) in permissions
    assert (
        "cluster.open-cluster-management.io",
        "backupschedules",
        "get",
        "open-cluster-management-backup",
    ) not in permissions


def test_validator_managed_cluster_scope_is_read_only():
    permissions = expand_rbac_requirements(
        role="validator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
        scope="managed_cluster",
    )

    assert (
        "",
        "secrets",
        "get",
        "open-cluster-management-agent",
    ) in permissions
    assert (
        "",
        "secrets",
        "create",
        "open-cluster-management-agent",
    ) not in permissions
    assert (
        "apps",
        "deployments",
        "patch",
        "open-cluster-management-agent",
    ) not in permissions


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


def test_main_maps_validator_argocd_manage_to_failed_module_output(monkeypatch, capsys):
    monkeypatch.setattr(
        basic,
        "_ANSIBLE_ARGS",
        json.dumps(
            {
                "ANSIBLE_MODULE_ARGS": {
                    "hub": "primary",
                    "role": "validator",
                    "argocd_mode": "manage",
                    "argocd_install_type": "operator",
                }
            }
        ).encode("utf-8"),
    )
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    monkeypatch.setattr(acm_rbac_validate_module, "AnsibleModule", RealAnsibleModule)

    with pytest.raises(SystemExit):
        main()

    output = capsys.readouterr().out
    result = json.loads(output)

    assert result["failed"] is True
    assert result["msg"] == "validator role cannot use argocd_mode=manage"


def test_operator_role_has_patch_on_managedclusters():
    """Operator role should have patch on managedclusters for activation."""
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
    )
    assert (
        "cluster.open-cluster-management.io",
        "managedclusters",
        "patch",
        None,
    ) in permissions


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


def test_operator_managed_cluster_secret_permissions_patch_without_delete():
    """Operator remediation should patch or create bootstrap secrets, not delete them."""
    permissions = expand_rbac_requirements(
        role="operator",
        include_decommission=False,
        skip_observability=False,
        argocd_mode="none",
        argocd_install_type="unknown",
        scope="managed_cluster",
    )
    secret_perms = [p for p in permissions if p[3] == MANAGED_CLUSTER_AGENT_NAMESPACE and p[1] == "secrets"]
    verbs = {p[2] for p in secret_perms}

    assert "get" in verbs
    assert "create" in verbs
    assert "patch" in verbs
    assert "delete" not in verbs
