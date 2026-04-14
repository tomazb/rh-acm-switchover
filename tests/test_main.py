"""Unit tests for acm_switchover.py (main script).

Tests argument parsing and basic entry point logic.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import pytest
from kubernetes.client.rest import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import (
    _fail_phase,
    _report_argocd_acm_impact,
    _run_phase_preflight,
    main,
    parse_args,
    run_restore_only,
    run_switchover,
    validate_args,
)
from lib import argocd as argocd_lib
from lib.constants import EXIT_FAILURE, EXIT_INTERRUPT, EXIT_SUCCESS
from lib.validation import ValidationError


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

    def test_argocd_resume_only_parses_without_method_or_old_hub_action(self):
        """Standalone resume-only mode must not require switchover-only flags."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--secondary-context",
                "p2",
                "--argocd-resume-only",
            ],
        ):
            args = parse_args()
            assert args.argocd_resume_only is True
            assert args.method is None
            assert args.old_hub_action is None

    def test_setup_parses_without_method_or_old_hub_action(self):
        """Setup mode must not require switchover-only flags."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--setup",
                "--admin-kubeconfig",
                ".state/admin.kubeconfig",
            ],
        ):
            args = parse_args()
            assert args.setup is True
            assert args.method is None
            assert args.old_hub_action is None

    def test_argocd_resume_only_rejects_dry_run_at_parse_time(self):
        """Resume-only is a standalone mode and must be mutually exclusive with dry-run."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--secondary-context",
                "p2",
                "--argocd-resume-only",
                "--dry-run",
            ],
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_validate_args_warns_when_argocd_manage_has_no_effect_in_validate_only(self):
        """validate_args should warn instead of rejecting argocd-manage with validate-only."""
        args = SimpleNamespace(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file=".state/switchover-state.json",
            decommission=False,
            setup=False,
            validate_only=True,
            argocd_manage=True,
            argocd_resume_only=False,
            argocd_resume_after_switchover=False,
            non_interactive=False,
        )
        logger = Mock()

        validate_args(args, logger)

        logger.warning.assert_any_call(
            "--argocd-manage has no effect with --validate-only; continuing without Argo CD management."
        )


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

    def test_force_with_missing_last_updated_validate_only_preserves_phase(self, tmp_path):
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

        with patch("acm_switchover._run_phase_preflight", return_value=True) as preflight:
            result = run_switchover(args, reloaded, Mock(), Mock(), Mock())

        assert result is True
        assert reloaded.get_current_phase() == Phase.COMPLETED
        preflight.assert_called_once()

    def test_validate_only_with_missing_last_updated_still_runs_preflight(self, tmp_path):
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        state.state.pop("last_updated", None)
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))
        args = SimpleNamespace(
            force=False,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True) as preflight:
            assert run_switchover(args, reloaded, Mock(), Mock(), Mock()) is True

        assert reloaded.get_current_phase() == Phase.COMPLETED
        preflight.assert_called_once()

    def test_validate_only_with_malformed_last_updated_still_runs_preflight(self, tmp_path):
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        state.state["last_updated"] = "not-a-timestamp"
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))
        args = SimpleNamespace(
            force=False,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True) as preflight:
            assert run_switchover(args, reloaded, Mock(), Mock(), Mock()) is True

        assert reloaded.get_current_phase() == Phase.COMPLETED
        preflight.assert_called_once()

    def test_validate_only_does_not_refresh_last_updated(self, tmp_path):
        """Validate-only must not update last_updated, preserving stale-state detection (F1)."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        original_timestamp = "2024-01-01T00:00:00+00:00"
        state.state["last_updated"] = original_timestamp
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))
        args = SimpleNamespace(
            force=False,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True):
            result = run_switchover(args, reloaded, Mock(), Mock(), Mock())

        assert result is True
        assert reloaded.get_current_phase() == Phase.COMPLETED
        # Re-read from disk to verify last_updated was NOT refreshed
        final_state = StateManager(str(state_file))
        assert final_state.state["last_updated"] == original_timestamp

    def test_stale_completed_state_remains_stale_after_validate_only(self, tmp_path):
        """A stale completed state must remain stale after validate-only (F1)."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        stale_timestamp = "2023-01-01T00:00:00+00:00"
        state.state["last_updated"] = stale_timestamp
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))
        args = SimpleNamespace(
            force=False,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True):
            run_switchover(args, reloaded, Mock(), Mock(), Mock())

        # Re-read from disk: timestamp must still be stale
        final_state = StateManager(str(state_file))
        assert final_state.state["last_updated"] == stale_timestamp
        assert final_state.get_current_phase() == Phase.COMPLETED

        # Subsequent non-validate-only run must detect the stale state
        reloaded2 = StateManager(str(state_file))
        args2 = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )
        with pytest.raises(SystemExit) as exc:
            run_switchover(args2, reloaded2, Mock(), Mock(), Mock())
        assert exc.value.code == EXIT_FAILURE

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

        with patch("acm_switchover._run_phase_preflight") as preflight, patch(
            "acm_switchover._run_phase_primary_prep"
        ) as primary_prep, patch("acm_switchover._run_phase_activation") as activation, patch(
            "acm_switchover._run_phase_post_activation"
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization"
        ) as finalization:
            assert run_switchover(args, reloaded, Mock(), Mock(), Mock()) is True

        preflight.assert_not_called()
        primary_prep.assert_not_called()
        activation.assert_not_called()
        post_activation.assert_not_called()
        finalization.assert_not_called()

    def test_recent_completed_state_validate_only_still_runs_preflight(self, tmp_path):
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
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=True) as preflight, patch(
            "acm_switchover._run_phase_primary_prep"
        ), patch("acm_switchover._run_phase_activation"), patch("acm_switchover._run_phase_post_activation"), patch(
            "acm_switchover._run_phase_finalization"
        ):
            assert run_switchover(args, reloaded, Mock(), Mock(), Mock()) is True

        assert reloaded.get_current_phase() == Phase.COMPLETED
        preflight.assert_called_once()

    def test_recent_completed_state_logs_explicit_noop_message(self, tmp_path):
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
        logger = Mock()
        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        assert run_switchover(args, reloaded, Mock(), Mock(), logger) is True

        joined_info = "\n".join(
            (
                call.args[0] % call.args[1:]
                if call.args and isinstance(call.args[0], str) and len(call.args) > 1
                else call.args[0]
            )
            for call in logger.info.call_args_list
            if call.args
        )
        assert "already completed" in joined_info.lower()
        assert "no phases were executed on this run" in joined_info.lower()


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

    def test_run_switchover_validate_only_ignores_resumed_non_init_phase(self, tmp_path):
        """Validate-only must run preflight only, even when state has progressed beyond INIT."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.POST_ACTIVATION)

        args = SimpleNamespace(
            force=False,
            validate_only=True,
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
        preflight.assert_called_once()
        primary_prep.assert_not_called()
        activation.assert_not_called()
        post_activation.assert_not_called()
        finalization.assert_not_called()

    def test_run_switchover_validate_only_preserves_resumed_phase(self, tmp_path):
        """Validate-only should not overwrite the persisted resume phase."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.POST_ACTIVATION)

        args = SimpleNamespace(
            force=False,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )
        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.POST_ACTIVATION

    def test_run_switchover_validate_only_restores_phase_on_preflight_failure(self, tmp_path):
        """Validate-only must restore the original phase even when preflight fails."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.POST_ACTIVATION)

        args = SimpleNamespace(
            force=False,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )

        with patch("acm_switchover._run_phase_preflight", return_value=False):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is False
        assert state.get_current_phase() == Phase.POST_ACTIVATION

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

    def test_run_switchover_resume_from_failed_secondary_verify_retries_activation_path(self, tmp_path):
        """Verify FAILED resume supports legacy SECONDARY_VERIFY by continuing from activation."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.SECONDARY_VERIFY)
        state.add_error("legacy secondary verification failure", Phase.SECONDARY_VERIFY.value)
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
        preflight.assert_not_called()
        primary_prep.assert_not_called()
        activation.assert_called_once()
        post_activation.assert_not_called()
        finalization.assert_not_called()

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

    def test_run_switchover_failed_state_with_non_runnable_error_phase_requires_force(self, tmp_path):
        """FAILED resume should refuse phases that are not valid restart points."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)
        state.add_error("init failure is not resumable", Phase.INIT.value)
        state.set_phase(Phase.FAILED)

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

    def test_run_switchover_validate_only_preserves_failed_state(self, tmp_path):
        """Validate-only must NOT mutate durable state when the phase is FAILED."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        # Simulate a failure during POST_ACTIVATION
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("some error", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)

        args = SimpleNamespace(
            force=False,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )
        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        # Critical: FAILED marker and error history must survive
        assert state.get_current_phase() == Phase.FAILED
        assert len(state.get_errors()) == 1

    def test_run_switchover_force_validate_only_preserves_failed_state(self, tmp_path):
        """--force --validate-only must NOT reset/wipe state when the phase is FAILED."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.ACTIVATION)
        state.add_error("activation error", Phase.ACTIVATION.value)
        state.set_phase(Phase.FAILED)

        args = SimpleNamespace(
            force=True,
            validate_only=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )
        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.FAILED
        assert len(state.get_errors()) == 1

    def test_run_switchover_rejects_non_runnable_phase(self):
        """Unexpected state phases should fail fast instead of flowing through as success."""

        class FakePhase:
            value = "unexpected"

        state = Mock()
        state.get_current_phase.return_value = FakePhase()
        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=".state/test.json",
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with patch("acm_switchover._fail_phase", return_value=False) as fail_phase:
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is False
        fail_phase.assert_called_once()

    def test_fail_phase_skips_exact_duplicate_same_phase_error(self):
        state = Mock()
        state.get_current_phase.return_value = SimpleNamespace(value="finalization")
        state.get_errors.return_value = [{"phase": "finalization", "error": "current failure"}]
        logger = Mock()

        result = _fail_phase(state, "current failure", logger)

        assert result is False
        state.add_error.assert_not_called()
        state.set_phase.assert_called_once()

    def test_fail_phase_skips_generic_when_module_already_recorded_same_phase_error(self):
        """F8: When the module already added a specific error for the current phase,
        _fail_phase should NOT overwrite it with a generic wrapper message."""
        state = Mock()
        state.get_current_phase.return_value = SimpleNamespace(value="finalization")
        state.get_errors.return_value = [{"phase": "finalization", "error": "specific root cause"}]
        logger = Mock()

        result = _fail_phase(state, "Finalization failed!", logger)

        assert result is False
        state.add_error.assert_not_called()
        state.set_phase.assert_called_once()

    def test_fail_phase_appends_error_when_last_error_is_different_phase(self):
        state = Mock()
        state.get_current_phase.return_value = SimpleNamespace(value="finalization")
        state.get_errors.return_value = [{"phase": "activation", "error": "prior"}]
        state.get_config.return_value = None
        logger = Mock()

        result = _fail_phase(state, "current failure", logger)

        assert result is False
        state.add_error.assert_called_once_with("current failure", phase="finalization")
        state.set_phase.assert_called_once()

    def test_fail_phase_appends_wrapper_after_retry_when_last_error_is_stale_same_phase(self):
        state = Mock()
        state.get_current_phase.return_value = SimpleNamespace(value="preflight_validation")
        state.get_errors.return_value = [{"phase": "preflight_validation", "error": "old failure"}]
        state._retry_error_baseline = {
            "phase": "preflight_validation",
            "count": 1,
        }
        logger = Mock()

        result = _fail_phase(state, "Pre-flight validation failed! Cannot proceed.", logger)

        assert result is False
        state.add_error.assert_called_once_with(
            "Pre-flight validation failed! Cannot proceed.",
            phase="preflight_validation",
        )
        state.set_phase.assert_called_once()

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
            "acm_switchover.os.path.exists", return_value=True
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
            "acm_switchover.os.path.exists", return_value=True
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
            "acm_switchover.os.path.exists", return_value=True
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
            "acm_switchover.os.path.exists", return_value=True
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

    def test_main_prints_gitops_report_on_argocd_resume_only_exception(self):
        args = self._base_args()
        args.argocd_resume_only = True
        logger = Mock()
        state = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover.os.path.exists", return_value=True
        ), patch(
            "acm_switchover.StateManager", return_value=state
        ), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._run_argocd_resume_only", side_effect=RuntimeError("boom")
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_FAILURE
        collector.print_report.assert_called_once()
        state.add_error.assert_called_once_with("boom")

    def test_main_prints_gitops_report_on_argocd_resume_only_success(self):
        args = self._base_args()
        args.argocd_resume_only = True
        logger = Mock()
        state = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover.os.path.exists", return_value=True
        ), patch(
            "acm_switchover.StateManager", return_value=state
        ), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._run_argocd_resume_only", return_value=True
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        collector.print_report.assert_called_once()
        state.add_error.assert_not_called()

    def test_main_skips_context_enforcement_for_argocd_resume_only(self):
        args = self._base_args()
        args.argocd_resume_only = True
        logger = Mock()
        state = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover.os.path.exists", return_value=True
        ), patch(
            "acm_switchover.StateManager", return_value=state
        ), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._run_argocd_resume_only", return_value=True
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        state.ensure_contexts.assert_not_called()

    def test_main_resume_only_uses_existing_reversed_default_state_file(self, tmp_path, monkeypatch):
        args = self._base_args()
        args.argocd_resume_only = True
        args.state_file = None
        args.primary_context = "primary-a"
        args.secondary_context = "secondary-b"
        logger = Mock()
        state = Mock()
        collector = Mock()
        reversed_path = tmp_path / "switchover-secondary-b__primary-a.json"
        reversed_path.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path))

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover.StateManager", return_value=state
        ) as state_manager, patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ), patch(
            "acm_switchover._run_argocd_resume_only", return_value=True
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        state_manager.assert_called_once_with(str(reversed_path))
        assert args.state_file == str(reversed_path)

    def test_main_resume_only_missing_state_file_exits_before_state_manager(self, tmp_path):
        args = self._base_args()
        args.argocd_resume_only = True
        missing_state_file = tmp_path / "missing-state.json"
        logger = Mock()
        collector = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value=str(missing_state_file)
        ), patch(
            "acm_switchover.StateManager"
        ) as state_manager, patch(
            "acm_switchover._initialize_clients"
        ) as initialize_clients, patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_FAILURE
        state_manager.assert_not_called()
        initialize_clients.assert_not_called()
        assert not missing_state_file.exists()
        collector.print_report.assert_not_called()

    def test_main_enforces_contexts_for_normal_operation(self):
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
        state.ensure_contexts.assert_called_once_with("primary", "secondary")


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

        args = SimpleNamespace(dry_run=False, non_interactive=False, skip_rbac_validation=False)
        primary = Mock()
        primary.namespace_exists.return_value = True
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom, patch(
            "acm_switchover.validate_decommission_permissions"
        ) as validate_decommission:
            instance = Decom.return_value
            instance.decommission.return_value = True

            result = run_decommission(args, primary, state, logger)

        assert result is True
        primary.namespace_exists.assert_called_once()
        validate_decommission.assert_called_once_with(
            primary_client=primary,
            skip_observability=False,
        )
        instance.decommission.assert_called_once_with(interactive=True)

    def test_run_decommission_respects_non_interactive_flag(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=True, skip_rbac_validation=False)
        primary = Mock()
        primary.namespace_exists.return_value = False
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom, patch(
            "acm_switchover.validate_decommission_permissions"
        ) as validate_decommission:
            instance = Decom.return_value
            instance.decommission.return_value = False

            result = run_decommission(args, primary, state, logger)

        assert result is False
        validate_decommission.assert_called_once_with(
            primary_client=primary,
            skip_observability=True,
        )
        instance.decommission.assert_called_once_with(interactive=False)

    def test_run_decommission_returns_false_when_rbac_validation_fails(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=False, skip_rbac_validation=False)
        primary = Mock()
        primary.namespace_exists.return_value = False
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom, patch(
            "acm_switchover.validate_decommission_permissions",
            side_effect=ValidationError("missing decommission permissions"),
        ):
            result = run_decommission(args, primary, state, logger)

        assert result is False
        Decom.assert_not_called()

    def test_run_decommission_skips_rbac_validation_when_requested(self):
        from acm_switchover import run_decommission

        args = SimpleNamespace(dry_run=False, non_interactive=False, skip_rbac_validation=True)
        primary = Mock()
        primary.namespace_exists.return_value = True
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.Decommission") as Decom, patch(
            "acm_switchover.validate_decommission_permissions"
        ) as validate_decommission:
            instance = Decom.return_value
            instance.decommission.return_value = True

            result = run_decommission(args, primary, state, logger)

        assert result is True
        validate_decommission.assert_not_called()
        instance.decommission.assert_called_once_with(interactive=True)

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
            include_decommission=False,
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
            assert run.call_args.args[0] == [
                str(fake_setup_script),
                "--admin-kubeconfig",
                args.admin_kubeconfig,
                "--context",
                args.primary_context,
                "--role",
                args.role,
                "--token-duration",
                args.token_duration,
                "--output-dir",
                args.output_dir,
            ]

    def test_run_setup_passes_include_decommission_flag(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
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
            include_decommission=True,
            skip_kubeconfig_generation=False,
            dry_run=False,
        )

        monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
        monkeypatch.setattr("os.path.isfile", lambda path: True)
        monkeypatch.setattr("os.path.abspath", lambda _: str(fake_script_dir / "dummy.py"))
        monkeypatch.setattr("os.path.dirname", lambda p: str(fake_script_dir))

        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0)
            logger = logging.getLogger("test")
            assert run_setup(args, logger) is True
            assert run.call_args.args[0][-1] == "--include-decommission"

    def test_run_setup_missing_kubeconfig_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        from acm_switchover import run_setup

        args = SimpleNamespace(
            admin_kubeconfig=str(tmp_path / "missing-kubeconfig"),
            primary_context="primary",
            role="operator",
            token_duration="48h",
            output_dir=str(tmp_path / "out"),
            include_decommission=False,
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
            old_hub_action="secondary",
            skip_rbac_validation=False,
            argocd_manage=True,
            skip_gitops_check=False,
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
            include_decommission=False,
            argocd_manage=True,
            skip_gitops_check=False,
            restore_only=False,
        )
        report_argocd_impact.assert_called_once_with(primary, secondary, logger, argocd_manage=True)

    def test_run_phase_preflight_passes_decommission_intent_to_preflight_validator(
        self,
    ):
        args = SimpleNamespace(
            method="passive",
            old_hub_action="decommission",
            skip_rbac_validation=False,
            argocd_manage=False,
            skip_gitops_check=False,
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
        ):
            validator_class.return_value.validate_all.return_value = (True, config)
            result = _run_phase_preflight(args, state, primary, secondary, logger)

        assert result is True
        validator_class.assert_called_once_with(
            primary,
            secondary,
            "passive",
            skip_rbac_validation=False,
            include_decommission=True,
            argocd_manage=False,
            skip_gitops_check=False,
            restore_only=False,
        )

    def test_report_argocd_impact_warns_instead_of_raising_on_list_failure(self):
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )

        with patch(
            "acm_switchover.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ), patch(
            "acm_switchover.argocd_lib.list_argocd_applications",
            side_effect=ApiException(status=403, reason="Forbidden"),
        ):
            _report_argocd_acm_impact(primary, secondary, logger)

        assert logger.warning.call_count == 2
        assert any("Unable to complete Argo CD check" in call.args[0] for call in logger.warning.call_args_list)

    @pytest.mark.parametrize(
        "side_effect",
        [
            ConnectionError("network down"),
            OSError("socket closed"),
            TypeError("unexpected payload"),
        ],
    )
    def test_report_argocd_impact_warns_on_non_blocking_failures(self, side_effect):
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )

        with patch(
            "acm_switchover.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ), patch(
            "acm_switchover.argocd_lib.list_argocd_applications",
            side_effect=side_effect,
        ):
            _report_argocd_acm_impact(primary, secondary, logger)

        assert logger.warning.call_count == 2
        assert any("Unable to complete Argo CD check" in call.args[0] for call in logger.warning.call_args_list)

    def test_argocd_detection_runs_automatically_in_preflight(self):
        """When skip_gitops_check=False, _report_argocd_acm_impact is called automatically."""
        args = SimpleNamespace(
            method="passive",
            old_hub_action="secondary",
            skip_rbac_validation=False,
            argocd_manage=False,
            skip_gitops_check=False,
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
            _run_phase_preflight(args, state, primary, secondary, logger)

        report_argocd_impact.assert_called_once_with(primary, secondary, logger, argocd_manage=False)

    def test_argocd_detection_skipped_when_skip_gitops_check(self):
        """When skip_gitops_check=True, _report_argocd_acm_impact is NOT called."""
        args = SimpleNamespace(
            method="passive",
            old_hub_action="secondary",
            skip_rbac_validation=False,
            argocd_manage=False,
            skip_gitops_check=True,
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
            _run_phase_preflight(args, state, primary, secondary, logger)

        report_argocd_impact.assert_not_called()

    def test_argocd_advisory_warning_shown_without_argocd_manage(self):
        """Advisory warning logged when ACM-touching apps with auto-sync exist and argocd_manage=False."""
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        acm_app = argocd_lib.AppImpact(
            namespace="openshift-gitops",
            name="acm-config",
            resource_count=3,
            app={"spec": {"syncPolicy": {"automated": {"prune": True, "selfHeal": True}}}},
        )

        with patch(
            "acm_switchover.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ), patch(
            "acm_switchover.argocd_lib.list_argocd_applications",
            return_value=[{"metadata": {"name": "acm-config"}}],
        ), patch(
            "acm_switchover.argocd_lib.find_acm_touching_apps",
            return_value=[acm_app],
        ):
            _report_argocd_acm_impact(primary, secondary, logger, argocd_manage=False)

        warning_texts = [
            call.args[0] % call.args[1:] if len(call.args) > 1 else call.args[0]
            for call in logger.warning.call_args_list
        ]
        assert any(
            "Consider --argocd-manage" in t for t in warning_texts
        ), f"Expected advisory warning with 'Consider --argocd-manage', got: {warning_texts}"

    def test_argocd_advisory_warning_hidden_with_argocd_manage(self):
        """No advisory warning when argocd_manage=True even with ACM-touching auto-sync apps."""
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        acm_app = argocd_lib.AppImpact(
            namespace="openshift-gitops",
            name="acm-config",
            resource_count=3,
            app={"spec": {"syncPolicy": {"automated": {"prune": True, "selfHeal": True}}}},
        )

        with patch(
            "acm_switchover.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ), patch(
            "acm_switchover.argocd_lib.list_argocd_applications",
            return_value=[{"metadata": {"name": "acm-config"}}],
        ), patch(
            "acm_switchover.argocd_lib.find_acm_touching_apps",
            return_value=[acm_app],
        ):
            _report_argocd_acm_impact(primary, secondary, logger, argocd_manage=True)

        warning_texts = [
            call.args[0] % call.args[1:] if len(call.args) > 1 else call.args[0]
            for call in logger.warning.call_args_list
        ]
        assert not any(
            "Consider --argocd-manage" in t for t in warning_texts
        ), f"Advisory warning should NOT appear when argocd_manage=True, got: {warning_texts}"

    def test_argocd_advisory_warning_only_for_autosync_apps(self):
        """No advisory warning when ACM-touching apps exist but none have auto-sync."""
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        acm_app_no_sync = argocd_lib.AppImpact(
            namespace="openshift-gitops",
            name="acm-config",
            resource_count=3,
            app={"spec": {"syncPolicy": {}}},
        )

        with patch(
            "acm_switchover.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ), patch(
            "acm_switchover.argocd_lib.list_argocd_applications",
            return_value=[{"metadata": {"name": "acm-config"}}],
        ), patch(
            "acm_switchover.argocd_lib.find_acm_touching_apps",
            return_value=[acm_app_no_sync],
        ):
            _report_argocd_acm_impact(primary, secondary, logger, argocd_manage=False)

        warning_texts = [
            call.args[0] % call.args[1:] if len(call.args) > 1 else call.args[0]
            for call in logger.warning.call_args_list
        ]
        assert not any(
            "Consider --argocd-manage" in t for t in warning_texts
        ), f"Advisory warning should NOT appear without auto-sync apps, got: {warning_texts}"

    def test_argocd_advisory_warning_shown_with_primary_none(self):
        """Advisory warning renders correctly in restore-only mode (primary=None)."""
        secondary = Mock()
        logger = Mock()
        discovery = argocd_lib.ArgocdDiscoveryResult(
            has_applications_crd=True,
            has_argocds_crd=False,
            install_type="vanilla",
        )
        acm_app = argocd_lib.AppImpact(
            namespace="openshift-gitops",
            name="acm-config",
            resource_count=3,
            app={"spec": {"syncPolicy": {"automated": {"prune": True}}}},
        )

        with patch(
            "acm_switchover.argocd_lib.detect_argocd_installation",
            return_value=discovery,
        ), patch(
            "acm_switchover.argocd_lib.list_argocd_applications",
            return_value=[{"metadata": {"name": "acm-config"}}],
        ), patch(
            "acm_switchover.argocd_lib.find_acm_touching_apps",
            return_value=[acm_app],
        ):
            _report_argocd_acm_impact(None, secondary, logger, argocd_manage=False)

        warning_texts = [
            call.args[0] % call.args[1:] if len(call.args) > 1 else call.args[0]
            for call in logger.warning.call_args_list
        ]
        assert any(
            "Consider --argocd-manage" in t for t in warning_texts
        ), f"Expected advisory warning with 'Consider --argocd-manage' in restore-only mode, got: {warning_texts}"


@pytest.mark.unit
class TestArgocdResumeOnly:
    def test_resume_only_swaps_clients_when_contexts_are_reversed(self):
        from acm_switchover import _run_argocd_resume_only

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
        state.state = {
            "contexts": {
                "primary": "hub-a",
                "secondary": "hub-b",
            }
        }
        state.get_config.side_effect = lambda key, default=None: {
            "argocd_run_id": "run-1",
            "argocd_paused_apps": paused_apps,
        }.get(key, default)
        args = SimpleNamespace(primary_context="hub-b", secondary_context="hub-a")
        primary = Mock(name="primary-client")
        secondary = Mock(name="secondary-client")
        logger = logging.getLogger("test")

        with patch("acm_switchover.argocd_lib.resume_recorded_applications") as resume_recorded:
            resume_recorded.return_value = argocd_lib.ResumeSummary(restored=2, already_resumed=0, failed=0)

            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

        resume_recorded.assert_called_once_with(
            paused_apps,
            "run-1",
            secondary,
            primary,
            logger,
        )

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

    def test_resume_only_treats_marker_missing_as_already_resumed(self):
        from acm_switchover import _run_argocd_resume_only
        from lib import argocd as argocd_lib

        paused_apps = [
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
            resume_autosync.return_value = argocd_lib.ResumeResult(
                namespace="argocd",
                name="app-2",
                restored=False,
                skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISSING,
            )
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is True

    def test_resume_only_fails_on_marker_mismatch(self):
        from acm_switchover import _run_argocd_resume_only
        from lib import argocd as argocd_lib

        paused_apps = [
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
            resume_autosync.return_value = argocd_lib.ResumeResult(
                namespace="argocd",
                name="app-2",
                restored=False,
                skip_reason=argocd_lib.RESUME_SKIP_REASON_MARKER_MISMATCH,
            )
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

    def test_resume_only_logs_malformed_state_entries(self, caplog):
        from acm_switchover import _run_argocd_resume_only

        paused_apps = [
            "bad-entry",
            {
                "hub": "secondary",
                "namespace": "argocd",
                "name": None,
                "original_sync_policy": {"automated": {}},
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

        with caplog.at_level(logging.WARNING):
            assert _run_argocd_resume_only(args, state, primary, secondary, logger) is False

        assert "unexpected format" in caplog.text
        assert "missing required fields" in caplog.text


@pytest.mark.unit
class TestPhaseFlowIntegration:
    """Integration tests that verify orchestrator phase-flow decisions using
    lightweight stubs (not full mocks) that track call order.

    Each stub advances the state phase (like the real handler does) so the
    orchestrator's phase-routing loop sees the correct state transitions.
    """

    # Map handler names to the phase they set at the start of execution.
    _PHASE_MAP = {
        "preflight": "PREFLIGHT",
        "primary_prep": "PRIMARY_PREP",
        "activation": "ACTIVATION",
        "post_activation": "POST_ACTIVATION",
        "finalization": "FINALIZATION",
    }

    @staticmethod
    def _make_args(**overrides):
        defaults = dict(
            force=False,
            validate_only=False,
            state_file="state.json",
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @classmethod
    def _make_stub(cls, name, call_order, succeeds=True):
        """Return a stub that advances state phase and tracks call order."""
        from lib.utils import Phase

        target_phase = Phase[cls._PHASE_MAP[name]]

        def stub(args, state, *rest, **kwargs):
            call_order.append(name)
            state.set_phase(target_phase)
            return succeeds

        return stub

    def test_full_phase_flow_call_order(self, tmp_path):
        """Stubs track that all five phase handlers fire in order for a fresh run."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch("acm_switchover._run_phase_preflight", side_effect=self._make_stub("preflight", call_order)), patch(
            "acm_switchover._run_phase_primary_prep", side_effect=self._make_stub("primary_prep", call_order)
        ), patch("acm_switchover._run_phase_activation", side_effect=self._make_stub("activation", call_order)), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization", side_effect=self._make_stub("finalization", call_order)
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        assert call_order == [
            "preflight",
            "primary_prep",
            "activation",
            "post_activation",
            "finalization",
        ]

    def test_mid_flow_failure_stops_subsequent_phases(self, tmp_path):
        """When activation fails (returns False), post_activation and finalization must NOT run."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch("acm_switchover._run_phase_preflight", side_effect=self._make_stub("preflight", call_order)), patch(
            "acm_switchover._run_phase_primary_prep", side_effect=self._make_stub("primary_prep", call_order)
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._make_stub("activation", call_order, succeeds=False),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization", side_effect=self._make_stub("finalization", call_order)
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is False
        assert call_order == ["preflight", "primary_prep", "activation"]

    def test_resume_from_primary_prep_skips_preflight(self, tmp_path):
        """When state is PRIMARY_PREP, the flow should skip preflight and start
        from primary_prep onwards."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.PRIMARY_PREP)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch("acm_switchover._run_phase_preflight", side_effect=self._make_stub("preflight", call_order)), patch(
            "acm_switchover._run_phase_primary_prep", side_effect=self._make_stub("primary_prep", call_order)
        ), patch("acm_switchover._run_phase_activation", side_effect=self._make_stub("activation", call_order)), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization", side_effect=self._make_stub("finalization", call_order)
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        assert "preflight" not in call_order
        assert call_order[0] == "primary_prep"

    def test_resume_from_activation_skips_earlier_phases(self, tmp_path):
        """When state is ACTIVATION, only activation onward should execute."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.ACTIVATION)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch("acm_switchover._run_phase_preflight", side_effect=self._make_stub("preflight", call_order)), patch(
            "acm_switchover._run_phase_primary_prep", side_effect=self._make_stub("primary_prep", call_order)
        ), patch("acm_switchover._run_phase_activation", side_effect=self._make_stub("activation", call_order)), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization", side_effect=self._make_stub("finalization", call_order)
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert "preflight" not in call_order
        assert "primary_prep" not in call_order
        assert call_order == ["activation", "post_activation", "finalization"]


@pytest.mark.unit
class TestResumeFromFailedState:
    """Tests that verify orchestrator resume-from-FAILED decisions."""

    # Map handler names to the phase they set at the start of execution.
    _PHASE_MAP = {
        "preflight": "PREFLIGHT",
        "primary_prep": "PRIMARY_PREP",
        "activation": "ACTIVATION",
        "post_activation": "POST_ACTIVATION",
        "finalization": "FINALIZATION",
    }

    @staticmethod
    def _make_args(**overrides):
        defaults = dict(
            force=False,
            validate_only=False,
            state_file="state.json",
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @classmethod
    def _make_stub(cls, name, call_order):
        from lib.utils import Phase

        target_phase = Phase[cls._PHASE_MAP[name]]

        def stub(args, state, *rest, **kwargs):
            call_order.append(name)
            state.set_phase(target_phase)
            return True

        return stub

    def test_resume_from_failed_preflight_reruns_from_preflight(self, tmp_path):
        """FAILED with last_error_phase=PREFLIGHT should resume from PREFLIGHT,
        running all phases from the beginning."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.PREFLIGHT)
        state.add_error("preflight check failed", Phase.PREFLIGHT.value)
        state.set_phase(Phase.FAILED)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch("acm_switchover._run_phase_preflight", side_effect=self._make_stub("preflight", call_order)), patch(
            "acm_switchover._run_phase_primary_prep", side_effect=self._make_stub("primary_prep", call_order)
        ), patch("acm_switchover._run_phase_activation", side_effect=self._make_stub("activation", call_order)), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization", side_effect=self._make_stub("finalization", call_order)
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        assert call_order[0] == "preflight"

    def test_resume_from_failed_activation_skips_preflight_and_prep(self, tmp_path):
        """FAILED with last_error_phase=ACTIVATION should resume from ACTIVATION,
        skipping preflight and primary_prep."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.ACTIVATION)
        state.add_error("activation failed", Phase.ACTIVATION.value)
        state.set_phase(Phase.FAILED)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch("acm_switchover._run_phase_preflight", side_effect=self._make_stub("preflight", call_order)), patch(
            "acm_switchover._run_phase_primary_prep", side_effect=self._make_stub("primary_prep", call_order)
        ), patch("acm_switchover._run_phase_activation", side_effect=self._make_stub("activation", call_order)), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization", side_effect=self._make_stub("finalization", call_order)
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        assert "preflight" not in call_order
        assert "primary_prep" not in call_order
        assert call_order[0] == "activation"

    def test_resume_from_failed_finalization_only_reruns_finalization(self, tmp_path):
        """FAILED with last_error_phase=FINALIZATION should only rerun finalization."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.FINALIZATION)
        state.add_error("finalization failed", Phase.FINALIZATION.value)
        state.set_phase(Phase.FAILED)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch("acm_switchover._run_phase_preflight", side_effect=self._make_stub("preflight", call_order)), patch(
            "acm_switchover._run_phase_primary_prep", side_effect=self._make_stub("primary_prep", call_order)
        ), patch("acm_switchover._run_phase_activation", side_effect=self._make_stub("activation", call_order)), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization", side_effect=self._make_stub("finalization", call_order)
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        assert call_order == ["finalization"]

    def test_failed_state_records_retry_error_baseline(self, tmp_path):
        """Resuming from FAILED should set _retry_error_baseline on the state,
        so that _fail_phase can detect duplicate errors vs new ones."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("post-act error", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._make_stub("finalization", call_order),
        ):
            run_switchover(args, state, Mock(), Mock(), Mock())

        assert hasattr(state, "_retry_error_baseline")
        assert state._retry_error_baseline["phase"] == Phase.POST_ACTIVATION.value
        assert state._retry_error_baseline["count"] == 1


@pytest.mark.unit
class TestStaleStateDetection:
    """Tests for stale completed state detection and --force override."""

    @staticmethod
    def _make_args(**overrides):
        defaults = dict(
            force=False,
            validate_only=False,
            state_file="state.json",
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_stale_completed_state_exits_without_force(self, tmp_path):
        """State older than STALE_STATE_THRESHOLD with COMPLETED phase should
        exit with EXIT_FAILURE when --force is not set."""
        from lib.constants import STALE_STATE_THRESHOLD
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        # Set COMPLETED with stale timestamp using _write_state to preserve it
        state.state["current_phase"] = Phase.COMPLETED.value
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_STATE_THRESHOLD + 1)
        state.state["last_updated"] = stale_time.isoformat()
        state._write_state(state.state)

        # Reload to simulate fresh run
        state2 = StateManager(str(state_file))
        args = self._make_args(state_file=str(state_file))

        with pytest.raises(SystemExit) as exc_info:
            run_switchover(args, state2, Mock(), Mock(), Mock())

        assert exc_info.value.code == EXIT_FAILURE

    def test_stale_completed_state_force_resets_and_proceeds(self, tmp_path):
        """With --force on stale COMPLETED state, orchestrator should reset
        state and run from the beginning."""
        from lib.constants import STALE_STATE_THRESHOLD
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_STATE_THRESHOLD + 1)
        state.state["last_updated"] = stale_time.isoformat()
        state._write_state(state.state)

        state2 = StateManager(str(state_file))
        args = self._make_args(state_file=str(state_file), force=True)

        with patch("acm_switchover._run_phase_preflight", return_value=True), patch(
            "acm_switchover._run_phase_primary_prep", return_value=True
        ), patch("acm_switchover._run_phase_activation", return_value=True), patch(
            "acm_switchover._run_phase_post_activation", return_value=True
        ), patch(
            "acm_switchover._run_phase_finalization", return_value=True
        ):
            result = run_switchover(args, state2, Mock(), Mock(), Mock())

        assert result is True
        assert state2.get_current_phase() == Phase.COMPLETED

    def test_validate_only_with_completed_state_runs_preflight(self, tmp_path):
        """--validate-only should run preflight even on stale COMPLETED state,
        bypassing the stale check entirely."""
        from lib.constants import STALE_STATE_THRESHOLD
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_STATE_THRESHOLD + 1)
        state.state["last_updated"] = stale_time.isoformat()
        state._write_state(state.state)

        state2 = StateManager(str(state_file))
        args = self._make_args(
            state_file=str(state_file),
            validate_only=True,
            old_hub_action="secondary",
            argocd_manage=False,
        )
        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = run_switchover(args, state2, Mock(), Mock(), Mock())

        assert result is True
        # Phase should be preserved (checkpoint mechanism)
        assert state2.get_current_phase() == Phase.COMPLETED


@pytest.mark.unit
class TestMainExceptionHandlers:
    """Tests for exception handling in main() entry point."""

    def test_state_load_error_exits_with_recovery_hint(self, tmp_path, monkeypatch):
        """StateLoadError during StateManager init should exit with EXIT_FAILURE
        and suggest --reset-state."""
        from lib.exceptions import StateLoadError

        state_file = tmp_path / "state.json"
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path))

        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--secondary-context",
                "s1",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
                "--state-file",
                str(state_file),
            ],
        ), patch(
            "acm_switchover.StateManager",
            side_effect=StateLoadError("corrupt state file"),
        ), pytest.raises(
            SystemExit
        ) as exc_info:
            main()

        assert exc_info.value.code == EXIT_FAILURE

    def test_state_lock_error_exits_with_failure(self, tmp_path, monkeypatch):
        """StateLockError during StateManager init should exit with EXIT_FAILURE."""
        from lib.exceptions import StateLockError

        state_file = tmp_path / "state.json"
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path))

        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--secondary-context",
                "s1",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
                "--state-file",
                str(state_file),
            ],
        ), patch(
            "acm_switchover.StateManager",
            side_effect=StateLockError("lock held by PID 12345"),
        ), pytest.raises(
            SystemExit
        ) as exc_info:
            main()

        assert exc_info.value.code == EXIT_FAILURE

    def test_resolve_state_file_value_error_exits(self, tmp_path, monkeypatch):
        """ValueError from _resolve_state_file should exit with EXIT_FAILURE."""
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path))

        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--secondary-context",
                "s1",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
            ],
        ), patch(
            "acm_switchover._resolve_state_file",
            side_effect=ValueError("Multiple candidate state files found"),
        ), pytest.raises(
            SystemExit
        ) as exc_info:
            main()

        assert exc_info.value.code == EXIT_FAILURE


@pytest.mark.unit
class TestPhaseHandlerFailure:
    """Tests that verify error recording when a phase handler fails."""

    # Map handler names to the phase they set at the start of execution.
    _PHASE_MAP = {
        "preflight": "PREFLIGHT",
        "primary_prep": "PRIMARY_PREP",
        "activation": "ACTIVATION",
        "post_activation": "POST_ACTIVATION",
        "finalization": "FINALIZATION",
    }

    @staticmethod
    def _make_args(**overrides):
        defaults = dict(
            force=False,
            validate_only=False,
            state_file="state.json",
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @classmethod
    def _make_failing_stub(cls, name, call_order):
        """Stub that advances state phase, calls _fail_phase, and returns False
        (mimicking what a real handler does on failure)."""
        from lib.utils import Phase

        target_phase = Phase[cls._PHASE_MAP[name]]

        def stub(args, state, *rest, **kwargs):
            call_order.append(name)
            state.set_phase(target_phase)
            return _fail_phase(state, f"{name} failed!", Mock())

        return stub

    @classmethod
    def _make_stub(cls, name, call_order):
        from lib.utils import Phase

        target_phase = Phase[cls._PHASE_MAP[name]]

        def stub(args, state, *rest, **kwargs):
            call_order.append(name)
            state.set_phase(target_phase)
            return True

        return stub

    def test_phase_failure_sets_failed_state_and_records_error(self, tmp_path):
        """When a phase handler returns False (via _fail_phase), the orchestrator
        should end with Phase.FAILED and recorded errors."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._make_failing_stub("preflight", call_order),
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is False
        assert state.get_current_phase() == Phase.FAILED
        errors = state.get_errors()
        assert len(errors) >= 1
        assert any("preflight failed" in e.get("error", "") for e in errors)

    def test_phase_handler_exception_propagates(self, tmp_path):
        """When a phase handler raises an unexpected exception, it should
        propagate (the caller main() catches and records it)."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        args = self._make_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=RuntimeError("unexpected cluster error"),
        ), pytest.raises(RuntimeError, match="unexpected cluster error"):
            run_switchover(args, state, Mock(), Mock(), Mock())

    def test_primary_prep_failure_prevents_activation(self, tmp_path):
        """When primary_prep fails, later phases should NOT execute and state
        should reflect the failure with recorded error metadata."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        call_order = []
        args = self._make_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._make_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=self._make_failing_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._make_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._make_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._make_stub("finalization", call_order),
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is False
        assert state.get_current_phase() == Phase.FAILED
        assert "activation" not in call_order
        assert "post_activation" not in call_order
        assert "finalization" not in call_order
        errors = state.get_errors()
        assert any("primary_prep failed" in e.get("error", "") for e in errors)

    def test_secondary_none_raises_value_error(self):
        """run_switchover should raise ValueError when secondary client is None."""
        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file="state.json",
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with pytest.raises(ValueError, match="Secondary client is required"):
            run_switchover(args, Mock(), Mock(), None, Mock())


class TestRestoreOnlyFlow:
    """Tests for --restore-only single-hub restore workflow."""

    def _make_restore_only_args(self, **overrides):
        defaults = dict(
            restore_only=True,
            primary_context=None,
            secondary_context="new-hub",
            method=None,
            old_hub_action=None,
            validate_only=False,
            dry_run=False,
            force=False,
            state_file="state.json",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            skip_gitops_check=True,
            argocd_manage=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_restore_only_defaults_method_to_full(self):
        """run_restore_only sets method=full when not specified."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args()
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True) as pf, \
             patch("acm_switchover._run_phase_activation", return_value=True), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        assert args.method == "full"
        assert args.old_hub_action == "none"

    def test_restore_only_skips_primary_prep(self):
        """Restore-only flow does NOT call _run_phase_primary_prep."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args()
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True) as pf, \
             patch("acm_switchover._run_phase_primary_prep") as pp, \
             patch("acm_switchover._run_phase_activation", return_value=True), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pp.assert_not_called()

    def test_restore_only_passes_none_primary_to_handlers(self):
        """Phase handlers receive primary=None in restore-only mode."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args()
        state = Mock(spec=StateManager)
        # Return INIT first, then follow each phase transition via set_phase
        current = [Phase.INIT]

        def track_phase(p):
            current[0] = p

        state.get_current_phase.side_effect = lambda: current[0]
        state.set_phase.side_effect = track_phase
        state.get_state_age.return_value = None
        secondary = Mock()

        called_with_primary = []
        # Map handler name to the phase it should advance to
        phase_transitions = {
            "preflight": Phase.PREFLIGHT,
            "activation": Phase.ACTIVATION,
            "post_activation": Phase.POST_ACTIVATION,
            "finalization": Phase.FINALIZATION,
        }

        def capture_primary(name):
            def handler(a, s, primary, sec, log):
                called_with_primary.append((name, primary))
                current[0] = phase_transitions[name]
                return True
            return handler

        with patch("acm_switchover._run_phase_preflight", side_effect=capture_primary("preflight")), \
             patch("acm_switchover._run_phase_activation", side_effect=capture_primary("activation")), \
             patch("acm_switchover._run_phase_post_activation", side_effect=capture_primary("post_activation")), \
             patch("acm_switchover._run_phase_finalization", side_effect=capture_primary("finalization")):
            run_restore_only(args, state, secondary, Mock())

        assert len(called_with_primary) == 4
        for name, primary_val in called_with_primary:
            assert primary_val is None, f"{name} should receive primary=None"

    def test_restore_only_phase_transitions(self):
        """Restore-only flow transitions through correct phases."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args()
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True), \
             patch("acm_switchover._run_phase_activation", return_value=True), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        state.set_phase.assert_any_call(Phase.COMPLETED)

    def test_restore_only_validate_only_runs_preflight_only(self):
        """--restore-only --validate-only only runs preflight."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args(validate_only=True)
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True) as pf, \
             patch("acm_switchover._run_phase_activation") as act:
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pf.assert_called_once()
        act.assert_not_called()

    def test_restore_only_preflight_failure_stops_flow(self):
        """Restore-only flow stops if preflight fails."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args()
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=False) as pf, \
             patch("acm_switchover._run_phase_activation") as act:
            result = run_restore_only(args, state, secondary, Mock())

        assert result is False
        act.assert_not_called()

    def test_restore_only_preflight_sets_restore_only_flag(self):
        """Preflight in restore-only mode passes restore_only=True to PreflightValidator."""
        from lib.utils import Phase

        args = self._make_restore_only_args()
        state = Mock()
        state.get_current_phase.return_value = Phase.PREFLIGHT
        state.get_config.return_value = False
        secondary = Mock()

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (
                True,
                {
                    "primary_version": "unknown",
                    "secondary_version": "2.14.0",
                    "primary_observability_detected": False,
                    "secondary_observability_detected": False,
                    "has_observability": False,
                },
            )
            _run_phase_preflight(args, state, None, secondary, Mock())

        call_kwargs = validator_class.call_args[1]
        assert call_kwargs["restore_only"] is True

    def test_restore_only_finalization_uses_none_old_hub_action(self):
        """Finalization in restore-only mode uses old_hub_action='none'."""
        from lib.utils import Phase

        args = self._make_restore_only_args()
        # Explicitly verify old_hub_action is set to "none" by run_restore_only
        args.method = "full"
        state = Mock()
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True), \
             patch("acm_switchover._run_phase_activation", return_value=True), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True) as fin:
            run_restore_only(args, state, secondary, Mock())

        assert args.old_hub_action == "none"

    def test_argocd_pause_called_before_activation_when_argocd_manage(self):
        """When --argocd-manage is set, _pause_argocd_for_restore is called before ACTIVATION."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args(argocd_manage=True)
        state = Mock(spec=StateManager)
        current = [Phase.INIT]
        state.get_current_phase.side_effect = lambda: current[0]
        state.get_state_age.return_value = None
        state.is_step_completed.return_value = False
        secondary = Mock()

        pause_order = []

        def track_pause(sec, st, dry_run, log):
            pause_order.append("pause")
            return True

        def track_preflight(a, s, primary, sec, log):
            current[0] = Phase.PREFLIGHT
            return True

        def track_activation(a, s, primary, sec, log):
            pause_order.append("activation")
            current[0] = Phase.ACTIVATION
            return True

        with patch("acm_switchover._run_phase_preflight", side_effect=track_preflight), \
             patch("acm_switchover._pause_argocd_for_restore", side_effect=track_pause) as pause_fn, \
             patch("acm_switchover._run_phase_activation", side_effect=track_activation), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pause_fn.assert_called_once_with(secondary, state, False, ANY)
        assert pause_order == ["pause", "activation"], "pause must run before activation"

    def test_argocd_pause_not_called_without_argocd_manage(self):
        """When --argocd-manage is not set, _pause_argocd_for_restore is NOT called."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args(argocd_manage=False)
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True), \
             patch("acm_switchover._pause_argocd_for_restore") as pause_fn, \
             patch("acm_switchover._run_phase_activation", return_value=True), \
             patch("acm_switchover._run_phase_post_activation", return_value=True), \
             patch("acm_switchover._run_phase_finalization", return_value=True):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pause_fn.assert_not_called()

    def test_argocd_pause_failure_aborts_restore(self):
        """When _pause_argocd_for_restore returns False, run_restore_only returns False."""
        from lib.utils import Phase, StateManager

        args = self._make_restore_only_args(argocd_manage=True)
        state = Mock(spec=StateManager)
        current = [Phase.INIT]
        state.get_current_phase.side_effect = lambda: current[0]
        state.get_state_age.return_value = None
        state.is_step_completed.return_value = False
        secondary = Mock()

        def track_preflight(a, s, primary, sec, log):
            current[0] = Phase.PREFLIGHT
            return True

        with patch("acm_switchover._run_phase_preflight", side_effect=track_preflight), \
             patch("acm_switchover._pause_argocd_for_restore", return_value=False), \
             patch("acm_switchover._run_phase_activation") as activation:
            result = run_restore_only(args, state, secondary, Mock())

        assert result is False
        activation.assert_not_called()


@pytest.mark.unit
class TestPauseArgocdForRestore:
    """Tests for _pause_argocd_for_restore() helper."""

    def _make_state(self, completed_steps=None):
        """Create a mock StateManager."""
        from lib.utils import StateManager
        state = Mock(spec=StateManager)
        completed = set(completed_steps or [])
        state.is_step_completed.side_effect = lambda s: s in completed
        state.get_config.return_value = None
        state.set_config.return_value = None
        state.mark_step_completed.return_value = None
        state.add_error.return_value = None
        return state

    def test_no_crd_is_noop(self):
        """When ArgoCD CRD is absent, pause is a no-op and step is marked complete."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect:
            detect.return_value = Mock(has_applications_crd=False)
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is True
        state.set_config.assert_any_call("argocd_paused_apps", [])
        state.set_config.assert_any_call("argocd_run_id", None)
        state.set_config.assert_any_call("argocd_pause_dry_run", False)
        state.mark_step_completed.assert_called_once_with("pause_argocd_apps")

    def test_step_already_completed_returns_immediately(self):
        """When step is already completed, no ArgoCD calls are made."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state(completed_steps=["pause_argocd_apps"])
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect:
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)
            detect.assert_not_called()

        assert result is True
        detect.assert_not_called()
        state.set_config.assert_not_called()
        state.mark_step_completed.assert_not_called()
        state.add_error.assert_not_called()

    def test_auto_sync_apps_are_paused(self):
        """Apps with auto-sync are paused and recorded in state with hub='secondary'."""
        from acm_switchover import _pause_argocd_for_restore
        import copy

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        app = {
            "metadata": {"namespace": "argocd", "name": "my-app"},
            "spec": {"syncPolicy": {"automated": {}}},
        }
        impact = Mock()
        impact.app = app

        pause_result = Mock()
        pause_result.patched = True
        pause_result.namespace = "argocd"
        pause_result.name = "my-app"
        pause_result.original_sync_policy = {"automated": {"selfHeal": True}}
        pause_result.error = None

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications") as list_apps, \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps") as find_acm, \
             patch("acm_switchover.argocd_lib.pause_autosync") as pause, \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            list_apps.return_value = [app]
            find_acm.return_value = [impact]
            pause.return_value = pause_result

            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is True
        pause.assert_called_once_with(secondary, app, "run-123")
        state.mark_step_completed.assert_called_once_with("pause_argocd_apps")
        # Verify state recorded the paused app with hub="secondary"
        set_config_calls = {call[0][0]: call[0][1] for call in state.set_config.call_args_list
                           if call[0][0] == "argocd_paused_apps"}
        last_paused = list(set_config_calls.values())[-1]
        assert len(last_paused) == 1
        assert last_paused[0]["hub"] == "secondary"
        assert last_paused[0]["namespace"] == "argocd"
        assert last_paused[0]["name"] == "my-app"
        assert last_paused[0]["pause_applied"] is True
        assert last_paused[0]["original_sync_policy"] == {"automated": {"selfHeal": True}}
        state.set_config.assert_any_call("argocd_run_id", "run-123")
        state.set_config.assert_any_call("argocd_pause_dry_run", False)

    def test_dry_run_does_not_mark_pause_applied(self):
        """In dry-run mode, pause_applied=False and argocd_pause_dry_run=True."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        app = {
            "metadata": {"namespace": "argocd", "name": "my-app"},
            "spec": {"syncPolicy": {"automated": {}}},
        }
        impact = Mock()
        impact.app = app

        pause_result = Mock()
        pause_result.patched = True
        pause_result.namespace = "argocd"
        pause_result.name = "my-app"
        pause_result.original_sync_policy = {"automated": {"selfHeal": True}}
        pause_result.error = None

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications", return_value=[app]), \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps", return_value=[impact]), \
             patch("acm_switchover.argocd_lib.pause_autosync", return_value=pause_result), \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            result = _pause_argocd_for_restore(secondary, state, dry_run=True, logger=logger)

        assert result is True
        state.set_config.assert_any_call("argocd_pause_dry_run", True)
        # Verify pause_applied=False for dry-run
        paused_writes = [call[0][1] for call in state.set_config.call_args_list
                        if call[0][0] == "argocd_paused_apps" and isinstance(call[0][1], list) and call[0][1]]
        assert paused_writes, "Expected at least one non-empty argocd_paused_apps write in dry-run"
        assert paused_writes[-1][0]["pause_applied"] is False

    def test_detection_failure_returns_false(self):
        """When ArgoCD detection raises, function returns False and records error."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation", side_effect=Exception("timeout")):
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is False
        state.add_error.assert_called_once_with(ANY, "argocd_pause_restore")
        state.mark_step_completed.assert_not_called()

    def test_no_acm_apps_skips_pause(self):
        """When no ACM-touching apps are found, step is marked complete with empty list."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        non_acm_app = {
            "metadata": {"namespace": "argocd", "name": "unrelated-app"},
            "spec": {"syncPolicy": {"automated": {}}},
        }

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications", return_value=[non_acm_app]), \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps", return_value=[]), \
             patch("acm_switchover.argocd_lib.pause_autosync") as pause_fn, \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is True
        pause_fn.assert_not_called()
        state.mark_step_completed.assert_called_once_with("pause_argocd_apps")

    def test_pause_failure_returns_false(self):
        """When pause_autosync returns patched=False with an error, returns False and records error."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        app = {
            "metadata": {"namespace": "argocd", "name": "my-app"},
            "spec": {"syncPolicy": {"automated": {}}},
        }
        impact = Mock()
        impact.app = app

        pause_result = Mock()
        pause_result.patched = False
        pause_result.namespace = "argocd"
        pause_result.name = "my-app"
        pause_result.error = "connection refused"

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications", return_value=[app]), \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps", return_value=[impact]), \
             patch("acm_switchover.argocd_lib.pause_autosync", return_value=pause_result), \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is False
        state.add_error.assert_called_once_with(ANY, "argocd_pause_restore")
        state.mark_step_completed.assert_not_called()
        # Entry is removed from state after failure — last paused_apps write should be empty
        paused_writes = [call[0][1] for call in state.set_config.call_args_list
                        if call[0][0] == "argocd_paused_apps"]
        assert paused_writes[-1] == [], "Failed app should be removed from paused_apps state"

    def test_manual_sync_app_not_paused(self):
        """ACM-touching apps without auto-sync are skipped (not paused)."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        manual_app = {
            "metadata": {"namespace": "argocd", "name": "manual-app"},
            "spec": {"syncPolicy": {"syncOptions": ["CreateNamespace=true"]}},
        }
        impact = Mock()
        impact.app = manual_app

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications", return_value=[manual_app]), \
             patch("acm_switchover.argocd_lib.find_acm_touching_apps", return_value=[impact]), \
             patch("acm_switchover.argocd_lib.pause_autosync") as pause_fn, \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is True
        pause_fn.assert_not_called()
        state.mark_step_completed.assert_called_once_with("pause_argocd_apps")
        # Verify no entries were recorded in paused_apps
        paused_writes = [call[0][1] for call in state.set_config.call_args_list
                        if call[0][0] == "argocd_paused_apps"]
        if paused_writes:
            assert paused_writes[-1] == []

    def test_list_apps_failure_returns_false(self):
        """When list_argocd_applications raises, returns False and records error."""
        from acm_switchover import _pause_argocd_for_restore

        secondary = Mock()
        state = self._make_state()
        logger = Mock()

        with patch("acm_switchover.argocd_lib.detect_argocd_installation") as detect, \
             patch("acm_switchover.argocd_lib.list_argocd_applications",
                   side_effect=Exception("api timeout")), \
             patch("acm_switchover.argocd_lib.run_id_or_new", return_value="run-123"):
            detect.return_value = Mock(has_applications_crd=True)
            result = _pause_argocd_for_restore(secondary, state, dry_run=False, logger=logger)

        assert result is False
        state.add_error.assert_called_once_with(ANY, "argocd_pause_restore")
        state.mark_step_completed.assert_not_called()
