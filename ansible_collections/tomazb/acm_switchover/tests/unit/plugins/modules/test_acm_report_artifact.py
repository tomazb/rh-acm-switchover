"""Tests for the acm_report_artifact collection module."""

from __future__ import annotations

import json

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_report_artifact import main


def test_run_module_writes_report_json(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "report.json"

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": str(destination),
                "report": {"status": "pass", "phase": "preflight"},
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
    assert json.loads(destination.read_text()) == {"status": "pass", "phase": "preflight"}


def test_run_module_check_mode_validates_without_writing(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "report.json"
    destination.parent.mkdir()

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": str(destination),
                "report": {"status": "pass"},
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

    assert captured["exit"] == {"changed": False, "path": str(destination)}
    assert not destination.exists()


def test_run_module_rejects_unsafe_report_path(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "path": "./artifacts/../outside/report.json",
                "report": {"status": "fail"},
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
