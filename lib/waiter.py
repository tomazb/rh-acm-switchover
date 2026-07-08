"""Generic wait/poll utilities for ACM switchover workflows."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from lib.constants import (
    BACKUP_NAMESPACE,
    CLUSTER_BACKUP_API_GROUP,
    CLUSTER_BACKUP_API_VERSION,
    RESTORE_FAST_POLL_INTERVAL,
    RESTORE_FAST_POLL_TIMEOUT,
    RESTORE_PLURAL,
    RESTORE_POLL_INTERVAL,
    RESTORE_WAIT_TIMEOUT,
)
from lib.exceptions import FatalError

PUBLIC_DETAIL_MAX_LENGTH = 500
PUBLIC_LIST_MAX_ITEMS = 20


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


def format_public_detail(detail: str, *, max_length: int = PUBLIC_DETAIL_MAX_LENGTH) -> str:
    """Return a deterministic, bounded public detail string for operator logs."""

    text = str(detail)
    if len(text) <= max_length:
        return text
    if max_length <= 0:
        return ""

    omitted = len(text)
    while True:
        marker = f"... [truncated {omitted} chars]"
        if len(marker) >= max_length:
            return marker[:max_length]

        keep = max_length - len(marker)
        new_omitted = len(text) - keep
        if new_omitted == omitted:
            return text[:keep] + marker
        omitted = new_omitted


def format_public_list(
    values: list[str],
    *,
    max_items: int = PUBLIC_LIST_MAX_ITEMS,
    max_length: int = PUBLIC_DETAIL_MAX_LENGTH,
) -> str:
    """Format a bounded comma-separated list for public log detail."""

    shown = [str(value) for value in values[:max_items]]
    omitted = len(values) - len(shown)
    text = ", ".join(shown)
    if omitted > 0:
        text = f"{text}, ... ({omitted} more)"
    return format_public_detail(text, max_length=max_length)


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
    logger.info("Waiting for %s...", description)

    while time.time() - start_time < timeout:
        result = _require_wait_condition_result(condition_fn())
        last_result = result

        if result.done:
            if result.public_detail:
                logger.info("%s complete: %s", description, format_public_detail(result.public_detail))
            else:
                logger.info("%s complete", description)
            return True

        elapsed_seconds = time.time() - start_time
        elapsed = int(elapsed_seconds)
        if result.public_detail:
            logger.debug(
                "%s in progress: %s (elapsed: %ss)",
                description,
                format_public_detail(result.public_detail),
                elapsed,
            )
        else:
            logger.debug("%s in progress (elapsed: %ss)", description, elapsed)

        remaining_timeout = max(0.0, timeout - elapsed_seconds)
        sleep_interval: float = interval
        if fast_interval and fast_timeout > 0 and elapsed < fast_timeout:
            sleep_interval = fast_interval
        sleep_interval = min(sleep_interval, remaining_timeout)
        if sleep_interval > 0:
            time.sleep(sleep_interval)

    if allow_success_after_timeout:
        result = _require_wait_condition_result(condition_fn())
        last_result = result
        if result.done:
            if result.public_detail:
                logger.info("%s complete: %s", description, format_public_detail(result.public_detail))
            else:
                logger.info("%s complete", description)
            return True

    if last_result and last_result.public_detail:
        logger.warning(
            "%s not complete before timeout: %s",
            description,
            format_public_detail(last_result.public_detail),
        )
    else:
        logger.warning("%s not complete before timeout", description)
    return False


def wait_for_restore_deletion(
    client,
    restore_name: str,
    *,
    dry_run: bool,
    timeout: int = RESTORE_WAIT_TIMEOUT,
    where: str = "",
    logger: Optional[logging.Logger] = None,
) -> None:
    """Wait until an ACM Restore resource is fully deleted.

    where is a display suffix (e.g. " on primary") used in the dry-run log,
    wait description, and timeout error, matching the historical per-caller
    wording.
    """
    log = logger or logging.getLogger("acm_switchover")
    if dry_run:
        if where == " on primary":
            log.info("[DRY-RUN] Skipping wait for deletion of %s on primary", restore_name)
        elif where:
            log.info("[DRY-RUN] Skipping wait for deletion of %s%s", restore_name, where)
        else:
            log.info("[DRY-RUN] Skipping wait for deletion of %s", restore_name)
        return

    def _poll_restore_deletion() -> WaitConditionResult:
        restore = client.get_custom_resource(
            group=CLUSTER_BACKUP_API_GROUP,
            version=CLUSTER_BACKUP_API_VERSION,
            plural=RESTORE_PLURAL,
            name=restore_name,
            namespace=BACKUP_NAMESPACE,
        )
        if not restore:
            return WaitConditionResult.complete("deleted")
        status = restore.get("status") if isinstance(restore, dict) else None
        phase = status.get("phase", "unknown") if isinstance(status, dict) else "unknown"
        return WaitConditionResult.pending(f"still present (phase={phase})")

    completed = wait_for_condition(
        f"deletion of restore {restore_name}{where}",
        _poll_restore_deletion,
        timeout=timeout,
        interval=RESTORE_POLL_INTERVAL,
        fast_interval=RESTORE_FAST_POLL_INTERVAL,
        fast_timeout=RESTORE_FAST_POLL_TIMEOUT,
        logger=log,
    )
    if not completed:
        raise FatalError(f"Timeout waiting for restore {restore_name} to be deleted{where} after {timeout}s")
