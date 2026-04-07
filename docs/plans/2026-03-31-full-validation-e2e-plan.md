# Full Validation E2E Test Suite — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a comprehensive E2E test suite that validates the ACM switchover tool against real clusters (mgmt1, mgmt2) across three Argo CD modes, with shell script cross-validation and a 4-hour soak test.

**Architecture:** A single pytest test file (`tests/e2e/test_e2e_full_validation.py`) containing an ordered `TestFullValidation` class. Tests invoke `acm_switchover.py` via `subprocess.run()` (no interactive prompts in the normal switchover path). Shell scripts and Python utilities are invoked as subprocesses for cross-validation. Class-level state tracking skips dependent phases on failure.

**Tech Stack:** pytest (existing), subprocess, kubectl/oc CLIs, existing E2E fixtures from `tests/e2e/conftest.py`

**Design doc:** `docs/plans/2026-03-31-full-validation-e2e-design.md`

---

### Task 1: Register new pytest markers

**Files:**
- Modify: `setup.cfg:75-80`

**Step 1: Add markers**

In the `[tool:pytest]` markers section, add two new markers after the existing ones:

```ini
markers =
    integration: Integration tests with mocked binaries
    unit: Unit tests for individual components
    slow: Slower running tests
    e2e: End-to-end tests
    e2e_dry_run: E2E dry-run tests
    e2e_full_validation: Full validation E2E suite against real clusters
    e2e_soak: Soak testing subset (long-running)
```

**Step 2: Verify markers are recognized**

Run: `source .venv/bin/activate && pytest --markers 2>/dev/null | grep e2e`
Expected: All 4 e2e markers listed without warnings.

**Step 3: Commit**

```bash
git add setup.cfg
git commit -m "config: register e2e_full_validation and e2e_soak pytest markers"
```

---

### Task 2: Add `--e2e-argocd-mode` conftest option

**Files:**
- Modify: `tests/e2e/conftest.py`

**Step 1: Add the CLI option**

After the `--e2e-inject-at-phase` option block (line 125), add:

```python
    group.addoption(
        "--e2e-argocd-mode",
        action="store",
        default=os.environ.get("E2E_ARGOCD_MODE", "rotate"),
        choices=["none", "pause", "pause-resume", "rotate"],
        help="Argo CD mode for full validation soak (env: E2E_ARGOCD_MODE, default: rotate)",
    )
```

**Step 2: Register the new markers in `pytest_configure`**

Update the `pytest_configure` function to also register the new markers:

```python
def pytest_configure(config):
    """Register E2E and resilience markers."""
    config.addinivalue_line("markers", "e2e: End-to-end tests requiring real clusters")
    config.addinivalue_line("markers", "resilience: Resilience tests with failure injection")
    config.addinivalue_line("markers", "e2e_full_validation: Full validation E2E suite against real clusters")
    config.addinivalue_line("markers", "e2e_soak: Soak testing subset (long-running)")
```

**Step 3: Run conftest import check**

Run: `source .venv/bin/activate && python -c "import tests.e2e.conftest" && echo OK`
Expected: `OK` (no import errors)

**Step 4: Commit**

```bash
git add tests/e2e/conftest.py
git commit -m "test: add --e2e-argocd-mode option and new marker registrations"
```

---

### Task 3: Create test helper module

**Files:**
- Create: `tests/e2e/full_validation_helpers.py`

This module provides the `run_switchover()`, `run_shell_script()`, and `run_python_tool()` subprocess wrappers, plus the `PhaseTracker` class for ordered test dependencies.

**Step 1: Write the helper module**

```python
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
from typing import Dict, List, Optional, Tuple

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


def run_switchover(
    primary: str,
    secondary: str,
    extra_args: Optional[List[str]] = None,
    timeout: int = 900,
    state_file: Optional[str] = None,
) -> RunResult:
    """Run acm_switchover.py as a subprocess.

    The normal switchover path has no interactive prompts, so plain
    subprocess.run() is sufficient.  Only decommission uses confirm_action().
    """
    cmd = [
        sys.executable,
        str(SWITCHOVER_SCRIPT),
        "--primary-context", primary,
        "--secondary-context", secondary,
    ]
    if state_file:
        cmd.extend(["--state-file", state_file])
    if extra_args:
        cmd.extend(extra_args)

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr, cmd)


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

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
        env=env,
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr, cmd)


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

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr, cmd)


def kubectl(context: str, *args: str, timeout: int = 30) -> RunResult:
    """Run a kubectl command against a specific context."""
    cmd = ["kubectl", "--context", context] + list(args)
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr, cmd)


def get_backup_schedule_phase(context: str, name: str = "acm-hub-backup") -> str:
    """Return the BackupSchedule .status.phase for a hub."""
    result = kubectl(
        context, "get", "backupschedule", name,
        "-n", "open-cluster-management-backup",
        "-o", "jsonpath={.status.phase}",
    )
    return result.stdout.strip()


def get_managed_cluster_count(context: str) -> int:
    """Return the number of ManagedCluster resources on a hub."""
    result = kubectl(context, "get", "managedclusters", "--no-headers")
    if not result.ok:
        return 0
    return len([line for line in result.stdout.strip().splitlines() if line.strip()])


def get_argocd_apps(context: str, namespace: str = "openshift-gitops") -> List[Dict]:
    """Return Argo CD Application names and sync statuses."""
    result = kubectl(
        context, "get", "applications.argoproj.io",
        "-n", namespace,
        "-o", "json",
    )
    if not result.ok:
        return []
    data = json.loads(result.stdout)
    return data.get("items", [])


def assert_hub_is_primary(context: str) -> None:
    """Assert that the given context is the active primary hub."""
    phase = get_backup_schedule_phase(context)
    assert phase == "Enabled", (
        f"Expected hub {context} to have BackupSchedule phase 'Enabled', got '{phase}'"
    )
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
        f"Hub {context} did not become primary within {timeout}s "
        f"(last BackupSchedule phase: '{last_phase}')"
    )
```

**Step 2: Verify the module imports**

Run: `source .venv/bin/activate && python -c "from tests.e2e.full_validation_helpers import PhaseTracker, run_switchover, run_shell_script; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add tests/e2e/full_validation_helpers.py
git commit -m "test: add full validation E2E helper module

Subprocess wrappers for switchover CLI, shell scripts, Python tools,
and a PhaseTracker for ordered test dependencies."
```

---

### Task 4: Create test file — phases 0-3 (baseline, validation, dry-run)

**Files:**
- Create: `tests/e2e/test_e2e_full_validation.py`

**Step 1: Write phases 0-3**

```python
"""
Full Validation E2E Test Suite.

Comprehensive end-to-end testing against real clusters with three Argo CD
variants, shell script cross-validation, and soak testing.

Phases execute sequentially; later phases skip if prerequisites failed.

Usage:
    # Full suite (phases 0-11)
    pytest -m e2e_full_validation --primary-context=mgmt2 --secondary-context=mgmt1 \\
        tests/e2e/test_e2e_full_validation.py -v -s

    # Skip soak
    pytest -m "e2e_full_validation and not e2e_soak" \\
        --primary-context=mgmt2 --secondary-context=mgmt1 \\
        tests/e2e/test_e2e_full_validation.py -v -s

    # Soak only
    pytest -m e2e_soak --primary-context=mgmt2 --secondary-context=mgmt1 \\
        tests/e2e/test_e2e_full_validation.py::TestFullValidation::test_phase10_soak -v -s
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import ClassVar, Dict

import pytest

from tests.e2e.full_validation_helpers import (
    PhaseTracker,
    RunResult,
    assert_hub_is_primary,
    get_argocd_apps,
    get_backup_schedule_phase,
    get_managed_cluster_count,
    kubectl,
    run_python_tool,
    run_shell_script,
    run_switchover,
    wait_and_assert_hub_is_primary,
)

logger = logging.getLogger("e2e_full_validation")


@pytest.mark.e2e
@pytest.mark.e2e_full_validation
class TestFullValidation:
    """Full validation suite — phases run in definition order."""

    # Populated by conftest fixtures via first test
    _primary: ClassVar[str] = ""
    _secondary: ClassVar[str] = ""
    _output_dir: ClassVar[Path] = Path(".")
    _state_dir: ClassVar[Path] = Path(".")

    @pytest.fixture(autouse=True)
    def _inject_config(self, e2e_config, tmp_path_factory):
        """Inject cluster contexts from conftest into class-level state."""
        if not TestFullValidation._primary:
            TestFullValidation._primary = e2e_config.primary_context
            TestFullValidation._secondary = e2e_config.secondary_context
            base = tmp_path_factory.mktemp("full_validation")
            TestFullValidation._output_dir = base / "output"
            TestFullValidation._output_dir.mkdir()
            TestFullValidation._state_dir = base / "states"
            TestFullValidation._state_dir.mkdir()

    def _state_file(self, label: str) -> str:
        return str(self._state_dir / f"{label}-state.json")

    # ── Phase 0: Baseline Cleanup ─────────────────────────────────────

    def test_phase0_baseline_cleanup(self, require_cluster_contexts):
        """Clean up BackupCollision on secondary, ensure known-good baseline."""
        passed = False
        try:
            secondary = self._secondary  # mgmt1

            # Delete colliding BackupSchedule if in BackupCollision state
            phase = get_backup_schedule_phase(secondary)
            if phase == "BackupCollision":
                logger.info("Cleaning up BackupCollision on %s", secondary)
                kubectl(
                    secondary, "delete", "backupschedule", "acm-hub-backup",
                    "-n", "open-cluster-management-backup",
                    "--ignore-not-found",
                )
                # Wait for it to be gone
                time.sleep(5)

            # Verify primary is healthy
            assert_hub_is_primary(self._primary)

            # Verify secondary has a passive-sync restore or is clean
            mc_count = get_managed_cluster_count(secondary)
            logger.info(
                "Baseline: primary=%s (primary), secondary=%s (%d MCs)",
                self._primary, secondary, mc_count,
            )
            passed = True
        finally:
            PhaseTracker.mark("phase0", passed)

    # ── Phase 1: Shell Script Validation ──────────────────────────────

    def test_phase1_shell_validation(self, require_cluster_contexts):
        """Run discover-hub, preflight, argocd-check, check_rbac, show_state."""
        PhaseTracker.require("phase0")
        passed = False
        try:
            contexts = f"{self._primary},{self._secondary}"

            # discover-hub
            result = run_shell_script(
                "discover-hub.sh",
                ["--contexts", contexts, "--verbose"],
                timeout=60,
            )
            result.assert_success("discover-hub.sh failed")
            logger.info("discover-hub output:\n%s", result.stdout[:2000])

            # preflight-check against primary
            result = run_shell_script(
                "preflight-check.sh",
                ["--context", self._primary],
                timeout=120,
            )
            # preflight may return warnings (non-zero) — log but don't fail hard
            logger.info("preflight exit=%d\n%s", result.returncode, result.stdout[:2000])

            # argocd-check via Python tool
            result = run_switchover(
                self._primary, self._secondary,
                extra_args=["--argocd-check"],
                timeout=120,
            )
            logger.info("argocd-check exit=%d\n%s", result.returncode, result.output[:2000])

            # check_rbac.py
            result = run_python_tool(
                "check_rbac.py",
                ["--context", self._primary],
                timeout=60,
            )
            logger.info("check_rbac exit=%d\n%s", result.returncode, result.output[:1000])

            passed = True
        finally:
            PhaseTracker.mark("phase1", passed)

    # ── Phase 2: Validate-Only ────────────────────────────────────────

    def test_phase2_validate_only(self, require_cluster_contexts):
        """Run --validate-only for both directions."""
        PhaseTracker.require("phase0")
        passed = False
        try:
            # Forward: primary → secondary
            result = run_switchover(
                self._primary, self._secondary,
                extra_args=["--validate-only"],
                timeout=120,
            )
            result.assert_success("validate-only (forward) failed")
            logger.info("validate-only forward OK")

            # Reverse: secondary → primary
            result = run_switchover(
                self._secondary, self._primary,
                extra_args=["--validate-only"],
                timeout=120,
            )
            result.assert_success("validate-only (reverse) failed")
            logger.info("validate-only reverse OK")

            passed = True
        finally:
            PhaseTracker.mark("phase2", passed)

    # ── Phase 3: Dry-Run ──────────────────────────────────────────────

    def test_phase3_dry_run(self, require_cluster_contexts):
        """Run --dry-run for both directions × 3 ArgoCD modes."""
        PhaseTracker.require("phase0")
        passed = False
        try:
            argocd_variants = [
                ("no-argocd", []),
                ("argocd-pause", ["--argocd-manage"]),
                ("argocd-full", ["--argocd-manage", "--argocd-resume-after-switchover"]),
            ]

            for label, argocd_args in argocd_variants:
                for direction, (pri, sec) in [
                    ("fwd", (self._primary, self._secondary)),
                    ("rev", (self._secondary, self._primary)),
                ]:
                    state = self._state_file(f"dryrun-{label}-{direction}")
                    result = run_switchover(
                        pri, sec,
                        extra_args=["--dry-run", "--state-file", state] + argocd_args,
                        timeout=180,
                    )
                    result.assert_success(f"dry-run {label} {direction} failed")
                    logger.info("dry-run %s %s OK", label, direction)

            passed = True
        finally:
            PhaseTracker.mark("phase3", passed)
```

**Step 2: Verify file parses**

Run: `source .venv/bin/activate && python -c "import ast; ast.parse(open('tests/e2e/test_e2e_full_validation.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Run dry-run phase tests (no real clusters needed)**

Run: `source .venv/bin/activate && pytest tests/e2e/test_e2e_full_validation.py --collect-only 2>&1 | head -20`
Expected: Shows collected test methods for phases 0-3.

**Step 4: Commit**

```bash
git add tests/e2e/test_e2e_full_validation.py
git commit -m "test: add full validation E2E phases 0-3

Baseline cleanup, shell script validation, validate-only, and dry-run
phases with three Argo CD variants."
```

---

### Task 5: Add phases 4-5 (first real switchover + post-validation)

**Files:**
- Modify: `tests/e2e/test_e2e_full_validation.py`

**Step 1: Append phases 4-5 to the class**

Add after the `test_phase3_dry_run` method:

```python
    # ── Phase 4: Real Switchover — No ArgoCD ──────────────────────────

    def test_phase4_switchover_no_argocd(self, require_cluster_contexts):
        """Real switchover primary→secondary without Argo CD management."""
        PhaseTracker.require("phase2")
        passed = False
        try:
            state = self._state_file("phase4-no-argocd")
            result = run_switchover(
                self._primary, self._secondary,
                extra_args=["--state-file", state],
                timeout=900,
            )
            result.assert_success("Phase 4 switchover failed")

            # After switchover: secondary becomes new primary
            wait_and_assert_hub_is_primary(self._secondary, timeout=300)

            # Verify state file shows COMPLETED
            with open(state) as f:
                st = json.load(f)
            assert st.get("current_phase") == "COMPLETED", (
                f"Expected COMPLETED, got {st.get('current_phase')}"
            )

            logger.info(
                "Phase 4 switchover %s→%s completed",
                self._primary, self._secondary,
            )
            passed = True
        finally:
            PhaseTracker.mark("phase4", passed)

    # ── Phase 5: Post-Switchover Cross-Validation ─────────────────────

    def test_phase5_post_switchover_validation(self, require_cluster_contexts):
        """Cross-validate with shell scripts after phase 4 switchover."""
        PhaseTracker.require("phase4")
        passed = False
        try:
            new_primary = self._secondary  # roles are swapped after phase 4

            # postflight-check against new primary
            result = run_shell_script(
                "postflight-check.sh",
                ["--context", new_primary],
                timeout=120,
            )
            logger.info("postflight exit=%d\n%s", result.returncode, result.stdout[:2000])

            # discover-hub to confirm role swap
            contexts = f"{self._primary},{self._secondary}"
            result = run_shell_script(
                "discover-hub.sh",
                ["--contexts", contexts, "--verbose"],
                timeout=60,
            )
            result.assert_success("discover-hub post-switchover failed")
            logger.info("discover-hub post-switchover:\n%s", result.stdout[:2000])

            # show_state.py for the phase4 state file
            state = self._state_file("phase4-no-argocd")
            result = run_python_tool("show_state.py", ["--state-file", state])
            logger.info("show_state:\n%s", result.output[:1000])

            passed = True
        finally:
            PhaseTracker.mark("phase5", passed)
```

**Step 2: Verify collection**

Run: `source .venv/bin/activate && pytest tests/e2e/test_e2e_full_validation.py --collect-only 2>&1 | grep "test_phase[45]"`
Expected: Both phase 4 and 5 test methods collected.

**Step 3: Commit**

```bash
git add tests/e2e/test_e2e_full_validation.py
git commit -m "test: add full validation E2E phases 4-5

Real switchover without Argo CD and post-switchover cross-validation
with postflight-check, discover-hub, and show_state."
```

---

### Task 6: Add phases 6-7 (ArgoCD pause-only + resume-only)

**Files:**
- Modify: `tests/e2e/test_e2e_full_validation.py`

**Step 1: Append phases 6-7 to the class**

Add after `test_phase5_post_switchover_validation`:

```python
    # ── Phase 6: Switchover — ArgoCD Pause-Only ───────────────────────

    def test_phase6_switchover_argocd_pause(self, require_cluster_contexts):
        """Switchover with --argocd-manage (pause-only, no resume)."""
        PhaseTracker.require("phase4")
        passed = False
        try:
            # After phase 4, roles are swapped: secondary is now primary
            current_primary = self._secondary
            current_secondary = self._primary

            state = self._state_file("phase6-argocd-pause")
            result = run_switchover(
                current_primary, current_secondary,
                extra_args=["--argocd-manage", "--state-file", state],
                timeout=900,
            )
            result.assert_success("Phase 6 switchover failed")

            # New primary is self._primary again (swapped back)
            wait_and_assert_hub_is_primary(current_secondary, timeout=300)

            # Verify Argo CD apps on the NEW primary have paused annotation
            apps = get_argocd_apps(current_secondary)
            paused_apps = [
                a for a in apps
                if a.get("metadata", {}).get("annotations", {}).get(
                    "acm-switchover.argoproj.io/paused-by"
                )
            ]
            logger.info(
                "Phase 6: %d/%d apps have paused-by annotation on %s",
                len(paused_apps), len(apps), current_secondary,
            )

            # Verify argocd-manage.sh status shows paused state
            result = run_shell_script(
                "argocd-manage.sh",
                ["--context", current_secondary, "--mode", "status"],
                timeout=60,
            )
            logger.info("argocd status:\n%s", result.output[:2000])

            # Verify state file records argocd paused apps
            with open(state) as f:
                st = json.load(f)
            assert st.get("current_phase") == "COMPLETED"

            passed = True
        finally:
            PhaseTracker.mark("phase6", passed)

    # ── Phase 7: ArgoCD Resume-Only ───────────────────────────────────

    def test_phase7_argocd_resume_only(self, require_cluster_contexts):
        """Standalone --argocd-resume-only to restore paused apps."""
        PhaseTracker.require("phase6")
        passed = False
        try:
            # After phase 6: self._primary is now primary again
            resume_target = self._primary
            state = self._state_file("phase6-argocd-pause")

            result = run_switchover(
                resume_target, resume_target,  # both args needed but only secondary matters
                extra_args=[
                    "--argocd-resume-only",
                    "--state-file", state,
                ],
                timeout=120,
            )
            result.assert_success("Phase 7 argocd-resume-only failed")

            # Verify paused-by annotations are removed
            apps = get_argocd_apps(resume_target)
            still_paused = [
                a for a in apps
                if a.get("metadata", {}).get("annotations", {}).get(
                    "acm-switchover.argoproj.io/paused-by"
                )
            ]
            assert len(still_paused) == 0, (
                f"Expected 0 paused apps after resume, found {len(still_paused)}"
            )
            logger.info("Phase 7: all apps resumed on %s", resume_target)

            passed = True
        finally:
            PhaseTracker.mark("phase7", passed)
```

**Step 2: Verify collection**

Run: `source .venv/bin/activate && pytest tests/e2e/test_e2e_full_validation.py --collect-only 2>&1 | grep "test_phase[67]"`
Expected: Both phase 6 and 7 collected.

**Step 3: Commit**

```bash
git add tests/e2e/test_e2e_full_validation.py
git commit -m "test: add full validation E2E phases 6-7

ArgoCD pause-only switchover with annotation verification and
standalone resume-only with cleanup validation."
```

---

### Task 7: Add phases 8-9 (ArgoCD pause+resume + restore original state)

**Files:**
- Modify: `tests/e2e/test_e2e_full_validation.py`

**Step 1: Append phases 8-9**

Add after `test_phase7_argocd_resume_only`:

```python
    # ── Phase 8: Switchover — ArgoCD Pause+Resume ─────────────────────

    def test_phase8_switchover_argocd_full(self, require_cluster_contexts):
        """Switchover with --argocd-manage --argocd-resume-after-switchover."""
        PhaseTracker.require("phase7")
        passed = False
        try:
            # After phase 6+7: self._primary is primary
            current_primary = self._primary
            current_secondary = self._secondary

            state = self._state_file("phase8-argocd-full")
            result = run_switchover(
                current_primary, current_secondary,
                extra_args=[
                    "--argocd-manage",
                    "--argocd-resume-after-switchover",
                    "--state-file", state,
                ],
                timeout=900,
            )
            result.assert_success("Phase 8 switchover failed")

            # New primary is self._secondary
            wait_and_assert_hub_is_primary(current_secondary, timeout=300)

            # Verify apps were resumed (no paused-by annotations)
            apps = get_argocd_apps(current_secondary)
            still_paused = [
                a for a in apps
                if a.get("metadata", {}).get("annotations", {}).get(
                    "acm-switchover.argoproj.io/paused-by"
                )
            ]
            assert len(still_paused) == 0, (
                f"Expected 0 paused apps after auto-resume, found {len(still_paused)}"
            )

            # argocd-manage.sh status as extra validation
            result = run_shell_script(
                "argocd-manage.sh",
                ["--context", current_secondary, "--mode", "status"],
                timeout=60,
            )
            logger.info("argocd status post-phase8:\n%s", result.output[:2000])

            logger.info("Phase 8 switchover+resume %s→%s completed", current_primary, current_secondary)
            passed = True
        finally:
            PhaseTracker.mark("phase8", passed)

    # ── Phase 9: Restore Original State ───────────────────────────────

    def test_phase9_restore_original_state(self, require_cluster_contexts):
        """Reverse switchover to restore mgmt2 as primary (original state)."""
        PhaseTracker.require("phase8")
        passed = False
        try:
            # After phase 8: self._secondary is primary, self._primary is secondary
            # We want to restore: self._primary → primary role (original mgmt2)
            current_primary = self._secondary
            current_secondary = self._primary

            state = self._state_file("phase9-restore")
            result = run_switchover(
                current_primary, current_secondary,
                extra_args=["--state-file", state],
                timeout=900,
            )
            result.assert_success("Phase 9 reverse switchover failed")

            # Verify original primary is back
            wait_and_assert_hub_is_primary(current_secondary, timeout=300)

            # postflight cross-validation
            result = run_shell_script(
                "postflight-check.sh",
                ["--context", current_secondary],
                timeout=120,
            )
            logger.info("postflight post-phase9:\n%s", result.stdout[:2000])

            logger.info(
                "Phase 9 restored original state: %s is primary again",
                current_secondary,
            )
            passed = True
        finally:
            PhaseTracker.mark("phase9", passed)
```

**Step 2: Verify collection**

Run: `source .venv/bin/activate && pytest tests/e2e/test_e2e_full_validation.py --collect-only 2>&1 | grep "test_phase[89]"`
Expected: Both phase 8 and 9 collected.

**Step 3: Commit**

```bash
git add tests/e2e/test_e2e_full_validation.py
git commit -m "test: add full validation E2E phases 8-9

ArgoCD pause+auto-resume switchover and reverse switchover to restore
original hub topology."
```

---

### Task 8: Add phase 10 (soak test)

**Files:**
- Modify: `tests/e2e/test_e2e_full_validation.py`

**Step 1: Append phase 10**

Add after `test_phase9_restore_original_state`:

```python
    # ── Phase 10: Soak Test ───────────────────────────────────────────

    @pytest.mark.e2e_soak
    @pytest.mark.slow
    def test_phase10_soak(self, request, require_cluster_contexts):
        """4-hour soak with ArgoCD mode rotation and periodic cross-validation."""
        PhaseTracker.require("phase9")
        passed = False
        try:
            argocd_mode = request.config.getoption("--e2e-argocd-mode", "rotate")
            run_hours = request.config.getoption("--e2e-run-hours") or 4.0
            cooldown = request.config.getoption("--e2e-cooldown") or 60

            argocd_rotation = [
                ("no-argocd", []),
                ("argocd-pause", ["--argocd-manage"]),
                ("argocd-full", ["--argocd-manage", "--argocd-resume-after-switchover"]),
            ]

            # After phase 9: self._primary is original primary
            current_primary = self._primary
            current_secondary = self._secondary

            deadline = time.time() + (run_hours * 3600)
            cycle = 0
            failures = 0
            consecutive_failures = 0
            max_consecutive = 3
            results_log = self._output_dir / "soak_results.jsonl"

            while time.time() < deadline and consecutive_failures < max_consecutive:
                cycle += 1

                # Select ArgoCD mode
                if argocd_mode == "rotate":
                    _, argocd_args = argocd_rotation[cycle % len(argocd_rotation)]
                    mode_label = argocd_rotation[cycle % len(argocd_rotation)][0]
                elif argocd_mode == "none":
                    argocd_args, mode_label = [], "no-argocd"
                elif argocd_mode == "pause":
                    argocd_args, mode_label = ["--argocd-manage"], "argocd-pause"
                else:
                    argocd_args = ["--argocd-manage", "--argocd-resume-after-switchover"]
                    mode_label = "argocd-full"

                state = self._state_file(f"soak-cycle-{cycle:04d}")
                start = time.time()

                result = run_switchover(
                    current_primary, current_secondary,
                    extra_args=["--state-file", state] + argocd_args,
                    timeout=900,
                )
                duration = time.time() - start
                success = result.ok

                # Log result
                entry = {
                    "cycle": cycle,
                    "primary": current_primary,
                    "secondary": current_secondary,
                    "argocd_mode": mode_label,
                    "success": success,
                    "duration_seconds": round(duration, 1),
                    "returncode": result.returncode,
                }
                with open(results_log, "a") as f:
                    f.write(json.dumps(entry) + "\n")

                if success:
                    consecutive_failures = 0
                    logger.info(
                        "Soak cycle %d OK (%s, %.0fs) %s→%s",
                        cycle, mode_label, duration, current_primary, current_secondary,
                    )
                else:
                    failures += 1
                    consecutive_failures += 1
                    logger.warning(
                        "Soak cycle %d FAILED (%s, %.0fs) %s→%s\n%s",
                        cycle, mode_label, duration,
                        current_primary, current_secondary,
                        result.output[-500:],
                    )

                # If argocd-pause (no auto-resume), do a standalone resume
                if success and mode_label == "argocd-pause":
                    resume_result = run_switchover(
                        current_secondary, current_secondary,
                        extra_args=["--argocd-resume-only", "--state-file", state],
                        timeout=120,
                    )
                    if not resume_result.ok:
                        logger.warning("Soak cycle %d: argocd-resume-only failed", cycle)

                # Periodic cross-validation every 5th cycle
                if cycle % 5 == 0 and success:
                    run_shell_script(
                        "postflight-check.sh",
                        ["--context", current_secondary],
                        timeout=120,
                    )

                # Swap directions for next cycle
                current_primary, current_secondary = current_secondary, current_primary

                # Cooldown
                if time.time() < deadline:
                    time.sleep(cooldown)

            # Report
            logger.info(
                "Soak complete: %d cycles, %d failures, %d consecutive failures",
                cycle, failures, consecutive_failures,
            )
            assert consecutive_failures < max_consecutive, (
                f"Soak stopped early: {max_consecutive} consecutive failures"
            )

            passed = True
        finally:
            PhaseTracker.mark("phase10", passed)
```

**Step 2: Verify collection**

Run: `source .venv/bin/activate && pytest tests/e2e/test_e2e_full_validation.py --collect-only 2>&1 | grep "test_phase10"`
Expected: `test_phase10_soak` collected with `e2e_soak` and `slow` markers.

**Step 3: Commit**

```bash
git add tests/e2e/test_e2e_full_validation.py
git commit -m "test: add full validation E2E phase 10 — 4-hour soak

Rotating ArgoCD modes, periodic cross-validation with postflight,
JSONL results log, consecutive failure circuit breaker."
```

---

### Task 9: Add phase 11 (final validation)

**Files:**
- Modify: `tests/e2e/test_e2e_full_validation.py`

**Step 1: Append phase 11**

Add after `test_phase10_soak`:

```python
    # ── Phase 11: Final Validation ────────────────────────────────────

    def test_phase11_final_validation(self, require_cluster_contexts):
        """Final cross-validation after all switchovers and soak."""
        # Run even if soak was skipped, as long as phase 9 passed
        PhaseTracker.require("phase9")
        passed = False
        try:
            # Determine who is currently primary based on whether soak ran
            # After phase 9, self._primary is primary.
            # After soak (even cycles), it depends on cycle count — check live.
            primary_phase = get_backup_schedule_phase(self._primary)
            secondary_phase = get_backup_schedule_phase(self._secondary)

            if primary_phase == "Enabled":
                current_primary = self._primary
            elif secondary_phase == "Enabled":
                current_primary = self._secondary
            else:
                pytest.fail(
                    f"Neither hub has BackupSchedule Enabled: "
                    f"{self._primary}={primary_phase}, {self._secondary}={secondary_phase}"
                )

            # discover-hub
            contexts = f"{self._primary},{self._secondary}"
            result = run_shell_script(
                "discover-hub.sh",
                ["--contexts", contexts, "--verbose"],
                timeout=60,
            )
            result.assert_success("Final discover-hub failed")
            logger.info("Final discover-hub:\n%s", result.stdout[:3000])

            # postflight on whoever is primary
            result = run_shell_script(
                "postflight-check.sh",
                ["--context", current_primary],
                timeout=120,
            )
            logger.info("Final postflight exit=%d\n%s", result.returncode, result.stdout[:2000])

            # check_rbac on both
            for ctx in [self._primary, self._secondary]:
                result = run_python_tool("check_rbac.py", ["--context", ctx])
                logger.info("Final check_rbac %s exit=%d", ctx, result.returncode)

            # Managed cluster count sanity
            for ctx in [self._primary, self._secondary]:
                count = get_managed_cluster_count(ctx)
                logger.info("Final MC count on %s: %d", ctx, count)

            logger.info("=== Full Validation Suite PASSED ===")
            passed = True
        finally:
            PhaseTracker.mark("phase11", passed)
```

**Step 2: Verify full collection**

Run: `source .venv/bin/activate && pytest tests/e2e/test_e2e_full_validation.py --collect-only 2>&1 | grep "test_phase"`
Expected: 12 test methods listed (phase0 through phase11).

**Step 3: Commit**

```bash
git add tests/e2e/test_e2e_full_validation.py
git commit -m "test: add full validation E2E phase 11 — final validation

Cross-validates both hubs with discover-hub, postflight, check_rbac,
and managed cluster counts after all switchover phases."
```

---

### Task 10: Update design doc and run full collection smoke test

**Files:**
- Modify: `docs/plans/2026-03-31-full-validation-e2e-design.md` (add status: implemented)

**Step 1: Run the full test collection to verify everything wires up**

Run: `source .venv/bin/activate && pytest tests/e2e/test_e2e_full_validation.py --collect-only -v`
Expected: 12 tests collected, no import errors, markers recognized.

**Step 2: Run the existing unit tests to verify no regressions**

Run: `source .venv/bin/activate && pytest tests/ -v -m "not e2e" --timeout=120 2>&1 | tail -20`
Expected: All existing tests pass.

**Step 3: Final commit**

```bash
git add -A
git commit -m "test: complete full validation E2E suite implementation

12-phase test suite covering baseline cleanup, shell script validation,
validate-only, dry-run (3 ArgoCD modes × 2 directions), 3 real
switchovers with ArgoCD variants, reverse switchover, 4-hour soak,
and final cross-validation.

Ref: docs/plans/2026-03-31-full-validation-e2e-design.md"
```
