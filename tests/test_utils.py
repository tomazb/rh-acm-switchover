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
        self.assertEqual(self.state.get_phase(), Phase.INIT)
        self.assertFalse(self.state.is_step_completed("any_step"))
        self.assertEqual(self.state.get_config("key"), None)

    def test_set_phase(self):
        """Test phase transition."""
        self.state.set_phase(Phase.PREFLIGHT)
        self.assertEqual(self.state.get_phase(), Phase.PREFLIGHT)
        self.state.save()
        
        # Load in new instance
        new_state = StateManager(self.state_file)
        self.assertEqual(new_state.get_phase(), Phase.PREFLIGHT)

    def test_mark_step_completed(self):
        """Test marking steps as completed."""
        self.state.mark_step_completed("step1")
        self.assertTrue(self.state.is_step_completed("step1"))
        self.assertFalse(self.state.is_step_completed("step2"))
        
        # Verify timestamp exists
        completed = self.state.state["completed_steps"]
        self.assertIn("timestamp", completed["step1"])

    def test_set_get_config(self):
        """Test configuration storage."""
        self.state.set_config("acm_version", "2.12.0")
        self.assertEqual(self.state.get_config("acm_version"), "2.12.0")
        self.assertEqual(self.state.get_config("nonexistent"), None)
        
        # Test with default
        self.assertEqual(self.state.get_config("missing", "default"), "default")

    def test_record_error(self):
        """Test error recording."""
        self.state.record_error("Test error", Phase.PREFLIGHT)
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
        self.state.record_error("error")
        
        self.state.reset()
        
        self.assertEqual(self.state.get_phase(), Phase.INIT)
        self.assertFalse(self.state.is_step_completed("step1"))
        self.assertEqual(self.state.get_config("key"), None)
        self.assertEqual(len(self.state.state["errors"]), 0)

    def test_persistence(self):
        """Test state persistence to file."""
        self.state.set_phase(Phase.PRIMARY_PREP)
        self.state.mark_step_completed("backup_paused")
        self.state.set_config("observability", True)
        self.state.save()
        
        # Load in new instance
        loaded_state = StateManager(self.state_file)
        self.assertEqual(loaded_state.get_phase(), Phase.PRIMARY_PREP)
        self.assertTrue(loaded_state.is_step_completed("backup_paused"))
        self.assertEqual(loaded_state.get_config("observability"), True)

    def test_get_all_completed_steps(self):
        """Test retrieval of all completed steps."""
        self.state.mark_step_completed("step1")
        self.state.mark_step_completed("step2")
        
        steps = self.state.get_all_completed_steps()
        self.assertIn("step1", steps)
        self.assertIn("step2", steps)
        self.assertEqual(len(steps), 2)


class TestPhaseEnum(unittest.TestCase):
    """Test cases for Phase enum."""

    def test_phase_values(self):
        """Test phase enum values."""
        self.assertEqual(Phase.INIT.value, "init")
        self.assertEqual(Phase.PREFLIGHT.value, "preflight")
        self.assertEqual(Phase.PRIMARY_PREP.value, "primary_prep")
        self.assertEqual(Phase.ACTIVATION.value, "activation")
        self.assertEqual(Phase.POST_ACTIVATION.value, "post_activation")
        self.assertEqual(Phase.FINALIZATION.value, "finalization")
        self.assertEqual(Phase.COMPLETED.value, "completed")
        self.assertEqual(Phase.FAILED.value, "failed")
        self.assertEqual(Phase.ROLLBACK.value, "rollback")


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
