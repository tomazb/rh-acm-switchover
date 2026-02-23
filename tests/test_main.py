"""Unit tests for acm_switchover.py (main script).

Tests argument parsing and basic entry point logic.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import _run_phase_preflight, main, parse_args, run_switchover
from lib.constants import EXIT_FAILURE, EXIT_INTERRUPT, EXIT_SUCCESS


@pytest.mark.unit
class TestArgParsing:
    """Tests for command line argument parsing."""

    def test_required_args(self):
        """Test that primary-context, old-hub-action, and method are required."""
        with patch("sys.argv", ["script.py"]):
            with pytest.raises(SystemExit):
                parse_args()

        # old-hub-action is also required
        with patch("sys.argv", ["script.py", "--primary-context", "p1", "--method", "passive"]):
            with pytest.raises(SystemExit):
                parse_args()

        # method is also required
        with patch(
            "sys.argv",
            ["script.py", "--primary-context", "p1", "--old-hub-action", "secondary"],
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_validate_only_mode(self):
        """Test parsing validate-only mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "secondary",
                "--method",
                "passive",
                "--validate-only",
            ],
        ):
            args = parse_args()
            assert args.validate_only is True
            assert args.dry_run is False
            assert args.primary_context == "p1"
            assert args.old_hub_action == "secondary"

    def test_dry_run_mode(self):
        """Test parsing dry-run mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "none",
                "--method",
                "passive",
                "--dry-run",
            ],
        ):
            args = parse_args()
            assert args.dry_run is True
            assert args.validate_only is False
            assert args.old_hub_action == "none"

    def test_decommission_mode(self):
        """Test parsing decommission mode."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "none",
                "--method",
                "passive",
                "--decommission",
            ],
        ):
            args = parse_args()
            assert args.decommission is True

    def test_mutually_exclusive_modes(self):
        """Test that mutually exclusive flags raise error."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "none",
                "--method",
                "passive",
                "--dry-run",
                "--validate-only",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_method_selection(self):
        """Test method selection argument."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "decommission",
                "--method",
                "full",
            ],
        ):
            args = parse_args()
            assert args.method == "full"
            assert args.old_hub_action == "decommission"

    def test_method_choices(self):
        """Test method only accepts valid choices."""
        # Valid choices
        for method in ["passive", "full"]:
            with patch(
                "sys.argv",
                [
                    "script.py",
                    "--primary-context",
                    "p1",
                    "--old-hub-action",
                    "secondary",
                    "--method",
                    method,
                ],
            ):
                args = parse_args()
                assert args.method == method

        # Invalid choice
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "secondary",
                "--method",
                "invalid",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_old_hub_action_choices(self):
        """Test old-hub-action only accepts valid choices."""
        # Valid choices
        for action in ["secondary", "decommission", "none"]:
            with patch(
                "sys.argv",
                [
                    "script.py",
                    "--primary-context",
                    "p1",
                    "--old-hub-action",
                    action,
                    "--method",
                    "passive",
                ],
            ):
                args = parse_args()
                assert args.old_hub_action == action

        # Invalid choice
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "invalid",
                "--method",
                "passive",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()


@pytest.mark.unit
class TestForceWithCompletedState:
    """Tests for --force flag behavior with completed state."""

    def test_force_resets_completed_stale_state_to_init(self, tmp_path):
        """Test that --force resets phase to INIT when state is stale COMPLETED.

        This verifies the fix for the issue where --force would silently no-op
        when state was already at COMPLETED because the phase loop skipped all
        handlers (COMPLETED is not in any allowed_phases tuple).
        """
        from lib.utils import Phase, StateManager

        # Create a stale state file (older than threshold)
        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))

        # Set to COMPLETED with stale timestamp (use _write_state to preserve timestamp)
        state.state["current_phase"] = Phase.COMPLETED.value
        from lib.constants import STALE_STATE_THRESHOLD

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_STATE_THRESHOLD + 1)
        state.state["last_updated"] = stale_time.isoformat()
        state._write_state(state.state)

        # Reload state to simulate fresh run
        state2 = StateManager(str(state_file))

        # Verify initial state is COMPLETED
        assert state2.get_current_phase() == Phase.COMPLETED

        # Check state age calculation
        state_age = datetime.now(timezone.utc) - datetime.fromisoformat(
            state2.state["last_updated"].replace("Z", "+00:00")
        )
        assert state_age.total_seconds() > STALE_STATE_THRESHOLD

        # Simulate what main() does with --force: reset to INIT
        state2.set_phase(Phase.INIT)

        # Verify phase is now INIT
        assert state2.get_current_phase() == Phase.INIT

    def test_force_flag_available_in_args(self):
        """Test that --force flag is properly parsed."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "secondary",
                "--method",
                "passive",
                "--force",
            ],
        ):
            args = parse_args()
            assert args.force is True

    def test_force_flag_defaults_to_false(self):
        """Test that force flag defaults to False when not specified."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--old-hub-action",
                "secondary",
                "--method",
                "passive",
            ],
        ):
            args = parse_args()
            assert args.force is False


@pytest.mark.unit
class TestCompletedStateTimestampHandling:
    def test_missing_last_updated_treated_as_stale_requires_force(self, tmp_path):
        from lib.constants import EXIT_FAILURE
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        state.state.pop("last_updated", None)
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with pytest.raises(SystemExit) as exc:
            run_switchover(args, reloaded, Mock(), Mock(), Mock())

        assert exc.value.code == EXIT_FAILURE

    def test_malformed_last_updated_treated_as_stale_requires_force(self, tmp_path):
        from lib.constants import EXIT_FAILURE
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        state.state["last_updated"] = "not-a-timestamp"
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with pytest.raises(SystemExit) as exc:
            run_switchover(args, reloaded, Mock(), Mock(), Mock())

        assert exc.value.code == EXIT_FAILURE

    def test_force_with_missing_last_updated_resets_state(self, tmp_path):
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        state.state.pop("last_updated", None)
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))

        args = SimpleNamespace(
            force=True,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True):
            result = run_switchover(args, reloaded, Mock(), Mock(), Mock())

        assert result is True
        assert reloaded.get_current_phase() == Phase.INIT

    def test_recent_completed_state_does_not_require_force(self, tmp_path):
        from lib.constants import STALE_STATE_THRESHOLD
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        state.state["last_updated"] = (
            datetime.now(timezone.utc) - timedelta(seconds=STALE_STATE_THRESHOLD - 1)
        ).isoformat()
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        assert run_switchover(args, reloaded, Mock(), Mock(), Mock()) is True


@pytest.mark.unit
class TestSwitchoverPhaseFlow:
    """Tests for the main switchover phase flow and operation routing."""

    def test_run_switchover_happy_path_starts_with_preflight_phase(self, tmp_path):
        """Verify that run_switchover starts by calling the preflight phase handler."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True) as preflight, patch(
            "acm_switchover._run_phase_primary_prep", return_value=True
        ) as primary_prep, patch("acm_switchover._run_phase_activation", return_value=True) as activation, patch(
            "acm_switchover._run_phase_post_activation", return_value=True
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization", return_value=True
        ) as finalization:
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        # Only the first phase handler is guaranteed to run in this setup
        preflight.assert_called_once()

    def test_run_switchover_resume_from_failed_state_retries_failed_phase(self, tmp_path):
        """Verify that run_switchover resumes from the phase that failed when state is FAILED."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        # Simulate a failure during POST_ACTIVATION
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("disable-auto-import annotation still present", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True) as preflight, patch(
            "acm_switchover._run_phase_primary_prep", return_value=True
        ) as primary_prep, patch("acm_switchover._run_phase_activation", return_value=True) as activation, patch(
            "acm_switchover._run_phase_post_activation", return_value=True
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization", return_value=True
        ) as finalization:
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        # Should NOT call preflight, primary_prep - those were already done
        preflight.assert_not_called()
        primary_prep.assert_not_called()
        activation.assert_not_called()
        # SHOULD call post_activation (the failed phase) and finalization
        post_activation.assert_called_once()
        finalization.assert_called_once()

    def test_run_switchover_failed_state_without_error_phase_requires_force(self, tmp_path):
        """Verify that FAILED state without determinable error phase requires --force."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.FAILED)
        # No errors recorded - cannot determine which phase failed

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with pytest.raises(SystemExit) as exc_info:
            run_switchover(args, state, Mock(), Mock(), Mock())

        assert exc_info.value.code == EXIT_FAILURE

    def test_run_switchover_failed_state_force_resets_and_retries(self, tmp_path):
        """Verify that --force with FAILED state and unknown error phase resets state."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.FAILED)
        # No errors recorded - cannot determine which phase failed

        args = SimpleNamespace(
            force=True,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True) as preflight, patch(
            "acm_switchover._run_phase_primary_prep", return_value=True
        ), patch("acm_switchover._run_phase_activation", return_value=True), patch(
            "acm_switchover._run_phase_post_activation", return_value=True
        ), patch(
            "acm_switchover._run_phase_finalization", return_value=True
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        # Should start from the beginning after reset
        preflight.assert_called_once()

    def test_execute_operation_routes_to_decommission_when_flag_set(self):
        """_execute_operation should call run_decommission when --decommission is set."""
        from acm_switchover import _execute_operation

        args = SimpleNamespace(decommission=True)
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.run_decommission", return_value=True) as run_dec:
            result = _execute_operation(args, state, primary, secondary, logger)

        assert result is True
        run_dec.assert_called_once_with(args, primary, state, logger)

    def test_execute_operation_requires_secondary_for_switchover(self):
        """_execute_operation should raise when secondary client is missing."""
        from acm_switchover import _execute_operation

        args = SimpleNamespace(decommission=False)
        with pytest.raises(ValueError):
            _execute_operation(args, Mock(), Mock(), None, Mock())

    def test_execute_operation_calls_run_switchover_for_normal_flow(self):
        """_execute_operation should delegate to run_switchover when decommission is False."""
        from acm_switchover import _execute_operation

        args = SimpleNamespace(decommission=False)
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.run_switchover", return_value=True) as run_sw:
            result = _execute_operation(args, state, primary, secondary, logger)

        assert result is True
        run_sw.assert_called_once_with(args, state, primary, secondary, logger)


@pytest.mark.unit
class TestMainGitOpsReporting:
    @staticmethod
    def _base_args():
        return SimpleNamespace(
            verbose=False,
            log_format="text",
            state_file="state.json",
            primary_context="primary",
            secondary_context="secondary",
            skip_gitops_check=False,
            argocd_check=False,
            validate_only=False,
            argocd_manage=False,
            setup=False,
            reset_state=False,
            argocd_resume_only=False,
        )

    def test_main_prints_gitops_report_on_operation_exception(self):
        args = self._base_args()
        logger = Mock()
        state = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover.StateManager", return_value=state
        ), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._execute_operation", side_effect=RuntimeError("boom")
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_FAILURE
        collector.print_report.assert_called_once()
        state.add_error.assert_called_once_with("boom")

    def test_main_prints_gitops_report_on_keyboard_interrupt(self):
        args = self._base_args()
        logger = Mock()
        state = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover.StateManager", return_value=state
        ), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._execute_operation", side_effect=KeyboardInterrupt
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_INTERRUPT
        collector.print_report.assert_called_once()
        state.add_error.assert_not_called()

    def test_main_prints_gitops_report_on_success(self):
        args = self._base_args()
        logger = Mock()
        state = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover.StateManager", return_value=state
        ), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._execute_operation", return_value=True
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        collector.print_report.assert_called_once()
        state.add_error.assert_not_called()

    def test_main_prints_gitops_report_on_operation_failure(self):
        args = self._base_args()
        logger = Mock()
        state = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover.StateManager", return_value=state
        ), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._execute_operation", return_value=False
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_FAILURE
        collector.print_report.assert_called_once()
        state.add_error.assert_not_called()


@pytest.mark.unit
class TestDecommissionAndSetupHelpers:
    """Tests for run_decommission, _get_default_state_dir and run_setup helpers."""

    def test_get_default_state_dir_prefers_env_var(self, monkeypatch: pytest.MonkeyPatch):
        from acm_switchover import _get_default_state_dir

        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", "/tmp/custom-state-dir")
        assert _get_default_state_dir() == "/tmp/custom-state-dir"

    def test_get_default_state_dir_falls_back_when_env_missing(self, monkeypatch: pytest.MonkeyPatch):
        from acm_switchover import _get_default_state_dir

        monkeypatch.delenv("ACM_SWITCHOVER_STATE_DIR", raising=False)
        assert _get_default_state_dir() == ".state"

    def test_run_decommission_uses_namespace_and_interactive_flag(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=False)
        primary = Mock()
        primary.namespace_exists.return_value = True
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom:
            instance = Decom.return_value
            instance.decommission.return_value = True

            result = run_decommission(args, primary, state, logger)

        assert result is True
        primary.namespace_exists.assert_called_once()
        instance.decommission.assert_called_once_with(interactive=True)

    def test_run_decommission_respects_non_interactive_flag(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=True)
        primary = Mock()
        primary.namespace_exists.return_value = False
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom:
            instance = Decom.return_value
            instance.decommission.return_value = False

            result = run_decommission(args, primary, state, logger)

        assert result is False
        instance.decommission.assert_called_once_with(interactive=False)

    def test_run_setup_successful_execution(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        from acm_switchover import run_setup

        fake_script_dir = tmp_path
        fake_setup_script = fake_script_dir / "scripts" / "setup-rbac.sh"
        fake_setup_script.parent.mkdir(parents=True)
        fake_setup_script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")

        args = SimpleNamespace(
            admin_kubeconfig=str(tmp_path / "admin-kubeconfig"),
            primary_context="primary",
            role="operator",
            token_duration="48h",
            output_dir=str(tmp_path / "out"),
            skip_kubeconfig_generation=False,
            dry_run=False,
        )

        # Ensure required files are reported as existing
        monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
        monkeypatch.setattr("os.path.isfile", lambda path: True)
        monkeypatch.setattr("os.path.abspath", lambda _: str(fake_script_dir / "dummy.py"))
        monkeypatch.setattr("os.path.dirname", lambda p: str(fake_script_dir))

        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0)
            logger = logging.getLogger("test")
            assert run_setup(args, logger) is True

    def test_run_setup_missing_kubeconfig_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        from acm_switchover import run_setup

        args = SimpleNamespace(
            admin_kubeconfig=str(tmp_path / "missing-kubeconfig"),
            primary_context="primary",
            role="operator",
            token_duration="48h",
            output_dir=str(tmp_path / "out"),
            skip_kubeconfig_generation=False,
            dry_run=False,
        )

        # Kubeconfig does not exist
        monkeypatch.setattr("os.path.isfile", lambda path: False)
        logger = logging.getLogger("test")
        assert run_setup(args, logger) is False


@pytest.mark.unit
class TestPreflightPhase:
    def test_run_phase_preflight_passes_argocd_flags_to_preflight_validator(self):
        args = SimpleNamespace(
            method="passive",
            skip_rbac_validation=False,
            argocd_check=True,
            argocd_manage=True,
            skip_observability_checks=False,
            validate_only=False,
        )
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
            "has_observability": False,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class, patch(
            "acm_switchover._report_argocd_acm_impact"
        ) as report_argocd_impact:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = _run_phase_preflight(args, state, primary, secondary, logger)

        assert result is True
        validator_class.assert_called_once_with(
            primary,
            secondary,
            "passive",
            skip_rbac_validation=False,
            argocd_check=True,
            argocd_manage=True,
        )
        report_argocd_impact.assert_called_once_with(primary, secondary, logger)

    def test_run_phase_preflight_disables_argocd_manage_in_validate_only_mode(self):
        args = SimpleNamespace(
            method="passive",
            skip_rbac_validation=False,
            argocd_check=False,
            argocd_manage=True,
            skip_observability_checks=False,
            validate_only=True,
        )
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
            "has_observability": False,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = _run_phase_preflight(args, state, primary, secondary, logger)

        assert result is True
        validator_class.assert_called_once_with(
            primary,
            secondary,
            "passive",
            skip_rbac_validation=False,
            argocd_check=False,
            argocd_manage=False,
        )


@pytest.mark.unit
class TestArgocdResumeOnly:
    def test_resume_only_fails_when_state_missing(self):
        from acm_switchover import _run_argocd_resume_only

        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": None,
            "argocd_paused_apps": [],
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

    def test_resume_only_rejects_dry_run_state(self):
        from acm_switchover import _run_argocd_resume_only

        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_pause_dry_run": True,
            "argocd_run_id": "run-1",
            "argocd_paused_apps": [
                {
                    "hub": "primary",
                    "namespace": "argocd",
                    "name": "app-1",
                    "original_sync_policy": {"automated": {}},
                }
            ],
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_autosync") as resume_autosync:
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False
            resume_autosync.assert_not_called()

    def test_resume_only_fails_when_restore_fails(self):
        from acm_switchover import _run_argocd_resume_only
        from lib import argocd as argocd_lib

        paused_apps = [
            {
                "hub": "primary",
                "namespace": "argocd",
                "name": "app-1",
                "original_sync_policy": {"automated": {}},
            },
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": "app-2",
                "original_sync_policy": {"automated": {"prune": True}},
            },
        ]
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace()
        primary = Mock()
        secondary = Mock()
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_autosync") as resume_autosync:
            resume_autosync.side_effect = [
                argocd_lib.ResumeResult(namespace="argocd", name="app-1", restored=True),
                argocd_lib.ResumeResult(
                    namespace="argocd",
                    name="app-2",
                    restored=False,
                    skip_reason="patch failed: 403 Forbidden",
                ),
            ]
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False
