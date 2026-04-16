"""Tests for activation role auto-import strategy management."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
ACTIVATION_TASKS = ROLES_DIR / "activation" / "tasks"
CONSTANTS_FILE = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "module_utils" / "constants.py"


def test_manage_auto_import_file_exists():
    """manage_auto_import.yml must exist in activation tasks."""
    assert (ACTIVATION_TASKS / "manage_auto_import.yml").exists()


def test_reset_auto_import_file_exists():
    """reset_auto_import.yml must exist in activation tasks."""
    assert (ACTIVATION_TASKS / "reset_auto_import.yml").exists()


def test_main_includes_manage_before_activation():
    """main.yml must include manage_auto_import before activate_restore."""
    main = yaml.safe_load((ACTIVATION_TASKS / "main.yml").read_text())
    # Find the block tasks
    block_tasks = None
    for item in main:
        if "block" in item:
            block_tasks = item["block"]
            break
    assert block_tasks is not None, "main.yml must have a block"

    task_names = [t.get("name", "") for t in block_tasks]
    includes = [t.get("ansible.builtin.include_tasks", "") for t in block_tasks]

    manage_idx = None
    activate_idx = None
    reset_idx = None
    immediate_idx = None
    for i, inc in enumerate(includes):
        if inc == "manage_auto_import.yml":
            manage_idx = i
        elif inc == "activate_restore.yml":
            activate_idx = i
        elif inc == "reset_auto_import.yml":
            reset_idx = i
        elif inc == "apply_immediate_import.yml":
            immediate_idx = i

    assert manage_idx is not None, "manage_auto_import.yml must be included"
    assert reset_idx is not None, "reset_auto_import.yml must be included"
    assert manage_idx < activate_idx, "manage_auto_import must come before activate_restore"
    assert reset_idx > immediate_idx, "reset_auto_import must come after apply_immediate_import"


def test_apply_immediate_import_is_not_a_stub():
    """apply_immediate_import.yml must contain real k8s tasks, not just set_fact."""
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()
    assert "kubernetes.core.k8s_info" in content, "Must query k8s for import config"
    assert "kubernetes.core.k8s" in content, "Must patch ManagedClusters"
    assert "local-cluster" in content, "Must filter out local-cluster"


def test_constants_include_auto_import():
    """Ansible constants must include auto-import strategy constants."""
    content = CONSTANTS_FILE.read_text()
    assert "IMPORT_CONTROLLER_CONFIG_CM" in content
    assert "AUTO_IMPORT_STRATEGY_KEY" in content
    assert "AUTO_IMPORT_STRATEGY_DEFAULT" in content
    assert "AUTO_IMPORT_STRATEGY_SYNC" in content
    assert "IMMEDIATE_IMPORT_ANNOTATION" in content
    assert "LOCAL_CLUSTER_NAME" in content
