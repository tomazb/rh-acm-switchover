"""Tests for the acm_preflight_report collection module."""

import json
from unittest.mock import patch

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


def test_report_hub_identity_excludes_kubeconfig_paths():
    report = build_preflight_report(
        phase="preflight",
        results=[],
        hubs={
            "primary": {
                "context": "primary-hub",
                "kubeconfig": "/tmp/primary-admin.kubeconfig",
            },
            "secondary": {
                "context": "secondary-hub",
                "kubeconfig": "/tmp/secondary-admin.kubeconfig",
                "cluster_uid": "uid-secondary-from-hubs",
            },
        },
        hub_identities={
            "primary": {"cluster_uid": "uid-primary"},
            "secondary": {"cluster_uid": "uid-secondary-from-identities"},
        },
    )

    assert report["hubs"] == {
        "primary": {"context": "primary-hub", "cluster_uid": "uid-primary"},
        "secondary": {
            "context": "secondary-hub",
            "cluster_uid": "uid-secondary-from-hubs",
        },
    }
    assert "kubeconfig" not in json.dumps(report["hubs"])


def test_run_module_empty_findings_passes_without_changes(monkeypatch):
    captured = {}

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "phase": "preflight",
                "results": [],
                "hubs": {
                    "primary": {"context": "primary-hub"},
                    "secondary": {"context": "secondary-hub"},
                },
                "path": None,
            }
            self.check_mode = False

        def exit_json(self, **kwargs):
            captured["exit"] = kwargs

        def fail_json(self, **kwargs):
            raise AssertionError(f"unexpected fail_json: {kwargs}")

    monkeypatch.setattr(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.AnsibleModule",
        FakeModule,
    )

    main()

    result = captured["exit"]
    assert result["changed"] is False
    assert result["report"]["status"] == "pass"
    assert result["report"]["summary"]["passed"] is True
    assert result["report"]["summary"]["critical_failures"] == 0
    assert result["report"]["summary"]["warning_failures"] == 0


def test_report_status_is_pass_when_only_warning_and_info_findings_exist():
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
            },
            {
                "id": "preflight-observability-optional",
                "severity": "info",
                "status": "fail",
                "message": "observability not installed",
                "details": {},
                "recommended_action": "None",
            },
        ],
        hubs={
            "primary": {"context": "primary-hub"},
            "secondary": {"context": "secondary-hub"},
        },
    )

    assert report["status"] == "pass"
    assert report["summary"]["passed"] is True
    assert report["summary"]["critical_failures"] == 0
    assert report["summary"]["warning_failures"] == 1


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


def test_run_module_check_mode_does_not_write_report_but_reports_would_change(tmp_path, monkeypatch):
    captured = {}
    destination = tmp_path / "artifacts" / "preflight-report.json"
    destination.parent.mkdir()

    class FakeModule:
        def __init__(self, *args, **kwargs):
            self.params = {
                "phase": "preflight",
                "results": [
                    {
                        "id": "preflight-version-compatibility",
                        "severity": "critical",
                        "status": "fail",
                        "message": "versions are incompatible",
                        "details": {},
                        "recommended_action": "Upgrade the secondary hub",
                    }
                ],
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

    with patch("ansible_collections.tomazb.acm_switchover.plugins.module_utils.artifacts.os.open") as mock_open:
        main()

    assert mock_open.called is False
    assert captured["exit"]["changed"] is True
    assert captured["exit"]["path"] == str(destination)
    assert captured["exit"]["report"]["phase"] == "preflight"
    assert captured["exit"]["report"]["status"] == "fail"
    assert not destination.exists()


def test_run_module_changed_verdict_is_mode_independent_for_create(tmp_path, monkeypatch):
    """check_mode and execute mode must agree on the changed verdict for a would-be create."""
    verdicts = {}
    for check_mode in (True, False):
        captured = {}
        destination = tmp_path / f"artifacts-{check_mode}" / "preflight-report.json"
        destination.parent.mkdir()

        class FakeModule:
            def __init__(self, *args, **kwargs):
                self.params = {
                    "phase": "preflight",
                    "results": [],
                    "hubs": {"secondary": {"context": "secondary-hub"}},
                    "path": str(destination),
                }
                self.check_mode = check_mode

            def exit_json(self, **kwargs):
                captured["exit"] = kwargs

            def fail_json(self, **kwargs):
                raise AssertionError(f"unexpected fail_json: {kwargs}")

        monkeypatch.setattr(
            "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.AnsibleModule",
            FakeModule,
        )
        main()
        verdicts[check_mode] = captured["exit"]["changed"]
        assert destination.exists() is (not check_mode)

    assert verdicts[True] is True
    assert verdicts[False] is True


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
