"""Shared workflow orchestration helpers for CLI operation flows."""

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Collection, Optional, Tuple

from lib.constants import (
    STALE_STATE_THRESHOLD,
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
from lib.migration_evidence import MigrationEvidenceError
from lib.migration_post_activation import require_post_activation_completed
from lib.run_record import (
    MIGRATION_JOURNAL_IMPLICIT_RESET_REFUSAL,
    MigrationJournalResetRefused,
    RunRecord,
    StateStructureError,
    migration_journal_present,
)
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


def _refuse_reset_of_migration_journal(
    state: StateManager, logger: logging.Logger
) -> None:
    """--force never discards a migration journal; only --reset-state does (R4-04 amendment section 10)."""
    if migration_journal_present(state.capture_state_snapshot()):
        raise MigrationJournalResetRefused(MIGRATION_JOURNAL_IMPLICIT_RESET_REFUSAL)


def completed_backup_assessment(args: Any, state: StateManager) -> bool:
    """Identify a read-only completed attempt before context/identity preparation."""
    return (
        not getattr(args, "decommission", False)
        and not getattr(args, "argocd_resume_only", False)
        and state.get_current_phase() == Phase.COMPLETED
    )


def handle_completed_state(
    args, state, logger, config, *, primary, secondary, backup_continuity_guard
) -> bool:
    """Require current proof before the bounded no-op; never reset or replay history."""
    if not completed_backup_assessment(args, state):
        return False
    try:
        age = _completed_state_age(state)
        if age.total_seconds() > STALE_STATE_THRESHOLD:
            logger.error(
                "Completed checkpoint is stale; Backup assessment cannot authorize reset or replay."
            )
            return False
        if not backup_continuity_guard(
            args, state, primary, secondary, logger, completed=True
        ):
            return False
        log_completed_noop(state, logger, age, config.operation_label)
        return True
    except Exception:
        logger.error(
            "Completed Backup assessment is unavailable; preserving the original checkpoint."
        )
        return False


def handle_failed_state(
    args: argparse.Namespace,
    state: StateManager,
    logger: logging.Logger,
    config: FailedStateConfig,
) -> None:
    """Prepare a failed state for retry, or exit when the retry phase is unknown."""

    if state.get_current_phase() != Phase.FAILED or getattr(
        args, "validate_only", False
    ):
        return

    last_error_phase = _failed_state_retry_phase(state)
    errors = state.get_errors()
    last_error_msg = (
        errors[-1].get("error", "Unknown error") if errors else "Unknown error"
    )

    logger.info(WORKFLOW_BLANK_LINE)
    logger.info(WORKFLOW_RESUMING_FAILED_STATE_MESSAGE)
    logger.info(WORKFLOW_LAST_ERROR_MESSAGE, last_error_msg)

    if last_error_phase and last_error_phase in config.resumable_phases:
        logger.info(WORKFLOW_FAILED_AT_PHASE_MESSAGE, last_error_phase.value)
        logger.info(WORKFLOW_RETRY_FROM_PHASE_MESSAGE)
        state.record_retry_error_baseline(last_error_phase, len(errors))
        if getattr(args, "dry_run", False):
            # A dry-run retries in memory only: a refusal before phase work must leave the state file unwritten.
            state.preview_phase(last_error_phase)
        else:
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
    _refuse_reset_of_migration_journal(state, logger)
    logger.warning(WORKFLOW_FORCE_RESET_FRESH_MESSAGE, config.operation_noun)
    state.reset()


def _completed_state_age(state: StateManager) -> timedelta:
    """A completed state's age; an unknown age counts as stale."""
    state_age = state.get_state_age()
    return (
        state_age
        if state_age is not None
        else timedelta(seconds=STALE_STATE_THRESHOLD + 1)
    )


def _failed_state_retry_phase(state: StateManager) -> Optional[Phase]:
    """The phase a FAILED state retries: an Argo CD re-pause marker's phase, else the last error's.

    The marker records the failed operational phase durably (PR C decisions amendment
    section 2.2), so an error recorded later against another phase cannot move the retry.
    """
    marker_phase = RunRecord(state).argocd_repause_required()
    return (
        Phase(marker_phase)
        if marker_phase is not None
        else state.get_last_error_phase()
    )


def refuse_predictable_journal_reset(
    args: argparse.Namespace,
    state: StateManager,
    logger: logging.Logger,
    resumable_phases: Tuple[Phase, ...],
) -> None:
    """Raise the migration-journal reset refusal before any write when this run's --force would reset.

    Mirrors the implicit reset of handle_failed_state, so
    a caller that writes before them (live hub identity binding, #332) refuses first and
    the refused state stays unchanged.
    """
    if getattr(args, "validate_only", False) or not getattr(args, "force", False):
        return
    phase = state.get_current_phase()
    if phase == Phase.FAILED:
        would_reset = _failed_state_retry_phase(state) not in resumable_phases
    else:
        return
    if would_reset:
        _refuse_reset_of_migration_journal(state, logger)


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


# The codes whose evidence a rerun can establish from fresh reads against the kept journal.
_MISSING_EVIDENCE_CODES = ("restore_not_completed", "post_activation_not_completed")
_MISSING_EVIDENCE_GUIDANCE = (
    "The migration journal is unchanged. For missing post-activation completion, set `current_phase` to "
    "`post_activation_verification`. For missing Restore completion, set it to `activation` and remove only the "
    "`completed_steps` entry named `wait_restore_completion`, if present. Preserve the migration journal, mutation "
    "markers, and Argo CD re-pause marker. Re-run with the same arguments; the tool must establish the missing "
    "evidence from fresh reads. Do not synthesize completion timestamps or replace frozen Backup identities. "
    "Missing accepted-mutation evidence requires operator reconciliation. `--reset-state` deliberately discards "
    "the transaction."
)
_INVALID_JOURNAL_GUIDANCE = (
    "Do not synthesize completion timestamps or replace frozen Backup identities. "
    "`--reset-state` deliberately discards the transaction."
)


def _post_activation_marker_refusal(
    args: argparse.Namespace, state: StateManager, current_phase: Phase
) -> Optional[str]:
    """Refuse a resume past post-activation whose journal lacks the evidence that phase records.

    The collection refuses the same journal at its completed post_activation phase marker
    (R4-04 Task 9). A dry-run refuses only an invalid journal, as the collection's preview
    does, and predicts the missing evidence; a journal-free state is left to finalization's
    own evidence gate. The refusal keeps the journal and names the rerun that establishes
    the missing evidence, since a plain retry resumes here again.
    """
    if current_phase != Phase.FINALIZATION:
        return None
    try:
        journal = RunRecord(state).migration_backups()
        if journal is not None and not getattr(args, "dry_run", False):
            require_post_activation_completed(journal)
    except (MigrationEvidenceError, StateStructureError) as exc:
        if getattr(exc, "code", "") in _MISSING_EVIDENCE_CODES:
            return (
                f"Migration evidence refused ({exc}). A plain re-run resumes at finalization and refuses again. "
                f"{_MISSING_EVIDENCE_GUIDANCE} State file: {state.state_file}."
            )
        return (
            f"Migration evidence refused ({exc}). The recorded migration journal is invalid: repair it in the "
            f"state file {state.state_file}. {_INVALID_JOURNAL_GUIDANCE}"
        )
    return None


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
    *,
    backup_continuity_guard: Callable[..., bool],
    before_phases: Optional[PhaseHandler] = None,
) -> bool:
    current_phase = state.get_current_phase()
    runnable_phases = {phase for _, phases, _ in phase_flow for phase in phases}
    if current_phase not in runnable_phases:
        return fail_phase(
            state,
            WORKFLOW_NON_RUNNABLE_PHASE_MESSAGE % (current_phase.value, flow_name),
            logger,
        )

    # A dry-run records no resume summary: its refusals below must leave the state file unwritten.
    resume_start_phase = CANONICAL_PHASE_NAMES.get(current_phase)
    if (
        current_phase != Phase.INIT
        and resume_start_phase is not None
        and not getattr(args, "dry_run", False)
    ):
        RunRecord(state).record_resume_start_phase(resume_start_phase)

    # Obligations a failed run left for its retry run before any phase work. The Argo CD
    # re-pause precedes the journal refusal below, as in the collection, so Argo CD stays
    # paused while the operator follows the refusal guidance.
    if before_phases is not None and not before_phases(
        args, state, primary, secondary, logger
    ):
        return False

    refusal = _post_activation_marker_refusal(args, state, current_phase)
    if refusal is not None:
        if getattr(args, "dry_run", False):
            # A preview refusal records nothing: the failure hook would write the error and FAILED phase.
            logger.error(refusal)
            return False
        return fail_phase(state, refusal, logger)

    ran_phase = False
    for handler, allowed_states, expected_phase in phase_flow:
        if state.get_current_phase() in allowed_states:
            ran_phase = True
            if expected_phase == Phase.FINALIZATION and not backup_continuity_guard(
                args, state, primary, secondary, logger, completed=False
            ):
                fail_phase(
                    state,
                    "Backup continuity is unproved; preserve historical evidence for assessment.",
                    logger,
                )
                on_phase_failure(args, state, primary, secondary, logger)
                return False
            result = handler(args, state, primary, secondary, logger)
            if not result:
                # A dry-run refusal before phase work is not a phase failure: only a FAILED phase runs the hook.
                if (
                    not getattr(args, "dry_run", False)
                    or state.get_current_phase() == Phase.FAILED
                ):
                    on_phase_failure(args, state, primary, secondary, logger)
                return False
            if state.get_current_phase() != expected_phase:
                fail_unexpected_phase_state(state, expected_phase, logger)
                on_phase_failure(args, state, primary, secondary, logger)
                return False

    if not ran_phase:
        return fail_phase(state, WORKFLOW_NO_RUNNABLE_PHASE_MATCHED_MESSAGE, logger)

    return True
