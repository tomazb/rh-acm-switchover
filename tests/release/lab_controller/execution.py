from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from tests.release.scenarios.catalog import SCENARIOS_BY_ID

from .artifacts import sanitize_artifact_payload, sanitize_artifact_text, validate_artifact_payload_redacted
from .decisions import scenario_segment_blocking_reason
from .models import DesiredRoleState, GeneratedProfile, LabObservation, SegmentPlan

RELEASE_CERTIFICATION_PYTEST_TARGET = "tests/release/test_release_certification.py"
DEFAULT_RELEASE_MODE = "focused-rerun"


class ExecutionBackendKind(str, Enum):
    FAKE = "fake"
    RELEASE_FRAMEWORK = "release_framework"


class ExecutionMode(str, Enum):
    FAKE = "fake"
    RELEASE_FRAMEWORK_DRY_RUN = "release_framework_dry_run"
    RELEASE_FRAMEWORK_LOCAL = "release_framework_local"
    RELEASE_FRAMEWORK_LIVE = "release_framework_live"


@dataclass(frozen=True)
class ExecutionRequest:
    backend_kind: ExecutionBackendKind
    execution_mode: ExecutionMode
    segment_id: str
    scenario_id: str
    selected_streams: tuple[str, ...]
    generated_profile_hash: str
    generated_profile_metadata: Mapping[str, Any]
    release_profile_summary: Mapping[str, Any]
    intended_pytest_target: str
    intended_release_mode: str
    intended_artifact_dir: str
    expected_initial_role_state: DesiredRoleState
    expected_final_role_state: DesiredRoleState
    mutates_lab: bool
    dry_run: bool
    redaction_status: str
    real_execution_evidence: bool = False
    live_certification_evidence: bool = False
    execution_summary: Mapping[str, Any] = field(default_factory=dict)
    materialized_invocation_summary: Mapping[str, Any] = field(default_factory=dict)
    materialized_argv_summary: Mapping[str, Any] = field(default_factory=dict)
    environment_plan_summary: Mapping[str, Any] = field(default_factory=dict)
    profile_compatibility_summary: Mapping[str, Any] = field(default_factory=dict)
    artifact_directory_summary: Mapping[str, Any] = field(default_factory=dict)
    future_execution_eligibility: Mapping[str, Any] = field(default_factory=dict)
    materialization_status: str = "not_materialized"
    request_hash: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    backend_kind: ExecutionBackendKind
    execution_mode: ExecutionMode
    scenario_id: str
    status: str
    mutation_attempted: bool
    mutation_completed: bool
    failure_reason: str | None = None
    retryable_infra_failure: bool = False
    stdout_summary: str = ""
    stderr_summary: str = ""
    post_segment_observation: LabObservation | None = None
    request: ExecutionRequest | None = None
    dry_run: bool = False
    real_execution_evidence: bool = False
    live_certification_evidence: bool = False

    @property
    def request_summary(self) -> dict[str, Any]:
        if self.request is None:
            return {
                "execution_backend": self.backend_kind.value,
                "execution_mode": self.execution_mode.value,
                "dry_run": self.dry_run,
                "intended_pytest_target": None,
                "intended_release_mode": None,
                "intended_scenario": self.scenario_id,
                "intended_stream": [],
                "generated_profile_hash": None,
                "execution_request_redaction_status": "not_applicable",
                "execution_summary": {
                    "scenario_id": self.scenario_id,
                    "status": self.status,
                    "failure_reason": sanitize_artifact_text(self.failure_reason),
                },
                "real_execution_evidence": self.real_execution_evidence,
                "live_certification_evidence": self.live_certification_evidence,
                "evidence_status": "fake_only",
            }
        return summarize_execution_request(self.request)


class ExecutionBackend(ABC):
    kind: ExecutionBackendKind
    mode: ExecutionMode

    @abstractmethod
    def execute(
        self,
        plan: SegmentPlan,
        generated_profile: GeneratedProfile,
        *,
        generated_profile_metadata: Mapping[str, Any] | None = None,
        artifact_root: str | None = None,
    ) -> ExecutionResult:
        """Build or execute one deterministic segment backend request."""


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _role_state_payload(state: DesiredRoleState) -> dict[str, str]:
    return {
        "primary_physical_hub": state.primary_physical_hub.value,
        "secondary_physical_hub": state.secondary_physical_hub.value,
    }


def _require_redacted_generated_profile_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(dict(metadata), sort_keys=True))
    hubs = payload.get("hubs")
    if payload.get("redaction_status") != "redacted" or not isinstance(hubs, dict):
        raise ValueError("generated profile metadata must be redacted before execution request construction")
    for hub in hubs.values():
        if not isinstance(hub, dict) or hub.get("kubeconfig_reference") != "[REDACTED]":
            raise ValueError("generated profile metadata must be redacted before execution request construction")
    validate_artifact_payload_redacted({"generated_profile": payload})
    return payload


def _profile_streams(generated_profile: GeneratedProfile) -> tuple[str, ...]:
    streams = generated_profile.profile_data.get("streams", ())
    if not isinstance(streams, list):
        return ()
    selected: list[str] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        if stream.get("enabled", True) is False:
            continue
        stream_id = stream.get("id")
        if isinstance(stream_id, str):
            selected.append(stream_id)
    return tuple(selected)


def _selected_streams_for_request(
    *,
    generated_profile: GeneratedProfile,
    scenario_id: str,
    stream_id: str | None,
) -> tuple[str, ...]:
    scenario = SCENARIOS_BY_ID[scenario_id]
    available: tuple[str, ...]
    if scenario.streams == ("local",):
        available = ("local",)
    else:
        enabled_streams = set(_profile_streams(generated_profile))
        available = tuple(stream for stream in scenario.streams if stream in enabled_streams)
    if stream_id is not None:
        if stream_id not in available:
            raise ValueError(f"stream {stream_id} is not selected for release scenario {scenario_id}")
        return (stream_id,)
    if not available:
        raise ValueError(f"release scenario {scenario_id} has no selected stream")
    return available


def _release_profile_summary(
    *,
    generated_profile: GeneratedProfile,
    generated_profile_metadata: Mapping[str, Any],
    artifact_root: str,
) -> dict[str, Any]:
    scenarios = generated_profile.profile_data.get("scenarios", ())
    scenario_ids = [str(item.get("id")) for item in scenarios if isinstance(item, dict) and item.get("id")]
    return {
        "profile_name": sanitize_artifact_text(str(generated_profile.profile_data.get("name", ""))),
        "profile_sha256": generated_profile.sha256,
        "scenario_ids": scenario_ids,
        "enabled_streams": list(_profile_streams(generated_profile)),
        "artifact_root": sanitize_artifact_text(artifact_root),
        "artifact_profile_path": sanitize_artifact_text(
            str(generated_profile_metadata.get("artifact_profile_path", ""))
        ),
        "logical_to_physical": dict(generated_profile.logical_to_physical),
    }


def _artifact_root(generated_profile: GeneratedProfile, artifact_root: str | None) -> str:
    if artifact_root is not None:
        return artifact_root
    artifacts = generated_profile.profile_data.get("artifacts", {})
    if isinstance(artifacts, dict):
        root = artifacts.get("root")
        if isinstance(root, str) and root:
            return root
    return "artifacts/release"


def _request_summary_payload(request: ExecutionRequest, *, include_hash: bool) -> dict[str, Any]:
    payload = {
        "execution_backend": request.backend_kind.value,
        "execution_mode": request.execution_mode.value,
        "backend_kind": request.backend_kind.value,
        "mode": request.execution_mode.value,
        "segment_id": request.segment_id,
        "dry_run": request.dry_run,
        "intended_pytest_target": sanitize_artifact_text(request.intended_pytest_target),
        "intended_release_mode": sanitize_artifact_text(request.intended_release_mode),
        "intended_scenario": request.scenario_id,
        "intended_stream": list(request.selected_streams),
        "selected_streams": list(request.selected_streams),
        "generated_profile_hash": request.generated_profile_hash,
        "generated_profile_metadata": sanitize_artifact_payload(dict(request.generated_profile_metadata)),
        "release_profile_summary": sanitize_artifact_payload(dict(request.release_profile_summary)),
        "intended_artifact_dir": sanitize_artifact_text(request.intended_artifact_dir),
        "expected_initial_role_state": _role_state_payload(request.expected_initial_role_state),
        "expected_final_role_state": _role_state_payload(request.expected_final_role_state),
        "mutates_lab": request.mutates_lab,
        "execution_request_redaction_status": request.redaction_status,
        "execution_summary": sanitize_artifact_payload(dict(request.execution_summary)),
        "materialization_status": request.materialization_status,
        "materialized_invocation_summary": sanitize_artifact_payload(dict(request.materialized_invocation_summary)),
        "materialized_argv_summary": sanitize_artifact_payload(dict(request.materialized_argv_summary)),
        "environment_plan_summary": sanitize_artifact_payload(dict(request.environment_plan_summary)),
        "profile_compatibility_summary": sanitize_artifact_payload(dict(request.profile_compatibility_summary)),
        "artifact_directory_summary": sanitize_artifact_payload(dict(request.artifact_directory_summary)),
        "future_execution_eligibility": sanitize_artifact_payload(dict(request.future_execution_eligibility)),
        "real_execution_evidence": request.real_execution_evidence,
        "live_certification_evidence": request.live_certification_evidence,
        "evidence_status": "dry_run_only" if request.dry_run else "not_live",
    }
    if include_hash:
        payload["request_hash"] = request.request_hash
    return payload


def summarize_execution_request(request: ExecutionRequest) -> dict[str, Any]:
    """Return the publishable, sanitized request summary for artifacts."""
    return _request_summary_payload(request, include_hash=True)


def validate_execution_request(request: ExecutionRequest) -> None:
    """Fail closed if a publishable execution request is incomplete or unsafe."""
    if request.backend_kind is not ExecutionBackendKind.RELEASE_FRAMEWORK:
        if request.execution_mode is not ExecutionMode.FAKE:
            raise ValueError("fake execution requests must use fake execution mode")
        return
    if request.execution_mode is ExecutionMode.RELEASE_FRAMEWORK_LIVE:
        raise ValueError("live release-framework execution is not supported in Phase 6A")
    if request.execution_mode is not ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN:
        raise ValueError(f"unsupported release-framework execution mode: {request.execution_mode.value}")
    if not request.dry_run:
        raise ValueError("release-framework dry-run requests must be marked dry_run=true")
    if request.real_execution_evidence or request.live_certification_evidence:
        raise ValueError("dry-run requests cannot claim real execution or live certification evidence")
    if not request.segment_id or not request.scenario_id or not request.generated_profile_hash:
        raise ValueError("execution request is missing required identity fields")
    if request.scenario_id not in SCENARIOS_BY_ID:
        raise ValueError(f"unknown release scenario: {request.scenario_id}")
    blocking_reason = scenario_segment_blocking_reason(request.scenario_id, mutates_lab=request.mutates_lab)
    if blocking_reason is not None:
        raise ValueError(blocking_reason)
    if not request.selected_streams:
        raise ValueError("execution request must select at least one stream")
    if request.intended_pytest_target != RELEASE_CERTIFICATION_PYTEST_TARGET:
        raise ValueError("execution request must target the release certification pytest entrypoint")
    if not request.intended_release_mode:
        raise ValueError("execution request is missing release mode")
    if not request.intended_artifact_dir:
        raise ValueError("execution request is missing artifact directory")
    if request.redaction_status != "redacted":
        raise ValueError("execution request redaction status must be redacted")
    _require_redacted_generated_profile_metadata(request.generated_profile_metadata)
    summary = summarize_execution_request(request)
    validate_artifact_payload_redacted(summary)


def build_release_framework_request(
    *,
    plan: SegmentPlan,
    generated_profile: GeneratedProfile,
    generated_profile_metadata: Mapping[str, Any],
    artifact_root: str | None = None,
    release_mode: str = DEFAULT_RELEASE_MODE,
    stream_id: str | None = None,
    execution_mode: ExecutionMode = ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
    plan_id: str = "standalone",
    explicit_env: Mapping[str, str] | None = None,
) -> ExecutionRequest:
    """Build the deterministic future pytest release-framework request without executing it."""
    if execution_mode is ExecutionMode.RELEASE_FRAMEWORK_LIVE:
        raise ValueError("live release-framework execution is not supported in Phase 6A")
    if execution_mode is not ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN:
        raise ValueError(f"unsupported release-framework execution mode: {execution_mode.value}")
    if plan.scenario_id not in SCENARIOS_BY_ID:
        raise ValueError(f"unknown release scenario: {plan.scenario_id}")
    blocking_reason = scenario_segment_blocking_reason(plan.scenario_id, mutates_lab=plan.mutates_lab)
    if blocking_reason is not None:
        raise ValueError(blocking_reason)

    redacted_metadata = _require_redacted_generated_profile_metadata(generated_profile_metadata)
    root = _artifact_root(generated_profile, artifact_root)
    selected_streams = _selected_streams_for_request(
        generated_profile=generated_profile,
        scenario_id=plan.scenario_id,
        stream_id=stream_id,
    )
    request = ExecutionRequest(
        backend_kind=ExecutionBackendKind.RELEASE_FRAMEWORK,
        execution_mode=execution_mode,
        segment_id=plan.segment_id,
        scenario_id=plan.scenario_id,
        selected_streams=selected_streams,
        generated_profile_hash=generated_profile.sha256,
        generated_profile_metadata=redacted_metadata,
        release_profile_summary=_release_profile_summary(
            generated_profile=generated_profile,
            generated_profile_metadata=redacted_metadata,
            artifact_root=root,
        ),
        intended_pytest_target=RELEASE_CERTIFICATION_PYTEST_TARGET,
        intended_release_mode=release_mode,
        intended_artifact_dir=sanitize_artifact_text(root) or "[REDACTED]",
        expected_initial_role_state=plan.expected_initial_role_state,
        expected_final_role_state=plan.expected_final_role_state,
        mutates_lab=plan.mutates_lab,
        dry_run=True,
        redaction_status="redacted",
        real_execution_evidence=False,
        live_certification_evidence=False,
        execution_summary={
            "action": "build_release_framework_request",
            "executed": False,
            "pytest_invoked": False,
            "adapters_invoked": False,
        },
    )
    from .invocation import materialize_release_framework_request, summarize_materialized_invocation

    materialized = materialize_release_framework_request(
        request=request,
        generated_profile=generated_profile,
        plan_id=plan_id,
        artifact_root=root,
        explicit_env=explicit_env,
    )
    materialized_summary = summarize_materialized_invocation(materialized)
    request = replace(
        request,
        materialized_invocation_summary=materialized_summary,
        materialized_argv_summary=materialized_summary["materialized_argv_summary"],
        environment_plan_summary=materialized_summary["environment_plan_summary"],
        profile_compatibility_summary=materialized_summary["profile_compatibility_summary"],
        artifact_directory_summary=materialized_summary["artifact_directory_summary"],
        future_execution_eligibility=materialized_summary["future_execution_eligibility"],
        materialization_status="materialized",
    )
    request_hash = _stable_hash(_request_summary_payload(request, include_hash=False))
    request = replace(request, request_hash=request_hash)
    validate_execution_request(request)
    return request


class FakeExecutionBackend(ExecutionBackend):
    kind = ExecutionBackendKind.FAKE
    mode = ExecutionMode.FAKE

    def __init__(
        self,
        *,
        scenario_id: str | None = None,
        status: str = "succeeded",
        mutation_attempted: bool = False,
        mutation_completed: bool = False,
        failure_reason: str | None = None,
        retryable_infra_failure: bool = False,
        stdout_summary: str = "fake scenario completed",
        stderr_summary: str = "",
        post_segment_observation: LabObservation | None = None,
    ) -> None:
        self._scenario_id = scenario_id
        self._status = status
        self._mutation_attempted = mutation_attempted
        self._mutation_completed = mutation_completed
        self._failure_reason = failure_reason
        self._retryable_infra_failure = retryable_infra_failure
        self._stdout_summary = stdout_summary
        self._stderr_summary = stderr_summary
        self._post_segment_observation = post_segment_observation

    def execute(
        self,
        plan: SegmentPlan,
        generated_profile: GeneratedProfile,
        *,
        generated_profile_metadata: Mapping[str, Any] | None = None,
        artifact_root: str | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            backend_kind=self.kind,
            execution_mode=self.mode,
            scenario_id=self._scenario_id or plan.scenario_id,
            status=self._status,
            mutation_attempted=self._mutation_attempted,
            mutation_completed=self._mutation_completed,
            failure_reason=self._failure_reason,
            retryable_infra_failure=self._retryable_infra_failure,
            stdout_summary=self._stdout_summary,
            stderr_summary=self._stderr_summary,
            post_segment_observation=self._post_segment_observation,
            dry_run=False,
            real_execution_evidence=False,
            live_certification_evidence=False,
        )


class ReleaseFrameworkDryRunBackend(ExecutionBackend):
    kind = ExecutionBackendKind.RELEASE_FRAMEWORK
    mode = ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN

    def __init__(
        self,
        *,
        release_mode: str = DEFAULT_RELEASE_MODE,
        stream_id: str | None = None,
        simulated_status: str = "succeeded",
        simulated_mutation_attempted: bool = False,
        simulated_mutation_completed: bool = False,
        simulated_failure_reason: str | None = None,
        simulated_retryable_infra_failure: bool = False,
        simulated_stdout_summary: str = "release-framework dry-run request built",
        simulated_stderr_summary: str = "",
        simulated_post_segment_observation: LabObservation | None = None,
        plan_id: str = "standalone",
        explicit_env: Mapping[str, str] | None = None,
    ) -> None:
        self.release_mode = release_mode
        self.stream_id = stream_id
        self.simulated_status = simulated_status
        self.simulated_mutation_attempted = simulated_mutation_attempted
        self.simulated_mutation_completed = simulated_mutation_completed
        self.simulated_failure_reason = simulated_failure_reason
        self.simulated_retryable_infra_failure = simulated_retryable_infra_failure
        self.simulated_stdout_summary = simulated_stdout_summary
        self.simulated_stderr_summary = simulated_stderr_summary
        self.simulated_post_segment_observation = simulated_post_segment_observation
        self.plan_id = plan_id
        self.explicit_env = explicit_env

    def execute(
        self,
        plan: SegmentPlan,
        generated_profile: GeneratedProfile,
        *,
        generated_profile_metadata: Mapping[str, Any] | None = None,
        artifact_root: str | None = None,
    ) -> ExecutionResult:
        if generated_profile_metadata is None:
            raise ValueError("generated profile metadata must be supplied for release-framework dry-run requests")
        request = build_release_framework_request(
            plan=plan,
            generated_profile=generated_profile,
            generated_profile_metadata=generated_profile_metadata,
            artifact_root=artifact_root,
            release_mode=self.release_mode,
            stream_id=self.stream_id,
            execution_mode=self.mode,
            plan_id=self.plan_id,
            explicit_env=self.explicit_env,
        )
        future_eligibility = request.future_execution_eligibility
        if isinstance(future_eligibility, Mapping) and not bool(future_eligibility.get("eligible", False)):
            reason = sanitize_artifact_text(
                str(future_eligibility.get("reason") or "future execution eligibility blocked")
            )
            return ExecutionResult(
                backend_kind=self.kind,
                execution_mode=self.mode,
                scenario_id=plan.scenario_id,
                status="failed",
                mutation_attempted=False,
                mutation_completed=False,
                failure_reason=f"materialization blocked: {reason or '[REDACTED]'}",
                retryable_infra_failure=False,
                stdout_summary=self.simulated_stdout_summary,
                stderr_summary=self.simulated_stderr_summary,
                post_segment_observation=None,
                request=request,
                dry_run=True,
                real_execution_evidence=False,
                live_certification_evidence=False,
            )
        return ExecutionResult(
            backend_kind=self.kind,
            execution_mode=self.mode,
            scenario_id=plan.scenario_id,
            status=self.simulated_status,
            mutation_attempted=self.simulated_mutation_attempted,
            mutation_completed=self.simulated_mutation_completed,
            failure_reason=self.simulated_failure_reason,
            retryable_infra_failure=self.simulated_retryable_infra_failure,
            stdout_summary=self.simulated_stdout_summary,
            stderr_summary=self.simulated_stderr_summary,
            post_segment_observation=self.simulated_post_segment_observation,
            request=request,
            dry_run=True,
            real_execution_evidence=False,
            live_certification_evidence=False,
        )


class ReleaseFrameworkLocalBackend(ExecutionBackend):
    kind = ExecutionBackendKind.RELEASE_FRAMEWORK
    mode = ExecutionMode.RELEASE_FRAMEWORK_LOCAL

    def __init__(
        self,
        *,
        command_runner: Any,
        allow_local_execution: bool,
        requested_execution_mode: ExecutionMode = ExecutionMode.RELEASE_FRAMEWORK_LOCAL,
        release_mode: str = DEFAULT_RELEASE_MODE,
        stream_id: str | None = None,
        plan_id: str = "standalone",
        explicit_env: Mapping[str, str] | None = None,
        timeout_seconds: int = 3600,
    ) -> None:
        if requested_execution_mode is ExecutionMode.RELEASE_FRAMEWORK_LIVE:
            raise ValueError("live release-framework execution is not supported in Phase 6C")
        self.command_runner = command_runner
        self.allow_local_execution = allow_local_execution
        self.requested_execution_mode = requested_execution_mode
        self.release_mode = release_mode
        self.stream_id = stream_id
        self.plan_id = plan_id
        self.explicit_env = explicit_env
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        plan: SegmentPlan,
        generated_profile: GeneratedProfile,
        *,
        generated_profile_metadata: Mapping[str, Any] | None = None,
        artifact_root: str | None = None,
    ) -> ExecutionResult:
        if generated_profile_metadata is None:
            raise ValueError("generated profile metadata must be supplied for release-framework local execution")
        request = build_release_framework_request(
            plan=plan,
            generated_profile=generated_profile,
            generated_profile_metadata=generated_profile_metadata,
            artifact_root=artifact_root,
            release_mode=self.release_mode,
            stream_id=self.stream_id,
            execution_mode=ExecutionMode.RELEASE_FRAMEWORK_DRY_RUN,
            plan_id=self.plan_id,
            explicit_env=self.explicit_env,
        )
        from .harness import execute_materialized_invocation, summarize_execution_evidence
        from .invocation import materialize_release_framework_request

        root = _artifact_root(generated_profile, artifact_root)
        materialized = materialize_release_framework_request(
            request=request,
            generated_profile=generated_profile,
            plan_id=self.plan_id,
            artifact_root=root,
            explicit_env=self.explicit_env,
        )

        evidence = execute_materialized_invocation(
            materialized,
            command_runner=self.command_runner,
            requested_execution_mode=self.requested_execution_mode,
            allow_local_execution=self.allow_local_execution,
            timeout_seconds=self.timeout_seconds,
        )
        evidence_summary = summarize_execution_evidence(evidence)
        result_request = replace(
            request,
            execution_mode=self.mode,
            dry_run=False,
            real_execution_evidence=evidence.real_execution_evidence,
            live_certification_evidence=False,
            execution_summary={
                "action": "release_framework_local_harness",
                "executed": evidence.executed,
                "pytest_invoked": evidence.executed,
                "adapters_invoked": False,
                "execution_gate": evidence_summary["execution_gate"],
                "execution_evidence": evidence_summary,
            },
            materialized_invocation_summary={
                **dict(request.materialized_invocation_summary),
                "executed": evidence.executed,
                "real_execution_evidence": evidence.real_execution_evidence,
                "live_certification_evidence": False,
            },
        )
        result_request = replace(
            result_request,
            request_hash=_stable_hash(_request_summary_payload(result_request, include_hash=False)),
        )
        status = "succeeded" if evidence.status == "succeeded" else "failed"
        mutation_attempted = bool(plan.mutates_lab and status == "succeeded")
        mutation_completed = bool(plan.mutates_lab and status == "succeeded")
        return ExecutionResult(
            backend_kind=self.kind,
            execution_mode=self.mode,
            scenario_id=plan.scenario_id,
            status=status,
            mutation_attempted=mutation_attempted,
            mutation_completed=mutation_completed,
            failure_reason=None if status == "succeeded" else evidence.stderr_summary or evidence.gate.reason,
            retryable_infra_failure=evidence.retryable_infra_failure,
            stdout_summary=evidence.stdout_summary,
            stderr_summary=evidence.stderr_summary,
            post_segment_observation=None,
            request=result_request,
            dry_run=False,
            real_execution_evidence=evidence.real_execution_evidence,
            live_certification_evidence=False,
        )
