"""Machine-readable report artifact helpers for the Python CLI."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.constants import (
    REPORT_DEFAULT_CHECK,
    REPORT_ID_PREFIX_PREFLIGHT,
    REPORT_PHASE_PREFLIGHT,
    REPORT_SCHEMA_VERSION,
    REPORT_SEVERITY_CRITICAL,
    REPORT_SEVERITY_WARNING,
    REPORT_SOURCE_PYTHON_CLI,
    REPORT_STATUS_FAIL,
    REPORT_STATUS_PASS,
)
from lib.exceptions import SecurityValidationError, ValidationError
from lib.validation import InputValidator

SCHEMA_VERSION = REPORT_SCHEMA_VERSION
SOURCE = REPORT_SOURCE_PYTHON_CLI


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_validation_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert Python preflight reporter entries to the collection result shape."""
    if {"id", "severity", "status", "message"}.issubset(result):
        return result

    check_name = str(result.get("check", REPORT_DEFAULT_CHECK)).strip() or REPORT_DEFAULT_CHECK
    passed = bool(result.get("passed"))
    critical = bool(result.get("critical", True))
    return {
        "id": REPORT_ID_PREFIX_PREFLIGHT + check_name.lower().replace(" ", "-").replace("_", "-"),
        "severity": REPORT_SEVERITY_CRITICAL if critical else REPORT_SEVERITY_WARNING,
        "status": REPORT_STATUS_PASS if passed else REPORT_STATUS_FAIL,
        "message": result.get("message", ""),
        "details": {"check": check_name},
        "recommended_action": None,
    }


def _summarize_state(state_snapshot: dict[str, Any], status: str) -> dict[str, Any]:
    errors = state_snapshot.get("errors", []) or []
    completed_steps = state_snapshot.get("completed_steps", []) or []
    return {
        "passed": status == REPORT_STATUS_PASS,
        "completed_steps": len(completed_steps),
        "error_count": len(errors),
        "current_phase": state_snapshot.get("current_phase"),
    }


def _hubs_from_args(args: Any) -> dict[str, dict[str, Any]]:
    hubs: dict[str, dict[str, Any]] = {}
    primary_context = getattr(args, "primary_context", None)
    secondary_context = getattr(args, "secondary_context", None)
    if primary_context:
        hubs["primary"] = {"context": primary_context}
    if secondary_context:
        hubs["secondary"] = {"context": secondary_context}
    return hubs


def _validate_path_syntax(path_value: str, field_name: str) -> None:
    """Apply path syntax checks without requiring absolute parents to exist yet."""
    if not path_value:
        raise ValidationError(f"{field_name} path cannot be empty")
    if ".." in path_value.split("/"):
        raise SecurityValidationError(
            f"SECURITY: Path traversal attempt detected in {field_name} path '{path_value}'. "
            "The '..' sequence is not allowed as a path component."
        )
    unsafe_chars = ["~", "$", "{", "}", "|", "&", ";", "<", ">", "`"]
    if any(char in path_value for char in unsafe_chars):
        raise SecurityValidationError(
            f"SECURITY: Invalid characters in {field_name} path '{path_value}'. "
            "Path contains unsafe characters that could be used for command injection. "
            f"Disallowed patterns: {', '.join(unsafe_chars)}."
        )


def _nearest_existing_ancestor(path: Path) -> Path:
    """Return the nearest existing ancestor for an absolute path."""
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def build_operation_report(
    report_type: str,
    status: str,
    source: str,
    args: Any,
    state_snapshot: dict[str, Any],
    phases: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a schema-compatible report for Python CLI operations."""
    config = state_snapshot.get("config", {}) or {}
    raw_results = config.get("preflight_results") or []
    results = [_normalise_validation_result(item) for item in raw_results]
    errors = state_snapshot.get("errors", []) or []

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "source": source,
        "type": report_type,
        "status": status,
        "summary": _summarize_state(state_snapshot, status),
        "hubs": _hubs_from_args(args),
        "operation": {
            "method": getattr(args, "method", None),
            "old_hub_action": getattr(args, "old_hub_action", None),
            "restore_only": bool(getattr(args, "restore_only", False)),
            "decommission": bool(getattr(args, "decommission", False)),
        },
        "errors": errors,
    }

    if phases:
        report["phases"] = phases

    if report_type == REPORT_PHASE_PREFLIGHT:
        report["phase"] = REPORT_PHASE_PREFLIGHT
        report["results"] = results

    argocd_run_id = config.get("argocd_run_id")
    paused_apps = config.get("argocd_paused_apps") or []
    if argocd_run_id or paused_apps:
        report["argocd"] = {
            "run_id": argocd_run_id or "",
            "summary": {"paused": len(paused_apps), "restored": 0},
        }

    return report


def write_json_report_artifact(report: dict[str, Any], destination: str) -> str:
    """Validate and write a JSON report artifact."""
    path = Path(destination)
    _validate_path_syntax(destination, "report artifact")
    if path.is_absolute():
        InputValidator.validate_safe_filesystem_path(
            str(_nearest_existing_ancestor(path.parent)),
            "report artifact ancestor",
        )
    else:
        InputValidator.validate_safe_filesystem_path(str(path.parent), "report artifact directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    InputValidator.validate_safe_filesystem_path(str(path.parent), "report artifact directory")
    InputValidator.validate_safe_filesystem_path(destination, "report artifact")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
