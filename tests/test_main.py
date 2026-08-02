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
from kubernetes.client.rest import ApiException

# Add parent to path to import modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import (
    _attempt_argocd_resume_on_failure,
    _bind_runtime_hub_identities,
    _execute_operation,
    _fail_phase,
    _fail_unexpected_phase_state,
    _initialize_clients,
    _missing_parse_required_args,
    _phase_report_from_state,
    _prepare_argocd_resume_clients,
    _prepare_runtime,
    _report_argocd_acm_impact,
    _report_target,
    _run_argocd_resume_only,
    _run_phase_activation,
    _run_phase_finalization,
    _run_phase_post_activation,
    _run_phase_preflight,
    _run_phase_primary_prep,
    _run_restore_only_argocd_pause,
    _run_restore_only_impl,
    _run_switchover_impl,
    _write_python_report,
    main,
    parse_args,
    run_decommission,
    run_restore_only,
    run_setup,
    run_switchover,
    validate_args,
)
from lib import KubeClient
from lib import argocd as argocd_lib
from lib.argocd_register import PauseSummary
from lib.constants import (
    DRY_RUN_RESTORE_ONLY_COMPLETION_MESSAGE,
    DRY_RUN_RESTORE_ONLY_NEXT_STEPS_MESSAGE,
    DRY_RUN_SWITCHOVER_COMPLETION_MESSAGE,
    DRY_RUN_SWITCHOVER_NEXT_STEPS_MESSAGE,
    EXIT_FAILURE,
    EXIT_INTERRUPT,
    EXIT_SUCCESS,
    EXPECTED_MANAGED_CLUSTER_COUNT_KEY,
    EXPECTED_MANAGED_CLUSTER_NAMES_KEY,
    HUB_ROLE_SECONDARY,
    MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT,
    MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED,
    MANAGED_CLUSTER_EXPECTATION_KEY,
    MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY,
    RESTORE_ONLY_COMPLETED_SUCCESS_MESSAGE,
    STEP_PAUSE_ARGOCD_APPS,
    SWITCHOVER_COMPLETED_SUCCESS_MESSAGE,
    TOKEN_DURATION_DEFAULT,
)
from lib.exceptions import SwitchoverError
from lib.validation import ValidationError
from tests.main_test_helpers import make_restore_only_args, make_switchover_args


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

    def test_required_args_still_apply_to_decommission(self):
        """Decommission is not a standalone mode; method and old-hub-action remain required."""
        with patch("sys.argv", ["script.py", "--decommission", "--primary-context", "old-hub"]):
            with pytest.raises(SystemExit):
                parse_args()

    def test_restore_only_abbreviation_does_not_require_primary_or_method(self):
        """Argparse abbreviation handling must run before standalone required-argument checks."""
        with patch("sys.argv", ["script.py", "--restore-on", "--secondary-context", "secondary"]):
            args = parse_args()

        assert args.restore_only is True
        assert args.primary_context is None
        assert args.method is None
        assert args.old_hub_action is None

    def test_argocd_resume_only_abbreviation_does_not_require_primary_or_method(self):
        """Argparse-recognized resume-only abbreviations should not be blocked by a pre-scan."""
        with patch("sys.argv", ["script.py", "--argocd-resume-onl", "--secondary-context", "secondary"]):
            args = parse_args()

        assert args.argocd_resume_only is True
        assert args.primary_context is None
        assert args.method is None
        assert args.old_hub_action is None

    def test_help_marks_conditionally_required_args(self, capsys):
        """Help text should describe mode-specific required arguments after parser-level required= removal."""
        with patch("sys.argv", ["script.py", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()

        assert exc_info.value.code == 0
        help_text = " ".join(capsys.readouterr().out.split()).replace("--restore- only", "--restore-only")
        assert "Kubernetes context for primary hub (required unless --restore-only/--argocd-resume-only)" in help_text
        assert "Kubernetes context for secondary hub (required except --decommission/--setup)" in help_text
        assert (
            "Switchover method: passive (continuous sync) or full (one-time restore) "
            "(required unless --setup/--restore-only/--argocd-resume-only)"
        ) in help_text
        assert "Action for old primary hub after switchover (required unless" in help_text

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

    def test_argocd_resume_only_parses_without_primary_context(self):
        """Standalone resume-only mode must allow restore-only follow-up without a dummy primary context."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--secondary-context",
                "p2",
                "--argocd-resume-only",
            ],
        ):
            args = parse_args()
            assert args.argocd_resume_only is True
            assert args.primary_context is None
            assert args.secondary_context == "p2"

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
            assert args.token_duration == TOKEN_DURATION_DEFAULT

    def test_missing_parse_required_args_tolerates_partial_namespace(self):
        """Helper callers using partial namespaces should get missing args, not AttributeError."""
        args = SimpleNamespace(primary_context=None)

        assert _missing_parse_required_args(args) == [
            "--primary-context",
            "--method",
            "--old-hub-action",
        ]

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

    def test_report_dir_parses_for_python_artifacts(self):
        """Python CLI should expose a report directory for machine-readable artifacts."""
        with patch(
            "sys.argv",
            [
                "script.py",
                "--primary-context",
                "p1",
                "--secondary-context",
                "p2",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
                "--report-dir",
                "./artifacts/run-1",
            ],
        ):
            args = parse_args()
            assert args.report_dir == "./artifacts/run-1"

    def test_min_managed_clusters_omitted_is_distinct_from_explicit_zero(self):
        """Omitted --min-managed-clusters derives from preflight; explicit 0 opts out."""
        base_argv = [
            "script.py",
            "--primary-context",
            "p1",
            "--secondary-context",
            "p2",
            "--method",
            "passive",
            "--old-hub-action",
            "secondary",
        ]

        with patch("sys.argv", base_argv):
            args = parse_args()
            assert args.min_managed_clusters is None

        with patch("sys.argv", base_argv + ["--min-managed-clusters", "0"]):
            args = parse_args()
            assert args.min_managed_clusters == 0

    def test_validate_args_warns_when_argocd_manage_has_no_effect_in_validate_only(
        self,
    ):
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

        with pytest.raises(SwitchoverError, match="Use --force to proceed with stale state"):
            run_switchover(args, reloaded, Mock(), Mock(), Mock())

    def test_malformed_last_updated_treated_as_stale_requires_force(self, tmp_path):
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

        with pytest.raises(SwitchoverError, match="Use --force to proceed with stale state"):
            run_switchover(args, reloaded, Mock(), Mock(), Mock())

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
        with pytest.raises(SwitchoverError, match="Use --force to proceed with stale state"):
            run_switchover(args2, reloaded2, Mock(), Mock(), Mock())

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

    @staticmethod
    def _successful_phase(next_phase):
        def handler(_args, phase_state, *_rest):
            phase_state.set_phase(next_phase)
            return True

        return handler

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

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
        ) as preflight, patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=self._successful_phase(Phase.PRIMARY_PREP),
        ) as primary_prep, patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ) as activation, patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
        ) as finalization:
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        preflight.assert_called_once()
        primary_prep.assert_called_once()
        activation.assert_called_once()
        post_activation.assert_called_once()
        finalization.assert_called_once()

    def test_run_switchover_fails_when_successful_handler_leaves_wrong_phase(self, tmp_path):
        """A True handler result must not allow COMPLETED when durable phase state is wrong."""
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
            argocd_resume_on_failure=True,
        )

        def bad_preflight(_args, phase_state, *_rest):
            phase_state.set_phase(Phase.INIT)
            return True

        with patch("acm_switchover._run_phase_preflight", side_effect=bad_preflight), patch(
            "acm_switchover._attempt_argocd_resume_on_failure"
        ) as resume_on_failure:
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is False
        assert state.get_current_phase() == Phase.FAILED
        assert state.get_last_error_phase() == Phase.PREFLIGHT
        assert "expected phase" in state.get_errors()[-1]["error"]
        resume_on_failure.assert_called_once()

    def test_run_switchover_valid_resume_paths_complete_with_expected_phases(self, tmp_path):
        """Phase assertions must not block legitimate mid-flow resume states."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.PRIMARY_PREP)
        args = SimpleNamespace(
            force=False,
            validate_only=False,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
        )

        with patch("acm_switchover._run_phase_preflight") as preflight, patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=self._successful_phase(Phase.PRIMARY_PREP),
        ) as primary_prep, patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ) as activation, patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
        ) as finalization:
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        preflight.assert_not_called()
        primary_prep.assert_called_once()
        activation.assert_called_once()
        post_activation.assert_called_once()
        finalization.assert_called_once()

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
        ) as primary_prep, patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ) as activation, patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
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

    def test_run_switchover_dry_run_restores_original_state(self, tmp_path):
        """Dry-run full switchover must not persist resume/checkpoint progress."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_config("operator_note", "keep")
        original_timestamp = state.state["last_updated"]

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            dry_run=True,
            state_file=str(state_file),
            method="passive",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="secondary",
            argocd_manage=False,
        )

        def _complete_phase(next_phase, step_name):
            def _handler(_args, phase_state, *_rest):
                phase_state.set_phase(next_phase)
                phase_state.mark_step_completed(step_name)
                phase_state.set_config("dry_run_only", step_name)
                return True

            return _handler

        logger = Mock()
        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=_complete_phase(Phase.PREFLIGHT, "dry_preflight"),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=_complete_phase(Phase.PRIMARY_PREP, "dry_primary_prep"),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=_complete_phase(Phase.ACTIVATION, "dry_activation"),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=_complete_phase(Phase.POST_ACTIVATION, "dry_post_activation"),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=_complete_phase(Phase.FINALIZATION, "dry_finalization"),
        ):
            assert run_switchover(args, state, Mock(), Mock(), logger) is True

        reloaded = StateManager(str(state_file))
        assert reloaded.get_current_phase() == Phase.INIT
        assert reloaded.get_config("operator_note") == "keep"
        assert reloaded.get_config("dry_run_only") is None
        assert reloaded.state["completed_steps"] == []
        assert reloaded.state["last_updated"] == original_timestamp
        log_text = "\n".join(str(call.args[0]) for call in logger.info.call_args_list if call.args)
        assert DRY_RUN_SWITCHOVER_COMPLETION_MESSAGE in log_text
        assert DRY_RUN_SWITCHOVER_NEXT_STEPS_MESSAGE in log_text
        assert SWITCHOVER_COMPLETED_SUCCESS_MESSAGE not in log_text

    def test_run_restore_only_dry_run_restores_original_state(self, tmp_path):
        """Dry-run restore-only must not persist resume/checkpoint progress."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_config("restore_note", "keep")
        original_timestamp = state.state["last_updated"]

        args = SimpleNamespace(
            force=False,
            validate_only=False,
            dry_run=True,
            state_file=str(state_file),
            method="full",
            skip_rbac_validation=True,
            skip_observability_checks=False,
            old_hub_action="none",
            argocd_manage=False,
            restore_only=True,
        )

        def _complete_phase(next_phase, step_name):
            def _handler(_args, phase_state, *_rest):
                phase_state.set_phase(next_phase)
                phase_state.mark_step_completed(step_name)
                phase_state.set_config("dry_run_only", step_name)
                return True

            return _handler

        logger = Mock()
        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=_complete_phase(Phase.PREFLIGHT, "dry_preflight"),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=_complete_phase(Phase.ACTIVATION, "dry_activation"),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=_complete_phase(Phase.POST_ACTIVATION, "dry_post_activation"),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=_complete_phase(Phase.FINALIZATION, "dry_finalization"),
        ):
            assert run_restore_only(args, state, Mock(), logger) is True

        reloaded = StateManager(str(state_file))
        assert reloaded.get_current_phase() == Phase.INIT
        assert reloaded.get_config("restore_note") == "keep"
        assert reloaded.get_config("dry_run_only") is None
        assert reloaded.state["completed_steps"] == []
        assert reloaded.state["last_updated"] == original_timestamp
        log_text = "\n".join(str(call.args[0]) for call in logger.info.call_args_list if call.args)
        assert DRY_RUN_RESTORE_ONLY_COMPLETION_MESSAGE in log_text
        assert DRY_RUN_RESTORE_ONLY_NEXT_STEPS_MESSAGE in log_text
        assert RESTORE_ONLY_COMPLETED_SUCCESS_MESSAGE not in log_text

    def test_restore_only_argocd_dry_run_pause_uses_register_without_marking_step(self):
        args = SimpleNamespace(argocd_manage=True, dry_run=True)
        state = Mock()
        state.is_step_completed.return_value = False
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.ArgocdPauseRegister") as coordinator_class:
            coordinator = coordinator_class.return_value
            coordinator.pause_hubs.return_value = PauseSummary(newly_paused=1, run_id="run-1")
            state.get_config.return_value = "run-1"

            result = _run_restore_only_argocd_pause(args, state, None, secondary, logger)

        assert result is True
        coordinator_class.assert_called_once_with(state, dry_run=True)
        coordinator.pause_hubs.assert_called_once_with([(secondary, HUB_ROLE_SECONDARY)])
        state.mark_step_completed.assert_not_called()

    def test_restore_only_argocd_dry_run_pause_reports_run_id_from_summary(self):
        """G1: dry-run persists no run id, so the report must come from the summary."""
        args = SimpleNamespace(argocd_manage=True, dry_run=True)
        state = Mock()
        state.is_step_completed.return_value = False
        state.get_config.return_value = None
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.ArgocdPauseRegister") as coordinator_class:
            coordinator = coordinator_class.return_value
            coordinator.pause_hubs.return_value = PauseSummary(newly_paused=2, run_id="run-9", dry_run=True)

            result = _run_restore_only_argocd_pause(args, state, None, secondary, logger)

        assert result is True
        coordinator.status.assert_not_called()
        messages = [call.args[0] % call.args[1:] for call in logger.info.call_args_list if call.args]
        summary_lines = [message for message in messages if message.startswith("Argo CD: ")]
        assert len(summary_lines) == 1
        assert "2 Application(s) would be paused" in summary_lines[0]
        assert "run_id=run-9" in summary_lines[0]

    def test_restore_only_argocd_pause_phrases_from_summary_dry_run(self):
        """G2: the caller reports the mode the register ran in, not its own flag."""
        args = SimpleNamespace(argocd_manage=True, dry_run=False)
        state = Mock()
        state.is_step_completed.return_value = False
        state.get_config.return_value = None
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.ArgocdPauseRegister") as coordinator_class:
            coordinator = coordinator_class.return_value
            coordinator.pause_hubs.return_value = PauseSummary(newly_paused=1, run_id="run-9", dry_run=True)

            assert _run_restore_only_argocd_pause(args, state, None, secondary, logger) is True

        messages = [call.args[0] % call.args[1:] for call in logger.info.call_args_list if call.args]
        assert any("would be paused" in message for message in messages)

    def test_restore_only_argocd_dry_run_pause_fails_on_blockers(self):
        args = SimpleNamespace(argocd_manage=True, dry_run=True)
        state = Mock()
        state.is_step_completed.return_value = False
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.ArgocdPauseRegister") as coordinator_class, patch(
            "acm_switchover._fail_phase",
            return_value=False,
        ) as fail_phase:
            coordinator = coordinator_class.return_value
            coordinator.pause_hubs.return_value = PauseSummary(blocked=1)

            result = _run_restore_only_argocd_pause(args, state, None, secondary, logger)

        assert result is False
        coordinator_class.assert_called_once_with(state, dry_run=True)
        coordinator.pause_hubs.assert_called_once_with([(secondary, HUB_ROLE_SECONDARY)])
        fail_phase.assert_called_once_with(
            state,
            "Argo CD auto-sync pause blocked for 1 Application(s); pause the owning ApplicationSet first",
            logger,
        )
        state.mark_step_completed.assert_not_called()

    def test_restore_only_argocd_pause_defaults_missing_dry_run_to_false(self):
        args = SimpleNamespace(argocd_manage=True)
        state = Mock()
        state.is_step_completed.return_value = False
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.ArgocdPauseRegister") as coordinator_class:
            coordinator = coordinator_class.return_value
            coordinator.pause_hubs.return_value = PauseSummary(newly_paused=1, run_id="run-1")

            result = _run_restore_only_argocd_pause(args, state, None, secondary, logger)

        assert result is True
        coordinator_class.assert_called_once_with(state, dry_run=False)
        coordinator.pause_hubs.assert_called_once_with([(secondary, HUB_ROLE_SECONDARY)])
        state.mark_step_completed.assert_called_once_with(STEP_PAUSE_ARGOCD_APPS)

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
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
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
        ) as primary_prep, patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ) as activation, patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ) as post_activation, patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
        ) as finalization:
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        preflight.assert_not_called()
        primary_prep.assert_not_called()
        activation.assert_called_once()
        post_activation.assert_called_once()
        finalization.assert_called_once()

    def test_run_switchover_failed_state_without_error_phase_requires_force(self, tmp_path):
        """FAILED state without determinable error phase should raise a domain error."""
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

        with pytest.raises(SwitchoverError, match="Use --force to reset state and retry"):
            run_switchover(args, state, Mock(), Mock(), Mock())

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

        with pytest.raises(SwitchoverError, match="Use --force to reset state and retry"):
            run_switchover(args, state, Mock(), Mock(), Mock())

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

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
        ) as preflight, patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=self._successful_phase(Phase.PRIMARY_PREP),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
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

    def test_run_switchover_validate_only_preflight_failure_preserves_failed_retry_phase(self, tmp_path):
        """A validate-only failure from FAILED state must not replace the durable retry phase."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("post activation failed", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)
        original_errors = list(state.get_errors())

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

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (False, {})
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is False
        assert state.get_current_phase() == Phase.FAILED
        assert state.get_errors() == original_errors
        assert state.get_last_error_phase() == Phase.POST_ACTIVATION

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

    def test_fail_phase_skips_generic_when_module_already_recorded_same_phase_error(
        self,
    ):
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

    def test_fail_phase_appends_wrapper_after_retry_when_last_error_is_stale_same_phase(
        self,
    ):
        state = Mock()
        state.get_current_phase.return_value = SimpleNamespace(value="preflight_validation")
        state.get_errors.return_value = [{"phase": "preflight_validation", "error": "old failure"}]
        state.get_retry_error_baseline.return_value = {
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
class TestOperationRunnerDelegation:
    def test_run_switchover_impl_delegates_with_current_phase_hooks(self):
        args = make_switchover_args()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.operation_runners.run_switchover_impl", return_value=True) as impl:
            result = _run_switchover_impl(args, state, primary, secondary, logger)

        assert result is True
        hooks = impl.call_args.kwargs["hooks"]
        assert hooks.preflight_handler is _run_phase_preflight
        assert hooks.primary_prep_handler is _run_phase_primary_prep
        assert hooks.activation_handler is _run_phase_activation
        assert hooks.post_activation_handler is _run_phase_post_activation
        assert hooks.finalization_handler is _run_phase_finalization
        assert hooks.fail_phase is _fail_phase
        assert hooks.fail_unexpected_phase_state is _fail_unexpected_phase_state
        assert hooks.on_phase_failure is _attempt_argocd_resume_on_failure

    def test_run_restore_only_impl_delegates_with_restore_only_pause_hook(self):
        args = make_restore_only_args()
        state = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.operation_runners.run_restore_only_impl", return_value=True) as impl:
            result = _run_restore_only_impl(args, state, secondary, logger)

        assert result is True
        hooks = impl.call_args.kwargs["hooks"]
        assert hooks.preflight_handler is _run_phase_preflight
        assert hooks.restore_only_pause_handler is _run_restore_only_argocd_pause
        assert hooks.activation_handler is _run_phase_activation
        assert hooks.post_activation_handler is _run_phase_post_activation
        assert hooks.finalization_handler is _run_phase_finalization
        assert hooks.fail_phase is _fail_phase
        assert hooks.fail_unexpected_phase_state is _fail_unexpected_phase_state
        assert hooks.on_phase_failure is _attempt_argocd_resume_on_failure

    def test_execute_operation_delegates_with_current_dispatch_hooks(self):
        args = make_switchover_args()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.operation_runners.execute_operation", return_value=True) as dispatch:
            result = _execute_operation(args, state, primary, secondary, logger)

        assert result is True
        hooks = dispatch.call_args.kwargs["hooks"]
        assert hooks.decommission_runner is run_decommission
        assert hooks.restore_only_runner is run_restore_only
        assert hooks.switchover_runner is run_switchover


@pytest.mark.unit
class TestArgocdResumeDelegation:
    def test_prepare_argocd_resume_clients_delegates_to_lib_module(self):
        args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=False, force=False)
        state = Mock()
        paused_hub_roles = {HUB_ROLE_SECONDARY}
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch(
            "acm_switchover.argocd_resume.prepare_argocd_resume_clients",
            return_value=(primary, secondary),
        ) as prepare_clients:
            result = _prepare_argocd_resume_clients(
                args,
                state,
                paused_hub_roles,
                primary,
                secondary,
                logger,
                allow_primary_load_from_state=True,
            )

        assert result == (primary, secondary)
        prepare_clients.assert_called_once_with(
            args,
            state,
            paused_hub_roles,
            primary,
            secondary,
            logger,
            allow_primary_load_from_state=True,
            kube_client_factory=KubeClient,
        )

    def test_run_argocd_resume_only_delegates_to_lib_module(self):
        args = SimpleNamespace()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch("acm_switchover.argocd_resume.run_argocd_resume_only", return_value=True) as run_resume:
            result = _run_argocd_resume_only(args, state, primary, secondary, logger)

        assert result is True
        run_resume.assert_called_once_with(args, state, primary, secondary, logger, kube_client_factory=KubeClient)

    def test_attempt_argocd_resume_on_failure_delegates_to_lib_module(self):
        args = SimpleNamespace(argocd_resume_on_failure=True, restore_only=False, force=False)
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()

        with patch(
            "acm_switchover.argocd_resume.attempt_argocd_resume_on_failure",
            return_value=None,
        ) as attempt_resume:
            _attempt_argocd_resume_on_failure(args, state, primary, secondary, logger)

        attempt_resume.assert_called_once_with(
            args,
            state,
            primary,
            secondary,
            logger,
            kube_client_factory=KubeClient,
        )


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

    def test_main_prints_gitops_report_on_identity_binding_exception(self):
        args = self._base_args()
        logger = Mock()
        state = Mock()
        collector = Mock()
        runtime = SimpleNamespace(
            state=state,
            primary=Mock(),
            secondary=Mock(),
            should_bind_state=True,
            should_record_state_errors=True,
        )

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value="state.json"
        ), patch(
            "acm_switchover._prepare_runtime", return_value=runtime
        ), patch(
            "acm_switchover._bind_runtime_hub_identities", side_effect=RuntimeError("identity boom")
        ), patch(
            "acm_switchover._execute_operation"
        ) as execute_operation, patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_FAILURE
        execute_operation.assert_not_called()
        collector.print_report.assert_called_once()
        state.add_error.assert_called_once_with("identity boom")

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

    def test_main_skips_state_enforcement_for_decommission(self):
        args = self._base_args()
        args.decommission = True
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
            "acm_switchover._initialize_clients", return_value=(Mock(), None)
        ), patch(
            "acm_switchover._execute_operation", return_value=True
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        state.ensure_contexts.assert_not_called()
        state.ensure_hub_identities.assert_not_called()

    def test_main_does_not_record_decommission_exceptions_in_state(self):
        args = self._base_args()
        args.decommission = True
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
            "acm_switchover._initialize_clients", return_value=(Mock(), None)
        ), patch(
            "acm_switchover._execute_operation", side_effect=RuntimeError("boom")
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_FAILURE
        collector.print_report.assert_called_once()
        state.add_error.assert_not_called()

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

    def test_main_resume_only_uses_unique_secondary_matched_state_file_without_primary_context(
        self, tmp_path, monkeypatch
    ):
        args = self._base_args()
        args.argocd_resume_only = True
        args.state_file = None
        args.primary_context = None
        args.secondary_context = "secondary-b"
        logger = Mock()
        state = Mock()
        collector = Mock()
        state_path = tmp_path / "switchover-primary-a__secondary-b.json"
        state_path.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path))

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover.StateManager", return_value=state
        ) as state_manager, patch(
            "acm_switchover._initialize_clients", return_value=(None, Mock())
        ), patch(
            "acm_switchover._run_argocd_resume_only", return_value=True
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance", return_value=collector
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        state_manager.assert_called_once_with(str(state_path))
        assert args.state_file == str(state_path)

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

    def test_resolve_state_file_resume_only_without_primary_context_rejects_ambiguous_candidates(
        self, tmp_path, monkeypatch
    ):
        from acm_switchover import _resolve_state_file

        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path))
        (tmp_path / "switchover-primary-a__secondary-b.json").write_text("{}", encoding="utf-8")
        (tmp_path / "switchover-restore-only__secondary-b.json").write_text("{}", encoding="utf-8")

        with pytest.raises(ValueError, match="Multiple candidate state files found"):
            _resolve_state_file(
                requested_path=None,
                primary_ctx=None,
                secondary_ctx="secondary-b",
                argocd_resume_only=True,
            )

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
class TestPrepareRuntime:
    def test_prepare_runtime_binds_contexts_and_returns_runtime_objects(self):
        args = TestMainGitOpsReporting._base_args()
        logger = Mock()
        state = Mock()
        primary = Mock()
        secondary = Mock()

        with patch("acm_switchover.StateManager", return_value=state), patch(
            "acm_switchover._initialize_clients", return_value=(primary, secondary)
        ):
            runtime = _prepare_runtime(args, logger, "state.json")

        assert runtime.state is state
        assert runtime.primary is primary
        assert runtime.secondary is secondary
        assert runtime.should_bind_state is True
        assert runtime.should_record_state_errors is True
        state.ensure_contexts.assert_called_once_with("primary", "secondary")
        state.ensure_hub_identities.assert_not_called()

    def test_prepare_runtime_tolerates_missing_secondary_context_attribute(self):
        args = SimpleNamespace(
            primary_context="primary",
            dry_run=False,
            validate_only=False,
            argocd_resume_only=False,
            decommission=False,
            force=False,
            reset_state=False,
        )
        logger = Mock()
        state = Mock()

        with patch("acm_switchover.StateManager", return_value=state), patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), None)
        ):
            runtime = _prepare_runtime(args, logger, "state.json")

        assert runtime.should_bind_state is True
        state.ensure_contexts.assert_called_once_with("primary", None)
        state.ensure_hub_identities.assert_not_called()

    def test_prepare_runtime_removes_existing_state_file_before_state_manager(self, tmp_path):
        args = TestMainGitOpsReporting._base_args()
        args.reset_state = True
        state_path = tmp_path / "state.json"
        state_path.write_text("{}", encoding="utf-8")
        logger = Mock()
        state = Mock()

        with patch("acm_switchover.StateManager", return_value=state) as state_manager, patch(
            "acm_switchover._initialize_clients", return_value=(Mock(), Mock())
        ):
            runtime = _prepare_runtime(args, logger, str(state_path))

        assert runtime.state_file == str(state_path)
        assert not state_path.exists()
        state_manager.assert_called_once_with(str(state_path))


@pytest.mark.unit
class TestBindRuntimeHubIdentities:
    @staticmethod
    def _clients():
        primary = Mock()
        secondary = Mock()
        primary.get_cluster_identity.return_value = {"context": "primary", "cluster_uid": "uid-primary"}
        secondary.get_cluster_identity.return_value = {"context": "secondary", "cluster_uid": "uid-secondary"}
        return primary, secondary

    def test_bind_runtime_hub_identities_persists_by_default(self):
        args = TestMainGitOpsReporting._base_args()
        state = Mock()
        primary, secondary = self._clients()

        _bind_runtime_hub_identities(args, state, primary, secondary)

        state.ensure_hub_identities.assert_called_once_with(
            {
                "primary": {"context": "primary", "cluster_uid": "uid-primary"},
                "secondary": {"context": "secondary", "cluster_uid": "uid-secondary"},
            },
            allow_legacy_backfill=False,
            persist=True,
        )

    def test_bind_runtime_hub_identities_uses_non_persistent_binding_for_dry_run(self):
        args = TestMainGitOpsReporting._base_args()
        args.dry_run = True
        state = Mock()
        primary, secondary = self._clients()

        _bind_runtime_hub_identities(args, state, primary, secondary)

        state.ensure_hub_identities.assert_called_once_with(
            {
                "primary": {"context": "primary", "cluster_uid": "uid-primary"},
                "secondary": {"context": "secondary", "cluster_uid": "uid-secondary"},
            },
            allow_legacy_backfill=False,
            persist=False,
        )

    def test_bind_runtime_hub_identities_uses_non_persistent_binding_for_validate_only(self):
        args = TestMainGitOpsReporting._base_args()
        args.validate_only = True
        state = Mock()
        primary, secondary = self._clients()

        _bind_runtime_hub_identities(args, state, primary, secondary)

        state.ensure_hub_identities.assert_called_once_with(
            {
                "primary": {"context": "primary", "cluster_uid": "uid-primary"},
                "secondary": {"context": "secondary", "cluster_uid": "uid-secondary"},
            },
            allow_legacy_backfill=False,
            persist=False,
        )


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
    def test_preflight_records_critical_result_when_expected_cluster_inventory_fails(
        self,
    ):
        from modules.preflight import ValidationReporter
        from modules.preflight_coordinator import PreflightValidator

        validator = PreflightValidator.__new__(PreflightValidator)
        validator.restore_only = False
        validator.primary = Mock()
        validator.primary.list_custom_resources.side_effect = ApiException(status=403, reason="Forbidden")
        validator.reporter = ValidationReporter()

        assert validator._derive_expected_managed_cluster_names() == []
        failures = validator.reporter.critical_failures()
        assert len(failures) == 1
        assert failures[0]["check"] == "ManagedCluster inventory"
        assert "403 Forbidden" in failures[0]["message"]

    def test_run_phase_preflight_persists_expected_managed_clusters_from_primary(self):
        args = SimpleNamespace(
            method="passive",
            old_hub_action="secondary",
            skip_rbac_validation=False,
            argocd_manage=False,
            skip_gitops_check=True,
            skip_observability_checks=False,
            validate_only=False,
            restore_only=False,
            min_managed_clusters=None,
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
            "expected_managed_cluster_names": ["cluster-a", "cluster-b"],
            "expected_managed_cluster_count": 2,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = _run_phase_preflight(args, state, primary, secondary, logger)

        assert result is True
        state.set_config.assert_any_call(EXPECTED_MANAGED_CLUSTER_NAMES_KEY, ["cluster-a", "cluster-b"])
        state.set_config.assert_any_call(EXPECTED_MANAGED_CLUSTER_COUNT_KEY, 2)
        state.set_config.assert_any_call(
            MANAGED_CLUSTER_EXPECTATION_KEY,
            MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT,
        )

    def test_restore_only_preflight_persists_empty_expected_managed_clusters(self):
        args = SimpleNamespace(
            method="full",
            old_hub_action=None,
            skip_rbac_validation=False,
            argocd_manage=False,
            skip_gitops_check=True,
            skip_observability_checks=False,
            validate_only=False,
            restore_only=True,
            min_managed_clusters=None,
        )
        state = Mock()
        secondary = Mock()
        config = {
            "primary_version": "unknown",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
            "has_observability": False,
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator_class.return_value.validate_all.return_value = (True, config)
            result = _run_phase_preflight(args, state, None, secondary, Mock())

        assert result is True
        state.set_config.assert_any_call(EXPECTED_MANAGED_CLUSTER_NAMES_KEY, [])
        state.set_config.assert_any_call(EXPECTED_MANAGED_CLUSTER_COUNT_KEY, 0)
        state.set_config.assert_any_call(
            MANAGED_CLUSTER_EXPECTATION_KEY,
            MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY,
        )

    def test_run_phase_activation_uses_derived_expected_count_when_min_omitted(self):
        args = SimpleNamespace(
            method="passive",
            activation_method="patch",
            manage_auto_import_strategy=False,
            old_hub_action="secondary",
            min_managed_clusters=None,
        )
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "expected_managed_cluster_names": ["cluster-a", "cluster-b"],
            "expected_managed_cluster_count": 2,
            MANAGED_CLUSTER_EXPECTATION_KEY: MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT,
        }.get(key, default)
        secondary = Mock()

        with patch("acm_switchover.SecondaryActivation") as activation_class:
            activation_class.return_value.activate.return_value = True
            assert _run_phase_activation(args, state, None, secondary, Mock()) is True

        activation_class.assert_called_once()
        kwargs = activation_class.call_args.kwargs
        assert kwargs["min_managed_clusters"] == 2
        assert kwargs["expected_managed_cluster_names"] == ["cluster-a", "cluster-b"]
        assert kwargs["enforce_expected_managed_cluster_names"] is True

    def test_run_phase_activation_preserves_explicit_zero_opt_out(self):
        args = SimpleNamespace(
            method="passive",
            activation_method="patch",
            manage_auto_import_strategy=False,
            old_hub_action="secondary",
            min_managed_clusters=0,
        )
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "expected_managed_cluster_names": ["cluster-a"],
            "expected_managed_cluster_count": 1,
            MANAGED_CLUSTER_EXPECTATION_KEY: MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED,
        }.get(key, default)
        secondary = Mock()

        with patch("acm_switchover.SecondaryActivation") as activation_class:
            activation_class.return_value.activate.return_value = True
            assert _run_phase_activation(args, state, None, secondary, Mock()) is True

        kwargs = activation_class.call_args.kwargs
        assert kwargs["min_managed_clusters"] == 0
        assert kwargs["expected_managed_cluster_names"] == []
        assert kwargs["enforce_expected_managed_cluster_names"] is False

    def test_run_phase_activation_restore_only_defaults_to_one_when_min_omitted(self):
        args = SimpleNamespace(
            method="full",
            activation_method="patch",
            manage_auto_import_strategy=False,
            old_hub_action=None,
            min_managed_clusters=None,
        )
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "expected_managed_cluster_names": [],
            "expected_managed_cluster_count": 0,
            MANAGED_CLUSTER_EXPECTATION_KEY: MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY,
        }.get(key, default)
        secondary = Mock()

        with patch("acm_switchover.SecondaryActivation") as activation_class:
            activation_class.return_value.activate.return_value = True
            assert _run_phase_activation(args, state, None, secondary, Mock()) is True

        kwargs = activation_class.call_args.kwargs
        assert kwargs["min_managed_clusters"] == 1
        assert kwargs["expected_managed_cluster_names"] == []
        assert kwargs["enforce_expected_managed_cluster_names"] is False

    def test_run_phase_post_activation_restore_only_defaults_to_one_when_min_omitted(
        self,
    ):
        args = SimpleNamespace(
            dry_run=False,
            min_managed_clusters=None,
        )
        state = Mock()
        state.get_config.side_effect = lambda key, default=None: {
            "expected_managed_cluster_names": [],
            "expected_managed_cluster_count": 0,
            MANAGED_CLUSTER_EXPECTATION_KEY: MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY,
            "secondary_has_observability": False,
        }.get(key, default)
        secondary = Mock()

        with patch("acm_switchover.PostActivationVerification") as verification_class:
            verification_class.return_value.verify.return_value = True
            assert _run_phase_post_activation(args, state, None, secondary, Mock()) is True

        kwargs = verification_class.call_args.kwargs
        assert kwargs["min_managed_clusters"] == 1
        assert kwargs["expected_managed_cluster_names"] == []
        assert kwargs["enforce_expected_managed_cluster_names"] is False

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
            include_old_hub_finalization=True,
            argocd_manage=True,
            skip_gitops_check=False,
            restore_only=False,
        )
        report_argocd_impact.assert_called_once_with(primary, secondary, logger, argocd_manage=True)

    def test_run_phase_preflight_honors_skip_observability_for_old_hub_finalization_rbac(
        self,
    ):
        args = SimpleNamespace(
            method="passive",
            old_hub_action="secondary",
            skip_rbac_validation=False,
            argocd_manage=False,
            skip_gitops_check=False,
            skip_observability_checks=True,
            validate_only=False,
        )
        state = Mock()
        primary = Mock()
        secondary = Mock()
        logger = Mock()
        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": True,
            "secondary_observability_detected": False,
            "has_observability": True,
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
            include_decommission=False,
            include_old_hub_finalization=False,
            argocd_manage=False,
            skip_gitops_check=False,
            restore_only=False,
        )

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
            include_old_hub_finalization=False,
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

    def test_automatic_preflight_logs_only_argocd_aggregate_without_application_identity(self, caplog):
        """Automatic preflight must retain the advisory count without publishing Application identity."""
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
        logger = logging.getLogger("acm_switchover")
        sensitive_app = {
            "metadata": {
                "namespace": "sensitive-app-namespace",
                "name": "sensitive-app-name\nINJECTED-APPLICATION-LINE\x1b[31m",
                "annotations": {
                    "argocd.argoproj.io/tracking-id": "sensitive-tracking-identifier",
                },
            },
            "spec": {"syncPolicy": {"automated": {"prune": True}}},
            "status": {
                "resources": [
                    {
                        "kind": "Policy",
                        "namespace": "open-cluster-management",
                    }
                ]
            },
        }

        def list_resources(*_args, **kwargs):
            if kwargs["plural"] == "argocds":
                return []
            if kwargs["plural"] == "applications":
                return [sensitive_app]
            raise AssertionError(f"Unexpected plural: {kwargs['plural']}")

        primary = Mock()
        secondary = Mock()
        for client in (primary, secondary):
            client.get_custom_resource_advisory.return_value = {"metadata": {"name": "crd"}}
            client.list_custom_resources_advisory.side_effect = list_resources

        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
            "has_observability": False,
            "expected_managed_cluster_names": [],
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator = validator_class.return_value
            validator.validate_all.return_value = (True, config)
            validator.reporter.results = []
            validator.reporter.critical_failures.return_value = []
            with caplog.at_level("INFO", logger="acm_switchover"):
                result = _run_phase_preflight(args, state, primary, secondary, logger)

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert result is True
        assert "Argo CD advisory" in log_text
        assert "1 ACM-touching Application(s)" in log_text
        assert "sensitive-app-namespace" not in log_text
        assert "sensitive-app-name" not in log_text
        assert "sensitive-tracking-identifier" not in log_text
        assert "INJECTED-APPLICATION-LINE" not in log_text
        assert "\x1b" not in log_text

    def test_automatic_preflight_omits_credential_bearing_argocd_exception(self, caplog):
        """Automatic preflight discovery failure must remain advisory without publishing exception detail."""
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
        logger = logging.getLogger("acm_switchover")
        private_reason = (
            "Authorization: Bearer automatic-preflight-token "
            "api=https://api.secret.example:6443 "
            "context=production-admin user=system:admin\n"
            "INJECTED-EXCEPTION-LINE\x1b[31m arbitrary exception detail"
        )
        primary = Mock()
        secondary = Mock()
        for client in (primary, secondary):
            client.get_custom_resource_advisory.side_effect = ApiException(status=401, reason=private_reason)

        config = {
            "primary_version": "2.14.0",
            "secondary_version": "2.14.0",
            "primary_observability_detected": False,
            "secondary_observability_detected": False,
            "has_observability": False,
            "expected_managed_cluster_names": [],
        }

        with patch("acm_switchover.PreflightValidator") as validator_class:
            validator = validator_class.return_value
            validator.validate_all.return_value = (True, config)
            validator.reporter.results = []
            validator.reporter.critical_failures.return_value = []
            with caplog.at_level("INFO", logger="acm_switchover"):
                result = _run_phase_preflight(args, state, primary, secondary, logger)

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert result is True
        assert "Unable to complete Argo CD check" in log_text
        for sentinel in (
            "automatic-preflight-token",
            "api.secret.example",
            "production-admin",
            "system:admin",
            "INJECTED-EXCEPTION-LINE",
            "arbitrary exception detail",
            "\x1b",
        ):
            assert sentinel not in log_text

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


@pytest.mark.unit
class TestInitializeClients:
    def test_initialize_clients_passes_dry_run_to_both_hubs(self):
        args = SimpleNamespace(primary_context="hub-a", secondary_context="hub-b", dry_run=True)
        logger = Mock()

        with patch("acm_switchover.KubeClient") as kube_client:
            primary_client = Mock(name="primary-client")
            secondary_client = Mock(name="secondary-client")
            kube_client.side_effect = [primary_client, secondary_client]
            primary, secondary = _initialize_clients(args, logger)

        assert primary is primary_client
        assert secondary is secondary_client
        assert kube_client.call_args_list == [
            (("hub-a",), {"dry_run": True}),
            (("hub-b",), {"dry_run": True}),
        ]

    def test_initialize_clients_passes_dry_run_to_restore_only_secondary(self):
        args = SimpleNamespace(primary_context=None, secondary_context="restore-hub", dry_run=True)
        logger = Mock()

        with patch("acm_switchover.KubeClient") as kube_client:
            primary, secondary = _initialize_clients(args, logger)

        assert primary is None
        assert secondary is kube_client.return_value
        kube_client.assert_called_once_with("restore-hub", dry_run=True)


@pytest.mark.unit
class TestCliOutcomesDelegation:
    """Tests that acm_switchover.py delegates outcome/report handling to lib.cli_outcomes."""

    def test_report_target_delegates_to_cli_outcomes_module(self):
        args = SimpleNamespace(validate_only=False, decommission=False, restore_only=True)

        with patch(
            "acm_switchover.cli_outcomes.report_target",
            return_value=("restore", "restore-only-report.json"),
        ) as report_target:
            assert _report_target(args) == ("restore", "restore-only-report.json")

        report_target.assert_called_once_with(args)

    def test_write_python_report_delegates_to_cli_outcomes_module(self):
        args = SimpleNamespace(report_dir="/tmp/reports")
        state = Mock()
        logger = Mock()

        with patch("acm_switchover.cli_outcomes.write_python_report", return_value=None) as write_report:
            _write_python_report(args, state, "pass", logger)

        write_report.assert_called_once_with(args, state, "pass", logger)

    def test_phase_report_from_state_delegates_to_cli_outcomes_module(self):
        snapshot = {"completed_steps": [], "current_phase": "INIT", "errors": []}

        with patch(
            "acm_switchover.cli_outcomes.phase_report_from_state",
            return_value={"preflight": {"status": "pass"}},
        ) as phase_report:
            assert _phase_report_from_state(snapshot) == {"preflight": {"status": "pass"}}

        phase_report.assert_called_once_with(snapshot)

    def test_main_setup_branch_delegates_to_cli_outcomes_setup_helper(self):
        args = SimpleNamespace(
            verbose=False,
            log_format="text",
            state_file="state.json",
            primary_context="primary",
            secondary_context="secondary",
            skip_gitops_check=False,
            validate_only=False,
            argocd_manage=False,
            setup=True,
            reset_state=False,
            argocd_resume_only=False,
        )
        logger = Mock()

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging",
            return_value=logger,
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file",
            return_value="state.json",
        ), patch(
            "acm_switchover._prepare_runtime",
        ) as prepare_runtime, patch(
            "acm_switchover.cli_outcomes.run_setup_mode",
            return_value=EXIT_SUCCESS,
        ) as run_setup_mode:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        prepare_runtime.assert_not_called()
        run_setup_mode.assert_called_once_with(
            args,
            logger,
            run_setup=run_setup,
            exit_success=EXIT_SUCCESS,
            exit_failure=EXIT_FAILURE,
            exit_interrupt=EXIT_INTERRUPT,
        )

    def test_main_non_setup_branch_delegates_to_cli_outcomes_operation_helper(self):
        args = SimpleNamespace(
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
        logger = Mock()
        state = Mock()
        primary = Mock()
        secondary = Mock()
        runtime = SimpleNamespace(
            state=state,
            primary=primary,
            secondary=secondary,
            should_bind_state=True,
            should_record_state_errors=True,
        )

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging",
            return_value=logger,
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file",
            return_value="state.json",
        ), patch(
            "acm_switchover.os.path.exists",
            return_value=True,
        ), patch(
            "acm_switchover._prepare_runtime",
            return_value=runtime,
        ), patch(
            "acm_switchover._build_cli_operation_hooks",
            return_value="hooks",
        ), patch(
            "acm_switchover.cli_outcomes.run_operation_mode",
            return_value=EXIT_SUCCESS,
        ) as run_operation_mode:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == EXIT_SUCCESS
        run_operation_mode.assert_called_once_with(
            args,
            state,
            primary,
            secondary,
            logger,
            should_bind_state=True,
            should_record_state_errors=True,
            hooks="hooks",
            exit_success=EXIT_SUCCESS,
            exit_failure=EXIT_FAILURE,
            exit_interrupt=EXIT_INTERRUPT,
        )
