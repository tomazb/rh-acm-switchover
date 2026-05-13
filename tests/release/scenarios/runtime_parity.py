"""Runtime parity normalization and comparison helpers for release scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CAPABILITY_REQUIRED_FIELDS = {
    "preflight validation": (
        "status",
        "critical_failure_count",
        "warning_failure_count",
        "check_ids",
        "failed_check_ids",
    ),
    "switchover artifacts": (
        "schema_version",
        "status",
        "phase_ids",
        "report_filename",
    ),
    "restore-only artifacts": (
        "schema_version",
        "status",
        "phase_ids",
        "report_filename",
    ),
    "decommission artifacts": (
        "status",
        "report_filename",
    ),
    "RBAC/bootstrap artifacts": (
        "manifest_asset_count",
        "include_decommission",
        "report_filename",
    ),
    "checkpoints": (
        "artifact_present",
        "completed_phase_count",
    ),
    "report artifacts": (
        "schema_version",
        "source_present",
        "safe_path_validated",
    ),
}


@dataclass(frozen=True)
class ComparisonRecord:
    capability: str
    scenario_id: str
    streams: tuple[str, ...]
    status: str
    required_fields: tuple[str, ...]
    differences: list[dict[str, Any]]
    evidence_paths: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["streams"] = list(self.streams)
        payload["required_fields"] = list(self.required_fields)
        payload["evidence_paths"] = list(self.evidence_paths)
        return payload


def compare_normalized_records(
    *,
    capability: str,
    scenario_id: str,
    python: dict[str, Any],
    ansible: dict[str, Any],
    required_fields: tuple[str, ...],
    evidence_paths: tuple[str, ...] = (),
) -> ComparisonRecord:
    differences: list[dict[str, Any]] = []
    for field in required_fields:
        if field not in python or field not in ansible:
            differences.append(
                {
                    "field": field,
                    "python": python.get(field, "<missing>"),
                    "ansible": ansible.get(field, "<missing>"),
                }
            )
            continue
        if python[field] != ansible[field]:
            differences.append({"field": field, "python": python[field], "ansible": ansible[field]})
    return ComparisonRecord(
        capability=capability,
        scenario_id=scenario_id,
        streams=("python", "ansible"),
        status="passed" if not differences else "failed",
        required_fields=required_fields,
        differences=differences,
        evidence_paths=evidence_paths,
    )


def runtime_parity_not_applicable(capability: str, scenario_id: str, reason: str) -> ComparisonRecord:
    return ComparisonRecord(
        capability=capability,
        scenario_id=scenario_id,
        streams=("python", "ansible"),
        status="not_applicable",
        required_fields=CAPABILITY_REQUIRED_FIELDS.get(capability, ()),
        differences=[{"reason": reason}],
        evidence_paths=(),
    )


def _sorted_list(value: Any) -> list:
    return sorted(value or [])


def normalize_preflight(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": source["status"],
        "critical_failure_count": int(source["critical_failure_count"]),
        "warning_failure_count": int(source["warning_failure_count"]),
        "check_ids": _sorted_list(source["check_ids"]),
        "failed_check_ids": _sorted_list(source["failed_check_ids"]),
    }


def normalize_argocd_management(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_applications": _sorted_list(source["selected_applications"]),
        "paused_applications": _sorted_list(source["paused_applications"]),
        "resumed_applications": _sorted_list(source["resumed_applications"]),
        "resume_failures": _sorted_list(source["resume_failures"]),
        "conflict_allowlist_used": bool(source["conflict_allowlist_used"]),
    }


def _phase_status(source: dict[str, Any]) -> str:
    if source.get("status"):
        return str(source["status"])
    phases = source.get("phases")
    if not isinstance(phases, dict):
        return "unknown"
    statuses = [phase.get("status") for phase in phases.values() if isinstance(phase, dict)]
    if not statuses:
        return "unknown"
    if any(status == "fail" for status in statuses):
        return "fail"
    if all(status == "pass" for status in statuses):
        return "pass"
    return "partial"


def normalize_operation_artifact(source: dict[str, Any], report_filename: str) -> dict[str, Any]:
    phases = source.get("phases") if isinstance(source.get("phases"), dict) else {}
    return {
        "schema_version": str(source.get("schema_version", "")),
        "status": _phase_status(source),
        "phase_ids": sorted(phases),
        "report_filename": report_filename,
    }


def normalize_decommission_artifact(source: dict[str, Any], report_filename: str) -> dict[str, Any]:
    return {
        "status": str(source.get("status", "unknown")),
        "report_filename": report_filename,
    }


def normalize_rbac_bootstrap_artifact(source: dict[str, Any], report_filename: str) -> dict[str, Any]:
    assets = source.get("assets_applied") if isinstance(source.get("assets_applied"), list) else []
    return {
        "manifest_asset_count": len(assets),
        "include_decommission": any("decommission" in str(asset) for asset in assets),
        "report_filename": report_filename,
    }


def normalize_checkpoint_artifact(source: dict[str, Any]) -> dict[str, Any]:
    completed = source.get("completed_phases")
    if completed is None:
        completed = source.get("completed_steps")
    return {
        "artifact_present": True,
        "completed_phase_count": len(completed) if isinstance(completed, list) else 0,
    }


def normalize_report_artifact(source: dict[str, Any], report_path: str) -> dict[str, Any]:
    path = Path(report_path)
    return {
        "schema_version": str(source.get("schema_version", "")),
        "source_present": bool(source.get("source")),
        "safe_path_validated": ".." not in path.parts and bool(path.name),
    }


def write_runtime_parity_artifact(*, artifacts, comparisons: list[ComparisonRecord]) -> dict:
    if not comparisons:
        status = "not_applicable"
    elif all(item.status == "not_applicable" for item in comparisons):
        status = "not_applicable"
    elif all(item.status in {"passed", "not_applicable"} for item in comparisons):
        status = "passed"
    else:
        status = "failed"
    payload = {
        "schema_version": 1,
        "comparisons": [item.to_dict() for item in comparisons],
        "status": status,
    }
    artifacts.write_json(
        "runtime-parity.json",
        payload,
    )
    return payload
