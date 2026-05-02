"""Ansible release stream adapter for release stream execution."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tests.release.reporting.redaction import RedactionError, sanitize_text

from .common import AssertionRecord, ReportArtifact, StreamResult

_COLLECTION_PLAYBOOKS_PREFIX = "ansible_collections/tomazb/acm_switchover/playbooks"

PLAYBOOKS: dict[str, str] = {
    "preflight": f"{_COLLECTION_PLAYBOOKS_PREFIX}/preflight.yml",
    "ansible-passive-switchover": f"{_COLLECTION_PLAYBOOKS_PREFIX}/switchover.yml",
    "ansible-restore-only": f"{_COLLECTION_PLAYBOOKS_PREFIX}/restore_only.yml",
    "argocd-managed-switchover": f"{_COLLECTION_PLAYBOOKS_PREFIX}/switchover.yml",
    "decommission": f"{_COLLECTION_PLAYBOOKS_PREFIX}/decommission.yml",
}

REPORT_NAMES: dict[str, tuple[str, str]] = {
    "preflight": ("preflight", "preflight-report.json"),
    "ansible-passive-switchover": ("switchover", "switchover-report.json"),
    "ansible-restore-only": ("restore", "restore-only-report.json"),
    "argocd-managed-switchover": ("switchover", "switchover-report.json"),
}


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

    def _build_env(self) -> dict[str, str]:
        """Build subprocess environment with ANSIBLE_COLLECTIONS_PATH pointing to repo root."""
        env = dict(os.environ)
        env["ANSIBLE_COLLECTIONS_PATH"] = ":".join(
            [
                str(self.repo_root),
                os.path.expanduser("~/.ansible/collections"),
            ]
        )
        env.setdefault("ANSIBLE_LOCAL_TEMP", "/tmp/ansible-local")
        env.setdefault("ANSIBLE_REMOTE_TMP", "/tmp/ansible-remote")
        return env

    def build_extra_vars(self, scenario_id: str) -> dict:
        return {
            "acm_switchover_hubs": {
                "primary": {"context": self.primary_context, "kubeconfig": self.primary_kubeconfig},
                "secondary": {"context": self.secondary_context, "kubeconfig": self.secondary_kubeconfig},
            },
            "acm_switchover_operation": {
                "restore_only": scenario_id == "ansible-restore-only",
                "method": "full" if scenario_id == "ansible-restore-only" else "passive",
                "old_hub_action": "secondary",
                "activation_method": "patch",
                "min_managed_clusters": 0,
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
                "manage_auto_import_strategy": False,
                "token_expiry_warning_hours": 4,
                "skip_observability_checks": False,
                "skip_gitops_check": False,
                "skip_rbac_validation": False,
                "disable_observability_on_secondary": False,
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
                timeout=3600,
                env=self._build_env(),
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
                    message="Ansible command timed out after 3600 seconds",
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
                stream="ansible",
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
                message=(
                    "Ansible command completed"
                    if status == "passed"
                    else "Ansible command returned a non-zero exit code"
                ),
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
            stream="ansible",
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
