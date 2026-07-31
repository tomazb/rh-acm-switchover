"""Shared workflow orchestration helpers for CLI operation flows."""

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Collection, Optional, Tuple

from lib.constants import (
    RESUME_START_PHASE_KEY,
    STALE_STATE_THRESHOLD,
    STATE_KEY_RESUME_SUMMARY,
    WORKFLOW_ALREADY_COMPLETED_MESSAGE,
    WORKFLOW_BANNER,
    WORKFLOW_BLANK_LINE,
    WORKFLOW_CANNOT_DETERMINE_FAILED_PHASE_MESSAGE,
    WORKFLOW_FAILED_AT_PHASE_MESSAGE,
    WORKFLOW_FAILED_STATE_FORCE_REQUIRED_MESSAGE,
    WORKFLOW_FORCE_RESET_FRESH_MESSAGE,
    WORKFLOW_FORCE_RESET_RETRY_OPTION,
    WORKFLOW_FORCE_STALE_STATE_OPTION,
    WORKFLOW_LAST_ERROR_MESSAGE,
    WORKFLOW_LEADING_BANNER,
    WORKFLOW_NEXT_STEPS_HEADER,
    WORKFLOW_NO_PHASES_EXECUTED_MESSAGE,
    WORKFLOW_NO_RUNNABLE_PHASE_MATCHED_MESSAGE,
    WORKFLOW_NON_RUNNABLE_PHASE_MESSAGE,
    WORKFLOW_OPTIONS_MESSAGE,
    WORKFLOW_REMOVE_STATE_FILE_OPTION,
    WORKFLOW_RESET_STATE_FRESH_OPTION,
    WORKFLOW_RESET_STATE_OPTION,
    WORKFLOW_RESUMING_FAILED_STATE_MESSAGE,
    WORKFLOW_RETRY_FROM_PHASE_MESSAGE,
    WORKFLOW_STALE_COMPLETED_DETAIL_MESSAGE,
    WORKFLOW_STALE_COMPLETED_STATE_MESSAGE,
    WORKFLOW_STALE_STATE_FORCE_REQUIRED_MESSAGE,
    WORKFLOW_START_FRESH_MESSAGE,
    WORKFLOW_STATE_AGE_MESSAGE,
    WORKFLOW_STATE_FILE_MESSAGE,
)
from lib.exceptions import SwitchoverError
from lib.utils import CANONICAL_PHASE_NAMES, Phase, StateManager

PhaseHandler = Callable[
    [argparse.Namespace, StateManager, Any, Any, logging.Logger],
    bool,
]
PhaseFlowEntry = Tuple[PhaseHandler, Collection[Phase], Phase]
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


@dataclass(frozen=True)
class CompletionLogConfig:
    success_message: str
    completed_at_message: str
    next_step_messages: Tuple[str, ...]


def log_completed_noop(
    state: StateManager,
    logger: logging.Logger,
    state_age: timedelta,
    operation_label: str,
) -> None:
    """Log an explicit no-op banner for reruns against a recent completed state."""

    age_minutes = int(state_age.total_seconds() // 60)
    logger.info(WORKFLOW_LEADING_BANNER)
    logger.info(WORKFLOW_ALREADY_COMPLETED_MESSAGE, operation_label)
    logger.info(WORKFLOW_BANNER)
    logger.info(WORKFLOW_STATE_AGE_MESSAGE, age_minutes)
    logger.info(WORKFLOW_NO_PHASES_EXECUTED_MESSAGE)
    logger.info(WORKFLOW_STATE_FILE_MESSAGE, state.state_file)


def log_operation_completion(
    args: argparse.Namespace,
    state: StateManager,
    logger: logging.Logger,
    config: CompletionLogConfig,
) -> None:
    """Mark an operation complete and log the shared completion banner."""
    state.set_phase(Phase.COMPLETED)
    logger.info(WORKFLOW_LEADING_BANNER)
    logger.info(config.success_message)
    logger.info(WORKFLOW_BANNER)
    logger.info(config.completed_at_message, datetime.now().astimezone().isoformat())
    logger.info(WORKFLOW_STATE_FILE_MESSAGE, state.state_file)
    logger.info(WORKFLOW_NEXT_STEPS_HEADER)
    for message in config.next_step_messages:
        logger.info(message)


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
        logger.warning(WORKFLOW_BLANK_LINE)
        logger.warning(WORKFLOW_STALE_COMPLETED_STATE_MESSAGE)
        logger.warning(
            WORKFLOW_STALE_COMPLETED_DETAIL_MESSAGE,
            operation_noun.title(),
            f"{int(state_age.total_seconds() // 60)} minutes",
        )
        logger.warning(WORKFLOW_BLANK_LINE)
        logger.warning(WORKFLOW_START_FRESH_MESSAGE, operation_noun)
        logger.warning(WORKFLOW_REMOVE_STATE_FILE_OPTION, state.state_file)
        logger.warning(WORKFLOW_RESET_STATE_OPTION)
        logger.warning(WORKFLOW_FORCE_STALE_STATE_OPTION)
        logger.warning(WORKFLOW_BLANK_LINE)
        if not getattr(args, "force", False):
            logger.error(WORKFLOW_STALE_STATE_FORCE_REQUIRED_MESSAGE)
            raise SwitchoverError(WORKFLOW_STALE_STATE_FORCE_REQUIRED_MESSAGE)
        logger.warning(WORKFLOW_FORCE_RESET_FRESH_MESSAGE, operation_noun)
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

    logger.info(WORKFLOW_BLANK_LINE)
    logger.info(WORKFLOW_RESUMING_FAILED_STATE_MESSAGE)
    logger.info(WORKFLOW_LAST_ERROR_MESSAGE, last_error_msg)

    if last_error_phase and last_error_phase in config.resumable_phases:
        logger.info(WORKFLOW_FAILED_AT_PHASE_MESSAGE, last_error_phase.value)
        logger.info(WORKFLOW_RETRY_FROM_PHASE_MESSAGE)
        state.record_retry_error_baseline(last_error_phase, len(errors))
        state.set_phase(last_error_phase)
        return

    logger.warning(WORKFLOW_CANNOT_DETERMINE_FAILED_PHASE_MESSAGE)
    logger.warning(WORKFLOW_BLANK_LINE)
    logger.warning(WORKFLOW_OPTIONS_MESSAGE)
    logger.warning(WORKFLOW_REMOVE_STATE_FILE_OPTION, state.state_file)
    logger.warning(WORKFLOW_RESET_STATE_FRESH_OPTION)
    logger.warning(WORKFLOW_FORCE_RESET_RETRY_OPTION)
    logger.warning(WORKFLOW_BLANK_LINE)
    if not getattr(args, "force", False):
        logger.error(WORKFLOW_FAILED_STATE_FORCE_REQUIRED_MESSAGE)
        raise SwitchoverError(WORKFLOW_FAILED_STATE_FORCE_REQUIRED_MESSAGE)
    logger.warning(WORKFLOW_FORCE_RESET_FRESH_MESSAGE, config.operation_noun)
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
            WORKFLOW_NON_RUNNABLE_PHASE_MESSAGE % (current_phase.value, flow_name),
            logger,
        )

    resume_start_phase = CANONICAL_PHASE_NAMES.get(current_phase)
    if current_phase != Phase.INIT and resume_start_phase is not None:
        state.set_config(
            STATE_KEY_RESUME_SUMMARY,
            {
                RESUME_START_PHASE_KEY: resume_start_phase,
            },
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
        return fail_phase(state, WORKFLOW_NO_RUNNABLE_PHASE_MATCHED_MESSAGE, logger)

    return True
