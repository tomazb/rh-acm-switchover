from __future__ import annotations

from tests.release.scenarios.runtime_parity import ComparisonRecord


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
