"""Python CLI adapter for release stream execution."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from lib.constants import (
    REPORT_FILENAME_DECOMMISSION,
    REPORT_FILENAME_PREFLIGHT,
    REPORT_FILENAME_RESTORE_ONLY,
    REPORT_FILENAME_SWITCHOVER,
    REPORT_TYPE_DECOMMISSION,
    REPORT_TYPE_PREFLIGHT,
    REPORT_TYPE_RESTORE,
    REPORT_TYPE_SWITCHOVER,
)

from .common import ReportArtifact, StreamResult, run_stream_subprocess

REPORT_NAMES: dict[str, tuple[str, str]] = {
    "preflight": (REPORT_TYPE_PREFLIGHT, REPORT_FILENAME_PREFLIGHT),
    "python-passive-switchover": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
    "python-restore-only": (REPORT_TYPE_RESTORE, REPORT_FILENAME_RESTORE_ONLY),
    "argocd-managed-switchover": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
    "full-restore": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
    "checkpoint-resume": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
    "decommission": (REPORT_TYPE_DECOMMISSION, REPORT_FILENAME_DECOMMISSION),
    "failure-injection": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
    "soak": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
}
SUPPORTED_SCENARIO_IDS = frozenset(REPORT_NAMES)


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

    @property
    def supported_scenario_ids(self) -> frozenset[str]:
        return SUPPORTED_SCENARIO_IDS

    def _build_env(self, scenario_id: str, extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
        """Build subprocess environment with KUBECONFIG set from adapter fields.

        Clears any inherited KUBECONFIG first, then sets it from the adapter
        kubeconfig fields. Restore-only uses secondary only; all other scenarios
        include both primary and secondary joined with os.pathsep.
        """
        env = {k: v for k, v in os.environ.items() if k != "KUBECONFIG"}
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items() if str(key) != "KUBECONFIG"})
        if scenario_id == "python-restore-only":
            kubeconfigs = [self.secondary_kubeconfig]
        else:
            kubeconfigs = [self.primary_kubeconfig, self.secondary_kubeconfig]
        kubeconfig_str = os.pathsep.join(k for k in kubeconfigs if k)
        if kubeconfig_str:
            env["KUBECONFIG"] = kubeconfig_str
        return env

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "python"

    def build_command(self, scenario_id: str, extra_args: tuple[str, ...] = ()) -> list[str]:
        if scenario_id not in REPORT_NAMES:
            raise ValueError(
                f"Unknown Python CLI release scenario: {scenario_id!r}. Known scenarios: {sorted(REPORT_NAMES)}"
            )
        scenario_dir = self.scenario_dir(scenario_id)
        state_file = scenario_dir / "state.json"
        report_dir = scenario_dir

        if scenario_id == "python-restore-only":
            # restore-only is standalone; method and old-hub-action are not required
            return [
                sys.executable,
                "acm_switchover.py",
                "--secondary-context",
                self.secondary_context,
                "--state-file",
                str(state_file),
                "--report-dir",
                str(report_dir),
                "--restore-only",
            ] + list(extra_args)

        if scenario_id == "decommission":
            # decommission targets the primary hub only; --non-interactive for automation
            return [
                sys.executable,
                "acm_switchover.py",
                "--primary-context",
                self.primary_context,
                "--state-file",
                str(state_file),
                "--report-dir",
                str(report_dir),
                "--decommission",
                "--non-interactive",
            ] + list(extra_args)

        # full-restore forces --method full regardless of the adapter method field
        method = "full" if scenario_id == "full-restore" else self.method
        base = [
            sys.executable,
            "acm_switchover.py",
            "--primary-context",
            self.primary_context,
            "--secondary-context",
            self.secondary_context,
            "--method",
            method,
            "--old-hub-action",
            self.old_hub_action,
            "--state-file",
            str(state_file),
            "--report-dir",
            str(report_dir),
        ]
        if scenario_id == "preflight":
            return base + ["--validate-only"] + list(extra_args)
        if scenario_id == "argocd-managed-switchover":
            return base + ["--argocd-manage"] + list(extra_args)
        # python-passive-switchover, full-restore, checkpoint-resume, failure-injection, soak
        return base + list(extra_args)

    def discover_reports(self, scenario_id: str) -> list[ReportArtifact]:
        if scenario_id not in REPORT_NAMES:
            return []
        report_type, filename = REPORT_NAMES[scenario_id]
        path = self.scenario_dir(scenario_id) / filename
        if not path.exists():
            return []
        try:
            schema_version = json.loads(path.read_text(encoding="utf-8")).get("schema_version")
        except (json.JSONDecodeError, OSError):
            schema_version = None
        return [ReportArtifact(type=report_type, path=str(path), schema_version=schema_version, required=True)]

    def execute(
        self,
        scenario_id: str,
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> StreamResult:
        return run_stream_subprocess(
            stream="python",
            scenario_id=scenario_id,
            command=self.build_command(scenario_id, extra_args=extra_args),
            cwd=self.repo_root,
            artifact_dir=self.artifact_dir,
            scenario_dir=self.scenario_dir(scenario_id),
            capability=scenario_id,
            timeout_message_template="Python CLI timed out after {timeout} seconds",
            success_message="Python CLI exited with expected code",
            failure_message="Python CLI returned a non-zero exit code",
            timeout_seconds=timeout_seconds,
            env=self._build_env(scenario_id, env),
            reports=lambda: self.discover_reports(scenario_id),
        )
