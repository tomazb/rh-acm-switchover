"""Runtime parity normalization and comparison helpers for release scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
