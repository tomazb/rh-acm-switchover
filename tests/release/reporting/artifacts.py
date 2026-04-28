"""Artifact directory management and sanitized persistence for release validation runs."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .redaction import RedactionError, sanitize_text

REQUIRED_JSON_ARTIFACTS = {
    "manifest.json": {
        "schema_version": 1,
        "status": "created",
        "warnings": [],
        "failure_reasons": [],
    },
    "scenario-results.json": {
        "schema_version": 1,
        "results": [],
        "scenario_statuses": [],
    },
    "runtime-parity.json": {
        "schema_version": 1,
        "comparisons": [],
        "status": "not_applicable",
    },
    "recovery.json": {
        "schema_version": 1,
        "budget_minutes": 0,
        "budget_consumed_seconds": 0,
        "pre_run": [],
        "post_failure": [],
        "hard_stops": [],
        "status": "not_applicable",
    },
    "redaction.json": {
        "schema_version": 1,
        "status": "not_applicable",
        "scanned_artifacts": [],
        "redacted_counts_by_class": {},
        "rejected_artifacts": [],
        "warnings": [],
    },
    "summary.json": {
        "schema_version": 1,
        "status": "failed",
        "certification_eligible": False,
        "warnings": [],
        "failure_reasons": [],
    },
}


@dataclass(frozen=True)
class ReleaseArtifacts:
    """Persistent container for a single release validation run's output files."""

    root: Path
    run_id: str
    run_dir: Path

    @classmethod
    def create(cls, *, root: Path, run_id: str) -> "ReleaseArtifacts":
        run_dir = root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        artifacts = cls(root=root, run_id=run_id, run_dir=run_dir)
        for filename, payload in REQUIRED_JSON_ARTIFACTS.items():
            artifacts.write_json(filename, copy.deepcopy(payload))
        (run_dir / "release-report.md").write_text(
            "# Release Report\n\nStatus: created\n", encoding="utf-8"
        )
        return artifacts

    def write_json(self, relative_path: str | Path, payload: dict[str, Any]) -> Path:
        """Serialize payload as indented JSON under run_dir and return the written path."""
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return path

    def write_sanitized_text(self, relative_path: str | Path, content: str) -> Path:
        """Sanitize content, update redaction audit log, then write to run_dir."""
        relative_path = Path(relative_path)
        try:
            sanitized = sanitize_text(content)
        except RedactionError:
            self._update_redaction_record(str(relative_path), {}, rejected=True)
            raise
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sanitized.text, encoding="utf-8")
        self._update_redaction_record(
            str(relative_path), sanitized.redacted_counts_by_class, rejected=False
        )
        return path

    def _update_redaction_record(
        self, artifact_path: str, counts: dict[str, int], *, rejected: bool
    ) -> None:
        """Merge a scan result into redaction.json for audit-trail tracking."""
        redaction_file = self.run_dir / "redaction.json"
        redaction: dict[str, Any] = json.loads(
            redaction_file.read_text(encoding="utf-8")
        )
        if rejected:
            redaction.setdefault("rejected_artifacts", []).append(artifact_path)
        else:
            redaction.setdefault("scanned_artifacts", []).append(artifact_path)
            existing = redaction.setdefault("redacted_counts_by_class", {})
            for klass, count in counts.items():
                existing[klass] = existing.get(klass, 0) + count
            if counts:
                redaction["status"] = "redacted"
            elif redaction.get("status") == "not_applicable":
                redaction["status"] = "ok"
        self.write_json("redaction.json", redaction)

    def write_failed_manifest(
        self, *, reason: str, command: list[str]
    ) -> tuple[Path, Path]:
        """Write manifest.json and summary.json with failed status and failure reason."""
        manifest_path = self.write_json(
            "manifest.json",
            {
                "schema_version": 1,
                "run_id": self.run_id,
                "status": "failed",
                "command": command,
                "certification_eligible": False,
                "warnings": [],
                "failure_reasons": [reason],
            },
        )
        summary_path = self.write_json(
            "summary.json",
            {
                "schema_version": 1,
                "run_id": self.run_id,
                "status": "failed",
                "certification_eligible": False,
                "warnings": [],
                "failure_reasons": [reason],
            },
        )
        return manifest_path, summary_path
