"""Python CLI adapter for release stream execution."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import AssertionRecord, StreamResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PythonCliAdapter:
    repo_root: Path
    primary_context: str
    secondary_context: str
    # NOTE: primary_kubeconfig / secondary_kubeconfig are reserved for future KUBECONFIG env setup
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path
    method: str = "passive"
    old_hub_action: str = "secondary"

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "python"

    def build_command(self, scenario_id: str) -> list[str]:
        scenario_dir = self.scenario_dir(scenario_id)
        state_file = scenario_dir / "state.json"

        if scenario_id == "python-restore-only":
            # restore-only is standalone; method and old-hub-action are not required
            base_restore = [
                sys.executable,
                "acm_switchover.py",
                "--secondary-context",
                self.secondary_context,
                "--state-file",
                str(state_file),
            ]
            return base_restore + ["--restore-only"]

        base = [
            sys.executable,
            "acm_switchover.py",
            "--primary-context",
            self.primary_context,
            "--secondary-context",
            self.secondary_context,
            "--method",
            self.method,
            "--old-hub-action",
            self.old_hub_action,
            "--state-file",
            str(state_file),
        ]
        if scenario_id == "preflight":
            return base + ["--validate-only"]
        if scenario_id == "argocd-managed-switchover":
            return base + ["--argocd-manage"]
        return base

    def execute(self, scenario_id: str) -> StreamResult:
        output_dir = self.scenario_dir(scenario_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id)
        started_at = _now()
        completed = subprocess.run(command, cwd=self.repo_root, text=True, capture_output=True, check=False)
        ended_at = _now()
        stdout_path = output_dir / "stdout.txt"
        stderr_path = output_dir / "stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        status = "passed" if completed.returncode == 0 else "failed"
        return StreamResult(
            stream="python",
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=[],
            assertions=[
                AssertionRecord(
                    capability=scenario_id,
                    name="exit-code",
                    status=status,
                    expected="0",
                    actual=str(completed.returncode),
                    evidence_path=str(stdout_path),
                    message="Python CLI exited with expected code" if status == "passed" else "Python CLI returned a non-zero exit code",
                )
            ],
            started_at=started_at,
            ended_at=ended_at,
        )
