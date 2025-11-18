"""Unit tests for lib/utils.py."""

import unittest
import tempfile
import json
import os
from unittest.mock import patch, MagicMock
import sys

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from lib.utils import StateManager, Phase, is_acm_version_ge, setup_logging


class TestStateManager(unittest.TestCase):
    """Test cases for StateManager class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test-state.json")
        self.state = StateManager(self.state_file)

    def tearDown(self):
        """Clean up test fixtures."""
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def test_initial_state(self):
        """Test initial state creation."""
        self.assertEqual(self.state.get_current_phase(), Phase.INIT)
        self.assertFalse(self.state.is_step_completed("any_step"))
        self.assertEqual(self.state.get_config("key"), None)

    def test_bare_filename(self):
        """Test that StateManager works with bare filename (no directory)."""
        # Use a bare filename in temp directory
        bare_file = os.path.join(self.temp_dir, "state.json")
        state = StateManager(bare_file)
        
        # Should be able to save without FileNotFoundError
        state.set_phase(Phase.PREFLIGHT)
        state.mark_step_completed("test_step")
        
        # Verify file was created and can be loaded
        self.assertTrue(os.path.exists(bare_file))
        loaded = StateManager(bare_file)
        self.assertEqual(loaded.get_current_phase(), Phase.PREFLIGHT)
        
        # Cleanup
        if os.path.exists(bare_file):
            os.remove(bare_file)

    def test_set_phase(self):
        """Test phase transition."""
        self.state.set_phase(Phase.PREFLIGHT)
        self.assertEqual(self.state.get_current_phase(), Phase.PREFLIGHT)
        
        # Load in new instance to verify persistence
        new_state = StateManager(self.state_file)
        self.assertEqual(new_state.get_current_phase(), Phase.PREFLIGHT)

    def test_mark_step_completed(self):
        """Test marking steps as completed."""
        self.state.mark_step_completed("step1")
        self.assertTrue(self.state.is_step_completed("step1"))
        self.assertFalse(self.state.is_step_completed("step2"))
        
        # Verify timestamp exists in the list of completed steps
        completed = self.state.state["completed_steps"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["name"], "step1")
        self.assertIn("timestamp", completed[0])

    def test_set_get_config(self):
        """Test configuration storage."""
        self.state.set_config("acm_version", "2.12.0")
        self.assertEqual(self.state.get_config("acm_version"), "2.12.0")
        self.assertEqual(self.state.get_config("nonexistent"), None)
        
        # Test with default
        self.assertEqual(self.state.get_config("missing", "default"), "default")

    def test_add_error(self):
        """Test error recording."""
        self.state.add_error("Test error", Phase.PREFLIGHT.value)
        errors = self.state.state["errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["error"], "Test error")
        self.assertEqual(errors[0]["phase"], Phase.PREFLIGHT.value)
        self.assertIn("timestamp", errors[0])

    def test_reset(self):
        """Test state reset."""
        self.state.set_phase(Phase.ACTIVATION)
        self.state.mark_step_completed("step1")
        self.state.set_config("key", "value")
        self.state.add_error("error")
        
        self.state.reset()
        
        self.assertEqual(self.state.get_current_phase(), Phase.INIT)
        self.assertFalse(self.state.is_step_completed("step1"))
        self.assertEqual(self.state.get_config("key"), None)
        self.assertEqual(len(self.state.state["errors"]), 0)

    def test_persistence(self):
        """Test state persistence to file."""
        self.state.set_phase(Phase.PRIMARY_PREP)
        self.state.mark_step_completed("backup_paused")
        self.state.set_config("observability", True)
        
        # Load in new instance
        loaded_state = StateManager(self.state_file)
        self.assertEqual(loaded_state.get_current_phase(), Phase.PRIMARY_PREP)
        self.assertTrue(loaded_state.is_step_completed("backup_paused"))
        self.assertEqual(loaded_state.get_config("observability"), True)

    def test_mark_step_completed_idempotency(self):
        """Test that marking same step multiple times doesn't create duplicates."""
        self.state.mark_step_completed("step1")
        self.state.mark_step_completed("step1")  # Call again
        self.state.mark_step_completed("step1")  # And again
        
        # Should only have one entry
        completed = self.state.state["completed_steps"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["name"], "step1")


class TestPhaseEnum(unittest.TestCase):
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
            self.assertEqual(phase.value, value)


class TestVersionComparison(unittest.TestCase):
    """Test cases for version comparison utilities."""

    def test_is_acm_version_ge_equal(self):
        """Test version comparison with equal versions."""
        self.assertTrue(is_acm_version_ge("2.12.0", "2.12.0"))
        self.assertTrue(is_acm_version_ge("2.11.5", "2.11.5"))

    def test_is_acm_version_ge_greater(self):
        """Test version comparison with greater versions."""
        self.assertTrue(is_acm_version_ge("2.12.0", "2.11.0"))
        self.assertTrue(is_acm_version_ge("2.12.5", "2.12.0"))
        self.assertTrue(is_acm_version_ge("3.0.0", "2.12.0"))

    def test_is_acm_version_ge_lesser(self):
        """Test version comparison with lesser versions."""
        self.assertFalse(is_acm_version_ge("2.11.0", "2.12.0"))
        self.assertFalse(is_acm_version_ge("2.12.0", "2.12.5"))
        self.assertFalse(is_acm_version_ge("2.12.0", "3.0.0"))

    def test_is_acm_version_ge_different_lengths(self):
        """Test version comparison with different version string lengths."""
        self.assertTrue(is_acm_version_ge("2.12", "2.11"))
        self.assertTrue(is_acm_version_ge("2.12.0", "2.12"))
        self.assertFalse(is_acm_version_ge("2.11", "2.12"))

    def test_is_acm_version_ge_invalid(self):
        """Test version comparison with invalid versions."""
        # Should return False for invalid versions
        self.assertFalse(is_acm_version_ge("invalid", "2.12.0"))
        self.assertFalse(is_acm_version_ge("2.12.0", "invalid"))
        self.assertFalse(is_acm_version_ge("", "2.12.0"))


class TestSetupLogging(unittest.TestCase):
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
        self.assertEqual(call_kwargs['level'], mock_logging.DEBUG)

    @patch('lib.utils.logging')
    def test_setup_logging_info(self, mock_logging):
        """Test logging setup with info level."""
        setup_logging(verbose=False)
        mock_logging.basicConfig.assert_called_once()
        call_kwargs = mock_logging.basicConfig.call_args[1]
        self.assertEqual(call_kwargs['level'], mock_logging.INFO)


if __name__ == '__main__':
    unittest.main()
