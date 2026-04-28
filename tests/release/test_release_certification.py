from __future__ import annotations

from collections.abc import Sequence

import pytest

# Explicitly enumerated Python scenarios supported by PythonCliAdapter.build_command().
# To add a scenario, also update build_command() and REPORT_NAMES in python_cli.py.
PYTHON_SCENARIOS = frozenset({
    "preflight",
    "python-passive-switchover",
    "python-restore-only",
    "argocd-managed-switchover",
})


def execute_python_scenarios(*, adapter, scenario_ids: Sequence[str]) -> list[dict]:
    results = []
    for scenario_id in scenario_ids:
        if scenario_id in PYTHON_SCENARIOS:
            result = adapter.execute(scenario_id)
            results.append(result.to_dict() if hasattr(result, "to_dict") else result)
    return results


@pytest.mark.release
def test_release_certification(release_options, baseline_manager) -> None:
    assert release_options.profile_path is not None
