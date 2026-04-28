"""Schema stability tests for collection report fields consumed by release normalizers.

These tests import actual collection module functions to verify field shapes do not
drift undetected. Tests that require ansible-core skip automatically when it is absent.
"""

from __future__ import annotations

import pytest

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    build_checkpoint_record,
)


def test_checkpoint_fields_consumed_by_release_normalizer_are_stable() -> None:
    checkpoint = build_checkpoint_record(phase="preflight", operational_data={})

    assert checkpoint["schema_version"] == "1.0"
    assert checkpoint["phase"] == "preflight"
    assert checkpoint["completed_phases"] == []
    assert checkpoint["errors"] == []
    assert checkpoint["report_refs"] == []
    assert "updated_at" in checkpoint
    assert "created_at" in checkpoint


def test_preflight_report_fields_consumed_by_release_normalizer_are_stable() -> None:
    acm_preflight_report = pytest.importorskip(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report",
        reason="ansible-core not installed; skipping ansible-dependent schema test",
    )
    build_preflight_report = acm_preflight_report.build_preflight_report

    results = [{"id": "acm-version", "severity": "critical", "status": "pass", "message": "ok"}]
    report = build_preflight_report(phase="preflight", results=results, hubs={})

    assert report["schema_version"] == "1.0"
    assert report["status"] == "pass"
    assert isinstance(report["summary"]["critical_failures"], int)
    assert isinstance(report["summary"]["warning_failures"], int)
    assert report["results"][0]["id"] == "acm-version"
    assert "generated_at" in report
    assert "hubs" in report


def test_preflight_report_status_is_fail_when_critical_results_present() -> None:
    acm_preflight_report = pytest.importorskip(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report",
        reason="ansible-core not installed; skipping ansible-dependent schema test",
    )
    build_preflight_report = acm_preflight_report.build_preflight_report

    results = [{"id": "acm-version", "severity": "critical", "status": "fail", "message": "mismatch"}]
    report = build_preflight_report(phase="preflight", results=results, hubs={})

    assert report["status"] == "fail"
    assert report["summary"]["critical_failures"] == 1


def test_summarize_preflight_results_counts_by_severity() -> None:
    acm_preflight_report = pytest.importorskip(
        "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report",
        reason="ansible-core not installed; skipping ansible-dependent schema test",
    )
    summarize_preflight_results = acm_preflight_report.summarize_preflight_results

    results = [
        {"id": "c1", "severity": "critical", "status": "fail", "message": ""},
        {"id": "w1", "severity": "warning", "status": "fail", "message": ""},
        {"id": "p1", "severity": "critical", "status": "pass", "message": ""},
    ]
    summary = summarize_preflight_results(results)

    assert summary["critical_failures"] == 1
    assert summary["warning_failures"] == 1
    assert summary["passed"] is False
