from __future__ import annotations

from tests.release.scenarios.runtime_parity import (
    ComparisonRecord,
    compare_normalized_records,
)


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
