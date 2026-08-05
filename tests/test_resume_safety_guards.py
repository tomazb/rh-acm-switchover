"""Guards from the 2026-08-03 parity audit (findings H1 and H10).

H1: a resumed run whose state carries no managed-cluster expectation must
fail closed instead of silently disabling all enforcement.
H10: --dry-run must never destroy a real in-progress state file, even when
the invocation's contexts differ from the file's (ensure_contexts reset).
"""

import argparse
import json
import logging
from unittest.mock import patch

import pytest

from acm_switchover import _prepare_runtime, _resolve_managed_cluster_expectation
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.utils import StateManager

pytestmark = pytest.mark.unit


def _args(**overrides):
    defaults = {
        "min_managed_clusters": None,
        "dry_run": False,
        "argocd_resume_only": False,
        "decommission": False,
        "primary_context": "hub-a",
        "secondary_context": "hub-b",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestExpectationFailsClosed:
    def test_missing_expectation_record_raises(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))

        with pytest.raises(SwitchoverError) as exc_info:
            _resolve_managed_cluster_expectation(_args(), state)

        message = str(exc_info.value)
        assert "--min-managed-clusters" in message
        assert "preflight" in message

    def test_recorded_expectation_still_resolves(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))
        RunRecord(state).record_managed_cluster_expectation(names=["c1", "c2"], count=2, mode="derived_from_preflight")

        assert _resolve_managed_cluster_expectation(_args(), state) == (2, ["c1", "c2"], True)

    def test_restore_only_mode_still_resolves(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))
        RunRecord(state).record_managed_cluster_expectation(names=[], count=0, mode="restore_only")

        assert _resolve_managed_cluster_expectation(_args(), state) == (1, [], False)

    def test_explicit_min_overrides_missing_record(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))

        assert _resolve_managed_cluster_expectation(_args(min_managed_clusters=3), state) == (3, [], False)
        assert _resolve_managed_cluster_expectation(_args(min_managed_clusters=0), state) == (0, [], False)


class TestDryRunStateGuard:
    def _progressed_state_file(self, tmp_path):
        path = tmp_path / "switchover-guard.json"
        state = StateManager(str(path))
        state.ensure_contexts("old-primary", "old-secondary")
        state.mark_step_completed("preflight_validation")
        state.flush_state()
        return path

    def test_dry_run_guard_predates_context_reset(self, tmp_path, caplog):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=True, primary_context="new-primary", secondary_context="new-secondary")

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.dry_run_state_guard is not None
        # The guard captured the state BEFORE ensure_contexts reset it.
        assert ctx.dry_run_state_guard["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in ctx.dry_run_state_guard["completed_steps"])

        # Restoring the guard brings the on-disk file back to the original run.
        ctx.state.restore_state_snapshot(ctx.dry_run_state_guard)
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])

    def test_real_run_has_no_guard_and_keeps_reset(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=False, primary_context="new-primary", secondary_context="new-secondary")

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.dry_run_state_guard is None
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "new-primary", "secondary": "new-secondary"}

    def test_dry_run_guard_absent_for_unbound_operations(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=True, argocd_resume_only=True)

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.dry_run_state_guard is None

    def test_dry_run_restores_state_when_client_init_fails(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=True, primary_context="new-primary", secondary_context="new-secondary")

        # Simulate _initialize_clients failure in a dry run
        with patch("acm_switchover._initialize_clients", side_effect=RuntimeError("client init boom")):
            with patch("sys.exit", side_effect=SystemExit(1)):
                with pytest.raises(SystemExit):
                    _prepare_runtime(args, logging.getLogger("test"), str(path))

        # Verify the state was restored before exit (the fix should do this)
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])

    def test_dry_run_restores_state_when_client_init_is_interrupted(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=True, primary_context="new-primary", secondary_context="new-secondary")

        # Simulate Ctrl-C during _initialize_clients's live network connects
        # (audit H10 finding 1): KeyboardInterrupt is not an Exception
        # subclass, so it must still trigger the state restore.
        with patch("acm_switchover._initialize_clients", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                _prepare_runtime(args, logging.getLogger("test"), str(path))

        # Verify the state was restored before the interrupt propagated
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])

    def test_dry_run_restores_state_when_ensure_contexts_raises(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=True, primary_context="new-primary", secondary_context="new-secondary")

        # Simulate ensure_contexts failing AFTER it has already reset and
        # flushed the mismatched state to disk (e.g. an interrupt or I/O error
        # raised at the end of the critical-checkpoint flush): the guard must
        # still restore the original run before the exception propagates.
        real_ensure_contexts = StateManager.ensure_contexts

        def _mutate_then_raise(self, primary_context, secondary_context):
            real_ensure_contexts(self, primary_context, secondary_context)
            raise RuntimeError("boom during context binding")

        with patch.object(StateManager, "ensure_contexts", _mutate_then_raise), patch(
            "acm_switchover._initialize_clients", return_value=(None, None)
        ):
            with pytest.raises(RuntimeError, match="boom during context binding"):
                _prepare_runtime(args, logging.getLogger("test"), str(path))

        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])

    def test_dry_run_restores_state_when_operation_raises(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from acm_switchover import main

        path = self._progressed_state_file(tmp_path)
        args = SimpleNamespace(
            dry_run=True,
            primary_context="new-primary",
            secondary_context="new-secondary",
            state_file=str(path),
            reset_state=False,
            verbose=False,
            log_format="text",
            force=False,
            validate_only=False,
            argocd_resume_only=False,
            decommission=False,
            skip_gitops_check=False,
            setup=False,
        )
        logger = Mock()
        state_mock = Mock()
        state_mock.restore_state_snapshot = StateManager(str(path)).restore_state_snapshot

        runtime = SimpleNamespace(
            state=state_mock,
            primary=None,
            secondary=None,
            should_bind_state=True,
            should_record_state_errors=True,
            dry_run_state_guard=None,  # Will be set by _prepare_runtime
        )

        def _mutate_state_then_raise(*_args, **_kwargs):
            # Simulate the rehearsal actually mutating the on-disk state
            # (e.g. via a later ensure_contexts/flush) before it fails, so
            # this test can only pass if main()'s finally-restore runs.
            mutator = StateManager(str(path))
            mutator.ensure_contexts("mutated-primary", "mutated-secondary")
            raise RuntimeError("operation boom")

        with patch("acm_switchover.parse_args", return_value=args), patch(
            "acm_switchover.setup_logging", return_value=logger
        ), patch("acm_switchover.validate_args"), patch(
            "acm_switchover._resolve_state_file", return_value=str(path)
        ), patch(
            "acm_switchover._prepare_runtime", return_value=runtime
        ), patch(
            "acm_switchover._build_cli_operation_hooks", return_value={}
        ), patch(
            "acm_switchover.cli_outcomes.run_operation_mode", side_effect=_mutate_state_then_raise
        ), patch(
            "acm_switchover.GitOpsCollector.get_instance"
        ):
            # Set dry_run_state_guard on the runtime to simulate _prepare_runtime's capture
            with pytest.raises(RuntimeError):
                # Capture the progressed state before operation
                guard_snapshot = StateManager(str(path)).capture_state_snapshot()
                runtime.dry_run_state_guard = guard_snapshot
                main()

        # Verify the state was restored in the finally block even though operation raised
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])

    def test_dry_run_rejects_reset_state(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(
            dry_run=True,
            reset_state=True,
            primary_context="new-primary",
            secondary_context="new-secondary",
        )

        with pytest.raises(SystemExit):
            _prepare_runtime(args, logging.getLogger("test"), str(path))

        # The rehearsal must never delete the real state file (parity audit H10).
        assert path.exists()
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])

    def test_real_reset_state_still_deletes(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(
            dry_run=False,
            reset_state=True,
            primary_context="new-primary",
            secondary_context="new-secondary",
        )

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            _prepare_runtime(args, logging.getLogger("test"), str(path))

        # Real --reset-state is unchanged: the old contexts/steps are gone.
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] != {"primary": "old-primary", "secondary": "old-secondary"}
        assert not any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])
