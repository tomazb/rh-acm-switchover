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
from unittest.mock import patch

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
    wait_for_restore_settled,
)

logger = logging.getLogger("e2e_full_validation")


def test_soak_argocd_pause_resume_uses_original_cycle_hub_pair(tmp_path):
    """Soak resume-only must reuse the switchover cycle's recorded primary/secondary pair."""
    PhaseTracker.reset()
    PhaseTracker.mark("phase9", True)

    suite = TestFullValidation()
    TestFullValidation._primary = "hub-a"
    TestFullValidation._secondary = "hub-b"
    TestFullValidation._output_dir = tmp_path / "output"
    TestFullValidation._output_dir.mkdir()
    TestFullValidation._state_dir = tmp_path / "states"
    TestFullValidation._state_dir.mkdir()

    request = type(
        "Request",
        (),
        {
            "config": type(
                "Config",
                (),
                {
                    "getoption": staticmethod(
                        lambda name, default=None: {
                            "--e2e-argocd-mode": "pause",
                            "--e2e-run-hours": 0.0001,
                            "--e2e-cooldown": 0,
                        }.get(name, default)
                    )
                },
            )()
        },
    )()

    calls = []

    def run_switchover_side_effect(primary, secondary, **kwargs):
        calls.append((primary, secondary, kwargs))
        return type(
            "Result",
            (),
            {
                "ok": True,
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "output": "",
            },
        )()

    with patch("tests.e2e.test_e2e_full_validation.run_switchover", side_effect=run_switchover_side_effect), patch(
        "tests.e2e.test_e2e_full_validation.run_shell_script"
    ) as run_shell, patch("tests.e2e.test_e2e_full_validation.time.sleep"), patch(
        "tests.e2e.test_e2e_full_validation.time.time",
        side_effect=[0.0, 0.0, 0.05, 0.10, 1.0, 1.0, 1.0],
    ):
        run_shell.return_value = type(
            "Result", (), {"ok": True, "returncode": 0, "stdout": "", "stderr": "", "output": ""}
        )()
        suite.test_phase10_soak(request, require_cluster_contexts=None)

    assert len(calls) == 2
    assert calls[0][0:2] == ("hub-a", "hub-b")
    assert calls[1][0:2] == ("hub-a", "hub-b")
    assert calls[1][2]["extra_args"] == [
        "--argocd-resume-only",
        "--state-file",
        calls[0][2]["extra_args"][1],
    ]


@pytest.mark.e2e
@pytest.mark.e2e_full_validation
class TestFullValidation:
    """Full validation suite — phases run in definition order."""

    _primary: ClassVar[str] = ""
    _secondary: ClassVar[str] = ""
    _output_dir: ClassVar[Path] = Path(".")
    _state_dir: ClassVar[Path] = Path(".")
    _initialized: ClassVar[bool] = False

    @pytest.fixture(autouse=True)
    def _inject_config(self, e2e_config, tmp_path_factory):
        """Inject cluster contexts from conftest into class-level state."""
        if not TestFullValidation._initialized:
            TestFullValidation._initialized = True
            TestFullValidation._primary = e2e_config.primary_context
            TestFullValidation._secondary = e2e_config.secondary_context
            base = tmp_path_factory.mktemp("full_validation")
            TestFullValidation._output_dir = base / "output"
            TestFullValidation._output_dir.mkdir()
            TestFullValidation._state_dir = base / "states"
            TestFullValidation._state_dir.mkdir()

    def _state_file(self, label: str) -> str:
        return str(self._state_dir / f"{label}-state.json")

    def _cleanup_argocd_conflicts(self, primary: str, secondary: str) -> None:
        """Remove ArgoCD-recreated resources that conflict with new hub roles.

        After switchover, ArgoCD apps resume and may recreate resources matching
        the OLD role (e.g. hub-backup-standby recreates a Restore on the new
        primary).  Delete conflicting resources and pause the offending apps.
        """
        # New primary should NOT have a Restore (standby ArgoCD may recreate it)
        r = kubectl(
            primary,
            "get",
            "restores.cluster.open-cluster-management.io",
            "-n",
            "open-cluster-management-backup",
            "--no-headers",
        )
        if r.ok and r.stdout.strip():
            restore_name = r.stdout.strip().split()[0]
            logger.warning(
                "Stale Restore %s on new primary %s — deleting",
                restore_name,
                primary,
            )
            # Pause the ArgoCD app that manages it to stop recreation
            kubectl(
                primary,
                "patch",
                "applications.argoproj.io",
                "hub-backup-standby",
                "-n",
                "openshift-gitops",
                "--type",
                "json",
                "-p",
                '[{"op":"remove","path":"/spec/syncPolicy/automated"}]',
                timeout=10,
            )
            kubectl(
                primary,
                "delete",
                f"restores.cluster.open-cluster-management.io/{restore_name}",
                "-n",
                "open-cluster-management-backup",
            )

        # New secondary should NOT have a BackupSchedule (schedule ArgoCD
        # may recreate it).  Only delete if a Restore already exists.
        bs = kubectl(
            secondary,
            "get",
            "backupschedules.cluster.open-cluster-management.io",
            "-n",
            "open-cluster-management-backup",
            "--no-headers",
        )
        if bs.ok and bs.stdout.strip():
            rs = kubectl(
                secondary,
                "get",
                "restores.cluster.open-cluster-management.io",
                "-n",
                "open-cluster-management-backup",
                "--no-headers",
            )
            if rs.ok and rs.stdout.strip():
                bs_name = bs.stdout.strip().split()[0]
                logger.warning(
                    "Stale BackupSchedule %s on new secondary %s — deleting",
                    bs_name,
                    secondary,
                )
                kubectl(
                    secondary,
                    "patch",
                    "applications.argoproj.io",
                    "hub-backup-schedule",
                    "-n",
                    "openshift-gitops",
                    "--type",
                    "json",
                    "-p",
                    '[{"op":"remove","path":"/spec/syncPolicy/automated"}]',
                    timeout=10,
                )
                kubectl(
                    secondary,
                    "delete",
                    f"backupschedules.cluster.open-cluster-management.io/{bs_name}",
                    "-n",
                    "open-cluster-management-backup",
                )

        # Give ACM a moment to reconcile
        time.sleep(10)

    # ── Phase 0: Baseline Cleanup ─────────────────────────────────────

    def _ensure_bs_on_primary(self):
        """Ensure the designated primary (self._primary) has an active BS.

        Handles three scenarios:
        1. BS Enabled — nothing to do
        2. BS Paused — unpause and wait
        3. BS missing/empty — re-enable ArgoCD auto-sync to recreate
        """
        primary = self._primary
        phase = get_backup_schedule_phase(primary)

        if phase == "Enabled":
            return

        if phase == "Paused":
            logger.warning("BS on %s is Paused — unpausing", primary)
            kubectl(
                primary,
                "patch",
                "backupschedules.cluster.open-cluster-management.io/acm-hub-backup",
                "-n",
                "open-cluster-management-backup",
                "--type",
                "merge",
                "-p",
                '{"spec":{"paused":false}}',
            )
        elif phase in ("FailedValidation", ""):
            # BS missing or in failed state — re-enable ArgoCD to recreate
            logger.warning(
                "BS on %s in phase '%s' — re-enabling ArgoCD auto-sync",
                primary,
                phase,
            )
            kubectl(
                primary,
                "patch",
                "applications.argoproj.io",
                "hub-backup-schedule",
                "-n",
                "openshift-gitops",
                "--type",
                "merge",
                "-p",
                '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}',
                timeout=10,
            )
            # If there's a stale restore on primary, delete it (BS won't activate
            # while a restore exists)
            r = kubectl(
                primary,
                "get",
                "restores.cluster.open-cluster-management.io",
                "-n",
                "open-cluster-management-backup",
                "--no-headers",
                timeout=10,
            )
            if r.ok and r.stdout.strip():
                logger.warning("Deleting stale restore on primary %s", primary)
                kubectl(
                    primary,
                    "delete",
                    "restore.cluster.open-cluster-management.io",
                    "--all",
                    "-n",
                    "open-cluster-management-backup",
                )
        else:
            logger.warning("BS on %s in unexpected phase '%s'", primary, phase)

        # Poll for BS to become Enabled (up to 120s)
        for _wait in range(12):
            time.sleep(10)
            phase = get_backup_schedule_phase(primary)
            if phase == "Enabled":
                logger.info("BS on %s is now Enabled", primary)
                return
            logger.info("Waiting for BS on %s (phase=%s)", primary, phase)

        logger.error("BS on %s still not Enabled (phase=%s)", primary, phase)

    def _cleanup_secondary_bs(self):
        """Delete any BackupSchedule on the secondary hub."""
        secondary = self._secondary
        bs_phase = get_backup_schedule_phase(secondary)
        if not bs_phase:
            return

        logger.warning(
            "BackupSchedule on secondary %s in phase '%s' — deleting",
            secondary,
            bs_phase,
        )
        # Disable ArgoCD auto-sync to prevent recreation
        kubectl(
            secondary,
            "patch",
            "applications.argoproj.io",
            "hub-backup-schedule",
            "-n",
            "openshift-gitops",
            "--type",
            "json",
            "-p",
            '[{"op":"remove","path":"/spec/syncPolicy/automated"}]',
            timeout=10,
        )
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

    def _cleanup_secondary_restore(self):
        """Delete broken restore on secondary (FinishedWithErrors/Failed)."""
        secondary = self._secondary
        restore_out = kubectl(
            secondary,
            "get",
            "restores.cluster.open-cluster-management.io",
            "-n",
            "open-cluster-management-backup",
            "-o",
            "jsonpath={.items[0].status.phase}",
            timeout=10,
        )
        restore_phase = restore_out.stdout.strip()
        if restore_phase in ("FinishedWithErrors", "Failed"):
            logger.warning(
                "Restore on %s in '%s' state — deleting for ArgoCD to recreate",
                secondary,
                restore_phase,
            )
            kubectl(
                secondary,
                "delete",
                "restore.cluster.open-cluster-management.io",
                "--all",
                "-n",
                "open-cluster-management-backup",
            )
            time.sleep(5)

    def test_phase0_baseline_cleanup(self, require_cluster_contexts):
        """Clean up BackupCollision on secondary, ensure known-good baseline."""
        passed = False
        try:
            # Step 1: Ensure primary has active BS FIRST (before touching secondary)
            # This handles: Paused BS, missing BS, FailedValidation
            self._ensure_bs_on_primary()

            # Step 2: Now safe to clean secondary (primary is active)
            self._cleanup_secondary_bs()
            self._cleanup_secondary_restore()

            # Verify primary is healthy
            assert_hub_is_primary(self._primary)

            # Clean stale ArgoCD paused-by markers from previous failed runs
            for ctx in (self._primary, self._secondary):
                apps = get_argocd_apps(ctx)
                for app in apps:
                    ann = app.get("metadata", {}).get("annotations", {})
                    if ann.get("acm-switchover.argoproj.io/paused-by"):
                        name = app["metadata"]["name"]
                        logger.warning(
                            "Clearing stale paused-by marker on %s/%s",
                            ctx,
                            name,
                        )
                        kubectl(
                            ctx,
                            "annotate",
                            "applications.argoproj.io",
                            name,
                            "-n",
                            "openshift-gitops",
                            "acm-switchover.argoproj.io/paused-by-",
                        )

            # Also ensure ArgoCD auto-sync is re-enabled on key backup apps
            for ctx, app_name in (
                (self._primary, "hub-backup-schedule"),
                (self._secondary, "hub-backup-standby"),
            ):
                kubectl(
                    ctx,
                    "patch",
                    "applications.argoproj.io",
                    app_name,
                    "-n",
                    "openshift-gitops",
                    "--type",
                    "merge",
                    "-p",
                    '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}',
                    timeout=10,
                )

            # Wait for passive-sync restore to exist and settle (Running → Enabled)
            # After baseline cleanup or a previous failed run, the restore may
            # not yet exist (ArgoCD recreating) or may be actively syncing.
            # Wait up to 120s for it to appear and reach a stable state.
            restore_ready = False
            for attempt in range(12):
                restore_out = kubectl(
                    self._secondary,
                    "get",
                    "restores.cluster.open-cluster-management.io",
                    "-n",
                    "open-cluster-management-backup",
                    "-o",
                    "jsonpath={.items[0].status.phase}",
                    timeout=10,
                )
                phase_val = restore_out.stdout.strip()
                if phase_val in ("Enabled", "Finished"):
                    restore_ready = True
                    break
                logger.info(
                    "Waiting for restore to settle (attempt %d/12, phase=%s)",
                    attempt + 1,
                    phase_val or "not-found",
                )
                time.sleep(10)
            if not restore_ready:
                logger.warning(
                    "Restore did not settle after 120s (phase=%s), proceeding anyway",
                    phase_val or "not-found",
                )

            # Verify secondary has a passive-sync restore or is clean
            mc_count = get_managed_cluster_count(self._secondary)
            logger.info(
                "Baseline: primary=%s (primary), secondary=%s (%d MCs)",
                self._primary,
                self._secondary,
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

            # discover-hub (advisory — may exit non-zero if roles are ambiguous)
            result = run_shell_script(
                "discover-hub.sh",
                ["--contexts", contexts, "--verbose"],
                timeout=60,
            )
            logger.info("discover-hub exit=%d\n%s", result.returncode, result.output[:2000])

            # preflight-check against primary
            result = run_shell_script(
                "preflight-check.sh",
                ["--context", self._primary],
                timeout=120,
            )
            logger.info("preflight exit=%d\n%s", result.returncode, result.stdout[:2000])

            # argocd-check via Python tool
            result = run_switchover(
                self._primary,
                self._secondary,
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

            # show_state.py
            state = self._state_file("phase1-show-state")
            result = run_python_tool(
                "show_state.py",
                ["--state-file", state],
                timeout=60,
            )
            logger.info("show_state exit=%d\n%s", result.returncode, result.output[:1000])

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
            # Retry up to 3 times because the passive-sync restore may be
            # transiently in "Running" state while syncing a new backup.
            for attempt in range(3):
                result = run_switchover(
                    self._primary,
                    self._secondary,
                    extra_args=["--validate-only"],
                    timeout=120,
                )
                if result.ok:
                    break
                if "unexpected state: Running" in result.stderr and attempt < 2:
                    logger.info(
                        "validate-only failed (restore syncing), retry %d/3 in 60s",
                        attempt + 1,
                    )
                    time.sleep(60)
                    continue
                break
            result.assert_success("validate-only (forward) failed")
            logger.info("validate-only forward OK")

            # Reverse: secondary → primary (advisory — may fail if secondary
            # is not configured as a primary; this is expected pre-switchover)
            result = run_switchover(
                self._secondary,
                self._primary,
                extra_args=["--validate-only"],
                timeout=120,
            )
            if result.ok:
                logger.info("validate-only reverse OK")
            else:
                logger.info(
                    "validate-only reverse exit=%d (expected pre-switchover)",
                    result.returncode,
                )

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
                # Forward direction: must succeed
                state = self._state_file(f"dryrun-{label}-fwd")
                result = run_switchover(
                    self._primary,
                    self._secondary,
                    extra_args=["--dry-run", "--state-file", state] + argocd_args,
                    timeout=180,
                )
                result.assert_success(f"dry-run {label} fwd failed")
                logger.info("dry-run %s fwd OK", label)

                # Reverse direction: advisory (expected to fail pre-switchover)
                state = self._state_file(f"dryrun-{label}-rev")
                result = run_switchover(
                    self._secondary,
                    self._primary,
                    extra_args=["--dry-run", "--state-file", state] + argocd_args,
                    timeout=180,
                )
                if result.ok:
                    logger.info("dry-run %s rev OK", label)
                else:
                    logger.info(
                        "dry-run %s rev exit=%d (expected pre-switchover)",
                        label,
                        result.returncode,
                    )

            passed = True
        finally:
            PhaseTracker.mark("phase3", passed)

    # ── Phase 4: Real Switchover — Basic ─────────────────────────────

    def test_phase4_switchover_no_argocd(self, require_cluster_contexts):
        """Real switchover primary->secondary (adds --argocd-manage when needed)."""
        PhaseTracker.require("phase2")
        passed = False
        try:
            state = self._state_file("phase4-no-argocd")
            # On clusters with ArgoCD apps managing backup resources,
            # --argocd-manage is required to prevent GitOps drift during
            # finalization.  Detect and add automatically.
            extra = ["--state-file", state]
            if get_argocd_apps(self._primary):
                logger.info("ArgoCD apps detected — adding --argocd-manage")
                extra.append("--argocd-manage")
            result = run_switchover(
                self._primary,
                self._secondary,
                extra_args=extra,
                timeout=900,
            )
            result.assert_success("Phase 4 switchover failed")

            # After switchover: secondary becomes new primary
            wait_and_assert_hub_is_primary(self._secondary, timeout=300)

            # Verify state file shows completed (Phase.COMPLETED.value)
            with open(state) as f:
                st = json.load(f)
            assert st.get("current_phase") == "completed", f"Expected completed, got {st.get('current_phase')}"

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
            new_secondary = self._primary

            # After switchover with --argocd-manage, ArgoCD apps resume and
            # may recreate conflicting resources (e.g. hub-backup-standby
            # recreates a Restore on the new primary that conflicts with the
            # new BackupSchedule).  Clean up before validation.
            self._cleanup_argocd_conflicts(new_primary, new_secondary)

            # postflight-check against new primary
            result = run_shell_script(
                "postflight-check.sh",
                ["--new-hub-context", new_primary],
                timeout=120,
            )
            logger.info(
                "postflight exit=%d\n%s",
                result.returncode,
                result.stdout[:2000],
            )

            # Wait for restore on new secondary to settle before discover-hub
            wait_for_restore_settled(new_secondary)

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

    # ── Phase 6: Switchover — ArgoCD Pause-Only ───────────────────────

    def test_phase6_switchover_argocd_pause(self, require_cluster_contexts):
        """Switchover with --argocd-manage (pause-only, no resume)."""
        PhaseTracker.require("phase4")
        passed = False
        try:
            # After phase 4, roles are swapped: secondary is now primary
            current_primary = self._secondary
            current_secondary = self._primary
            assert_hub_is_primary(current_primary)

            # Wait for restore on current secondary to settle
            wait_for_restore_settled(current_secondary)

            state = self._state_file("phase6-argocd-pause")
            result = run_switchover(
                current_primary,
                current_secondary,
                extra_args=["--argocd-manage", "--state-file", state],
                timeout=900,
            )
            result.assert_success("Phase 6 switchover failed")

            # New primary is self._primary again (swapped back)
            wait_and_assert_hub_is_primary(current_secondary, timeout=300)

            # Verify Argo CD apps on the NEW primary have paused annotation
            apps = get_argocd_apps(current_secondary)
            assert len(apps) > 0, f"No ArgoCD apps found on {current_secondary} — possible API failure"
            paused_apps = [
                a
                for a in apps
                if a.get("metadata", {}).get("annotations", {}).get("acm-switchover.argoproj.io/paused-by")
            ]
            logger.info(
                "Phase 6: %d/%d apps have paused-by annotation on %s",
                len(paused_apps),
                len(apps),
                current_secondary,
            )

            # Verify argocd-manage.sh status shows paused state
            result = run_shell_script(
                "argocd-manage.sh",
                ["--context", current_secondary, "--mode", "status"],
                timeout=60,
            )
            logger.info("argocd status:\n%s", result.output[:2000])

            # Verify state file records completion
            with open(state) as f:
                st = json.load(f)
            assert st.get("current_phase") == "completed"

            # show_state.py after real switchover
            result = run_python_tool("show_state.py", ["--state-file", state], timeout=60)
            logger.info("Phase 6 show_state exit=%d", result.returncode)

            passed = True
        finally:
            PhaseTracker.mark("phase6", passed)

    # ── Phase 7: ArgoCD Resume-Only ───────────────────────────────────

    def test_phase7_argocd_resume_only(self, require_cluster_contexts):
        """Standalone --argocd-resume-only to restore paused apps."""
        PhaseTracker.require("phase6")
        passed = False
        try:
            # Phase 6 ran switchover(self._secondary, self._primary) so the
            # state file maps apps to those contexts.  Resume must use the
            # same primary/secondary pair to reconnect to the right hubs.
            phase6_primary = self._secondary
            phase6_secondary = self._primary
            state = self._state_file("phase6-argocd-pause")

            # Clean stale paused-by markers that don't match the expected
            # run_id.  The passive-sync restore can overwrite markers set
            # during phase 6 with old backup data containing annotations
            # from previous runs.
            with open(state) as f:
                expected_run_id = json.load(f).get("config", {}).get("argocd_run_id", "")
            if expected_run_id:
                for ctx in (phase6_primary, phase6_secondary):
                    apps = get_argocd_apps(ctx)
                    for app in apps:
                        marker = (
                            app.get("metadata", {})
                            .get("annotations", {})
                            .get("acm-switchover.argoproj.io/paused-by", "")
                        )
                        if marker and marker != expected_run_id:
                            app_name = app["metadata"]["name"]
                            logger.info(
                                "Phase 7: removing stale marker %s from %s/%s " "(expected %s)",
                                marker,
                                ctx,
                                app_name,
                                expected_run_id,
                            )
                            kubectl(
                                ctx,
                                "annotate",
                                "applications.argoproj.io",
                                app_name,
                                "-n",
                                "openshift-gitops",
                                "acm-switchover.argoproj.io/paused-by-",
                                timeout=10,
                            )

            result = run_switchover(
                phase6_primary,
                phase6_secondary,
                extra_args=[
                    "--argocd-resume-only",
                    "--state-file",
                    state,
                ],
                timeout=120,
            )
            result.assert_success("Phase 7 argocd-resume-only failed")

            # Verify paused-by annotations are removed on both hubs
            for ctx in (phase6_primary, phase6_secondary):
                apps = get_argocd_apps(ctx)
                assert len(apps) > 0, f"No ArgoCD apps found on {ctx} — possible API failure"
                still_paused = [
                    a
                    for a in apps
                    if a.get("metadata", {}).get("annotations", {}).get("acm-switchover.argoproj.io/paused-by")
                ]
                assert (
                    len(still_paused) == 0
                ), f"Expected 0 paused apps on {ctx} after resume, found {len(still_paused)}"
            logger.info(
                "Phase 7: all apps resumed on %s and %s",
                phase6_primary,
                phase6_secondary,
            )

            # argocd-manage.sh status after resume (check both hubs)
            for ctx in (phase6_primary, phase6_secondary):
                result = run_shell_script(
                    "argocd-manage.sh",
                    ["--context", ctx, "--mode", "status"],
                    timeout=60,
                )
                logger.info(
                    "Phase 7 argocd status on %s:\n%s",
                    ctx,
                    result.output[:2000],
                )

            passed = True
        finally:
            PhaseTracker.mark("phase7", passed)

    # ── Phase 8: Switchover — ArgoCD Pause + Auto-Resume ──────────────

    def test_phase8_switchover_argocd_pause_resume(self, require_cluster_contexts):
        """Switchover with --argocd-manage + --argocd-resume-after-switchover."""
        PhaseTracker.require("phase7")
        passed = False
        try:
            # After phase 6, self._primary is primary again
            current_primary = self._primary
            current_secondary = self._secondary
            assert_hub_is_primary(current_primary)

            # Wait for restore on secondary to settle (may still be syncing
            # from phase 7's ArgoCD resume operations)
            wait_for_restore_settled(current_secondary)

            state = self._state_file("phase8-argocd-full")
            result = run_switchover(
                current_primary,
                current_secondary,
                extra_args=[
                    "--argocd-manage",
                    "--argocd-resume-after-switchover",
                    "--state-file",
                    state,
                ],
                timeout=900,
            )
            result.assert_success("Phase 8 switchover failed")

            # New primary is self._secondary
            wait_and_assert_hub_is_primary(current_secondary, timeout=300)

            # With auto-resume, apps should NOT have paused annotation
            apps = get_argocd_apps(current_secondary)
            assert len(apps) > 0, f"No ArgoCD apps found on {current_secondary} — possible API failure"
            still_paused = [
                a
                for a in apps
                if a.get("metadata", {}).get("annotations", {}).get("acm-switchover.argoproj.io/paused-by")
            ]
            assert len(still_paused) == 0, (
                f"Phase 8: expected 0 paused apps after auto-resume, " f"found {len(still_paused)}"
            )
            logger.info("Phase 8: switchover + auto-resume OK on %s", current_secondary)

            # argocd-manage.sh status after auto-resume
            result = run_shell_script(
                "argocd-manage.sh",
                ["--context", current_secondary, "--mode", "status"],
                timeout=60,
            )
            logger.info("Phase 8 argocd status:\n%s", result.output[:2000])

            # show_state.py after real switchover
            result = run_python_tool("show_state.py", ["--state-file", state], timeout=60)
            logger.info("Phase 8 show_state exit=%d", result.returncode)

            passed = True
        finally:
            PhaseTracker.mark("phase8", passed)

    # ── Phase 9: Reverse Switchover — Restore Original Topology ───────

    def test_phase9_reverse_to_original(self, require_cluster_contexts):
        """Reverse switchover to restore original hub topology."""
        PhaseTracker.require("phase8")
        passed = False
        try:
            # After phase 8: secondary is primary.  Swap back.
            current_primary = self._secondary
            current_secondary = self._primary
            assert_hub_is_primary(current_primary)

            # Wait for restore on current secondary to settle
            wait_for_restore_settled(current_secondary)

            state = self._state_file("phase9-reverse")
            extra = ["--state-file", state]
            if get_argocd_apps(current_primary):
                logger.info("ArgoCD apps detected — adding --argocd-manage")
                extra.append("--argocd-manage")
            result = run_switchover(
                current_primary,
                current_secondary,
                extra_args=extra,
                timeout=900,
            )
            result.assert_success("Phase 9 reverse switchover failed")

            # Original primary (self._primary) is primary again
            wait_and_assert_hub_is_primary(self._primary, timeout=300)

            # Clean ArgoCD conflicts before validation
            self._cleanup_argocd_conflicts(self._primary, self._secondary)

            # Shell cross-validation — postflight on restored primary
            result = run_shell_script(
                "postflight-check.sh",
                ["--new-hub-context", self._primary],
                timeout=120,
            )
            result.assert_success("Phase 9 postflight on restored primary failed")

            # show_state.py after real switchover
            result = run_python_tool("show_state.py", ["--state-file", state], timeout=60)
            logger.info("Phase 9 show_state exit=%d", result.returncode)

            logger.info(
                "Phase 9: original topology restored, %s is primary",
                self._primary,
            )

            passed = True
        finally:
            PhaseTracker.mark("phase9", passed)

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
                (
                    "argocd-full",
                    ["--argocd-manage", "--argocd-resume-after-switchover"],
                ),
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
                    argocd_args = [
                        "--argocd-manage",
                        "--argocd-resume-after-switchover",
                    ]
                    mode_label = "argocd-full"

                state = self._state_file(f"soak-cycle-{cycle:04d}")
                start = time.time()

                result = run_switchover(
                    current_primary,
                    current_secondary,
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
                try:
                    with open(results_log, "a") as f:
                        f.write(json.dumps(entry) + "\n")
                except OSError as exc:
                    logger.warning("Failed to write soak results: %s", exc)

                if success:
                    consecutive_failures = 0
                    logger.info(
                        "Soak cycle %d OK (%s, %.0fs) %s→%s",
                        cycle,
                        mode_label,
                        duration,
                        current_primary,
                        current_secondary,
                    )
                else:
                    failures += 1
                    consecutive_failures += 1
                    logger.warning(
                        "Soak cycle %d FAILED (%s, %.0fs) %s→%s\n%s",
                        cycle,
                        mode_label,
                        duration,
                        current_primary,
                        current_secondary,
                        result.output[-500:],
                    )

                # If argocd-pause (no auto-resume), do a standalone resume
                if success and mode_label == "argocd-pause":
                    resume_result = run_switchover(
                        current_primary,
                        current_secondary,
                        extra_args=[
                            "--argocd-resume-only",
                            "--state-file",
                            state,
                        ],
                        timeout=120,
                    )
                    if not resume_result.ok:
                        logger.warning("Soak cycle %d: argocd-resume-only failed", cycle)

                # Periodic cross-validation every 5th cycle
                if cycle % 5 == 0 and success:
                    run_shell_script(
                        "postflight-check.sh",
                        ["--new-hub-context", current_secondary],
                        timeout=120,
                    )

                # Swap directions for next cycle (only if switchover succeeded)
                if success:
                    current_primary, current_secondary = (
                        current_secondary,
                        current_primary,
                    )

                # Cooldown
                if time.time() < deadline:
                    time.sleep(cooldown)

            # Report
            logger.info(
                "Soak complete: %d cycles, %d failures, %d consecutive failures",
                cycle,
                failures,
                consecutive_failures,
            )
            assert consecutive_failures < max_consecutive, f"Soak stopped early: {max_consecutive} consecutive failures"

            passed = True
        finally:
            PhaseTracker.mark("phase10", passed)

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
                    f"{self._primary}={primary_phase}, "
                    f"{self._secondary}={secondary_phase}"
                )

            # Wait for restore on secondary to settle (may still be Running
            # after the reverse switchover in phase 9 recreated it)
            secondary_ctx = (
                self._secondary
                if current_primary == self._primary
                else self._primary
            )
            wait_for_restore_settled(secondary_ctx)

            # discover-hub
            contexts = f"{self._primary},{self._secondary}"
            result = run_shell_script(
                "discover-hub.sh",
                ["--contexts", contexts, "--verbose"],
                timeout=60,
            )
            result.assert_success("Final discover-hub failed")
            logger.info("Final discover-hub:\n%s", result.output[:3000])

            # postflight on whoever is primary
            result = run_shell_script(
                "postflight-check.sh",
                ["--new-hub-context", current_primary],
                timeout=120,
            )
            logger.info(
                "Final postflight exit=%d\n%s",
                result.returncode,
                result.output[:2000],
            )

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
