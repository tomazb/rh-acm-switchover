from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioDefinition:
    id: str
    required: bool
    streams: tuple[str, ...]
    mutates_lab: bool
    runtime_parity_required: bool


@dataclass(frozen=True)
class SelectedReleaseMatrix:
    scenarios: tuple[ScenarioDefinition, ...]
    selected_streams: tuple[str, ...]
    matrix_hash: str

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.scenarios)


V1_SCENARIOS: tuple[ScenarioDefinition, ...] = (
    ScenarioDefinition("static-gates", True, ("local",), False, False),
    ScenarioDefinition("lab-readiness", True, ("local",), False, False),
    ScenarioDefinition("baseline-check", True, ("local",), False, False),
    ScenarioDefinition("preflight", True, ("bash", "python", "ansible"), False, True),
    ScenarioDefinition("python-passive-switchover", True, ("python",), True, True),
    ScenarioDefinition("ansible-passive-switchover", True, ("ansible",), True, True),
    ScenarioDefinition("python-restore-only", True, ("python",), True, True),
    ScenarioDefinition("ansible-restore-only", True, ("ansible",), True, True),
    ScenarioDefinition("argocd-managed-switchover", True, ("python", "ansible"), True, True),
    ScenarioDefinition("runtime-parity", True, ("local",), False, True),
    ScenarioDefinition("final-baseline-check", True, ("local",), False, False),
    ScenarioDefinition("full-restore", False, ("python", "ansible"), True, True),
    ScenarioDefinition("checkpoint-resume", False, ("python", "ansible"), True, True),
    ScenarioDefinition("decommission", False, ("python", "ansible"), True, True),
    ScenarioDefinition("failure-injection", False, ("python", "ansible"), True, False),
    ScenarioDefinition("soak", False, ("python", "ansible"), True, True),
)
SCENARIOS_BY_ID = {item.id: item for item in V1_SCENARIOS}
PREREQUISITES = ("static-gates", "lab-readiness", "baseline-check")
POST_MUTATION = ("runtime-parity", "final-baseline-check")


def _hash_matrix(scenario_ids: tuple[str, ...], selected_streams: tuple[str, ...]) -> str:
    payload = json.dumps({"scenarios": scenario_ids, "streams": selected_streams}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_release_matrix(
    *,
    enabled_streams: tuple[str, ...],
    scenario_filters: tuple[str, ...],
    stream_filters: tuple[str, ...],
) -> SelectedReleaseMatrix:
    unknown = [item for item in scenario_filters if item not in SCENARIOS_BY_ID]
    if unknown:
        raise ValueError(f"unknown release scenario: {unknown[0]}")
    selected_streams = tuple(stream for stream in enabled_streams if not stream_filters or stream in stream_filters)
    if scenario_filters:
        requested = tuple(dict.fromkeys(scenario_filters))
        mutating = any(SCENARIOS_BY_ID[item].mutates_lab for item in requested)
        scenario_ids = PREREQUISITES + requested + (POST_MUTATION if mutating else ())
        scenario_ids = tuple(dict.fromkeys(scenario_ids))
    else:
        scenario_ids = tuple(item.id for item in V1_SCENARIOS if item.required)
    scenarios = tuple(SCENARIOS_BY_ID[item] for item in scenario_ids)
    return SelectedReleaseMatrix(
        scenarios=scenarios,
        selected_streams=selected_streams,
        matrix_hash=_hash_matrix(scenario_ids, selected_streams),
    )
