import pathlib

import yaml

ROLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles" / "rbac_bootstrap"


def _load_tasks(name):
    return yaml.safe_load((ROLE_DIR / "tasks" / name).read_text(encoding="utf-8"))


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
