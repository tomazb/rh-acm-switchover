# SPDX-License-Identifier: MIT
"""Artifact report helpers for the ACM switchover collection."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    validate_safe_path,
)


def build_report_ref(path: str, phase: str, kind: str = "json-report") -> dict:
    """Return a report-ref dict pointing to an artifact file on disk."""
    return {"phase": phase, "path": path, "kind": kind}


class ArtifactWriteError(Exception):
    """Raised when a validated artifact path cannot be written."""


def _parse_file_mode(mode: str | int) -> int:
    try:
        file_mode = mode if isinstance(mode, int) else int(str(mode), 8)
    except ValueError as exc:
        raise ArtifactWriteError(f"Invalid report artifact mode '{mode}'") from exc

    if file_mode < 0 or file_mode > 0o777:
        raise ArtifactWriteError(f"Invalid report artifact mode '{mode}'")

    return file_mode


def write_json_artifact(
    report: dict, destination: str, check_mode: bool = False, mode: str = "0644"
) -> tuple[str, bool]:
    """Validate and optionally write a JSON artifact on the controller."""
    file_mode = _parse_file_mode(mode)
    validate_safe_path(destination)

    path = Path(destination)
    content = json.dumps(report, indent=2, sort_keys=True) + "\n"

    try:
        current_content = path.read_text() if path.exists() else None
        current_mode = path.stat().st_mode & 0o777 if path.exists() else None
        changed = current_content != content or current_mode != file_mode

        if check_mode or not changed:
            return str(path), changed

        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and current_mode != file_mode:
            path.chmod(file_mode)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, file_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        path.chmod(file_mode)
    except OSError as exc:
        raise ArtifactWriteError(
            f"Cannot write report artifact to '{path}': {exc}"
        ) from exc

    return str(path), True
