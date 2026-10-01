"""Contract: the finalization role wires the R4-04 Restore-cleanup transaction (plan Task 10).

The runtime behavior is covered against a fake API in
tests/integration/test_finalization_cleanup_runtime.py; these checks pin the wiring that
test cannot reach: the transaction runs before every old-hub action and BackupSchedule
enablement, the enablement gate precedes every BackupSchedule write, and the journaled
locator has no name-only delete path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

TASKS = Path(__file__).resolve().parents[2] / "roles" / "finalization" / "tasks"
_INCLUDE = "ansible.builtin.include_tasks"
_DELETE_MODULES = ("kubernetes.core.k8s", "k8s")
_GUARDED = "tomazb.acm_switchover.acm_restore_guarded_mutation"


def _load(name: str) -> list:
    return yaml.safe_load((TASKS / name).read_text(encoding="utf-8"))


def _walk(tasks):
    for task in tasks or []:
        yield task
        for section in ("block", "rescue", "always"):
            yield from _walk(task.get(section))


def _phase_includes() -> list:
    run = next(task for task in _load("main.yml") if task.get("name") == "Run finalization phase")
    return [task[_INCLUDE] for task in run["block"] if _INCLUDE in task]


def test_the_transaction_runs_before_the_old_hub_and_every_restore_or_backup_step():
    includes = _phase_includes()
    first = includes.index("cleanup_journaled_restore.yml")
    for later in (
        "disable_old_hub_observability.yml",
        "cleanup_restores.yml",
        "enable_backups.yml",
        "handle_old_hub.yml",
    ):
        assert first < includes.index(later), later


def test_the_journal_facts_and_the_invalid_journal_refusal_precede_the_transaction():
    run = next(task for task in _load("main.yml") if task.get("name") == "Run finalization phase")
    names = [task.get("name") for task in run["block"]]
    transaction = names.index("Reconcile the journaled Restore cleanup")
    assert names.index("Guard the finalization role entry") < transaction
    assert names.index("Publish the finalization migration journal facts") < transaction
    assert names.index("Refuse an invalid recorded migration journal") < transaction


def test_the_enablement_gate_precedes_every_backup_schedule_write():
    tasks = _load("enable_backups.yml")
    gate = tasks[0]
    assert gate["when"] == "not (_acm_preview | bool)"
    assert [
        task["tomazb.acm_switchover.acm_migration_evidence"]["operation"]
        for task in _walk(gate["block"])
        if "tomazb.acm_switchover.acm_migration_evidence" in task
    ] == ["cleanup_locator_absent"]
    assert not any(module in task for task in _walk(tasks[:1]) for module in _DELETE_MODULES)


def test_the_journaled_locator_is_deleted_only_by_the_guarded_primitive():
    for name in ("cleanup_journaled_restore.yml", "cleanup_journaled_restore_attempt.yml"):
        for task in _walk(_load(name)):
            assert not any(module in task for module in _DELETE_MODULES), (name, task.get("name"))
    deletes = [task[_GUARDED] for task in _walk(_load("cleanup_journaled_restore_attempt.yml")) if _GUARDED in task]
    assert len(deletes) == 1
    assert deletes[0]["action"] == "delete"
    assert deletes[0]["expected_uid"] and deletes[0]["expected_resource_version"]


def test_generic_cleanup_classifies_only_the_reserved_inventory_in_execute_mode():
    tasks = _load("cleanup_restores.yml")
    reserve = next(
        task for task in tasks if task.get("name") == "Reserve the journaled Restore locator from generic cleanup"
    )
    assert reserve["tomazb.acm_switchover.acm_migration_evidence"]["operation"] == "cleanup_generic_restores"
    assert reserve["when"] == "not (_acm_preview | bool)"
    select = next(task for task in tasks if task.get("name") == "Select the Restores generic cleanup classifies")
    source = select["ansible.builtin.set_fact"]["_acm_secondary_restores_to_cleanup"]["resources"]
    assert "_acm_generic_restores.restores\n  if not (_acm_preview | bool)" in source


def test_executing_backup_schedule_decisions_use_a_fresh_strict_inventory():
    """Strict-read correction: discovery and the post-enable refresh read through one strict file."""
    execute = "acm_switchover_execution.mode | default('dry_run') == 'execute'"
    discover = next(
        task for task in _load("discover_resources.yml") if task.get(_INCLUDE) == "read_backup_schedules.yml"
    )
    assert discover["when"] == execute
    refresh = next(task for task in _load("enable_backups.yml") if task.get(_INCLUDE) == "read_backup_schedules.yml")
    assert execute in refresh["when"]
    preseeded = next(
        task
        for task in _load("discover_resources.yml")
        if (task.get("kubernetes.core.k8s_info") or {}).get("kind") == "BackupSchedule"
    )
    assert "acm_switchover_execution.mode | default('dry_run') != 'execute'" in preseeded["when"]
    reads = _load("read_backup_schedules.yml")
    assert reads[0][_INCLUDE].endswith("/activation/tasks/strict_read.yml")
    assert reads[0]["vars"]["_acm_read"]["read_mode"] == "list"
    assert "ansible.builtin.fail" in reads[1] and "!= 'ok'" in reads[1]["when"]
