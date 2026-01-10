"""Unit tests for lib/utils.py.

Modernized pytest tests with fixtures, markers, and better organization.
Tests cover StateManager, Phase enum, version comparison, and logging setup.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

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

    def test_set_get_config(self, state_manager):
        """Test configuration storage."""
        state_manager.set_config("acm_version", "2.12.0")
        assert state_manager.get_config("acm_version") == "2.12.0"
        assert state_manager.get_config("nonexistent") is None

        # Test with default
        assert state_manager.get_config("missing", "default") == "default"

    def test_add_error(self, state_manager):
        """Test error recording."""
        state_manager.add_error("Test error", Phase.PREFLIGHT.value)
        errors = state_manager.state["errors"]
        assert len(errors) == 1
        assert errors[0]["error"] == "Test error"
        assert errors[0]["phase"] == Phase.PREFLIGHT.value
        assert "timestamp" in errors[0]

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

    @patch("lib.utils.logging")
    def test_get_current_phase_handles_unknown(self, mock_logging, temp_state_file):
        """Unknown persisted phase should reset to INIT with warning."""
        sm = StateManager(str(temp_state_file))
        sm.state["current_phase"] = "mystery-phase"
        sm.save_state()

        # Reload to simulate separate run
        sm_reloaded = StateManager(str(temp_state_file))
        phase = sm_reloaded.get_current_phase()

        assert phase == Phase.INIT
        assert sm_reloaded.state["current_phase"] == Phase.INIT.value
        mock_logging.warning.assert_called()

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

    @patch("lib.utils.logging")
    def test_setup_logging_info(self, mock_logging):
        """Test logging setup with info level."""
        root_logger, _ = self._mock_logging_env(mock_logging)

        setup_logging(verbose=False)

        root_logger.setLevel.assert_called_once_with(mock_logging.INFO)


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
        """Test decorator gracefully handles missing attribute."""

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

        # Should execute since attribute is missing (falsy)
        assert result == "executed"
        assert obj.executed is True

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
