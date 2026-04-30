"""Tests for activation role auto-import strategy management."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
ACTIVATION_TASKS = ROLES_DIR / "activation" / "tasks"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"
CONSTANTS_FILE = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "module_utils" / "constants.py"


def test_manage_auto_import_file_exists():
    """manage_auto_import.yml must exist in activation tasks."""
    assert (ACTIVATION_TASKS / "manage_auto_import.yml").exists()


def test_reset_auto_import_in_finalization():
    """reset_auto_import.yml must exist in finalization tasks (not activation)."""
    assert (FINALIZATION_TASKS / "reset_auto_import.yml").exists(), (
        "reset_auto_import.yml must live in finalization — the reset must happen "
        "after the post_activation cleanup_auto_import_annotations window closes"
    )


def test_main_includes_manage_after_passive_verification_before_activation():
    """activation/main.yml must verify passive sync before temporary ImportAndSync management."""
    main = yaml.safe_load((ACTIVATION_TASKS / "main.yml").read_text())
    block_tasks = None
    for item in main:
        if "block" in item:
            block_tasks = item["block"]
            break
    assert block_tasks is not None, "main.yml must have a block"

    includes = [t.get("ansible.builtin.include_tasks", "") for t in block_tasks]

    manage_idx = None
    verify_idx = None
    activate_idx = None
    reset_idx = None
    for i, inc in enumerate(includes):
        if inc == "manage_auto_import.yml":
            manage_idx = i
        elif inc == "verify_passive_sync.yml":
            verify_idx = i
        elif inc == "activate_restore.yml":
            activate_idx = i
        elif inc == "reset_auto_import.yml":
            reset_idx = i

    assert manage_idx is not None, "manage_auto_import.yml must be included in activation"
    assert verify_idx is not None, "verify_passive_sync.yml must be included in activation"
    assert verify_idx < manage_idx, "passive sync must be verified before ImportAndSync management"
    assert manage_idx < activate_idx, "manage_auto_import must come before activate_restore"
    assert reset_idx is None, (
        "reset_auto_import.yml must NOT be in activation/tasks/main.yml — "
        "it belongs in finalization to match Python CLI timing"
    )


def test_finalization_includes_reset_after_discover():
    """finalization/main.yml must include reset_auto_import after discover_resources."""
    main = yaml.safe_load((FINALIZATION_TASKS / "main.yml").read_text())
    block_tasks = None
    for item in main:
        if "block" in item:
            block_tasks = item["block"]
            break
    assert block_tasks is not None, "finalization/main.yml must have a block"

    includes = [t.get("ansible.builtin.include_tasks", "") for t in block_tasks]

    discover_idx = None
    reset_idx = None
    for i, inc in enumerate(includes):
        if inc == "discover_resources.yml":
            discover_idx = i
        elif inc == "reset_auto_import.yml":
            reset_idx = i

    assert reset_idx is not None, "reset_auto_import.yml must be included in finalization"
    assert discover_idx is not None, "discover_resources.yml must be in finalization"
    assert reset_idx > discover_idx, "reset_auto_import must come after discover_resources"


def test_activation_persists_auto_import_reset_flag_in_checkpoint():
    """activation/main.yml must persist auto-import reset intent for resumed finalization."""
    text = (ACTIVATION_TASKS / "main.yml").read_text()
    assert "operational_data:" in text
    assert "auto_import_strategy_changed" in text, (
        "activation/main.yml must write auto_import_strategy_changed into checkpoint operational_data "
        "so finalization can still reset ImportAndSync after a resumed run"
    )


def test_finalization_restores_auto_import_reset_flag_from_checkpoint():
    """finalization/main.yml must rehydrate auto-import reset intent before reset runs."""
    text = (FINALIZATION_TASKS / "main.yml").read_text()
    assert "_checkpoint_enter.checkpoint" in text
    assert "auto_import_strategy_changed" in text, (
        "finalization/main.yml must restore auto_import_strategy_changed from checkpoint operational_data "
        "before including reset_auto_import.yml"
    )


def test_apply_immediate_import_is_not_a_stub():
    """apply_immediate_import.yml must contain real k8s tasks, not just set_fact."""
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()
    assert "kubernetes.core.k8s_info" in content, "Must query k8s for import config"
    assert "kubernetes.core.k8s" in content, "Must patch ManagedClusters"
    assert "local-cluster" in content, "Must filter out local-cluster"


def test_apply_immediate_import_does_not_swallow_patch_failures():
    """activation must fail if immediate-import annotations cannot be applied."""
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()
    assert (
        "ignore_errors: true" not in content
    ), "apply_immediate_import.yml must not ignore ManagedCluster patch failures"


def test_manage_auto_import_preserves_python_guards_and_detect_only_mode():
    """Activation auto-import management must mirror Python _maybe_set_auto_import_strategy()."""
    content = (ACTIVATION_TASKS / "manage_auto_import.yml").read_text()

    assert "acm_secondary_version" in content
    assert "version('2.14.0', '>=')" in content
    assert "old_hub_action" in content
    assert "local-cluster" in content
    assert "rejectattr('metadata.name', 'equalto', 'local-cluster')" in content
    assert "manage_auto_import_strategy" in content
    assert "Detect-only" in content
    assert "ImportAndSync" in content


def test_apply_immediate_import_requires_acm_214_or_newer():
    """Immediate-import annotations are an ACM 2.14+ behavior and must be version-gated."""
    content = (ACTIVATION_TASKS / "apply_immediate_import.yml").read_text()

    assert "acm_secondary_version" in content
    assert "version('2.14.0', '>=')" in content
    assert "_acm_secondary_supports_auto_import" in content


def test_constants_include_auto_import():
    """Ansible constants must include auto-import strategy constants."""
    content = CONSTANTS_FILE.read_text()
    assert "IMPORT_CONTROLLER_CONFIG_CM" in content
    assert "AUTO_IMPORT_STRATEGY_KEY" in content
    assert "AUTO_IMPORT_STRATEGY_DEFAULT" in content
    assert "AUTO_IMPORT_STRATEGY_SYNC" in content
    assert "IMMEDIATE_IMPORT_ANNOTATION" in content
    assert "LOCAL_CLUSTER_NAME" in content
