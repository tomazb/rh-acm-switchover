"""Parity contract: R4-04 migration-evidence pure model (plan Task 2).

lib/migration_evidence.py and the collection's module_utils/migration_evidence.py
share no runtime code, so equality is proven here, executably: every shared vector
is fed to both modules and must produce the same result or the same error code.
"""

import json
from pathlib import Path

import pytest

import lib.migration_evidence as py_evidence
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_evidence as col_evidence

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]


def _run(module, case):
    try:
        result = getattr(module, case["function"])(**case["input"])
    except module.MigrationEvidenceError as exc:
        return {"error_code": exc.code}
    if isinstance(result, tuple):
        result = list(result)
    return {"result": result}


def test_fixture_format_is_pinned():
    assert FIXTURE["fixture_format"] == 1


@pytest.mark.parametrize("module", [py_evidence, col_evidence], ids=["python", "collection"])
def test_fixture_lane_table_is_the_pinned_contract_matrix(module):
    assert set(FIXTURE["lanes"]) == set(module.PINNED_CONTROLLER_SHAS) == set(module.ACM_MINOR_CONTRACTS)
    for minor, lane in FIXTURE["lanes"].items():
        assert lane["controller_sha"] == module.PINNED_CONTROLLER_SHAS[minor]
        assert lane["controller_contract"] == module.controller_contract_for_acm_minor(minor)


def test_shared_constants_are_equal():
    assert py_evidence.ACM_MINOR_CONTRACTS == col_evidence.ACM_MINOR_CONTRACTS
    assert py_evidence.PINNED_CONTROLLER_SHAS == col_evidence.PINNED_CONTROLLER_SHAS
    assert py_evidence.SCHEDULE_TOKENS == col_evidence.SCHEDULE_TOKENS


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_case_is_well_formed(case):
    assert set(case) == {"id", "lanes", "source_refs", "function", "input", "expect", "r4_stricter"}
    assert set(case["lanes"]) <= set(FIXTURE["lanes"])
    assert isinstance(case["r4_stricter"], bool)
    assert len(case["expect"]) == 1 and set(case["expect"]) <= {"result", "error_code"}
    for ref in case["source_refs"]:
        assert ref.split(":", 1)[0] in case["lanes"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_both_modules_agree_with_the_vector(case):
    python_outcome = _run(py_evidence, case)
    collection_outcome = _run(col_evidence, case)
    assert python_outcome == case["expect"]
    assert collection_outcome == python_outcome
