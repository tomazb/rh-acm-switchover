# SPDX-License-Identifier: MIT
"""Shared checkpoint schema helpers for the acm_switchover collection."""

from __future__ import annotations

from datetime import datetime, timezone

SCHEMA_VERSION = "2.0"
KNOWN_PHASES = (
    "preflight",
    "primary_prep",
    "activation",
    "post_activation",
    "finalization",
)

CHECKPOINT_VALID_STATUSES = frozenset({"enter", "pass", "fail", "reset"})
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


def _operational_data(checkpoint) -> dict:
    if not isinstance(checkpoint, dict):
        return {}
    data = checkpoint.get("operational_data")
    return data if isinstance(data, dict) else {}


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
    return {
        KEY_ARGOCD_RUN_ID: data.get(KEY_ARGOCD_RUN_ID) or "",
        KEY_ARGOCD_DISCOVERY_NAMESPACES: namespaces if isinstance(namespaces, dict) else {},
        # Strict boolean: a malformed value like the string "false" must degrade
        # to False, never coerce truthy — this flag feeds finalization's legacy
        # discharge branch, which deletes the auto-import ConfigMap.
        KEY_AUTO_IMPORT_STRATEGY_CHANGED: data.get(KEY_AUTO_IMPORT_STRATEGY_CHANGED, False) is True,
        # None means never recorded; wrong-typed values degrade to None so the
        # roles' `is not none` guards treat them as never recorded.
        KEY_EXPECTED_MANAGED_CLUSTER_NAMES: names if isinstance(names, list) else None,
        KEY_EXPECTED_MANAGED_CLUSTER_COUNT: count if isinstance(count, int) and not isinstance(count, bool) else None,
        KEY_PRIMARY_HAS_OBSERVABILITY: primary_obs if isinstance(primary_obs, bool) else None,
        KEY_SECONDARY_HAS_OBSERVABILITY: secondary_obs if isinstance(secondary_obs, bool) else None,
        KEY_SAVED_BACKUP_SCHEDULE: saved_schedule if isinstance(saved_schedule, dict) else None,
        KEY_BACKUP_SCHEDULE_ENABLED_AT: data.get(KEY_BACKUP_SCHEDULE_ENABLED_AT) or "",
        KEY_RESUME_START_PHASE: resume_summary.get(KEY_RESUME_START_PHASE) or "",
    }


def record_resume_start_phase(checkpoint: dict, phase: str) -> None:
    """Record where this resumed run starts. Replace semantics — parity with
    Python RunRecord.record_resume_start_phase (last resume wins)."""
    data = checkpoint.get("operational_data")
    if not isinstance(data, dict):
        data = {}
        checkpoint["operational_data"] = data
    data[KEY_RESUME_SUMMARY] = {KEY_RESUME_START_PHASE: phase}
