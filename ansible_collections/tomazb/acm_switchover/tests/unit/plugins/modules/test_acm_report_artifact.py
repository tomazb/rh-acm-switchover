"""Tests for the acm_report_artifact collection module."""

from __future__ import annotations

import json
import os

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils import artifacts
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.artifacts import (
    ArtifactWriteError,
    write_json_artifact,
)
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.validation import (
    ValidationError,
)
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact import (
    main,
)


def test_run_module_writes_report_json(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "report.json"

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": str(destination),
                "report": {"status": "pass", "phase": "preflight"},
                "mode": "0644",
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["exit"] == {"changed": True, "path": str(destination)}
    assert json.loads(destination.read_text()) == {
        "status": "pass",
        "phase": "preflight",
    }


def test_run_module_reports_unchanged_when_artifact_matches(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "report.json"
    destination.parent.mkdir()
    destination.write_text(json.dumps({"phase": "preflight", "status": "pass"}, indent=2, sort_keys=True) + "\n")
    destination.chmod(0o644)

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": str(destination),
                "report": {"status": "pass", "phase": "preflight"},
                "mode": "0644",
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["exit"] == {"changed": False, "path": str(destination)}


def test_run_module_check_mode_validates_without_writing(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "report.json"
    destination.parent.mkdir()

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": str(destination),
                "report": {"status": "pass"},
                "mode": "0644",
            }
            self.check_mode = True

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["exit"] == {"changed": True, "path": str(destination)}
    assert not destination.exists()


def test_run_module_rejects_unsafe_report_path(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": "./artifacts/../outside/report.json",
                "report": {"status": "fail"},
                "mode": "0644",
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact.AnsibleModule",
        FakeModule,
    )

    main()

    assert "Path traversal attempt" in captured["fail"]["msg"]


def test_write_json_artifact_accepts_valid_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    output_path, changed = write_json_artifact(
        report={"phase": "decommission"},
        destination="artifacts/decommission-summary.json",
        mode="0644",
    )

    destination = tmp_path / "artifacts" / "decommission-summary.json"
    assert output_path == "artifacts/decommission-summary.json"
    assert changed is True
    assert json.loads(destination.read_text()) == {"phase": "decommission"}


def test_write_json_artifact_accepts_valid_absolute_path_with_mode(tmp_path):
    destination = tmp_path / "artifacts" / "decommission-summary.json"

    output_path, changed = write_json_artifact(
        report={"phase": "decommission"},
        destination=str(destination),
        mode="0600",
    )

    assert output_path == str(destination)
    assert changed is True
    assert json.loads(destination.read_text()) == {"phase": "decommission"}
    assert destination.stat().st_mode & 0o777 == 0o600


def test_write_json_artifact_accepts_octal_prefix_mode(tmp_path):
    destination = tmp_path / "artifacts" / "decommission-summary.json"

    _output_path, changed = write_json_artifact(
        report={"phase": "decommission"},
        destination=str(destination),
        mode="0o600",
    )

    assert changed is True
    assert destination.stat().st_mode & 0o777 == 0o600


def test_write_json_artifact_creates_file_with_requested_mode(tmp_path, monkeypatch):
    destination = tmp_path / "artifacts" / "decommission-summary.json"
    captured = {}
    real_open = os.open

    def capture_open(path, flags, mode):
        captured["mode"] = mode
        return real_open(path, flags, mode)

    monkeypatch.setattr(artifacts.os, "open", capture_open)

    write_json_artifact(
        report={"phase": "decommission"},
        destination=str(destination),
        mode="0600",
    )

    assert captured["mode"] == 0o600
    assert destination.stat().st_mode & 0o777 == 0o600


def test_write_json_artifact_reports_unchanged_for_matching_file(tmp_path):
    destination = tmp_path / "artifacts" / "decommission-summary.json"

    write_json_artifact(
        report={"phase": "decommission"},
        destination=str(destination),
        mode="0600",
    )
    output_path, changed = write_json_artifact(
        report={"phase": "decommission"},
        destination=str(destination),
        mode="0600",
    )

    assert output_path == str(destination)
    assert changed is False
    assert destination.stat().st_mode & 0o777 == 0o600


def test_write_json_artifact_reports_changed_for_mode_only_update(tmp_path):
    destination = tmp_path / "artifacts" / "decommission-summary.json"

    write_json_artifact(
        report={"phase": "decommission"},
        destination=str(destination),
        mode="0644",
    )
    output_path, changed = write_json_artifact(
        report={"phase": "decommission"},
        destination=str(destination),
        mode="0600",
    )

    assert output_path == str(destination)
    assert changed is True
    assert destination.stat().st_mode & 0o777 == 0o600


def test_write_json_artifact_rejects_traversal_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="Path traversal attempt"):
        write_json_artifact(
            report={"phase": "decommission"},
            destination="artifacts/../outside/decommission-summary.json",
            mode="0644",
        )


def test_write_json_artifact_rejects_relative_parent_symlink_escape(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workspace)
    (workspace / "artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValidationError, match="symlink"):
        write_json_artifact(
            report={"phase": "decommission"},
            destination="artifacts/decommission-summary.json",
            mode="0644",
        )

    assert not (outside / "decommission-summary.json").exists()


def test_write_json_artifact_rejects_final_file_symlink(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workspace)
    (workspace / "artifacts").mkdir()
    (workspace / "artifacts" / "report.json").symlink_to(outside / "report.json")

    with pytest.raises(ValidationError, match="symlink"):
        write_json_artifact(
            report={"phase": "decommission"},
            destination="artifacts/report.json",
            mode="0644",
        )

    assert not (outside / "report.json").exists()


def test_write_json_artifact_rejects_invalid_mode_before_writing(tmp_path):
    destination = tmp_path / "artifacts" / "decommission-summary.json"

    with pytest.raises(ArtifactWriteError, match="Invalid report artifact mode"):
        write_json_artifact(
            report={"phase": "decommission"},
            destination=str(destination),
            mode="not-octal",
        )

    assert not destination.exists()


def test_write_json_artifact_rejects_negative_mode_before_writing(tmp_path):
    destination = tmp_path / "artifacts" / "decommission-summary.json"

    with pytest.raises(ArtifactWriteError, match="Invalid report artifact mode"):
        write_json_artifact(
            report={"phase": "decommission"},
            destination=str(destination),
            mode="-1",
        )

    assert not destination.exists()


def test_write_json_artifact_rejects_out_of_range_mode_before_writing(tmp_path):
    destination = tmp_path / "artifacts" / "decommission-summary.json"

    with pytest.raises(ArtifactWriteError, match="Invalid report artifact mode"):
        write_json_artifact(
            report={"phase": "decommission"},
            destination=str(destination),
            mode="1000",
        )

    assert not destination.exists()


def test_run_module_fails_with_actionable_message_on_permission_denied(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "report.json"

    def fail_open(path, flags, mode):
        raise PermissionError(13, "Permission denied", path)

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": str(destination),
                "report": {"status": "pass", "phase": "preflight"},
                "mode": "0644",
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(artifacts.os, "open", fail_open)
    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["fail"]["path"] == str(destination)
    assert "Cannot write report artifact to" in captured["fail"]["msg"]
    assert "Permission denied" in captured["fail"]["msg"]


def test_run_module_fails_deterministically_for_existing_unwritable_file(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "report.json"
    destination.parent.mkdir()
    destination.write_text(json.dumps({"phase": "preflight", "status": "fail"}, indent=2, sort_keys=True) + "\n")
    destination.chmod(0o644)

    def fail_open(path, flags, mode):
        raise PermissionError(13, "Permission denied", path)

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": str(destination),
                "report": {"status": "pass", "phase": "preflight"},
                "mode": "0644",
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(artifacts.os, "open", fail_open)
    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["fail"]["path"] == str(destination)
    assert "Cannot write report artifact to" in captured["fail"]["msg"]
    assert "Permission denied" in captured["fail"]["msg"]


def test_write_json_artifact_preserves_complex_payload_with_stable_key_order(tmp_path):
    destination = tmp_path / "artifacts" / "complex-report.json"
    report = {
        "summary": {
            "warning_failures": 0,
            "critical_failures": 0,
            "passed": True,
        },
        "source": "tomazb.acm_switchover",
        "status": "pass",
        "schema_version": "1.0",
        "results": [
            {
                "status": "pass",
                "message": "ready",
                "details": {
                    "managed_clusters": ["cluster-b", "cluster-a"],
                    "counts": {"warning": 0, "critical": 0},
                },
                "id": "preflight-cluster-status",
            }
        ],
        "phase": "preflight",
        "generated_at": "2026-05-20T00:00:00+00:00",
    }

    output_path, changed = write_json_artifact(
        report=report,
        destination=str(destination),
        mode="0644",
    )

    written = destination.read_text(encoding="utf-8")

    assert output_path == str(destination)
    assert changed is True
    assert json.loads(written) == report
    assert written == json.dumps(report, indent=2, sort_keys=True) + "\n"
    assert '"generated_at": "2026-05-20T00:00:00+00:00"' in written
    assert '"schema_version": "1.0"' in written
    assert '"source": "tomazb.acm_switchover"' in written
    assert written.index('"generated_at"') < written.index('"phase"') < written.index('"results"')
    assert written.index('"results"') < written.index('"schema_version"') < written.index('"source"')
    top_level_status_index = written.rindex('\n  "status"')
    assert written.index('"source"') < top_level_status_index < written.index('"summary"')
