"""Tests for the acm_preflight_report collection module."""

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
        hubs={"primary": {"context": "primary-hub"}, "secondary": {"context": "secondary-hub"}},
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
        hubs={"primary": {"context": "primary-hub"}, "secondary": {"context": "secondary-hub"}},
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
