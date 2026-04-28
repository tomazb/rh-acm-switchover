"""Python CLI adapter for release stream execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tests.release.reporting.redaction import RedactionError, sanitize_text

from .common import AssertionRecord, ReportArtifact, StreamResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(data: str | bytes | None) -> str:
    """Decode partial subprocess capture, handling bytes or None from TimeoutExpired."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _sanitized_write(path: Path, content: str) -> bool:
    """Sanitize *content* and write to *path*. Returns False if content was rejected."""
    try:
        sanitized = sanitize_text(content)
        path.write_text(sanitized.text, encoding="utf-8")
        return True
    except RedactionError:
        return False


REPORT_NAMES: dict[str, tuple[str, str]] = {
    "preflight": ("preflight", "preflight-report.json"),
    "python-passive-switchover": ("switchover", "switchover-report.json"),
    "python-restore-only": ("restore", "restore-only-report.json"),
    "argocd-managed-switchover": ("switchover", "switchover-report.json"),
    "full-restore": ("switchover", "switchover-report.json"),
    "checkpoint-resume": ("switchover", "switchover-report.json"),
    "decommission": ("decommission", "decommission-report.json"),
    "failure-injection": ("switchover", "switchover-report.json"),
    "soak": ("switchover", "switchover-report.json"),
}


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

    def _build_env(self, scenario_id: str) -> dict[str, str]:
        """Build subprocess environment with KUBECONFIG set from adapter fields.

        Clears any inherited KUBECONFIG first, then sets it from the adapter
        kubeconfig fields. Restore-only uses secondary only; all other scenarios
        include both primary and secondary joined with os.pathsep.
        """
        env = {k: v for k, v in os.environ.items() if k != "KUBECONFIG"}
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

    def build_command(self, scenario_id: str) -> list[str]:
        scenario_dir = self.scenario_dir(scenario_id)
        state_file = scenario_dir / "state.json"

        if scenario_id == "python-restore-only":
            # restore-only is standalone; method and old-hub-action are not required
            return [
                sys.executable,
                "acm_switchover.py",
                "--secondary-context",
                self.secondary_context,
                "--state-file",
                str(state_file),
                "--restore-only",
            ]

        if scenario_id == "decommission":
            # decommission targets the primary hub only; --non-interactive for automation
            return [
                sys.executable,
                "acm_switchover.py",
                "--primary-context",
                self.primary_context,
                "--state-file",
                str(state_file),
                "--decommission",
                "--non-interactive",
            ]

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
        ]
        if scenario_id == "preflight":
            return base + ["--validate-only"]
        if scenario_id == "argocd-managed-switchover":
            return base + ["--argocd-manage"]
        # python-passive-switchover, full-restore, checkpoint-resume, failure-injection, soak
        return base

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

    def execute(self, scenario_id: str) -> StreamResult:
        output_dir = self.scenario_dir(scenario_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id)
        stdout_path = output_dir / "stdout.txt"
        stderr_path = output_dir / "stderr.txt"
        started_at = _now()
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=3600,
                env=self._build_env(scenario_id),
            )
        except subprocess.TimeoutExpired as exc:
            ended_at = _now()
            stdout_written = _sanitized_write(stdout_path, _decode(exc.stdout))
            stderr_written = _sanitized_write(stderr_path, _decode(exc.stderr))
            timeout_assertions: list[AssertionRecord] = [
                AssertionRecord(
                    capability=scenario_id,
                    name="exit-code",
                    status="failed",
                    expected="0",
                    actual="timeout",
                    evidence_path=str(stderr_path),
                    message="Python CLI timed out after 3600 seconds",
                )
            ]
            if not stdout_written or not stderr_written:
                timeout_assertions.append(
                    AssertionRecord(
                        capability=scenario_id,
                        name="artifact-redaction",
                        status="failed",
                        expected="clean",
                        actual="rejected",
                        evidence_path="",
                        message="Captured output was rejected by the sanitizer",
                    )
                )
            return StreamResult(
                stream="python",
                scenario_id=scenario_id,
                status="failed",
                command=command,
                returncode=-1,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                reports=self.discover_reports(scenario_id),
                assertions=timeout_assertions,
                started_at=started_at,
                ended_at=ended_at,
            )
        ended_at = _now()
        stdout_written = _sanitized_write(stdout_path, completed.stdout)
        stderr_written = _sanitized_write(stderr_path, completed.stderr)
        status = "passed" if completed.returncode == 0 else "failed"
        assertions: list[AssertionRecord] = [
            AssertionRecord(
                capability=scenario_id,
                name="exit-code",
                status=status,
                expected="0",
                actual=str(completed.returncode),
                evidence_path=str(stderr_path) if status == "failed" else str(stdout_path),
                message="Python CLI exited with expected code" if status == "passed" else "Python CLI returned a non-zero exit code",
            )
        ]
        if not stdout_written or not stderr_written:
            status = "failed"
            assertions.append(
                AssertionRecord(
                    capability=scenario_id,
                    name="artifact-redaction",
                    status="failed",
                    expected="clean",
                    actual="rejected",
                    evidence_path="",
                    message="Captured output was rejected by the sanitizer",
                )
            )
        return StreamResult(
            stream="python",
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=self.discover_reports(scenario_id),
            assertions=assertions,
            started_at=started_at,
            ended_at=ended_at,
        )
