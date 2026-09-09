"""Safety tests for old-hub reads in finalization tasks."""

import pathlib

import yaml

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"
FINALIZATION_TASKS = ROLES_DIR / "finalization" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((FINALIZATION_TASKS / name).read_text())


def _walk_tasks(tasks: list[dict]):
    for task in tasks:
        yield task
        for nested_key in ("block", "rescue", "always"):
            nested_tasks = task.get(nested_key, []) or []
            yield from _walk_tasks(nested_tasks)


def _finalization_task_files() -> list[pathlib.Path]:
    return sorted(path for path in FINALIZATION_TASKS.rglob("*") if path.suffix in {".yml", ".yaml"})


def test_no_finalization_task_deletes_a_multiclusterobservability_by_name():
    """A name-only delete can remove a replacement object that reused the name.

    The one MCO teardown lives in the decommission role behind the UID-preconditioned
    guarded delete; finalization must reach it, never re-implement it.
    """
    offenders = []
    for path in _finalization_task_files():
        for task in _walk_tasks(yaml.safe_load(path.read_text()) or []):
            args = task.get("kubernetes.core.k8s") or {}
            if args.get("kind") == "MultiClusterObservability" and args.get("state") == "absent":
                offenders.append(f"{path.name}: {task.get('name')}")

    assert offenders == []


def test_no_finalization_task_absorbs_a_failure_with_failed_when_false():
    """`failed_when: false` on an old-hub read turns an unverifiable hub into a pass."""
    offenders = []
    for path in _finalization_task_files():
        for task in _walk_tasks(yaml.safe_load(path.read_text()) or []):
            if task.get("failed_when") is False:
                offenders.append(f"{path.name}: {task.get('name')}")

    assert offenders == []


def test_the_old_hub_observability_adapter_only_delegates():
    """The adapter owns no MCO logic: no read, no delete, no drain wait of its own."""
    tasks = _load_yaml("disable_old_hub_observability.yml")
    modules = {
        module for task in _walk_tasks(tasks) for module in task if module.startswith(("kubernetes.core.", "tomazb."))
    }

    assert modules == set()
    includes = [
        task["ansible.builtin.include_role"] for task in _walk_tasks(tasks) if "ansible.builtin.include_role" in task
    ]
    assert len(includes) == 1
    assert includes[0]["name"] == "tomazb.acm_switchover.decommission"
    assert includes[0]["tasks_from"] == "delete_observability.yml"


def test_the_old_hub_observability_adapter_never_includes_the_decommission_entry_point():
    """decommission/tasks/main.yml owns a standalone lifecycle finalization is already inside."""
    tasks = _load_yaml("disable_old_hub_observability.yml")
    includes = [
        task["ansible.builtin.include_role"] for task in _walk_tasks(tasks) if "ansible.builtin.include_role" in task
    ]

    assert includes
    for include in includes:
        # Absent `tasks_from` IS main.yml, so its presence is the whole assertion.
        assert include.get("tasks_from") not in (None, "main", "main.yml")


def test_the_old_hub_observability_adapter_seeds_what_the_shared_task_reads():
    """The shared task reads `_acm_decommission_records_outcomes`; nothing else seeds it here."""
    tasks = _load_yaml("disable_old_hub_observability.yml")
    seeded = {key for task in _walk_tasks(tasks) for key in (task.get("ansible.builtin.set_fact") or {})}

    assert "_acm_decommission_records_outcomes" in seeded
    assert "acm_switchover_decommission_outcomes" in seeded


def test_the_old_hub_observability_adapter_requires_durable_state_before_deleting():
    """The shared task's checkpoint writers refuse without an identity map; say so first."""
    tasks = _load_yaml("disable_old_hub_observability.yml")
    assertion = next(task for task in _walk_tasks(tasks) if "ansible.builtin.assert" in task)
    when = str(assertion.get("when", ""))

    assert "checkpoint.enabled" in str(assertion["ansible.builtin.assert"]["that"])
    assert "not ansible_check_mode" in when
    assert "execute" in when


def test_the_old_hub_observability_adapter_keeps_its_published_result_shape():
    """finalization/main.yml consumes changed/deleted_mcos/status by name."""
    tasks = _load_yaml("disable_old_hub_observability.yml")
    published = [
        fact["acm_switchover_disable_old_hub_observability_result"]
        for fact in (task.get("ansible.builtin.set_fact") or {} for task in _walk_tasks(tasks))
        if "acm_switchover_disable_old_hub_observability_result" in fact
    ]

    assert published
    for result in published:
        assert set(result) == {"changed", "deleted_mcos", "status"}
    assert "dry_run" in str(published[-1]["status"])


def test_disable_old_hub_observability_debug_tasks_do_not_use_unsupported_warn_parameter():
    """ansible.builtin.debug does not accept warn, so finalization must not pass it."""
    tasks = _load_yaml("disable_old_hub_observability.yml")

    offenders = [
        task.get("name", "<unnamed>")
        for task in _walk_tasks(tasks)
        if "warn" in (task.get("ansible.builtin.debug") or {})
    ]

    assert offenders == []


def test_verify_old_hub_state_does_not_suppress_read_failures():
    """Regression verification reads must not downgrade old-hub API failures to success."""
    tasks = _load_yaml("verify_old_hub_state.yml")

    k8s_info_tasks = [task for task in tasks if "kubernetes.core.k8s_info" in task]
    assert k8s_info_tasks, "verify_old_hub_state.yml must read old-hub resources"
    assert all(
        task.get("failed_when") is not False for task in k8s_info_tasks
    ), "verify_old_hub_state.yml must not suppress old-hub read failures"
