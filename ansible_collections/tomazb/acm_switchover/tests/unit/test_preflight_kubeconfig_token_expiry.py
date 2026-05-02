"""Static tests for preflight kubeconfig token-expiry wiring."""

import pathlib

import yaml

COLLECTION_DIR = pathlib.Path(__file__).resolve().parents[2]
PREFLIGHT_TASKS = COLLECTION_DIR / "roles" / "preflight" / "tasks"
PREFLIGHT_DEFAULTS = COLLECTION_DIR / "roles" / "preflight" / "defaults" / "main.yml"


def _load_tasks() -> list[dict]:
    return yaml.safe_load((PREFLIGHT_TASKS / "validate_kubeconfigs.yml").read_text())


def _find_task(tasks: list[dict], name: str) -> dict:
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"task '{name}' not found in validate_kubeconfigs.yml")


def test_preflight_defaults_set_token_expiry_warning_hours():
    """preflight defaults must expose the token expiry warning threshold."""
    defaults = yaml.safe_load(PREFLIGHT_DEFAULTS.read_text())

    assert defaults["acm_switchover_features"]["token_expiry_warning_hours"] == 4


def test_validate_kubeconfigs_invokes_token_expiry_inspection_for_both_hubs():
    """validate_kubeconfigs.yml must inspect kubeconfig token expiry for each hub."""
    tasks = _load_tasks()

    primary_task = _find_task(tasks, "Inspect primary kubeconfig token expiry")
    secondary_task = _find_task(tasks, "Inspect secondary kubeconfig token expiry")

    assert "tomazb.acm_switchover.acm_kubeconfig_inspect" in primary_task
    assert "tomazb.acm_switchover.acm_kubeconfig_inspect" in secondary_task


def test_validate_kubeconfigs_wires_warning_hours_feature_into_module_call():
    """validate_kubeconfigs.yml must pass the configured warning-hours value into the module."""
    tasks = _load_tasks()

    module_args = _find_task(tasks, "Inspect secondary kubeconfig token expiry")[
        "tomazb.acm_switchover.acm_kubeconfig_inspect"
    ]

    assert module_args["warning_hours"] == "{{ acm_switchover_features.token_expiry_warning_hours }}"


def test_validate_kubeconfigs_records_stable_token_expiry_result_ids():
    """validate_kubeconfigs.yml must append the planned token-expiry result ids."""
    text = (PREFLIGHT_TASKS / "validate_kubeconfigs.yml").read_text()

    assert "preflight-kubeconfig-primary-token-expiry" in text
    assert "preflight-kubeconfig-secondary-token-expiry" in text


def test_primary_token_expiry_tasks_are_restore_only_guarded():
    """Primary token-expiry inspection must be skipped in restore-only mode."""
    tasks = _load_tasks()

    primary_inspect = _find_task(tasks, "Inspect primary kubeconfig token expiry")
    primary_record = _find_task(tasks, "Record primary kubeconfig token expiry result")
    expected_guard = "not (acm_switchover_operation.restore_only | default(false))"

    assert primary_inspect["when"] == expected_guard
    assert primary_record["when"] == expected_guard


def test_secondary_token_expiry_tasks_are_always_present():
    """Secondary token-expiry inspection must not be restore-only guarded."""
    tasks = _load_tasks()

    secondary_inspect = _find_task(tasks, "Inspect secondary kubeconfig token expiry")
    secondary_record = _find_task(tasks, "Record secondary kubeconfig token expiry result")

    assert "when" not in secondary_inspect
    assert "when" not in secondary_record
