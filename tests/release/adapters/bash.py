"""Bash release stream adapter for release stream execution."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tests.release.reporting.redaction import RedactionError, sanitize_text

from .common import AssertionRecord, StreamResult

SCRIPT_BY_SCENARIO = {
    "preflight": "scripts/preflight-check.sh",
    "bash-discovery": "scripts/discover-hub.sh",
    "bash-postflight": "scripts/postflight-check.sh",
}


_BASH_COMMAND_TIMEOUT_SECONDS = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(data: str | bytes | None) -> str:
    """Decode partial subprocess capture, handling bytes or None from TimeoutExpired."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _sanitized_write(path: Path, content: str) -> bool:
    try:
        sanitized = sanitize_text(content)
        path.write_text(sanitized.text, encoding="utf-8")
        return True
    except RedactionError:
        return False


@dataclass(frozen=True)
class BashAdapter:
    repo_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "bash"

    def build_command(self, scenario_id: str) -> list[str]:
        if scenario_id not in SCRIPT_BY_SCENARIO:
            raise ValueError(f"Unknown scenario: {scenario_id!r}. Known scenarios: {sorted(SCRIPT_BY_SCENARIO)}")
        script = SCRIPT_BY_SCENARIO[scenario_id]
        return [
            script,
            "--primary-context",
            self.primary_context,
            "--secondary-context",
            self.secondary_context,
            "--primary-kubeconfig",
            self.primary_kubeconfig,
            "--secondary-kubeconfig",
            self.secondary_kubeconfig,
        ]

    def execute(self, scenario_id: str) -> StreamResult:
        scenario_dir = self.scenario_dir(scenario_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id)
        stdout_path = scenario_dir / "stdout.txt"
        stderr_path = scenario_dir / "stderr.txt"
        started_at = _now()
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=_BASH_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            ended_at = _now()
            stdout_written = _sanitized_write(stdout_path, _decode(exc.stdout))
            stderr_written = _sanitized_write(stderr_path, _decode(exc.stderr))
            timeout_assertions: list[AssertionRecord] = [
                AssertionRecord(
                    capability=f"bash-{scenario_id}",
                    name="exit-code",
                    status="failed",
                    expected="0",
                    actual="timeout",
                    evidence_path=str(stderr_path),
                    message=f"Bash script timed out after {_BASH_COMMAND_TIMEOUT_SECONDS} seconds",
                )
            ]
            if not stdout_written or not stderr_written:
                timeout_assertions.append(
                    AssertionRecord(
                        capability=f"bash-{scenario_id}",
                        name="artifact-redaction",
                        status="failed",
                        expected="clean",
                        actual="rejected",
                        evidence_path="",
                        message="Captured output was rejected by the sanitizer",
                    )
                )
            return StreamResult(
                stream="bash",
                scenario_id=scenario_id,
                status="failed",
                command=command,
                returncode=-1,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                reports=[],
                assertions=timeout_assertions,
                started_at=started_at,
                ended_at=ended_at,
            )
        ended_at = _now()
        stdout_written = _sanitized_write(stdout_path, completed.stdout)
        stderr_written = _sanitized_write(stderr_path, completed.stderr)
        status = "passed" if completed.returncode == 0 else "failed"
        assertions = [
            AssertionRecord(
                capability=f"bash-{scenario_id}",
                name="exit-code",
                status=status,
                expected="0",
                actual=str(completed.returncode),
                evidence_path=(str(stdout_path) if status == "passed" else str(stderr_path)),
                message=(
                    "Bash script completed" if status == "passed" else "Bash script returned a non-zero exit code"
                ),
            )
        ]
        if not stdout_written or not stderr_written:
            status = "failed"
            assertions.append(
                AssertionRecord(
                    capability=f"bash-{scenario_id}",
                    name="artifact-redaction",
                    status="failed",
                    expected="clean",
                    actual="rejected",
                    evidence_path="",
                    message="Captured output was rejected by the sanitizer",
                )
            )
        return StreamResult(
            stream="bash",
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=[],
            assertions=assertions,
            started_at=started_at,
            ended_at=ended_at,
        )
