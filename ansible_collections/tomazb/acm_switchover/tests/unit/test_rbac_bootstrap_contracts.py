import pathlib

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[5]
COLLECTION_ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles" / "rbac_bootstrap"
PREFLIGHT_TASKS = COLLECTION_ROOT / "roles" / "preflight" / "tasks"


def _load_tasks(name):
    return yaml.safe_load((ROLE_DIR / "tasks" / name).read_text(encoding="utf-8"))


def _operator_mco_verbs_from_clusterrole(path):
    docs = [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]
    operator = next(doc for doc in docs if doc.get("metadata", {}).get("name") == "acm-switchover-operator")
    rule = next(
        rule
        for rule in operator["rules"]
        if rule.get("apiGroups") == ["observability.open-cluster-management.io"]
        and rule.get("resources") == ["multiclusterobservabilities"]
    )
    return set(rule["verbs"])


def test_bootstrap_permission_validation_covers_shipped_argocd_rules():
    tasks = _load_tasks("validate_permissions.yml")
    validation_tasks = [task for task in tasks if "tomazb.acm_switchover.acm_rbac_validate" in task]

    assert validation_tasks, "validate_permissions.yml must expand RBAC requirements"
    for task in validation_tasks:
        module_args = task["tomazb.acm_switchover.acm_rbac_validate"]
        assert "argocd_mode" in module_args
        assert "argocd_install_type" in module_args

    argocd_mode_expr = validation_tasks[0]["tomazb.acm_switchover.acm_rbac_validate"]["argocd_mode"]
    assert "manage" in argocd_mode_expr
    assert "check" in argocd_mode_expr


def test_preflight_primary_rbac_expansion_requests_old_hub_finalization_delete():
    tasks = yaml.safe_load((PREFLIGHT_TASKS / "validate_rbac.yml").read_text(encoding="utf-8"))
    primary_expansion = next(
        task for task in tasks if task.get("name") == "Expand required RBAC permissions for primary hub"
    )
    module_args = primary_expansion["tomazb.acm_switchover.acm_rbac_validate"]

    assert "include_old_hub_finalization" in module_args
    expression = module_args["include_old_hub_finalization"]
    assert "old_hub_action" in expression
    assert "secondary" in expression
    assert "open-cluster-management-observability" in expression


def test_operator_mco_delete_rule_matches_root_helm_and_collection_bundle():
    root_verbs = _operator_mco_verbs_from_clusterrole(REPO_ROOT / "deploy" / "rbac" / "clusterrole.yaml")
    bundled_verbs = _operator_mco_verbs_from_clusterrole(ROLE_DIR / "files" / "deploy" / "rbac" / "clusterrole.yaml")
    helm_clusterrole = (
        REPO_ROOT / "deploy" / "helm" / "acm-switchover-rbac" / "templates" / "clusterrole.yaml"
    ).read_text(encoding="utf-8")
    helm_snippet = (
        'apiGroups: ["observability.open-cluster-management.io"]\n'
        '    resources: ["multiclusterobservabilities"]\n'
        '    verbs: ["get", "list", "delete"]'
    )

    assert root_verbs == {"get", "list", "delete"}
    assert bundled_verbs == root_verbs
    assert helm_snippet in helm_clusterrole
