"""Durable decommission teardown records (R4-03 PR B, plan section 10.2).

This module owns the record value type, the resource-key grammar, and the
single validation rule set that both the reader and the writer run. Storage
belongs to lib/run_record.py: nothing here reads or writes the state file, so
the run-record seam guardrail keeps its allow-list unchanged.

A teardown record is mutation authority, so every rule fails closed by raising
MalformedTeardownRecord. Nothing here repairs, defaults, or degrades a stored
shape -- the deliberate opposite of RunSummary.from_snapshot, which is only a
reporting view.

`operator_deployment` and `operator_identity_unavailable` are validated here
but written by a later PR, so no producer can persist a shape this reader does
not already check.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, NoReturn, Optional, Tuple

from lib.constants import (
    ACM_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
    OPERATOR_IDENTITY_DISCOVERY_METHOD,
    OPERATOR_IDENTITY_UNAVAILABLE_REASONS,
)
from lib.exceptions import FatalError


class MalformedTeardownRecord(FatalError):
    """A teardown record violates plan section 10.2. Fail closed."""


class TeardownPhase(Enum):
    """The teardown lifecycle of one deleted object."""

    DELETE_STARTED = "delete_started"
    CR_ABSENT = "cr_absent"
    DRAIN_PENDING = "drain_pending"
    DRAINED = "drained"
    COMPLETED = "completed"
    RECOVERY_REQUIRED = "recovery_required"


# Kinds whose teardown has a drain scope, and kinds that carry operator identity.
DRAIN_SCOPED_KINDS = frozenset({"MultiClusterObservability", "MultiClusterHub"})
IDENTITY_BEARING_KINDS = frozenset({"MultiClusterHub"})

# Section 10.2.1c closed vocabularies. This module is their single authoritative
# Python definition; the Ansible collection mirrors them and a parity test holds
# the two equal. They are deliberately not restated in lib/constants.py.
RESOURCE_VERSION_LABELS = frozenset({"drain_namespace", "drain_pods", "operator_deployment"})
ABSENCE_PROOF_KEYS = frozenset({"target_cr", "drain_namespace"})
ABSENCE_PROOF_TYPES = frozenset({"object_absent", "crd_absent", "namespace_absent"})
ABSENCE_PROOF_TYPES_BY_KEY = {
    "target_cr": frozenset({"object_absent", "crd_absent"}),
    "drain_namespace": frozenset({"namespace_absent"}),
}

# The fixed drain namespace each drain-scoped family tears down. A
# `drain_namespace` absence proof must name exactly this namespace.
DRAIN_NAMESPACE_BY_KIND = {
    "MultiClusterObservability": OBSERVABILITY_NAMESPACE,
    "MultiClusterHub": ACM_NAMESPACE,
}

# The CRD whose owning CSV identifies the MultiClusterHub operator Deployment.
MCH_OWNED_CRD = "multiclusterhubs.operator.open-cluster-management.io"

NAMESPACE_API_VERSION = "v1"
NAMESPACE_KIND = "Namespace"

_EVIDENCE_FIELDS = ("observed_at", "resource_versions", "absence_proofs")
_IDENTITY_FIELDS = ("operator_deployment", "operator_identity_unavailable")
_ABSENCE_PROOF_FIELDS = frozenset({"proof_type", "resource_key"})
# The closed top-level field set of a stored record. An unknown field is
# malformed: the record is mutation authority, so no later producer may write a
# shape the reader does not check.
_RECORD_FIELDS = frozenset({"expected_uid", "phase"}) | frozenset(_EVIDENCE_FIELDS) | frozenset(_IDENTITY_FIELDS)
_OPERATOR_DEPLOYMENT_FIELDS = frozenset(
    {
        "namespace",
        "name",
        "uid",
        "discovery_method",
        "captured_at",
        "csv",
        "mch_teardown_key",
        "mch_expected_uid",
    }
)
_CSV_FIELDS = frozenset({"namespace", "name", "uid", "owned_crd"})
_IDENTITY_UNAVAILABLE_FIELDS = frozenset(
    {
        "reason",
        "discovery_method",
        "captured_at",
        "evidence_summary",
        "mch_teardown_key",
        "mch_expected_uid",
    }
)


@dataclass(frozen=True)
class AbsenceProof:
    """One positive-absence final-proof predicate. Exactly two required fields."""

    proof_type: str
    resource_key: str


@dataclass(frozen=True)
class TeardownRecord:
    """One durable teardown record, keyed by its canonical resource key."""

    key: str
    expected_uid: str
    phase: TeardownPhase
    # The three completion-evidence fields. `None` means the field is ABSENT;
    # `{}` means the field is PRESENT and empty. The distinction is load-bearing
    # (section 10.2.1a) and is why these are not `field(default_factory=dict)`.
    observed_at: Optional[str] = None
    resource_versions: Optional[Dict[str, str]] = None
    absence_proofs: Optional[Dict[str, AbsenceProof]] = None
    operator_deployment: Optional[dict] = None
    operator_identity_unavailable: Optional[dict] = None


# -- key grammar ---------------------------------------------------------------


def teardown_key(api_version: str, kind: str, namespace: Optional[str], name: str) -> str:
    """Build a canonical `<apiVersion>/<kind>/<namespace>/<name>` record key.

    The namespace segment is empty for a cluster-scoped object.
    """
    return "/".join((api_version, kind, namespace or "", name))


def split_resource_key(key: str) -> Optional[Tuple[str, str, str, str]]:
    """Right-split a resource key into (api_version, kind, namespace, name).

    Returns None when the key is malformed: the right-split must yield exactly
    four segments; api_version must be non-empty and carry at most one "/";
    kind and name must be non-empty. The namespace segment may be empty, which
    denotes a cluster-scoped object. This is the only implementation of the
    grammar -- every other rule that parses a key calls it.
    """
    if not isinstance(key, str):
        return None
    parts = key.rsplit("/", 3)
    if len(parts) != 4:
        return None
    api_version, kind, namespace, name = parts
    if not api_version or api_version.count("/") > 1:
        return None
    if not kind or not name:
        return None
    return api_version, kind, namespace, name


def teardown_kind(key: str) -> str:
    """The kind segment of a record key, or "" when the key is malformed."""
    parts = split_resource_key(key)
    return parts[1] if parts else ""


# -- validation ----------------------------------------------------------------


def _fail(message: str) -> NoReturn:
    raise MalformedTeardownRecord(message)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _require_non_empty_strings(shape: dict, fields, label: str) -> None:
    for name in sorted(fields):
        if not _non_empty_str(shape.get(name)):
            _fail(f"{label}.{name} must be a non-empty string")


def validate(record: TeardownRecord, previous: Optional[TeardownRecord] = None) -> None:
    """Validate a value-typed record against plan sections 10.2.1a-10.2.4.

    `previous` is the currently stored record, if any, so the immutability
    rules are checked in the same place as everything else. Raises
    MalformedTeardownRecord.
    """
    if not isinstance(record, TeardownRecord):
        _fail(f"teardown record must be a TeardownRecord, got {type(record).__name__}")
    parts = split_resource_key(record.key)
    if parts is None:
        _fail(f"malformed teardown record key: {record.key!r}")
    kind = parts[1]
    if not _non_empty_str(record.expected_uid):
        _fail(f"teardown record {record.key!r} needs a non-empty expected_uid")
    if not isinstance(record.phase, TeardownPhase):
        _fail(f"teardown record {record.key!r} has an unknown phase: {record.phase!r}")
    _validate_identity(record, kind)
    _validate_evidence(record, kind)
    _validate_immutability(record, previous)


def _validate_evidence(record: TeardownRecord, kind: str) -> None:
    """Section 10.2.1a-10.2.1e: completion evidence exists only at `completed`."""
    present = [name for name in _EVIDENCE_FIELDS if getattr(record, name) is not None]
    if record.phase is not TeardownPhase.COMPLETED:
        if present:
            _fail(f"completion evidence {sorted(present)} is not permitted at phase {record.phase.value}")
        return

    missing = [name for name in _EVIDENCE_FIELDS if getattr(record, name) is None]
    if missing:
        _fail(f"completed record {record.key!r} is missing completion evidence {sorted(missing)}")
    if not _non_empty_str(record.observed_at):
        _fail(f"observed_at must be a non-empty string, got {record.observed_at!r}")
    if not isinstance(record.resource_versions, dict):
        _fail(f"resource_versions must be a mapping, got {type(record.resource_versions).__name__}")
    if not isinstance(record.absence_proofs, dict):
        _fail(f"absence_proofs must be a mapping, got {type(record.absence_proofs).__name__}")
    _validate_resource_versions(record)
    _validate_absence_proofs(record, kind)
    _validate_evidence_key_sets(record, kind)


def _validate_resource_versions(record: TeardownRecord) -> None:
    """Section 10.2.1b: a closed label set mapping to non-empty string revisions.

    A resourceVersion is opaque and server-defined, so no validator can decide
    from the value alone that a string is a genuine revision (amendment
    section 22.1). The two checkable properties are the closed key set and the
    value type; provenance is bound by the producer-seam tests.
    """
    for label, value in record.resource_versions.items():
        if label not in RESOURCE_VERSION_LABELS:
            _fail(f"resource_versions carries an unknown label {label!r}")
        if not _non_empty_str(value):
            _fail(f"resource_versions[{label!r}] must be a non-empty string, got {value!r}")


def _validate_absence_proofs(record: TeardownRecord, kind: str) -> None:
    """Section 10.2.1c: typed positive-absence evidence with a required key."""
    for proof_key, proof in record.absence_proofs.items():
        if proof_key not in ABSENCE_PROOF_KEYS:
            _fail(f"absence_proofs carries an unknown key {proof_key!r}")
        if not isinstance(proof, AbsenceProof):
            _fail(f"absence_proofs[{proof_key!r}] must be an AbsenceProof")
        if not _non_empty_str(proof.proof_type) or not _non_empty_str(proof.resource_key):
            _fail(f"absence_proofs[{proof_key!r}] fields must be non-empty strings")
        if proof.proof_type not in ABSENCE_PROOF_TYPES:
            _fail(f"absence_proofs[{proof_key!r}] has an unknown proof_type {proof.proof_type!r}")
        if proof.proof_type not in ABSENCE_PROOF_TYPES_BY_KEY[proof_key]:
            _fail(f"proof_type {proof.proof_type!r} is not permitted for absence_proofs[{proof_key!r}]")
        if split_resource_key(proof.resource_key) is None:
            _fail(f"absence_proofs[{proof_key!r}] resource_key is malformed: {proof.resource_key!r}")
        required = _required_absence_resource_key(record, kind, proof_key)
        if proof.resource_key != required:
            _fail(f"absence_proofs[{proof_key!r}] resource_key must be {required!r}, " f"got {proof.resource_key!r}")


def _required_absence_resource_key(record: TeardownRecord, kind: str, proof_key: str) -> str:
    if proof_key == "target_cr":
        return record.key
    drain_namespace = DRAIN_NAMESPACE_BY_KIND.get(kind)
    if drain_namespace is None:
        _fail(f"kind {kind!r} has no drain scope, so it may not carry a drain_namespace proof")
    return teardown_key(NAMESPACE_API_VERSION, NAMESPACE_KIND, None, drain_namespace)


def _validate_evidence_key_sets(record: TeardownRecord, kind: str) -> None:
    """Section 10.2.1d: the recorded evidence key set, per family and proof mode."""
    revisions = set(record.resource_versions)
    proofs = set(record.absence_proofs)
    if "target_cr" not in proofs:
        _fail(f"completed record {record.key!r} must carry a target_cr absence proof")

    if kind not in DRAIN_SCOPED_KINDS:
        expected_revisions = set()
        expected_proofs = {"target_cr"}
    else:
        namespace_present = "drain_namespace" in revisions
        namespace_absent = "drain_namespace" in proofs
        if namespace_present == namespace_absent:
            _fail(
                f"completed record {record.key!r} must record drain_namespace in exactly one of "
                "resource_versions (namespace present) or absence_proofs (namespace absent)"
            )
        if namespace_present:
            expected_revisions = {"drain_namespace", "drain_pods"}
            if record.operator_deployment is not None:
                expected_revisions.add("operator_deployment")
            expected_proofs = {"target_cr"}
        else:
            expected_revisions = set()
            expected_proofs = {"target_cr", "drain_namespace"}

    if revisions != expected_revisions:
        _fail(f"resource_versions keys must be {sorted(expected_revisions)}, got {sorted(revisions)}")
    if proofs != expected_proofs:
        _fail(f"absence_proofs keys must be {sorted(expected_proofs)}, got {sorted(proofs)}")


def _validate_identity(record: TeardownRecord, kind: str) -> None:
    """Sections 10.2.2-10.2.4: exactly one identity outcome, and only on MCH."""
    deployment = record.operator_deployment
    unavailable = record.operator_identity_unavailable
    if kind not in IDENTITY_BEARING_KINDS:
        if deployment is not None or unavailable is not None:
            _fail(f"kind {kind!r} may not carry an operator identity field")
        return
    if (deployment is None) == (unavailable is None):
        _fail(f"record {record.key!r} must carry exactly one of operator_deployment / " "operator_identity_unavailable")
    if deployment is not None:
        _validate_operator_deployment(record, deployment)
    else:
        _validate_identity_unavailable(record, unavailable)


def _validate_operator_deployment(record: TeardownRecord, identity: Any) -> None:
    """Section 10.2.2 exact nested schema."""
    if not isinstance(identity, dict):
        _fail(f"operator_deployment must be a mapping, got {type(identity).__name__}")
    if set(identity) != _OPERATOR_DEPLOYMENT_FIELDS:
        _fail(f"operator_deployment fields must be exactly {sorted(_OPERATOR_DEPLOYMENT_FIELDS)}")
    _require_non_empty_strings(identity, {"namespace", "name", "uid", "captured_at"}, "operator_deployment")
    if identity["discovery_method"] != OPERATOR_IDENTITY_DISCOVERY_METHOD:
        _fail(f"operator_deployment.discovery_method must be {OPERATOR_IDENTITY_DISCOVERY_METHOD!r}")

    csv = identity["csv"]
    if not isinstance(csv, dict):
        _fail(f"operator_deployment.csv must be a mapping, got {type(csv).__name__}")
    if set(csv) != _CSV_FIELDS:
        _fail(f"operator_deployment.csv fields must be exactly {sorted(_CSV_FIELDS)}")
    _require_non_empty_strings(csv, _CSV_FIELDS, "operator_deployment.csv")
    if csv["namespace"] != identity["namespace"]:
        _fail("operator_deployment.csv.namespace must equal operator_deployment.namespace")
    if csv["owned_crd"] != MCH_OWNED_CRD:
        _fail(f"operator_deployment.csv.owned_crd must be {MCH_OWNED_CRD!r}")

    _validate_identity_backrefs(record, identity, "operator_deployment")


def _validate_identity_unavailable(record: TeardownRecord, unavailable: Any) -> None:
    """Section 10.2.3 exact nested schema."""
    if not isinstance(unavailable, dict):
        _fail(f"operator_identity_unavailable must be a mapping, got {type(unavailable).__name__}")
    if set(unavailable) != _IDENTITY_UNAVAILABLE_FIELDS:
        _fail(f"operator_identity_unavailable fields must be exactly {sorted(_IDENTITY_UNAVAILABLE_FIELDS)}")
    _require_non_empty_strings(
        unavailable,
        {"reason", "captured_at", "evidence_summary"},
        "operator_identity_unavailable",
    )
    if unavailable["reason"] not in OPERATOR_IDENTITY_UNAVAILABLE_REASONS:
        _fail(f"operator_identity_unavailable.reason {unavailable['reason']!r} is not an enumerated reason")
    if unavailable["discovery_method"] != OPERATOR_IDENTITY_DISCOVERY_METHOD:
        _fail(f"operator_identity_unavailable.discovery_method must be {OPERATOR_IDENTITY_DISCOVERY_METHOD!r}")

    _validate_identity_backrefs(record, unavailable, "operator_identity_unavailable")


def _validate_identity_backrefs(record: TeardownRecord, shape: dict, label: str) -> None:
    if shape["mch_teardown_key"] != record.key:
        _fail(f"{label}.mch_teardown_key must equal the record key {record.key!r}")
    if shape["mch_expected_uid"] != record.expected_uid:
        _fail(f"{label}.mch_expected_uid must equal the record expected_uid {record.expected_uid!r}")


def _validate_immutability(record: TeardownRecord, previous: Optional[TeardownRecord]) -> None:
    """Section 10.2.4: the first expected_uid stands, and completed evidence is final."""
    if previous is None:
        return
    if previous.expected_uid != record.expected_uid:
        _fail(
            f"expected_uid for {record.key!r} is already bound to {previous.expected_uid!r} "
            f"and may not be rebound to {record.expected_uid!r}"
        )
    if previous.phase is not TeardownPhase.COMPLETED:
        return
    for name in _EVIDENCE_FIELDS:
        if getattr(previous, name) != getattr(record, name):
            _fail(f"{name} of the completed record {record.key!r} may not be changed")


# -- serialization -------------------------------------------------------------


def to_stored(record: TeardownRecord) -> dict:
    """Serialize a validated record, omitting absent fields and keeping `{}`.

    An absent completion-evidence field is omitted entirely; a present-and-empty
    one is written as `{}` (section 10.2.1a). `validate_stored` inverts this.
    """
    stored: Dict[str, Any] = {"expected_uid": record.expected_uid, "phase": record.phase.value}
    if record.observed_at is not None:
        stored["observed_at"] = record.observed_at
    if record.resource_versions is not None:
        stored["resource_versions"] = dict(record.resource_versions)
    if record.absence_proofs is not None:
        stored["absence_proofs"] = {
            proof_key: {"proof_type": proof.proof_type, "resource_key": proof.resource_key}
            for proof_key, proof in record.absence_proofs.items()
        }
    for name in _IDENTITY_FIELDS:
        value = getattr(record, name)
        if value is not None:
            stored[name] = value
    return stored


def validate_stored(key: str, stored: Any) -> TeardownRecord:
    """Validate a raw stored mapping and return the record.

    This is the serialization-level entry point: it enforces the closed
    top-level field set, absent-versus-empty (section 10.2.1a), and the exact
    absence-proof field set before any value type exists, then delegates every
    remaining rule to `validate`. Raises MalformedTeardownRecord.
    """
    if not isinstance(stored, dict):
        _fail(f"teardown record {key!r} must be a mapping, got {type(stored).__name__}")
    unknown = set(stored) - _RECORD_FIELDS
    if unknown:
        _fail(f"teardown record {key!r} carries unknown fields {sorted(unknown)}")
    record = TeardownRecord(
        key=key,
        expected_uid=stored.get("expected_uid"),
        phase=_decode_phase(key, stored),
        observed_at=_decode_optional(key, stored, "observed_at"),
        resource_versions=_decode_optional(key, stored, "resource_versions"),
        absence_proofs=_decode_absence_proofs(key, stored),
        operator_deployment=_decode_optional(key, stored, "operator_deployment"),
        operator_identity_unavailable=_decode_optional(key, stored, "operator_identity_unavailable"),
    )
    validate(record)
    return record


def _decode_phase(key: str, stored: dict) -> TeardownPhase:
    raw = stored.get("phase")
    try:
        return TeardownPhase(raw)
    except (ValueError, TypeError):
        _fail(f"teardown record {key!r} has an unknown phase: {raw!r}")


def _decode_optional(key: str, stored: dict, name: str) -> Any:
    """An omitted key loads as None; a stored null is malformed, not absent."""
    if name not in stored:
        return None
    value = stored[name]
    if value is None:
        _fail(f"teardown record {key!r} stores {name} as null; an absent field is not a null one")
    return value


def _decode_absence_proofs(key: str, stored: dict) -> Any:
    raw = _decode_optional(key, stored, "absence_proofs")
    if not isinstance(raw, dict):
        # Absent, or the wrong type: `validate` reports the type error.
        return raw
    decoded = {}
    for proof_key, entry in raw.items():
        if not isinstance(entry, dict) or set(entry) != _ABSENCE_PROOF_FIELDS:
            _fail(f"absence_proofs[{proof_key!r}] fields must be exactly {sorted(_ABSENCE_PROOF_FIELDS)}")
        decoded[proof_key] = AbsenceProof(proof_type=entry["proof_type"], resource_key=entry["resource_key"])
    return decoded
