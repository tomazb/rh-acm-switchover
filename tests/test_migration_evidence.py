"""R4-04 migration-evidence pure model: Python CLI unit tests (plan Task 2)."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from lib.migration_child_evidence import (
    acm_phase_accepts,
    freeze_one_shot_backups,
    generated_child_name,
    is_owned_by,
    normalize_child_list,
    one_shot_completion,
    one_shot_required_predictions,
    passive_patch_cohort,
    passive_patch_completion,
    predict_one_shot_child_names,
    predict_passive_patch_child_names,
    validate_velero_child,
)
from lib.migration_evidence import (
    ACM_MINOR_CONTRACTS,
    PINNED_CONTROLLER_SHAS,
    SCHEDULE_TOKENS,
    MigrationEvidenceError,
    controller_contract_for_acm_minor,
    go_normalize,
    normalize_backup_evidence,
    predict_correlated_backup,
    predict_latest_backup,
    rfc3339_ns,
    select_correlated_evidence,
    select_latest_evidence,
    validate_backup_projection,
)
from lib.migration_journal import (
    canonical_restore_projection,
    restore_spec_fingerprint,
    validate_cleanup_transition,
    validate_journal_transition,
    validate_migration_journal,
    validate_repair,
    validate_waiver,
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
    "predict_passive_patch_child_names": predict_passive_patch_child_names,
    "validate_velero_child": validate_velero_child,
    "normalize_child_list": normalize_child_list,
    "canonical_restore_projection": canonical_restore_projection,
    "restore_spec_fingerprint": restore_spec_fingerprint,
    "validate_migration_journal": validate_migration_journal,
    "validate_cleanup_transition": validate_cleanup_transition,
    "validate_journal_transition": validate_journal_transition,
    "validate_waiver": validate_waiver,
    "validate_repair": validate_repair,
    "rfc3339_ns": rfc3339_ns,
    "go_normalize": go_normalize,
    "validate_backup_projection": validate_backup_projection,
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


def _case_input(case_id):
    return copy.deepcopy(next(case for case in CASES if case["id"] == case_id)["input"])


def test_fingerprint_escapes_non_ascii_like_json_dumps():
    restore = {
        "activation_method": "full",
        "mutation_kind": "full_restore",
        "backup_fields": {"veleroManagedClustersBackupName": "acm-managed-clusters-schedule-\u010d-20240101120000"},
        "cleanup_before_restore": "CleanupRestored",
    }
    expected = hashlib.sha256(
        b'{"activation_method":"full","backup_fields":{"veleroManagedClustersBackupName":'
        b'"acm-managed-clusters-schedule-\\u010d-20240101120000"},"cleanup_before_restore":"CleanupRestored",'
        b'"mutation_kind":"full_restore"}'
    ).hexdigest()
    assert restore_spec_fingerprint({"restore": restore}) == expected


def test_validated_journal_is_detached_from_the_candidate():
    candidate = _case_input("journal-completed-full-restore-2.17")["candidate"]
    validated = validate_migration_journal(candidate)
    assert validated == candidate and validated is not candidate
    validated["restore"]["velero_restores"]["credentials"].clear()
    assert len(candidate["restore"]["velero_restores"]["credentials"]) == 2


def test_repair_never_synthesizes_absence_or_completion():
    inputs = _case_input("repair-preserves-accepted-delete")
    repaired = validate_repair(**inputs)
    assert repaired["state"] == "repaired"
    assert repaired["absence_verified_at"] is None and repaired["completed_at"] is None
    assert repaired["delete_accepted_at"] == inputs["journal"]["cleanup"]["delete_accepted_at"]
    assert repaired["recovery"] == inputs["journal"]["cleanup"]["recovery"]


# --- scoped generic reclassification (plan Task 2, AC-27) ------------------------------------

_REPO = Path(__file__).resolve().parent.parent
_EVIDENCE_MODULES = {"migration_evidence.py", "migration_child_evidence.py", "migration_journal.py"}
_GENERIC_CATEGORY = re.compile(r"[\"'](?:activation_)?resources_generic[\"']")


def test_the_coarse_backup_classifiers_are_not_widened():
    from lib.constants import ACM_BACKUP_NAME_RE, ACM_BACKUP_SCHEDULE_TYPES

    assert ACM_BACKUP_SCHEDULE_TYPES == frozenset({"managedClusters", "credentials", "resources"})
    assert ACM_BACKUP_NAME_RE.pattern == r"^acm-(managed-clusters|credentials|resources)-"
    # A generic Backup stays coarsely a `resources` Backup outside the evidence model.
    match = ACM_BACKUP_NAME_RE.match("acm-resources-generic-schedule-20240101120000")
    assert match is not None and match.group(1) == "resources"


def test_generic_categories_are_named_only_by_the_evidence_model():
    roots = [_REPO / name for name in ("lib", "modules", "scripts")]
    roots += [_REPO / name for name in ("acm_switchover.py", "show_state.py", "check_rbac.py")]
    roots += [_REPO / "ansible_collections/tomazb/acm_switchover" / name for name in ("plugins", "roles", "playbooks")]
    offenders = []
    for root in roots:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.suffix in {".py", ".yml", ".yaml"}]
        for path in files:
            if path.name in _EVIDENCE_MODULES and path.parent.name in {"lib", "module_utils"}:
                continue
            if _GENERIC_CATEGORY.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(_REPO)))
    assert not offenders, f"generic Backup categories named outside the evidence model: {offenders}"


def test_the_evidence_model_reclassifies_only_the_generic_backup():
    by_id = {case["id"]: case for case in CASES}
    frozen = by_id["freeze-full-legacy"]["expect"]["result"]
    assert frozen["resources"]["name"] == "acm-resources-schedule-20240101120000"
    assert frozen["resources_generic"]["name"] == "acm-resources-generic-schedule-20240101120000"
