"""Static guardrails banning hardcoded ACM API-group literals outside lib/constants.py.

Thermos R2-H2: MANAGED_CLUSTER_API_GROUP exists so the API group is defined
once; workflow modules must import it (or its CLUSTER_BACKUP_* companions)
instead of repeating the literal.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = REPO_ROOT / "modules"
BANNED_LITERAL = "cluster.open-cluster-management.io"


def test_no_hardcoded_managed_cluster_api_group_in_modules():
    """modules/*.py must route the ACM cluster API group through lib.constants."""
    violations = []
    for path in sorted(MODULES_DIR.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if BANNED_LITERAL in line:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Hardcoded ACM API-group literals found; import MANAGED_CLUSTER_API_GROUP / "
        "CLUSTER_BACKUP_* constants from lib.constants instead:\n  " + "\n  ".join(violations)
    )
