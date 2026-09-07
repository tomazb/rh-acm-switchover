"""Parity contract: shared constants between Python CLI and Ansible collection must match."""

import ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants as ans_constants
import lib.constants as py_constants
import lib.teardown_record as py_teardown_record

# Explicit contract map: Python constant name → Ansible constant name.
# Only constants that MUST stay in sync are listed here.
CONSTANT_PAIRS = {
    # Namespaces
    "ACM_NAMESPACE": "ACM_NAMESPACE",
    "BACKUP_NAMESPACE": "BACKUP_NAMESPACE",
    "OBSERVABILITY_NAMESPACE": "OBSERVABILITY_NAMESPACE",
    "MCE_NAMESPACE": "MCE_NAMESPACE",
    "MANAGED_CLUSTER_AGENT_NAMESPACE": "MANAGED_CLUSTER_AGENT_NAMESPACE",
    "HUB_KUBECONFIG_SECRET_NAME": "HUB_KUBECONFIG_SECRET_NAME",
    "BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME": "BOOTSTRAP_HUB_KUBECONFIG_SECRET_NAME",
    "SECRET_VISIBILITY_TIMEOUT": "SECRET_VISIBILITY_TIMEOUT",
    "SECRET_VISIBILITY_INTERVAL": "SECRET_VISIBILITY_INTERVAL",
    "KLUSTERLET_RECHECK_TIMEOUT": "KLUSTERLET_RECHECK_TIMEOUT",
    "KLUSTERLET_RECHECK_INTERVAL": "KLUSTERLET_RECHECK_INTERVAL",
    "CLUSTER_VERIFY_MAX_WORKERS": "KLUSTERLET_DEFAULT_WORKERS",
    # Restore resource names (different naming convention)
    "RESTORE_PASSIVE_SYNC_NAME": "PASSIVE_SYNC_RESTORE_NAME",
    "RESTORE_FULL_NAME": "FULL_RESTORE_NAME",
    "MANAGED_CLUSTER_RESTORE_NAME": "ACTIVATION_RESTORE_NAME",
    "BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN": "BENIGN_ALREADY_AVAILABLE_MESSAGE_PATTERN",
    # Velero / restore values
    "VELERO_BACKUP_LATEST": "VELERO_BACKUP_LATEST",
    "VELERO_BACKUP_SKIP": "VELERO_BACKUP_SKIP",
    "CLEANUP_BEFORE_RESTORE_VALUE": "CLEANUP_BEFORE_RESTORE_VALUE",
    # Auto-import strategy constants
    "IMPORT_CONTROLLER_CONFIG_CM": "IMPORT_CONTROLLER_CONFIG_CM",
    "AUTO_IMPORT_STRATEGY_KEY": "AUTO_IMPORT_STRATEGY_KEY",
    "AUTO_IMPORT_STRATEGY_DEFAULT": "AUTO_IMPORT_STRATEGY_DEFAULT",
    "AUTO_IMPORT_STRATEGY_SYNC": "AUTO_IMPORT_STRATEGY_SYNC",
    # Annotation keys
    "DISABLE_AUTO_IMPORT_ANNOTATION": "DISABLE_AUTO_IMPORT_ANNOTATION",
    "IMMEDIATE_IMPORT_ANNOTATION": "IMMEDIATE_IMPORT_ANNOTATION",
    # Cluster naming
    "LOCAL_CLUSTER_NAME": "LOCAL_CLUSTER_NAME",
    # API groups
    "MANAGED_CLUSTER_API_GROUP": "CLUSTER_OPEN_CLUSTER_MANAGEMENT_IO",
    # Observability components
    "OBSERVATORIUM_API_DEPLOYMENT": "OBSERVATORIUM_API_DEPLOYMENT",
    "THANOS_COMPACTOR_STATEFULSET": "THANOS_COMPACTOR_STATEFULSET",
    "THANOS_COMPACTOR_LABEL_SELECTOR": "THANOS_COMPACTOR_LABEL_SELECTOR",
    "OBSERVABILITY_POD_LABEL_SELECTOR": "OBSERVABILITY_POD_LABEL_SELECTOR",
    # R4-03 strict-read reason codes
    "STRICT_READ_REASON_KIND_NOT_SERVED": "STRICT_READ_REASON_KIND_NOT_SERVED",
    "STRICT_READ_REASON_NAMESPACE_NOT_FOUND": "STRICT_READ_REASON_NAMESPACE_NOT_FOUND",
    "STRICT_READ_REASON_OBJECT_NOT_FOUND": "STRICT_READ_REASON_OBJECT_NOT_FOUND",
    "STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE": "STRICT_READ_REASON_DISCOVERY_UNVERIFIABLE",
    "STRICT_READ_REASON_INVENTORY_INCOMPLETE": "STRICT_READ_REASON_INVENTORY_INCOMPLETE",
    "STRICT_READ_REASON_MALFORMED_RESPONSE": "STRICT_READ_REASON_MALFORMED_RESPONSE",
    "STRICT_READ_REASON_READ_FAILED": "STRICT_READ_REASON_READ_FAILED",
    # R4-03 strict-read bounds. STRICT_READ_REQUEST_TIMEOUT is deliberately absent: it is
    # collection-only, and its equality with the Python per-instance default is asserted by
    # test_strict_read_bounds_are_mirrored in tests/test_strict_read_parity.py.
    "STRICT_READ_PAGE_LIMIT": "STRICT_READ_PAGE_LIMIT",
    "STRICT_READ_MAX_PAGES": "STRICT_READ_MAX_PAGES",
    "STRICT_READ_MAX_RESTARTS": "STRICT_READ_MAX_RESTARTS",
    # R4-03 decommission teardown records (plan §10.2.2, §10.2.3). Both are defined in
    # lib/constants.py, so the generic helper resolves them; the closed vocabularies whose
    # Python owner is lib/teardown_record.py are compared by the two tests below instead.
    "OPERATOR_IDENTITY_DISCOVERY_METHOD": "OPERATOR_IDENTITY_DISCOVERY_METHOD",
    "OPERATOR_IDENTITY_UNAVAILABLE_REASONS": "OPERATOR_IDENTITY_UNAVAILABLE_REASONS",
}


def test_shared_constants_parity():
    """All shared constants must have identical values across Python CLI and Ansible collection."""
    mismatches = []
    for py_name, ans_name in CONSTANT_PAIRS.items():
        py_val = getattr(py_constants, py_name, _MISSING)
        ans_val = getattr(ans_constants, ans_name, _MISSING)

        if py_val is _MISSING:
            mismatches.append(f"Python missing: {py_name}")
        elif ans_val is _MISSING:
            mismatches.append(f"Ansible missing: {ans_name}")
        elif py_val != ans_val:
            mismatches.append(f"{py_name}={py_val!r} (Python) != {ans_name}={ans_val!r} (Ansible)")

    assert not mismatches, "Constants drift detected:\n  " + "\n  ".join(mismatches)


# Closed vocabularies whose Python owner is lib/teardown_record.py, not lib/constants.py.
TEARDOWN_VOCABULARY_NAMES = (
    "DRAIN_SCOPED_KINDS",
    "IDENTITY_BEARING_KINDS",
    "RESOURCE_VERSION_LABELS",
    "ABSENCE_PROOF_KEYS",
    "ABSENCE_PROOF_TYPES",
    "ABSENCE_PROOF_TYPES_BY_KEY",
)

# Teardown shape constants with the same Python owner. These are NOT vocabularies, and the
# shared malformed-vector set cannot catch drift in them: DRAIN_NAMESPACE_BY_KIND decides
# which namespace a `drain_namespace` absence proof must name (§10.2.1c), MCH_OWNED_CRD
# decides which owned CRD identifies the operator CSV (§10.2.2), and NAMESPACE_API_VERSION /
# NAMESPACE_KIND build the namespace resource_key both sides require.
TEARDOWN_SHAPE_NAMES = (
    "DRAIN_NAMESPACE_BY_KIND",
    "MCH_OWNED_CRD",
    "NAMESPACE_API_VERSION",
    "NAMESPACE_KIND",
)


def test_teardown_vocabularies_are_mirrored():
    """R4-03 §10.2.1c closed vocabularies: one authoritative definition per form factor.

    CONSTANT_PAIRS resolves Python names from lib.constants; these live in
    lib.teardown_record, so they are compared directly against the collection's
    module_utils/constants.py rather than duplicated to fit the generic helper.
    """
    mismatches = []
    for name in TEARDOWN_VOCABULARY_NAMES:
        py_val = getattr(py_teardown_record, name, _MISSING)
        ans_val = getattr(ans_constants, name, _MISSING)
        if py_val is _MISSING:
            mismatches.append(f"Python missing: lib.teardown_record.{name}")
        elif ans_val is _MISSING:
            mismatches.append(f"Ansible missing: {name}")
        elif py_val != ans_val:
            mismatches.append(f"{name}={py_val!r} (Python) != {ans_val!r} (Ansible)")

    assert not mismatches, "Teardown vocabulary drift detected:\n  " + "\n  ".join(mismatches)


def test_teardown_shape_constants_are_mirrored():
    """The non-vocabulary teardown constants owned by lib/teardown_record.py."""
    mismatches = []
    for name in TEARDOWN_SHAPE_NAMES:
        py_val = getattr(py_teardown_record, name, _MISSING)
        ans_val = getattr(ans_constants, name, _MISSING)
        if py_val is _MISSING:
            mismatches.append(f"Python missing: lib.teardown_record.{name}")
        elif ans_val is _MISSING:
            mismatches.append(f"Ansible missing: {name}")
        elif py_val != ans_val:
            mismatches.append(f"{name}={py_val!r} (Python) != {ans_val!r} (Ansible)")

    assert not mismatches, "Teardown shape drift detected:\n  " + "\n  ".join(mismatches)


def test_teardown_phase_vocabulary_is_mirrored():
    """The phase set itself. The shared "unknown phase" vector only proves the set is not
    too permissive; a phase silently dropped from one side would go unnoticed without this.
    """
    python_phases = frozenset(phase.value for phase in py_teardown_record.TeardownPhase)
    assert ans_constants.TEARDOWN_PHASES == python_phases


_MISSING = object()


def test_decommission_outcome_vocabulary_parity():
    """The collection's mirrored outcome tuple must equal SubstepOutcome's values.

    Compares ENUM VALUES ONLY. The derived convenience UNSUCCESSFUL_OUTCOMES in
    lib/decommission_outcome.py is not part of the vocabulary and must never
    enter this comparison.
    """
    from lib.decommission_outcome import SubstepOutcome

    assert {o.value for o in SubstepOutcome} == set(ans_constants.DECOMMISSION_SUBSTEP_OUTCOMES)
