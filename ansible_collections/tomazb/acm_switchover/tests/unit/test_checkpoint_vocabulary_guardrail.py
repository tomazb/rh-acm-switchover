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

COLLECTION_ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLES_DIR = COLLECTION_ROOT / "roles"
PLAYBOOKS_DIR = COLLECTION_ROOT / "playbooks"

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
