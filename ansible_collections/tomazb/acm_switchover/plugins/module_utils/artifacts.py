# SPDX-License-Identifier: MIT
"""Artifact report helpers for the ACM switchover collection."""

from __future__ import annotations

import json
from pathlib import Path

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import validate_safe_path


def build_report_ref(path: str, phase: str, kind: str = "json-report") -> dict:
    """Return a report-ref dict pointing to an artifact file on disk."""
    return {"phase": phase, "path": path, "kind": kind}


class ArtifactWriteError(Exception):
    """Raised when a validated artifact path cannot be written."""


def write_json_artifact(report: dict, destination: str, check_mode: bool = False) -> str:
    """Validate and optionally write a JSON artifact on the controller."""
    validate_safe_path(destination)

    if check_mode:
        return destination

    path = Path(destination)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        raise ArtifactWriteError(f"Cannot write report artifact to '{path}': {exc}") from exc

    return str(path)
