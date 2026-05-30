"""Contracts for shared activation/post-activation Ansible logic."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
ACTIVATION_TASKS = ROLES_DIR / "activation" / "tasks"
POST_ACTIVATION_TASKS = ROLES_DIR / "post_activation" / "tasks"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"
COMMON_TASKS = ROLES_DIR / "common" / "tasks"

SHARED_EXPECTATION_TASK = "{{ role_path }}/../common/tasks/resolve_managed_cluster_expectation.yml"


def _load_tasks(path: pathlib.Path) -> list[dict]:
    return yaml.safe_load(path.read_text()) or []


def _activation_block_tasks() -> list[dict]:
    main_tasks = _load_tasks(ACTIVATION_TASKS / "main.yml")
    return next(task["block"] for task in main_tasks if "block" in task)


def test_activation_and_post_activation_use_shared_managed_cluster_expectation():
    activation_tasks = _load_tasks(ACTIVATION_TASKS / "wait_for_restore.yml")
    post_activation_tasks = _load_tasks(POST_ACTIVATION_TASKS / "verify_managed_clusters.yml")

    activation_include = next(
        task
        for task in activation_tasks
        if task.get("name") == "Resolve full-restore managed-cluster presence expectation"
    )
    post_activation_include = next(
        task for task in post_activation_tasks if task.get("name") == "Resolve managed-cluster verification expectation"
    )

    assert activation_include["ansible.builtin.include_tasks"] == SHARED_EXPECTATION_TASK
    assert activation_include["vars"]["acm_switchover_managed_cluster_expectation_mode"] == "activation_restore_wait"
    assert post_activation_include["ansible.builtin.include_tasks"] == SHARED_EXPECTATION_TASK
    assert (
        post_activation_include["vars"]["acm_switchover_managed_cluster_expectation_mode"] == "post_activation_verify"
    )


def test_shared_managed_cluster_expectation_publishes_common_facts():
    tasks = _load_tasks(COMMON_TASKS / "resolve_managed_cluster_expectation.yml")
    text = (COMMON_TASKS / "resolve_managed_cluster_expectation.yml").read_text()

    assert any(task.get("name") == "Resolve activation managed-cluster expectation" for task in tasks)
    assert any(task.get("name") == "Resolve post-activation managed-cluster expectation" for task in tasks)
    assert "acm_switchover_resolved_min_managed_clusters" in text
    assert "acm_switchover_resolved_expected_managed_cluster_names" in text
    assert "acm_switchover_resolved_allow_zero_managed_clusters" in text


def test_shared_managed_cluster_expectation_validates_counts_before_int_coercion():
    tasks = _load_tasks(COMMON_TASKS / "resolve_managed_cluster_expectation.yml")
    text = (COMMON_TASKS / "resolve_managed_cluster_expectation.yml").read_text()
    task_names = [task.get("name") for task in tasks]

    validation_index = task_names.index("Validate managed-cluster expectation count inputs")
    activation_index = task_names.index("Resolve activation managed-cluster expectation")
    post_activation_zero_index = task_names.index("Resolve post-activation zero managed-cluster allowance")

    assert validation_index < activation_index
    assert validation_index < post_activation_zero_index
    assert "acm_switchover_operation.min_managed_clusters must be a non-negative integer when set" in text
    assert "acm_switchover_expected_managed_cluster_count must be a non-negative integer when set" in text
    assert "is match('^[0-9]+$')" in text


def test_activation_computes_auto_import_support_once_before_consumers():
    block_tasks = _activation_block_tasks()
    includes = [task.get("ansible.builtin.include_tasks") for task in block_tasks]

    support_index = includes.index("resolve_auto_import_support.yml")
    manage_index = includes.index("manage_auto_import.yml")
    apply_index = includes.index("apply_immediate_import.yml")

    assert support_index < manage_index < apply_index
    assert includes.count("resolve_auto_import_support.yml") == 1
    assert "version('2.14.0', '>=')" in (ACTIVATION_TASKS / "resolve_auto_import_support.yml").read_text()
    assert "version('2.14.0', '>=')" not in (ACTIVATION_TASKS / "manage_auto_import.yml").read_text()
    assert "version('2.14.0', '>=')" not in (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()


def test_finalization_keeps_separate_fresh_version_derivation():
    finalization_enable_backups = (FINALIZATION_TASKS / "enable_backups.yml").read_text()
    finalization_main = (FINALIZATION_TASKS / "main.yml").read_text()

    assert "Derive secondary ACM version safely" in finalization_enable_backups
    assert "resolve_auto_import_support.yml" not in finalization_main
