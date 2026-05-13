from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


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
    ScenarioDefinition("bash-discovery", False, ("bash",), False, False),
    ScenarioDefinition("bash-postflight", False, ("bash",), False, False),
    ScenarioDefinition("full-restore", False, ("python", "ansible"), True, True),
    ScenarioDefinition("checkpoint-resume", False, ("python", "ansible"), True, True),
    ScenarioDefinition("decommission", False, ("python", "ansible"), True, True),
    ScenarioDefinition("rbac-bootstrap", False, ("python", "ansible"), True, True),
    ScenarioDefinition("failure-injection", False, ("python", "ansible"), True, False),
    ScenarioDefinition("soak", False, ("python", "ansible"), True, True),
)
SCENARIOS_BY_ID = {item.id: item for item in V1_SCENARIOS}
PREREQUISITES = ("static-gates", "lab-readiness", "baseline-check")
POST_MUTATION = ("runtime-parity", "final-baseline-check")


class ScenarioSelection(Protocol):
    id: str
    required: bool | None
    streams: tuple[str, ...]


def _hash_matrix(scenarios: tuple[ScenarioDefinition, ...], selected_streams: tuple[str, ...]) -> str:
    payload = json.dumps(
        {
            "scenarios": [
                {
                    "id": scenario.id,
                    "required": scenario.required,
                    "streams": scenario.streams,
                }
                for scenario in scenarios
            ],
            "streams": selected_streams,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_release_matrix(
    *,
    enabled_streams: tuple[str, ...],
    scenario_filters: tuple[str, ...],
    stream_filters: tuple[str, ...],
    profile_scenarios: tuple[ScenarioSelection, ...] = (),
) -> SelectedReleaseMatrix:
    unknown = [item for item in scenario_filters if item not in SCENARIOS_BY_ID]
    if unknown:
        raise ValueError(f"unknown release scenario: {unknown[0]}")
    selected_streams = tuple(stream for stream in enabled_streams if not stream_filters or stream in stream_filters)
    profile_by_id = {item.id: item for item in profile_scenarios}
    unknown_profile_ids = [item for item in profile_by_id if item not in SCENARIOS_BY_ID]
    if unknown_profile_ids:
        raise ValueError(f"unknown profile release scenario: {unknown_profile_ids[0]}")
    profile_ids = tuple(profile_by_id) if profile_scenarios else ()
    if scenario_filters:
        requested = tuple(dict.fromkeys(scenario_filters))
        if profile_ids:
            missing = [item for item in requested if item not in profile_by_id]
            if missing:
                raise ValueError(f"release scenario is not declared by profile: {missing[0]}")
        mutating = any(SCENARIOS_BY_ID[item].mutates_lab for item in requested)
        scenario_ids = PREREQUISITES + requested + (POST_MUTATION if mutating else ())
        scenario_ids = tuple(dict.fromkeys(scenario_ids))
    elif profile_ids:
        scenario_ids = profile_ids
    else:
        scenario_ids = tuple(item.id for item in V1_SCENARIOS if item.required)
    scenarios = []
    for scenario_id in scenario_ids:
        definition = SCENARIOS_BY_ID[scenario_id]
        profile_item = profile_by_id.get(scenario_id)
        streams = definition.streams
        required = definition.required
        if profile_item is not None:
            streams = profile_item.streams or streams
            required = definition.required if profile_item.required is None else bool(profile_item.required)
        if streams != ("local",):
            streams = tuple(stream for stream in streams if stream in selected_streams)
        scenarios.append(
            ScenarioDefinition(
                id=definition.id,
                required=required,
                streams=streams,
                mutates_lab=definition.mutates_lab,
                runtime_parity_required=definition.runtime_parity_required,
            )
        )
    return SelectedReleaseMatrix(
        scenarios=tuple(scenarios),
        selected_streams=selected_streams,
        matrix_hash=_hash_matrix(tuple(scenarios), selected_streams),
    )
