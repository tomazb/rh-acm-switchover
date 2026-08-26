"""Cross-form-factor guardrails for the R3-02 fail-closed decisions."""

import re
from pathlib import Path

import yaml

from modules.activation import AUTO_IMPORT_VERIFY_ERROR

REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_ACTIVATION_TASKS = (
    REPO_ROOT
    / "ansible_collections"
    / "tomazb"
    / "acm_switchover"
    / "roles"
    / "activation"
    / "tasks"
    / "apply_immediate_import.yml"
)
PARITY_MATRIX = REPO_ROOT / "docs" / "ansible-collection" / "parity-matrix.md"


def test_activation_verification_failure_message_matches_collection():
    """Python and Collection must expose the same stable verification failure."""
    tasks = yaml.safe_load(COLLECTION_ACTIVATION_TASKS.read_text())
    failure_task = next(
        task
        for task in tasks
        if task.get("name") == "Fail when autoImportStrategy cannot be verified"
    )

    assert failure_task["ansible.builtin.fail"]["msg"] == AUTO_IMPORT_VERIFY_ERROR


def test_r3_02_capabilities_remain_dual_supported():
    """R3-02 must not silently change the support status of affected capabilities."""
    matrix = PARITY_MATRIX.read_text()

    for capability in ("preflight validation", "primary prep", "activation"):
        row = rf"^\|\s*{re.escape(capability)}\s*\|\s*dual-supported\s*\|"
        assert re.search(row, matrix, flags=re.MULTILINE), (
            f"{capability} must remain dual-supported"
        )
