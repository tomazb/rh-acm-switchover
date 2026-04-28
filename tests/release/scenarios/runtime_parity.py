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
