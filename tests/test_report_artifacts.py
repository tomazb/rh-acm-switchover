"""Tests for Python CLI machine-readable report artifacts."""

import json
from types import SimpleNamespace

import pytest

from lib.exceptions import SecurityValidationError
from lib.report_artifacts import build_operation_report, write_json_report_artifact


def test_build_operation_report_uses_collection_compatible_schema():
    report = build_operation_report(
        report_type="switchover",
        status="pass",
        source="python-cli",
        args=SimpleNamespace(
            primary_context="primary",
            secondary_context="secondary",
            method="passive",
            old_hub_action="secondary",
            restore_only=False,
            decommission=False,
        ),
        state_snapshot={
            "current_phase": "completed",
            "completed_steps": [{"name": "preflight"}],
            "errors": [],
            "config": {"secondary_version": "2.12.3"},
        },
        phases={"preflight": {"phase": "preflight", "status": "pass"}},
    )

    assert report["schema_version"] == "1.0"
    assert report["source"] == "python-cli"
    assert report["type"] == "switchover"
    assert report["status"] == "pass"
    assert report["summary"]["completed_steps"] == 1
    assert report["hubs"]["primary"]["context"] == "primary"
    assert report["phases"]["preflight"]["status"] == "pass"


def test_build_operation_report_does_not_label_switchover_as_preflight():
    report = build_operation_report(
        report_type="switchover",
        status="pass",
        source="python-cli",
        args=SimpleNamespace(
            primary_context="primary",
            secondary_context="secondary",
            method="passive",
            old_hub_action="secondary",
            restore_only=False,
            decommission=False,
        ),
        state_snapshot={
            "current_phase": "completed",
            "completed_steps": [{"name": "preflight"}],
            "errors": [],
            "config": {"preflight_results": [{"check": "ACM version", "passed": True}]},
        },
        phases={"preflight": {"phase": "preflight", "status": "pass"}},
    )

    assert "phase" not in report
    assert "results" not in report


def test_write_json_report_artifact_validates_path_and_writes_json(tmp_path):
    destination = tmp_path / "reports" / "switchover-report.json"

    written = write_json_report_artifact({"schema_version": "1.0"}, str(destination))

    assert written == str(destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == {"schema_version": "1.0"}


def test_write_json_report_artifact_allows_nested_absolute_report_dirs(tmp_path):
    destination = tmp_path / "reports" / "run-1" / "switchover-report.json"

    written = write_json_report_artifact({"schema_version": "1.0"}, str(destination))

    assert written == str(destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == {"schema_version": "1.0"}


def test_write_json_report_artifact_rejects_unsafe_paths():
    with pytest.raises(SecurityValidationError):
        write_json_report_artifact({"schema_version": "1.0"}, "./artifacts/../outside/report.json")


def test_write_json_report_artifact_rejects_relative_parent_symlink_escape(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workspace)
    (workspace / "artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SecurityValidationError, match="symlink"):
        write_json_report_artifact({"schema_version": "1.0"}, "artifacts/report.json")

    assert not (outside / "report.json").exists()


def test_write_json_report_artifact_rejects_final_file_symlink(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workspace)
    (workspace / "artifacts").mkdir()
    (workspace / "artifacts" / "report.json").symlink_to(outside / "report.json")

    with pytest.raises(SecurityValidationError, match="symlink"):
        write_json_report_artifact({"schema_version": "1.0"}, "artifacts/report.json")

    assert not (outside / "report.json").exists()
