"""Generic wait/poll utilities for ACM switchover workflows."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Tuple

ConditionFn = Callable[[], Tuple[bool, str]]


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

    while time.time() - start_time < timeout:
        done, _detail = condition_fn()

        if done:
            return True

        elapsed = int(time.time() - start_time)

        sleep_interval = interval
        if fast_interval:
            if fast_timeout <= 0 or elapsed < fast_timeout:
                sleep_interval = fast_interval
        time.sleep(sleep_interval)

    if allow_success_after_timeout:
        done, _detail = condition_fn()
        if done:
            return True

    return False
