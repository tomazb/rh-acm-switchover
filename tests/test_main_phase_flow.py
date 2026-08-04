"""Tests for acm_switchover phase-flow and restore-only orchestration."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from acm_switchover import (
    _run_phase_finalization,
    _run_phase_preflight,
    main,
    run_restore_only,
    run_switchover,
)
from lib.constants import EXIT_FAILURE
from lib.exceptions import SwitchoverError
from tests.main_test_helpers import (
    failing_phase_stub,
    make_restore_only_args,
    make_switchover_args,
    phase_stub,
)


@pytest.mark.unit
class TestPhaseFlowIntegration:
    """Integration tests that verify orchestrator phase-flow decisions using
    lightweight stubs (not full mocks) that track call order.

    Each stub advances the state phase (like the real handler does) so the
    orchestrator's phase-routing loop sees the correct state transitions.
    """

    def test_full_phase_flow_call_order(self, tmp_path):
        """Stubs track that all five phase handlers fire in order for a fresh run."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        call_order = []
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
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
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order, succeeds=False),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
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
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        assert "preflight" not in call_order
        assert call_order[0] == "primary_prep"
        reloaded = StateManager(str(state_file))
        assert reloaded._get_config("resume_summary") == {"resume_start_phase": "primary_prep"}

    def test_resume_from_activation_skips_earlier_phases(self, tmp_path):
        """When state is ACTIVATION, only activation onward should execute."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.ACTIVATION)

        call_order = []
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert "preflight" not in call_order
        assert "primary_prep" not in call_order
        assert call_order == ["activation", "post_activation", "finalization"]

    def test_resume_from_activation_persists_resume_summary(self, tmp_path):
        """Mid-flow resume should persist the phase where execution restarted."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.ACTIVATION)

        call_order = []
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        reloaded = StateManager(str(state_file))
        assert reloaded._get_config("resume_summary") == {"resume_start_phase": "activation"}

    def test_resume_from_preflight_persists_resume_summary(self, tmp_path):
        """Resuming from persisted PREFLIGHT should record a preflight restart."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.PREFLIGHT)

        call_order = []
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert call_order[0] == "preflight"
        reloaded = StateManager(str(state_file))
        assert reloaded._get_config("resume_summary") == {"resume_start_phase": "preflight"}


@pytest.mark.unit
class TestResumeFromFailedState:
    """Tests that verify orchestrator resume-from-FAILED decisions."""

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
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
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
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
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
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
        ):
            result = run_switchover(args, state, Mock(), Mock(), Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED
        assert call_order == ["finalization"]

    def test_failed_state_records_retry_error_baseline(self, tmp_path):
        """Resuming from FAILED should record a retry error baseline on the state."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.POST_ACTIVATION)
        state.add_error("post-act error", Phase.POST_ACTIVATION.value)
        state.set_phase(Phase.FAILED)

        call_order = []
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
        ):
            run_switchover(args, state, Mock(), Mock(), Mock())

        assert state.get_retry_error_baseline() == {
            "phase": Phase.POST_ACTIVATION.value,
            "count": 1,
        }


@pytest.mark.unit
class TestStaleStateDetection:
    """Tests for stale completed state detection and --force override."""

    def test_stale_completed_state_raises_without_force(self, tmp_path):
        """State older than STALE_STATE_THRESHOLD with COMPLETED phase should
        raise a workflow-domain error when --force is not set."""
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
        args = make_switchover_args(state_file=str(state_file))

        with pytest.raises(SwitchoverError, match="Use --force to proceed with stale state"):
            run_switchover(args, state2, Mock(), Mock(), Mock())

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
        args = make_switchover_args(state_file=str(state_file), force=True)

        def successful_phase(next_phase):
            def handler(_args, phase_state, *_rest):
                phase_state.set_phase(next_phase)
                return True

            return handler

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=successful_phase(Phase.PREFLIGHT),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=successful_phase(Phase.PRIMARY_PREP),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=successful_phase(Phase.ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=successful_phase(Phase.POST_ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=successful_phase(Phase.FINALIZATION),
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
        args = make_switchover_args(
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

    def test_workflow_domain_error_exits_without_unexpected_log(self, tmp_path, monkeypatch, capsys):
        """SwitchoverError from workflow helpers should exit cleanly with failure."""
        monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path))
        state_file = tmp_path / "state.json"

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
        ), patch("acm_switchover._initialize_clients", return_value=(Mock(), Mock())), patch(
            "acm_switchover._collect_hub_identities",
            return_value={},
        ), patch(
            "acm_switchover._execute_operation",
            side_effect=SwitchoverError("Use --force to proceed with stale state"),
        ), patch(
            "acm_switchover._write_python_report"
        ), pytest.raises(
            SystemExit
        ) as exc_info:
            main()

        assert exc_info.value.code == EXIT_FAILURE
        captured = capsys.readouterr()
        assert "Use --force to proceed with stale state" in captured.err
        assert "Unexpected error" not in captured.err


@pytest.mark.unit
class TestPhaseHandlerFailure:
    """Tests that verify error recording when a phase handler fails."""

    def test_phase_failure_sets_failed_state_and_records_error(self, tmp_path):
        """When a phase handler returns False (via _fail_phase), the orchestrator
        should end with Phase.FAILED and recorded errors."""
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.set_phase(Phase.INIT)

        call_order = []
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=failing_phase_stub("preflight", call_order),
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

        args = make_switchover_args(state_file=str(state_file))

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
        args = make_switchover_args(state_file=str(state_file))

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=phase_stub("preflight", call_order),
        ), patch(
            "acm_switchover._run_phase_primary_prep",
            side_effect=failing_phase_stub("primary_prep", call_order),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=phase_stub("activation", call_order),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=phase_stub("post_activation", call_order),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=phase_stub("finalization", call_order),
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
        args = make_switchover_args()

        with pytest.raises(ValueError, match="Secondary client is required"):
            run_switchover(args, Mock(), Mock(), None, Mock())


class TestRestoreOnlyFlow:
    """Tests for --restore-only single-hub restore workflow."""

    @staticmethod
    def _successful_phase(next_phase):
        def handler(_args, phase_state, *_rest):
            phase_state.set_phase(next_phase)
            return True

        return handler

    def test_restore_only_defaults_method_to_full(self, tmp_path):
        """run_restore_only sets method=full when not specified."""
        from lib.utils import Phase, StateManager

        args = make_restore_only_args(state_file=str(tmp_path / "state.json"))
        state = StateManager(args.state_file)
        state.set_phase(Phase.INIT)
        secondary = Mock()

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
        ) as pf, patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
        ):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        assert args.method == "full"
        assert args.old_hub_action == "none"

    def test_restore_only_skips_primary_prep(self, tmp_path):
        """Restore-only flow does NOT call _run_phase_primary_prep."""
        from lib.utils import Phase, StateManager

        args = make_restore_only_args(state_file=str(tmp_path / "state.json"))
        state = StateManager(args.state_file)
        state.set_phase(Phase.INIT)
        secondary = Mock()

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
        ) as pf, patch("acm_switchover._run_phase_primary_prep") as pp, patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
        ):
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pp.assert_not_called()

    def test_restore_only_fails_when_successful_handler_leaves_wrong_phase(self, tmp_path):
        """Restore-only must not complete when a True handler leaves stale phase state."""
        from lib.utils import Phase, StateManager

        args = make_restore_only_args(state_file=str(tmp_path / "state.json"))
        state = StateManager(args.state_file)
        state.set_phase(Phase.INIT)

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
        ), patch("acm_switchover._attempt_argocd_resume_on_failure") as resume_on_failure:
            result = run_restore_only(args, state, Mock(), Mock())

        assert result is False
        assert state.get_current_phase() == Phase.FAILED
        assert state.get_last_error_phase() == Phase.ACTIVATION
        assert "expected phase" in state.get_errors()[-1]["error"]
        resume_on_failure.assert_called_once()

    def test_restore_only_passes_none_primary_to_handlers(self):
        """Phase handlers receive primary=None in restore-only mode."""
        from lib.utils import Phase, StateManager

        args = make_restore_only_args()
        state = Mock(spec=StateManager)
        # Return INIT first, then follow each phase transition via set_phase
        current = [Phase.INIT]

        def track_phase(p):
            current[0] = p

        state.get_current_phase.side_effect = lambda: current[0]
        state.set_phase.side_effect = track_phase
        state.get_state_age.return_value = None
        state.state_file = ".state/restore-only-phase-flow.json"
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

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=capture_primary("preflight"),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=capture_primary("activation"),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=capture_primary("post_activation"),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=capture_primary("finalization"),
        ):
            run_restore_only(args, state, secondary, Mock())

        assert len(called_with_primary) == 4
        for name, primary_val in called_with_primary:
            assert primary_val is None, f"{name} should receive primary=None"

    def test_restore_only_phase_transitions(self, tmp_path):
        """Restore-only flow transitions through correct phases."""
        from lib.utils import Phase, StateManager

        args = make_restore_only_args(state_file=str(tmp_path / "state.json"))
        state = StateManager(args.state_file)
        state.set_phase(Phase.INIT)
        secondary = Mock()

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
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
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        assert state.get_current_phase() == Phase.COMPLETED

    def test_restore_only_validate_only_runs_preflight_only(self):
        """--restore-only --validate-only only runs preflight."""
        from lib.utils import Phase, StateManager

        args = make_restore_only_args(validate_only=True)
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=True) as pf, patch(
            "acm_switchover._run_phase_activation"
        ) as act:
            result = run_restore_only(args, state, secondary, Mock())

        assert result is True
        pf.assert_called_once()
        act.assert_not_called()

    def test_restore_only_preflight_failure_stops_flow(self):
        """Restore-only flow stops if preflight fails."""
        from lib.utils import Phase, StateManager

        args = make_restore_only_args()
        state = Mock(spec=StateManager)
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        with patch("acm_switchover._run_phase_preflight", return_value=False) as pf, patch(
            "acm_switchover._run_phase_activation"
        ) as act:
            result = run_restore_only(args, state, secondary, Mock())

        assert result is False
        act.assert_not_called()

    def test_restore_only_preflight_sets_restore_only_flag(self):
        """Preflight in restore-only mode passes restore_only=True to PreflightValidator."""
        from lib.utils import Phase

        args = make_restore_only_args()
        state = Mock()
        state.get_current_phase.return_value = Phase.PREFLIGHT
        state._get_config.return_value = False
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

        args = make_restore_only_args()
        # Explicitly verify old_hub_action is set to "none" by run_restore_only
        args.method = "full"
        state = Mock()
        state.get_current_phase.return_value = Phase.INIT
        state.get_state_age.return_value = None
        secondary = Mock()

        current = [Phase.INIT]
        state.get_current_phase.side_effect = lambda: current[0]
        state.set_phase.side_effect = lambda phase: current.__setitem__(0, phase)

        with patch(
            "acm_switchover._run_phase_preflight",
            side_effect=self._successful_phase(Phase.PREFLIGHT),
        ), patch(
            "acm_switchover._run_phase_activation",
            side_effect=self._successful_phase(Phase.ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_post_activation",
            side_effect=self._successful_phase(Phase.POST_ACTIVATION),
        ), patch(
            "acm_switchover._run_phase_finalization",
            side_effect=self._successful_phase(Phase.FINALIZATION),
        ) as fin:
            run_restore_only(args, state, secondary, Mock())

        assert args.old_hub_action == "none"

    def test_restore_only_phase_finalization_passes_restore_only_flag(self):
        """_run_phase_finalization must wire restore_only through to Finalization."""
        from lib.utils import Phase

        args = make_restore_only_args()
        args.method = "full"
        state = Mock()
        state.get_current_phase.return_value = Phase.POST_ACTIVATION
        state._get_config.side_effect = lambda key, default=None: default
        secondary = Mock()

        with patch("acm_switchover.Finalization") as finalization_class:
            finalization_class.return_value.finalize.return_value = True
            assert _run_phase_finalization(args, state, None, secondary, Mock()) is True

        call_kwargs = finalization_class.call_args.kwargs
        assert call_kwargs["restore_only"] is True

    def test_restore_only_completed_noop_banner_says_restore(self, tmp_path, caplog):
        """Noop banner for a recent completed restore-only run must say 'RESTORE', not 'SWITCHOVER'.

        Bug: _log_completed_noop always logs 'SWITCHOVER ALREADY COMPLETED' regardless of
        operation type. A restore-only rerun must log 'RESTORE ALREADY COMPLETED' to avoid
        misleading operators.
        """
        from datetime import timedelta

        from lib.constants import STALE_STATE_THRESHOLD
        from lib.utils import Phase, StateManager

        state_file = tmp_path / "state.json"
        state = StateManager(str(state_file))
        state.state["current_phase"] = Phase.COMPLETED.value
        # Set last_updated to a recent timestamp so it is NOT stale (age < threshold)
        recent_age = timedelta(seconds=STALE_STATE_THRESHOLD - 60)
        recent_ts = (datetime.now(timezone.utc) - recent_age).isoformat()
        state.state["last_updated"] = recent_ts
        state._write_state(state.state)

        reloaded = StateManager(str(state_file))
        args = make_restore_only_args()
        real_logger = logging.getLogger("acm_switchover")

        with caplog.at_level(logging.INFO, logger="acm_switchover"):
            result = run_restore_only(args, reloaded, Mock(), real_logger)

        assert result is True
        assert "RESTORE ALREADY COMPLETED" in caplog.text, (
            "Expected 'RESTORE ALREADY COMPLETED' in log output; got: " + caplog.text
        )
        assert "SWITCHOVER ALREADY COMPLETED" not in caplog.text
