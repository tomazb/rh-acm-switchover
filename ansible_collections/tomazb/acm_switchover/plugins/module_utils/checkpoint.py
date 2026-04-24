# SPDX-License-Identifier: MIT
"""Shared checkpoint schema helpers for the acm_switchover collection."""

from __future__ import annotations

from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"
KNOWN_PHASES = ("preflight", "primary_prep", "activation", "post_activation", "finalization")


def build_checkpoint_record(phase: str, operational_data: dict) -> dict:
    """Return a fresh checkpoint record dict for the given phase."""
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "completed_phases": [],
        "operational_data": operational_data,
        "errors": [],
        "report_refs": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def should_resume_phase(checkpoint: dict, phase: str) -> bool:
    """Return True if the phase still needs to run, False if already completed."""
    return phase not in checkpoint.get("completed_phases", [])
