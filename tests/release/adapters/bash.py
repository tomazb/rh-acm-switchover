"""Bash release stream adapter for release stream execution."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from tests.release.reporting.artifacts import write_capture_artifact

from .common import AssertionRecord, StreamResult

SCRIPT_BY_SCENARIO = {
    "preflight": "scripts/preflight-check.sh",
    "bash-discovery": "scripts/discover-hub.sh",
    "bash-postflight": "scripts/postflight-check.sh",
}
SUPPORTED_SCENARIO_IDS = frozenset(SCRIPT_BY_SCENARIO)


_BASH_COMMAND_TIMEOUT_SECONDS = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(data: str | bytes | None) -> str:
    """Decode partial subprocess capture, handling bytes or None from TimeoutExpired."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


@dataclass(frozen=True)
class BashAdapter:
    repo_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    @property
    def supported_scenario_ids(self) -> frozenset[str]:
        return SUPPORTED_SCENARIO_IDS

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "bash"

    def _build_env(self, extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        return env

    def build_command(self, scenario_id: str, extra_args: tuple[str, ...] = ()) -> list[str]:
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
        ] + list(extra_args)

    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> StreamResult:
        scenario_dir = self.scenario_dir(scenario_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id, extra_args=extra_args)
        stdout_path = scenario_dir / "stdout.txt"
        stderr_path = scenario_dir / "stderr.txt"
        started_at = _now()
        run_kwargs = {
            "cwd": self.repo_root,
            "text": True,
            "capture_output": True,
            "check": False,
            "timeout": timeout_seconds or _BASH_COMMAND_TIMEOUT_SECONDS,
        }
        if env:
            run_kwargs["env"] = self._build_env(env)
        try:
            completed = subprocess.run(command, **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            ended_at = _now()
            _, stdout_written = write_capture_artifact(
                run_dir=self.artifact_dir,
                relative_path=stdout_path.relative_to(self.artifact_dir),
                content=_decode(exc.stdout),
                rejected_placeholder="",
            )
            _, stderr_written = write_capture_artifact(
                run_dir=self.artifact_dir,
                relative_path=stderr_path.relative_to(self.artifact_dir),
                content=_decode(exc.stderr),
                rejected_placeholder="Captured output was rejected by the sanitizer\n",
            )
            timeout_assertions: list[AssertionRecord] = [
                AssertionRecord(
                    capability=f"bash-{scenario_id}",
                    name="exit-code",
                    status="failed",
                    expected="0",
                    actual="timeout",
                    evidence_path=str(stderr_path),
                    message=f"Bash script timed out after {timeout_seconds or _BASH_COMMAND_TIMEOUT_SECONDS} seconds",
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
        _, stdout_written = write_capture_artifact(
            run_dir=self.artifact_dir,
            relative_path=stdout_path.relative_to(self.artifact_dir),
            content=completed.stdout,
            rejected_placeholder="",
        )
        _, stderr_written = write_capture_artifact(
            run_dir=self.artifact_dir,
            relative_path=stderr_path.relative_to(self.artifact_dir),
            content=completed.stderr,
            rejected_placeholder="Captured output was rejected by the sanitizer\n",
        )
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
