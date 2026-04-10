"""Tests for the acm_preflight_report collection module."""

from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report import (
    build_preflight_report,
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
