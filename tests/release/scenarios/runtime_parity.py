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
    "Argo CD management": (
        "run_id_present",
        "paused_application_names",
        "paused_application_count",
        "run_id_preserved_for_retry",
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
        "bootstrap_status",
        "manifest_assets",
        "include_decommission",
        "report_filename",
    ),
    "checkpoints": (
        "resume_start_phase",
        "skipped_phases",
        "checkpoint_error_count",
        "identity_bound",
    ),
    "report artifacts": (
        "schema_version",
        "source_present",
        "safe_path_validated",
    ),
}

PHASE_ORDER_BY_SCENARIO = {
    "python-passive-switchover": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
    "ansible-passive-switchover": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
    "argocd-managed-switchover": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
    "python-restore-only": ("preflight", "activation", "post_activation", "finalization"),
    "ansible-restore-only": ("preflight", "activation", "post_activation", "finalization"),
    "checkpoint-resume": ("preflight", "primary_prep", "activation", "post_activation", "finalization"),
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
    paused_application_names = _sorted_list(source.get("paused_application_names"))
    return {
        "run_id_present": bool(source.get("run_id")),
        "paused_application_names": paused_application_names,
        "paused_application_count": len(paused_application_names),
        "run_id_preserved_for_retry": str(source.get("run_id_preserved_for_retry", "not_applicable")),
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
    assets = _sorted_list(str(asset) for asset in source.get("assets_applied", []) if asset)
    return {
        "bootstrap_status": str(source.get("status", "unknown")),
        "manifest_assets": assets,
        "include_decommission": any("decommission" in str(asset) for asset in assets),
        "report_filename": report_filename,
    }


def _skipped_phases_for_resume(*, scenario_id: str | None, resume_start_phase: str | None) -> list[str]:
    if not scenario_id or not resume_start_phase:
        return []
    phase_order = PHASE_ORDER_BY_SCENARIO.get(scenario_id, ())
    if resume_start_phase not in phase_order:
        return []
    return list(phase_order[: phase_order.index(resume_start_phase)])


def normalize_checkpoint_artifact(source: dict[str, Any], *, scenario_id: str | None = None) -> dict[str, Any]:
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    operational_data = source.get("operational_data") if isinstance(source.get("operational_data"), dict) else {}
    resume_summary = config.get("resume_summary") or operational_data.get("resume_summary") or {}
    errors = source.get("errors") if isinstance(source.get("errors"), list) else []
    resume_start_phase = resume_summary.get("resume_start_phase")
    return {
        "resume_start_phase": resume_start_phase,
        "skipped_phases": _skipped_phases_for_resume(
            scenario_id=scenario_id,
            resume_start_phase=resume_start_phase,
        ),
        "checkpoint_error_count": len(errors),
        "identity_bound": bool(source.get("hub_identities") or source.get("operation_identity")),
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
