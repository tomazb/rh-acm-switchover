"""Unit tests for lib/utils.py.

Modernized pytest tests with fixtures, markers, and better organization.
Tests cover StateManager, Phase enum, version comparison, and logging setup.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

try:
    import fcntl
except ImportError:  # pragma: no cover - platform-specific
    fcntl = None

from lib.exceptions import StateLoadError, StateLockError
from lib.utils import (
    Phase,
    StateManager,
    dry_run_skip,
    is_acm_version_ge,
    setup_logging,
)


def temp_file_exists(path: str) -> bool:
    """Check if a temp file exists (helper for atomic write tests)."""
    return os.path.exists(path)


@pytest.fixture
def temp_state_file(tmp_path):
    """Provide a temporary state file path."""
    return tmp_path / "test-state.json"


@pytest.fixture
def state_manager(temp_state_file):
    """Create a StateManager instance with temp file."""
    return StateManager(str(temp_state_file))


@pytest.mark.unit
class TestStateManager:
    """Test cases for StateManager class."""

    def test_initial_state(self, state_manager):
        """Test initial state creation."""
        assert state_manager.get_current_phase() == Phase.INIT
        assert state_manager.is_step_completed("any_step") is False
        assert state_manager.get_config("key") is None

    def test_bare_filename(self, tmp_path):
        """Test that StateManager works with bare filename (no directory)."""
        bare_file = tmp_path / "state.json"
        state = StateManager(str(bare_file))

        # Should be able to save without FileNotFoundError
        state.set_phase(Phase.PREFLIGHT)
        state.mark_step_completed("test_step")

        # Verify file was created and can be loaded
        assert bare_file.exists()
        loaded = StateManager(str(bare_file))
        assert loaded.get_current_phase() == Phase.PREFLIGHT

    def test_set_phase(self, state_manager, temp_state_file):
        """Test phase transition."""
        state_manager.set_phase(Phase.PREFLIGHT)
        assert state_manager.get_current_phase() == Phase.PREFLIGHT

        # Load in new instance to verify persistence
        new_state = StateManager(str(temp_state_file))
        assert new_state.get_current_phase() == Phase.PREFLIGHT

    def test_mark_step_completed(self, state_manager):
        """Test marking steps as completed."""
        state_manager.mark_step_completed("step1")
        assert state_manager.is_step_completed("step1") is True
        assert state_manager.is_step_completed("step2") is False

        # Verify timestamp exists in the list of completed steps
        completed = state_manager.state["completed_steps"]
        assert len(completed) == 1
        assert completed[0]["name"] == "step1"
        assert "timestamp" in completed[0]

    def test_mark_step_completed_persists_immediately(self, tmp_path):
        """Completed steps should be persisted without explicit save_state."""
        state_path = tmp_path / "state-step.json"
        sm = StateManager(str(state_path))

        sm.mark_step_completed("step1")

        reloaded = StateManager(str(state_path))
        assert reloaded.is_step_completed("step1") is True

    def test_clear_step_completed_persists_immediately(self, tmp_path):
        """Cleared completed steps should be persisted for retryable cleanup."""
        state_path = tmp_path / "state-clear-step.json"
        sm = StateManager(str(state_path))
        sm.mark_step_completed("pause_argocd_apps")

        sm.clear_step_completed("pause_argocd_apps")

        assert sm.is_step_completed("pause_argocd_apps") is False
        reloaded = StateManager(str(state_path))
        assert reloaded.is_step_completed("pause_argocd_apps") is False

    def test_restore_runtime_checkpoint_restores_errors_deep_copy(self, tmp_path):
        """Validate-only checkpoint restore must discard transient errors."""
        state_path = tmp_path / "state-runtime-checkpoint.json"
        sm = StateManager(str(state_path))
        sm.set_phase(Phase.POST_ACTIVATION)
        sm.add_error("original failure", Phase.POST_ACTIVATION.value)
        checkpoint = sm.capture_runtime_checkpoint()
        assert "errors" in checkpoint
        assert checkpoint["errors"] is not sm.state["errors"]

        # Mutate runtime state after capture to prove the checkpoint kept an
        # independent copy of the durable error list.
        sm.set_phase(Phase.PREFLIGHT)
        sm.add_error("validate-only preflight failure", Phase.PREFLIGHT.value)

        sm.restore_runtime_checkpoint(checkpoint)

        assert sm.get_current_phase() == Phase.POST_ACTIVATION
        errors = sm.get_errors()
        assert len(errors) == 1
        assert errors[0]["phase"] == Phase.POST_ACTIVATION.value
        assert errors[0]["error"] == "original failure"

    def test_set_get_config(self, state_manager):
        """Test configuration storage."""
        state_manager.set_config("acm_version", "2.12.0")
        assert state_manager.get_config("acm_version") == "2.12.0"
        assert state_manager.get_config("nonexistent") is None

        # Test with default
        assert state_manager.get_config("missing", "default") == "default"

    def test_set_config_persists_immediately(self, tmp_path):
        """Config updates should be persisted without explicit save_state."""
        state_path = tmp_path / "state-config.json"
        sm = StateManager(str(state_path))

        sm.set_config("key", "value")

        reloaded = StateManager(str(state_path))
        assert reloaded.get_config("key") == "value"

    def test_add_error(self, state_manager):
        """Test error recording."""
        state_manager.add_error("Test error", Phase.PREFLIGHT.value)
        errors = state_manager.state["errors"]
        assert len(errors) == 1
        assert errors[0]["error"] == "Test error"
        assert errors[0]["phase"] == Phase.PREFLIGHT.value
        assert "timestamp" in errors[0]

    def test_get_errors_empty(self, state_manager):
        """Test get_errors returns empty list when no errors."""
        errors = state_manager.get_errors()
        assert errors == []

    def test_get_errors_returns_all_errors(self, state_manager):
        """Test get_errors returns all recorded errors."""
        state_manager.add_error("Error 1", Phase.PREFLIGHT.value)
        state_manager.add_error("Error 2", Phase.ACTIVATION.value)
        state_manager.add_error("Error 3", Phase.POST_ACTIVATION.value)

        errors = state_manager.get_errors()
        assert len(errors) == 3
        assert errors[0]["error"] == "Error 1"
        assert errors[1]["error"] == "Error 2"
        assert errors[2]["error"] == "Error 3"

    def test_get_last_error_phase_no_errors(self, state_manager):
        """Test get_last_error_phase returns None when no errors."""
        result = state_manager.get_last_error_phase()
        assert result is None

    def test_get_last_error_phase_returns_correct_phase(self, state_manager):
        """Test get_last_error_phase returns the phase of the last error."""
        state_manager.add_error("Error 1", Phase.PREFLIGHT.value)
        state_manager.add_error("Error 2", Phase.POST_ACTIVATION.value)

        result = state_manager.get_last_error_phase()
        assert result == Phase.POST_ACTIVATION

    def test_get_last_error_phase_invalid_phase(self, state_manager):
        """Test get_last_error_phase handles invalid phase gracefully."""
        # Manually add an error with an invalid phase
        state_manager.state["errors"].append(
            {
                "error": "Test error",
                "phase": "invalid_phase",
                "timestamp": "2026-01-29T12:00:00+00:00",
            }
        )

        result = state_manager.get_last_error_phase()
        assert result is None

    def test_get_last_error_phase_missing_phase_field(self, state_manager):
        """Test get_last_error_phase handles missing phase field."""
        # Manually add an error without phase field
        state_manager.state["errors"].append({"error": "Test error", "timestamp": "2026-01-29T12:00:00+00:00"})

        result = state_manager.get_last_error_phase()
        assert result is None

    def test_reset(self, state_manager):
        """Test state reset."""
        state_manager.set_phase(Phase.ACTIVATION)
        state_manager.mark_step_completed("step1")
        state_manager.set_config("key", "value")
        state_manager.add_error("error")

        state_manager.reset()

        assert state_manager.get_current_phase() == Phase.INIT
        assert state_manager.is_step_completed("step1") is False
        assert state_manager.get_config("key") is None
        assert len(state_manager.state["errors"]) == 0

    def test_persistence(self, state_manager, temp_state_file):
        """Test state persistence to file."""
        state_manager.set_phase(Phase.PRIMARY_PREP)
        state_manager.mark_step_completed("backup_paused")
        state_manager.set_config("observability", True)
        state_manager.save_state()  # Ensure dirty state is persisted

        # Load in new instance
        loaded_state = StateManager(str(temp_state_file))
        assert loaded_state.get_current_phase() == Phase.PRIMARY_PREP
        assert loaded_state.is_step_completed("backup_paused") is True
        assert loaded_state.get_config("observability") is True

    def test_atomic_write_no_temp_file_left(self, state_manager, temp_state_file):
        """Test that atomic write doesn't leave temp files after success."""
        state_manager.set_phase(Phase.ACTIVATION)
        state_manager.save_state()

        # Temp file should not exist after successful write
        temp_file = str(temp_state_file) + ".tmp"
        assert not temp_file_exists(temp_file)
        # Main file should exist and be valid
        assert temp_state_file.exists()
        loaded = StateManager(str(temp_state_file))
        assert loaded.get_current_phase() == Phase.ACTIVATION

    def test_atomic_write_preserves_state_on_write_error(self, tmp_path):
        """Test that original state is preserved if write fails."""
        state_path = tmp_path / "atomic-test.json"
        sm = StateManager(str(state_path))
        sm.set_phase(Phase.PRIMARY_PREP)
        sm.mark_step_completed("step1")
        sm.save_state()

        # Verify initial state is saved
        assert state_path.exists()

        # Make temp file location unwritable to simulate write failure
        temp_file = str(state_path) + ".tmp"
        # Create temp as a directory to cause write failure
        os.makedirs(temp_file, exist_ok=True)

        with pytest.raises(OSError):
            sm.set_phase(Phase.ACTIVATION)

        # Clean up the directory we created
        os.rmdir(temp_file)

        # Original state should still be intact
        loaded = StateManager(str(state_path))
        assert loaded.get_current_phase() == Phase.PRIMARY_PREP
        assert loaded.is_step_completed("step1") is True

    def test_mark_step_completed_idempotency(self, state_manager):
        """Test that marking same step multiple times doesn't create duplicates."""
        state_manager.mark_step_completed("step1")
        state_manager.mark_step_completed("step1")  # Call again
        state_manager.mark_step_completed("step1")  # And again

        # Should only have one entry
        completed = state_manager.state["completed_steps"]
        assert len(completed) == 1
        assert completed[0]["name"] == "step1"

    def test_invalid_persisted_phase_raises_state_load_error(self, temp_state_file):
        """Unknown persisted phase must fail fast instead of being rewritten."""
        sm = StateManager(str(temp_state_file))
        sm.state["current_phase"] = "mystery-phase"
        sm.flush_state()  # Force write even if not dirty

        with pytest.raises(StateLoadError, match="Unknown phase"):
            StateManager(str(temp_state_file))

    def test_restore_runtime_checkpoint_restores_phase_and_timestamp_only(self, tmp_path):
        """Runtime checkpoint restore should preserve config updates while restoring durable state."""
        state_path = tmp_path / "runtime-checkpoint.json"
        sm = StateManager(str(state_path))
        original_timestamp = sm.state["last_updated"]
        checkpoint = sm.capture_runtime_checkpoint()

        sm.set_phase(Phase.PREFLIGHT)
        sm.set_config("primary_version", "2.14.0")

        sm.restore_runtime_checkpoint(checkpoint)

        reloaded = StateManager(str(state_path))
        assert reloaded.get_current_phase() == Phase.INIT
        assert reloaded.get_config("primary_version") == "2.14.0"
        assert reloaded.state["last_updated"] == original_timestamp

    def test_ensure_contexts_stores_values(self, tmp_path):
        """Contexts should be persisted and reloaded."""
        state_path = tmp_path / "ctx.json"
        sm = StateManager(str(state_path))
        sm.ensure_contexts("primary-a", "secondary-b")

        reloaded = StateManager(str(state_path))
        assert reloaded.state["contexts"]["primary"] == "primary-a"
        assert reloaded.state["contexts"]["secondary"] == "secondary-b"

    def test_ensure_contexts_resets_on_mismatch(self, tmp_path):
        """Mismatched contexts should trigger a reset to avoid stale state."""
        state_path = tmp_path / "ctx-reset.json"
        sm = StateManager(str(state_path))
        sm.ensure_contexts("primary-a", "secondary-b")
        sm.set_phase(Phase.PRIMARY_PREP)
        sm.mark_step_completed("step1")

        reloaded = StateManager(str(state_path))
        reloaded.ensure_contexts("primary-other", "secondary-b")

        assert reloaded.get_current_phase() == Phase.INIT
        assert reloaded.state["completed_steps"] == []

    def test_ensure_contexts_resets_when_missing_and_in_progress(self, tmp_path):
        """Missing contexts with in-progress state should reset for safety."""
        state_path = tmp_path / "ctx-missing.json"
        sm = StateManager(str(state_path))
        sm.set_phase(Phase.PRIMARY_PREP)
        sm.mark_step_completed("step1")

        reloaded = StateManager(str(state_path))
        reloaded.ensure_contexts("primary-a", "secondary-b")

        assert reloaded.get_current_phase() == Phase.INIT
        assert reloaded.state["completed_steps"] == []

    def test_get_state_age_valid_timestamp(self, state_manager):
        """Test get_state_age returns timedelta for valid timestamp."""
        # State was just created, so age should be very small
        age = state_manager.get_state_age()
        assert age is not None
        assert age.total_seconds() < 5  # Should be less than 5 seconds

    def test_get_state_age_with_z_suffix(self, tmp_path):
        """Test get_state_age handles 'Z' suffix timestamps."""
        import json

        state_path = tmp_path / "z-suffix.json"
        # Write state with 'Z' suffix timestamp (some systems produce this)
        state_data = {
            "version": "1.0",
            "current_phase": "init",
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": "2026-01-21T12:00:00Z",
            "contexts": {"primary": None, "secondary": None},
        }
        state_path.write_text(json.dumps(state_data))

        sm = StateManager(str(state_path))
        age = sm.get_state_age()

        assert age is not None
        # Should be positive (timestamp is in the past)
        assert age.total_seconds() > 0

    @patch("lib.utils.logging")
    def test_get_state_age_missing_timestamp(self, mock_logging, tmp_path):
        """Test get_state_age returns None and logs warning for missing timestamp."""
        import json

        state_path = tmp_path / "missing-ts.json"
        state_data = {
            "version": "1.0",
            "current_phase": "init",
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": "",
            "contexts": {"primary": None, "secondary": None},
        }
        state_path.write_text(json.dumps(state_data))

        sm = StateManager(str(state_path))
        age = sm.get_state_age()

        assert age is None
        mock_logging.warning.assert_called_with("State file missing last_updated timestamp")

    @patch("lib.utils.logging")
    def test_get_state_age_malformed_timestamp(self, mock_logging, tmp_path):
        """Test get_state_age returns None and logs warning for malformed timestamp."""
        import json

        state_path = tmp_path / "bad-ts.json"
        state_data = {
            "version": "1.0",
            "current_phase": "init",
            "completed_steps": [],
            "config": {},
            "errors": [],
            "last_updated": "not-a-timestamp",
            "contexts": {"primary": None, "secondary": None},
        }
        state_path.write_text(json.dumps(state_data))

        sm = StateManager(str(state_path))
        age = sm.get_state_age()

        assert age is None
        # Should have logged a warning about parsing failure
        assert mock_logging.warning.called
        call_args = mock_logging.warning.call_args[0]
        assert "Could not parse state timestamp" in call_args[0]


@pytest.mark.unit
class TestStateLoadSafety:
    """Tests for fail-fast behavior on corrupt or unreadable state files."""

    def test_corrupt_json_raises_state_load_error(self, tmp_path):
        """A corrupt state file must raise StateLoadError, not silently reset."""
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid json %%}")

        with pytest.raises(StateLoadError, match="corrupt"):
            StateManager(str(state_file))

    def test_corrupt_file_is_preserved_not_deleted(self, tmp_path):
        """The corrupt state file must remain in place while a forensic copy is created."""
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid}")

        with pytest.raises(StateLoadError):
            StateManager(str(state_file))

        assert state_file.exists(), "Original corrupt file should keep blocking reuse"
        corrupt_files = list(tmp_path.glob("state.json.corrupt.*"))
        assert len(corrupt_files) == 1, f"Expected one .corrupt.* file, found: {corrupt_files}"

    def test_corrupt_file_continues_blocking_until_removed(self, tmp_path):
        """The same corrupt state path must keep failing until the operator resets it."""
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid}")

        with pytest.raises(StateLoadError):
            StateManager(str(state_file))

        with pytest.raises(StateLoadError):
            StateManager(str(state_file))

    def test_unreadable_file_raises_state_load_error(self, tmp_path):
        """An unreadable state file must raise StateLoadError."""
        import stat as stat_mod

        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("Root can still read chmod 000 files")

        state_file = tmp_path / "state.json"
        state_file.write_text('{"version": "1.0"}')
        state_file.chmod(0o000)

        try:
            with pytest.raises(StateLoadError, match="cannot be read"):
                StateManager(str(state_file))
        finally:
            state_file.chmod(stat_mod.S_IRUSR | stat_mod.S_IWUSR)

    def test_missing_file_creates_fresh_state(self, tmp_path):
        """When no state file exists, a fresh one must be created without error."""
        state_file = tmp_path / "new-state.json"
        assert not state_file.exists()

        sm = StateManager(str(state_file))

        assert state_file.exists()
        assert sm.get_current_phase() == Phase.INIT

    def test_reset_state_flag_allows_recovery_from_corrupt_file(self, tmp_path):
        """Simulates the --reset-state path: delete file before constructing StateManager."""
        state_file = tmp_path / "state.json"
        state_file.write_text("{invalid}")

        # --reset-state removes the file before constructing StateManager
        state_file.unlink()
        sm = StateManager(str(state_file))

        assert sm.get_current_phase() == Phase.INIT

    def test_same_process_reuses_run_lock(self, tmp_path):
        """Multiple StateManager instances in the same process should share the run lock."""
        state_file = tmp_path / "state.json"

        sm1 = StateManager(str(state_file))
        sm2 = StateManager(str(state_file))

        assert sm1.get_current_phase() == Phase.INIT
        assert sm2.get_current_phase() == Phase.INIT

    @pytest.mark.skipif(fcntl is None, reason="fcntl unavailable on this platform")
    @patch("lib.utils.fcntl.flock")
    def test_conflicting_run_lock_raises_state_lock_error(self, mock_flock, tmp_path):
        """A conflicting OS-level lock should raise StateLockError during initialization."""
        state_file = tmp_path / "state.json"

        def side_effect(_fd, operation):
            if operation & fcntl.LOCK_NB:
                raise BlockingIOError("already locked")
            return None

        mock_flock.side_effect = side_effect

        with pytest.raises(StateLockError, match="already using state file"):
            StateManager(str(state_file))


@pytest.mark.unit
class TestPhaseResumeMetadata:
    """Tests for reliable failure metadata required by resume logic."""

    def test_add_error_captures_current_phase(self, tmp_path):
        """add_error records the current phase so get_last_error_phase can locate it."""
        sm = StateManager(str(tmp_path / "state.json"))
        sm.set_phase(Phase.ACTIVATION)
        sm.add_error("activation step failed")

        last_phase = sm.get_last_error_phase()
        assert last_phase == Phase.ACTIVATION

    def test_fail_phase_helper_records_both_error_and_failed_state(self, tmp_path):
        """_fail_phase must record an error entry AND set phase to FAILED."""
        import logging

        import acm_switchover

        sm = StateManager(str(tmp_path / "state.json"))
        sm.set_phase(Phase.ACTIVATION)

        logger = logging.getLogger("test")
        result = acm_switchover._fail_phase(sm, "something broke", logger)

        assert result is False
        assert sm.get_current_phase() == Phase.FAILED
        errors = sm.get_errors()
        assert len(errors) == 1
        assert errors[0]["phase"] == Phase.ACTIVATION.value


@pytest.mark.unit
class TestStateManagerExitRegistration:
    """Tests for exit-handler ordering and canonical lock paths."""

    def test_uses_realpath_for_run_lock_and_registers_release_first(self, tmp_path):
        """The lock path must follow the canonical target and release must register first."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "alias"
        if not hasattr(os, "symlink"):
            pytest.skip("symlink not supported on this platform")
        os.symlink(real_dir, link_dir)
        state_path = link_dir / "state.json"

        with patch("lib.utils.atexit.register") as register:
            sm = StateManager(str(state_path))

        assert sm._run_lock_path == os.path.realpath(str(state_path)) + ".run.lock"
        registered = [call.args[0].__name__ for call in register.call_args_list]
        assert registered == [
            "_release_run_lock",
            "_flush_on_exit",
            "_cleanup_temp_files",
        ]

    def test_fail_phase_helper_reuses_existing_same_phase_and_message_error(self, tmp_path):
        """_fail_phase should not append another error when the last entry matches phase and message."""
        import logging

        import acm_switchover

        sm = StateManager(str(tmp_path / "state.json"))
        sm.set_phase(Phase.ACTIVATION)
        sm.add_error("same failure", phase=Phase.ACTIVATION.value)

        logger = logging.getLogger("test")
        result = acm_switchover._fail_phase(sm, "same failure", logger)

        assert result is False
        assert sm.get_current_phase() == Phase.FAILED
        errors = sm.get_errors()
        assert len(errors) == 1
        assert errors[0]["error"] == "same failure"

    def test_fail_phase_helper_skips_generic_when_module_already_recorded_same_phase(self, tmp_path):
        """F8: _fail_phase should NOT append a generic wrapper when the module
        already recorded a specific error for the same phase."""
        import logging

        import acm_switchover

        sm = StateManager(str(tmp_path / "state.json"))
        sm.set_phase(Phase.ACTIVATION)
        sm.add_error("specific root cause", phase=Phase.ACTIVATION.value)

        logger = logging.getLogger("test")
        result = acm_switchover._fail_phase(sm, "Secondary hub activation failed!", logger)

        assert result is False
        assert sm.get_current_phase() == Phase.FAILED
        errors = sm.get_errors()
        # Only the module's specific error should be present
        assert len(errors) == 1
        assert errors[-1]["error"] == "specific root cause"

    def test_fail_phase_preserves_same_attempt_specific_error_without_generic_wrapper(self, tmp_path):
        """A retry should still prefer a new same-attempt specific error over the wrapper."""
        import logging

        import acm_switchover

        sm = StateManager(str(tmp_path / "state.json"))
        sm.set_phase(Phase.ACTIVATION)
        sm.add_error("stale prior failure", phase=Phase.ACTIVATION.value)
        sm.record_retry_error_baseline(Phase.ACTIVATION, 1)
        sm.add_error("specific root cause", phase=Phase.ACTIVATION.value)

        logger = logging.getLogger("test")
        result = acm_switchover._fail_phase(sm, "Secondary hub activation failed!", logger)

        assert result is False
        assert sm.get_current_phase() == Phase.FAILED
        errors = sm.get_errors()
        assert len(errors) == 2
        assert errors[-1]["error"] == "specific root cause"

    def test_retry_error_baseline_accessor_returns_copy(self, tmp_path):
        """Reading the retry baseline should not expose mutable internal state."""
        sm = StateManager(str(tmp_path / "state.json"))

        sm.record_retry_error_baseline(Phase.ACTIVATION, 2)
        baseline = sm.get_retry_error_baseline()

        assert baseline == {"phase": Phase.ACTIVATION.value, "count": 2}
        baseline["count"] = 999
        assert sm.get_retry_error_baseline() == {
            "phase": Phase.ACTIVATION.value,
            "count": 2,
        }

    def test_resume_after_failure_uses_recorded_phase(self, tmp_path):
        """After a phase failure, get_last_error_phase returns the phase to retry."""
        sm = StateManager(str(tmp_path / "state.json"))
        sm.set_phase(Phase.PRIMARY_PREP)
        sm.add_error("prep failed", phase=Phase.PRIMARY_PREP.value)
        sm.set_phase(Phase.FAILED)

        assert sm.get_last_error_phase() == Phase.PRIMARY_PREP


@pytest.mark.unit
class TestPhaseEnum:
    """Test cases for Phase enum."""

    def test_phase_values(self):
        """Test phase enum values."""
        expected = {
            Phase.INIT: "init",
            Phase.PREFLIGHT: "preflight_validation",
            Phase.PRIMARY_PREP: "primary_preparation",
            Phase.ACTIVATION: "activation",
            Phase.POST_ACTIVATION: "post_activation_verification",
            Phase.FINALIZATION: "finalization",
            Phase.COMPLETED: "completed",
            Phase.FAILED: "failed",
        }

        for phase, value in expected.items():
            assert phase.value == value


@pytest.mark.unit
class TestVersionComparison:
    """Test cases for version comparison utilities."""

    @pytest.mark.parametrize(
        "version1,version2",
        [
            ("2.12.0", "2.12.0"),
            ("2.11.5", "2.11.5"),
            ("3.0.0", "3.0.0"),
        ],
    )
    def test_is_acm_version_ge_equal(self, version1, version2):
        """Test version comparison with equal versions."""
        assert is_acm_version_ge(version1, version2) is True

    @pytest.mark.parametrize(
        "version1,version2",
        [
            ("2.12.0", "2.11.0"),
            ("2.12.5", "2.12.0"),
            ("3.0.0", "2.12.0"),
            ("2.12", "2.11"),
            ("2.12.0", "2.12"),
        ],
    )
    def test_is_acm_version_ge_greater(self, version1, version2):
        """Test version comparison with greater versions."""
        assert is_acm_version_ge(version1, version2) is True

    @pytest.mark.parametrize(
        "version1,version2",
        [
            ("2.11.0", "2.12.0"),
            ("2.12.0", "2.12.5"),
            ("2.12.0", "3.0.0"),
            ("2.11", "2.12"),
        ],
    )
    def test_is_acm_version_ge_lesser(self, version1, version2):
        """Test version comparison with lesser versions."""
        assert is_acm_version_ge(version1, version2) is False

    @pytest.mark.parametrize(
        "version1,version2",
        [
            ("invalid", "2.12.0"),
            ("2.12.0", "invalid"),
            ("", "2.12.0"),
        ],
    )
    def test_is_acm_version_ge_invalid(self, version1, version2):
        """Test version comparison with invalid versions."""
        assert is_acm_version_ge(version1, version2) is False


@pytest.mark.unit
class TestSetupLogging:
    """Test cases for logging setup."""

    def _mock_logging_env(self, mock_logging):
        root_logger = MagicMock()
        root_logger.handlers = [MagicMock()]
        named_logger = MagicMock()
        mock_logging.getLogger.side_effect = [root_logger, named_logger]
        return root_logger, named_logger

    @patch("lib.utils.logging")
    def test_setup_logging_default(self, mock_logging):
        """Test logging setup with default level."""
        root_logger, named_logger = self._mock_logging_env(mock_logging)

        result_logger = setup_logging()

        root_logger.setLevel.assert_called_once_with(mock_logging.INFO)
        assert root_logger.removeHandler.call_count == 1
        root_logger.addHandler.assert_called_once()
        assert result_logger is named_logger

    @patch("lib.utils.logging")
    def test_setup_logging_verbose(self, mock_logging):
        """Test logging setup with verbose flag."""
        root_logger, _ = self._mock_logging_env(mock_logging)

        setup_logging(verbose=True)

        root_logger.setLevel.assert_called_once_with(mock_logging.DEBUG)


@pytest.mark.unit
class TestDryRunSkipDecorator:
    """Test cases for the dry_run_skip decorator."""

    def test_decorator_skips_when_dry_run_true(self):
        """Test that decorator skips execution when dry_run is True."""

        class TestClass:
            def __init__(self):
                self.dry_run = True
                self.executed = False

            @dry_run_skip(message="Test skip message", return_value=42)
            def test_method(self):
                self.executed = True
                return 100

        obj = TestClass()
        result = obj.test_method()

        assert result == 42
        assert obj.executed is False

    def test_decorator_executes_when_dry_run_false(self):
        """Test that decorator allows execution when dry_run is False."""

        class TestClass:
            def __init__(self):
                self.dry_run = False
                self.executed = False

            @dry_run_skip(message="Test skip message", return_value=42)
            def test_method(self):
                self.executed = True
                return 100

        obj = TestClass()
        result = obj.test_method()

        assert result == 100
        assert obj.executed is True

    def test_decorator_with_nested_attribute(self):
        """Test decorator with dot-separated attribute path."""

        class Client:
            def __init__(self, dry_run: bool):
                self.dry_run = dry_run

        class TestClass:
            def __init__(self, client: Client):
                self.client = client
                self.executed = False

            @dry_run_skip(
                message="Nested dry run",
                return_value="skipped",
                dry_run_attr="client.dry_run",
            )
            def test_method(self):
                self.executed = True
                return "executed"

        # Test with dry_run=True
        obj = TestClass(Client(dry_run=True))
        result = obj.test_method()
        assert result == "skipped"
        assert obj.executed is False

        # Test with dry_run=False
        obj2 = TestClass(Client(dry_run=False))
        result2 = obj2.test_method()
        assert result2 == "executed"
        assert obj2.executed is True

    def test_decorator_with_missing_attribute(self):
        """When dry_run attribute doesn't exist at all, skip safely."""

        class TestClass:
            def __init__(self):
                self.executed = False
                # No dry_run attribute

            @dry_run_skip(message="Missing attr", return_value="skipped")
            def test_method(self):
                self.executed = True
                return "executed"

        obj = TestClass()
        result = obj.test_method()

        # Should skip since attribute is missing (safe default)
        assert result == "skipped"
        assert obj.executed is False

    def test_decorator_with_arguments(self):
        """Test decorator preserves function arguments."""

        class TestClass:
            def __init__(self):
                self.dry_run = False
                self.received_args = None

            @dry_run_skip(message="With args")
            def test_method(self, arg1, arg2, kwarg1=None):
                self.received_args = (arg1, arg2, kwarg1)
                return f"{arg1}-{arg2}-{kwarg1}"

        obj = TestClass()
        result = obj.test_method("a", "b", kwarg1="c")

        assert result == "a-b-c"
        assert obj.received_args == ("a", "b", "c")

    def test_decorator_callable_return_value_supports_keyword_invocation(self):
        """Callable return_value should receive the original keyword-based call shape."""

        class Client:
            def __init__(self):
                self.dry_run = True

        @dry_run_skip(
            message="Keyword invocation",
            return_value=lambda client, app=None, run_id=None: (client, app, run_id),
        )
        def pause_like_function(client, app=None, run_id=None):
            return "executed"

        client = Client()
        returned_client, returned_app, returned_run_id = pause_like_function(
            client=client,
            app={"metadata": {"name": "app-1"}},
            run_id="run-1",
        )

        assert returned_client is client
        assert returned_app == {"metadata": {"name": "app-1"}}
        assert returned_run_id == "run-1"

    def test_decorator_default_return_value_is_none(self):
        """Test decorator returns None by default when skipping."""

        class TestClass:
            def __init__(self):
                self.dry_run = True

            @dry_run_skip(message="Default return")
            def test_method(self):
                return "should not return this"

        obj = TestClass()
        result = obj.test_method()

        assert result is None

    @patch("lib.utils.logging.getLogger")
    def test_decorator_logs_message(self, mock_get_logger):
        """Test decorator logs the skip message."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        class TestClass:
            def __init__(self):
                self.dry_run = True

            @dry_run_skip(message="Custom skip message")
            def test_method(self):
                return "executed"

        obj = TestClass()
        obj.test_method()

        mock_get_logger.assert_called_with("acm_switchover")
        mock_logger.info.assert_called_with("[DRY-RUN] %s", "Custom skip message")

    def test_decorator_skips_when_intermediate_attribute_is_none(self):
        """When an intermediate in the dot-path is None, default to skipping (safe)."""

        class Outer:
            client = None  # intermediate is None

        @dry_run_skip(
            message="Should skip safely",
            return_value="skipped",
            dry_run_attr="client.dry_run",
        )
        def guarded_method(self):
            return "executed"

        obj = Outer()
        result = guarded_method(obj)
        assert result == "skipped"


@pytest.mark.unit
class TestFormatDuration:
    """Test cases for format_duration utility function."""

    def test_seconds_only(self):
        """Test formatting durations under 60 seconds."""
        from lib.utils import format_duration

        assert format_duration(0) == "0.0s"
        assert format_duration(1) == "1.0s"
        assert format_duration(30.5) == "30.5s"
        assert format_duration(59.9) == "59.9s"

    def test_minutes_only(self):
        """Test formatting durations between 1-60 minutes."""
        from lib.utils import format_duration

        assert format_duration(60) == "1.0m"
        assert format_duration(90) == "1.5m"
        assert format_duration(120) == "2.0m"
        assert format_duration(3599) == "60.0m"

    def test_hours(self):
        """Test formatting durations over 1 hour."""
        from lib.utils import format_duration

        assert format_duration(3600) == "1.0h"
        assert format_duration(5400) == "1.5h"
        assert format_duration(7200) == "2.0h"
        assert format_duration(36000) == "10.0h"

    def test_boundary_values(self):
        """Test boundary values between units."""
        from lib.utils import format_duration

        # Just under 1 minute should show seconds
        assert format_duration(59.99) == "60.0s"
        # Exactly 1 minute should show minutes
        assert format_duration(60.0) == "1.0m"
        # Just under 1 hour should show minutes
        assert format_duration(3599.99) == "60.0m"
        # Exactly 1 hour should show hours
        assert format_duration(3600.0) == "1.0h"


@pytest.mark.unit
class TestConfirmAction:
    """Test cases for confirm_action utility function."""

    def test_confirm_with_yes(self):
        """Test confirmation with 'y' or 'yes' responses."""
        from lib.utils import confirm_action

        with patch("builtins.input", return_value="y"):
            assert confirm_action("Continue?") is True

        with patch("builtins.input", return_value="Y"):
            assert confirm_action("Continue?") is True

        with patch("builtins.input", return_value="yes"):
            assert confirm_action("Continue?") is True

        with patch("builtins.input", return_value="YES"):
            assert confirm_action("Continue?") is True

    def test_confirm_with_no(self):
        """Test confirmation with 'n' or 'no' responses."""
        from lib.utils import confirm_action

        with patch("builtins.input", return_value="n"):
            assert confirm_action("Continue?") is False

        with patch("builtins.input", return_value="N"):
            assert confirm_action("Continue?") is False

        with patch("builtins.input", return_value="no"):
            assert confirm_action("Continue?") is False

        with patch("builtins.input", return_value="NO"):
            assert confirm_action("Continue?") is False

    def test_confirm_empty_with_default_false(self):
        """Test empty response uses default=False."""
        from lib.utils import confirm_action

        with patch("builtins.input", return_value=""):
            assert confirm_action("Continue?", default=False) is False

    def test_confirm_empty_with_default_true(self):
        """Test empty response uses default=True."""
        from lib.utils import confirm_action

        with patch("builtins.input", return_value=""):
            assert confirm_action("Continue?", default=True) is True

    def test_confirm_invalid_then_valid(self):
        """Test that invalid responses prompt again."""
        from lib.utils import confirm_action

        responses = iter(["maybe", "invalid", "y"])
        with patch("builtins.input", side_effect=responses):
            with patch("builtins.print") as mock_print:
                result = confirm_action("Continue?")
                assert result is True
                # Should have printed error messages for invalid inputs
                assert mock_print.call_count == 2

    def test_confirm_prompt_format_default_false(self):
        """Test prompt format with default=False shows [y/N]."""
        from lib.utils import confirm_action

        with patch("builtins.input", return_value="y") as mock_input:
            confirm_action("Continue?", default=False)
            mock_input.assert_called_once_with("Continue? [y/N]: ")

    def test_confirm_prompt_format_default_true(self):
        """Test prompt format with default=True shows [Y/n]."""
        from lib.utils import confirm_action

        with patch("builtins.input", return_value="n") as mock_input:
            confirm_action("Continue?", default=True)
            mock_input.assert_called_once_with("Continue? [Y/n]: ")


@pytest.mark.unit
class TestStepContext:
    """Test cases for the StepContext context manager."""

    def test_step_runs_when_not_completed(self, state_manager):
        """Test that step executes when not previously completed."""
        executed = False

        with state_manager.step("new_step") as should_run:
            if should_run:
                executed = True

        assert executed is True
        assert state_manager.is_step_completed("new_step") is True

    def test_step_skips_when_already_completed(self, state_manager):
        """Test that step is skipped when already completed."""
        state_manager.mark_step_completed("existing_step")
        executed = False

        with state_manager.step("existing_step") as should_run:
            if should_run:
                executed = True

        assert executed is False
        # Still completed
        assert state_manager.is_step_completed("existing_step") is True

    def test_step_logs_when_skipped(self, state_manager):
        """Test that skip message is logged when step already completed."""
        import logging

        state_manager.mark_step_completed("logged_step")
        mock_logger = MagicMock(spec=logging.Logger)

        with state_manager.step("logged_step", mock_logger) as should_run:
            pass

        mock_logger.info.assert_called_once_with("Step already completed: %s", "logged_step")
        assert should_run is False

    def test_step_not_marked_on_exception(self, state_manager):
        """Test that step is not marked completed if exception occurs."""
        with pytest.raises(ValueError):
            with state_manager.step("failing_step") as should_run:
                if should_run:
                    raise ValueError("Intentional failure")

        # Step should NOT be marked completed due to exception
        assert state_manager.is_step_completed("failing_step") is False

    def test_step_persists_completion(self, tmp_path):
        """Test that step completion is persisted to disk."""
        state_path = tmp_path / "step-persist.json"
        sm = StateManager(str(state_path))

        with sm.step("persisted_step") as should_run:
            if should_run:
                pass  # Do work

        # Reload and verify
        reloaded = StateManager(str(state_path))
        assert reloaded.is_step_completed("persisted_step") is True

    def test_step_without_logger(self, state_manager):
        """Test that step works without a logger."""
        state_manager.mark_step_completed("no_logger_step")

        # Should not raise even without logger
        with state_manager.step("no_logger_step") as should_run:
            assert should_run is False

    def test_step_multiple_sequential(self, state_manager):
        """Test multiple sequential steps."""
        results = []

        with state_manager.step("step_a") as should_run:
            if should_run:
                results.append("a")

        with state_manager.step("step_b") as should_run:
            if should_run:
                results.append("b")

        with state_manager.step("step_a") as should_run:  # Already done
            if should_run:
                results.append("a_again")

        assert results == ["a", "b"]
        assert state_manager.is_step_completed("step_a") is True
        assert state_manager.is_step_completed("step_b") is True


@pytest.mark.unit
class TestSignalAndAtexitHandlers:
    """Tests for signal handler registration and flush-on-exit logic."""

    def test_signal_handlers_registered_for_sigterm_and_sigint(self, tmp_path):
        """Test that StateManager registers signal handlers for SIGTERM and SIGINT."""
        import signal

        with patch("lib.utils.signal.signal") as mock_signal, patch("lib.utils.signal.getsignal") as mock_get:
            mock_get.return_value = signal.SIG_DFL
            sm = StateManager(str(tmp_path / "state.json"))

        # signal.signal should be called for both SIGTERM and SIGINT
        registered_signals = [call.args[0] for call in mock_signal.call_args_list]
        assert signal.SIGTERM in registered_signals
        assert signal.SIGINT in registered_signals

    def test_atexit_flush_calls_do_flush_when_dirty(self, tmp_path):
        """Test that the atexit handler flushes state when dirty."""
        sm = StateManager(str(tmp_path / "state.json"))
        sm._dirty = True

        with patch.object(sm, "_write_state") as mock_write:
            sm._flush_on_exit()

        mock_write.assert_called_once()
        assert sm._dirty is False

    def test_atexit_flush_skips_when_clean(self, tmp_path):
        """Test that the atexit handler does nothing when state is clean."""
        sm = StateManager(str(tmp_path / "state.json"))
        sm._dirty = False

        with patch.object(sm, "_write_state") as mock_write:
            sm._flush_on_exit()

        mock_write.assert_not_called()

    def test_flush_on_signal_flushes_dirty_state_and_forwards(self, tmp_path):
        """Test that the signal handler flushes dirty state and forwards the signal."""
        import signal

        sm = StateManager(str(tmp_path / "state.json"))
        sm._dirty = True

        with patch.object(sm, "_write_state"), patch.object(sm, "_forward_signal") as mock_fwd:
            sm._flush_on_signal(signal.SIGTERM, None)

        mock_fwd.assert_called_once_with(signal.SIGTERM, None)
        assert sm._dirty is False

    def test_forward_signal_calls_previous_handler(self, tmp_path):
        """Test that _forward_signal invokes a previously registered handler."""
        import signal

        sm = StateManager(str(tmp_path / "state.json"))
        prev_handler = MagicMock()
        sm._previous_signal_handlers[signal.SIGTERM] = prev_handler

        sm._forward_signal(signal.SIGTERM, None)

        prev_handler.assert_called_once_with(signal.SIGTERM, None)
