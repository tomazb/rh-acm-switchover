from __future__ import annotations

"""Python CLI adapter for release stream execution."""

import sys
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
