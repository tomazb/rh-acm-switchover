"""Ansible release stream adapter for release stream execution."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import AssertionRecord, ReportArtifact, StreamResult


PLAYBOOKS: dict[str, str] = {
    "preflight": "playbooks/preflight.yml",
    "ansible-passive-switchover": "playbooks/switchover.yml",
    "ansible-restore-only": "playbooks/restore_only.yml",
    "argocd-managed-switchover": "playbooks/switchover.yml",
    "decommission": "playbooks/decommission.yml",
}

REPORT_NAMES: dict[str, tuple[str, str]] = {
    "preflight": ("preflight", "preflight-report.json"),
    "ansible-passive-switchover": ("switchover", "switchover-report.json"),
    "ansible-restore-only": ("restore", "restore-only-report.json"),
    "argocd-managed-switchover": ("switchover", "switchover-report.json"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AnsibleAdapter:
    repo_root: Path
    collection_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "ansible"

    def build_extra_vars(self, scenario_id: str) -> dict:
        return {
            "acm_switchover_hubs": {
                "primary": {"context": self.primary_context, "kubeconfig": self.primary_kubeconfig},
                "secondary": {"context": self.secondary_context, "kubeconfig": self.secondary_kubeconfig},
            },
            "acm_switchover_operation": {
                "restore_only": scenario_id == "ansible-restore-only",
                "dry_run": False,
            },
            "acm_switchover_execution": {
                "mode": "execute",
                "report_dir": str(self.scenario_dir(scenario_id)),
                "checkpoint": {
                    "enabled": True,
                    "backend": "file",
                    "path": str(self.scenario_dir(scenario_id) / "checkpoint.json"),
                },
            },
            "acm_switchover_features": {
                "argocd": {
                    "manage": scenario_id == "argocd-managed-switchover",
                    "resume_on_failure": False,
                },
            },
        }

    def build_command(self, scenario_id: str) -> list[str]:
        if scenario_id not in PLAYBOOKS:
            raise ValueError(f"Unknown scenario: {scenario_id!r}. Known scenarios: {sorted(PLAYBOOKS)}")
        return [
            "ansible-playbook",
            PLAYBOOKS[scenario_id],
            "-e",
            json.dumps(self.build_extra_vars(scenario_id), sort_keys=True),
        ]

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
        scenario_dir = self.scenario_dir(scenario_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id)
        started_at = _now()
        completed = subprocess.run(
            command,
            cwd=self.collection_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=3600,
        )
        ended_at = _now()
        stdout_path = scenario_dir / "stdout.txt"
        stderr_path = scenario_dir / "stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        status = "passed" if completed.returncode == 0 else "failed"
        return StreamResult(
            stream="ansible",
            scenario_id=scenario_id,
            status=status,
            command=command,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            reports=self.discover_reports(scenario_id),
            assertions=[
                AssertionRecord(
                    capability=scenario_id,
                    name="exit-code",
                    status=status,
                    expected="0",
                    actual=str(completed.returncode),
                    evidence_path=str(stderr_path) if status == "failed" else str(stdout_path),
                    message="Ansible command completed" if status == "passed" else "Ansible command returned a non-zero exit code",
                )
            ],
            started_at=started_at,
            ended_at=ended_at,
        )
