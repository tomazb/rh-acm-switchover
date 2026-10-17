"""Shared helper functions for YAML role contract tests."""


def _when_text(task: dict) -> str:
    """Normalize a task's 'when' condition to a single string for assertion."""
    when = task.get("when", "")
    if isinstance(when, list):
        return " ".join(str(w) for w in when)
    return str(when)


def _flatten_tasks(tasks: list) -> list:
    """Recursively flatten block/rescue/always nested tasks into a flat list."""
    result = []
    for task in tasks or []:
        result.append(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                result.extend(_flatten_tasks(task[key]))
    return result


def _workflow_entry_sections(outer: dict) -> tuple:
    """Locate lifecycle owners by role purpose and verify their safety ordering."""
    tasks = outer["block"]

    def include(role, entry):
        return next(
            task
            for task in tasks
            if task.get("ansible.builtin.include_role")
            == {"name": "tomazb.acm_switchover." + role, "tasks_from": entry}
        )

    assessment = include("finalization", "assess_completed")
    identity = include("preflight", "identity_barrier")
    recovery = include("argocd_manage", "repause_recovery")
    phases = next(task for task in tasks if "block" in task and "rescue" in task)
    assert (
        "rescue" not in outer and "rescue" not in assessment and "rescue" not in identity and "rescue" not in recovery
    )
    assert tasks.index(assessment) < tasks.index(identity) < tasks.index(recovery) < tasks.index(phases)
    for active in (identity, recovery, phases):
        assert _when_text(active) == "not (acm_switchover_backup_completed_assessed | bool)"
    return assessment, identity, recovery, phases
