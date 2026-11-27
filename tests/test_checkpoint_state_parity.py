"""Parity contract: cross-phase state key names shared between runtimes (issue #214).

The Python CLI persists cross-phase facts through the RunRecord facade
(lib/run_record.py); the collection persists them in checkpoint
operational_data (module_utils/checkpoint.py). Shared names are pinned equal;
intentional divergences are pinned explicitly so silent drift is impossible.
"""

import importlib

import pytest

import lib.constants as py_constants
import lib.run_record as py_run_record
import lib.teardown_record as py_teardown_record
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import checkpoint as ansible_checkpoint


def test_shared_key_names_match():
    assert ansible_checkpoint.KEY_RESUME_SUMMARY == py_constants.STATE_KEY_RESUME_SUMMARY
    assert ansible_checkpoint.KEY_RESUME_START_PHASE == py_constants.RESUME_START_PHASE_KEY
    assert ansible_checkpoint.KEY_EXPECTED_MANAGED_CLUSTER_NAMES == py_constants.EXPECTED_MANAGED_CLUSTER_NAMES_KEY
    assert ansible_checkpoint.KEY_EXPECTED_MANAGED_CLUSTER_COUNT == py_constants.EXPECTED_MANAGED_CLUSTER_COUNT_KEY
    assert ansible_checkpoint.KEY_PRIMARY_HAS_OBSERVABILITY == py_run_record._KEY_PRIMARY_HAS_OBS
    assert ansible_checkpoint.KEY_SECONDARY_HAS_OBSERVABILITY == py_run_record._KEY_SECONDARY_HAS_OBS
    assert ansible_checkpoint.KEY_SAVED_BACKUP_SCHEDULE == py_run_record._KEY_SAVED_BACKUP_SCHEDULE
    assert ansible_checkpoint.KEY_BACKUP_SCHEDULE_ENABLED_AT == py_run_record._KEY_BACKUP_WATCH_STARTED_AT


def test_intentional_divergences_are_pinned():
    """auto-import obligation: Python records auto_import_strategy_set (state
    file, always on); the collection records auto_import_strategy_changed
    (checkpoint) plus the cluster marker. Renaming either side without
    updating this contract is a parity break."""
    assert py_run_record._KEY_AUTO_IMPORT_SET == "auto_import_strategy_set"
    assert ansible_checkpoint.KEY_AUTO_IMPORT_STRATEGY_CHANGED == "auto_import_strategy_changed"


# --- R4-03 decommission teardown records (plan §10.2) -----------------------------
#
# The two implementations share no runtime code, so equality of the rules is proven
# here, executably: the same stored payloads must be classified identically by
# lib/teardown_record.py and by the collection checkpoint reader.

# Closed field sets. Both sides keep them private to their own validator; a divergence
# would let one side accept a nested shape the other rejects, which the shared vector
# set (whose members are all top-level completion evidence) cannot catch.
TEARDOWN_FIELD_SET_NAMES = (
    "_RECORD_FIELDS",
    "_ABSENCE_PROOF_FIELDS",
    "_OPERATOR_DEPLOYMENT_FIELDS",
    "_CSV_FIELDS",
    "_IDENTITY_UNAVAILABLE_FIELDS",
)


def test_teardown_record_key_is_the_same_string_on_both_sides():
    assert ansible_checkpoint.KEY_DECOMMISSION_TEARDOWN_RECORDS == py_run_record._KEY_TEARDOWN_RECORDS


def test_migration_journal_key_is_the_same_string_on_both_sides():
    assert ansible_checkpoint.KEY_MIGRATION_BACKUPS == py_run_record._KEY_MIGRATION_BACKUPS


@pytest.mark.parametrize(
    "api_version, kind, namespace, name",
    [
        ("observability.open-cluster-management.io/v1beta2", "MultiClusterObservability", None, "observability"),
        ("operator.open-cluster-management.io/v1", "MultiClusterHub", "open-cluster-management", "multiclusterhub"),
        ("cluster.open-cluster-management.io/v1", "ManagedCluster", "", "spoke-1"),
        ("v1", "Namespace", None, "open-cluster-management"),
    ],
)
def test_both_teardown_key_builders_produce_the_same_key(api_version, kind, namespace, name):
    assert py_teardown_record.teardown_key(api_version, kind, namespace, name) == ansible_checkpoint.teardown_key(
        api_version, kind, namespace, name
    )


def test_teardown_record_field_sets_are_mirrored():
    mismatches = []
    for name in TEARDOWN_FIELD_SET_NAMES:
        py_val = getattr(py_teardown_record, name, None)
        ans_val = getattr(ansible_checkpoint, name, None)
        if py_val is None or ans_val is None:
            mismatches.append(f"{name} is missing on one side (Python={py_val!r}, Ansible={ans_val!r})")
        elif set(py_val) != set(ans_val):
            mismatches.append(f"{name}={sorted(py_val)} (Python) != {sorted(ans_val)} (Ansible)")
    assert not mismatches, "Teardown record shape drift detected:\n  " + "\n  ".join(mismatches)


def _collection_checkpoint():
    """The collection module, imported here rather than at module scope, so root
    tests/ stays import-safe without ansible-core (the brief's rule; this file
    already imports it above for the pre-existing key-name assertions)."""
    return importlib.import_module("ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint")


def _assert_rejected_by_both(key, stored):
    checkpoint = _collection_checkpoint()
    with pytest.raises(py_teardown_record.MalformedTeardownRecord):
        py_teardown_record.validate_stored(key, stored)
    with pytest.raises(checkpoint.MalformedTeardownRecord):
        checkpoint.teardown_record({"operational_data": {"decommission_teardown_records": {key: stored}}}, key)


def test_every_malformed_completion_record_is_rejected_by_both_implementations():
    from tests.test_teardown_record import _VECTOR_KEYS, MALFORMED_COMPLETION_RECORDS, MCO_KEY

    for vector_id, stored in MALFORMED_COMPLETION_RECORDS:
        key = _VECTOR_KEYS.get(vector_id, MCO_KEY)
        _assert_rejected_by_both(key, stored)


def _nested_identity_vectors():
    """Malformed payloads the shared completion-evidence vector set does not cover.

    The §10.2.2/§10.2.3 members are generated from the two authoritative tuples in
    tests/test_teardown_record.py — the very objects that file's own parametrize
    consumes — so this fixture cannot drift from the Python side, and every one of
    those payloads is checked against BOTH implementations rather than only Python.
    The remaining members are the §10.2.4 record-level rules and the B1 decisions no
    vector encodes. Writer-side rules (identity immutability across two writes) are
    read-only here and are covered by each side's own unit tests.
    """
    from tests.test_teardown_record import (
        MALFORMED_OPERATOR_DEPLOYMENTS,
        MALFORMED_OPERATOR_IDENTITY_UNAVAILABLE,
        MCH_KEY,
        MCO_KEY,
        OBSERVED_AT,
        _identity,
        _unavailable,
    )

    acm_namespace_key = "v1/Namespace//open-cluster-management"

    def _mch(**overrides):
        stored = {"expected_uid": "uid-mch", "phase": "delete_started"}
        stored.update(overrides)
        return stored

    vectors = [
        # --- B1 decisions and §10.2.4 record rules that no vector list encodes ------
        (
            "unknown_top_level_field",
            MCO_KEY,
            {"expected_uid": "u", "phase": "delete_started", "surprise": 1},
        ),
        # A stored null is a third representation of "absent" that neither side may
        # accept. The payloads are identity fields, not evidence fields, because the
        # evidence fields are additionally type-checked: `resource_versions: None`
        # fails for a second, unrelated reason and so proves nothing about this rule.
        (
            "stored_null_identity_field",
            MCH_KEY,
            {
                "expected_uid": "uid-mch",
                "phase": "delete_started",
                "operator_deployment": None,
                "operator_identity_unavailable": _unavailable(),
            },
        ),
        (
            "stored_null_identity_field_on_a_non_identity_kind",
            MCO_KEY,
            {"expected_uid": "u", "phase": "delete_started", "operator_deployment": None},
        ),
        (
            "drain_namespace_proof_naming_another_familys_namespace",
            MCO_KEY,
            {
                "expected_uid": "u",
                "phase": "completed",
                "observed_at": OBSERVED_AT,
                "resource_versions": {},
                "absence_proofs": {
                    "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY},
                    "drain_namespace": {"proof_type": "namespace_absent", "resource_key": acm_namespace_key},
                },
            },
        ),
        (
            "both_identity_outcomes",
            MCH_KEY,
            _mch(operator_deployment=_identity(), operator_identity_unavailable=_unavailable()),
        ),
        ("neither_identity_outcome", MCH_KEY, _mch()),
        (
            "non_mch_record_carrying_an_identity",
            MCO_KEY,
            {"expected_uid": "u", "phase": "delete_started", "operator_deployment": _identity()},
        ),
    ]

    # --- the authoritative §10.2.2 / §10.2.3 payloads, member for member ------------
    vectors.extend(
        (f"malformed_operator_deployment_{index}", MCH_KEY, _mch(operator_deployment=identity))
        for index, identity in enumerate(MALFORMED_OPERATOR_DEPLOYMENTS)
    )
    vectors.extend(
        (f"malformed_operator_identity_unavailable_{index}", MCH_KEY, _mch(operator_identity_unavailable=unavailable))
        for index, unavailable in enumerate(MALFORMED_OPERATOR_IDENTITY_UNAVAILABLE)
    )
    return vectors


def test_every_nested_identity_payload_is_rejected_by_both_implementations():
    for _vector_id, key, stored in _nested_identity_vectors():
        _assert_rejected_by_both(key, stored)


def test_valid_records_are_accepted_by_both_implementations():
    """The mirrored rules must also agree on what is VALID: a validator that rejects
    everything would pass every malformed-vector test above."""
    from tests.test_teardown_record import MCH_KEY, MCO_KEY, OBSERVED_AT, _identity

    checkpoint = _collection_checkpoint()
    valid = [
        (MCO_KEY, {"expected_uid": "u", "phase": "delete_started"}),
        (
            MCO_KEY,
            {
                "expected_uid": "u",
                "phase": "completed",
                "observed_at": OBSERVED_AT,
                "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219"},
                "absence_proofs": {"target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY}},
            },
        ),
        (
            MCH_KEY,
            {
                "expected_uid": "uid-mch",
                "phase": "completed",
                "observed_at": OBSERVED_AT,
                "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219", "operator_deployment": "8"},
                "absence_proofs": {"target_cr": {"proof_type": "object_absent", "resource_key": MCH_KEY}},
                "operator_deployment": _identity(),
            },
        ),
    ]
    for key, stored in valid:
        py_teardown_record.validate_stored(key, stored)
        assert (
            checkpoint.teardown_record(
                {"operational_data": {"decommission_teardown_records": {key: stored}}},
                key,
            )
            == stored
        )


@pytest.mark.parametrize(
    ("include_key", "discovery_namespaces"),
    [
        (False, None),
        (True, {}),
        (True, {"primary": ["9-team"]}),
        (True, {"primary": ["argocd", "9-team"], "secondary": []}),
    ],
    ids=["absent", "empty-dict", "digit-leading", "multi-hub"],
)
def test_argocd_discovery_namespaces_accepts_same_valid_shapes(tmp_path, include_key, discovery_namespaces):
    from lib.argocd import trusted_application_namespaces
    from lib.argocd_register import ArgocdPauseRegister
    from lib.constants import STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES
    from lib.utils import StateManager

    state = StateManager(str(tmp_path / "switchover-state.json"))
    checkpoint = {"operational_data": {}}
    if include_key:
        state._set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, discovery_namespaces)
        checkpoint["operational_data"][ansible_checkpoint.KEY_ARGOCD_DISCOVERY_NAMESPACES] = discovery_namespaces

    py_stored = ArgocdPauseRegister(state)._store._get_discovery_namespaces_by_hub()
    col_validated = ansible_checkpoint.argocd_discovery_namespaces(checkpoint)
    col_facts = ansible_checkpoint.checkpoint_facts(checkpoint)[ansible_checkpoint.KEY_ARGOCD_DISCOVERY_NAMESPACES]

    assert py_stored == col_validated == col_facts
    for hub, namespaces in col_validated.items():
        expected = sorted(set(namespaces)) or None
        assert trusted_application_namespaces(namespaces) == expected


@pytest.mark.parametrize(
    "malformed_scope",
    [
        None,
        "not-a-mapping",
        ["9-team"],
        42,
        {1: ["9-team"]},
        {"primary": None},
        {"primary": "9-team"},
        {"primary": 42},
        {"primary": [""]},
        {"primary": [" "]},
        {"primary": [" 9-team "]},
        {"primary": ["9-team\n"]},
        {"primary": ["bad.ns"]},
        {"primary": ["-bad"]},
        {"primary": ["Bad"]},
        {"primary": ["9-team-"]},
        {"primary": [123]},
        {"primary": ["9-team", "bad.ns"]},
        {"primary": ["9-team"], "secondary": ["bad.ns"]},
    ],
)
@pytest.mark.parametrize("run_id", ["run-1", None])
def test_argocd_discovery_namespaces_rejects_same_malformed_shapes(tmp_path, malformed_scope, run_id):
    from unittest.mock import Mock

    from lib.argocd_register import ArgocdPauseRegister
    from lib.constants import STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, STATE_KEY_ARGOCD_RUN_ID
    from lib.exceptions import ValidationError
    from lib.utils import StateManager

    state = StateManager(str(tmp_path / "switchover-state.json"))
    if run_id is not None:
        state._set_config(STATE_KEY_ARGOCD_RUN_ID, run_id)
    state._set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, malformed_scope)
    primary = Mock()
    secondary = Mock()

    with pytest.raises(ValidationError):
        ArgocdPauseRegister(state).pause_hubs([(primary, "primary"), (secondary, "secondary")])
    assert primary.method_calls == []
    assert secondary.method_calls == []

    checkpoint = {"operational_data": {ansible_checkpoint.KEY_ARGOCD_DISCOVERY_NAMESPACES: malformed_scope}}
    with pytest.raises(ValueError):
        ansible_checkpoint.argocd_discovery_namespaces(checkpoint)
    with pytest.raises(ValueError):
        ansible_checkpoint.checkpoint_facts(checkpoint)
