"""Runtime parity normalization and comparison helpers for release scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
        "selected_applications",
        "paused_applications",
        "resumed_applications",
        "resume_failures",
        "conflict_allowlist_used",
    ),
    "activation": (
        "restore_name",
        "restore_phase_category",
        "sync_restore_enabled",
        "managed_cluster_activation_requested",
    ),
    "finalization": (
        "backup_schedule_present",
        "backup_schedule_paused",
        "post_enable_backup_observed",
        "old_hub_action_result",
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
            differences.append(
                {"field": field, "python": python[field], "ansible": ansible[field]}
            )
    return ComparisonRecord(
        capability=capability,
        scenario_id=scenario_id,
        streams=("python", "ansible"),
        status="passed" if not differences else "failed",
        required_fields=required_fields,
        differences=differences,
        evidence_paths=evidence_paths,
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


def write_runtime_parity_artifact(*, artifacts, comparisons: list[ComparisonRecord]) -> None:
    status = (
        "passed"
        if comparisons
        and all(item.status in {"passed", "not_applicable"} for item in comparisons)
        else "failed"
    )
    if not comparisons:
        status = "not_applicable"
    artifacts.write_json(
        "runtime-parity.json",
        {
            "schema_version": 1,
            "comparisons": [item.to_dict() for item in comparisons],
            "status": status,
        },
    )
