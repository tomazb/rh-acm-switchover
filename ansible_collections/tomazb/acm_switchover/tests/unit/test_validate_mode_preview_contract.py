"""Contract: a direct validate entry into a mutating role mutates nothing (Task 8 round-2 finding 1).

The playbooks stop after preflight in validate mode, but activation, post_activation and
finalization can be entered directly. There validate is a preview exactly like dry_run or
native check mode: every task that can write to a cluster is reachable only under an
execute gate (``mode ... == 'execute'``) or the transaction preview fact. A ``!= 'dry_run'``
gate is not enough, because validate passes it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"

# Modules and role entries that can write to a cluster.
MUTATING_MODULES = frozenset(
    {
        "kubernetes.core.k8s",
        "kubernetes.core.k8s_json_patch",
        "kubernetes.core.k8s_scale",
        "tomazb.acm_switchover.acm_restore_guarded_mutation",
        "tomazb.acm_switchover.acm_klusterlet_remediate",
    }
)
MUTATING_ROLES = frozenset({"tomazb.acm_switchover.decommission", "tomazb.acm_switchover.argocd_manage"})

# The old hub observability teardown refuses validate outright before any delete
# (test_mco_teardown_runtime pins the refusal); its shared teardown is not walked.
REFUSES_VALIDATE = frozenset({"finalization/tasks/disable_old_hub_observability.yml"})

_EXECUTE_GATE = re.compile(r"\.mode \| default\('[a-z_]+'\)\)? == 'execute'|\.get\('mode', '[a-z_]+'\) == 'execute'")
_PREVIEW_GATE = "not (_acm_preview | bool)"
_PREVIEW_CHECK_MODE = "{{ _acm_preview | bool }}"


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _resolve(role: str, target: str) -> Path:
    return (ROLES_DIR / role / "tasks" / target.replace("{{ role_path }}", str(ROLES_DIR / role))).resolve()


def _gated(conditions, check_mode) -> bool:
    if check_mode == _PREVIEW_CHECK_MODE:
        return True
    return any(_EXECUTE_GATE.search(str(c)) or _PREVIEW_GATE in str(c) for c in conditions)


def _ungated_mutations(role: str, path: Path, conditions: list, seen: set) -> list[str]:
    where = path.relative_to(ROLES_DIR).as_posix()
    if where in REFUSES_VALIDATE or (where, len(conditions)) in seen:
        return []
    seen.add((where, len(conditions)))
    offenders = []

    def walk(tasks, inherited):
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            conds = inherited + _as_list(task.get("when"))
            for section in ("block", "rescue", "always"):
                if section in task:
                    walk(task[section], conds)
            for key in ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks"):
                if key in task:
                    target = task[key] if isinstance(task[key], str) else task[key]["file"]
                    owner = target.split("/../")[1].split("/")[0] if "/../" in target else role
                    offenders.extend(_ungated_mutations(owner, _resolve(role, target), conds, seen))
            module = next((key for key in task if key in MUTATING_MODULES), None)
            role_call = task.get("ansible.builtin.include_role") or task.get("ansible.builtin.import_role")
            if role_call and role_call.get("name") in MUTATING_ROLES:
                if role_call.get("tasks_from"):
                    owner = role_call["name"].rsplit(".", 1)[1]
                    entry = ROLES_DIR / owner / "tasks" / f"{role_call['tasks_from']}.yml"
                    offenders.extend(_ungated_mutations(owner, entry, conds, seen))
                else:
                    module = f"role {role_call['name']}"
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
