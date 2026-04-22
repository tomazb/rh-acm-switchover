"""Unit tests for lib/waiter.py.

Tests generic wait/poll utilities.
"""

import logging
from unittest.mock import Mock, patch

import pytest

from lib.waiter import WaitConditionResult, wait_for_condition


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return Mock(spec=logging.Logger)


@pytest.mark.unit
class TestWaitForCondition:
    """Tests for wait_for_condition function."""

    @patch("lib.waiter.time")
    def test_wait_success_immediate_logs_description_and_public_detail(self, mock_time, mock_logger):
        """Test condition succeeds immediately and logs explicit public detail."""
        mock_time.time.return_value = 0

        def condition():
            return WaitConditionResult.complete("done")

        result = wait_for_condition(description="test wait", condition_fn=condition, logger=mock_logger)

        assert result is True
        mock_logger.info.assert_any_call("Waiting for %s (timeout: %ss)...", "test wait", 600)
        mock_logger.info.assert_any_call("%s complete: %s", "test wait", "done")
        mock_logger.debug.assert_not_called()
        mock_time.sleep.assert_not_called()

    @patch("lib.waiter.time")
    def test_wait_success_after_retry(self, mock_time, mock_logger):
        """Test condition succeeds after a few retries."""
        # time.time() calls:
        # 1. start_time = 0
        # 2. loop 1 check = 10
        # 3. loop 1 elapsed = 10
        # 4. loop 2 check = 20
        mock_time.time.side_effect = [0, 10, 10, 20]

        # condition() calls: fail, success
        condition = Mock(
            side_effect=[
                WaitConditionResult.pending("waiting"),
                WaitConditionResult.complete("done"),
            ]
        )

        result = wait_for_condition(
            description="test retry",
            condition_fn=condition,
            logger=mock_logger,
            interval=5,
        )

        assert result is True
        assert condition.call_count == 2
        mock_logger.debug.assert_called_once_with("%s in progress: %s (elapsed: %ss)", "test retry", "waiting", 10)
        mock_logger.info.assert_any_call("%s complete: %s", "test retry", "done")
        mock_time.sleep.assert_called_once_with(5)

    @patch("lib.waiter.time")
    def test_wait_rejects_legacy_tuple_contract(self, mock_time, mock_logger):
        """Test legacy tuple results are rejected in favor of the explicit contract."""
        mock_time.time.return_value = 0

        def condition():
            return True, "done"

        with pytest.raises(TypeError, match="WaitConditionResult"):
            wait_for_condition(description="test progress", condition_fn=condition, logger=mock_logger)

    @patch("lib.waiter.time")
    def test_wait_timeout(self, mock_time, mock_logger):
        """Test condition times out."""
        # time.time() calls:
        # 1. start_time = 0
        # 2. loop check = 100 (timeout exceeded)
        # 3. final elapsed check = 100
        mock_time.time.side_effect = [0, 10, 10, 100]

        condition = Mock(return_value=WaitConditionResult.pending("still waiting"))

        result = wait_for_condition(
            description="test timeout",
            condition_fn=condition,
            timeout=50,
            logger=mock_logger,
        )

        assert result is False
        mock_logger.warning.assert_called_once_with(
            "%s not complete after %ss timeout: %s", "test timeout", 50, "still waiting"
        )

    @patch("lib.waiter.time")
    def test_wait_success_on_last_check(self, mock_time, mock_logger):
        """Test condition succeeds exactly on the final check after loop exit when enabled."""
        # Simulate loop exit due to timeout
        mock_time.time.side_effect = [0, 100]

        # But the final check (after loop) succeeds
        condition = Mock(return_value=WaitConditionResult.complete("just in time"))

        result = wait_for_condition(
            description="test last chance",
            condition_fn=condition,
            timeout=50,
            allow_success_after_timeout=True,
            logger=mock_logger,
        )

        assert result is True
        mock_logger.info.assert_any_call("%s complete: %s", "test last chance", "just in time")

    @patch("lib.waiter.time")
    def test_wait_timeout_no_last_chance(self, mock_time, mock_logger):
        """Test timeout does not succeed after loop exit by default."""
        mock_time.time.side_effect = [0, 100]

        condition = Mock(return_value=WaitConditionResult.complete("late"))

        result = wait_for_condition(
            description="test no last chance",
            condition_fn=condition,
            timeout=50,
            logger=mock_logger,
        )

        assert result is False
        mock_logger.warning.assert_called_once_with("%s not complete after %ss timeout", "test no last chance", 50)
