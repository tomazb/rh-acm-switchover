"""Schema stability tests for collection report fields consumed by release normalizers.

These tests import actual collection module functions to verify field shapes do not
drift undetected. Tests that require ansible-core skip automatically when it is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    build_checkpoint_record,
)

_PLAYBOOKS_DIR = Path(__file__).resolve().parents[2] / "playbooks"


def _find_task_set_fact(plays: list, task_name: str) -> dict | None:
    """Recursively search all plays/tasks for a set_fact task by name."""

    def _search(tasks: list | None) -> dict | None:
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            if task.get("name") == task_name:
                sf = task.get("ansible.builtin.set_fact", {})
                return sf.get("acm_switchover_report")
            for key in ("block", "rescue", "always", "tasks"):
                found = _search(task.get(key))
                if found is not None:
                    return found
        return None

    for play in plays or []:
        for key in ("tasks", "pre_tasks", "post_tasks"):
            found = _search(play.get(key))
            if found is not None:
                return found
    return None


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


def test_switchover_report_contract_top_level_keys_are_stable() -> None:
    plays = yaml.safe_load((_PLAYBOOKS_DIR / "switchover.yml").read_text())
    contract = _find_task_set_fact(plays, "Build switchover report contract")

    assert contract is not None, "Build switchover report contract task not found in switchover.yml"
    assert contract["schema_version"] == "1.0"
    assert contract["source"] == "tomazb.acm_switchover"
    assert "argocd" in contract
    assert "phases" in contract


def test_restore_only_report_contract_top_level_keys_are_stable() -> None:
    plays = yaml.safe_load((_PLAYBOOKS_DIR / "restore_only.yml").read_text())
    contract = _find_task_set_fact(plays, "Build restore-only report contract")

    assert contract is not None, "Build restore-only report contract task not found in restore_only.yml"
    assert contract["schema_version"] == "1.0"
    assert contract["source"] == "tomazb.acm_switchover"
    assert contract["operation"] == "restore_only"
    assert "argocd" in contract
    assert "phases" in contract
