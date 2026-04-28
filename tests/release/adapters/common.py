from __future__ import annotations

"""Stream-neutral result model for release adapter records."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReportArtifact:
    type: str
    path: str
    schema_version: str | int | None
    required: bool


@dataclass(frozen=True)
class AssertionRecord:
    capability: str
    name: str
    status: str
    expected: str
    actual: str
    evidence_path: str | None
    message: str


@dataclass(frozen=True)
class StreamResult:
    stream: str
    scenario_id: str
    status: str
    command: list[str]
    returncode: int | None
    stdout_path: str | None
    stderr_path: str | None
    reports: list[ReportArtifact]
    assertions: list[AssertionRecord]
    started_at: str
    ended_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
