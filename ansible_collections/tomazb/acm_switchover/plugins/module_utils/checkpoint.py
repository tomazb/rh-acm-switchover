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
