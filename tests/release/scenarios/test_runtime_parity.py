from __future__ import annotations

import json
from pathlib import Path

from tests.release.reporting.artifacts import ReleaseArtifacts
from tests.release.scenarios.runtime_parity import (
    ComparisonRecord,
    compare_normalized_records,
    normalize_argocd_management,
    normalize_preflight,
    write_runtime_parity_artifact,
)
from tests.release.test_release_certification import execute_runtime_parity


def test_comparison_record_serializes_required_fields() -> None:
    record = ComparisonRecord(
        capability="preflight validation",
        scenario_id="preflight",
        streams=("python", "ansible"),
        status="passed",
        required_fields=("status", "check_ids"),
        differences=[],
        evidence_paths=("scenario-results.json",),
    )

    payload = record.to_dict()

    assert payload["capability"] == "preflight validation"
    assert payload["streams"] == ["python", "ansible"]
    assert payload["required_fields"] == ["status", "check_ids"]


def test_compare_normalized_records_passes_equal_required_fields() -> None:
    record = compare_normalized_records(
        capability="activation",
        scenario_id="python-passive-switchover",
        python={"status": "passed", "restore_name": "restore-acm", "duration": 10},
        ansible={"status": "passed", "restore_name": "restore-acm", "duration": 12},
        required_fields=("status", "restore_name"),
    )

    assert record.status == "passed"
    assert record.differences == []


def test_compare_normalized_records_fails_missing_source_field() -> None:
    record = compare_normalized_records(
        capability="activation",
        scenario_id="python-passive-switchover",
        python={"status": "passed"},
        ansible={"status": "passed", "restore_name": "restore-acm"},
        required_fields=("status", "restore_name"),
    )

    assert record.status == "failed"
    assert record.differences[0]["field"] == "restore_name"


def test_normalize_preflight_sorts_check_sets() -> None:
    normalized = normalize_preflight(
        {
            "status": "passed",
            "critical_failure_count": 0,
            "warning_failure_count": 1,
            "check_ids": ["z", "a"],
            "failed_check_ids": ["z"],
        }
    )

    assert normalized["check_ids"] == ["a", "z"]
    assert normalized["failed_check_ids"] == ["z"]


def test_normalize_argocd_management_uses_discovered_application_sets() -> None:
    normalized = normalize_argocd_management(
        {
            "selected_applications": ["app-b", "app-a"],
            "paused_applications": ["app-a"],
            "resumed_applications": ["app-b"],
            "resume_failures": [],
            "conflict_allowlist_used": False,
        }
    )

    assert normalized["selected_applications"] == ["app-a", "app-b"]


def test_write_runtime_parity_artifact_sets_failed_status(tmp_path: Path) -> None:
    artifacts = ReleaseArtifacts.create(root=tmp_path, run_id="run-1")
    failed = ComparisonRecord(
        "activation",
        "python-passive-switchover",
        ("python", "ansible"),
        "failed",
        ("status",),
        [{"field": "status"}],
        (),
    )

    write_runtime_parity_artifact(artifacts=artifacts, comparisons=[failed])

    payload = json.loads((artifacts.run_dir / "runtime-parity.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["comparisons"][0]["capability"] == "activation"


def test_execute_runtime_parity_compares_matching_sources(tmp_path: Path) -> None:
    comparisons = execute_runtime_parity(
        normalized_sources={
            "preflight validation": {
                "python": {
                    "status": "passed",
                    "critical_failure_count": 0,
                    "warning_failure_count": 0,
                    "check_ids": ["a"],
                    "failed_check_ids": [],
                },
                "ansible": {
                    "status": "passed",
                    "critical_failure_count": 0,
                    "warning_failure_count": 0,
                    "check_ids": ["a"],
                    "failed_check_ids": [],
                },
            }
        }
    )

    assert comparisons[0].status == "passed"
