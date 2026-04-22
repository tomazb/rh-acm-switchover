"""Generic wait/poll utilities for ACM switchover workflows."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Callable, Optional


@dataclass(frozen=True)
class WaitConditionResult:
    """Explicit polling result with operator-safe public detail."""

    done: bool
    public_detail: str = ""

    @classmethod
    def complete(cls, public_detail: str = "") -> "WaitConditionResult":
        """Build a successful wait result."""
        return cls(done=True, public_detail=public_detail)

    @classmethod
    def pending(cls, public_detail: str = "") -> "WaitConditionResult":
        """Build an in-progress wait result."""
        return cls(done=False, public_detail=public_detail)


ConditionFn = Callable[[], WaitConditionResult]


def _require_wait_condition_result(result: object) -> WaitConditionResult:
    """Validate that polling callbacks return the explicit wait contract."""

    if isinstance(result, WaitConditionResult):
        return result

    raise TypeError("condition_fn must return WaitConditionResult")


def wait_for_condition(
    description: str,
    condition_fn: ConditionFn,
    *,
    timeout: int = 600,
    interval: int = 30,
    fast_interval: Optional[int] = None,
    fast_timeout: int = 0,
    allow_success_after_timeout: bool = False,
    logger: logging.Logger,
) -> bool:
    """Poll until a condition succeeds or timeout expires."""

    start_time = time.time()
    last_result: Optional[WaitConditionResult] = None
    logger.info("Waiting for %s (timeout: %ss)...", description, timeout)

    while time.time() - start_time < timeout:
        result = _require_wait_condition_result(condition_fn())
        last_result = result

        if result.done:
            if result.public_detail:
                logger.info("%s complete: %s", description, result.public_detail)
            else:
                logger.info("%s complete", description)
            return True

        elapsed = int(time.time() - start_time)
        if result.public_detail:
            logger.debug("%s in progress: %s (elapsed: %ss)", description, result.public_detail, elapsed)
        else:
            logger.debug("%s in progress (elapsed: %ss)", description, elapsed)

        sleep_interval = interval
        if fast_interval:
            if fast_timeout <= 0 or elapsed < fast_timeout:
                sleep_interval = fast_interval
        time.sleep(sleep_interval)

    if allow_success_after_timeout:
        result = _require_wait_condition_result(condition_fn())
        last_result = result
        if result.done:
            if result.public_detail:
                logger.info("%s complete: %s", description, result.public_detail)
            else:
                logger.info("%s complete", description)
            return True

    if last_result and last_result.public_detail:
        logger.warning("%s not complete after %ss timeout: %s", description, timeout, last_result.public_detail)
    else:
        logger.warning("%s not complete after %ss timeout", description, timeout)
    return False
