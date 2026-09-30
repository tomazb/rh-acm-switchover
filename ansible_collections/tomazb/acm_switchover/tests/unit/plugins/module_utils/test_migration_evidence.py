"""R4-04 migration-evidence pure model: collection unit tests (plan Task 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_child_evidence import (
    acm_phase_accepts,
    freeze_one_shot_backups,
    generated_child_name,
    is_owned_by,
    one_shot_completion,
    one_shot_required_predictions,
    passive_patch_cohort,
    passive_patch_completion,
    predict_one_shot_child_names,
    validate_velero_child,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    ACM_MINOR_CONTRACTS,
    SCHEDULE_TOKENS,
    MigrationEvidenceError,
    controller_contract_for_acm_minor,
    normalize_backup_evidence,
    predict_correlated_backup,
    predict_latest_backup,
    select_correlated_evidence,
    select_latest_evidence,
)

# The shared vectors live in the repository's root test fixtures; outside a
# repository checkout only the module-specific cases below run.
FIXTURE_PATH = Path(__file__).resolve().parents[7] / "tests" / "fixtures" / "r4_04_migration_evidence_vectors.json"
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"] if FIXTURE_PATH.is_file() else []
FUNCTIONS = {
    "controller_contract_for_acm_minor": controller_contract_for_acm_minor,
    "normalize_backup_evidence": normalize_backup_evidence,
    "predict_latest_backup": predict_latest_backup,
    "predict_correlated_backup": predict_correlated_backup,
    "select_latest_evidence": select_latest_evidence,
    "select_correlated_evidence": select_correlated_evidence,
    "acm_phase_accepts": acm_phase_accepts,
    "freeze_one_shot_backups": freeze_one_shot_backups,
    "generated_child_name": generated_child_name,
    "is_owned_by": is_owned_by,
    "one_shot_completion": one_shot_completion,
    "one_shot_required_predictions": one_shot_required_predictions,
    "passive_patch_cohort": passive_patch_cohort,
    "passive_patch_completion": passive_patch_completion,
    "predict_one_shot_child_names": predict_one_shot_child_names,
    "validate_velero_child": validate_velero_child,
}
NS = "open-cluster-management-backup"


def _backup(name, start="2024-01-01T12:00:00Z", phase="Completed"):
    return {
        "metadata": {"name": name, "uid": "uid-" + name, "namespace": NS},
        "status": {"phase": phase, "startTimestamp": start, "completionTimestamp": "2024-01-01T12:05:00Z"},
    }


@pytest.mark.skipif(not CASES, reason="shared vector fixture is only available in a repository checkout")
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_vector(case):
    function = FUNCTIONS[case["function"]]
    if "error_code" in case["expect"]:
        with pytest.raises(MigrationEvidenceError) as excinfo:
            function(**case["input"])
        assert excinfo.value.code == case["expect"]["error_code"]
    else:
        result = function(**case["input"])
        assert (list(result) if isinstance(result, tuple) else result) == case["expect"]["result"]


def test_contract_matrix():
    assert controller_contract_for_acm_minor("2.16") == "legacy_2_12_2_16"
    assert controller_contract_for_acm_minor("2.17") == "active_2_17"
    assert len(ACM_MINOR_CONTRACTS) == 6
    with pytest.raises(MigrationEvidenceError) as excinfo:
        controller_contract_for_acm_minor("2.18")
    assert excinfo.value.code == "unknown_acm_minor"


def test_error_is_a_value_error():
    assert issubclass(MigrationEvidenceError, ValueError)


def test_schedule_tokens_carry_no_trailing_hyphen():
    assert not any(token.endswith("-") for token in SCHEDULE_TOKENS.values())


def test_latest_tie_at_maximum_blocks():
    inventory = [
        _backup("acm-managed-clusters-schedule-20240101120000"),
        _backup("acm-managed-clusters-schedule-20240101120001"),
    ]
    with pytest.raises(MigrationEvidenceError) as excinfo:
        predict_latest_backup(inventory, "ManagedClusters")
    assert excinfo.value.code == "latest_ambiguous"


def test_latest_returns_the_inventory_object_itself():
    newest = _backup("acm-managed-clusters-schedule-20240101120000")
    older = _backup("acm-managed-clusters-schedule-20240101110000", start="2024-01-01T11:00:00Z")
    decision, raw = predict_latest_backup([older, newest], "ManagedClusters")
    assert decision == "selected" and raw is newest


def test_correlated_exact_first_and_ambiguous_fallback():
    exact = _backup("acm-resources-generic-schedule-20240101120000", phase="Failed")
    near = _backup("acm-resources-generic-schedule-20240101120005", start="2024-01-01T12:00:05Z")
    near2 = _backup("acm-resources-generic-schedule-20240101120010", start="2024-01-01T12:00:10Z")
    source = "acm-resources-schedule-20240101120000"
    assert predict_correlated_backup([near, exact], source, "ResourcesGeneric") == ("selected", exact)
    with pytest.raises(MigrationEvidenceError) as excinfo:
        predict_correlated_backup([near, near2], source, "ResourcesGeneric")
    assert excinfo.value.code == "correlated_ambiguous"


def test_counter_normalization():
    raw = _backup("acm-managed-clusters-schedule-20240101120000")
    assert normalize_backup_evidence(raw, NS)["errors"] == 0
    raw["status"]["warnings"] = True
    with pytest.raises(MigrationEvidenceError) as excinfo:
        normalize_backup_evidence(raw, NS)
    assert excinfo.value.code == "malformed_backup_counter"


def test_selected_evidence_is_detached_from_the_inventory():
    newest = _backup("acm-managed-clusters-schedule-20240101120000")
    decision, evidence = select_latest_evidence([newest], "ManagedClusters", NS)
    assert decision == "selected" and evidence is not newest
    assert set(evidence) == {"namespace", "name", "uid", "phase", "completed_at", "errors", "warnings"}


def test_fractional_name_suffix_reaches_the_fallback():
    near = _backup("acm-resources-generic-schedule-fallback", start="2024-01-01T12:00:30.5Z")
    source = "acm-resources-schedule-20240101120000.5"
    assert predict_correlated_backup([near], source, "ResourcesGeneric") == ("selected", near)
