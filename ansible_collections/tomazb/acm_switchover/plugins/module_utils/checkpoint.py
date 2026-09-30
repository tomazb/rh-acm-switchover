# SPDX-License-Identifier: MIT
"""Shared checkpoint schema helpers for the acm_switchover collection."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import NoReturn

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
    ABSENCE_PROOF_KEYS,
    ABSENCE_PROOF_TYPES_BY_KEY,
    DRAIN_NAMESPACE_BY_KIND,
    DRAIN_SCOPED_KINDS,
    IDENTITY_BEARING_KINDS,
    MCH_OWNED_CRD,
    NAMESPACE_API_VERSION,
    NAMESPACE_KIND,
    OPERATOR_IDENTITY_DISCOVERY_METHOD,
    OPERATOR_IDENTITY_UNAVAILABLE_REASONS,
    RESOURCE_VERSION_LABELS,
    TEARDOWN_PHASES,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_evidence import (
    MigrationEvidenceError,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.migration_journal import (
    validate_journal_transition,
    validate_migration_journal,
)

SCHEMA_VERSION = "2.0"
KNOWN_PHASES = (
    "preflight",
    "primary_prep",
    "activation",
    "post_activation",
    "finalization",
    # Decommission is a separate entry point, not a switchover phase, but it needs a
    # durable phase so the identity map is persisted before the first DELETE. LAST is
    # semantically required: reset_completed_phases_from prunes the named phase and
    # everything downstream of it, and decommission is downstream of finalization.
    "decommission",
)

# `update` is the mid-phase operational-data write (R4-04 amendment AC26): it moves no
# phase marker, so it is not a phase transition.
CHECKPOINT_VALID_STATUSES = frozenset({"enter", "pass", "fail", "reset", "update"})
CHECKPOINT_BACKEND_FILE = "file"
CHECKPOINT_DEFAULT_PATH = ".state/checkpoint.json"
CHECKPOINT_REPORT_KIND_JSON = "json-report"
LEGACY_OPERATION_IDENTITY_FIELDS = frozenset({"primary_kubeconfig", "secondary_kubeconfig"})


class CheckpointIdentityMismatch(ValueError):
    """Raised when a checkpoint belongs to a different switchover operation."""


def build_operation_identity(
    hubs: dict,
    operation: dict,
    collection_version: str | None = None,
    hub_identities: dict | None = None,
) -> dict:
    """Build a stable identity payload for the current switchover operation."""
    primary = hubs.get("primary") or {}
    secondary = hubs.get("secondary") or {}
    identities = hub_identities or {}
    primary_identity = identities.get("primary") or {}
    secondary_identity = identities.get("secondary") or {}
    restore_only = operation.get("restore_only")
    _restore_only = False if restore_only is None else restore_only
    return {
        "primary_context": primary.get("context") or "",
        "secondary_context": secondary.get("context") or "",
        "primary_cluster_uid": primary.get("cluster_uid") or primary_identity.get("cluster_uid") or "",
        "secondary_cluster_uid": secondary.get("cluster_uid") or secondary_identity.get("cluster_uid") or "",
        "method": operation.get("method") or ("full" if _restore_only else "passive"),
        "activation_method": operation.get("activation_method") or "patch",
        "restore_only": _restore_only,
        "old_hub_action": operation.get("old_hub_action") or ("none" if _restore_only else "secondary"),
        "collection_version": collection_version or "",
    }


def normalize_operation_identity(identity: dict) -> dict:
    """Drop legacy sensitive fields before comparing or persisting identities."""
    if not isinstance(identity, dict):
        return {}
    return {key: value for key, value in identity.items() if key not in LEGACY_OPERATION_IDENTITY_FIELDS}


def build_checkpoint_record(phase: str, operational_data: dict, operation_identity: dict | None = None) -> dict:
    """Return a fresh checkpoint record dict for the given phase."""
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "completed_phases": [],
        "operational_data": operational_data,
        "operation_identity": operation_identity,
        "errors": [],
        "report_refs": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def validate_operation_identity(checkpoint: dict, expected_identity: dict, *, allow_missing: bool = False) -> bool:
    """Validate that a checkpoint belongs to the expected switchover operation."""
    actual_identity = checkpoint.get("operation_identity")
    if actual_identity is None:
        if allow_missing:
            return False
        raise CheckpointIdentityMismatch("Checkpoint is missing operation identity.")
    if normalize_operation_identity(actual_identity) != normalize_operation_identity(expected_identity):
        raise CheckpointIdentityMismatch("Checkpoint operation identity does not match the current execution.")
    return True


def reset_completed_phases_from(completed_phases: list[str], phase: str) -> list[str]:
    """Prune the requested phase and every downstream phase from the completed phase list."""
    if phase not in KNOWN_PHASES:
        raise ValueError(f"Unknown checkpoint phase '{phase}'.")
    phases_to_reset = set(KNOWN_PHASES[KNOWN_PHASES.index(phase) :])
    return [completed_phase for completed_phase in completed_phases if completed_phase not in phases_to_reset]


def is_unsafe_legacy_checkpoint(checkpoint: dict) -> bool:
    """Return True when a legacy schema 1.0 checkpoint has completed phases to prune."""
    return checkpoint.get("schema_version") == "1.0" and bool(checkpoint.get("completed_phases"))


def should_resume_phase(checkpoint: dict, phase: str) -> bool:
    """Return True if the phase still needs to run, False if already completed."""
    return phase not in checkpoint.get("completed_phases", [])


# Named-operation vocabulary over checkpoint operational_data (issue #214).
# This module owns the key literals: roles and playbooks read the flattened
# `facts` returned by checkpoint_phase, never raw operational_data keys
# (guardrail: tests/unit/test_checkpoint_vocabulary_guardrail.py). The KEY_*
# constants are stable surface for plugins and for the cross-runtime parity
# tests; the raw string literals must not be duplicated outside this module.
KEY_ARGOCD_RUN_ID = "argocd_run_id"
KEY_ARGOCD_DISCOVERY_NAMESPACES = "argocd_discovery_namespaces"
KEY_AUTO_IMPORT_STRATEGY_CHANGED = "auto_import_strategy_changed"
KEY_EXPECTED_MANAGED_CLUSTER_NAMES = "expected_managed_cluster_names"
KEY_EXPECTED_MANAGED_CLUSTER_COUNT = "expected_managed_cluster_count"
KEY_PRIMARY_HAS_OBSERVABILITY = "primary_has_observability"
KEY_SECONDARY_HAS_OBSERVABILITY = "secondary_has_observability"
KEY_SAVED_BACKUP_SCHEDULE = "saved_backup_schedule"
KEY_BACKUP_SCHEDULE_ENABLED_AT = "backup_schedule_enabled_at"
KEY_RESUME_SUMMARY = "resume_summary"
KEY_RESUME_START_PHASE = "resume_start_phase"
FACT_TEARDOWN_RECORDS = "teardown_records"


def _operational_data(checkpoint) -> dict:
    if not isinstance(checkpoint, dict):
        return {}
    data = checkpoint.get("operational_data")
    return data if isinstance(data, dict) else {}


# On the collection's ansible-core floor (2.15-2.18, jinja2_native off) folded
# set_fact scalars are stringified before they reach checkpoint_phase, so a
# checkpoint written there can carry "2" or "True" where 2.19+ writes native
# types. The coercions below accept exactly Ansible's own boolean vocabulary
# and digit strings; anything else degrades to the never-recorded default.
_BOOL_TRUE_STRINGS = frozenset({"true", "yes", "on", "1"})
_BOOL_FALSE_STRINGS = frozenset({"false", "no", "off", "0"})


def _coerce_bool(value):
    """Return a real bool for bools and Ansible-stringified bools, else None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUE_STRINGS:
            return True
        if lowered in _BOOL_FALSE_STRINGS:
            return False
    return None


def _coerce_count(value):
    """Return a non-negative int for ints and digit strings, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def checkpoint_facts(checkpoint) -> dict:
    """Flattened named view of a checkpoint's cross-phase facts.

    Malformed or missing shapes degrade to defaults (same tolerance model as
    the Python CLI's RunSummary.from_snapshot). Values that roles must be able
    to distinguish as never-recorded stay None.
    """
    data = _operational_data(checkpoint)
    namespaces = data.get(KEY_ARGOCD_DISCOVERY_NAMESPACES)
    saved_schedule = data.get(KEY_SAVED_BACKUP_SCHEDULE)
    resume_summary = data.get(KEY_RESUME_SUMMARY)
    if not isinstance(resume_summary, dict):
        resume_summary = {}
    names = data.get(KEY_EXPECTED_MANAGED_CLUSTER_NAMES)
    count = data.get(KEY_EXPECTED_MANAGED_CLUSTER_COUNT)
    primary_obs = data.get(KEY_PRIMARY_HAS_OBSERVABILITY)
    secondary_obs = data.get(KEY_SECONDARY_HAS_OBSERVABILITY)
    validated_teardown_records = (
        teardown_records(checkpoint)
        if isinstance(checkpoint, dict) and isinstance(checkpoint.get("operational_data", {}), dict)
        else {}
    )
    return {
        KEY_ARGOCD_RUN_ID: data.get(KEY_ARGOCD_RUN_ID) or "",
        KEY_ARGOCD_DISCOVERY_NAMESPACES: (namespaces if isinstance(namespaces, dict) else {}),
        # Ansible's own boolean vocabulary coerces; anything else — e.g. a
        # hand-edited "banana" — degrades to False, never truthy: this flag
        # feeds finalization's legacy discharge branch, which deletes the
        # auto-import ConfigMap. The string forms exist because the ansible-core
        # floor stringifies folded scalars (see _coerce_bool).
        KEY_AUTO_IMPORT_STRATEGY_CHANGED: _coerce_bool(data.get(KEY_AUTO_IMPORT_STRATEGY_CHANGED, False)) is True,
        # None means never recorded; wrong-typed values degrade to None so the
        # roles' `is not none` guards treat them as never recorded.
        KEY_EXPECTED_MANAGED_CLUSTER_NAMES: names if isinstance(names, list) else None,
        KEY_EXPECTED_MANAGED_CLUSTER_COUNT: _coerce_count(count),
        KEY_PRIMARY_HAS_OBSERVABILITY: _coerce_bool(primary_obs),
        KEY_SECONDARY_HAS_OBSERVABILITY: _coerce_bool(secondary_obs),
        KEY_SAVED_BACKUP_SCHEDULE: (saved_schedule if isinstance(saved_schedule, dict) else None),
        KEY_BACKUP_SCHEDULE_ENABLED_AT: data.get(KEY_BACKUP_SCHEDULE_ENABLED_AT) or "",
        KEY_RESUME_START_PHASE: resume_summary.get(KEY_RESUME_START_PHASE) or "",
        # Teardown records are mutation authority. Unlike the reporting values above,
        # malformed state must fail closed through the shared record validator.
        FACT_TEARDOWN_RECORDS: validated_teardown_records,
    }


def record_resume_start_phase(checkpoint: dict, phase: str) -> None:
    """Record where this resumed run starts. Replace semantics — parity with
    Python RunRecord.record_resume_start_phase (last resume wins)."""
    data = checkpoint.get("operational_data")
    if not isinstance(data, dict):
        data = {}
        checkpoint["operational_data"] = data
    data[KEY_RESUME_SUMMARY] = {KEY_RESUME_START_PHASE: phase}


# -- R4-03 durable decommission teardown records (plan §10.2) ------------------
#
# The independent collection-side implementation of the schema lib/teardown_record.py
# owns on the Python side. No runtime code is shared between the two: they are held
# equal by tests/test_checkpoint_state_parity.py and tests/test_constants_parity.py.
#
# A teardown record is mutation authority, so every rule fails closed by raising
# MalformedTeardownRecord. Nothing here repairs, defaults, or degrades a stored shape
# — the deliberate opposite of checkpoint_facts, which is only a reporting view.
#
# `operator_deployment` and `operator_identity_unavailable` are validated here but
# written by a later PR, so no producer can persist a shape this reader does not check.
#
# Durability is the caller's: this module only edits the checkpoint mapping. The
# checkpoint action plugin owns the write to disk, which is where "forced durable
# before DELETE" is discharged.

KEY_DECOMMISSION_TEARDOWN_RECORDS = "decommission_teardown_records"

_EVIDENCE_FIELDS = ("observed_at", "resource_versions", "absence_proofs")
_IDENTITY_FIELDS = ("operator_deployment", "operator_identity_unavailable")
_ABSENCE_PROOF_FIELDS = frozenset({"proof_type", "resource_key"})
# The closed top-level field set of a stored record. An unknown field is malformed:
# the record is mutation authority, so no later producer may write a shape the reader
# does not check.
_RECORD_FIELDS = frozenset({"expected_uid", "phase"}) | frozenset(_EVIDENCE_FIELDS) | frozenset(_IDENTITY_FIELDS)
_OPTIONAL_FIELDS = _EVIDENCE_FIELDS + _IDENTITY_FIELDS
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


class MalformedTeardownRecord(ValueError):
    """A teardown record violates plan §10.2. Fail closed."""


def teardown_key(api_version: str, kind: str, namespace, name: str) -> str:
    """Build a canonical `<apiVersion>/<kind>/<namespace>/<name>` record key.

    The namespace segment is empty for a cluster-scoped object.
    """
    return "/".join((api_version, kind, namespace or "", name))


def split_resource_key(key):
    """Right-split a resource key into (api_version, kind, namespace, name).

    Returns None when the key is malformed: the right-split must yield exactly
    four segments; api_version must be non-empty and carry at most one "/";
    kind and name must be non-empty. The namespace segment may be empty, which
    denotes a cluster-scoped object. This is the only implementation of the
    grammar — every other rule that parses a key calls it.
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


def _fail(message: str) -> NoReturn:
    raise MalformedTeardownRecord(message)


def _non_empty_str(value) -> bool:
    return isinstance(value, str) and value != ""


def _require_non_empty_strings(shape: dict, fields, label: str) -> None:
    for name in sorted(fields):
        if not _non_empty_str(shape.get(name)):
            _fail(f"{label}.{name} must be a non-empty string")


def validate_stored(key: str, stored, previous=None) -> dict:
    """Validate one stored teardown record against plan §10.2.1a-§10.2.4.

    This is the single collection-side rule set: both the reader and the writer
    call it, so a hand-edited checkpoint fails exactly where a bad write would.
    `previous` is the currently stored record, if any — the writer passes it so
    the immutability rules are checked in the same place as everything else, and
    it is validated in its own right before anything is compared against it.

    Returns a deep copy of the validated record. Raises MalformedTeardownRecord.
    """
    if not isinstance(stored, dict):
        _fail(f"teardown record {key!r} must be a mapping, got {type(stored).__name__}")
    unknown = set(stored) - _RECORD_FIELDS
    if unknown:
        _fail(f"teardown record {key!r} carries unknown fields {sorted(unknown)}")
    for name in _OPTIONAL_FIELDS:
        # An omitted key means ABSENT. A stored null is a third representation the
        # reader must not silently accept as absence (§10.2.1a).
        if name in stored and stored[name] is None:
            _fail(f"teardown record {key!r} stores {name} as null; an absent field is not a null one")

    parts = split_resource_key(key)
    if parts is None:
        _fail(f"malformed teardown record key: {key!r}")
    kind = parts[1]
    if not _non_empty_str(stored.get("expected_uid")):
        _fail(f"teardown record {key!r} needs a non-empty expected_uid")
    phase = stored.get("phase")
    if not isinstance(phase, str) or phase not in TEARDOWN_PHASES:
        _fail(f"teardown record {key!r} has an unknown phase: {phase!r}")

    _validate_identity(key, stored, kind)
    _validate_evidence(key, stored, kind)
    _validate_immutability(key, stored, previous)
    return copy.deepcopy(stored)


def _validate_evidence(key: str, stored: dict, kind: str) -> None:
    """§10.2.1a-§10.2.1e: completion evidence exists only at `completed`."""
    present = [name for name in _EVIDENCE_FIELDS if name in stored]
    if stored["phase"] != "completed":
        if present:
            _fail(f"completion evidence {sorted(present)} is not permitted at phase {stored['phase']}")
        return

    missing = [name for name in _EVIDENCE_FIELDS if name not in stored]
    if missing:
        _fail(f"completed record {key!r} is missing completion evidence {sorted(missing)}")
    observed_at = stored["observed_at"]
    resource_versions = stored["resource_versions"]
    absence_proofs = stored["absence_proofs"]
    if not _non_empty_str(observed_at):
        _fail(f"observed_at must be a non-empty string, got {observed_at!r}")
    if not isinstance(resource_versions, dict):
        _fail(f"resource_versions must be a mapping, got {type(resource_versions).__name__}")
    if not isinstance(absence_proofs, dict):
        _fail(f"absence_proofs must be a mapping, got {type(absence_proofs).__name__}")
    _validate_resource_versions(resource_versions)
    _validate_absence_proofs(key, absence_proofs, kind)
    _validate_evidence_key_sets(key, stored, kind)


def _validate_resource_versions(resource_versions: dict) -> None:
    """§10.2.1b: a closed label set mapping to non-empty string revisions.

    A resourceVersion is opaque and server-defined, so no validator can decide
    from the value alone that a string is a genuine revision (amendment §22.1).
    The two checkable properties are the closed key set and the value type;
    provenance is bound by the producer-seam tests.
    """
    for label, value in resource_versions.items():
        if label not in RESOURCE_VERSION_LABELS:
            _fail(f"resource_versions carries an unknown label {label!r}")
        if not _non_empty_str(value):
            _fail(f"resource_versions[{label!r}] must be a non-empty string, got {value!r}")


def _validate_absence_proofs(key: str, absence_proofs: dict, kind: str) -> None:
    """§10.2.1c: typed positive-absence evidence with a required resource key."""
    for proof_key, proof in absence_proofs.items():
        if proof_key not in ABSENCE_PROOF_KEYS:
            _fail(f"absence_proofs carries an unknown key {proof_key!r}")
        if not isinstance(proof, dict) or set(proof) != _ABSENCE_PROOF_FIELDS:
            _fail(f"absence_proofs[{proof_key!r}] fields must be exactly {sorted(_ABSENCE_PROOF_FIELDS)}")
        if not _non_empty_str(proof["proof_type"]) or not _non_empty_str(proof["resource_key"]):
            _fail(f"absence_proofs[{proof_key!r}] fields must be non-empty strings")
        if proof["proof_type"] not in ABSENCE_PROOF_TYPES_BY_KEY[proof_key]:
            _fail(f"proof_type {proof['proof_type']!r} is not permitted for absence_proofs[{proof_key!r}]")
        if split_resource_key(proof["resource_key"]) is None:
            _fail(f"absence_proofs[{proof_key!r}] resource_key is malformed: {proof['resource_key']!r}")
        required = _required_absence_resource_key(key, kind, proof_key)
        if proof["resource_key"] != required:
            _fail(f"absence_proofs[{proof_key!r}] resource_key must be {required!r}, got {proof['resource_key']!r}")


def _required_absence_resource_key(key: str, kind: str, proof_key: str) -> str:
    if proof_key == "target_cr":
        return key
    drain_namespace = DRAIN_NAMESPACE_BY_KIND.get(kind)
    if drain_namespace is None:
        _fail(f"kind {kind!r} has no drain scope, so it may not carry a drain_namespace proof")
    return teardown_key(NAMESPACE_API_VERSION, NAMESPACE_KIND, None, drain_namespace)


def _validate_evidence_key_sets(key: str, stored: dict, kind: str) -> None:
    """§10.2.1d: the recorded evidence key set, per family and proof mode."""
    revisions = set(stored["resource_versions"])
    proofs = set(stored["absence_proofs"])
    if "target_cr" not in proofs:
        _fail(f"completed record {key!r} must carry a target_cr absence proof")

    if kind not in DRAIN_SCOPED_KINDS:
        expected_revisions = set()
        expected_proofs = {"target_cr"}
    else:
        namespace_present = "drain_namespace" in revisions
        namespace_absent = "drain_namespace" in proofs
        if namespace_present == namespace_absent:
            _fail(
                f"completed record {key!r} must record drain_namespace in exactly one of "
                "resource_versions (namespace present) or absence_proofs (namespace absent)"
            )
        if namespace_present:
            expected_revisions = {"drain_namespace", "drain_pods"}
            if "operator_deployment" in stored:
                expected_revisions.add("operator_deployment")
            expected_proofs = {"target_cr"}
        else:
            expected_revisions = set()
            expected_proofs = {"target_cr", "drain_namespace"}

    if revisions != expected_revisions:
        _fail(f"resource_versions keys must be {sorted(expected_revisions)}, got {sorted(revisions)}")
    if proofs != expected_proofs:
        _fail(f"absence_proofs keys must be {sorted(expected_proofs)}, got {sorted(proofs)}")


def _validate_identity(key: str, stored: dict, kind: str) -> None:
    """§10.2.2-§10.2.4: exactly one identity outcome, and only on an MCH record."""
    deployment = stored.get("operator_deployment")
    unavailable = stored.get("operator_identity_unavailable")
    if kind not in IDENTITY_BEARING_KINDS:
        if deployment is not None or unavailable is not None:
            _fail(f"kind {kind!r} may not carry an operator identity field")
        return
    if (deployment is None) == (unavailable is None):
        _fail(f"record {key!r} must carry exactly one of operator_deployment / operator_identity_unavailable")
    if deployment is not None:
        _validate_operator_deployment(key, stored, deployment)
    else:
        _validate_identity_unavailable(key, stored, unavailable)


def _validate_operator_deployment(key: str, stored: dict, identity) -> None:
    """§10.2.2 exact nested schema."""
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

    _validate_identity_backrefs(key, stored, identity, "operator_deployment")


def _validate_identity_unavailable(key: str, stored: dict, unavailable) -> None:
    """§10.2.3 exact nested schema."""
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

    _validate_identity_backrefs(key, stored, unavailable, "operator_identity_unavailable")


def _validate_identity_backrefs(key: str, stored: dict, shape: dict, label: str) -> None:
    if shape["mch_teardown_key"] != key:
        _fail(f"{label}.mch_teardown_key must equal the record key {key!r}")
    if shape["mch_expected_uid"] != stored["expected_uid"]:
        _fail(f"{label}.mch_expected_uid must equal the record expected_uid {stored['expected_uid']!r}")


def _validate_immutability(key: str, stored: dict, previous) -> None:
    """§10.2.4 and amendment §13: what a later write may not change.

    The first `expected_uid` stands, a captured identity outcome stands for the
    record's lifetime, and the completion evidence of a completed record stands.
    """
    if previous is None:
        return
    # A corrupt stored record is never laundered by being compared against.
    validate_stored(key, previous)
    if previous["expected_uid"] != stored["expected_uid"]:
        _fail(
            f"expected_uid for {key!r} is already bound to {previous['expected_uid']!r} "
            f"and may not be rebound to {stored['expected_uid']!r}"
        )
    _validate_identity_immutability(key, stored, previous)
    if previous["phase"] != "completed":
        return
    if stored["phase"] != "completed":
        _fail(f"record {key!r} is already completed and may not transition back to {stored['phase']}")
    for name in _EVIDENCE_FIELDS:
        if previous.get(name) != stored.get(name):
            _fail(f"{name} of the completed record {key!r} may not be changed")


def _validate_identity_immutability(key: str, stored: dict, previous: dict) -> None:
    """Amendment §13: a captured operator identity outcome is never rebound.

    `operator_deployment` is immutable for the record's lifetime, and
    `operator_identity_unavailable` is never silently upgraded by rediscovery.
    Both hold at every phase, not only after completion, so a later write must
    carry the identical outcome: the same variant with the same content, compared
    by value — over-rejecting would deadlock the phase machine.
    """
    if all(name not in previous for name in _IDENTITY_FIELDS):
        return
    for name in _IDENTITY_FIELDS:
        if previous.get(name) != stored.get(name):
            _fail(
                f"the operator identity outcome of {key!r} is already captured and "
                f"may not be changed ({name} differs from the recorded one)"
            )


def _stored_records(checkpoint) -> dict:
    """The raw stored records mapping, or `{}` when none was ever written.

    An absent key reads as `{}`. A container that is present but not a mapping —
    including a stored null, `[]`, `""` or `0` — is corruption, and treating it
    as "no records" would silently skip the expected_uid immutability guard, so
    it fails closed. checkpoint_facts' degrade-to-defaults tolerance deliberately
    does not apply here: these records are mutation authority, not a report.
    """
    if not isinstance(checkpoint, dict):
        _fail(f"checkpoint must be a mapping, got {type(checkpoint).__name__}")
    if "operational_data" not in checkpoint:
        return {}
    data = checkpoint["operational_data"]
    if not isinstance(data, dict):
        # A stored null is corruption here too, not "no operational data yet".
        _fail(f"operational_data must be a mapping, got {data!r}")
    if KEY_DECOMMISSION_TEARDOWN_RECORDS not in data:
        return {}
    records = data[KEY_DECOMMISSION_TEARDOWN_RECORDS]
    if not isinstance(records, dict):
        _fail(f"{KEY_DECOMMISSION_TEARDOWN_RECORDS} must be a mapping, got {records!r}")
    return records


def teardown_record(checkpoint, key: str):
    """The recorded teardown for `key`, or None if none was ever recorded.

    A stored record that violates the schema raises rather than degrading: a
    teardown record is mutation authority, not a reporting fact.
    """
    records = _stored_records(checkpoint)
    if key not in records:
        return None
    return validate_stored(key, records[key])


def teardown_records(checkpoint) -> dict:
    """Every recorded teardown, keyed by record key. Any malformed member fails the read."""
    records = _stored_records(checkpoint)
    return {key: validate_stored(key, stored) for key, stored in records.items()}


def record_teardown_phase(
    checkpoint,
    key: str,
    expected_uid: str,
    phase: str,
    *,
    observed_at=None,
    resource_versions=None,
    absence_proofs=None,
    operator_deployment=None,
    operator_identity_unavailable=None,
) -> None:
    """Validate and store one teardown record in the checkpoint's operational_data.

    A `None` argument means the field is ABSENT and is omitted from the stored
    mapping; an empty mapping is stored as `{}` (§10.2.1a). Validation runs
    before the write, so a rejected record leaves the stored one untouched.
    Persisting the checkpoint is the caller's (the action plugin's) job — this
    function only edits the mapping.
    """
    records = _stored_records(checkpoint)
    stored = {"expected_uid": expected_uid, "phase": phase}
    for name, value in (
        ("observed_at", observed_at),
        ("resource_versions", resource_versions),
        ("absence_proofs", absence_proofs),
        ("operator_deployment", operator_deployment),
        ("operator_identity_unavailable", operator_identity_unavailable),
    ):
        if value is not None:
            # Copied, so a caller mutating its own mapping afterwards cannot edit
            # a durable record behind the validator's back.
            stored[name] = copy.deepcopy(value)

    # A record slot present but stored as null is corruption, not "no previous"
    # (controller ruling C14): `records.get(key)` would read it as absent and the
    # expected_uid immutability guard would be skipped on the write path, while the
    # reader refuses to load it. Presence, not the value, decides.
    previous = records[key] if key in records else None
    if key in records and previous is None:
        _fail(f"teardown record {key!r} must be a mapping, got None")
    validate_stored(key, stored, previous=previous)

    data = checkpoint.get("operational_data")
    if not isinstance(data, dict):
        data = {}
        checkpoint["operational_data"] = data
    data[KEY_DECOMMISSION_TEARDOWN_RECORDS] = {**records, key: stored}


# -- R4-04 migration journal (amendment sections 2 and 10) ---------------------
#
# The collection side of the persistence ownership lib/run_record.py holds for the
# Python CLI. Schema and transition rules belong to module_utils/migration_journal.py;
# this module owns only the key, the store-structure check that decides whether a
# decoded checkpoint is readable at all, and the reset/rewind rule. Like the teardown
# records above it only edits the mapping: the action plugin owns the durable write.

KEY_MIGRATION_BACKUPS = "migration_backups"
MIGRATION_JOURNAL_ABSENT = "absent"
MIGRATION_JOURNAL_VALID = "valid"
MIGRATION_JOURNAL_INVALID = "invalid"
# The phases before the first Backup freeze: rewinding to one would start a new
# migration transaction, which only the explicit full checkpoint reset may do.
PRE_FREEZE_PHASES = ("preflight", "primary_prep")


class CheckpointStructureError(ValueError):
    """The decoded checkpoint is not a readable checkpoint store. Fail closed."""


class MigrationRewindRefused(ValueError):
    """A reset_from rewind would discard or launder the migration journal."""


def checkpoint_structure_error(checkpoint) -> str | None:
    """Why a decoded checkpoint is structurally corrupt, or None when it is readable.

    The one structural rule every checkpoint reader already relies on: the top level is
    a JSON object. No key becomes required; the ordinary transition path keeps its
    tolerance of a malformed operational_data value, which enter heals.
    """
    if not isinstance(checkpoint, dict):
        return f"the top level is a JSON {type(checkpoint).__name__}, not an object"
    return None


def _migration_data(checkpoint) -> dict:
    """The operational_data a journal read may trust, or CheckpointStructureError.

    Stricter than checkpoint_structure_error for the same reason _stored_records is:
    the journal is mutation authority, so a present non-mapping container is not read
    as "no journal".
    """
    error = checkpoint_structure_error(checkpoint)
    if error is None and "operational_data" in checkpoint and not isinstance(checkpoint["operational_data"], dict):
        error = f"operational_data is a {type(checkpoint['operational_data']).__name__}, not a mapping"
    if error is not None:
        raise CheckpointStructureError(f"Checkpoint is not readable: {error}.")
    return checkpoint.get("operational_data", {})


def classify_migration_backups(checkpoint) -> tuple:
    """The journal outcome of a readable checkpoint: (absent, None), (valid, journal),
    or (invalid, MigrationEvidenceError).

    Only a missing key is absent; a present key of any value is valid or invalid, never
    absent. The valid journal is a detached copy. Raises CheckpointStructureError when
    the checkpoint is not readable, which is never an absent journal either.
    """
    data = _migration_data(checkpoint)
    if KEY_MIGRATION_BACKUPS not in data:
        return MIGRATION_JOURNAL_ABSENT, None
    try:
        return MIGRATION_JOURNAL_VALID, validate_migration_journal(data[KEY_MIGRATION_BACKUPS])
    except MigrationEvidenceError as exc:
        return MIGRATION_JOURNAL_INVALID, exc


def migration_backups(checkpoint):
    """The validated journal, or None when it was never written. An invalid one raises."""
    outcome, value = classify_migration_backups(checkpoint)
    if outcome == MIGRATION_JOURNAL_INVALID:
        raise value
    return value


def record_migration_backups(checkpoint, candidate) -> None:
    """Validate `candidate` and store it as the one complete journal value.

    The candidate is validated first, then checked as a transition from the strictly
    read stored journal (the freeze-write rule when there is none). A rejected write
    leaves the mapping untouched.
    """
    validate_migration_journal(candidate)
    validate_journal_transition(migration_backups(checkpoint), candidate)
    if "operational_data" not in checkpoint:
        checkpoint["operational_data"] = {}
    checkpoint["operational_data"][KEY_MIGRATION_BACKUPS] = copy.deepcopy(candidate)


def check_migration_rewind(checkpoint, reset_from: str, *, prunes: bool) -> str:
    """Decide whether `reset_from` may run against this checkpoint's journal.

    `prunes` says whether reset_from still names a completed phase, i.e. whether a
    rewind actually happens. An invalid journal refuses every reset_from, pruning or
    not: dropping a phase marker never turns bad evidence into absence. A valid journal
    refuses a rewind to a pre-freeze phase and is retained by any other rewind. Returns
    the journal outcome. The full checkpoint reset rebuilds the record without reading
    it and so never reaches this rule; it is the only path that may drop a journal.
    """
    outcome, value = classify_migration_backups(checkpoint)
    if outcome == MIGRATION_JOURNAL_INVALID:
        raise MigrationRewindRefused(
            f"Refusing checkpoint.reset_from '{reset_from}': the stored migration journal is invalid ({value}). "
            "Repair the checkpoint, or start a new migration with the full checkpoint reset."
        )
    if outcome == MIGRATION_JOURNAL_VALID and prunes and reset_from in PRE_FREEZE_PHASES:
        raise MigrationRewindRefused(
            f"Refusing checkpoint.reset_from '{reset_from}': the checkpoint records a migration transaction "
            "frozen after that phase. Rewind to activation or later to reuse it, or start a new migration "
            "with the full checkpoint reset (checkpoint.reset)."
        )
    return outcome
