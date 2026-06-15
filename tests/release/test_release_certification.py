from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.release.adapters.ansible import SUPPORTED_SCENARIO_IDS as ANSIBLE_SCENARIOS
from tests.release.adapters.bash import SUPPORTED_SCENARIO_IDS as BASH_SCENARIOS
from tests.release.adapters.python_cli import SUPPORTED_SCENARIO_IDS as PYTHON_SCENARIOS
from tests.release.orchestrator import run_release_certification
from tests.release.reporting.render import render_release_report
from tests.release.reporting.summary import build_summary
from tests.release.scenarios.runtime_parity import (
    CAPABILITY_REQUIRED_FIELDS,
    compare_normalized_records,
)


def execute_ansible_scenarios(*, adapter, scenario_ids: Sequence[str]) -> list:
    results = []
    for scenario_id in scenario_ids:
        if scenario_id in ANSIBLE_SCENARIOS:
            result = adapter.execute(scenario_id)
            results.append(result.to_dict() if hasattr(result, "to_dict") else result)
    return results


def execute_python_scenarios(*, adapter, scenario_ids: Sequence[str]) -> list[dict]:
    results = []
    for scenario_id in scenario_ids:
        if scenario_id in PYTHON_SCENARIOS:
            result = adapter.execute(scenario_id)
            results.append(result.to_dict() if hasattr(result, "to_dict") else result)
    return results


def execute_bash_scenarios(*, adapter, scenario_ids: Sequence[str]) -> list:
    results = []
    for scenario_id in scenario_ids:
        if scenario_id in BASH_SCENARIOS:
            result = adapter.execute(scenario_id)
            results.append(result.to_dict() if hasattr(result, "to_dict") else result)
    return results


def execute_runtime_parity(*, normalized_sources: dict) -> list:
    comparisons = []
    for capability, by_stream in normalized_sources.items():
        comparisons.append(
            compare_normalized_records(
                capability=capability,
                scenario_id="runtime-parity",
                python=by_stream["python"],
                ansible=by_stream["ansible"],
                required_fields=CAPABILITY_REQUIRED_FIELDS[capability],
            )
        )
    return comparisons


def finalize_release_artifacts(*, artifacts, manifest: dict, summary_inputs: dict) -> dict:
    summary = build_summary(**summary_inputs)
    artifacts.write_json("summary.json", summary)
    report = render_release_report(summary, manifest)
    report_path = artifacts.run_dir / "release-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return summary


@pytest.mark.release
def test_release_certification(release_options, release_profile, release_artifacts) -> None:
    assert release_options.profile_path is not None
    summary = run_release_certification(
        release_options=release_options,
        release_profile=release_profile,
        artifacts=release_artifacts,
        repo_root=Path.cwd(),
    )
    assert summary["status"] == "passed"
