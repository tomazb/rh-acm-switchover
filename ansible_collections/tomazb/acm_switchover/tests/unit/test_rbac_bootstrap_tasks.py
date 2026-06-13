"""Static tests for RBAC bootstrap task wiring."""

import re
from pathlib import Path

import yaml
from jinja2 import Environment

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_bootstrap import expand_rbac_role_targets

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
    defaults = yaml.safe_load(defaults_text)
    packaged_duration = re.search(r"^\s*DURATION\s*=\s*['\"]?([^'\"\n]+)", packaged_script_text, re.MULTILINE)

    assert "role_path" in text
    assert "files/scripts/generate-sa-kubeconfig.sh" in text
    assert "scripts/generate-sa-kubeconfig.sh" not in text.replace("files/scripts/generate-sa-kubeconfig.sh", "")
    assert "acm-switchover" in text
    assert "acm-switchover-operator" in text
    assert "acm-switchover-validator" in text
    assert "--token-duration" in text
    assert "token_duration" in text
    assert "output_dir" in text
    assert defaults["acm_switchover_rbac_bootstrap"]["token_duration"] == "24h"
    assert packaged_duration
    assert packaged_duration.group(1) == "24h"
    assert "default: 24h" in packaged_script_text

    command_task = next(
        task
        for task in tasks
        if task.get("name") == "Generate kubeconfig from service account" and task.get("ansible.builtin.command")
    )
    argv = command_task["ansible.builtin.command"]["argv"]
    token_duration_arg = argv[argv.index("--token-duration") + 1]
    assert (
        token_duration_arg
        == "{{ (acm_switchover_rbac_bootstrap | default({})).get('token_duration', '24h') | default('24h', true) }}"
    )

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
        if task.get("tomazb.acm_switchover.acm_safe_path_validate", {}).get("path") == "{{ item.path }}"
    )
    file_index = next(idx for idx, task in enumerate(tasks) if task.get("ansible.builtin.file"))
    copy_index = next(idx for idx, task in enumerate(tasks) if task.get("ansible.builtin.copy"))
    validate_task = tasks[validate_index]["tomazb.acm_switchover.acm_safe_path_validate"]

    assert validate_index < file_index
    assert validate_index < copy_index
    assert validate_task.get("path_type") == "artifact"


def test_generate_kubeconfigs_expands_all_planned_role_targets():
    """role=both must generate one kubeconfig per concrete role target."""
    text = (RBAC_BOOTSTRAP_TASKS / "generate_kubeconfigs.yml").read_text()
    tasks = _load_tasks("generate_kubeconfigs.yml")

    assert "_rbac_plan.role_targets" in text
    assert "acm_switchover_rbac_bootstrap_generated_kubeconfigs" in text

    command_task = next(
        task
        for task in tasks
        if task.get("name") == "Generate kubeconfig from service account"
        and task.get("ansible.builtin.command")
        and task.get("loop") == "{{ _rbac_bootstrap_kubeconfig_targets }}"
    )
    copy_task = next(
        task
        for task in tasks
        if task.get("name") == "Write generated service account kubeconfig"
        and task.get("ansible.builtin.copy")
        and task.get("loop") == "{{ _rbac_generated_kubeconfigs.results | default([]) }}"
    )

    assert command_task["loop"] == "{{ _rbac_bootstrap_kubeconfig_targets }}"
    assert copy_task["loop"] == "{{ _rbac_generated_kubeconfigs.results | default([]) }}"


def test_validate_permissions_impersonates_bootstrapped_service_account():
    """Bootstrap validation must check the created service account, not the admin credential."""
    text = (RBAC_BOOTSTRAP_TASKS / "validate_permission_target.yml").read_text()

    assert "SubjectAccessReview" in text
    assert "SelfSubjectAccessReview" not in text
    assert "system:serviceaccount:acm-switchover:" in text
    assert "include_role" not in text


def test_validate_permissions_expands_all_planned_role_targets():
    """role=both must validate operator and validator permissions separately."""
    tasks = _load_tasks("validate_permissions.yml")
    include_task = next(
        task for task in tasks if task.get("ansible.builtin.include_tasks") == "validate_permission_target.yml"
    )

    assert include_task["ansible.builtin.include_tasks"] == "validate_permission_target.yml"
    assert (
        include_task["loop"]
        == "{{ _rbac_plan.role_targets | default([(acm_switchover_rbac_bootstrap | default({})).get('role', 'operator')]) }}"
    )
    assert include_task["loop_control"]["loop_var"] == "_rbac_bootstrap_role_target"


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

    role_targets = expand_rbac_role_targets(role)
    return bool(expression(item=item, _rbac_plan={"role": role, "role_targets": role_targets}))


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
                    "app.kubernetes.io/role": "operator",
                }
            }
        },
        "both",
    )
    assert _manifest_filter_applies(
        {
            "metadata": {
                "labels": {
                    "app.kubernetes.io/role": "validator",
                }
            }
        },
        "both",
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
