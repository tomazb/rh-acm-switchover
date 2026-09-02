"""Durable teardown records (R4-03 PR B)."""

import copy

import pytest

from lib.constants import OPERATOR_IDENTITY_UNAVAILABLE_REASONS
from lib.exceptions import FatalError
from lib.run_record import RunRecord
from lib.teardown_record import (
    AbsenceProof,
    MalformedTeardownRecord,
    TeardownPhase,
    TeardownRecord,
    split_resource_key,
    teardown_key,
)
from lib.utils import StateManager

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


def _cr_absent(key, proof_type="object_absent"):
    """The `target_cr` proof every completed record must carry (§10.2.1c)."""
    return AbsenceProof(proof_type=proof_type, resource_key=key)


def _ns_absent(namespace_key):
    return AbsenceProof(proof_type="namespace_absent", resource_key=namespace_key)


def _completed(key, **overrides):
    """A valid completed record for `key`, defaulting to the MCO namespace-present mode."""
    fields = {
        "key": key,
        "expected_uid": "u",
        "phase": TeardownPhase.COMPLETED,
        "observed_at": OBSERVED_AT,
        "resource_versions": {"drain_namespace": "88190", "drain_pods": "88219"},
        "absence_proofs": {"target_cr": _cr_absent(key)},
    }
    fields.update(overrides)
    return TeardownRecord(**fields)


@pytest.fixture
def state_manager(tmp_path):
    return StateManager(str(tmp_path / "switchover-test.json"))


def _raise(exc):
    """Return a zero-argument callable that raises `exc` (monkeypatch stand-in)."""

    def _raiser(*args, **kwargs):
        raise exc

    return _raiser


def _identity(**overrides):
    identity = {
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


def _mch_record(**overrides):
    # A non-completed record carries NO completion evidence at all (§10.2.1a).
    fields = {
        "key": MCH_KEY,
        "expected_uid": "uid-mch",
        "phase": TeardownPhase.DELETE_STARTED,
        "operator_deployment": _identity(),
    }
    fields.update(overrides)
    return TeardownRecord(**fields)


def test_key_is_stable_and_includes_an_empty_namespace_segment_when_cluster_scoped():
    assert MCO_KEY == ("observability.open-cluster-management.io/v1beta2/MultiClusterObservability//observability")


def test_record_round_trips_through_the_facade(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED))
    loaded = record.teardown_record(MCO_KEY)
    assert loaded.expected_uid == "uid-1"
    assert loaded.phase is TeardownPhase.DELETE_STARTED
    # No completion evidence exists before `completed`, and absent is not empty.
    assert loaded.observed_at is None
    assert loaded.resource_versions is None
    assert loaded.absence_proofs is None


def test_completed_record_round_trips_both_evidence_fields(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(_completed(MCO_KEY, expected_uid="uid-1"))
    loaded = record.teardown_record(MCO_KEY)
    assert loaded.observed_at == OBSERVED_AT
    assert loaded.resource_versions == {"drain_namespace": "88190", "drain_pods": "88219"}
    assert loaded.absence_proofs == {"target_cr": _cr_absent(MCO_KEY)}


def test_present_empty_resource_versions_is_not_the_same_as_absent(state_manager):
    """§10.2.1a: `{}` is valid evidence for an absence-only proof; `None` is malformed."""
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        _completed(
            CLUSTER_KEY,
            resource_versions={},
            absence_proofs={"target_cr": _cr_absent(CLUSTER_KEY)},
        )
    )
    loaded = record.teardown_record(CLUSTER_KEY)
    assert loaded.resource_versions == {}
    assert loaded.resource_versions is not None
    stored = state_manager._get_config("decommission_teardown_records")[CLUSTER_KEY]
    assert stored["resource_versions"] == {}, "a present-and-empty mapping must be serialized"


def test_absent_record_reads_as_none(state_manager):
    assert RunRecord(state_manager).teardown_record(MCO_KEY) is None


def test_phase_write_is_forced_durable_before_returning(state_manager, monkeypatch):
    flushed = []
    monkeypatch.setattr(state_manager, "flush_state", lambda: flushed.append(True))
    RunRecord(state_manager).record_teardown_phase(
        TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
    )
    assert flushed, "teardown phase must be forced durable, not left to lazy save"


def test_a_failed_durable_write_propagates(state_manager, monkeypatch):
    monkeypatch.setattr(state_manager, "flush_state", _raise(OSError("disk full")))
    with pytest.raises(OSError):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED)
        )


@pytest.mark.parametrize(
    "stored",
    [
        {"phase": "delete_started"},  # missing expected_uid
        {"expected_uid": "", "phase": "delete_started"},  # empty expected_uid
        {"expected_uid": "u", "phase": "banana"},  # unknown phase
        {"expected_uid": "u"},  # missing phase
        "not-a-mapping",
    ],
)
def test_malformed_records_fail_closed(state_manager, stored):
    state_manager._set_config("decommission_teardown_records", {MCO_KEY: stored})
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).teardown_record(MCO_KEY)


def test_malformed_teardown_record_is_fatal():
    assert issubclass(MalformedTeardownRecord, FatalError)


# --- §10.2.1 completion evidence: the shared malformed vectors --------------------
#
# MALFORMED_COMPLETION_RECORDS is the executable, serialization-level vector set. It is the
# single source of truth for the malformed completion-evidence matrix on BOTH sides: Task B2's
# collection tests import it, and the shared parity test in
# tests/test_checkpoint_state_parity.py asserts that Python and the collection classify every
# member identically. Each entry is (vector_id, stored_mapping) using only JSON-compatible
# types, so the collection can consume it without importing any Python-CLI runtime code.

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
    # --- §10.2.1a phase-conditional presence -------------------------------------
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
    # --- §10.2.1b resource_versions closure and value type ------------------------
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
    # --- §10.2.1c absence_proofs schema -------------------------------------------
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
    # --- §10.2.1d family and mode key sets ----------------------------------------
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
def test_malformed_completion_evidence_fails_closed_on_read(state_manager, vector_id, stored):
    key = _VECTOR_KEYS.get(vector_id, MCO_KEY)
    state_manager._set_config("decommission_teardown_records", {key: stored})
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).teardown_record(key)


# --- §10.2.1b what a reader can and cannot check ----------------------------------
def test_a_revision_string_the_schema_cannot_disprove_is_accepted(state_manager):
    """Amendment §22.1: a `resourceVersion` is an opaque, server-defined string.

    No validator can decide from the value alone that a string is a genuine
    revision, and a numeric-format check would violate that API contract. The
    reader's only checkable properties are the closed key set (§22.3) and the
    value type (a non-empty string), so a namespace name written where a
    revision belongs is accepted here. That is a PRODUCER defect, caught by the
    producer-seam tests PRs C/D/E own, not by this validator.
    """
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        _completed(
            MCO_KEY,
            resource_versions={"drain_namespace": "open-cluster-management-observability", "drain_pods": "88219"},
        )
    )
    loaded = record.teardown_record(MCO_KEY)
    assert loaded.resource_versions == {
        "drain_namespace": "open-cluster-management-observability",
        "drain_pods": "88219",
    }


# --- closed top-level record shape ------------------------------------------------
def test_an_unknown_top_level_field_fails_closed(state_manager):
    """The record is mutation authority: no producer may write a field the reader
    does not check, so an unrecognized top-level field is malformed."""
    state_manager._set_config(
        "decommission_teardown_records",
        {MCO_KEY: {"expected_uid": "u", "phase": "delete_started", "surprise": 1}},
    )
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).teardown_record(MCO_KEY)


def test_a_stored_null_evidence_field_is_malformed_not_absent(state_manager):
    """§10.2.1a: an absent field is omitted entirely. A stored null is a third
    representation the reader must not silently accept as absence."""
    state_manager._set_config(
        "decommission_teardown_records",
        {MCO_KEY: {"expected_uid": "u", "phase": "delete_started", "resource_versions": None}},
    )
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).teardown_record(MCO_KEY)


# --- §10.2.1d valid per-family, per-mode records ----------------------------------
def test_completed_mco_namespace_present_records_exactly_the_two_drain_revisions(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(_completed(MCO_KEY))
    loaded = record.teardown_record(MCO_KEY)
    assert set(loaded.resource_versions) == {"drain_namespace", "drain_pods"}
    assert set(loaded.absence_proofs) == {"target_cr"}


def test_completed_mco_namespace_absent_records_an_empty_revision_map(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        _completed(
            MCO_KEY,
            resource_versions={},
            absence_proofs={"target_cr": _cr_absent(MCO_KEY), "drain_namespace": _ns_absent(OBSERVABILITY_NS_KEY)},
        )
    )
    loaded = record.teardown_record(MCO_KEY)
    assert loaded.resource_versions == {}
    assert set(loaded.absence_proofs) == {"target_cr", "drain_namespace"}
    assert loaded.absence_proofs["drain_namespace"].resource_key == OBSERVABILITY_NS_KEY


@pytest.mark.parametrize("proof_type", ["object_absent", "crd_absent"])
def test_completed_managed_cluster_is_absence_only(state_manager, proof_type):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        _completed(
            CLUSTER_KEY,
            resource_versions={},
            absence_proofs={"target_cr": _cr_absent(CLUSTER_KEY, proof_type)},
        )
    )
    loaded = record.teardown_record(CLUSTER_KEY)
    assert loaded.resource_versions == {}
    assert set(loaded.absence_proofs) == {"target_cr"}
    # A crd_absent completion keeps the enclosing record key; it is not replaced by a CRD identity.
    assert loaded.absence_proofs["target_cr"].resource_key == CLUSTER_KEY


def _unavailable(reason="csv_absent"):
    return {
        "reason": reason,
        "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
        "captured_at": "2026-09-04T10:09:00Z",
        "evidence_summary": "no candidate CSV",
        "mch_teardown_key": MCH_KEY,
        "mch_expected_uid": "uid-mch",
    }


def test_completed_mch_namespace_present_with_captured_identity(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(
            key=MCH_KEY,
            expected_uid="uid-mch",
            phase=TeardownPhase.COMPLETED,
            observed_at=OBSERVED_AT,
            resource_versions={"drain_namespace": "88190", "drain_pods": "88219", "operator_deployment": "88203"},
            absence_proofs={"target_cr": _cr_absent(MCH_KEY)},
            operator_deployment=_identity(),
        )
    )
    loaded = record.teardown_record(MCH_KEY)
    assert set(loaded.resource_versions) == {"drain_namespace", "drain_pods", "operator_deployment"}
    assert set(loaded.absence_proofs) == {"target_cr"}


def test_completed_mch_namespace_present_with_identity_unavailable(state_manager):
    """July criterion 11: no identity means no exclusion, so only the two drain revisions exist."""
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(
            key=MCH_KEY,
            expected_uid="uid-mch",
            phase=TeardownPhase.COMPLETED,
            observed_at=OBSERVED_AT,
            resource_versions={"drain_namespace": "88190", "drain_pods": "88219"},
            absence_proofs={"target_cr": _cr_absent(MCH_KEY)},
            operator_identity_unavailable=_unavailable(),
        )
    )
    loaded = record.teardown_record(MCH_KEY)
    assert set(loaded.resource_versions) == {"drain_namespace", "drain_pods"}
    assert "operator_deployment" not in loaded.resource_versions


def test_completed_mch_namespace_absent_discharges_both_drain_predicates(state_manager):
    """§10.2.1d: one namespace-absence entry stands for the pod-empty AND Deployment re-read."""
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        TeardownRecord(
            key=MCH_KEY,
            expected_uid="uid-mch",
            phase=TeardownPhase.COMPLETED,
            observed_at=OBSERVED_AT,
            resource_versions={},
            absence_proofs={"target_cr": _cr_absent(MCH_KEY), "drain_namespace": _ns_absent(ACM_NS_KEY)},
            operator_deployment=_identity(),
        )
    )
    loaded = record.teardown_record(MCH_KEY)
    assert loaded.resource_versions == {}
    # The identity field survives; the Deployment REVISION does not, because no GET happened.
    assert loaded.operator_deployment["uid"] == "dep-uid-1"
    assert "operator_deployment" not in loaded.resource_versions


def test_deployment_revision_rejected_when_no_identity_was_captured(state_manager):
    """§10.2.1b if-and-only-if, violation 1: no identity field, so no Deployment revision."""
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(
                key=MCH_KEY,
                expected_uid="uid-mch",
                phase=TeardownPhase.COMPLETED,
                observed_at=OBSERVED_AT,
                resource_versions={"drain_namespace": "88190", "drain_pods": "88219", "operator_deployment": "88203"},
                absence_proofs={"target_cr": _cr_absent(MCH_KEY)},
                operator_identity_unavailable=_unavailable(),
            )
        )


def test_deployment_revision_rejected_in_the_namespace_absent_mode(state_manager):
    """§10.2.1b if-and-only-if, violation 2: the entailment skipped the Deployment GET."""
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(
                key=MCH_KEY,
                expected_uid="uid-mch",
                phase=TeardownPhase.COMPLETED,
                observed_at=OBSERVED_AT,
                resource_versions={"operator_deployment": "88203"},
                absence_proofs={"target_cr": _cr_absent(MCH_KEY), "drain_namespace": _ns_absent(ACM_NS_KEY)},
                operator_deployment=_identity(),
            )
        )


# --- §10.2.1c resource_key grammar ------------------------------------------------
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
def test_expected_uid_is_never_rebound_by_a_later_write(state_manager):
    record = RunRecord(state_manager)
    record.record_teardown_phase(TeardownRecord(key=MCO_KEY, expected_uid="uid-1", phase=TeardownPhase.DELETE_STARTED))
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(TeardownRecord(key=MCO_KEY, expected_uid="uid-2", phase=TeardownPhase.CR_ABSENT))
    assert record.teardown_record(MCO_KEY).expected_uid == "uid-1"


@pytest.mark.parametrize(
    "mutation",
    [
        {"resource_versions": {"drain_namespace": "99999", "drain_pods": "88219"}},
        {"absence_proofs": {"target_cr": AbsenceProof(proof_type="crd_absent", resource_key=MCO_KEY)}},
        {"observed_at": "2026-09-05T00:00:00Z"},
    ],
    ids=["revisions", "absence_proofs", "observed_at"],
)
def test_completion_evidence_is_never_mutated_after_completed(state_manager, mutation):
    record = RunRecord(state_manager)
    record.record_teardown_phase(_completed(MCO_KEY, expected_uid="uid-1"))
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(_completed(MCO_KEY, expected_uid="uid-1", **mutation))
    assert record.teardown_record(MCO_KEY).resource_versions["drain_namespace"] == "88190"


# --- §10.2.2 / §10.2.3 nested identity -------------------------------------------
def test_mch_identity_outcome_must_be_exactly_one(state_manager):
    record = RunRecord(state_manager)
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(_mch_record(operator_identity_unavailable={"reason": "csv_ambiguous"}))
    with pytest.raises(MalformedTeardownRecord):
        record.record_teardown_phase(_mch_record(operator_deployment=None, operator_identity_unavailable=None))


def test_a_non_mch_record_may_not_carry_operator_identity(state_manager):
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(
            TeardownRecord(
                key=MCO_KEY, expected_uid="u", phase=TeardownPhase.DELETE_STARTED, operator_deployment=_identity()
            )
        )


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
def test_malformed_operator_deployment_fails_closed(state_manager, identity):
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(_mch_record(operator_deployment=identity))


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
def test_malformed_operator_identity_unavailable_fails_closed(state_manager, unavailable):
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).record_teardown_phase(
            _mch_record(operator_deployment=None, operator_identity_unavailable=unavailable)
        )


@pytest.mark.parametrize("reason", OPERATOR_IDENTITY_UNAVAILABLE_REASONS)
def test_every_enumerated_unavailable_reason_is_accepted(state_manager, reason):
    record = RunRecord(state_manager)
    record.record_teardown_phase(
        _mch_record(
            operator_deployment=None,
            operator_identity_unavailable={
                "reason": reason,
                "discovery_method": "olm_csv_owned_mch_crd_install_deployment_v1",
                "captured_at": "2026-09-04T10:09:00Z",
                "evidence_summary": "sanitized summary",
                "mch_teardown_key": MCH_KEY,
                "mch_expected_uid": "uid-mch",
            },
        )
    )
    assert record.teardown_record(MCH_KEY).operator_identity_unavailable["reason"] == reason


def test_malformed_nested_identity_is_rejected_on_reload_too(state_manager):
    """The reader validates independently of the writer: a hand-edited state file fails closed."""
    state_manager._set_config(
        "decommission_teardown_records",
        {MCH_KEY: {"expected_uid": "uid-mch", "phase": "delete_started", "operator_deployment": {"uid": "dep-uid-1"}}},
    )
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).teardown_record(MCH_KEY)


def test_all_teardown_records_fails_closed_on_any_malformed_member(state_manager):
    state_manager._set_config(
        "decommission_teardown_records",
        {MCO_KEY: {"expected_uid": "u", "phase": "delete_started"}, MCH_KEY: {"expected_uid": "u", "phase": "banana"}},
    )
    with pytest.raises(MalformedTeardownRecord):
        RunRecord(state_manager).all_teardown_records()
