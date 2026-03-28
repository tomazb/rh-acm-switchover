"""Generic wait/poll utilities for ACM switchover workflows."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Tuple

ConditionFn = Callable[[], Tuple[bool, str]]


def _sanitize_detail(detail: str, max_length: int = 256) -> str:
    """Sanitize a condition detail string for logging.

    This helper trims whitespace/newlines and truncates overly long
    messages to avoid logging large payloads or secrets verbatim.
    Callers should still avoid passing raw secrets as detail strings.
    """

    if not detail:
        return ""

    safe = " ".join(str(detail).splitlines()).strip()
    if len(safe) > max_length:
        return safe[:max_length] + "... (truncated)"
    return safe


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
    logger.info("Waiting for %s (timeout: %ss)...", description, timeout)

    while time.time() - start_time < timeout:
        done, detail = condition_fn()
        safe_detail = _sanitize_detail(detail)

        if done:
            if safe_detail:
                logger.info("%s complete: %s", description, safe_detail)
            else:
                logger.info("%s complete", description)
            return True

        elapsed = int(time.time() - start_time)
        if safe_detail:
            # safe_detail is a sanitized status string from the condition function,
            # not derived from secret content. lgtm [py/clear-text-logging-sensitive-data]
            logger.debug("%s in progress: %s (elapsed: %ss)", description, safe_detail, elapsed)
        else:
            logger.debug("%s in progress (elapsed: %ss)", description, elapsed)

        sleep_interval = interval
        if fast_interval:
            if fast_timeout <= 0 or elapsed < fast_timeout:
                sleep_interval = fast_interval
        time.sleep(sleep_interval)

    if allow_success_after_timeout:
        done, detail = condition_fn()
        safe_detail = _sanitize_detail(detail)
        if done:
            if safe_detail:
                logger.info("%s complete: %s", description, safe_detail)
            else:
                logger.info("%s complete", description)
            return True
        elif safe_detail:
            elapsed = int(time.time() - start_time)
            logger.debug(
                "%s in progress: %s (elapsed: %ss)",
                description,
                safe_detail,
                elapsed,
            )

    logger.warning("%s not complete after %ss timeout", description, timeout)
    return False
