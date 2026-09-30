"""Parity contract: R4-04 migration-evidence pure model (plan Task 2).

lib/migration_evidence.py and lib/migration_child_evidence.py share no runtime
code with their collection mirrors in module_utils, so equality is proven here,
executably: every shared vector is fed to both form factors and must produce the
same result or the same error code.
"""

import importlib.util
import json
from pathlib import Path

import pytest

import lib.migration_child_evidence as py_child
import lib.migration_evidence as py_evidence
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_child_evidence as col_child
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_evidence as col_evidence

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json"
GENERATOR_PATH = Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors_gen.py"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
PYTHON = (py_evidence, py_child)
COLLECTION = (col_evidence, col_child)


def _public_functions(module):
    return {
        name
        for name, value in vars(module).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__
        and not isinstance(value, type)
    }


def _run(form_factor, case):
    base, child = form_factor
    function = getattr(base, case["function"], None) or getattr(child, case["function"])
    try:
        result = function(**case["input"])
    except base.MigrationEvidenceError as exc:
        return {"error_code": exc.code}
    if isinstance(result, tuple):
        result = list(result)
    return {"result": result}


def test_fixture_format_is_pinned():
    assert FIXTURE["fixture_format"] == 1


def test_fixture_is_the_generator_output():
    spec = importlib.util.spec_from_file_location("r4_04_migration_evidence_vectors_gen", GENERATOR_PATH)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert FIXTURE_PATH.read_bytes() == generator.build().encode("utf-8")


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
    assert py_child.STATUS_NAME_FIELDS == col_child.STATUS_NAME_FIELDS
    assert py_child.VELERO_RESTORE_LISTS == col_child.VELERO_RESTORE_LISTS


def test_child_module_raises_the_base_error_class():
    assert py_child.MigrationEvidenceError is py_evidence.MigrationEvidenceError
    assert col_child.MigrationEvidenceError is col_evidence.MigrationEvidenceError


@pytest.mark.parametrize("form_factor", [PYTHON, COLLECTION], ids=["python", "collection"])
def test_every_public_function_has_vectors(form_factor):
    base, child = form_factor
    assert not _public_functions(base) & _public_functions(child)
    assert _public_functions(base) | _public_functions(child) == {case["function"] for case in CASES}


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
    python_outcome = _run(PYTHON, case)
    collection_outcome = _run(COLLECTION, case)
    assert python_outcome == case["expect"]
    assert collection_outcome == python_outcome
