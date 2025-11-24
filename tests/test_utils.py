"""Unit tests for lib/utils.py.

Modernized pytest tests with fixtures, markers, and better organization.
Tests cover StateManager, Phase enum, version comparison, and logging setup.
"""

import tempfile
import json
import os
from unittest.mock import patch, MagicMock
import pytest

from lib.utils import StateManager, Phase, is_acm_version_ge, setup_logging


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

    @pytest.mark.parametrize("version1,version2", [
        ("2.12.0", "2.12.0"),
        ("2.11.5", "2.11.5"),
        ("3.0.0", "3.0.0"),
    ])
    def test_is_acm_version_ge_equal(self, version1, version2):
        """Test version comparison with equal versions."""
        assert is_acm_version_ge(version1, version2) is True

    @pytest.mark.parametrize("version1,version2", [
        ("2.12.0", "2.11.0"),
        ("2.12.5", "2.12.0"),
        ("3.0.0", "2.12.0"),
        ("2.12", "2.11"),
        ("2.12.0", "2.12"),
    ])
    def test_is_acm_version_ge_greater(self, version1, version2):
        """Test version comparison with greater versions."""
        assert is_acm_version_ge(version1, version2) is True

    @pytest.mark.parametrize("version1,version2", [
        ("2.11.0", "2.12.0"),
        ("2.12.0", "2.12.5"),
        ("2.12.0", "3.0.0"),
        ("2.11", "2.12"),
    ])
    def test_is_acm_version_ge_lesser(self, version1, version2):
        """Test version comparison with lesser versions."""
        assert is_acm_version_ge(version1, version2) is False

    @pytest.mark.parametrize("version1,version2", [
        ("invalid", "2.12.0"),
        ("2.12.0", "invalid"),
        ("", "2.12.0"),
    ])
    def test_is_acm_version_ge_invalid(self, version1, version2):
        """Test version comparison with invalid versions."""
        assert is_acm_version_ge(version1, version2) is False

@pytest.mark.unit
class TestSetupLogging:
    """Test cases for logging setup."""

    @patch('lib.utils.logging')
    def test_setup_logging_default(self, mock_logging):
        """Test logging setup with default level."""
        setup_logging()
        mock_logging.basicConfig.assert_called_once()

    @patch('lib.utils.logging')
    def test_setup_logging_verbose(self, mock_logging):
        """Test logging setup with verbose flag."""
        setup_logging(verbose=True)
        mock_logging.basicConfig.assert_called_once()
        # Check that DEBUG level is used
        call_kwargs = mock_logging.basicConfig.call_args[1]
        assert call_kwargs['level'] == mock_logging.DEBUG

    @patch('lib.utils.logging')
    def test_setup_logging_info(self, mock_logging):
        """Test logging setup with info level."""
        setup_logging(verbose=False)
        mock_logging.basicConfig.assert_called_once()
        call_kwargs = mock_logging.basicConfig.call_args[1]
        assert call_kwargs['level'] == mock_logging.INFO
