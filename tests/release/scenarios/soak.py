"""Soak scenario aggregation helpers."""

from __future__ import annotations


def aggregate_soak_results(cycle_results: list[dict]) -> dict:
    failed_cycles = [
        item["scenario_id"]
        for item in cycle_results
        if item.get("required", True) and item.get("status") not in {"passed", "not_applicable"}
    ]
    return {
        "scenario_id": "soak",
        "status": "failed" if failed_cycles else "passed",
        "failed_cycles": failed_cycles,
        "cycle_count": len(cycle_results),
    }
