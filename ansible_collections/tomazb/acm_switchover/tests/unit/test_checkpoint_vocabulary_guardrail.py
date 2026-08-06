"""Guardrail: checkpoint key vocabulary lives in module_utils, not role YAML.

Mirror of the Python side's tests/test_run_record_guardrails.py: roles consume
the flattened `facts` dict returned by checkpoint_phase; raw operational_data
read chains in YAML bypass the facade and are forbidden (issue #214).
"""

import pathlib

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"

FORBIDDEN_PATTERNS = (
    ".get('operational_data'",
    '.get("operational_data"',
)


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
