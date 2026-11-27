"""Guards from the 2026-08-03 parity audit (findings H1 and H10).

H1: a resumed run whose state carries no managed-cluster expectation must
fail closed instead of silently disabling all enforcement.
H10: --dry-run must never destroy a real in-progress state file, even when
the invocation's contexts differ from the file's (ensure_contexts reset).
"""

import argparse
import json
import logging
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from acm_switchover import _prepare_runtime, _resolve_managed_cluster_expectation, main
from lib.constants import EXIT_FAILURE, EXIT_SUCCESS, STATE_KEY_ARGOCD_PAUSED_APPS, STATE_KEY_ARGOCD_RUN_ID
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.utils import Phase, StateManager, fcntl

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


class TestDryRunStateIsolation:
    @pytest.mark.parametrize("contents", [b'{ "current_phase": "activation", "contexts": {} }\n', None])
    @pytest.mark.parametrize("termination", ["normal", "error", "SIGTERM", "SIGKILL"])
    def test_cli_dry_run_leaves_checkpoint_untouched(self, tmp_path, contents, termination):
        path = tmp_path / "state.json"
        report_dir = tmp_path / "reports"
        if contents is not None:
            path.write_bytes(contents)

        # Enter the real CLI with fake clients and stop after a critical phase
        # checkpoint. SIGKILL makes it impossible to repair an earlier write.
        script = """
import os
import signal
import sys
from unittest.mock import patch

from acm_switchover import main
from lib.utils import Phase

termination = sys.argv.pop(1)

def rehearse(args, state, *unused):
    state.set_phase(Phase.PREFLIGHT)
    assert state.get_current_phase() == Phase.PREFLIGHT
    if termination == "error":
        raise RuntimeError("rehearsal failed")
    if termination != "normal":
        os.kill(os.getpid(), getattr(signal, termination))
    return True

with patch("acm_switchover._initialize_clients", return_value=(None, None)), \
     patch("acm_switchover._bind_runtime_hub_identities"), \
     patch("acm_switchover._execute_operation", side_effect=rehearse):
    main()
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                termination,
                "--primary-context",
                "hub-a",
                "--secondary-context",
                "hub-b",
                "--method",
                "passive",
                "--old-hub-action",
                "secondary",
                "--state-file",
                str(path),
                "--dry-run",
                "--report-dir",
                str(report_dir),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        if termination in ("normal", "error"):
            expected_exit = EXIT_SUCCESS if termination == "normal" else EXIT_FAILURE
        else:
            expected_exit = -getattr(signal, termination)
        assert result.returncode == expected_exit, result.stdout + result.stderr
        if contents is None:
            assert not path.exists()
        else:
            assert path.read_bytes() == contents
        if termination in ("normal", "error"):
            report = json.loads((report_dir / "switchover-report.json").read_text())
            assert report["status"] == ("pass" if termination == "normal" else "fail")

    def _progressed_state_file(self, tmp_path):
        path = tmp_path / "switchover-guard.json"
        state = StateManager(str(path))
        state.ensure_contexts("old-primary", "old-secondary")
        state.mark_step_completed("preflight_validation")
        state.flush_state()
        return path

    @pytest.mark.parametrize("dry_run", [True, False])
    def test_context_reset_persists_only_in_real_run(self, tmp_path, dry_run):
        path = self._progressed_state_file(tmp_path)
        before = path.read_bytes()
        args = _args(dry_run=dry_run, primary_context="new-primary", secondary_context="new-secondary")

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.state.state["contexts"] == {"primary": "new-primary", "secondary": "new-secondary"}
        assert not ctx.state.is_step_completed("preflight_validation")
        if dry_run:
            assert path.read_bytes() == before
        else:
            assert json.loads(path.read_text())["contexts"] == ctx.state.state["contexts"]

    def test_unbound_dry_run_does_not_persist_errors(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        before = path.read_bytes()
        args = _args(dry_run=True, argocd_resume_only=True)

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.state.is_step_completed("preflight_validation")
        ctx.state.add_error("simulated failure")
        assert path.read_bytes() == before

    @pytest.mark.parametrize("error", [RuntimeError("client init boom"), KeyboardInterrupt()])
    def test_client_init_failure_leaves_checkpoint_untouched(self, tmp_path, error):
        path = self._progressed_state_file(tmp_path)
        before = path.read_bytes()
        args = _args(dry_run=True, primary_context="new-primary", secondary_context="new-secondary")
        expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else SystemExit

        with patch("acm_switchover._initialize_clients", side_effect=error), pytest.raises(expected):
            _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert path.read_bytes() == before

    def test_context_binding_failure_leaves_checkpoint_untouched(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        before = path.read_bytes()
        args = _args(dry_run=True, primary_context="new-primary", secondary_context="new-secondary")
        real_ensure_contexts = StateManager.ensure_contexts

        def mutate_then_raise(state, primary_context, secondary_context):
            real_ensure_contexts(state, primary_context, secondary_context)
            raise RuntimeError("boom during context binding")

        with patch.object(StateManager, "ensure_contexts", mutate_then_raise):
            with pytest.raises(RuntimeError, match="boom during context binding"):
                _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert path.read_bytes() == before

    def test_dry_run_refuses_corrupt_checkpoint_without_copying_it(self, tmp_path, caplog):
        path = tmp_path / "state.json"
        contents = b"{corrupt"
        path.write_bytes(contents)

        with pytest.raises(SystemExit) as result:
            _prepare_runtime(_args(dry_run=True), logging.getLogger("test"), str(path))

        assert result.value.code == EXIT_FAILURE
        assert "State file is corrupt" in caplog.text
        assert path.read_bytes() == contents
        assert not list(tmp_path.glob("state.json.corrupt.*"))

    @pytest.mark.skipif(fcntl is None, reason="fcntl unavailable on this platform")
    @pytest.mark.parametrize("alias", ["hardlink", "dangling-symlink"])
    def test_dry_run_refuses_lock_alias_to_checkpoint(self, tmp_path, alias):
        path = tmp_path / "state.json"
        lock = tmp_path / "state.json.run.lock"
        contents = b'{ "current_phase": "init" }\n'
        if alias == "hardlink":
            path.write_bytes(contents)
            lock.hardlink_to(path)
        else:
            lock.symlink_to(path)

        with pytest.raises(SystemExit) as result:
            _prepare_runtime(_args(dry_run=True), logging.getLogger("test"), str(path))

        assert result.value.code == EXIT_FAILURE
        if alias == "hardlink":
            assert path.read_bytes() == contents
        else:
            assert not path.exists()

    @pytest.mark.parametrize("suffix", ["/", "/."])
    def test_dry_run_refuses_directory_path_without_creating_it(self, tmp_path, suffix):
        path = tmp_path / "state.json"

        with pytest.raises(SystemExit) as result:
            _prepare_runtime(_args(dry_run=True), logging.getLogger("test"), str(path) + suffix)

        assert result.value.code == EXIT_FAILURE
        assert not path.exists()

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


class TestResetStateStartup:
    @staticmethod
    def _cli(path, *flags):
        return [
            str(Path(__file__).resolve().parents[1] / "acm_switchover.py"),
            "--primary-context",
            "hub-a",
            "--secondary-context",
            "hub-b",
            "--method",
            "passive",
            "--old-hub-action",
            "secondary",
            "--state-file",
            str(path),
            *flags,
        ]

    @pytest.mark.skipif(fcntl is None, reason="fcntl unavailable on this platform")
    @pytest.mark.parametrize("contents", [b'{"current_phase": "init"}\n', b"{corrupt\n"])
    @pytest.mark.parametrize("mode", ["--reset-state", "--dry-run"])
    def test_competing_cli_preserves_exact_bytes(self, tmp_path, contents, mode):
        path = tmp_path / "state.json"
        owner = StateManager(str(path))
        path.write_bytes(contents)

        try:
            result = subprocess.run(
                [sys.executable, *self._cli(path, mode)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        finally:
            owner._release_run_lock()

        assert result.returncode == EXIT_FAILURE
        assert "already using state file" in result.stdout + result.stderr
        assert path.exists(), "the checkpoint disappeared while the other process held its run lock"
        assert path.read_bytes() == contents

    def test_resume_only_reset_preserves_pause_register(self, tmp_path):
        path = tmp_path / "state.json"
        state = StateManager(str(path))
        state.state["config"][STATE_KEY_ARGOCD_RUN_ID] = "paused-run"
        state.state["config"][STATE_KEY_ARGOCD_PAUSED_APPS] = [
            {
                "hub": "primary",
                "namespace": "openshift-gitops",
                "name": "acm",
                "pause_applied": True,
                "original_sync_policy": {"automated": {}},
            }
        ]
        state.flush_state()
        before = path.read_bytes()
        argv = [
            "acm_switchover.py",
            "--argocd-resume-only",
            "--reset-state",
            "--secondary-context",
            "hub-b",
            "--state-file",
            str(path),
        ]
        with patch("sys.argv", argv), patch(
            "acm_switchover._initialize_clients", return_value=(None, None)
        ) as initialize, pytest.raises(SystemExit) as result:
            main()

        assert path.read_bytes() == before
        assert result.value.code == EXIT_FAILURE
        initialize.assert_not_called()

    @pytest.mark.parametrize("stored_contexts", [None, {"primary": "old-a", "secondary": "old-b"}])
    @pytest.mark.parametrize("explicit_reset", [False, True])
    def test_validate_only_requires_explicit_reset_to_discard_legacy_progress(
        self, tmp_path, stored_contexts, explicit_reset
    ):
        path = tmp_path / "state.json"
        state = StateManager(str(path))
        state.state["contexts"] = stored_contexts
        state.set_phase(Phase.ACTIVATION)
        state.mark_step_completed("legacy-progress")
        before = path.read_bytes()
        primary = Mock()
        primary.get_cluster_identity.return_value = {"context": "hub-a", "cluster_uid": "uid-a"}
        secondary = Mock()
        secondary.get_cluster_identity.return_value = {"context": "hub-b", "cluster_uid": "uid-b"}
        argv = self._cli(path, "--validate-only")
        if explicit_reset:
            argv.append("--reset-state")

        with patch("sys.argv", argv), patch(
            "acm_switchover._initialize_clients", return_value=(primary, secondary)
        ), patch("acm_switchover._run_phase_preflight", return_value=True) as preflight, pytest.raises(
            SystemExit
        ) as result:
            main()

        if explicit_reset:
            assert result.value.code == EXIT_SUCCESS
            preflight.assert_called_once()
            fresh = json.loads(path.read_bytes())
            assert fresh["contexts"] == {"primary": "hub-a", "secondary": "hub-b"}
            assert fresh["current_phase"] == Phase.INIT.value
            assert fresh["completed_steps"] == []
        else:
            assert path.read_bytes() == before
            assert result.value.code == EXIT_FAILURE
            preflight.assert_not_called()

    @pytest.mark.parametrize(
        ("stored_contexts", "primary_context"),
        [
            (None, "hub-a"),
            ({"primary": "hub-a", "secondary": "hub-b"}, "hub-a"),
            ({"primary": None, "secondary": "hub-b"}, None),
        ],
    )
    def test_validate_only_accepts_fresh_or_matching_contexts_without_rebinding(
        self, tmp_path, stored_contexts, primary_context
    ):
        path = tmp_path / "state.json"
        state = StateManager(str(path))
        state.state["contexts"] = stored_contexts
        state.flush_state()
        before = path.read_bytes()
        args = _args(validate_only=True, primary_context=primary_context)

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert path.read_bytes() == before
