"""Static tests for preflight input validation ordering."""

import pathlib

import yaml

PREFLIGHT_MAIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "roles"
    / "preflight"
    / "tasks"
    / "main.yml"
)


def _top_level_and_block_tasks() -> list[dict]:
    tasks = yaml.safe_load(PREFLIGHT_MAIN.read_text())
    flattened: list[dict] = []
    for task in tasks:
        flattened.append(task)
        flattened.extend(task.get("block", []))
    return flattened


def test_preflight_validates_inputs_before_entering_checkpoint_phase():
    """Preflight must validate controller-side inputs before checkpoint writes."""
    tasks = _top_level_and_block_tasks()

    validate_inputs_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.include_tasks") == "validate_inputs.yml"
    )
    checkpoint_enter_index = next(
        index
        for index, task in enumerate(tasks)
        if "tomazb.acm_switchover.checkpoint_phase" in task
        and task["tomazb.acm_switchover.checkpoint_phase"].get("status") == "enter"
    )

    assert validate_inputs_index < checkpoint_enter_index, (
        "preflight must run validate_inputs.yml before checkpoint_phase status=enter "
        "so unsafe checkpoint paths are rejected before controller-side writes"
    )


def test_preflight_initializes_validation_accumulator_only_once():
    """Preflight must not reset validation facts after input validation runs."""
    tasks = _top_level_and_block_tasks()
    init_count = sum(
        1
        for task in tasks
        if task.get("name") == "Initialize preflight result accumulator"
    )

    assert init_count == 1, "preflight should initialize the validation accumulator once and preserve input results"
