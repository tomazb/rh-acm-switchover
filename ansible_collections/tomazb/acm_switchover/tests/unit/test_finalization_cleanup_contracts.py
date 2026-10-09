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
    assert tasks[0][_INCLUDE] == "require_journaled_locator_absent.yml"
    assert tasks[0]["when"] == "not (_acm_preview | bool)"
    gate = _load("require_journaled_locator_absent.yml")
    assert [
        task["tomazb.acm_switchover.acm_migration_evidence"]["operation"]
        for task in _walk(gate)
        if "tomazb.acm_switchover.acm_migration_evidence" in task
    ] == ["cleanup_locator_absent"]


def test_every_collision_recreation_is_gated_on_a_proven_empty_locator():
    """Finding 1: the delete/recreate cycle, the recreate and the rescue restoration each re-prove absence."""
    tasks = _load("repair_backup_schedule_collision.yml")
    names = [task.get("name") for task in tasks]
    assert names.index("Prove the journaled Restore locator empty before collision repair") < names.index(
        "Recreate BackupSchedule for collision repair"
    )
    cycle = tasks[names.index("Recreate BackupSchedule for collision repair")]
    for section, write in (
        (cycle["block"], "Recreate BackupSchedule after collision repair delete"),
        (cycle["rescue"][1]["block"], "Restore BackupSchedule from saved body after failed collision repair"),
    ):
        steps = [task.get("name") for task in section]
        gate = steps.index(write) - 1
        assert section[gate][_INCLUDE] == "require_journaled_locator_absent.yml"
        assert section[gate]["when"] == "not (_acm_preview | bool)"


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


def test_the_phase_result_reports_the_journaled_cleanup():
    """An accepted DELETE of the journaled Restore is a change the finalization result reports."""
    run = next(task for task in _load("main.yml") if task.get("name") == "Run finalization phase")
    publish = next(task for task in run["block"] if task.get("name") == "Publish finalization result")
    result = publish["ansible.builtin.set_fact"]["acm_switchover_finalization_result"]
    assert "acm_switchover_journaled_restore_cleanup_result.changed" in result["changed"]
    assert "acm_switchover_journaled_restore_cleanup_result" in result["journaled_restore_cleanup"]
    cleanup = _load("cleanup_journaled_restore.yml")[-1]["ansible.builtin.set_fact"]
    step = cleanup["acm_switchover_journaled_restore_cleanup_result"]["step"]
    # A preview's skipped register is still defined, so the step is chosen by the preview flag.
    assert "_acm_preview" in step
