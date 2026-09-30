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


def test_migration_journal_key_is_never_named_in_role_or_playbook_yaml():
    """No YAML reads or writes the journal key; the facade is checkpoint_phase status: update.

    A future journal writer is a checkpoint_phase `status: update` task and needs an
    explicit, narrow allowance here rather than a silent exemption.
    """
    offenders = [
        str(path.relative_to(COLLECTION_ROOT))
        for path in _yaml_files()
        if KEY_MIGRATION_BACKUPS in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"The '{KEY_MIGRATION_BACKUPS}' operational_data key is owned by module_utils/checkpoint.py "
        f"and must not be named in YAML. Offenders: {offenders}"
    )


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
