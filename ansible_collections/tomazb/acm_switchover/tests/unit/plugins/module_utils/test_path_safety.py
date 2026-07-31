"""Consolidation contracts for collection path-safety helpers."""

from __future__ import annotations

import os

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import validation
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
)


def test_collection_safe_path_wrapper_delegates_to_path_safety(monkeypatch):
    calls = []

    def fake_validate(path: str) -> None:
        calls.append(path)

    monkeypatch.setattr(validation.path_safety, "validate_safe_path", fake_validate)

    validation.validate_safe_path("/tmp/state.json")

    assert calls == ["/tmp/state.json"]


def test_collection_report_artifact_wrapper_delegates_to_path_safety(monkeypatch):
    calls = []

    def fake_validate(path: str) -> None:
        calls.append(path)

    monkeypatch.setattr(validation.path_safety, "validate_report_artifact_path", fake_validate)

    validation.validate_report_artifact_path("/tmp/report.json")

    assert calls == ["/tmp/report.json"]


def test_report_artifact_rejects_tmp_prefix_confusion():
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.path_safety import (
        validate_report_artifact_path,
    )

    with pytest.raises(ValidationError, match="allowed"):
        validate_report_artifact_path("/tmp-not-allowed/report.json")


def test_report_artifact_rejects_missing_path_under_non_directory_ancestor(tmp_path):
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.path_safety import (
        validate_report_artifact_path,
    )

    file_anchor = tmp_path / "state-file"
    file_anchor.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValidationError, match="non-directory ancestor"):
        validate_report_artifact_path(str(file_anchor / "missing" / "report.json"))


def test_relative_report_artifact_path_allows_symlinked_cwd(tmp_path, monkeypatch):
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.path_safety import (
        validate_report_artifact_path,
    )

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    symlinked_workspace = tmp_path / "workspace-link"
    symlinked_workspace.symlink_to(real_workspace, target_is_directory=True)

    monkeypatch.setattr(os, "getcwd", lambda: str(symlinked_workspace))

    validate_report_artifact_path("reports/switchover.json")
