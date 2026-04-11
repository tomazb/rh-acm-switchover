# SPDX-License-Identifier: MIT
"""Runtime helpers for the write_artifact action plugin."""

from __future__ import annotations


def build_report_ref(path: str, phase: str, kind: str = "json-report") -> dict:
    """Return a report-ref dict pointing to an artifact file on disk."""
    return {"phase": phase, "path": path, "kind": kind}
