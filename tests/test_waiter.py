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
        mock_logger.info.assert_any_call("Waiting for %s...", "test wait")
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
    def test_wait_caps_sleep_to_remaining_timeout_budget(self, mock_time, mock_logger):
        """Polling sleeps must not overshoot the remaining wall-clock timeout budget."""
        mock_time.time.side_effect = [0, 8, 8, 11]
        condition = Mock(return_value=WaitConditionResult.pending("waiting"))

        result = wait_for_condition(
            description="bounded wait",
            condition_fn=condition,
            logger=mock_logger,
            timeout=10,
            interval=30,
        )

        assert result is False
        mock_time.sleep.assert_called_once_with(2)

    @patch("lib.waiter.time")
    def test_wait_uses_float_remaining_timeout_budget(self, mock_time, mock_logger):
        """Remaining timeout math should not truncate elapsed time before sleeping."""
        mock_time.time.side_effect = [0, 8.75, 8.75, 10.1]
        condition = Mock(return_value=WaitConditionResult.pending("waiting"))

        result = wait_for_condition(
            description="precise wait",
            condition_fn=condition,
            logger=mock_logger,
            timeout=10,
            interval=30,
        )

        assert result is False
        mock_time.sleep.assert_called_once_with(1.25)

    @patch("lib.waiter.time")
    def test_wait_fast_interval_respects_remaining_timeout_budget(self, mock_time, mock_logger):
        """Fast polling must still be capped by the remaining timeout budget."""
        mock_time.time.side_effect = [0, 4, 4, 6]
        condition = Mock(return_value=WaitConditionResult.pending("waiting"))

        result = wait_for_condition(
            description="bounded fast wait",
            condition_fn=condition,
            logger=mock_logger,
            timeout=5,
            interval=30,
            fast_interval=10,
            fast_timeout=20,
        )

        assert result is False
        mock_time.sleep.assert_called_once_with(1)

    @patch("lib.waiter.time")
    def test_wait_rejects_legacy_tuple_contract(self, mock_time, mock_logger):
        """Test legacy tuple results are rejected in favor of the explicit contract."""
        mock_time.time.return_value = 0

        def condition():
            return True, "done"

        with pytest.raises(TypeError, match="WaitConditionResult"):
            wait_for_condition(description="test progress", condition_fn=condition, logger=mock_logger)

    @patch("lib.waiter.time")
    def test_wait_timeout_zero_expires_immediately(self, mock_time, mock_logger):
        """A zero timeout should expire before the first poll attempt."""
        mock_time.time.side_effect = [0, 0]
        condition = Mock(return_value=WaitConditionResult.pending("still waiting"))

        result = wait_for_condition(
            description="zero timeout",
            condition_fn=condition,
            timeout=0,
            logger=mock_logger,
        )

        assert result is False
        condition.assert_not_called()
        mock_time.sleep.assert_not_called()
        mock_logger.warning.assert_called_once_with("%s not complete before timeout", "zero timeout")

    @patch("lib.waiter.time")
    def test_wait_fast_timeout_zero_disables_fast_interval(self, mock_time, mock_logger):
        """fast_timeout=0 should bypass fast polling and use the standard interval."""
        mock_time.time.side_effect = [0, 1, 1, 41]
        condition = Mock(return_value=WaitConditionResult.pending("waiting"))

        result = wait_for_condition(
            description="standard interval wait",
            condition_fn=condition,
            timeout=40,
            interval=30,
            fast_interval=10,
            fast_timeout=0,
            logger=mock_logger,
        )

        assert result is False
        mock_time.sleep.assert_called_once_with(30)

    @patch("lib.waiter.time")
    def test_wait_timeout(self, mock_time, mock_logger):
        """Test condition times out without logging raw timeout configuration."""
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
            "%s not complete before timeout: %s", "test timeout", "still waiting"
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
        mock_logger.warning.assert_called_once_with("%s not complete before timeout", "test no last chance")


@pytest.mark.unit
class TestWaitForRestoreDeletion:
    def _client(self, side_effect):
        client = Mock()
        client.get_custom_resource.side_effect = side_effect
        return client

    def test_completes_when_restore_absent(self):
        from lib.waiter import wait_for_restore_deletion

        client = self._client([None])
        wait_for_restore_deletion(client, "restore-x", dry_run=False, timeout=5)
        client.get_custom_resource.assert_called_once_with(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="restores",
            name="restore-x",
            namespace="open-cluster-management-backup",
        )

    def test_polls_until_absent(self):
        from lib.waiter import wait_for_restore_deletion

        client = self._client([{"status": {"phase": "Deleting"}}, None])
        with patch("lib.waiter.time.sleep", lambda _s: None):
            wait_for_restore_deletion(client, "restore-x", dry_run=False, timeout=30)
        assert client.get_custom_resource.call_count == 2

    def test_timeout_raises_fatal_error_with_where_suffix(self):
        from lib.exceptions import FatalError
        from lib.waiter import wait_for_restore_deletion

        client = self._client(lambda **_kw: {"status": {"phase": "Deleting"}})
        clock = iter(range(0, 100_000, 60))
        with patch("lib.waiter.time.sleep", lambda _s: None), patch("lib.waiter.time.time", lambda: next(clock)):
            with pytest.raises(FatalError, match=r"restore restore-x to be deleted on primary after 120s"):
                wait_for_restore_deletion(client, "restore-x", dry_run=False, timeout=120, where=" on primary")

    def test_dry_run_skips_polling(self):
        from lib.waiter import wait_for_restore_deletion

        client = self._client(AssertionError("must not poll in dry run"))
        wait_for_restore_deletion(client, "restore-x", dry_run=True)
        client.get_custom_resource.assert_not_called()
