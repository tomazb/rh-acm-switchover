"""Guardrail: checkpoint key vocabulary lives in module_utils, not role or
playbook YAML.

Mirror of the Python side's tests/test_run_record_guardrails.py: roles and
playbooks consume the flattened `facts` dict returned by a checkpoint_phase
ENTER result register; raw operational_data read chains against that enter
register bypass the facade and are forbidden (issue #214).

Exception: playbooks/argocd_resume.yml resumes Argo CD standalone by reading
a checkpoint file it slurps and parses itself (`_argocd_resume_checkpoint`),
not a checkpoint_phase enter-result register — that variable has no `facts`
key to read, so its raw operational_data chains are the correct, and only,
way to consume that data and are excluded from the playbook scan below.
"""

import pathlib
import re

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    KEY_DECOMMISSION_TEARDOWN_RECORDS,
    KEY_MIGRATION_BACKUPS,
)

COLLECTION_ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLES_DIR = COLLECTION_ROOT / "roles"
PLAYBOOKS_DIR = COLLECTION_ROOT / "playbooks"
PLUGINS_DIR = COLLECTION_ROOT / "plugins"
# R4-04 amendment AC13: module_utils/checkpoint.py owns the migration journal key and
# checkpoint_phase reaches it only through that module's constant and functions.
MIGRATION_JOURNAL_KEY_OWNER = PLUGINS_DIR / "module_utils" / "checkpoint.py"
QUOTED_MIGRATION_JOURNAL_KEY = re.compile(r"""["']""" + KEY_MIGRATION_BACKUPS + r"""["']""")

FORBIDDEN_PATTERNS = (
    ".get('operational_data'",
    '.get("operational_data"',
)

# See module docstring: argocd_resume.yml reads a raw slurped-and-parsed
# checkpoint file for standalone resume, not a checkpoint_phase enter-result
# register, so it has no `facts` dict to converge on.
ALLOWED_RAW_CHECKPOINT_PLAYBOOKS = frozenset({"argocd_resume.yml"})
PREFLIGHT_TASKS_DIR = ROLES_DIR / "preflight" / "tasks"
PREFLIGHT_POST_IDENTITY = PREFLIGHT_TASKS_DIR / "post_identity.yml"


def test_preflight_post_identity_allows_only_checkpoint_control_inputs():
    """Preflight may resume operational facts, never identity evidence, from enter."""
    text = PREFLIGHT_POST_IDENTITY.read_text(encoding="utf-8")

    assert "_checkpoint_enter | default({})).skipped_phase" in text
    assert "_checkpoint_enter | default({})).get('facts', {})" in text
    for forbidden in (
        "_checkpoint_enter.hub_identities",
        "_checkpoint_enter | default({})).hub_identities",
        "_checkpoint_enter | default({})).get('hub_identities'",
        "cluster_uid",
        "operation_identity",
        "_acm_primary_identity_namespace",
        "_acm_secondary_identity_namespace",
    ):
        assert forbidden not in text, f"post-identity control flow must not trust {forbidden}"


def test_roles_do_not_read_operational_data_directly():
    offenders = []
    for path in sorted(ROLES_DIR.rglob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in FORBIDDEN_PATTERNS):
            offenders.append(str(path.relative_to(ROLES_DIR)))
    assert not offenders, (
        "Role YAML must read checkpoint state via _checkpoint_enter.facts, "
        f"not raw operational_data chains. Offenders: {offenders}"
    )


def test_teardown_record_key_is_never_named_in_role_or_playbook_yaml():
    """The raw durable key stays private to checkpoint.py.

    The `.get('operational_data'` patterns above do not catch bracket access, so
    the raw key literal is forbidden outright. Roles consume the validated
    ``facts.teardown_records`` facade and never name the storage key themselves.
    """
    offenders = []
    for directory in (ROLES_DIR, PLAYBOOKS_DIR):
        # Both suffixes: roles/**/*.yaml files exist, so a tasks/main.yaml would
        # otherwise evade this scan (controller ruling C15).
        for pattern in ("*.yml", "*.yaml"):
            for path in sorted(directory.rglob(pattern)):
                if KEY_DECOMMISSION_TEARDOWN_RECORDS in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(COLLECTION_ROOT)))
    assert not offenders, (
        f"The '{KEY_DECOMMISSION_TEARDOWN_RECORDS}' operational_data key is owned by "
        f"module_utils/checkpoint.py and must not be named in YAML. Offenders: {offenders}"
    )


def test_playbooks_do_not_read_operational_data_directly():
    offenders = []
    for path in sorted(PLAYBOOKS_DIR.rglob("*.yml")):
        if path.name in ALLOWED_RAW_CHECKPOINT_PLAYBOOKS:
            continue
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in FORBIDDEN_PATTERNS):
            offenders.append(str(path.relative_to(PLAYBOOKS_DIR)))
    assert not offenders, (
        "Playbook YAML must read checkpoint_phase enter results via "
        "_checkpoint_enter.facts, not raw operational_data chains "
        "(playbooks/argocd_resume.yml is exempt — see module docstring). "
        f"Offenders: {offenders}"
    )


def _yaml_files():
    for directory in (ROLES_DIR, PLAYBOOKS_DIR):
        for pattern in ("*.yml", "*.yaml"):
            yield from sorted(directory.rglob(pattern))


# #332: the one YAML shape that may name the journal key. It is the sole operational_data
# key of a checkpoint_phase `status: update` task, and its value is exactly the complete
# candidate a validating acm_migration_evidence task of the same file registered. The
# allowance is structural (parsed YAML), per task, never per file.
JOURNAL_WRITER_MODULE = "tomazb.acm_switchover.checkpoint_phase"
JOURNAL_CANDIDATE_MODULE = "tomazb.acm_switchover.acm_migration_evidence"
_CANDIDATE_REFERENCE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\.candidate\s*\}\}$")


# The acm_migration_evidence operations that return a journal candidate.
CANDIDATE_OPERATIONS = frozenset(
    {
        "freeze",
        "post_patch",
        "post_create",
        "completion_bundle",
        "complete",
        "post_activation_names_evidence",
        "post_activation_completion",
    }
)
_PLAY_TASK_LISTS = ("pre_tasks", "tasks", "post_tasks", "handlers")
_SET_FACT_MODULES = ("ansible.builtin.set_fact", "set_fact")


def _executable_tasks(document) -> list:
    """Every task Ansible would execute from this document, in file order.

    A playbook's task lists, or a role task file's top-level list, and their blocks,
    rescues and always sections. Mappings anywhere else (play or task `vars`, module
    arguments) are data, never tasks.
    """
    if not isinstance(document, list):
        return []
    is_playbook = any(isinstance(item, dict) and ("hosts" in item or "import_playbook" in item) for item in document)
    lists = (
        [play.get(name) or [] for play in document if isinstance(play, dict) for name in _PLAY_TASK_LISTS]
        if is_playbook
        else [document]
    )
    tasks: list = []

    def walk(items):
        for task in items if isinstance(items, list) else []:
            if not isinstance(task, dict):
                continue
            tasks.append(task)
            for section in ("block", "rescue", "always"):
                walk(task.get(section))

    for items in lists:
        walk(items)
    return tasks


def _assigned_names(node) -> list:
    """Names any `vars` mapping in the document defines (repeats kept)."""
    names: list = []
    if isinstance(node, dict):
        if isinstance(node.get("vars"), dict):
            names.extend(node["vars"])
        for value in node.values():
            names.extend(_assigned_names(value))
    elif isinstance(node, list):
        for item in node:
            names.extend(_assigned_names(item))
    return names


def _candidate_registers(document) -> set:
    """Registers that can only hold a validated candidate.

    The register of an executable acm_migration_evidence task running a candidate-producing
    operation, registered exactly once in the file and never reassigned by another register,
    a set_fact or a vars mapping.
    """
    tasks = _executable_tasks(document)
    registered = [task["register"] for task in tasks if isinstance(task.get("register"), str)]
    reassigned = set(_assigned_names(document))
    for task in tasks:
        for module in _SET_FACT_MODULES:
            if isinstance(task.get(module), dict):
                reassigned.update(task[module])
    candidates = set()
    for task in tasks:
        args = task.get(JOURNAL_CANDIDATE_MODULE)
        name = task.get("register")
        if isinstance(args, dict) and args.get("operation") in CANDIDATE_OPERATIONS and isinstance(name, str):
            if registered.count(name) == 1 and name not in reassigned:
                candidates.add(name)
    return candidates


def _is_allowed_journal_write(task: dict, registers: set) -> bool:
    args = task.get(JOURNAL_WRITER_MODULE)
    if not isinstance(args, dict) or args.get("status") != "update":
        return False
    data = args.get("operational_data")
    if not isinstance(data, dict) or list(data) != [KEY_MIGRATION_BACKUPS]:
        return False
    value = data[KEY_MIGRATION_BACKUPS]
    match = _CANDIDATE_REFERENCE.match(value.strip()) if isinstance(value, str) else None
    return match is not None and match.group(1) in registers


def journal_key_offenders(node, registers: set, where: str = "", executable=None) -> list:
    """Locations naming the journal key outside the allowed writer shape of an executable task."""
    executable = executable if executable is not None else {id(task) for task in _executable_tasks(node)}
    offenders = []
    if isinstance(node, dict):
        allowed = id(node) in executable and _is_allowed_journal_write(node, registers)
        for key, value in node.items():
            location = f"{where}/{key}"
            if isinstance(key, str) and KEY_MIGRATION_BACKUPS in key:
                offenders.append(location)
            if allowed and key == JOURNAL_WRITER_MODULE:
                value = {name: item for name, item in value.items() if name != "operational_data"}
            offenders.extend(journal_key_offenders(value, registers, location, executable))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            offenders.extend(journal_key_offenders(item, registers, f"{where}[{index}]", executable))
    elif isinstance(node, str) and KEY_MIGRATION_BACKUPS in node:
        offenders.append(where)
    return offenders


class _TaskLoader(yaml.SafeLoader):
    """Safe loading that keeps Ansible's custom tags (e.g. !unsafe) as their plain values."""


_TaskLoader.add_multi_constructor("!", lambda loader, _tag, node: loader.construct_scalar(node))


def yaml_journal_key_offenders(paths) -> list:
    offenders: list = []
    for path in paths:
        for document in yaml.load_all(path.read_text(encoding="utf-8"), Loader=_TaskLoader):
            registers = _candidate_registers(document)
            offenders.extend(f"{path.name}:{location}" for location in journal_key_offenders(document, registers, ""))
    return offenders


def test_migration_journal_key_is_named_in_yaml_only_by_validated_update_writes():
    """No YAML reads the journal key; it writes it only through checkpoint_phase status: update."""
    offenders = yaml_journal_key_offenders(_yaml_files())
    assert not offenders, (
        f"The '{KEY_MIGRATION_BACKUPS}' operational_data key is owned by module_utils/checkpoint.py; YAML may "
        "name it only as the sole operational_data key of a checkpoint_phase status: update task whose value "
        f"is a registered acm_migration_evidence candidate. Offenders: {offenders}"
    )


_ALLOWED_WRITER = f"""
- name: Build the candidate
  {JOURNAL_CANDIDATE_MODULE}:
    operation: freeze
  register: _candidate_result
- name: Persist the candidate
  {JOURNAL_WRITER_MODULE}:
    phase: activation
    checkpoint: "{{{{ acm_switchover_execution.checkpoint }}}}"
    status: update
    operational_data:
      {KEY_MIGRATION_BACKUPS}: "{{{{ _candidate_result.candidate }}}}"
"""


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_the_allowance_admits_the_validated_update_writer(tmp_path, suffix):
    path = tmp_path / f"tasks{suffix}"
    path.write_text(_ALLOWED_WRITER, encoding="utf-8")
    assert yaml_journal_key_offenders([path]) == []


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
@pytest.mark.parametrize(
    "mutation",
    [
        ("status: update", "status: pass"),
        ("status: update", "status: fail"),
        (f"      {KEY_MIGRATION_BACKUPS}:", f"      argocd_run_id: x\n      {KEY_MIGRATION_BACKUPS}:"),
        ("_candidate_result.candidate }}", "_candidate_result.candidate | combine({}) }}"),
        ("_candidate_result.candidate }}", "_candidate_result }}"),
        ("  register: _candidate_result", "  register: _other_result"),
        (f"  {JOURNAL_CANDIDATE_MODULE}:", "  ansible.builtin.set_fact:"),
        (f"  {JOURNAL_WRITER_MODULE}:", "  tomazb.acm_switchover.acm_checkpoint:"),
        ("  register: _candidate_result", "  register: _candidate_result\n  when: facts." + KEY_MIGRATION_BACKUPS),
    ],
    ids=[
        "status-pass",
        "status-fail",
        "extra-key",
        "filtered-value",
        "whole-result",
        "unregistered",
        "not-a-candidate-module",
        "other-action",
        "raw-read-in-when",
    ],
)
def test_the_allowance_rejects_every_other_shape(tmp_path, suffix, mutation):
    old, new = mutation
    assert old in _ALLOWED_WRITER
    path = tmp_path / f"tasks{suffix}"
    path.write_text(_ALLOWED_WRITER.replace(old, new, 1), encoding="utf-8")
    assert yaml_journal_key_offenders([path])


def test_the_allowance_finds_raw_uses_nested_in_blocks_and_values(tmp_path):
    path = tmp_path / "play.yml"
    path.write_text(
        f"""
- hosts: localhost
  tasks:
    - block:
        - ansible.builtin.set_fact:
            journal: "{{{{ _checkpoint_enter.facts['{KEY_MIGRATION_BACKUPS}'] }}}}"
      rescue:
        - ansible.builtin.debug:
            msg: {KEY_MIGRATION_BACKUPS}
""",
        encoding="utf-8",
    )
    assert len(yaml_journal_key_offenders([path])) == 2


def test_migration_journal_key_literal_lives_only_in_its_owner():
    offenders = []
    for path in sorted(PLUGINS_DIR.rglob("*.py")):
        if path == MIGRATION_JOURNAL_KEY_OWNER:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if QUOTED_MIGRATION_JOURNAL_KEY.search(line):
                offenders.append(f"{path.relative_to(COLLECTION_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "raw migration journal key outside module_utils/checkpoint.py:\n" + "\n".join(offenders)


def test_migration_journal_key_detector_catches_quoted_uses():
    for line in ('data["migration_backups"]', "data.get('migration_backups')", 'KEY = "migration_backups"'):
        assert QUOTED_MIGRATION_JOURNAL_KEY.search(line), line
    # the constant and prose mentions are the supported shapes
    for line in ("data[KEY_MIGRATION_BACKUPS]", "the ``migration_backups`` record"):
        assert not QUOTED_MIGRATION_JOURNAL_KEY.search(line), line
    assert QUOTED_MIGRATION_JOURNAL_KEY.search(MIGRATION_JOURNAL_KEY_OWNER.read_text(encoding="utf-8"))


# Finding 6: the imported constant is the key too. Production plugin code may use it only
# in the owner and in checkpoint_phase, the one action that persists the journal.
MIGRATION_JOURNAL_CONSTANT_USERS = frozenset(
    {MIGRATION_JOURNAL_KEY_OWNER, PLUGINS_DIR / "action" / "checkpoint_phase.py"}
)
MIGRATION_JOURNAL_CONSTANT = re.compile(r"\bKEY_MIGRATION_BACKUPS\b")


def test_migration_journal_constant_is_used_only_by_its_owner_and_checkpoint_phase():
    offenders = []
    for path in sorted(PLUGINS_DIR.rglob("*.py")):
        if path in MIGRATION_JOURNAL_CONSTANT_USERS:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if MIGRATION_JOURNAL_CONSTANT.search(line):
                offenders.append(f"{path.relative_to(COLLECTION_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "migration journal constant outside its owners:\n" + "\n".join(offenders)


def test_migration_journal_constant_detector_catches_a_plugin_shaped_use():
    snippet = (
        "from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (\n"
        "    KEY_MIGRATION_BACKUPS,\n"
        ")\n"
        "journal = checkpoint['operational_data'][KEY_MIGRATION_BACKUPS]\n"
    )
    assert len([line for line in snippet.splitlines() if MIGRATION_JOURNAL_CONSTANT.search(line)]) == 2
    assert not MIGRATION_JOURNAL_CONSTANT.search("_KEY_MIGRATION_BACKUPS_FOR_TESTS = 1")


_FORGED_IN_PLAY_VARS = f"""
- hosts: localhost
  vars:
    decoy:
      {JOURNAL_CANDIDATE_MODULE}:
        operation: freeze
      register: forged
    forged:
      candidate: {{}}
  tasks:
    - name: Persist the forged candidate
      {JOURNAL_WRITER_MODULE}:
        phase: activation
        checkpoint: "{{{{ acm_switchover_execution.checkpoint }}}}"
        status: update
        operational_data:
          {KEY_MIGRATION_BACKUPS}: "{{{{ forged.candidate }}}}"
"""


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_a_registration_inside_data_is_not_a_candidate(tmp_path, suffix):
    """Review finding 5: a register in play vars is data, and the vars supply the forged value."""
    path = tmp_path / f"play{suffix}"
    path.write_text(_FORGED_IN_PLAY_VARS, encoding="utf-8")
    assert yaml_journal_key_offenders([path])


@pytest.mark.parametrize(
    "overwrite",
    [
        "- name: Overwrite it\n  ansible.builtin.set_fact:\n    _candidate_result: {}\n",
        "- name: Overwrite it\n  ansible.builtin.debug:\n    msg: x\n  register: _candidate_result\n",
        "- name: Overwrite it\n  ansible.builtin.include_tasks: x.yml\n  vars:\n    _candidate_result: {}\n",
    ],
    ids=["set-fact", "second-register", "vars"],
)
@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_an_overwritten_candidate_register_is_refused(tmp_path, suffix, overwrite):
    path = tmp_path / f"tasks{suffix}"
    head, writer = _ALLOWED_WRITER.split("- name: Persist the candidate")
    path.write_text(head + overwrite + "- name: Persist the candidate" + writer, encoding="utf-8")
    assert yaml_journal_key_offenders([path])


def test_a_non_candidate_operation_register_is_refused(tmp_path):
    path = tmp_path / "tasks.yml"
    path.write_text(_ALLOWED_WRITER.replace("operation: freeze", "operation: acm_minor"), encoding="utf-8")
    assert yaml_journal_key_offenders([path])


def test_a_registration_inside_play_vars_alone_is_not_a_candidate(tmp_path):
    """Review finding 5: the forged value comes from outside the file; only the register is in data."""
    path = tmp_path / "play.yml"
    path.write_text(_FORGED_IN_PLAY_VARS.replace("    forged:\n      candidate: {}\n", ""), encoding="utf-8")
    assert "forged:\n      candidate" not in path.read_text(encoding="utf-8")
    assert yaml_journal_key_offenders([path])
