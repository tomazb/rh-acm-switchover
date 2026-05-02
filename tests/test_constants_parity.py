"""Parity contract: shared constants between Python CLI and Ansible collection must match."""

import ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants as ans_constants
import lib.constants as py_constants

# Explicit contract map: Python constant name → Ansible constant name.
# Only constants that MUST stay in sync are listed here.
CONSTANT_PAIRS = {
    # Namespaces
    "ACM_NAMESPACE": "ACM_NAMESPACE",
    "BACKUP_NAMESPACE": "BACKUP_NAMESPACE",
    "OBSERVABILITY_NAMESPACE": "OBSERVABILITY_NAMESPACE",
    "MCE_NAMESPACE": "MCE_NAMESPACE",
    "MANAGED_CLUSTER_AGENT_NAMESPACE": "MANAGED_CLUSTER_AGENT_NAMESPACE",
    # Restore resource names (different naming convention)
    "RESTORE_PASSIVE_SYNC_NAME": "PASSIVE_SYNC_RESTORE_NAME",
    "RESTORE_FULL_NAME": "FULL_RESTORE_NAME",
    "MANAGED_CLUSTER_RESTORE_NAME": "ACTIVATION_RESTORE_NAME",
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
    # Observability components
    "OBSERVATORIUM_API_DEPLOYMENT": "OBSERVATORIUM_API_DEPLOYMENT",
    "THANOS_COMPACTOR_STATEFULSET": "THANOS_COMPACTOR_STATEFULSET",
    "THANOS_COMPACTOR_LABEL_SELECTOR": "THANOS_COMPACTOR_LABEL_SELECTOR",
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
            mismatches.append(
                f"{py_name}={py_val!r} (Python) != {ans_name}={ans_val!r} (Ansible)"
            )

    assert not mismatches, "Constants drift detected:\n  " + "\n  ".join(mismatches)


_MISSING = object()
