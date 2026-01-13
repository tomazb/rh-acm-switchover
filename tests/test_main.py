"""Unit tests for acm_switchover.py (main script).

Tests argument parsing and basic entry point logic.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import parse_args, run_switchover


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

        # Create a stale state file (older than 5 minutes)
        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))

        # Set to COMPLETED with stale timestamp (use _write_state to preserve timestamp)
        state.state["current_phase"] = Phase.COMPLETED.value
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
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
        assert state_age.total_seconds() > 300  # > 5 minutes

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
