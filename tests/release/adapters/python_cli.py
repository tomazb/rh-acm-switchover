from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PythonCliAdapter:
    repo_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "python"

    def build_command(self, scenario_id: str) -> list[str]:
        state_file = self.artifact_dir / "scenarios" / scenario_id / "state.json"
        base = [
            "python",
            "acm_switchover.py",
            "--primary-context",
            self.primary_context,
            "--secondary-context",
            self.secondary_context,
            "--primary-kubeconfig",
            self.primary_kubeconfig,
            "--secondary-kubeconfig",
            self.secondary_kubeconfig,
            "--state-file",
            str(state_file),
        ]
        if scenario_id == "preflight":
            return base + ["--validate-only"]
        if scenario_id == "python-restore-only":
            return base + ["--restore-only"]
        if scenario_id == "argocd-managed-switchover":
            return base + ["--argocd-manage"]
        return base
