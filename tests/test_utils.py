"""Unit tests for lib/utils.py.

Modernized pytest tests with fixtures, markers, and better organization.
Tests cover StateManager, Phase enum, version comparison, and logging setup.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from lib.utils import Phase, StateManager, dry_run_skip, is_acm_version_ge, setup_logging


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
            Phase.ROLLBACK: "rollback",
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
        mock_logger.info.assert_called_with("[DRY-RUN] Custom skip message")
