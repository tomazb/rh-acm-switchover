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

import json  # noqa: F401 — used by later phases appended to this file
import logging
import time
from pathlib import Path
from typing import ClassVar

import pytest

from tests.e2e.full_validation_helpers import get_argocd_apps  # noqa: F401
from tests.e2e.full_validation_helpers import (
    PhaseTracker,
    assert_hub_is_primary,
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
            secondary = self._secondary

            # Delete colliding BackupSchedule if in BackupCollision state
            phase = get_backup_schedule_phase(secondary)
            if phase == "BackupCollision":
                logger.info("Cleaning up BackupCollision on %s", secondary)
                kubectl(
                    secondary,
                    "delete",
                    "backupschedule",
                    "acm-hub-backup",
                    "-n",
                    "open-cluster-management-backup",
                    "--ignore-not-found",
                )
                time.sleep(5)

            # Verify primary is healthy
            assert_hub_is_primary(self._primary)

            # Verify secondary has a passive-sync restore or is clean
            mc_count = get_managed_cluster_count(secondary)
            logger.info(
                "Baseline: primary=%s (primary), secondary=%s (%d MCs)",
                self._primary,
                secondary,
                mc_count,
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
            logger.info(
                "preflight exit=%d\n%s", result.returncode, result.stdout[:2000]
            )

            # argocd-check via Python tool
            result = run_switchover(
                self._primary,
                self._secondary,
                extra_args=["--argocd-check"],
                timeout=120,
            )
            logger.info(
                "argocd-check exit=%d\n%s", result.returncode, result.output[:2000]
            )

            # check_rbac.py
            result = run_python_tool(
                "check_rbac.py",
                ["--context", self._primary],
                timeout=60,
            )
            logger.info(
                "check_rbac exit=%d\n%s", result.returncode, result.output[:1000]
            )

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
                self._primary,
                self._secondary,
                extra_args=["--validate-only"],
                timeout=120,
            )
            result.assert_success("validate-only (forward) failed")
            logger.info("validate-only forward OK")

            # Reverse: secondary → primary
            result = run_switchover(
                self._secondary,
                self._primary,
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
        """Run --dry-run for both directions x 3 ArgoCD modes."""
        PhaseTracker.require("phase0")
        passed = False
        try:
            argocd_variants = [
                ("no-argocd", []),
                ("argocd-pause", ["--argocd-manage"]),
                (
                    "argocd-full",
                    ["--argocd-manage", "--argocd-resume-after-switchover"],
                ),
            ]

            for label, argocd_args in argocd_variants:
                for direction, (pri, sec) in [
                    ("fwd", (self._primary, self._secondary)),
                    ("rev", (self._secondary, self._primary)),
                ]:
                    state = self._state_file(f"dryrun-{label}-{direction}")
                    result = run_switchover(
                        pri,
                        sec,
                        extra_args=["--dry-run", "--state-file", state] + argocd_args,
                        timeout=180,
                    )
                    result.assert_success(f"dry-run {label} {direction} failed")
                    logger.info("dry-run %s %s OK", label, direction)

            passed = True
        finally:
            PhaseTracker.mark("phase3", passed)

    # ── Phase 4: Real Switchover — No ArgoCD ──────────────────────────

    def test_phase4_switchover_no_argocd(self, require_cluster_contexts):
        """Real switchover primary->secondary without Argo CD management."""
        PhaseTracker.require("phase2")
        passed = False
        try:
            state = self._state_file("phase4-no-argocd")
            result = run_switchover(
                self._primary,
                self._secondary,
                extra_args=["--state-file", state],
                timeout=900,
            )
            result.assert_success("Phase 4 switchover failed")

            # After switchover: secondary becomes new primary
            wait_and_assert_hub_is_primary(self._secondary, timeout=300)

            # Verify state file shows COMPLETED
            with open(state) as f:
                st = json.load(f)
            assert (
                st.get("current_phase") == "COMPLETED"
            ), f"Expected COMPLETED, got {st.get('current_phase')}"

            logger.info(
                "Phase 4 switchover %s->%s completed",
                self._primary,
                self._secondary,
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
            logger.info(
                "postflight exit=%d\n%s",
                result.returncode,
                result.stdout[:2000],
            )

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
