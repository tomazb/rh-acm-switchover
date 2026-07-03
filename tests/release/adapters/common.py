from __future__ import annotations

"""Stream-neutral result model for release adapter records."""

import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from tests.release.reporting.artifacts import write_capture_artifact

DEFAULT_STREAM_COMMAND_TIMEOUT_SECONDS = 3600
_REDACTION_REJECTED_MESSAGE = "Captured output was rejected by the sanitizer"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(data: str | bytes | None) -> str:
    """Decode partial subprocess capture, handling bytes or None from TimeoutExpired."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


@dataclass(frozen=True)
class ReportArtifact:
    type: str
    path: str
    schema_version: str | int | None
    required: bool


@dataclass(frozen=True)
class AssertionRecord:
    capability: str
    name: str
    status: str
    expected: str
    actual: str
    evidence_path: str | None
    message: str


@dataclass(frozen=True)
class StreamResult:
    stream: str
    scenario_id: str
    status: str
    command: list[str]
    returncode: int | None
    stdout_path: str | None
    stderr_path: str | None
    reports: list[ReportArtifact]
    assertions: list[AssertionRecord]
    started_at: str
    ended_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StreamAdapter(Protocol):
    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> StreamResult: ...


def run_stream_subprocess(
    *,
    stream: str,
    scenario_id: str,
    command: list[str],
    cwd: Path,
    artifact_dir: Path,
    scenario_dir: Path,
    capability: str,
    timeout_message_template: str,
    success_message: str,
    failure_message: str,
    timeout_seconds: int | None = None,
    env: Mapping[str, str] | None = None,
    reports: Callable[[], list[ReportArtifact]] | None = None,
) -> StreamResult:
    """Run a stream command with shared timeout/capture/assertion handling.

    env=None inherits the current process environment (subprocess default);
    reports is re-evaluated when each StreamResult is built.
    """
    scenario_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = scenario_dir / "stdout.txt"
    stderr_path = scenario_dir / "stderr.txt"
    effective_timeout = timeout_seconds or DEFAULT_STREAM_COMMAND_TIMEOUT_SECONDS
    started_at = _now()
    run_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "text": True,
        "capture_output": True,
        "check": False,
        "timeout": effective_timeout,
    }
    if env is not None:
        run_kwargs["env"] = dict(env)

    def _write_captures(stdout_content: str, stderr_content: str) -> bool:
        _, stdout_written = write_capture_artifact(
            run_dir=artifact_dir,
            relative_path=stdout_path.relative_to(artifact_dir),
            content=stdout_content,
            rejected_placeholder="",
        )
        _, stderr_written = write_capture_artifact(
            run_dir=artifact_dir,
            relative_path=stderr_path.relative_to(artifact_dir),
            content=stderr_content,
            rejected_placeholder=_REDACTION_REJECTED_MESSAGE + "\n",
        )
        return stdout_written and stderr_written

    def _redaction_assertion() -> AssertionRecord:
        return AssertionRecord(
            capability=capability,
            name="artifact-redaction",
            status="failed",
            expected="clean",
            actual="rejected",
            evidence_path="",
            message=_REDACTION_REJECTED_MESSAGE,
        )

    def _result(status: str, returncode: int | None, assertions: list[AssertionRecord], ended_at: str) -> StreamResult:
        return StreamResult(
            stream=stream,
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=reports() if reports else [],
            assertions=assertions,
            started_at=started_at,
            ended_at=ended_at,
        )

    try:
        completed = subprocess.run(command, **run_kwargs)
    except subprocess.TimeoutExpired as exc:
        ended_at = _now()
        captures_clean = _write_captures(_decode(exc.stdout), _decode(exc.stderr))
        assertions = [
            AssertionRecord(
                capability=capability,
                name="exit-code",
                status="failed",
                expected="0",
                actual="timeout",
                evidence_path=str(stderr_path),
                message=timeout_message_template.format(timeout=effective_timeout),
            )
        ]
        if not captures_clean:
            assertions.append(_redaction_assertion())
        return _result("failed", -1, assertions, ended_at)

    ended_at = _now()
    captures_clean = _write_captures(completed.stdout, completed.stderr)
    status = "passed" if completed.returncode == 0 else "failed"
    assertions = [
        AssertionRecord(
            capability=capability,
            name="exit-code",
            status=status,
            expected="0",
            actual=str(completed.returncode),
            evidence_path=(str(stdout_path) if status == "passed" else str(stderr_path)),
            message=(success_message if status == "passed" else failure_message),
        )
    ]
    if not captures_clean:
        status = "failed"
        assertions.append(_redaction_assertion())
    return _result(status, completed.returncode, assertions, ended_at)
