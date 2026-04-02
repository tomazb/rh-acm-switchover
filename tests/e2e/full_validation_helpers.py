"""
Helpers for the full-validation E2E test suite.

Provides subprocess wrappers for the switchover CLI, shell scripts, and
Python utilities, plus a class-level phase tracker for ordered tests.
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pytest

logger = logging.getLogger("e2e_full_validation")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SWITCHOVER_SCRIPT = REPO_ROOT / "acm_switchover.py"


class PhaseTracker:
    """Track phase pass/fail for ordered test dependencies."""

    _results: Dict[str, bool] = {}

    @classmethod
    def mark(cls, phase: str, passed: bool) -> None:
        cls._results[phase] = passed

    @classmethod
    def require(cls, *phases: str) -> None:
        """Skip current test if any prerequisite phase did not pass."""
        for phase in phases:
            result = cls._results.get(phase)
            if result is None:
                pytest.skip(f"Prerequisite phase '{phase}' has not run yet")
            if result is not True:
                pytest.skip(f"Prerequisite phase '{phase}' failed")

    @classmethod
    def reset(cls) -> None:
        cls._results.clear()


class RunResult:
    """Captures subprocess outcome with structured access."""

    def __init__(self, returncode: int, stdout: str, stderr: str, cmd: List[str]):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cmd = cmd

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def assert_success(self, msg: str = "") -> "RunResult":
        label = msg or f"Command failed: {' '.join(self.cmd)}"
        assert self.ok, f"{label}\n--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}"
        return self


def _safe_run(cmd: List[str], timeout: int, **kwargs) -> RunResult:
    """Run subprocess with TimeoutExpired handling."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            **kwargs,
        )
        return RunResult(proc.returncode, proc.stdout, proc.stderr, cmd)
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            returncode=-1,
            stdout=(
                (exc.stdout or b"").decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            ),
            stderr=(
                (exc.stderr or b"").decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            ),
            cmd=cmd,
        )


def run_switchover(
    primary: str,
    secondary: str,
    extra_args: Optional[List[str]] = None,
    timeout: int = 900,
    state_file: Optional[str] = None,
    method: str = "passive",
    old_hub_action: str = "secondary",
) -> RunResult:
    """Run acm_switchover.py as a subprocess.

    The normal switchover path has no interactive prompts, so plain
    subprocess.run() is sufficient.  Only decommission uses confirm_action().
    """
    cmd = [
        sys.executable,
        str(SWITCHOVER_SCRIPT),
        "--primary-context",
        primary,
        "--secondary-context",
        secondary,
        "--method",
        method,
        "--old-hub-action",
        old_hub_action,
    ]
    if state_file:
        cmd.extend(["--state-file", state_file])
    if extra_args:
        cmd.extend(extra_args)

    return _safe_run(cmd, timeout=timeout, cwd=str(REPO_ROOT))


def run_shell_script(
    script_name: str,
    args: Optional[List[str]] = None,
    timeout: int = 120,
    env_overrides: Optional[Dict[str, str]] = None,
) -> RunResult:
    """Run a shell script from the scripts/ directory."""
    script_path = SCRIPTS_DIR / script_name
    cmd = ["bash", str(script_path)]
    if args:
        cmd.extend(args)

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    return _safe_run(cmd, timeout=timeout, cwd=str(REPO_ROOT), env=env)


def run_python_tool(
    script_name: str,
    args: Optional[List[str]] = None,
    timeout: int = 60,
) -> RunResult:
    """Run a Python tool from the repo root (e.g. check_rbac.py, show_state.py)."""
    script_path = REPO_ROOT / script_name
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    return _safe_run(cmd, timeout=timeout, cwd=str(REPO_ROOT))


def kubectl(context: str, *args: str, timeout: int = 30) -> RunResult:
    """Run a kubectl command against a specific context."""
    cmd = ["kubectl", "--context", context] + list(args)
    return _safe_run(cmd, timeout=timeout)


def get_backup_schedule_phase(context: str, name: str = "acm-hub-backup") -> str:
    """Return the BackupSchedule .status.phase for a hub."""
    result = kubectl(
        context,
        "get",
        "backupschedule",
        name,
        "-n",
        "open-cluster-management-backup",
        "-o",
        "jsonpath={.status.phase}",
    )
    return result.stdout.strip()


def get_managed_cluster_count(context: str) -> int:
    """Return the number of ManagedCluster resources on a hub."""
    result = kubectl(context, "get", "managedclusters", "--no-headers")
    if not result.ok:
        return 0
    return len([line for line in result.stdout.strip().splitlines() if line.strip()])


def get_argocd_apps(context: str, namespace: str = "openshift-gitops") -> List[Dict]:
    """Return Argo CD Application objects from the given hub."""
    result = kubectl(
        context,
        "get",
        "applications.argoproj.io",
        "-n",
        namespace,
        "-o",
        "json",
    )
    if not result.ok:
        detail = result.output.strip() or "no command output"
        pytest.fail(f"Failed to list Argo CD Applications on {context}: {detail}")
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        detail = result.stdout.strip() or result.stderr.strip() or "empty output"
        pytest.fail(f"Invalid Argo CD Applications JSON on {context}: {detail}")
    return data.get("items", [])


def assert_hub_is_primary(context: str) -> None:
    """Assert that the given context is the active primary hub."""
    phase = get_backup_schedule_phase(context)
    assert phase == "Enabled", f"Expected hub {context} to have BackupSchedule phase 'Enabled', got '{phase}'"
    count = get_managed_cluster_count(context)
    assert count > 0, f"Expected hub {context} to have ManagedClusters, got {count}"


def wait_and_assert_hub_is_primary(context: str, timeout: int = 300, poll: int = 15) -> None:
    """Poll until the hub becomes primary (BackupSchedule Enabled + ManagedClusters present)."""
    deadline = time.time() + timeout
    last_phase = ""
    while time.time() < deadline:
        last_phase = get_backup_schedule_phase(context)
        count = get_managed_cluster_count(context)
        if last_phase == "Enabled" and count > 0:
            return
        time.sleep(poll)
    pytest.fail(
        f"Hub {context} did not become primary within {timeout}s " f"(last BackupSchedule phase: '{last_phase}')"
    )


def wait_for_restore_settled(context: str, timeout: int = 120, poll: int = 10) -> None:
    """Wait for the passive-sync restore on a hub to reach a stable state.

    After a switchover, the restore on the new secondary may be Running/InProgress.
    The tool's preflight and discover-hub.sh require it to be Enabled or Finished.
    """
    deadline = time.time() + timeout
    last_phase = ""
    while time.time() < deadline:
        result = kubectl(
            context,
            "get",
            "restores.cluster.open-cluster-management.io",
            "-n",
            "open-cluster-management-backup",
            "-o",
            "jsonpath={.items[0].status.phase}",
            timeout=10,
        )
        last_phase = result.stdout.strip()
        if last_phase in ("Enabled", "Finished"):
            logger.info("Restore on %s settled: %s", context, last_phase)
            return
        logger.info("Waiting for restore on %s to settle (phase=%s)", context, last_phase)
        time.sleep(poll)
    logger.warning("Restore on %s did not settle within %ds (phase=%s)", context, timeout, last_phase)
