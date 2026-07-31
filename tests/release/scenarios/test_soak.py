from __future__ import annotations

from tests.release.scenarios.soak import aggregate_soak_results


def test_soak_aggregation_fails_when_any_required_cycle_fails() -> None:
    result = aggregate_soak_results(
        [
            {"scenario_id": "soak/cycle-1/python", "status": "passed", "required": True},
            {"scenario_id": "soak/cycle-2/python", "status": "failed", "required": True},
        ]
    )

    assert result["status"] == "failed"
    assert result["failed_cycles"] == ["soak/cycle-2/python"]


def test_soak_aggregation_passes_all_required_cycles() -> None:
    result = aggregate_soak_results(
        [
            {"scenario_id": "soak/cycle-1/python", "status": "passed", "required": True},
            {"scenario_id": "soak/cycle-1/ansible", "status": "passed", "required": True},
        ]
    )

    assert result["status"] == "passed"
