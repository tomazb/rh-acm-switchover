"""Bash release stream adapter for release stream execution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .common import StreamResult, run_stream_subprocess

SCRIPT_BY_SCENARIO = {
    "preflight": "scripts/preflight-check.sh",
    "bash-discovery": "scripts/discover-hub.sh",
    "bash-postflight": "scripts/postflight-check.sh",
}
SUPPORTED_SCENARIO_IDS = frozenset(SCRIPT_BY_SCENARIO)


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
        return run_stream_subprocess(
            stream="bash",
            scenario_id=scenario_id,
            command=self.build_command(scenario_id, extra_args=extra_args),
            cwd=self.repo_root,
            artifact_dir=self.artifact_dir,
            scenario_dir=self.scenario_dir(scenario_id),
            capability=f"bash-{scenario_id}",
            timeout_message_template="Bash script timed out after {timeout} seconds",
            success_message="Bash script completed",
            failure_message="Bash script returned a non-zero exit code",
            timeout_seconds=timeout_seconds,
            env=self._build_env(env) if env else None,
        )
