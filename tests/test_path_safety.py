"""Consolidation contracts for Python path-safety helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.exceptions import SecurityValidationError
from lib.validation import InputValidator


def test_python_safe_path_wrapper_delegates_to_path_safety(monkeypatch):
    from lib import path_safety

    calls = []

    def fake_validate(path: str, field_name: str) -> None:
        calls.append((path, field_name))

    monkeypatch.setattr(path_safety, "validate_safe_filesystem_path", fake_validate)

    InputValidator.validate_safe_filesystem_path("/tmp/state.json", "state-file")

    assert calls == [("/tmp/state.json", "state-file")]


def test_python_report_artifact_wrapper_delegates_to_path_safety(monkeypatch):
    import lib.report_artifacts as report_artifacts

    calls = []

    def fake_validate(destination: str, field_name: str = "report artifact") -> Path:
        calls.append((destination, field_name))
        return Path(destination)

    monkeypatch.setattr(report_artifacts.path_safety, "validate_report_artifact_path", fake_validate)

    result = report_artifacts.validate_report_artifact_path("/tmp/report.json", "custom report")

    assert result == Path("/tmp/report.json")
    assert calls == [("/tmp/report.json", "custom report")]


def test_report_artifact_rejects_tmp_prefix_confusion():
    from lib.path_safety import validate_report_artifact_path

    with pytest.raises(SecurityValidationError, match="allowed"):
        validate_report_artifact_path("/tmp-not-allowed/report.json")


def test_report_artifact_rejects_missing_path_under_non_directory_ancestor(tmp_path):
    from lib.path_safety import validate_report_artifact_path

    file_anchor = tmp_path / "state-file"
    file_anchor.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SecurityValidationError, match="non-directory ancestor"):
        validate_report_artifact_path(str(file_anchor / "missing" / "report.json"))
