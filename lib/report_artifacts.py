"""Machine-readable report artifact helpers for the Python CLI."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib import path_safety
from lib.argocd_register_store import PauseRegisterStore
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
from lib.run_record import RunSummary

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
    """Count raw snapshot entries, deliberately not RunSummary's typed view.

    Only ``error_count`` is genuinely pinned to the raw list: the errors
    strategy generates arbitrary JSON-native values (ints, strings, None), all
    of which RunSummary.from_snapshot would filter out, while
    tests/properties/test_report_artifact_properties.py::
    test_state_summary_uses_exact_counts_status_and_empty_collection_semantics
    asserts the count equals len(raw errors).

    ``completed_steps`` and ``current_phase`` are kept raw for contract
    consistency, not because a test forces it -- the strategies only generate
    well-typed values there (steps are always {"name": str}, current_phase is
    always a Phase value), so the typed view would agree on every generated
    input. They stay raw so this function has one provenance, and because
    from_snapshot's filtering/degradation would change the reported numbers for
    malformed real-world state.
    """
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


def validate_report_artifact_path(destination: str, field_name: str = "report artifact") -> Path:
    """Validate an artifact path without following unsafe relative symlinks."""
    return path_safety.validate_report_artifact_path(destination, field_name)


def validate_report_artifact_directory(path_value: str, field_name: str = "report artifact directory") -> None:
    """Validate a report artifact directory supplied before the final filename is known."""
    path_safety.validate_report_artifact_directory(path_value, field_name)


def build_operation_report(
    report_type: str,
    status: str,
    source: str,
    args: Any,
    state_snapshot: dict[str, Any],
    phases: dict[str, Any] | None = None,
    *,
    refusal_message: str | None = None,
    redact_identity_inputs: bool = False,
) -> dict[str, Any]:
    """Build a schema-compatible report for Python CLI operations."""
    config = state_snapshot.get("config", {}) or {}
    summary = RunSummary.from_snapshot(state_snapshot)
    # Typed view for preflight results: every entry it yields is a dict, which
    # is what _normalise_validation_result requires. A non-dict entry, or a
    # truthy non-list preflight_results, used to raise here and lose the whole
    # report; both are now skipped. Trade-off: from_snapshot only accepts a
    # dict config, so a non-dict Mapping config yields no results at all where
    # the old config.get() read them fine (see the task 7 report).
    results = [_normalise_validation_result(item) for item in summary.preflight_results]
    # Raw errors list on purpose: tests/properties/test_report_artifact_properties.py::
    # test_python_operation_report_schema_fields_round_trip_and_exclude_sensitive_config
    # pins report["errors"] to the snapshot's list verbatim, including the
    # non-dict entries RunSummary.from_snapshot filters out.
    errors = [refusal_message] if refusal_message is not None else state_snapshot.get("errors", []) or []
    summary_data = _summarize_state(state_snapshot, status)
    if refusal_message is not None:
        summary_data["error_count"] = 1

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "source": source,
        "type": report_type,
        "status": status,
        "summary": summary_data,
        "hubs": {} if redact_identity_inputs else _hubs_from_args(args),
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

    argocd_status = PauseRegisterStore.status_from_state_config(config)
    if argocd_status.run_id or argocd_status.confirmed_paused_count:
        report["argocd"] = {
            "run_id": argocd_status.run_id or "",
            "summary": {"paused": argocd_status.confirmed_paused_count, "restored": 0},
        }

    return report


_DRY_RUN_REPORT_CONFLICT = "Dry-run report destination conflicts with the state file. Choose a different --report-dir."


def _open_protected_report(path: Path, flags: int, checkpoint: Path) -> int:
    """Create report parents and open the file using the same pinned directories."""
    parent = path_safety.validate_report_artifact_path(str(path), resolve_parent=True).parent

    # Search-only access is sufficient for relative lookup and creation.
    lookup = getattr(os, "O_SEARCH", getattr(os, "O_PATH", os.O_RDONLY))
    directory_flags = lookup | os.O_DIRECTORY | os.O_NOFOLLOW

    def check_component(directory_fd: int, name: str) -> None:
        if name != checkpoint.name:
            return
        try:
            checkpoint_parent = checkpoint.parent.stat()
        except FileNotFoundError:
            return
        if os.path.samestat(os.fstat(directory_fd), checkpoint_parent):
            raise ValueError(_DRY_RUN_REPORT_CONFLICT)

    directory_fd = os.open(parent.anchor, directory_flags)
    try:
        for name in parent.parts[1:]:
            check_component(directory_fd, name)
            try:
                child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(name, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        check_component(directory_fd, path.name)
        return os.open(path.name, flags, 0o644, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def write_json_report_artifact(report: dict[str, Any], destination: str, *, protected_path: str | None = None) -> str:
    """Validate and write a JSON report artifact."""
    path = validate_report_artifact_path(destination, "report artifact")
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    if protected_path is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path = validate_report_artifact_path(destination, "report artifact")
        fd = os.open(str(path), flags | os.O_TRUNC, 0o644)
    else:
        checkpoint = Path(os.path.realpath(protected_path))
        fd = _open_protected_report(path, flags, checkpoint)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        if protected_path is not None:
            # The destination may have changed since validation. Compare the
            # opened inode before truncating anything.
            try:
                checkpoint_info = os.stat(protected_path)
            except FileNotFoundError:
                checkpoint_info = None
            if checkpoint_info is not None and os.path.samestat(os.fstat(handle.fileno()), checkpoint_info):
                raise ValueError(_DRY_RUN_REPORT_CONFLICT)
            handle.truncate(0)
        handle.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return str(path)
