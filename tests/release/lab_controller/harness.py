from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from tests.release.scenarios.catalog import SCENARIOS_BY_ID

from .artifacts import sanitize_artifact_text, validate_artifact_payload_redacted
from .decisions import classify_scenario
from .execution import ExecutionMode
from .invocation import (
    CLI_RELEASE_STREAMS,
    PYTEST_RUNNER,
    RELEASE_CERTIFICATION_PYTEST_TARGET,
    SUPPORTED_RELEASE_MODES,
    MaterializedExecutionRequest,
)
from .models import ScenarioClassification

_SUPPORTED_RELEASE_FLAGS = {
    "--release-profile",
    "--release-mode",
    "--release-scenario",
    "--release-stream",
    "--release-artifact-dir",
}
_SHELL_METACHARS = (";", "|", "&", "`", "$", "\n", "\r")
_LOCAL_EXECUTION_MODE = "release_framework_local"
_DRY_RUN_EXECUTION_MODE = "release_framework_dry_run"
_LIVE_EXECUTION_MODE = "release_framework_live"


@dataclass(frozen=True)
class CommandRunRequest:
    argv: tuple[str, ...]
    env: Mapping[str, str]
    timeout_seconds: int

    def to_summary(self) -> dict[str, Any]:
        return {
            "argv": [sanitize_artifact_text(item) or "[REDACTED]" for item in self.argv],
            "env": {
                str(key): sanitize_artifact_text(value) or "[REDACTED]"
                for key, value in sorted((str(key), str(value)) for key, value in self.env.items())
            },
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class CommandRunResult:
    return_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CommandRunner(Protocol):
    kind: str

    def run(self, request: CommandRunRequest) -> CommandRunResult:
        """Run a structured command request without shell expansion."""


class FakeCommandRunner:
    kind = "fake"

    def __init__(self, *, results: Sequence[CommandRunResult] = ()) -> None:
        self._results = list(results)
        self._requests: list[CommandRunRequest] = []

    @property
    def requests(self) -> tuple[CommandRunRequest, ...]:
        return tuple(self._requests)

    def run(self, request: CommandRunRequest) -> CommandRunResult:
        self._requests.append(request)
        if self._results:
            return self._results.pop(0)
        return CommandRunResult(return_code=0, stdout="", stderr="", timed_out=False)


@dataclass(frozen=True)
class ExecutionGateDecision:
    allowed: bool
    reason: str
    blocking_fields: tuple[str, ...]
    execution_mode: str
    local_execution_allowed: bool
    live_execution_allowed: bool = False
    real_execution_evidence_possible: bool = False
    live_certification_evidence: bool = False

    def to_summary(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": sanitize_artifact_text(self.reason) or "[REDACTED]",
            "blocking_fields": [sanitize_artifact_text(item) or "[REDACTED]" for item in self.blocking_fields],
            "execution_mode": sanitize_artifact_text(self.execution_mode) or "[REDACTED]",
            "local_execution_allowed": self.local_execution_allowed,
            "live_execution_allowed": False,
            "real_execution_evidence_possible": self.real_execution_evidence_possible,
            "live_certification_evidence": False,
        }


@dataclass(frozen=True)
class ExecutionEvidence:
    gate: ExecutionGateDecision
    command_runner_kind: str
    executed: bool
    status: str
    return_code: int | None
    timeout: bool
    stdout_summary: str
    stderr_summary: str
    execution_evidence_type: str
    real_execution_evidence: bool
    live_certification_evidence: bool
    sanitized_command_summary: Mapping[str, Any]
    redaction_status: str
    retryable_infra_failure: bool = False


def _execution_mode_value(value: ExecutionMode | str) -> str:
    enum_value = getattr(value, "value", None)
    return str(enum_value or value)


def _sanitize_output(value: str) -> str:
    return sanitize_artifact_text(value) or ""


def _scenario_from_materialized(materialized: MaterializedExecutionRequest) -> str | None:
    if not materialized.argv.scenario_selectors:
        return None
    return str(materialized.argv.scenario_selectors[0])


def _contains_shell_metachar(value: str) -> bool:
    return any(marker in value for marker in _SHELL_METACHARS)


def _parse_release_flags(argv: tuple[str, ...]) -> tuple[dict[str, list[str]], tuple[str, ...]]:
    parsed: dict[str, list[str]] = {}
    errors: list[str] = []
    index = 4
    while index < len(argv):
        flag = argv[index]
        if flag not in _SUPPORTED_RELEASE_FLAGS:
            errors.append(f"unsupported release flag: {flag}")
            index += 1
            continue
        if index + 1 >= len(argv):
            errors.append(f"missing value for release flag: {flag}")
            break
        parsed.setdefault(flag, []).append(argv[index + 1])
        index += 2
    return parsed, tuple(errors)


def _argv_validation_errors(materialized: MaterializedExecutionRequest) -> tuple[str, ...]:
    argv = materialized.argv.argv
    errors: list[str] = []
    if len(argv) < 4 or argv[:4] != (*PYTEST_RUNNER, RELEASE_CERTIFICATION_PYTEST_TARGET):
        errors.append("argv must start with the supported pytest release certification invocation")
    if materialized.argv.pytest_target != RELEASE_CERTIFICATION_PYTEST_TARGET:
        errors.append("pytest target does not match release certification entrypoint")
    for item in argv:
        if _contains_shell_metachar(item):
            errors.append("argv contains shell metacharacters")
            break

    parsed, parse_errors = _parse_release_flags(argv)
    errors.extend(parse_errors)
    scenario_id = _scenario_from_materialized(materialized)
    if parsed.get("--release-mode", [None]) != [materialized.argv.release_mode]:
        errors.append("release mode flag does not match materialized argv")
    if materialized.argv.release_mode not in SUPPORTED_RELEASE_MODES:
        errors.append("unsupported release mode")
    if parsed.get("--release-scenario", [None]) != ([scenario_id] if scenario_id else [None]):
        errors.append("release scenario flag does not match materialized argv")
    stream_flags = tuple(parsed.get("--release-stream", []))
    expected_stream_flags = tuple(
        stream for stream in materialized.argv.stream_selectors if stream in CLI_RELEASE_STREAMS
    )
    if stream_flags != expected_stream_flags:
        errors.append("release stream flags do not match materialized argv")
    if parsed.get("--release-artifact-dir", [None]) != [materialized.argv.artifact_directory]:
        errors.append("artifact directory flag does not match materialized argv")
    if parsed.get("--release-profile", [None]) != [materialized.argv.profile_reference]:
        errors.append("release profile flag does not match materialized argv")
    return tuple(dict.fromkeys(errors))


def _scenario_gate_errors(materialized: MaterializedExecutionRequest) -> tuple[str, ...]:
    scenario_id = _scenario_from_materialized(materialized)
    if scenario_id is None or scenario_id not in SCENARIOS_BY_ID:
        return ("unknown release scenario",)
    classification = classify_scenario(scenario_id)
    if classification is ScenarioClassification.DESTRUCTIVE_DISPOSABLE_LAB_ONLY:
        return (f"scenario {scenario_id} is destructive/disposable-lab-only",)
    if classification is ScenarioClassification.RECOVERY:
        return (f"scenario {scenario_id} is a recovery scenario",)
    return ()


def _add_mode_gate_results(
    *,
    mode: str,
    allow_local_execution: bool,
    blockers: list[str],
    reasons: list[str],
) -> None:
    if mode == _LIVE_EXECUTION_MODE:
        blockers.append("execution_mode")
        reasons.append("live release-framework execution is unsupported in Phase 6C")
    elif mode not in {_DRY_RUN_EXECUTION_MODE, _LOCAL_EXECUTION_MODE}:
        blockers.append("execution_mode")
        reasons.append(f"unsupported release-framework execution mode: {mode}")

    if mode == _LOCAL_EXECUTION_MODE and not allow_local_execution:
        blockers.append("allow_local_execution")
        reasons.append("local execution requires explicit allow_local_execution=true")


def _add_materialized_gate_results(
    *,
    materialized: MaterializedExecutionRequest,
    blockers: list[str],
    reasons: list[str],
) -> None:
    if materialized.argv.executed:
        blockers.append("materialized_invocation")
        reasons.append("materialized invocation is already marked executed")
    if not materialized.eligibility.eligible:
        blockers.extend(materialized.eligibility.blocking_fields)
        reasons.append(materialized.eligibility.reason)
    if materialized.eligibility.live_execution_supported:
        blockers.append("future_execution_eligibility")
        reasons.append("materialized request unexpectedly claims live execution support")
    if materialized.eligibility.live_certification_evidence:
        blockers.append("future_execution_eligibility")
        reasons.append("materialized request cannot claim live certification evidence")
    if not materialized.profile_compatibility.compatible:
        blockers.append("profile_compatibility")
        reasons.append("generated profile compatibility failed")
    if not materialized.env_plan.safe:
        blockers.append("environment_plan")
        reasons.append("environment plan contains unsafe entries")
    if not materialized.artifact_directory.safe:
        blockers.append("artifact_directory")
        reasons.append("artifact directory plan is unsafe")


def _add_validation_gate_results(
    *,
    materialized: MaterializedExecutionRequest,
    blockers: list[str],
    reasons: list[str],
) -> None:
    scenario_errors = _scenario_gate_errors(materialized)
    if scenario_errors:
        blockers.append("scenario_id")
        reasons.extend(scenario_errors)

    argv_errors = _argv_validation_errors(materialized)
    if argv_errors:
        blockers.append("argv")
        reasons.extend(argv_errors)

    try:
        validate_artifact_payload_redacted(materialized.summary)
    except ValueError as exc:
        blockers.append("redaction")
        reasons.append(str(exc))


def evaluate_execution_gates(
    materialized: MaterializedExecutionRequest,
    *,
    requested_execution_mode: ExecutionMode | str,
    allow_local_execution: bool,
) -> ExecutionGateDecision:
    mode = _execution_mode_value(requested_execution_mode)
    blockers: list[str] = []
    reasons: list[str] = []

    _add_mode_gate_results(
        mode=mode,
        allow_local_execution=allow_local_execution,
        blockers=blockers,
        reasons=reasons,
    )
    _add_materialized_gate_results(materialized=materialized, blockers=blockers, reasons=reasons)
    _add_validation_gate_results(materialized=materialized, blockers=blockers, reasons=reasons)

    unique_blockers = tuple(dict.fromkeys(blockers))
    allowed = not unique_blockers and mode == _LOCAL_EXECUTION_MODE
    if mode == _DRY_RUN_EXECUTION_MODE and not unique_blockers:
        reasons.append("dry-run materialization is non-executing")

    return ExecutionGateDecision(
        allowed=allowed,
        reason=(
            "local release-framework harness execution is allowed"
            if allowed
            else "; ".join(dict.fromkeys(reasons)) or "execution is not allowed"
        ),
        blocking_fields=unique_blockers,
        execution_mode=mode,
        local_execution_allowed=allowed,
        real_execution_evidence_possible=allowed,
    )


def _command_summary(materialized: MaterializedExecutionRequest) -> dict[str, Any]:
    return dict(materialized.argv.to_summary())


def _blocked_evidence(
    materialized: MaterializedExecutionRequest,
    *,
    gate: ExecutionGateDecision,
    command_runner_kind: str,
) -> ExecutionEvidence:
    evidence_type = "dry_run_materialization" if gate.execution_mode == _DRY_RUN_EXECUTION_MODE else "blocked"
    return ExecutionEvidence(
        gate=gate,
        command_runner_kind=command_runner_kind,
        executed=False,
        status="blocked",
        return_code=None,
        timeout=False,
        stdout_summary="",
        stderr_summary=sanitize_artifact_text(gate.reason) or "[REDACTED]",
        execution_evidence_type=evidence_type,
        real_execution_evidence=False,
        live_certification_evidence=False,
        sanitized_command_summary=_command_summary(materialized),
        redaction_status="redacted",
    )


def execute_materialized_invocation(
    materialized: MaterializedExecutionRequest,
    *,
    command_runner: CommandRunner,
    requested_execution_mode: ExecutionMode | str,
    allow_local_execution: bool,
    timeout_seconds: int = 3600,
) -> ExecutionEvidence:
    gate = evaluate_execution_gates(
        materialized,
        requested_execution_mode=requested_execution_mode,
        allow_local_execution=allow_local_execution,
    )
    if not gate.allowed:
        evidence = _blocked_evidence(materialized, gate=gate, command_runner_kind=command_runner.kind)
        summarize_execution_evidence(evidence)
        return evidence

    request = CommandRunRequest(
        argv=materialized.argv.argv,
        env=dict(materialized.env_plan.allowed_env),
        timeout_seconds=timeout_seconds,
    )
    result = command_runner.run(request)
    if result.timed_out:
        status = "timeout"
    elif result.return_code == 0:
        status = "succeeded"
    else:
        status = "failed"

    evidence = ExecutionEvidence(
        gate=gate,
        command_runner_kind=command_runner.kind,
        executed=True,
        status=status,
        return_code=result.return_code,
        timeout=result.timed_out,
        stdout_summary=_sanitize_output(result.stdout),
        stderr_summary=_sanitize_output(result.stderr),
        execution_evidence_type="local_release_framework",
        real_execution_evidence=True,
        live_certification_evidence=False,
        sanitized_command_summary=_command_summary(materialized),
        redaction_status="redacted",
        retryable_infra_failure=result.timed_out,
    )
    summarize_execution_evidence(evidence)
    return evidence


def summarize_execution_evidence(evidence: ExecutionEvidence) -> dict[str, Any]:
    summary = {
        "execution_gate": evidence.gate.to_summary(),
        "execution_mode": evidence.gate.execution_mode,
        "command_runner_kind": sanitize_artifact_text(evidence.command_runner_kind) or "[REDACTED]",
        "executed": evidence.executed,
        "return_code": evidence.return_code,
        "timeout": evidence.timeout,
        "status": evidence.status,
        "stdout_summary": sanitize_artifact_text(evidence.stdout_summary) or "",
        "stderr_summary": sanitize_artifact_text(evidence.stderr_summary) or "",
        "execution_evidence_type": evidence.execution_evidence_type,
        "real_execution_evidence": evidence.real_execution_evidence,
        "live_certification_evidence": False,
        "sanitized_command_summary": evidence.sanitized_command_summary,
        "redaction_status": evidence.redaction_status,
        "retryable_infra_failure": evidence.retryable_infra_failure,
    }
    validate_artifact_payload_redacted(summary)
    return json.loads(json.dumps(summary, sort_keys=True))


class ReleaseFrameworkExecutionHarness:
    def __init__(self, *, command_runner: CommandRunner) -> None:
        self.command_runner = command_runner

    def execute(
        self,
        materialized: MaterializedExecutionRequest,
        *,
        requested_execution_mode: ExecutionMode | str,
        allow_local_execution: bool,
        timeout_seconds: int = 3600,
    ) -> ExecutionEvidence:
        return execute_materialized_invocation(
            materialized,
            command_runner=self.command_runner,
            requested_execution_mode=requested_execution_mode,
            allow_local_execution=allow_local_execution,
            timeout_seconds=timeout_seconds,
        )
