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
