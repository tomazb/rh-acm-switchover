"""Tests for Python CLI machine-readable report artifacts."""

import json
import os
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


def test_argocd_report_counts_only_confirmed_paused_entries():
    """F2: the report artifact must agree with ArgocdPauseRegister.status()."""
    report = build_operation_report(
        report_type="switchover",
        status="pass",
        source="python-cli",
        args=SimpleNamespace(primary_context="primary", secondary_context="secondary"),
        state_snapshot={
            "current_phase": "completed",
            "completed_steps": [],
            "errors": [],
            "config": {
                "argocd_run_id": "run-1",
                "argocd_paused_apps": [
                    {"hub": "primary", "namespace": "argocd", "name": "app-1", "pause_applied": True},
                    {"hub": "primary", "namespace": "argocd", "name": "app-2", "pause_applied": False},
                    {"hub": "primary", "namespace": "argocd", "name": "legacy-dry", "dry_run": True},
                    "garbage",
                ],
            },
        },
    )

    assert report["argocd"]["run_id"] == "run-1"
    assert report["argocd"]["summary"] == {"paused": 1, "restored": 0}


def test_report_writer_preserves_protected_checkpoint_inode(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_bytes(b'{ "current_phase": "init" }\n')
    destination = tmp_path / "report.json"
    destination.hardlink_to(checkpoint)
    original = checkpoint.read_bytes()

    with pytest.raises(ValueError, match="conflicts with the state file"):
        write_json_report_artifact({"status": "pass"}, str(destination), protected_path=str(checkpoint))

    assert checkpoint.read_bytes() == original


def test_report_writer_leaves_protected_missing_checkpoint_absent(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"

    with pytest.raises(ValueError, match="conflicts with the state file"):
        write_json_report_artifact({"status": "pass"}, str(checkpoint), protected_path=str(checkpoint))

    assert not checkpoint.exists()


@pytest.mark.skipif(
    not (hasattr(os, "O_PATH") or hasattr(os, "O_SEARCH")) or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="requires a search-only directory handle and an unprivileged user",
)
def test_dry_run_report_directory_does_not_require_read_permission(tmp_path):
    directory = tmp_path / "reports"
    directory.mkdir()
    destination = directory / "report.json"
    checkpoint = tmp_path / "checkpoint.json"
    directory.chmod(0o300)
    try:
        write_json_report_artifact({"status": "pass"}, str(destination), protected_path=str(checkpoint))
    finally:
        directory.chmod(0o700)

    assert json.loads(destination.read_text()) == {"status": "pass"}
    assert not checkpoint.exists()


def test_report_writer_does_not_create_protected_checkpoint_as_directory(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    destination = checkpoint / "reports" / "report.json"

    with pytest.raises(ValueError, match="conflicts with the state file"):
        write_json_report_artifact({"status": "pass"}, str(destination), protected_path=str(checkpoint))

    assert not checkpoint.exists()


def test_protected_report_creates_nested_parents_through_validated_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    actual = tmp_path / "actual"
    actual.mkdir()
    (tmp_path / "alias").symlink_to(actual, target_is_directory=True)
    checkpoint = tmp_path / "checkpoint.json"
    destination = "alias/nested/reports/report.json"

    assert write_json_report_artifact({"status": "pass"}, destination, protected_path=str(checkpoint)) == destination

    assert json.loads((actual / "nested/reports/report.json").read_text()) == {"status": "pass"}
    assert not checkpoint.exists()
