# tests/release/reporting/schema.py
from __future__ import annotations

from typing import Any


REQUIRED_FIELDS = {
    "manifest.json": ("schema_version", "status", "warnings", "failure_reasons"),
    "scenario-results.json": ("schema_version", "results", "scenario_statuses"),
    "runtime-parity.json": ("schema_version", "comparisons", "status"),
    "recovery.json": ("schema_version", "budget_minutes", "budget_consumed_seconds", "pre_run", "post_failure", "hard_stops", "status"),
    "redaction.json": ("schema_version", "status", "scanned_artifacts", "redacted_counts_by_class", "rejected_artifacts", "warnings"),
    "summary.json": ("schema_version", "status", "certification_eligible", "warnings", "failure_reasons"),
}
LIST_FIELDS = {
    "results", "scenario_statuses", "comparisons",
    "pre_run", "post_failure",
    "hard_stops", "scanned_artifacts",
    "rejected_artifacts", "warnings", "failure_reasons",
}


def validate_required_artifact(filename: str, payload: dict[str, Any]) -> None:
    if filename not in REQUIRED_FIELDS:
        raise ValueError(f"{filename}: not a recognised required artifact")
    if payload.get("schema_version") != 1:
        raise ValueError(f"{filename}: schema_version must be 1")
    for field in REQUIRED_FIELDS[filename]:
        if field == "schema_version":
            continue
        if field not in payload:
            raise ValueError(f"{filename}: missing required field {field}")
        if field in LIST_FIELDS and not isinstance(payload[field], list):
            raise ValueError(f"{filename}: field {field} must be a list")
