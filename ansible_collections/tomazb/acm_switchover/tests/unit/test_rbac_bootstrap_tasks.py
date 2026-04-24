"""Static tests for RBAC bootstrap task wiring."""

from pathlib import Path

import yaml

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
RBAC_BOOTSTRAP_TASKS = ROLES_DIR / "rbac_bootstrap" / "tasks"


def _load_tasks(name: str) -> list[dict]:
    return yaml.safe_load((RBAC_BOOTSTRAP_TASKS / name).read_text())


def test_generate_kubeconfigs_invokes_packaged_script_for_selected_service_account():
    """Generated kubeconfigs must target the bootstrapped service account and persist output."""
    text = (RBAC_BOOTSTRAP_TASKS / "generate_kubeconfigs.yml").read_text()
    tasks = _load_tasks("generate_kubeconfigs.yml")

    assert "role_path" in text
    assert "files/scripts/generate-sa-kubeconfig.sh" in text
    assert "scripts/generate-sa-kubeconfig.sh" not in text.replace("files/scripts/generate-sa-kubeconfig.sh", "")
    assert "acm-switchover" in text
    assert "acm-switchover-operator" in text
    assert "acm-switchover-validator" in text
    assert "--token-duration" in text
    assert "token_duration" in text
    assert "output_dir" in text

    copy_tasks = [task for task in tasks if task.get("ansible.builtin.copy")]
    assert copy_tasks, "generated kubeconfig stdout must be written to a durable file"
    assert any(task["ansible.builtin.copy"].get("mode") == "0600" for task in copy_tasks)
    assert any(task.get("no_log") is True for task in tasks), "credential output must be hidden"


def test_validate_permissions_impersonates_bootstrapped_service_account():
    """Bootstrap validation must check the created service account, not the admin credential."""
    text = (RBAC_BOOTSTRAP_TASKS / "validate_permissions.yml").read_text()

    assert "SubjectAccessReview" in text
    assert "SelfSubjectAccessReview" not in text
    assert "system:serviceaccount:acm-switchover:" in text
    assert "include_role" not in text
