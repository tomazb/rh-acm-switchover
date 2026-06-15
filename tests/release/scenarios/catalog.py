from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ScenarioLifecycle:
    initial_primary: str | None
    final_primary: str | None
    mutates_lab: bool
    reset_required: bool
    allowed_followup_scenarios: tuple[str, ...] = ()
    recovery_strategy: str = "none"


@dataclass(frozen=True)
class ScenarioSupport:
    certification_supported: bool = True
    unsupported_reason: str | None = None
    unsupported_stream_reasons: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioDefinition:
    id: str
    required: bool
    streams: tuple[str, ...]
    mutates_lab: bool
    runtime_parity_required: bool
    lifecycle: ScenarioLifecycle
    support: ScenarioSupport


@dataclass(frozen=True)
class SelectedReleaseMatrix:
    scenarios: tuple[ScenarioDefinition, ...]
    selected_streams: tuple[str, ...]
    matrix_hash: str

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.scenarios)


_STATIC_LIFECYCLE = ScenarioLifecycle(
    initial_primary=None,
    final_primary=None,
    mutates_lab=False,
    reset_required=False,
)
_SWITCHOVER_LIFECYCLE = ScenarioLifecycle(
    initial_primary="primary",
    final_primary="secondary",
    mutates_lab=True,
    reset_required=True,
    recovery_strategy="lab-reset-required",
)
_RESTORE_ONLY_LIFECYCLE = ScenarioLifecycle(
    initial_primary="secondary",
    final_primary="secondary",
    mutates_lab=True,
    reset_required=True,
    recovery_strategy="lab-reset-required",
)
_SECONDARY_FINAL_LIFECYCLE = ScenarioLifecycle(
    initial_primary="secondary",
    final_primary="secondary",
    mutates_lab=True,
    reset_required=True,
    recovery_strategy="lab-reset-required",
)

SCENARIO_LIFECYCLE_BY_ID: dict[str, ScenarioLifecycle] = {
    "static-gates": _STATIC_LIFECYCLE,
    "lab-readiness": _STATIC_LIFECYCLE,
    "baseline-check": _STATIC_LIFECYCLE,
    "preflight": _STATIC_LIFECYCLE,
    "python-passive-switchover": _SWITCHOVER_LIFECYCLE,
    "ansible-passive-switchover": _SWITCHOVER_LIFECYCLE,
    "python-restore-only": _RESTORE_ONLY_LIFECYCLE,
    "ansible-restore-only": _RESTORE_ONLY_LIFECYCLE,
    "argocd-managed-switchover": _SWITCHOVER_LIFECYCLE,
    "runtime-parity": _STATIC_LIFECYCLE,
    "final-baseline-check": _STATIC_LIFECYCLE,
    "bash-discovery": _STATIC_LIFECYCLE,
    "bash-postflight": _STATIC_LIFECYCLE,
    "full-restore": _SWITCHOVER_LIFECYCLE,
    "checkpoint-resume": _SWITCHOVER_LIFECYCLE,
    "decommission": _SECONDARY_FINAL_LIFECYCLE,
    "rbac-bootstrap": _STATIC_LIFECYCLE,
    "rbac-bootstrap-live": _STATIC_LIFECYCLE,
    "failure-injection": _SWITCHOVER_LIFECYCLE,
    "soak": _SWITCHOVER_LIFECYCLE,
}

_ANSIBLE_NOT_IMPLEMENTED = {
    "ansible": "ansible stream does not implement this scenario in Phase 1",
}

SCENARIO_SUPPORT_BY_ID: dict[str, ScenarioSupport] = {
    "static-gates": ScenarioSupport(),
    "lab-readiness": ScenarioSupport(),
    "baseline-check": ScenarioSupport(),
    "preflight": ScenarioSupport(),
    "python-passive-switchover": ScenarioSupport(),
    "ansible-passive-switchover": ScenarioSupport(),
    "python-restore-only": ScenarioSupport(),
    "ansible-restore-only": ScenarioSupport(),
    "argocd-managed-switchover": ScenarioSupport(),
    "runtime-parity": ScenarioSupport(),
    "final-baseline-check": ScenarioSupport(),
    "bash-discovery": ScenarioSupport(),
    "bash-postflight": ScenarioSupport(),
    "full-restore": ScenarioSupport(unsupported_stream_reasons=_ANSIBLE_NOT_IMPLEMENTED),
    "checkpoint-resume": ScenarioSupport(unsupported_stream_reasons=_ANSIBLE_NOT_IMPLEMENTED),
    "decommission": ScenarioSupport(),
    "rbac-bootstrap": ScenarioSupport(),
    "rbac-bootstrap-live": ScenarioSupport(),
    "failure-injection": ScenarioSupport(
        certification_supported=False,
        unsupported_reason=(
            "failure-injection is not certification-supported in Phase 1 because adapters do not inject failures"
        ),
        unsupported_stream_reasons=_ANSIBLE_NOT_IMPLEMENTED,
    ),
    "soak": ScenarioSupport(
        certification_supported=False,
        unsupported_reason=(
            "soak is not certification-supported in Phase 1 because adapters do not enforce soak cycles"
        ),
        unsupported_stream_reasons=_ANSIBLE_NOT_IMPLEMENTED,
    ),
}


def _scenario(
    scenario_id: str,
    required: bool,
    streams: tuple[str, ...],
    runtime_parity_required: bool,
) -> ScenarioDefinition:
    lifecycle = SCENARIO_LIFECYCLE_BY_ID[scenario_id]
    return ScenarioDefinition(
        scenario_id,
        required,
        streams,
        lifecycle.mutates_lab,
        runtime_parity_required,
        lifecycle,
        SCENARIO_SUPPORT_BY_ID[scenario_id],
    )


V1_SCENARIOS: tuple[ScenarioDefinition, ...] = (
    _scenario("static-gates", True, ("local",), False),
    _scenario("lab-readiness", True, ("local",), False),
    _scenario("baseline-check", True, ("local",), False),
    _scenario("preflight", True, ("bash", "python", "ansible"), True),
    _scenario("python-passive-switchover", True, ("python",), True),
    _scenario("ansible-passive-switchover", True, ("ansible",), True),
    _scenario("python-restore-only", True, ("python",), True),
    _scenario("ansible-restore-only", True, ("ansible",), True),
    _scenario("argocd-managed-switchover", True, ("python", "ansible"), True),
    _scenario("runtime-parity", True, ("local",), True),
    _scenario("final-baseline-check", True, ("local",), False),
    _scenario("bash-discovery", False, ("bash",), False),
    _scenario("bash-postflight", False, ("bash",), False),
    _scenario("full-restore", False, ("python", "ansible"), True),
    _scenario("checkpoint-resume", False, ("python", "ansible"), True),
    _scenario("decommission", False, ("python", "ansible"), True),
    _scenario("rbac-bootstrap", False, ("ansible",), True),
    _scenario("rbac-bootstrap-live", False, ("local",), False),
    _scenario("failure-injection", False, ("python", "ansible"), False),
    _scenario("soak", False, ("python", "ansible"), True),
)
SCENARIOS_BY_ID = {item.id: item for item in V1_SCENARIOS}
PREREQUISITES = ("static-gates", "lab-readiness", "baseline-check")
POST_MUTATION = ("runtime-parity", "final-baseline-check")


class ScenarioSelection(Protocol):
    id: str
    required: bool | None
    streams: tuple[str, ...]


def _lifecycle_payload(lifecycle: ScenarioLifecycle) -> dict[str, Any]:
    return {
        "initial_primary": lifecycle.initial_primary,
        "final_primary": lifecycle.final_primary,
        "mutates_lab": lifecycle.mutates_lab,
        "reset_required": lifecycle.reset_required,
        "allowed_followup_scenarios": list(lifecycle.allowed_followup_scenarios),
        "recovery_strategy": lifecycle.recovery_strategy,
    }


def _support_payload(support: ScenarioSupport) -> dict[str, Any]:
    return {
        "certification_supported": support.certification_supported,
        "unsupported_reason": support.unsupported_reason,
        "unsupported_stream_reasons": dict(sorted(support.unsupported_stream_reasons.items())),
    }


def _hash_matrix(scenarios: tuple[ScenarioDefinition, ...], selected_streams: tuple[str, ...]) -> str:
    payload = json.dumps(
        {
            "scenarios": [
                {
                    "id": scenario.id,
                    "required": scenario.required,
                    "streams": scenario.streams,
                    "lifecycle": _lifecycle_payload(scenario.lifecycle),
                    "support": _support_payload(scenario.support),
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
    requested_scenarios = set(scenario_filters)
    scenarios = []
    for scenario_id in scenario_ids:
        definition = SCENARIOS_BY_ID[scenario_id]
        profile_item = profile_by_id.get(scenario_id)
        streams = definition.streams
        required = definition.required
        if profile_item is not None:
            streams = profile_item.streams or streams
            required = definition.required if profile_item.required is None else bool(profile_item.required)
        if scenario_id in requested_scenarios:
            required = True
        if streams != ("local",):
            streams = tuple(stream for stream in streams if stream in selected_streams)
        scenarios.append(
            ScenarioDefinition(
                id=definition.id,
                required=required,
                streams=streams,
                mutates_lab=definition.mutates_lab,
                runtime_parity_required=definition.runtime_parity_required,
                lifecycle=definition.lifecycle,
                support=definition.support,
            )
        )
    return SelectedReleaseMatrix(
        scenarios=tuple(scenarios),
        selected_streams=selected_streams,
        matrix_hash=_hash_matrix(tuple(scenarios), selected_streams),
    )


@dataclass(frozen=True)
class MatrixValidationIssue:
    scenario_id: str
    stream: str | None
    status: str
    required: bool
    reason: str
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "stream": self.stream,
            "status": self.status,
            "required": self.required,
            "reason": self.reason,
            "code": self.code,
        }


@dataclass(frozen=True)
class MatrixValidationResult:
    status: str
    blocked: bool
    issues: tuple[MatrixValidationIssue, ...]

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(issue.reason for issue in self.issues if issue.status == "failed")

    @property
    def not_applicable_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (issue.scenario_id, issue.stream)
            for issue in self.issues
            if issue.stream is not None and issue.status == "not_applicable"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "blocked": self.blocked,
            "reasons": list(self.reasons),
            "issues": [issue.to_dict() for issue in self.issues],
            "not_applicable_pairs": [
                {"scenario_id": scenario_id, "stream": stream} for scenario_id, stream in self.not_applicable_pairs
            ],
        }


def _issue_status(required: bool) -> str:
    return "failed" if required else "not_applicable"


def _support_reason(
    scenario: ScenarioDefinition,
    stream: str,
    supported_scenario_ids: frozenset[str],
) -> str | None:
    if scenario.id not in supported_scenario_ids:
        return f"{stream} stream does not implement scenario {scenario.id}"
    if stream in scenario.support.unsupported_stream_reasons:
        return scenario.support.unsupported_stream_reasons[stream]
    if not scenario.support.certification_supported and scenario.support.unsupported_reason:
        return scenario.support.unsupported_reason
    return None


def _focused_single_mutating_rerun(
    *,
    release_mode: str,
    scenario_filters: tuple[str, ...],
) -> bool:
    if release_mode != "focused-rerun" or not scenario_filters:
        return False
    requested = tuple(dict.fromkeys(scenario_filters))
    requested_mutating = [SCENARIOS_BY_ID[item] for item in requested if SCENARIOS_BY_ID[item].mutates_lab]
    return len(requested_mutating) == 1


def _has_executable_stream(
    scenario: ScenarioDefinition,
    blocked_pairs: set[tuple[str, str]],
) -> bool:
    for stream in scenario.streams:
        if stream == "local":
            return True
        if (scenario.id, stream) not in blocked_pairs:
            return True
    return False


def validate_release_matrix(
    *,
    matrix: SelectedReleaseMatrix,
    release_mode: str,
    scenario_filters: tuple[str, ...],
    adapter_supported_scenarios: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]],
) -> MatrixValidationResult:
    issues: list[MatrixValidationIssue] = []
    supported_by_stream = {
        stream: frozenset(scenario_ids) for stream, scenario_ids in adapter_supported_scenarios.items()
    }

    for scenario in matrix.scenarios:
        if not scenario.streams and scenario.required:
            issues.append(
                MatrixValidationIssue(
                    scenario_id=scenario.id,
                    stream=None,
                    status="failed",
                    required=scenario.required,
                    reason=f"required scenario {scenario.id} has no selected executable streams",
                    code="no-selected-stream",
                )
            )
            continue
        for stream in scenario.streams:
            if stream == "local":
                continue
            reason = _support_reason(
                scenario,
                stream,
                supported_by_stream.get(stream, frozenset()),
            )
            if reason is None:
                continue
            issues.append(
                MatrixValidationIssue(
                    scenario_id=scenario.id,
                    stream=stream,
                    status=_issue_status(scenario.required),
                    required=scenario.required,
                    reason=reason,
                    code="matrix-support",
                )
            )

    blocked_pairs = {
        (issue.scenario_id, issue.stream)
        for issue in issues
        if issue.stream is not None and issue.status in {"failed", "not_applicable"}
    }
    mutating_scenarios = [
        scenario
        for scenario in matrix.scenarios
        if scenario.lifecycle.mutates_lab
        and scenario.lifecycle.reset_required
        and _has_executable_stream(scenario, blocked_pairs)
    ]
    if len(mutating_scenarios) > 1 and not _focused_single_mutating_rerun(
        release_mode=release_mode,
        scenario_filters=scenario_filters,
    ):
        for previous, current in zip(mutating_scenarios, mutating_scenarios[1:]):
            if current.id in previous.lifecycle.allowed_followup_scenarios:
                continue
            issues.append(
                MatrixValidationIssue(
                    scenario_id=current.id,
                    stream=None,
                    status="failed",
                    required=current.required,
                    reason=(
                        "mutating scenario sequence requires reset/recovery between scenarios: "
                        f"{previous.id} -> {current.id}"
                    ),
                    code="matrix-lifecycle",
                )
            )

    status = "failed" if any(issue.status == "failed" for issue in issues) else "passed"
    return MatrixValidationResult(
        status=status,
        blocked=status == "failed",
        issues=tuple(issues),
    )


def matrix_validation_results(validation: MatrixValidationResult) -> list[dict[str, Any]]:
    results = []
    for issue in validation.issues:
        results.append(
            {
                "stream": issue.stream or "local",
                "scenario_id": issue.scenario_id,
                "status": issue.status,
                "required": issue.required,
                "assertions": [
                    {
                        "capability": issue.scenario_id,
                        "name": issue.code,
                        "status": issue.status,
                        "expected": "supported",
                        "actual": "unsupported",
                        "message": issue.reason,
                    }
                ],
            }
        )
    return results
