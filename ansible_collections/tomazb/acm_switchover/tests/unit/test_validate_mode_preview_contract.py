"""Contract: a validate run mutates nothing, through any supported entry point (#335, #284).

Validate is a preview exactly like dry_run or native check mode: every task that can write
to a cluster, or write durable local state, is reachable only under an execute gate
(``mode ... == 'execute'``) or the transaction preview fact. A ``!= 'dry_run'`` gate is
not enough, because validate passes it.

The guard resolves each task's action with Ansible's own ``ModuleArgsParser`` and fails
closed on any task it cannot resolve or that is not spelled as a canonical FQCN task key
(``action:``, ``local_action:``, short names, ``ansible.legacy.*``, templated actions).
Only then does it apply a read-only allowlist: every other action must be execute-gated.

Supported entry points are the shipped playbooks, every role's ``main.yml``, and every
``tasks_from`` target that shipped code names. Other task files are internal: they are
not entry roots, but they are still scanned wherever a supported entry reaches them.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml
from ansible.parsing.mod_args import ModuleArgsParser

COLLECTION_DIR = Path(__file__).resolve().parents[2]
ROLES_DIR = COLLECTION_DIR / "roles"
PLAYBOOKS_DIR = COLLECTION_DIR / "playbooks"
COLLECTION_ROLE_PREFIX = "tomazb.acm_switchover."
_CANONICAL = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+")

_TASK_INCLUDES = frozenset({"ansible.builtin.include_tasks", "ansible.builtin.import_tasks"})
_ROLE_INCLUDES = frozenset({"ansible.builtin.include_role", "ansible.builtin.import_role"})

# Actions that change nothing a later run, a cluster or an operator could observe as state.
READ_ONLY_ACTIONS = frozenset(
    {
        # control flow and facts
        "ansible.builtin.assert",
        "ansible.builtin.debug",
        "ansible.builtin.fail",
        "ansible.builtin.meta",
        "ansible.builtin.pause",
        "ansible.builtin.set_fact",
        # controller-side reads
        "ansible.builtin.slurp",
        "ansible.builtin.stat",
        # cluster reads
        "kubernetes.core.k8s_info",
        "tomazb.acm_switchover.acm_k8s_read_outcome",
        "tomazb.acm_switchover.acm_klusterlet_probe",
        "tomazb.acm_switchover.acm_restore_info",
        # pure planners and validators: inputs in, result out, no I/O
        "tomazb.acm_switchover.acm_argocd_filter",
        "tomazb.acm_switchover.acm_backup_continuity",
        "tomazb.acm_switchover.acm_backup_schedule",
        "tomazb.acm_switchover.acm_checkpoint_identity_validate",
        "tomazb.acm_switchover.acm_discovery",
        "tomazb.acm_switchover.acm_input_validate",
        "tomazb.acm_switchover.acm_kubeconfig_inspect",
        "tomazb.acm_switchover.acm_managedcluster_status",
        "tomazb.acm_switchover.acm_migration_evidence",
        "tomazb.acm_switchover.acm_pod_owner_classify",
        "tomazb.acm_switchover.acm_rbac_bootstrap",
        "tomazb.acm_switchover.acm_rbac_validate",
        "tomazb.acm_switchover.acm_safe_path_validate",
        # Report artifacts are run output, not execution state, and are written in every
        # mode by design (the preflight report is the point of a validate run).
        "tomazb.acm_switchover.acm_report_artifact",
        "tomazb.acm_switchover.acm_preflight_report",
        # The checkpoint action computes non-mutating from validate, dry_run and check
        # mode itself and persists nothing then (checkpoint_phase.py, see #284 audit).
        "tomazb.acm_switchover.checkpoint_phase",
    }
)

# Access reviews are created, answered and never persisted by the API server.
_ACCESS_REVIEW_KINDS = frozenset({"SubjectAccessReview", "SelfSubjectAccessReview"})
# Read-only controller tooling probes, exempt by their exact parsed command.
_READ_ONLY_COMMANDS = frozenset({"sh -c 'command -v oc || command -v kubectl'", "sh -c 'command -v jq'"})

# Entries that refuse validate before any write, so their writes are not walked. Each is
# tied to a refusal pin below (test_every_refusing_entry_carries_its_validate_refusal).
REFUSES_VALIDATE = {
    "finalization/tasks/disable_old_hub_observability.yml": "(acm_switchover_execution.mode | default('dry_run')) != 'validate'",
    "decommission/tasks/main.yml": "(acm_switchover_execution.mode | default('dry_run')) != 'validate'",
}

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


def _resolve(task: dict) -> tuple[str | None, dict, str]:
    """(action, args, refusal): the action is None whenever the guard cannot vouch for it."""
    try:
        action, args, _delegate = ModuleArgsParser(task).parse(skip_action_validation=True)
    except Exception as exc:  # any parse failure is refused, never skipped
        return None, {}, f"unresolvable action ({type(exc).__name__})"
    if "action" in task or "local_action" in task:
        return None, {}, f"non-canonical action form ({action!r})"
    if action not in task or not _CANONICAL.fullmatch(action) or action.startswith("ansible.legacy."):
        return None, {}, f"non-canonical action {action!r}"
    return action, args or {}, ""


def _is_read_only(action: str, args: dict) -> bool:
    if action in READ_ONLY_ACTIONS:
        return True
    if action == "kubernetes.core.k8s":
        definition = args.get("definition")
        return (
            isinstance(definition, dict)
            and definition.get("kind") in _ACCESS_REVIEW_KINDS
            and args.get("state", "present") == "present"
        )
    if action == "ansible.builtin.command":
        return args.get("_raw_params") in _READ_ONLY_COMMANDS and not args.get("argv")
    return False


class _Scan:
    """Walk the task graph from every supported entry and judge every reachable task."""

    def __init__(self):
        self.offenders: list[str] = []
        self.seen: set = set()
        self.visited: set[str] = set()
        self.judged_writes: list[str] = []
        self.tasks_from_roots: set[tuple[str, Path]] = set()

    def file(self, role: str, path: Path, conditions: list) -> None:
        where = path.relative_to(ROLES_DIR).as_posix() if path.is_relative_to(ROLES_DIR) else path.name
        context = (where, _gated(conditions, None))
        if where in REFUSES_VALIDATE or context in self.seen:
            return
        self.seen.add(context)
        self.visited.add(where)
        self.tasks(role, path, where, yaml.safe_load(path.read_text(encoding="utf-8")), conditions)

    def role(self, name: str, tasks_from: str, conditions: list, where: str, task_name) -> None:
        short = name[len(COLLECTION_ROLE_PREFIX) :] if name.startswith(COLLECTION_ROLE_PREFIX) else ""
        entry = ROLES_DIR / short / "tasks" / (tasks_from if tasks_from.endswith(".yml") else f"{tasks_from}.yml")
        if not short or "{{" in tasks_from or not entry.is_file():
            self.offenders.append(f"{where}: {task_name} (unresolvable role {name!r})")
            return
        if tasks_from not in ("main", "main.yml"):
            self.tasks_from_roots.add((short, entry))
        self.file(short, entry, conditions)

    def tasks(self, role: str, path: Path, where: str, tasks, inherited: list) -> None:
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            conds = inherited + _as_list(task.get("when"))
            if any(section in task for section in ("block", "rescue", "always")):
                for section in ("block", "rescue", "always"):
                    self.tasks(role, path, where, task.get(section), conds)
                continue
            action, args, refusal = _resolve(task)
            if action is None:
                self.offenders.append(f"{where}: {task.get('name')} ({refusal})")
            elif action in _TASK_INCLUDES:
                target = str(args.get("_raw_params") or args.get("file") or "")
                target = target.replace("{{ role_path }}", str(ROLES_DIR / role))
                if not target or "{{" in target:
                    self.offenders.append(f"{where}: {task.get('name')} (computed include {target!r})")
                    continue
                self.file(role, (path.parent / target).resolve(), conds)
            elif action in _ROLE_INCLUDES:
                self.role(
                    str(args.get("name", "")), str(args.get("tasks_from", "main")), conds, where, task.get("name")
                )
            elif not _is_read_only(action, args):
                self.judged_writes.append(f"{where}: {task.get('name')}")
                if not _gated(conds, task.get("check_mode")):
                    self.offenders.append(f"{where}: {task.get('name')} ({action})")

    def playbook(self, path: Path) -> None:
        where = f"playbooks/{path.name}"
        self.visited.add(where)
        for play in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            self.tasks("", path, where, play.get("pre_tasks"), [])
            for entry in play.get("roles") or []:
                name = entry if isinstance(entry, str) else entry.get("role", entry.get("name", ""))
                self.role(
                    str(name), "main", _as_list(None if isinstance(entry, str) else entry.get("when")), where, name
                )
            for key in ("tasks", "post_tasks", "handlers"):
                self.tasks("", path, where, play.get(key), [])


def _entry_roles() -> list[str]:
    return sorted(p.parent.parent.name for p in ROLES_DIR.glob("*/tasks/main.yml"))


def _scan_everything() -> _Scan:
    scan = _Scan()
    for playbook in sorted(PLAYBOOKS_DIR.glob("*.yml")):
        scan.playbook(playbook)
    for role in _entry_roles():
        scan.file(role, ROLES_DIR / role / "tasks" / "main.yml", [])
    # A tasks_from target that shipped code names is itself a supported direct entry, so it
    # is also walked from an ungated context. New targets found on the way are walked too.
    walked: set = set()
    while pending := scan.tasks_from_roots - walked:
        for role, entry in sorted(pending):
            walked.add((role, entry))
            scan.file(role, entry, [])
    return scan


def test_every_write_reachable_from_a_supported_entry_is_execute_gated():
    assert _scan_everything().offenders == []


def test_every_role_entry_and_shipped_playbook_is_a_scan_root():
    scan = _scan_everything()
    for role in _entry_roles():
        assert f"{role}/tasks/main.yml" in scan.visited or f"{role}/tasks/main.yml" in REFUSES_VALIDATE
    for playbook in PLAYBOOKS_DIR.glob("*.yml"):
        assert f"playbooks/{playbook.name}" in scan.visited
    # The three roles #284 fixes, and the entry playbooks it puts in scope, are walked.
    for name in ("primary_prep", "argocd_manage", "rbac_bootstrap"):
        assert f"{name}/tasks/main.yml" in scan.visited
    for name in ("argocd_resume.yml", "rbac_bootstrap.yml", "argocd_manage_test.yml"):
        assert f"playbooks/{name}" in scan.visited


def test_internal_files_are_not_roots_but_are_scanned_through_supported_roles():
    """Astra r2: internal-file exemptions exclude unsupported entry roots only."""
    scan = _scan_everything()
    root_files = {entry.relative_to(ROLES_DIR).as_posix() for _role, entry in scan.tasks_from_roots}
    internal = {
        "post_activation/tasks/fix_klusterlet.yml",
        "rbac_bootstrap/tasks/apply_manifest_file.yml",
        "rbac_bootstrap/tasks/generate_kubeconfigs.yml",
        "primary_prep/tasks/scale_observability.yml",
        "primary_prep/tasks/manage_auto_import.yml",
        "primary_prep/tasks/pause_backups.yml",
        "argocd_manage/tasks/pause.yml",
        "argocd_manage/tasks/resume.yml",
    }
    assert not internal & root_files
    assert internal <= scan.visited


def test_the_walk_sees_the_writes_it_guards():
    """Kill condition for a vacuous walk: the known writers are found and judged."""
    judged = _scan_everything().judged_writes
    # #335's activation/post_activation/finalization writers plus #284's: BackupSchedule
    # patch, delete and restore, auto-import, compactor scale, Argo CD pause and resume,
    # RBAC manifests, kubeconfig directory, token command and kubeconfig file.
    assert len(judged) >= 30
    for expected in (
        "primary_prep/tasks/scale_observability.yml: Scale thanos-compactor",
        "primary_prep/tasks/pause_backups.yml: Delete BackupSchedule resource for ACM 2.11 pause",
        "primary_prep/tasks/pause_backups.yml: Restore BackupSchedule from saved body after failed pause delete",
        "primary_prep/tasks/manage_auto_import.yml: Add disable-auto-import annotations",
        "argocd_manage/tasks/pause.yml: Remove automated sync policy",
        "argocd_manage/tasks/resume.yml: Restore original sync policy",
        "rbac_bootstrap/tasks/apply_manifest_file.yml: Apply filtered RBAC resources",
        "rbac_bootstrap/tasks/generate_kubeconfigs.yml: Generate kubeconfig from service account",
        "rbac_bootstrap/tasks/generate_kubeconfigs.yml: Write generated service account kubeconfig",
        "post_activation/tasks/fix_klusterlet.yml: ",
    ):
        assert any(entry.startswith(expected) for entry in judged), expected


@pytest.mark.parametrize("where", sorted(REFUSES_VALIDATE))
def test_every_refusing_entry_carries_its_validate_refusal(where):
    """An exemption is only as good as the refusal it stands for."""
    tasks = yaml.safe_load((ROLES_DIR / where).read_text(encoding="utf-8"))
    refusals = [
        task
        for task in tasks
        if isinstance(task.get("ansible.builtin.assert"), dict)
        and REFUSES_VALIDATE[where] in _as_list(task["ansible.builtin.assert"].get("that"))
    ]
    assert refusals, where


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


@pytest.mark.parametrize("role", ["activation", "post_activation", "finalization", "primary_prep"])
def test_a_preview_result_reports_the_actual_preview_mode(role):
    """Round-3 nit: a validate run must not report reason dry_run."""
    found = []
    for path in sorted((ROLES_DIR / role / "tasks").glob("*.yml")):
        for reason, message in _preview_reasons(yaml.safe_load(path.read_text(encoding="utf-8"))):
            found.append(path.name)
            assert reason == _MODE_LABEL, f"{path.name}: reason {reason!r}"
            assert "[dry_run]" not in message, f"{path.name}: {message!r}"
    assert found, "the walk must see the preview results it checks"


# --- the scanner judges semantics, call contexts, every include and every action form ------

_GATE = "acm_switchover_execution.mode | default('dry_run') == 'execute'"
_WRITE = "- name: Write\n  kubernetes.core.k8s:\n    state: present\n"


def _tree(tmp_path, monkeypatch, files: dict) -> list[str]:
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    monkeypatch.setitem(globals(), "ROLES_DIR", tmp_path)
    scan = _Scan()
    scan.file("probe", tmp_path / "probe" / "tasks" / "main.yml", [])
    return scan.offenders


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
    # #284 (Astra H2): every action spelling Ansible accepts is resolved or refused.
    "action-string": {"probe/tasks/main.yml": "- name: Write\n  action: kubernetes.core.k8s state=present\n"},
    "action-mapping": {
        "probe/tasks/main.yml": "- name: Write\n  action:\n    module: kubernetes.core.k8s\n    state: present\n"
    },
    "local-action-string": {"probe/tasks/main.yml": "- name: Write\n  local_action: ansible.builtin.copy dest=x\n"},
    "local-action-mapping": {
        "probe/tasks/main.yml": "- name: Write\n  local_action:\n    module: ansible.builtin.debug\n    msg: x\n"
    },
    "action-on-read-only-module": {"probe/tasks/main.yml": "- name: Read\n  action: ansible.builtin.debug msg=x\n"},
    "short-writer": {"probe/tasks/main.yml": "- name: Write\n  assemble:\n    src: a\n    dest: b\n"},
    "short-read": {"probe/tasks/main.yml": "- name: Read\n  debug:\n    msg: x\n"},
    "legacy-writer": {"probe/tasks/main.yml": "- name: Write\n  ansible.legacy.copy:\n    dest: x\n"},
    "templated-action": {"probe/tasks/main.yml": '- name: Write\n  action: "{{ x }}"\n'},
    "templated-key": {"probe/tasks/main.yml": '- name: Write\n  "{{ x }}": {}\n'},
    "two-actions": {"probe/tasks/main.yml": "- name: Write\n  ansible.builtin.debug: {}\n  ansible.builtin.copy: {}\n"},
    "no-action": {"probe/tasks/main.yml": "- name: Nothing\n  register: r\n"},
    "unknown-canonical": {"probe/tasks/main.yml": "- name: Write\n  example.unknown.writer: {}\n"},
    "canonical-file-writer": {"probe/tasks/main.yml": "- name: Write\n  ansible.builtin.assemble:\n    dest: x\n"},
    "ungated-command": {"probe/tasks/main.yml": "- name: Run\n  ansible.builtin.command: kubectl create token x\n"},
    "probe-name-with-another-command": {
        "probe/tasks/main.yml": "- name: Check for jq on the controller\n  ansible.builtin.command: sh -c 'rm -rf x'\n"
    },
    "access-review-name-with-another-kind": {
        "probe/tasks/main.yml": (
            "- name: Run SelfSubjectAccessReview for each permission\n  kubernetes.core.k8s:\n"
            "    state: present\n    definition:\n      kind: ClusterRoleBinding\n"
        )
    },
    "access-review-deleted": {
        "probe/tasks/main.yml": (
            "- name: Review\n  kubernetes.core.k8s:\n    state: absent\n    definition:\n      kind: SubjectAccessReview\n"
        )
    },
}


@pytest.mark.parametrize("files", list(_SCANNER_CASES.values()), ids=list(_SCANNER_CASES))
def test_the_scanner_refuses_what_it_cannot_prove_gated(tmp_path, monkeypatch, files):
    assert _tree(tmp_path, monkeypatch, files)


def test_the_scanner_admits_a_gated_write_in_every_call_context(tmp_path, monkeypatch):
    files = {
        "probe/tasks/main.yml": (
            f'- ansible.builtin.include_tasks: write.yml\n  when: "{_GATE}"\n'
            f"- ansible.builtin.include_tasks: write.yml\n  when:\n    - other | bool\n    - {_GATE}\n"
            "- ansible.builtin.include_role:\n    name: tomazb.acm_switchover.other\n"
            "    tasks_from: write\n"
            f'  when: "{_GATE}"\n'
        ),
        "probe/tasks/write.yml": _WRITE,
        "other/tasks/write.yml": _WRITE,
    }
    assert _tree(tmp_path, monkeypatch, files) == []


def test_the_scanner_admits_read_only_actions_and_exact_exemptions(tmp_path, monkeypatch):
    files = {
        "probe/tasks/main.yml": (
            "- name: Read\n  kubernetes.core.k8s_info:\n    kind: Pod\n"
            "- name: Review\n  kubernetes.core.k8s:\n    state: present\n    definition:\n"
            "      kind: SelfSubjectAccessReview\n"
            "- name: Probe\n  ansible.builtin.command: sh -c 'command -v jq'\n"
            "- name: Fact\n  ansible.builtin.set_fact:\n    x: 1\n"
        )
    }
    assert _tree(tmp_path, monkeypatch, files) == []


def _mutated_roles(tmp_path, relative: str, old: str, new: str) -> Path:
    shutil.copytree(ROLES_DIR, tmp_path / "roles")
    target = tmp_path / "roles" / relative
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation anchor must be unique in {relative}"
    mutated = text.replace(old, new)
    target.write_text(mutated, encoding="utf-8")
    assert mutated != text  # the mutation is present on the surface the scan reads
    return tmp_path / "roles"


@pytest.mark.parametrize(
    ("relative", "task", "old"),
    [
        (
            "primary_prep/tasks/scale_observability.yml",
            "Scale thanos-compactor",
            "  register: acm_primary_compactor_scale\n"
            "  when:\n"
            "    - (acm_primary_compactor_info.resources | default([]) | length) > 0\n"
            "    - acm_switchover_execution.mode | default('dry_run') == 'execute'\n",
        ),
        (
            "argocd_manage/tasks/pause.yml",
            "Remove automated sync policy",
            "      register: acm_argocd_pause_results\n"
            "      when:\n"
            "        - acm_switchover_argocd_mock_apps is not defined\n"
            "        - acm_switchover_execution.mode | default('dry_run') == 'execute'\n",
        ),
    ],
)
def test_reverting_one_real_execute_gate_is_caught(tmp_path, monkeypatch, relative, task, old):
    """The gate-revert mutation: a real write's gate back to `!= 'dry_run'` must be refused."""
    new = old.replace("== 'execute'", "!= 'dry_run'", 1)
    roles = _mutated_roles(tmp_path, relative, old, new)
    monkeypatch.setitem(globals(), "ROLES_DIR", roles)
    offenders = _scan_everything().offenders
    assert any(entry.startswith(f"{relative}: {task}") for entry in offenders), offenders
