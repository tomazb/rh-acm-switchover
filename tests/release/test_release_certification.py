from __future__ import annotations

from collections.abc import Sequence

import pytest

from tests.release.scenarios.runtime_parity import (
    CAPABILITY_REQUIRED_FIELDS,
    compare_normalized_records,
)
from tests.release.scenarios.catalog import SCENARIOS_BY_ID

# Derived from catalog.py — automatically in sync with the Python stream.
# When adding a new Python scenario, update build_command() and REPORT_NAMES
# in tests/release/adapters/python_cli.py.
PYTHON_SCENARIOS = frozenset(
    scenario_id
    for scenario_id, defn in SCENARIOS_BY_ID.items()
    if "python" in defn.streams
)

# V1 Ansible scenarios executed by AnsibleAdapter.
# When adding a new Ansible scenario, update PLAYBOOKS and REPORT_NAMES
# in tests/release/adapters/ansible.py.
ANSIBLE_SCENARIOS = {"preflight", "ansible-passive-switchover", "ansible-restore-only", "argocd-managed-switchover"}


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


@pytest.mark.release
def test_release_certification(release_options, baseline_manager) -> None:
    assert release_options.profile_path is not None
