"""Durable decommission teardown records in the collection checkpoint (R4-03 PR B, Task B2).

Mirror of tests/test_teardown_record.py expressed against the collection's own
checkpoint store, with the same fixture data and the same expected outcome.

The malformed vectors are declared here rather than imported from
tests/test_teardown_record.py: that module imports lib/, and the collection
lane must not import Python-CLI runtime code. Agreement between the two
implementations is proven where the two sides may legitimately meet --
tests/test_checkpoint_state_parity.py imports the authoritative vector list and
runs every member through this implementation as well.
"""

import copy
from typing import Any

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    KEY_DECOMMISSION_TEARDOWN_RECORDS,
    MalformedTeardownRecord,
    record_teardown_phase,
    split_resource_key,
    teardown_key,
    teardown_record,
    teardown_records,
)

MCO_KEY = teardown_key(
    "observability.open-cluster-management.io/v1beta2", "MultiClusterObservability", None, "observability"
)
MCH_KEY = teardown_key(
    "operator.open-cluster-management.io/v1", "MultiClusterHub", "open-cluster-management", "multiclusterhub"
)
CLUSTER_KEY = teardown_key("cluster.open-cluster-management.io/v1", "ManagedCluster", None, "spoke-1")

OBSERVABILITY_NS_KEY = "v1/Namespace//open-cluster-management-observability"
ACM_NS_KEY = "v1/Namespace//open-cluster-management"

OBSERVED_AT = "2026-09-04T00:00:00Z"

UNAVAILABLE_REASONS = (
    "csv_absent",
    "csv_ambiguous",
    "csv_not_succeeded",
    "csv_owned_crd_mismatch",
    "install_deployment_absent",
    "install_deployment_ambiguous",
    "deployment_read_failed",
    "deployment_identity_incomplete",
)


_UNSET = object()  # "the key was never written", which is not the same as a stored null


def _checkpoint(records=_UNSET):
    """A schema 2.0 checkpoint carrying `records` under the teardown key."""
    operational_data = {}
    if records is not _UNSET:
        operational_data[KEY_DECOMMISSION_TEARDOWN_RECORDS] = records
    return {"operational_data": operational_data}


def _cr_absent(key, proof_type="object_absent"):
    """The `target_cr` proof every completed record must carry (10.2.1c)."""
    return {"proof_type": proof_type, "resource_key": key}


def _ns_absent(namespace_key):
    return {"proof_type": "namespace_absent", "resource_key": namespace_key}


def _record_completed(checkpoint, key, **overrides):
    """Write a valid completed record, defaulting to the MCO namespace-present mode."""
    # Annotated because the literal mixes strings with mappings: without it the popped
    # expected_uid/phase infer as Collection[str] and fail the repository mypy gate.
    fields: dict[str, Any] = {
        "expected_uid": "u",
        "phase": "completed",
        "observed_at": OBSERVED_AT,
        "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219"},
        "absence_proofs": {"target_cr": _cr_absent(key)},
    }
    fields.update(overrides)
    expected_uid = fields.pop("expected_uid")
    phase = fields.pop("phase")
    record_teardown_phase(checkpoint, key, expected_uid, phase, **fields)


def _identity(**overrides):
    identity: dict[str, Any] = {
        "namespace": "open-cluster-management",
        "name": "multiclusterhub-operator",
        "uid": "dep-uid-1",
        "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
        "captured_at": "2026-09-04T10:09:00Z",
        "csv": {
            "namespace": "open-cluster-management",
            "name": "advanced-cluster-management.v2.13.0",
            "uid": "csv-uid-1",
            "owned_crd": "multiclusterhubs.operator.open-cluster-management.io",
        },
        "mch_teardown_key": MCH_KEY,
        "mch_expected_uid": "uid-mch",
    }
    identity.update(overrides)
    return identity


def _unavailable(reason="csv_absent"):
    return {
        "reason": reason,
        "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
        "captured_at": "2026-09-04T10:09:00Z",
        "evidence_summary": "no candidate CSV",
        "mch_teardown_key": MCH_KEY,
        "mch_expected_uid": "uid-mch",
    }


def _record_mch(checkpoint, **overrides):
    # A non-completed record carries NO completion evidence at all (10.2.1a).
    fields: dict[str, Any] = {"phase": "delete_started", "operator_deployment": _identity()}
    fields.update(overrides)
    phase = fields.pop("phase")
    record_teardown_phase(checkpoint, MCH_KEY, "uid-mch", phase, **fields)


# --- round trip -------------------------------------------------------------------


def test_key_is_stable_and_includes_an_empty_namespace_segment_when_cluster_scoped():
    assert MCO_KEY == ("observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability")


def test_record_round_trips_through_the_checkpoint():
    checkpoint = _checkpoint()
    record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")
    loaded = teardown_record(checkpoint, MCO_KEY)
    assert loaded["expected_uid"] == "uid-1"
    assert loaded["phase"] == "delete_started"
    # No completion evidence exists before `completed`, and absent is not empty.
    assert "observed_at" not in loaded
    assert "resource_versions" not in loaded
    assert "absence_proofs" not in loaded


def test_record_is_written_under_the_named_key_only():
    checkpoint = _checkpoint()
    record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")
    assert set(checkpoint["operational_data"]) == {KEY_DECOMMISSION_TEARDOWN_RECORDS}


def test_missing_operational_data_is_created_by_the_writer():
    checkpoint: dict = {}
    record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")
    assert teardown_record(checkpoint, MCO_KEY)["expected_uid"] == "uid-1"


def test_completed_record_round_trips_both_evidence_fields():
    checkpoint = _checkpoint()
    _record_completed(checkpoint, MCO_KEY, expected_uid="uid-1")
    loaded = teardown_record(checkpoint, MCO_KEY)
    assert loaded["observed_at"] == OBSERVED_AT
    assert loaded["resource_versions"] == {"drain_namespace": "88190", "drain_pods": "88219"}
    assert loaded["absence_proofs"] == {"target_cr": _cr_absent(MCO_KEY)}


def test_present_empty_resource_versions_is_not_the_same_as_absent():
    """10.2.1a: `{}` is valid evidence for an absence-only proof; absent is not."""
    checkpoint = _checkpoint()
    _record_completed(
        checkpoint,
        CLUSTER_KEY,
        resource_versions={},
        absence_proofs={"target_cr": _cr_absent(CLUSTER_KEY)},
    )
    loaded = teardown_record(checkpoint, CLUSTER_KEY)
    assert loaded["resource_versions"] == {}
    stored = checkpoint["operational_data"][KEY_DECOMMISSION_TEARDOWN_RECORDS][CLUSTER_KEY]
    assert stored["resource_versions"] == {}, "a present-and-empty mapping must be serialized"


def test_absent_completion_evidence_is_omitted_from_the_stored_mapping():
    checkpoint = _checkpoint()
    record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "drained")
    stored = checkpoint["operational_data"][KEY_DECOMMISSION_TEARDOWN_RECORDS][MCO_KEY]
    assert set(stored) == {"expected_uid", "phase"}


def test_the_writer_does_not_alias_caller_owned_mappings():
    checkpoint = _checkpoint()
    revisions = {"drain_namespace": "88190", "drain_pods": "88219"}
    proofs = {"target_cr": _cr_absent(MCO_KEY)}
    record_teardown_phase(
        checkpoint,
        MCO_KEY,
        "uid-1",
        "completed",
        observed_at=OBSERVED_AT,
        resource_versions=revisions,
        absence_proofs=proofs,
    )
    revisions["drain_pods"] = "tampered"
    proofs["target_cr"]["proof_type"] = "tampered"
    loaded = teardown_record(checkpoint, MCO_KEY)
    assert loaded["resource_versions"]["drain_pods"] == "88219"
    assert loaded["absence_proofs"]["target_cr"]["proof_type"] == "object_absent"


def test_absent_record_reads_as_none():
    assert teardown_record(_checkpoint(), MCO_KEY) is None
    assert teardown_record(_checkpoint({}), MCO_KEY) is None
    assert teardown_records(_checkpoint()) == {}


def test_all_records_are_returned_when_every_member_is_valid():
    checkpoint = _checkpoint()
    record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")
    record_teardown_phase(checkpoint, CLUSTER_KEY, "uid-2", "cr_absent")
    assert set(teardown_records(checkpoint)) == {MCO_KEY, CLUSTER_KEY}


@pytest.mark.parametrize(
    "stored",
    [
        {"phase": "delete_started"},  # missing expected_uid
        {"expected_uid": "", "phase": "delete_started"},  # empty expected_uid
        {"expected_uid": "u", "phase": "banana"},  # unknown phase
        {"expected_uid": "u"},  # missing phase
        {"expected_uid": "u", "phase": []},  # unhashable phase
        "not-a-mapping",
    ],
)
def test_malformed_records_fail_closed(stored):
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(_checkpoint({MCO_KEY: stored}), MCO_KEY)


@pytest.mark.parametrize("key", ["", "v1/Namespace", "a/b/c/Kind//name"])
def test_a_malformed_record_key_fails_closed(key):
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(_checkpoint(), key, "u", "delete_started")


# --- 10.2.1 completion evidence: the mirrored malformed vectors -------------------
#
# Mirror of MALFORMED_COMPLETION_RECORDS in tests/test_teardown_record.py, member for
# member and id for id. tests/test_checkpoint_state_parity.py runs the authoritative
# list through this implementation, so a divergence between the two lists cannot hide
# a divergence between the two validators.

_DROP = object()  # sentinel: remove the key entirely, which is NOT the same as setting it to {}

_VALID_MCO_PRESENT = {
    "expected_uid": "u",
    "phase": "completed",
    "observed_at": OBSERVED_AT,
    "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219"},
    "absence_proofs": {"target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY}},
}


def _mco(**overrides):
    """A deep copy of the valid MCO namespace-present completed record, with overrides applied."""
    record = copy.deepcopy(_VALID_MCO_PRESENT)
    for key, value in overrides.items():
        if value is _DROP:
            record.pop(key, None)
        else:
            record[key] = value
    return record


MALFORMED_COMPLETION_RECORDS = [
    # --- 10.2.1a phase-conditional presence ---------------------------------------
    ("evidence_before_completed_observed_at", {"expected_uid": "u", "phase": "drained", "observed_at": OBSERVED_AT}),
    ("evidence_before_completed_resource_versions", {"expected_uid": "u", "phase": "drained", "resource_versions": {}}),
    (
        "evidence_before_completed_absence_proofs",
        {
            "expected_uid": "u",
            "phase": "cr_absent",
            "absence_proofs": {"target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY}},
        },
    ),
    ("completed_missing_observed_at", _mco(observed_at=_DROP)),
    ("completed_missing_resource_versions", _mco(resource_versions=_DROP)),
    ("completed_missing_absence_proofs", _mco(absence_proofs=_DROP)),
    ("completed_empty_observed_at", _mco(observed_at="")),
    # --- wrong mapping types ------------------------------------------------------
    ("resource_versions_not_a_mapping", _mco(resource_versions=["88190"])),
    ("absence_proofs_not_a_mapping", _mco(absence_proofs=["target_cr"])),
    ("observed_at_not_a_string", _mco(observed_at=1757000000)),
    # --- 10.2.1b resource_versions closure and value type -------------------------
    ("unknown_rv_label", _mco(resource_versions={"drain_namespace": "1", "drain_pods": "2", "pods": "3"})),
    ("empty_rv_value", _mco(resource_versions={"drain_namespace": "1", "drain_pods": ""})),
    ("non_string_rv_value", _mco(resource_versions={"drain_namespace": "1", "drain_pods": 88219})),
    ("rejected_cr_rv_key", _mco(resource_versions={"cr": "88214", "drain_namespace": "1", "drain_pods": "2"})),
    ("rejected_namespace_absent_rv_key", _mco(resource_versions={"drain_namespace": "1", "namespace_absent": "2"})),
    (
        "canonical_identity_used_as_an_rv_key",
        _mco(resource_versions={"v1/Namespace//open-cluster-management-observability": "88190"}),
    ),
    (
        "replicaset_revision_recorded",
        _mco(resource_versions={"drain_namespace": "1", "drain_pods": "2", "operator_replicaset": "3"}),
    ),
    # --- 10.2.1c absence_proofs schema --------------------------------------------
    (
        "unknown_absence_key",
        _mco(
            absence_proofs={
                "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY},
                "operator_deployment": {"proof_type": "object_absent", "resource_key": MCO_KEY},
            }
        ),
    ),
    ("absence_entry_not_a_mapping", _mco(absence_proofs={"target_cr": "object_absent"})),
    ("absence_entry_missing_proof_type", _mco(absence_proofs={"target_cr": {"resource_key": MCO_KEY}})),
    ("absence_entry_missing_resource_key", _mco(absence_proofs={"target_cr": {"proof_type": "object_absent"}})),
    (
        "absence_entry_extra_field",
        _mco(
            absence_proofs={
                "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY, "observed_at": OBSERVED_AT}
            }
        ),
    ),
    ("absence_entry_empty_string", _mco(absence_proofs={"target_cr": {"proof_type": "", "resource_key": MCO_KEY}})),
    (
        "unknown_proof_type",
        _mco(absence_proofs={"target_cr": {"proof_type": "assumed_absent", "resource_key": MCO_KEY}}),
    ),
    (
        "proof_type_not_permitted_for_its_key",
        _mco(absence_proofs={"target_cr": {"proof_type": "namespace_absent", "resource_key": MCO_KEY}}),
    ),
    (
        "target_cr_resource_key_mismatch",
        _mco(absence_proofs={"target_cr": {"proof_type": "object_absent", "resource_key": CLUSTER_KEY}}),
    ),
    (
        "malformed_resource_key_extra_slash",
        _mco(absence_proofs={"target_cr": {"proof_type": "object_absent", "resource_key": "a/b/c/Kind//name"}}),
    ),
    (
        "malformed_resource_key_empty_component",
        _mco(absence_proofs={"target_cr": {"proof_type": "object_absent", "resource_key": "v1/Namespace//"}}),
    ),
    (
        "drain_namespace_proof_with_a_non_empty_namespace_segment",
        _mco(
            resource_versions={},
            absence_proofs={
                "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY},
                "drain_namespace": {
                    "proof_type": "namespace_absent",
                    "resource_key": "v1/Namespace/some-ns/" "open-cluster-management-observability",
                },
            },
        ),
    ),
    # --- 10.2.1d family and mode key sets -----------------------------------------
    (
        "mco_both_drain_modes",
        _mco(
            absence_proofs={
                "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY},
                "drain_namespace": {"proof_type": "namespace_absent", "resource_key": OBSERVABILITY_NS_KEY},
            }
        ),
    ),
    (
        "mco_neither_drain_mode",
        _mco(
            resource_versions={}, absence_proofs={"target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY}}
        ),
    ),
    ("mco_drain_pods_without_drain_namespace", _mco(resource_versions={"drain_pods": "88219"})),
    (
        "mco_namespace_absent_mode_carrying_drain_pods",
        _mco(
            resource_versions={"drain_pods": "88219"},
            absence_proofs={
                "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY},
                "drain_namespace": {"proof_type": "namespace_absent", "resource_key": OBSERVABILITY_NS_KEY},
            },
        ),
    ),
    (
        "managed_cluster_carrying_drain_evidence",
        {
            "expected_uid": "u",
            "phase": "completed",
            "observed_at": OBSERVED_AT,
            "resource_versions": {"drain_namespace": "1", "drain_pods": "2"},
            "absence_proofs": {"target_cr": {"proof_type": "object_absent", "resource_key": CLUSTER_KEY}},
        },
    ),
    (
        "managed_cluster_carrying_a_namespace_absence_proof",
        {
            "expected_uid": "u",
            "phase": "completed",
            "observed_at": OBSERVED_AT,
            "resource_versions": {},
            "absence_proofs": {
                "target_cr": {"proof_type": "object_absent", "resource_key": CLUSTER_KEY},
                "drain_namespace": {"proof_type": "namespace_absent", "resource_key": OBSERVABILITY_NS_KEY},
            },
        },
    ),
    ("completed_without_target_cr_proof", _mco(absence_proofs={})),
]

# Vector ids whose stored record key is not MCO_KEY, so the tests file them under the right key.
_VECTOR_KEYS = {
    "managed_cluster_carrying_drain_evidence": CLUSTER_KEY,
    "managed_cluster_carrying_a_namespace_absence_proof": CLUSTER_KEY,
    "target_cr_resource_key_mismatch": MCO_KEY,
}


@pytest.mark.parametrize(
    "vector_id, stored",
    MALFORMED_COMPLETION_RECORDS,
    ids=[vector_id for vector_id, _ in MALFORMED_COMPLETION_RECORDS],
)
def test_malformed_completion_evidence_fails_closed_on_read(vector_id, stored):
    key = _VECTOR_KEYS.get(vector_id, MCO_KEY)
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(_checkpoint({key: stored}), key)


def test_the_mirrored_vector_set_is_the_same_size_as_the_python_one():
    """A silently shortened mirror would weaken this lane without failing it."""
    ids = [vector_id for vector_id, _ in MALFORMED_COMPLETION_RECORDS]
    assert len(ids) == 36
    assert len(set(ids)) == 36


def test_a_drain_namespace_proof_must_name_the_family_drain_namespace():
    """10.2.1c: the MCO record's fixed drain namespace, not another family's."""
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(
            _checkpoint(
                {
                    MCO_KEY: _mco(
                        resource_versions={},
                        absence_proofs={
                            "target_cr": {"proof_type": "object_absent", "resource_key": MCO_KEY},
                            "drain_namespace": {"proof_type": "namespace_absent", "resource_key": ACM_NS_KEY},
                        },
                    )
                }
            ),
            MCO_KEY,
        )


# --- 10.2.1b what a reader can and cannot check -----------------------------------
def test_a_revision_string_the_schema_cannot_disprove_is_accepted():
    """Amendment 22.1: a `resourceVersion` is an opaque, server-defined string.

    No validator can decide from the value alone that a string is a genuine
    revision, and a numeric-format check would violate that API contract. The
    reader's only checkable properties are the closed key set and the value
    type, so a namespace name written where a revision belongs is accepted
    here. That is a PRODUCER defect, caught by producer-seam tests.
    """
    checkpoint = _checkpoint()
    _record_completed(
        checkpoint,
        MCO_KEY,
        resource_versions={"drain_namespace": "open-cluster-management-observability", "drain_pods": "88219"},
    )
    assert teardown_record(checkpoint, MCO_KEY)["resource_versions"] == {
        "drain_namespace": "open-cluster-management-observability",
        "drain_pods": "88219",
    }


# --- closed top-level record shape ------------------------------------------------
def test_an_unknown_top_level_field_fails_closed():
    """The record is mutation authority: no producer may write a field the reader
    does not check, so an unrecognized top-level field is malformed."""
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(
            _checkpoint({MCO_KEY: {"expected_uid": "u", "phase": "delete_started", "surprise": 1}}), MCO_KEY
        )


@pytest.mark.parametrize(
    "key, stored",
    [
        # An identity field stored as null. Remove the null rule and the
        # exactly-one-outcome check reads `operator_deployment` as ABSENT, so this
        # record is accepted here while lib/teardown_record.py rejects it. No other
        # rule covers it: the evidence fields are additionally type-checked, but the
        # identity fields are tested for presence with `is not None`, which is
        # precisely what a stored null subverts.
        (
            MCH_KEY,
            {
                "expected_uid": "uid-mch",
                "phase": "delete_started",
                "operator_deployment": None,
                "operator_identity_unavailable": _unavailable(),
            },
        ),
        # The same hole on a kind that may carry no identity field at all.
        (MCO_KEY, {"expected_uid": "u", "phase": "delete_started", "operator_deployment": None}),
        # The other identity field, so neither name is protected by accident.
        (
            MCH_KEY,
            {
                "expected_uid": "uid-mch",
                "phase": "delete_started",
                "operator_deployment": _identity(),
                "operator_identity_unavailable": None,
            },
        ),
    ],
    ids=["deployment_null_beside_an_unavailable", "deployment_null_on_a_non_identity_kind", "unavailable_null"],
)
def test_a_stored_null_field_is_malformed_not_absent(key, stored):
    """10.2.1a: an absent field is omitted entirely. A stored null is a third
    representation the reader must not silently accept as absence.

    Every payload here is one the rest of the rule set ACCEPTS once the null rule is
    removed, so this test actually discriminates (verified by mutation). Two shapes
    deliberately do not appear, because they prove nothing about this rule: a null in
    a completion-evidence field, which the evidence type checks reject anyway, and a
    null identity field at `completed`, which the §10.2.1d key-set check rejects via
    its own `"operator_deployment" in stored` presence test.
    """
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(_checkpoint({key: stored}), key)


def test_a_null_record_slot_fails_the_write_not_only_the_read():
    """Controller ruling C14: `{key: null}` inside a valid container is corruption.

    Reading `records.get(key)` as "no previous" would let the writer rebind
    expected_uid over a record slot the reader refuses to load.
    """
    checkpoint = _checkpoint({MCO_KEY: None})
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(checkpoint, MCO_KEY)
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")


# --- 10.2.1d valid per-family, per-mode records -----------------------------------
def test_completed_mco_namespace_present_records_exactly_the_two_drain_revisions():
    checkpoint = _checkpoint()
    _record_completed(checkpoint, MCO_KEY)
    loaded = teardown_record(checkpoint, MCO_KEY)
    assert set(loaded["resource_versions"]) == {"drain_namespace", "drain_pods"}
    assert set(loaded["absence_proofs"]) == {"target_cr"}


def test_completed_mco_namespace_absent_records_an_empty_revision_map():
    checkpoint = _checkpoint()
    _record_completed(
        checkpoint,
        MCO_KEY,
        resource_versions={},
        absence_proofs={"target_cr": _cr_absent(MCO_KEY), "drain_namespace": _ns_absent(OBSERVABILITY_NS_KEY)},
    )
    loaded = teardown_record(checkpoint, MCO_KEY)
    assert loaded["resource_versions"] == {}
    assert set(loaded["absence_proofs"]) == {"target_cr", "drain_namespace"}
    assert loaded["absence_proofs"]["drain_namespace"]["resource_key"] == OBSERVABILITY_NS_KEY


@pytest.mark.parametrize("proof_type", ["object_absent", "crd_absent"])
def test_completed_managed_cluster_is_absence_only(proof_type):
    checkpoint = _checkpoint()
    _record_completed(
        checkpoint,
        CLUSTER_KEY,
        resource_versions={},
        absence_proofs={"target_cr": _cr_absent(CLUSTER_KEY, proof_type)},
    )
    loaded = teardown_record(checkpoint, CLUSTER_KEY)
    assert loaded["resource_versions"] == {}
    assert set(loaded["absence_proofs"]) == {"target_cr"}
    # A crd_absent completion keeps the enclosing record key; it is not replaced by a CRD identity.
    assert loaded["absence_proofs"]["target_cr"]["resource_key"] == CLUSTER_KEY


def test_completed_mch_namespace_present_with_captured_identity():
    checkpoint = _checkpoint()
    record_teardown_phase(
        checkpoint,
        MCH_KEY,
        "uid-mch",
        "completed",
        observed_at=OBSERVED_AT,
        resource_versions={"drain_namespace": "88190", "drain_pods": "88219", "operator_deployment": "88203"},
        absence_proofs={"target_cr": _cr_absent(MCH_KEY)},
        operator_deployment=_identity(),
    )
    loaded = teardown_record(checkpoint, MCH_KEY)
    assert set(loaded["resource_versions"]) == {"drain_namespace", "drain_pods", "operator_deployment"}
    assert set(loaded["absence_proofs"]) == {"target_cr"}


def test_completed_mch_namespace_present_with_identity_unavailable():
    """July criterion 11: no identity means no exclusion, so only the two drain revisions exist."""
    checkpoint = _checkpoint()
    record_teardown_phase(
        checkpoint,
        MCH_KEY,
        "uid-mch",
        "completed",
        observed_at=OBSERVED_AT,
        resource_versions={"drain_namespace": "88190", "drain_pods": "88219"},
        absence_proofs={"target_cr": _cr_absent(MCH_KEY)},
        operator_identity_unavailable=_unavailable(),
    )
    loaded = teardown_record(checkpoint, MCH_KEY)
    assert set(loaded["resource_versions"]) == {"drain_namespace", "drain_pods"}
    assert "operator_deployment" not in loaded["resource_versions"]


def test_completed_mch_namespace_absent_discharges_both_drain_predicates():
    """10.2.1d: one namespace-absence entry stands for the pod-empty AND Deployment re-read."""
    checkpoint = _checkpoint()
    record_teardown_phase(
        checkpoint,
        MCH_KEY,
        "uid-mch",
        "completed",
        observed_at=OBSERVED_AT,
        resource_versions={},
        absence_proofs={"target_cr": _cr_absent(MCH_KEY), "drain_namespace": _ns_absent(ACM_NS_KEY)},
        operator_deployment=_identity(),
    )
    loaded = teardown_record(checkpoint, MCH_KEY)
    assert loaded["resource_versions"] == {}
    # The identity field survives; the Deployment REVISION does not, because no GET happened.
    assert loaded["operator_deployment"]["uid"] == "dep-uid-1"
    assert "operator_deployment" not in loaded["resource_versions"]


def test_deployment_revision_rejected_when_no_identity_was_captured():
    """10.2.1b if-and-only-if, violation 1: no identity field, so no Deployment revision."""
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(
            _checkpoint(),
            MCH_KEY,
            "uid-mch",
            "completed",
            observed_at=OBSERVED_AT,
            resource_versions={"drain_namespace": "88190", "drain_pods": "88219", "operator_deployment": "88203"},
            absence_proofs={"target_cr": _cr_absent(MCH_KEY)},
            operator_identity_unavailable=_unavailable(),
        )


def test_deployment_revision_rejected_in_the_namespace_absent_mode():
    """10.2.1b if-and-only-if, violation 2: the entailment skipped the Deployment GET."""
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(
            _checkpoint(),
            MCH_KEY,
            "uid-mch",
            "completed",
            observed_at=OBSERVED_AT,
            resource_versions={"operator_deployment": "88203"},
            absence_proofs={"target_cr": _cr_absent(MCH_KEY), "drain_namespace": _ns_absent(ACM_NS_KEY)},
            operator_deployment=_identity(),
        )


# --- 10.2.1c resource_key grammar -------------------------------------------------
@pytest.mark.parametrize(
    "resource_key, expected",
    [
        (
            "cluster.open-cluster-management.io/v1/ManagedCluster//cluster-a",
            ("cluster.open-cluster-management.io/v1", "ManagedCluster", "", "cluster-a"),
        ),
        (
            "v1/Namespace//open-cluster-management-observability",
            ("v1", "Namespace", "", "open-cluster-management-observability"),
        ),
        (
            "operator.open-cluster-management.io/v1/MultiClusterHub/" "open-cluster-management/multiclusterhub",
            ("operator.open-cluster-management.io/v1", "MultiClusterHub", "open-cluster-management", "multiclusterhub"),
        ),
    ],
)
def test_resource_key_right_split_is_unambiguous(resource_key, expected):
    assert split_resource_key(resource_key) == expected


@pytest.mark.parametrize(
    "resource_key",
    [
        "a/b/c/Kind//name",  # apiVersion carries more than one "/"
        "v1/Namespace//",  # empty name
        "v1/Namespace",  # too few segments
        "/Namespace//name",  # empty apiVersion
        "v1///name",  # empty kind
    ],
)
def test_malformed_resource_keys_are_rejected(resource_key):
    assert split_resource_key(resource_key) is None


# --- immutability of completion evidence ------------------------------------------
def test_expected_uid_is_never_rebound_by_a_later_write():
    checkpoint = _checkpoint()
    record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(checkpoint, MCO_KEY, "uid-2", "cr_absent")
    assert teardown_record(checkpoint, MCO_KEY)["expected_uid"] == "uid-1"


@pytest.mark.parametrize(
    "mutation",
    [
        {"resource_versions": {"drain_namespace": "99999", "drain_pods": "88219"}},
        {"absence_proofs": {"target_cr": {"proof_type": "crd_absent", "resource_key": MCO_KEY}}},
        {"observed_at": "2026-09-05T00:00:00Z"},
    ],
    ids=["revisions", "absence_proofs", "observed_at"],
)
def test_completion_evidence_is_never_mutated_after_completed(mutation):
    checkpoint = _checkpoint()
    _record_completed(checkpoint, MCO_KEY, expected_uid="uid-1")
    with pytest.raises(MalformedTeardownRecord):
        _record_completed(checkpoint, MCO_KEY, expected_uid="uid-1", **mutation)
    assert teardown_record(checkpoint, MCO_KEY)["resource_versions"]["drain_namespace"] == "88190"


def test_a_completed_record_may_not_regress_to_an_earlier_phase():
    """The rejection message must name the phase transition, not a changed field."""
    checkpoint = _checkpoint()
    _record_completed(checkpoint, MCO_KEY, expected_uid="uid-1")
    with pytest.raises(MalformedTeardownRecord, match="already completed"):
        record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "recovery_required")


def test_a_corrupt_previous_record_fails_the_write():
    """The writer loads `previous` through the same rule set, so it cannot be laundered."""
    checkpoint = _checkpoint({MCO_KEY: {"expected_uid": "u", "phase": "banana"}})
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(checkpoint, MCO_KEY, "u", "cr_absent")


# --- 10.2.2 / 10.2.3 nested identity ----------------------------------------------
def test_mch_identity_outcome_must_be_exactly_one():
    with pytest.raises(MalformedTeardownRecord):
        _record_mch(_checkpoint(), operator_identity_unavailable={"reason": "csv_ambiguous"})
    with pytest.raises(MalformedTeardownRecord):
        _record_mch(_checkpoint(), operator_deployment=None, operator_identity_unavailable=None)


def test_a_non_mch_record_may_not_carry_operator_identity():
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(_checkpoint(), MCO_KEY, "u", "delete_started", operator_deployment=_identity())


@pytest.mark.parametrize(
    "identity",
    [
        _identity(namespace=None),
        _identity(namespace=""),
        _identity(name=None),
        _identity(uid=None),
        _identity(uid=""),
        _identity(captured_at=None),
        _identity(discovery_method=None),
        _identity(discovery_method="guessed_by_name_prefix"),
        _identity(csv=None),
        _identity(
            csv={
                "namespace": "open-cluster-management",
                "uid": "csv-uid-1",
                "owned_crd": "multiclusterhubs.operator.open-cluster-management.io",
            }
        ),
        _identity(
            csv={
                "namespace": "open-cluster-management",
                "name": "acm.v2.13.0",
                "owned_crd": "multiclusterhubs.operator.open-cluster-management.io",
            }
        ),
        _identity(
            csv={
                "namespace": "elsewhere",
                "name": "acm.v2.13.0",
                "uid": "csv-uid-1",
                "owned_crd": "multiclusterhubs.operator.open-cluster-management.io",
            }
        ),
        _identity(
            csv={
                "namespace": "open-cluster-management",
                "name": "acm.v2.13.0",
                "uid": "csv-uid-1",
                "owned_crd": "somethingelse.example.com",
            }
        ),
        _identity(mch_teardown_key="operator.open-cluster-management.io/v1/MultiClusterHub//other"),
        _identity(mch_expected_uid="uid-other"),
        _identity(uid=7),
        {"uid": "dep-uid-1"},  # partial shape
    ],
)
def test_malformed_operator_deployment_fails_closed(identity):
    with pytest.raises(MalformedTeardownRecord):
        _record_mch(_checkpoint(), operator_deployment=identity)


@pytest.mark.parametrize(
    "unavailable",
    [
        {
            "reason": "not_a_known_reason",
            "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
            "captured_at": "2026-09-04T10:09:00Z",
            "evidence_summary": "no candidate CSV",
            "mch_teardown_key": MCH_KEY,
            "mch_expected_uid": "uid-mch",
        },
        {
            "reason": "csv_absent",
            "captured_at": "2026-09-04T10:09:00Z",
            "evidence_summary": "no candidate CSV",
            "mch_teardown_key": MCH_KEY,
            "mch_expected_uid": "uid-mch",
        },  # missing discovery_method
        {
            "reason": "csv_absent",
            "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
            "evidence_summary": "no candidate CSV",
            "mch_teardown_key": MCH_KEY,
            "mch_expected_uid": "uid-mch",
        },  # missing captured_at
        {
            "reason": "csv_absent",
            "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
            "captured_at": "2026-09-04T10:09:00Z",
            "mch_teardown_key": MCH_KEY,
            "mch_expected_uid": "uid-mch",
        },  # missing evidence_summary
        {
            "reason": "csv_absent",
            "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
            "captured_at": "2026-09-04T10:09:00Z",
            "evidence_summary": "x",
            "mch_expected_uid": "uid-mch",
        },  # missing teardown key
        {
            "reason": "csv_absent",
            "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
            "captured_at": "2026-09-04T10:09:00Z",
            "evidence_summary": "x",
            "mch_teardown_key": MCH_KEY,
            "mch_expected_uid": "uid-other",
        },  # mismatched UID
    ],
)
def test_malformed_operator_identity_unavailable_fails_closed(unavailable):
    with pytest.raises(MalformedTeardownRecord):
        _record_mch(_checkpoint(), operator_deployment=None, operator_identity_unavailable=unavailable)


@pytest.mark.parametrize("reason", UNAVAILABLE_REASONS)
def test_every_enumerated_unavailable_reason_is_accepted(reason):
    checkpoint = _checkpoint()
    _record_mch(checkpoint, operator_deployment=None, operator_identity_unavailable=_unavailable(reason))
    assert teardown_record(checkpoint, MCH_KEY)["operator_identity_unavailable"]["reason"] == reason


def test_malformed_nested_identity_is_rejected_on_reload_too():
    """The reader validates independently of the writer: a hand-edited checkpoint fails closed."""
    checkpoint = _checkpoint(
        {MCH_KEY: {"expected_uid": "uid-mch", "phase": "delete_started", "operator_deployment": {"uid": "dep-uid-1"}}}
    )
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(checkpoint, MCH_KEY)


def test_teardown_records_fails_closed_on_any_malformed_member():
    checkpoint = _checkpoint(
        {MCO_KEY: {"expected_uid": "u", "phase": "delete_started"}, MCH_KEY: {"expected_uid": "u", "phase": "banana"}}
    )
    with pytest.raises(MalformedTeardownRecord):
        teardown_records(checkpoint)


# --- a corrupt record container fails closed, never open --------------------------
@pytest.mark.parametrize("container", [[], "", 0, None, [1], "x"], ids=repr)
def test_a_corrupt_record_container_fails_closed_on_read(container):
    """A falsy container must not read as "no record was ever written".

    Reading `[]` or `null` as an empty mapping would skip the expected_uid
    immutability guard entirely, so a replacement object created after the
    switchover would be bound fresh and deleted.
    """
    checkpoint = _checkpoint(container)
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(checkpoint, MCO_KEY)
    with pytest.raises(MalformedTeardownRecord):
        teardown_records(checkpoint)


@pytest.mark.parametrize("container", [[], "", 0, None, [1], "x"], ids=repr)
def test_a_corrupt_record_container_fails_closed_on_write(container):
    """The writer loads `previous` from the same container, so it fails closed too."""
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(_checkpoint(container), MCO_KEY, "uid-1", "delete_started")


@pytest.mark.parametrize("operational_data", ["", [], 0, None, "x"], ids=repr)
def test_corrupt_operational_data_fails_closed(operational_data):
    """Records are mutation authority: a corrupt container is never read as empty.

    A missing `operational_data` key is "nothing recorded yet" and is created by the
    writer; a key present with a corrupt value — a stored null included — is not.
    """
    checkpoint = {"operational_data": operational_data}
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(checkpoint, MCO_KEY)
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")


@pytest.mark.parametrize("checkpoint", [None, [], "x"], ids=repr)
def test_a_non_mapping_checkpoint_fails_closed(checkpoint):
    with pytest.raises(MalformedTeardownRecord):
        teardown_record(checkpoint, MCO_KEY)
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(checkpoint, MCO_KEY, "uid-1", "delete_started")


# --- amendment 13: the captured operator identity is immutable at every phase -----
def test_an_advancing_phase_may_rewrite_the_identical_identity():
    """The phase machine must still be able to progress."""
    checkpoint = _checkpoint()
    _record_mch(checkpoint)
    _record_mch(checkpoint, phase="drain_pending")
    loaded = teardown_record(checkpoint, MCH_KEY)
    assert loaded["phase"] == "drain_pending"
    assert loaded["operator_deployment"]["uid"] == "dep-uid-1"


def test_operator_deployment_is_never_rebound_by_a_later_write():
    """13 case A: the recorded Deployment identity may not point somewhere else."""
    checkpoint = _checkpoint()
    _record_mch(checkpoint)
    with pytest.raises(MalformedTeardownRecord):
        _record_mch(checkpoint, phase="drain_pending", operator_deployment=_identity(uid="dep-uid-2"))
    assert teardown_record(checkpoint, MCH_KEY)["operator_deployment"]["uid"] == "dep-uid-1"


def test_identity_unavailable_is_never_upgraded_by_rediscovery():
    """13 case B: an unavailable outcome is never silently upgraded to an identity."""
    checkpoint = _checkpoint()
    _record_mch(checkpoint, operator_deployment=None, operator_identity_unavailable=_unavailable())
    with pytest.raises(MalformedTeardownRecord):
        _record_mch(checkpoint, phase="drain_pending", operator_deployment=_identity())


def test_a_captured_identity_is_never_downgraded_to_unavailable():
    """13 case C: the reverse transition is equally a rebind."""
    checkpoint = _checkpoint()
    _record_mch(checkpoint)
    with pytest.raises(MalformedTeardownRecord):
        _record_mch(
            checkpoint,
            phase="drain_pending",
            operator_deployment=None,
            operator_identity_unavailable=_unavailable(),
        )


def test_identity_is_immutable_on_a_completed_record_with_unchanged_evidence():
    """13 case D: byte-identical evidence does not license a rebound identity."""
    checkpoint = _checkpoint()
    completed: dict[str, Any] = {
        "observed_at": OBSERVED_AT,
        "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219", "operator_deployment": "88203"},
        "absence_proofs": {"target_cr": _cr_absent(MCH_KEY)},
    }
    record_teardown_phase(
        checkpoint, MCH_KEY, "uid-mch", "completed", operator_deployment=_identity(), **copy.deepcopy(completed)
    )
    with pytest.raises(MalformedTeardownRecord):
        record_teardown_phase(
            checkpoint,
            MCH_KEY,
            "uid-mch",
            "completed",
            operator_deployment=_identity(uid="dep-uid-2"),
            **copy.deepcopy(completed),
        )
    assert teardown_record(checkpoint, MCH_KEY)["operator_deployment"]["uid"] == "dep-uid-1"
