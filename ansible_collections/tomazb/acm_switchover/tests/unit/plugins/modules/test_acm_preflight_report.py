"""Tests for the acm_preflight_report collection module."""

import json

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report import (
    build_preflight_report,
    main,
    summarize_preflight_results,
)


def test_report_status_is_fail_when_critical_finding_fails():
    report = build_preflight_report(
        phase="preflight",
        results=[
            {
                "id": "preflight-version-compatibility",
                "severity": "critical",
                "status": "fail",
                "message": "versions are incompatible",
                "details": {},
                "recommended_action": "Upgrade the secondary hub",
            }
        ],
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
    )
    assert report["status"] == "fail"
    assert report["phase"] == "preflight"


def test_report_status_is_pass_when_only_warnings_exist():
    report = build_preflight_report(
        phase="preflight",
        results=[
            {
                "id": "preflight-kubeconfig-duplicate-users",
                "severity": "warning",
                "status": "fail",
                "message": "duplicate user names found",
                "details": {},
                "recommended_action": "Regenerate kubeconfigs",
            }
        ],
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
    )
    assert report["status"] == "pass"


def test_summary_counts_failures_by_severity():
    summary = summarize_preflight_results(
        [
            {"severity": "critical", "status": "fail"},
            {"severity": "warning", "status": "fail"},
            {"severity": "info", "status": "pass"},
        ]
    )
    assert summary["critical_failures"] == 1
    assert summary["warning_failures"] == 1
    assert summary["passed"] is False


def test_run_module_rejects_unsafe_report_path(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "phase": "preflight",
                "results": [],
                "hubs": {"secondary": {"context": "secondary-hub"}},
                "path": "./artifacts/../outside/preflight-report.json",
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.AnsibleModule",
        FakeModule,
    )

    main()

    assert "Path traversal attempt" in captured["fail"]["msg"]
    assert captured["fail"]["path"] == "./artifacts/../outside/preflight-report.json"


def test_run_module_check_mode_rejects_unsafe_report_path(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "phase": "preflight",
                "results": [],
                "hubs": {"secondary": {"context": "secondary-hub"}},
                "path": "./artifacts/../outside/preflight-report.json",
            }
            self.check_mode = True

        def exit_json(self, **kwargs):
            raise AssertionError(f"unexpected exit_json: {kwargs}")

        def fail_json(self, **kwargs):
            captured["fail"] = kwargs

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.AnsibleModule",
        FakeModule,
    )

    main()

    assert "Path traversal attempt" in captured["fail"]["msg"]
    assert captured["fail"]["path"] == "./artifacts/../outside/preflight-report.json"


def test_run_module_check_mode_reports_planned_report_without_writing(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "preflight-report.json"
    destination.parent.mkdir()

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "phase": "preflight",
                "results": [],
                "hubs": {"secondary": {"context": "secondary-hub"}},
                "path": str(destination),
            }
            self.check_mode = True

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.AnsibleModule",
        FakeModule,
    )

    main()

    assert captured["exit"]["changed"] is True
    assert captured["exit"]["path"] == str(destination)
    assert captured["exit"]["report"]["phase"] == "preflight"
    assert not destination.exists()


def test_run_module_check_mode_reports_unchanged_when_artifact_matches(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "preflight-report.json"
    destination.parent.mkdir()

    class FakeDateTime:
        @classmethod
        def now(cls, timezone):
            return cls()

        def isoformat(self):
            return "2026-05-20T00:00:00+00:00"

    report = {
        "schema_version": "1.0",
        "generated_at": "2026-05-20T00:00:00+00:00",
        "source": "tomazb.acm_switchover",
        "phase": "preflight",
        "status": "pass",
        "summary": {"passed": True, "critical_failures": 0, "warning_failures": 0},
        "hubs": {"secondary": {"context": "secondary-hub"}},
        "results": [],
    }
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    destination.chmod(0o644)

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "phase": "preflight",
                "results": [],
                "hubs": {"secondary": {"context": "secondary-hub"}},
                "path": str(destination),
            }
            self.check_mode = True

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.AnsibleModule",
        FakeModule,
    )
    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.datetime",
        FakeDateTime,
    )

    main()

    assert captured["exit"]["changed"] is False
    assert captured["exit"]["path"] == str(destination)
