"""Static tests for RBAC bootstrap task wiring."""

from pathlib import Path

import yaml
from jinja2 import Environment

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
RBAC_BOOTSTRAP_TASKS = ROLES_DIR / "rbac_bootstrap" / "tasks"


def _load_tasks(name: str) -> list[dict]:
    return yaml.safe_load((RBAC_BOOTSTRAP_TASKS / name).read_text())


def test_generate_kubeconfigs_invokes_packaged_script_for_selected_service_account():
    """Generated kubeconfigs must target the bootstrapped service account and persist output."""
    text = (RBAC_BOOTSTRAP_TASKS / "generate_kubeconfigs.yml").read_text()
    defaults_text = (ROLES_DIR / "rbac_bootstrap" / "defaults" / "main.yml").read_text()
    packaged_script_text = (
        ROLES_DIR / "rbac_bootstrap" / "files" / "scripts" / "generate-sa-kubeconfig.sh"
    ).read_text()
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
    assert "token_duration: 24h" in defaults_text
    assert "default('24h')" in text
    assert 'DURATION="24h"' in packaged_script_text
    assert "default: 24h" in packaged_script_text

    copy_tasks = [task for task in tasks if task.get("ansible.builtin.copy")]
    assert copy_tasks, "generated kubeconfig stdout must be written to a durable file"
    assert any(task["ansible.builtin.copy"].get("mode") == "0600" for task in copy_tasks)
    assert any(task.get("no_log") is True for task in tasks), "credential output must be hidden"


def test_generate_kubeconfigs_validates_output_path_before_writing_credentials():
    """Generated service-account kubeconfigs must use artifact-safe path validation before writes."""
    tasks = _load_tasks("generate_kubeconfigs.yml")
    validate_index = next(
        idx
        for idx, task in enumerate(tasks)
        if task.get("tomazb.acm_switchover.acm_safe_path_validate", {}).get("path")
        == "{{ _rbac_bootstrap_kubeconfig_path }}"
    )
    file_index = next(idx for idx, task in enumerate(tasks) if task.get("ansible.builtin.file"))
    copy_index = next(idx for idx, task in enumerate(tasks) if task.get("ansible.builtin.copy"))
    validate_task = tasks[validate_index]["tomazb.acm_switchover.acm_safe_path_validate"]

    assert validate_index < file_index
    assert validate_index < copy_index
    assert validate_task.get("path_type") == "artifact"


def test_validate_permissions_impersonates_bootstrapped_service_account():
    """Bootstrap validation must check the created service account, not the admin credential."""
    text = (RBAC_BOOTSTRAP_TASKS / "validate_permissions.yml").read_text()

    assert "SubjectAccessReview" in text
    assert "SelfSubjectAccessReview" not in text
    assert "system:serviceaccount:acm-switchover:" in text
    assert "include_role" not in text


def test_manifest_filter_uses_positive_role_or_common_labels_only():
    """RBAC bootstrap must not apply unlabeled role-specific resources by default."""
    text = (RBAC_BOOTSTRAP_TASKS / "apply_manifest_file.yml").read_text()

    assert "app.kubernetes.io/role" in text
    assert "app.kubernetes.io/part-of" in text
    assert "common" in text
    assert "item.metadata is not defined" not in text
    assert "item.metadata.labels is not defined" not in text
    assert "| default('')) in ['', _rbac_plan.role]" not in text


def _manifest_filter_applies(item: dict, role: str) -> bool:
    tasks = _load_tasks("apply_manifest_file.yml")
    apply_task = next(task for task in tasks if task.get("name") == "Apply filtered RBAC resources from manifest file")
    expression = Environment().compile_expression(apply_task["when"])

    return bool(expression(item=item, _rbac_plan={"role": role}))


def test_manifest_filter_positive_matching_behavior():
    """The runtime manifest filter must apply only selected role or explicitly common resources."""
    assert _manifest_filter_applies(
        {
            "metadata": {
                "labels": {
                    "app.kubernetes.io/role": "operator",
                }
            }
        },
        "operator",
    )
    assert not _manifest_filter_applies(
        {
            "metadata": {
                "labels": {
                    "app.kubernetes.io/role": "validator",
                }
            }
        },
        "operator",
    )
    assert _manifest_filter_applies(
        {
            "metadata": {
                "labels": {
                    "app.kubernetes.io/role": "common",
                    "app.kubernetes.io/part-of": "acm-switchover-rbac",
                }
            }
        },
        "operator",
    )
    assert not _manifest_filter_applies(
        {
            "metadata": {
                "labels": {
                    "app.kubernetes.io/role": "common",
                }
            }
        },
        "operator",
    )
    assert not _manifest_filter_applies({"metadata": {"labels": {}}}, "operator")
    assert not _manifest_filter_applies({"metadata": None}, "operator")
    assert not _manifest_filter_applies({}, "operator")
