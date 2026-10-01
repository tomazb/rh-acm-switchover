"""Contract: a direct validate entry into a mutating role mutates nothing (Task 8 round-2 finding 1).

The playbooks stop after preflight in validate mode, but activation, post_activation and
finalization can be entered directly. There validate is a preview exactly like dry_run or
native check mode: every task that can write to a cluster is reachable only under an
execute gate (``mode ... == 'execute'``) or the transaction preview fact. A ``!= 'dry_run'``
gate is not enough, because validate passes it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
COLLECTION_ROLE_PREFIX = "tomazb.acm_switchover."
_PREFIXES = ("", "ansible.builtin.", "ansible.legacy.")

# Modules that can write to a cluster, by every name a task can use for them.
MUTATING_MODULES = frozenset(
    f"{prefix}{name}"
    for prefix, names in (
        ("kubernetes.core.", ("k8s", "k8s_json_patch", "k8s_scale")),
        ("", ("k8s", "k8s_json_patch", "k8s_scale")),
        (COLLECTION_ROLE_PREFIX, ("acm_restore_guarded_mutation", "acm_klusterlet_remediate")),
        ("", ("acm_restore_guarded_mutation", "acm_klusterlet_remediate")),
    )
    for name in names
)
_TASK_INCLUDES = frozenset(
    f"{prefix}{name}" for prefix in _PREFIXES for name in ("include", "include_tasks", "import_tasks")
)
_ROLE_INCLUDES = frozenset(f"{prefix}{name}" for prefix in _PREFIXES for name in ("include_role", "import_role"))

# The old hub observability teardown refuses validate outright before any delete
# (test_mco_teardown_runtime pins the refusal); its shared teardown is not walked.
REFUSES_VALIDATE = frozenset({"finalization/tasks/disable_old_hub_observability.yml"})

# A condition proves the gate only as a whole `when` item (items are ANDed), never as a
# fragment of a larger expression such as `... == 'execute' or true` or `not (...)`.
_GATES = frozenset(
    {
        "acm_switchover_execution.mode | default('dry_run') == 'execute'",
        "(acm_switchover_execution.mode | default('dry_run')) == 'execute'",
        "not (_acm_preview | bool)",
    }
)
_PREVIEW_CHECK_MODE = "{{ _acm_preview | bool }}"


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _gated(conditions, check_mode) -> bool:
    if check_mode == _PREVIEW_CHECK_MODE:
        return True
    return any(" ".join(str(condition).split()) in _GATES for condition in conditions)


def _ungated_mutations(role: str, path: Path, conditions: list, seen: set) -> list[str]:
    """Writes reachable from `path`, run by `role`, that no enclosing condition gates.

    A file's verdict depends only on whether its call context is already gated, so each
    (file, gated) context is walked once. An include or role call the walk cannot resolve
    to a collection file is refused rather than skipped.
    """
    where = path.relative_to(ROLES_DIR).as_posix()
    context = (where, _gated(conditions, None))
    if where in REFUSES_VALIDATE or context in seen:
        return []
    seen.add(context)
    offenders = []

    def walk(tasks, inherited):
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            conds = inherited + _as_list(task.get("when"))
            for section in ("block", "rescue", "always"):
                if section in task:
                    walk(task[section], conds)
            for key in _TASK_INCLUDES.intersection(task):
                target = task[key] if isinstance(task[key], str) else (task[key] or {}).get("file", "")
                target = target.replace("{{ role_path }}", str(ROLES_DIR / role))
                if "{{" in target:
                    offenders.append(f"{where}: {task.get('name')} (computed include {task[key]!r})")
                    continue
                offenders.extend(_ungated_mutations(role, (path.parent / target).resolve(), conds, seen))
            for key in _ROLE_INCLUDES.intersection(task):
                call = task[key] or {}
                name, tasks_from = call.get("name", ""), str(call.get("tasks_from", "main"))
                short = name[len(COLLECTION_ROLE_PREFIX) :] if name.startswith(COLLECTION_ROLE_PREFIX) else ""
                entry = (
                    ROLES_DIR / short / "tasks" / (tasks_from if tasks_from.endswith(".yml") else f"{tasks_from}.yml")
                )
                if not short or "{{" in tasks_from or not entry.is_file():
                    offenders.append(f"{where}: {task.get('name')} (unresolvable role {name!r})")
                    continue
                offenders.extend(_ungated_mutations(short, entry, conds, seen))
            module = next((key for key in task if key in MUTATING_MODULES), None)
            if module and not _gated(conds, task.get("check_mode")):
                offenders.append(f"{where}: {task.get('name')} ({module})")

    walk(yaml.safe_load(path.read_text(encoding="utf-8")), conditions)
    return offenders


@pytest.mark.parametrize("role", ["activation", "post_activation", "finalization"])
def test_every_cluster_write_of_a_mutating_role_is_execute_gated(role):
    assert _ungated_mutations(role, ROLES_DIR / role / "tasks" / "main.yml", [], set()) == []


def test_the_walk_sees_the_writes_it_guards():
    """Kill condition for a vacuous walk: the known writers are found and judged."""
    found = []
    original = _gated

    def recording(conditions, check_mode):
        found.append(check_mode)
        return original(conditions, check_mode)

    globals()["_gated"] = recording
    try:
        for role in ("activation", "post_activation", "finalization"):
            _ungated_mutations(role, ROLES_DIR / role / "tasks" / "main.yml", [], set())
    finally:
        globals()["_gated"] = original
    # Three guarded Restore mutations, auto-import, annotations, observability, klusterlet,
    # backups, Restore cleanup, collision repair, old hub disposition.
    assert len(found) >= 20


def test_the_old_hub_observability_teardown_refuses_validate():
    text = (ROLES_DIR / "finalization" / "tasks" / "disable_old_hub_observability.yml").read_text(encoding="utf-8")
    assert "(acm_switchover_execution.mode | default('dry_run')) != 'validate'" in text


def test_a_not_dry_run_gate_does_not_count_as_execute():
    assert not _gated(["acm_switchover_execution.mode | default('dry_run') != 'dry_run'"], None)
    assert _gated(["acm_switchover_execution.mode | default('dry_run') == 'execute'"], None)
    assert _gated(["(acm_switchover_execution.mode | default('dry_run')) == 'execute'"], None)
    assert _gated([], _PREVIEW_CHECK_MODE)


_PREVIEW_BRANCH = "acm_switchover_execution.mode | default('dry_run') != 'execute'"
_MODE_LABEL = "{{ acm_switchover_execution.mode | default('dry_run') }}"


def _preview_reasons(node):
    """The `reason` values a preview-branch set_fact publishes (dry_run and validate alike)."""
    if isinstance(node, list):
        for item in node:
            yield from _preview_reasons(item)
    elif isinstance(node, dict):
        if _PREVIEW_BRANCH in _as_list(node.get("when")) and isinstance(node.get("ansible.builtin.set_fact"), dict):
            for value in node["ansible.builtin.set_fact"].values():
                if isinstance(value, dict) and "reason" in value:
                    yield value["reason"], value.get("message", "")
        for value in node.values():
            if isinstance(value, (list, dict)):
                yield from _preview_reasons(value)


@pytest.mark.parametrize("role", ["activation", "post_activation", "finalization"])
def test_a_preview_result_reports_the_actual_preview_mode(role):
    """Round-3 nit: a validate run must not report reason dry_run."""
    found = []
    for path in sorted((ROLES_DIR / role / "tasks").glob("*.yml")):
        for reason, message in _preview_reasons(yaml.safe_load(path.read_text(encoding="utf-8"))):
            found.append(path.name)
            assert reason == _MODE_LABEL, f"{path.name}: reason {reason!r}"
            assert "[dry_run]" not in message, f"{path.name}: {message!r}"
    assert found, "the walk must see the preview results it checks"


# --- round-3 finding 3: the scanner judges semantics, call contexts and every include --------

_GATE = "acm_switchover_execution.mode | default('dry_run') == 'execute'"
_WRITE = "- name: Write\n  kubernetes.core.k8s:\n    state: present\n"


def _tree(tmp_path, monkeypatch, files: dict) -> list[str]:
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    monkeypatch.setitem(globals(), "ROLES_DIR", tmp_path)
    return _ungated_mutations("probe", tmp_path / "probe" / "tasks" / "main.yml", [], set())


_SCANNER_CASES = {
    "or-true": {"probe/tasks/main.yml": _WRITE + f'  when: "{_GATE} or true"\n'},
    "negated": {"probe/tasks/main.yml": _WRITE + f'  when: "not ({_GATE})"\n'},
    "second-context": {
        "probe/tasks/main.yml": (
            f'- ansible.builtin.include_tasks: write.yml\n  when: "{_GATE}"\n'
            "- ansible.builtin.include_tasks: write.yml\n  when: other | bool\n"
        ),
        "probe/tasks/write.yml": _WRITE,
    },
    "short-include": {"probe/tasks/main.yml": "- include_tasks: write.yml\n", "probe/tasks/write.yml": _WRITE},
    "legacy-import": {
        "probe/tasks/main.yml": "- ansible.legacy.import_tasks: write.yml\n",
        "probe/tasks/write.yml": _WRITE,
    },
    "unlisted-role": {
        "probe/tasks/main.yml": "- ansible.builtin.include_role:\n    name: tomazb.acm_switchover.other\n",
        "other/tasks/main.yml": _WRITE,
    },
    "foreign-role": {"probe/tasks/main.yml": "- ansible.builtin.import_role:\n    name: example.other.writer\n"},
    "computed-file": {"probe/tasks/main.yml": '- ansible.builtin.include_tasks: "{{ step }}.yml"\n'},
}


@pytest.mark.parametrize("files", list(_SCANNER_CASES.values()), ids=list(_SCANNER_CASES))
def test_the_scanner_refuses_what_it_cannot_prove_gated(tmp_path, monkeypatch, files):
    assert _tree(tmp_path, monkeypatch, files)


def test_the_scanner_admits_a_gated_write_in_every_call_context(tmp_path, monkeypatch):
    files = {
        "probe/tasks/main.yml": (
            f'- ansible.builtin.include_tasks: write.yml\n  when: "{_GATE}"\n'
            f"- include_tasks: write.yml\n  when:\n    - other | bool\n    - {_GATE}\n"
            "- ansible.builtin.include_role:\n    name: tomazb.acm_switchover.other\n"
            "    tasks_from: write\n"
            f'  when: "{_GATE}"\n'
        ),
        "probe/tasks/write.yml": _WRITE,
        "other/tasks/write.yml": _WRITE,
    }
    assert _tree(tmp_path, monkeypatch, files) == []
