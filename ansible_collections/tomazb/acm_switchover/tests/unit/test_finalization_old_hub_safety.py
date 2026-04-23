"""Safety tests for old-hub reads in finalization tasks."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((FINALIZATION_TASKS / name).read_text())


def _stringify(value) -> str:
    return str(value).lower()


def _find_mco_read_block(tasks: list[dict]) -> dict:
    for task in tasks:
        for nested in task.get("block", []):
            module = nested.get("kubernetes.core.k8s_info", {})
            if module.get("kind") == "MultiClusterObservability":
                return task
    raise AssertionError("disable_old_hub_observability.yml must wrap the MCO read in a block")


def test_disable_old_hub_observability_handles_absent_resource_separately_from_other_errors():
    """Unexpected MCO read failures must fail instead of silently skipping deletion."""
    tasks = _load_yaml("disable_old_hub_observability.yml")

    block = _find_mco_read_block(tasks)
    rescue = block.get("rescue")
    assert rescue is not None, "MCO discovery must have a rescue block"

    error_capture = next(
        (
            task
            for task in rescue
            if "_acm_old_hub_mco_read_error" in task.get("ansible.builtin.set_fact", {})
        ),
        None,
    )
    assert error_capture is not None, "Rescue must capture the MCO read error before classifying it"

    normalize_task = next(
        (
            task
            for task in rescue
            if "_acm_old_hub_mco_info" in task.get("ansible.builtin.set_fact", {})
        ),
        None,
    )
    assert normalize_task is not None, "Rescue must normalize absent-resource cases to an empty MCO list"

    normalize_when = _stringify(normalize_task.get("when", ""))
    assert "_acm_old_hub_mco_read_error" in normalize_when
    assert "could not find the requested resource" in normalize_when
    assert "the server doesn\\'t have a resource type" in normalize_when
    assert 'no matches for kind "multiclusterobservability"' in normalize_when

    fail_task = next((task for task in rescue if "ansible.builtin.fail" in task), None)
    assert fail_task is not None, "Rescue must fail on unexpected old-hub MCO read errors"
    fail_text = _stringify(fail_task["ansible.builtin.fail"].get("msg", ""))
    assert "_acm_old_hub_mco_read_error" in fail_text


def test_verify_old_hub_state_does_not_suppress_read_failures():
    """Regression verification reads must not downgrade old-hub API failures to success."""
    tasks = _load_yaml("verify_old_hub_state.yml")

    k8s_info_tasks = [task for task in tasks if "kubernetes.core.k8s_info" in task]
    assert k8s_info_tasks, "verify_old_hub_state.yml must read old-hub resources"
    assert all(
        task.get("failed_when") is not False for task in k8s_info_tasks
    ), "verify_old_hub_state.yml must not suppress old-hub read failures"
