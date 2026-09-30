"""R4-04 migration-evidence pure model: Python CLI unit tests (plan Task 2)."""

import copy
import json
from pathlib import Path

import pytest

from lib.migration_child_evidence import (
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
from lib.migration_evidence import (
    ACM_MINOR_CONTRACTS,
    PINNED_CONTROLLER_SHAS,
    SCHEDULE_TOKENS,
    MigrationEvidenceError,
    controller_contract_for_acm_minor,
    normalize_backup_evidence,
    predict_correlated_backup,
    predict_latest_backup,
    select_correlated_evidence,
    select_latest_evidence,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "r4_04_migration_evidence_vectors.json"
CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]
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


def test_every_public_function_has_vectors():
    assert {case["function"] for case in CASES} == set(FUNCTIONS)


def test_error_is_a_value_error_with_a_stable_code():
    with pytest.raises(ValueError) as excinfo:
        controller_contract_for_acm_minor("2.99")
    assert isinstance(excinfo.value, MigrationEvidenceError)
    assert excinfo.value.code == "unknown_acm_minor"


def test_contract_matrix_is_the_pinned_six_minors():
    assert ACM_MINOR_CONTRACTS == {
        "2.12": "legacy_2_12_2_16",
        "2.13": "legacy_2_12_2_16",
        "2.14": "legacy_2_12_2_16",
        "2.15": "legacy_2_12_2_16",
        "2.16": "legacy_2_12_2_16",
        "2.17": "active_2_17",
    }
    assert set(PINNED_CONTROLLER_SHAS) == set(ACM_MINOR_CONTRACTS)
    assert all(len(sha) == 40 for sha in PINNED_CONTROLLER_SHAS.values())


def test_schedule_tokens_carry_no_trailing_hyphen():
    assert SCHEDULE_TOKENS["ManagedClusters"] == "acm-managed-clusters-schedule"
    assert SCHEDULE_TOKENS["CredentialsHive"] == "acm-credentials-hive-schedule"
    assert not any(token.endswith("-") for token in SCHEDULE_TOKENS.values())


def test_predictions_return_the_inventory_object_itself():
    newest = _backup("acm-managed-clusters-schedule-20240101120000")
    assert predict_latest_backup([newest], "ManagedClusters")[1] is newest
    generic = _backup("acm-resources-generic-schedule-20240101120000")
    assert (
        predict_correlated_backup([generic], "acm-resources-schedule-20240101120000", "ResourcesGeneric")[1] is generic
    )


def test_predictions_do_not_mutate_the_inventory():
    inventory = [
        _backup("acm-managed-clusters-schedule-20240101110000", start="2024-01-01T11:00:00Z"),
        _backup("acm-managed-clusters-schedule-20240101120000"),
    ]
    snapshot = copy.deepcopy(inventory)
    predict_latest_backup(inventory, "ManagedClusters")
    predict_correlated_backup(inventory, "acm-resources-schedule-20240101120000", "ResourcesGeneric")
    assert inventory == snapshot


def test_projection_is_exactly_seven_fields():
    projection = normalize_backup_evidence(_backup("acm-managed-clusters-schedule-20240101120000"), NS)
    assert set(projection) == {"namespace", "name", "uid", "phase", "completed_at", "errors", "warnings"}


def test_selected_evidence_is_detached_from_the_inventory():
    newest = _backup("acm-managed-clusters-schedule-20240101120000")
    decision, evidence = select_latest_evidence([newest], "ManagedClusters", NS)
    assert decision == "selected" and evidence is not newest
    evidence["name"] = "changed"
    assert newest["metadata"]["name"] == "acm-managed-clusters-schedule-20240101120000"
    generic = _backup("acm-resources-generic-schedule-20240101120000")
    decision, evidence = select_correlated_evidence(
        [generic], "acm-resources-schedule-20240101120000", "ResourcesGeneric", NS
    )
    assert decision == "selected" and evidence is not generic and evidence["uid"] == generic["metadata"]["uid"]
