"""Ansible release stream adapter for release stream execution."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
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
from tests.release.reporting.artifacts import write_capture_artifact

from .common import AssertionRecord, ReportArtifact, StreamResult

_COLLECTION_PLAYBOOKS_PREFIX = "ansible_collections/tomazb/acm_switchover/playbooks"

PLAYBOOKS: dict[str, str] = {
    "preflight": f"{_COLLECTION_PLAYBOOKS_PREFIX}/preflight.yml",
    "ansible-passive-switchover": f"{_COLLECTION_PLAYBOOKS_PREFIX}/switchover.yml",
    "ansible-restore-only": f"{_COLLECTION_PLAYBOOKS_PREFIX}/restore_only.yml",
    "argocd-managed-switchover": f"{_COLLECTION_PLAYBOOKS_PREFIX}/switchover.yml",
    "decommission": f"{_COLLECTION_PLAYBOOKS_PREFIX}/decommission.yml",
    "rbac-bootstrap": f"{_COLLECTION_PLAYBOOKS_PREFIX}/rbac_bootstrap.yml",
}
SUPPORTED_SCENARIO_IDS = frozenset(PLAYBOOKS)

REPORT_NAMES: dict[str, tuple[str, str]] = {
    "preflight": (REPORT_TYPE_PREFLIGHT, REPORT_FILENAME_PREFLIGHT),
    "ansible-passive-switchover": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
    "ansible-restore-only": (REPORT_TYPE_RESTORE, REPORT_FILENAME_RESTORE_ONLY),
    "argocd-managed-switchover": (REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER),
    "decommission": (REPORT_TYPE_DECOMMISSION, REPORT_FILENAME_DECOMMISSION),
    "rbac-bootstrap": ("rbac-bootstrap", "rbac-bootstrap-report.json"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(data: str | bytes | None) -> str:
    """Decode partial subprocess capture, handling bytes or None from TimeoutExpired."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


@dataclass(frozen=True)
class AnsibleAdapter:
    repo_root: Path
    collection_root: Path
    primary_context: str
    secondary_context: str
    primary_kubeconfig: str
    secondary_kubeconfig: str
    artifact_dir: Path

    @property
    def supported_scenario_ids(self) -> frozenset[str]:
        return SUPPORTED_SCENARIO_IDS

    def scenario_dir(self, scenario_id: str) -> Path:
        return self.artifact_dir / "scenarios" / scenario_id / "ansible"

    def _build_env(self, extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
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
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        return env

    def build_extra_vars(self, scenario_id: str) -> dict:
        restore_only = scenario_id == "ansible-restore-only"
        primary_hub = (
            {"context": "", "kubeconfig": ""}
            if restore_only
            else {"context": self.primary_context, "kubeconfig": self.primary_kubeconfig}
        )
        extra_vars = {
            "acm_switchover_hubs": {
                "primary": primary_hub,
                "secondary": {"context": self.secondary_context, "kubeconfig": self.secondary_kubeconfig},
            },
            "acm_switchover_operation": {
                "restore_only": restore_only,
                "method": "full" if restore_only else "passive",
                "old_hub_action": "none" if restore_only else "secondary",
                "activation_method": "patch",
                "min_managed_clusters": None,
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
        if scenario_id == "rbac-bootstrap":
            extra_vars["acm_switchover_operation"]["dry_run"] = True
            extra_vars["acm_switchover_execution"]["mode"] = "dry_run"
            extra_vars["summary_path"] = str(self.scenario_dir(scenario_id) / "rbac-bootstrap-report.json")
            extra_vars["acm_switchover_rbac_bootstrap"] = {
                "role": "operator",
                "include_decommission": True,
                "generate_kubeconfigs": False,
                "validate_permissions": False,
                "output_dir": str(self.scenario_dir(scenario_id) / "kubeconfigs"),
            }
        if scenario_id == "decommission":
            extra_vars["acm_switchover_operation"]["dry_run"] = True
            extra_vars["acm_switchover_execution"]["mode"] = "dry_run"
            extra_vars["summary_path"] = str(self.scenario_dir(scenario_id) / REPORT_FILENAME_DECOMMISSION)
            extra_vars["acm_switchover_decommission"] = {
                "confirm": True,
                "has_observability": "auto",
            }
        return extra_vars

    def build_command(self, scenario_id: str, extra_args: tuple[str, ...] = ()) -> list[str]:
        if scenario_id not in PLAYBOOKS:
            raise ValueError(f"Unknown scenario: {scenario_id!r}. Known scenarios: {sorted(PLAYBOOKS)}")
        return [
            "ansible-playbook",
            PLAYBOOKS[scenario_id],
            "-e",
            json.dumps(self.build_extra_vars(scenario_id), sort_keys=True),
        ] + list(extra_args)

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
        scenario_dir = self.scenario_dir(scenario_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(scenario_id, extra_args=extra_args)
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
                timeout=timeout_seconds or 3600,
                env=self._build_env(env),
            )
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
                    capability=scenario_id,
                    name="exit-code",
                    status="failed",
                    expected="0",
                    actual="timeout",
                    evidence_path=str(stderr_path),
                    message=f"Ansible command timed out after {timeout_seconds or 3600} seconds",
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
