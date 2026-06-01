"""Shared workflow orchestration helpers for CLI operation flows."""

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional, Tuple

from lib.constants import EXIT_FAILURE, STALE_STATE_THRESHOLD
from lib.utils import Phase, StateManager

PhaseHandler = Callable[
    [argparse.Namespace, StateManager, Any, Any, logging.Logger],
    bool,
]
PhaseFlowEntry = Tuple[PhaseHandler, Iterable[Phase], Phase]
FailPhaseHandler = Callable[[StateManager, str, logging.Logger], bool]
UnexpectedPhaseHandler = Callable[[StateManager, Phase, logging.Logger], bool]
FailureHook = Callable[
    [argparse.Namespace, StateManager, Any, Any, logging.Logger],
    None,
]


@dataclass(frozen=True)
class CompletedStateConfig:
    operation_label: str
    operation_noun: str


@dataclass(frozen=True)
class FailedStateConfig:
    resumable_phases: Tuple[Phase, ...]
    operation_noun: str


def log_completed_noop(
    state: StateManager,
    logger: logging.Logger,
    state_age: timedelta,
    operation_label: str,
) -> None:
    """Log an explicit no-op banner for reruns against a recent completed state."""

    age_minutes = int(state_age.total_seconds() // 60)
    logger.info("\n" + "=" * 60)
    logger.info("%s ALREADY COMPLETED", operation_label)
    logger.info("=" * 60)
    logger.info("Existing state file age: %s minutes", age_minutes)
    logger.info("No phases were executed on this run.")
    logger.info("State file: %s", state.state_file)


def handle_completed_state(
    args: argparse.Namespace,
    state: StateManager,
    logger: logging.Logger,
    config: CompletedStateConfig,
) -> bool:
    """Handle completed-state reruns.

    Returns True when the caller should stop successfully, or False when the
    operation should continue.
    """

    if state.get_current_phase() != Phase.COMPLETED:
        return False

    state_age = state.get_state_age()
    if state_age is None:
        state_age = timedelta(seconds=STALE_STATE_THRESHOLD + 1)

    if getattr(args, "validate_only", False):
        return False

    operation_noun = config.operation_noun
    if state_age.total_seconds() > STALE_STATE_THRESHOLD:
        logger.warning("")
        logger.warning("⚠️  DETECTED STALE COMPLETED STATE")
        logger.warning(
            "%s appears already completed, but state file is %s old.",
            operation_noun.title(),
            f"{int(state_age.total_seconds() // 60)} minutes",
        )
        logger.warning("")
        logger.warning("To start a fresh %s:", operation_noun)
        logger.warning("  1. Remove state file: rm %s", state.state_file)
        logger.warning("  2. Or use: --reset-state")
        logger.warning("  3. Or use: --force to override (use with caution)")
        logger.warning("")
        if not getattr(args, "force", False):
            logger.error("Use --force to proceed with stale state, or remove/reset state file to start fresh.")
            sys.exit(EXIT_FAILURE)
        logger.warning("--force used: Resetting state to start fresh %s", operation_noun)
        state.reset()
        return False

    log_completed_noop(state, logger, state_age, config.operation_label)
    return True


def handle_failed_state(
    args: argparse.Namespace,
    state: StateManager,
    logger: logging.Logger,
    config: FailedStateConfig,
) -> None:
    """Prepare a failed state for retry, or exit when the retry phase is unknown."""

    if state.get_current_phase() != Phase.FAILED or getattr(args, "validate_only", False):
        return

    last_error_phase = state.get_last_error_phase()
    errors = state.get_errors()
    last_error_msg = errors[-1].get("error", "Unknown error") if errors else "Unknown error"

    logger.info("")
    logger.info("⚠️  RESUMING FROM FAILED STATE")
    logger.info("Last error: %s", last_error_msg)

    if last_error_phase and last_error_phase in config.resumable_phases:
        logger.info("Failed at phase: %s", last_error_phase.value)
        logger.info("Will retry from this phase")
        state.record_retry_error_baseline(last_error_phase, len(errors))
        state.set_phase(last_error_phase)
        return

    logger.warning("Cannot determine which phase failed from error history")
    logger.warning("")
    logger.warning("Options:")
    logger.warning("  1. Remove state file: rm %s", state.state_file)
    logger.warning("  2. Or use: --reset-state to start fresh")
    logger.warning("  3. Or use: --force to reset and retry from beginning")
    logger.warning("")
    if not getattr(args, "force", False):
        logger.error("Use --force to reset state and retry, or remove state file to start fresh.")
        sys.exit(EXIT_FAILURE)
    logger.warning("--force used: Resetting state to start fresh %s", config.operation_noun)
    state.reset()


def run_validate_only_preflight(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[Any],
    secondary: Any,
    logger: logging.Logger,
    preflight_handler: PhaseHandler,
) -> bool:
    runtime_checkpoint = state.capture_runtime_checkpoint()
    try:
        return preflight_handler(args, state, primary, secondary, logger)
    finally:
        state.restore_runtime_checkpoint(runtime_checkpoint)


def run_phase_flow(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[Any],
    secondary: Any,
    logger: logging.Logger,
    phase_flow: Tuple[PhaseFlowEntry, ...],
    flow_name: str,
    fail_phase: FailPhaseHandler,
    fail_unexpected_phase_state: UnexpectedPhaseHandler,
    on_phase_failure: FailureHook,
) -> bool:
    current_phase = state.get_current_phase()
    runnable_phases = {phase for _, phases, _ in phase_flow for phase in phases}
    if current_phase not in runnable_phases:
        return fail_phase(
            state,
            f"State phase '{current_phase.value}' is not runnable in {flow_name} flow.",
            logger,
        )

    ran_phase = False
    for handler, allowed_states, expected_phase in phase_flow:
        if state.get_current_phase() in allowed_states:
            ran_phase = True
            result = handler(args, state, primary, secondary, logger)
            if not result:
                on_phase_failure(args, state, primary, secondary, logger)
                return False
            if state.get_current_phase() != expected_phase:
                fail_unexpected_phase_state(state, expected_phase, logger)
                on_phase_failure(args, state, primary, secondary, logger)
                return False

    if not ran_phase:
        return fail_phase(state, "No runnable phase matched current state.", logger)

    return True
