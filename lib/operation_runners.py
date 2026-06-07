from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Tuple

from lib import Phase
from lib.constants import (
    DEFAULT_OLD_HUB_ACTION,
    DEFAULT_RESTORE_METHOD,
    DRY_RUN_RESTORE_ONLY_COMPLETION_MESSAGE,
    DRY_RUN_RESTORE_ONLY_NEXT_STEPS_MESSAGE,
    DRY_RUN_SWITCHOVER_COMPLETION_MESSAGE,
    DRY_RUN_SWITCHOVER_NEXT_STEPS_MESSAGE,
    OPERATION_LABEL_RESTORE,
    OPERATION_LABEL_SWITCHOVER,
    OPERATION_NOUN_RESTORE,
    OPERATION_NOUN_SWITCHOVER,
    PHASE_FLOW_NAME_RESTORE_ONLY,
    PHASE_FLOW_NAME_SWITCHOVER,
    RESTORE_ONLY_COMPLETED_AT_MESSAGE,
    RESTORE_ONLY_COMPLETED_SUCCESS_MESSAGE,
    RESTORE_ONLY_NEXT_STEP_MESSAGES,
    SWITCHOVER_COMPLETED_AT_MESSAGE,
    SWITCHOVER_COMPLETED_SUCCESS_MESSAGE,
    SWITCHOVER_NEXT_STEP_MESSAGES,
    WORKFLOW_BANNER,
    WORKFLOW_LEADING_BANNER,
    WORKFLOW_NEXT_STEPS_HEADER,
    WORKFLOW_STATE_FILE_MESSAGE,
)
from lib.workflow import (
    CompletedStateConfig,
    FailedStateConfig,
    FailPhaseHandler,
    FailureHook,
    PhaseFlowEntry,
    PhaseHandler,
    UnexpectedPhaseHandler,
    handle_completed_state,
    handle_failed_state,
    run_phase_flow,
    run_validate_only_preflight,
)

DecommissionRunner = Callable[[Any, Any, Any, Any], bool]
RestoreOnlyRunner = Callable[[Any, Any, Any, Any], bool]
SwitchoverRunner = Callable[[Any, Any, Any, Any, Any], bool]


@dataclass(frozen=True)
class OperationDispatchHooks:
    decommission_runner: DecommissionRunner
    restore_only_runner: RestoreOnlyRunner
    switchover_runner: SwitchoverRunner


@dataclass(frozen=True)
class SwitchoverRunnerHooks:
    preflight_handler: PhaseHandler
    primary_prep_handler: PhaseHandler
    activation_handler: PhaseHandler
    post_activation_handler: PhaseHandler
    finalization_handler: PhaseHandler
    fail_phase: FailPhaseHandler
    fail_unexpected_phase_state: UnexpectedPhaseHandler
    on_phase_failure: FailureHook


@dataclass(frozen=True)
class RestoreOnlyRunnerHooks:
    preflight_handler: PhaseHandler
    restore_only_pause_handler: PhaseHandler
    activation_handler: PhaseHandler
    post_activation_handler: PhaseHandler
    finalization_handler: PhaseHandler
    fail_phase: FailPhaseHandler
    fail_unexpected_phase_state: UnexpectedPhaseHandler
    on_phase_failure: FailureHook


def execute_operation(
    args: Any,
    state: Any,
    primary: Optional[Any],
    secondary: Optional[Any],
    logger: Any,
    *,
    hooks: OperationDispatchHooks,
) -> bool:
    if getattr(args, "decommission", False):
        return hooks.decommission_runner(args, primary, state, logger)

    if getattr(args, "restore_only", False):
        if secondary is None:
            raise ValueError("Secondary context is required for restore-only")
        return hooks.restore_only_runner(args, state, secondary, logger)

    if secondary is None:
        raise ValueError("Secondary context is required for switchover")

    return hooks.switchover_runner(args, state, primary, secondary, logger)


def run_switchover_impl(
    args: Any,
    state: Any,
    primary: Any,
    secondary: Any,
    logger: Any,
    *,
    hooks: SwitchoverRunnerHooks,
) -> bool:
    if secondary is None:
        raise ValueError("Secondary client is required for switchover")

    if handle_completed_state(
        args,
        state,
        logger,
        CompletedStateConfig(operation_label=OPERATION_LABEL_SWITCHOVER, operation_noun=OPERATION_NOUN_SWITCHOVER),
    ):
        return True
    handle_failed_state(
        args,
        state,
        logger,
        FailedStateConfig(
            resumable_phases=(
                Phase.PREFLIGHT,
                Phase.PRIMARY_PREP,
                Phase.SECONDARY_VERIFY,
                Phase.ACTIVATION,
                Phase.POST_ACTIVATION,
                Phase.FINALIZATION,
            ),
            operation_noun=OPERATION_NOUN_SWITCHOVER,
        ),
    )

    if getattr(args, "validate_only", False):
        return run_validate_only_preflight(args, state, primary, secondary, logger, hooks.preflight_handler)

    phase_flow: Tuple[PhaseFlowEntry, ...] = (
        (hooks.preflight_handler, (Phase.INIT, Phase.PREFLIGHT), Phase.PREFLIGHT),
        (
            hooks.primary_prep_handler,
            (Phase.PREFLIGHT, Phase.PRIMARY_PREP),
            Phase.PRIMARY_PREP,
        ),
        (
            hooks.activation_handler,
            (
                Phase.PREFLIGHT,
                Phase.PRIMARY_PREP,
                Phase.SECONDARY_VERIFY,
                Phase.ACTIVATION,
            ),
            Phase.ACTIVATION,
        ),
        (
            hooks.post_activation_handler,
            (Phase.ACTIVATION, Phase.POST_ACTIVATION),
            Phase.POST_ACTIVATION,
        ),
        (
            hooks.finalization_handler,
            (Phase.POST_ACTIVATION, Phase.FINALIZATION),
            Phase.FINALIZATION,
        ),
    )

    if not run_phase_flow(
        args,
        state,
        primary,
        secondary,
        logger,
        phase_flow,
        PHASE_FLOW_NAME_SWITCHOVER,
        hooks.fail_phase,
        hooks.fail_unexpected_phase_state,
        hooks.on_phase_failure,
    ):
        return False

    if getattr(args, "dry_run", False):
        logger.info(DRY_RUN_SWITCHOVER_COMPLETION_MESSAGE)
        logger.info(DRY_RUN_SWITCHOVER_NEXT_STEPS_MESSAGE)
        return True

    state.set_phase(Phase.COMPLETED)
    logger.info(WORKFLOW_LEADING_BANNER)
    logger.info(SWITCHOVER_COMPLETED_SUCCESS_MESSAGE)
    logger.info(WORKFLOW_BANNER)
    logger.info(SWITCHOVER_COMPLETED_AT_MESSAGE, datetime.now().astimezone().isoformat())
    logger.info(WORKFLOW_STATE_FILE_MESSAGE, getattr(args, "state_file", None))
    logger.info(WORKFLOW_NEXT_STEPS_HEADER)
    for message in SWITCHOVER_NEXT_STEP_MESSAGES:
        logger.info(message)
    return True


def run_restore_only_impl(
    args: Any,
    state: Any,
    secondary: Any,
    logger: Any,
    *,
    hooks: RestoreOnlyRunnerHooks,
) -> bool:
    if secondary is None:
        raise ValueError("Secondary client is required for restore-only")

    if not getattr(args, "method", None):
        args.method = DEFAULT_RESTORE_METHOD
    if not getattr(args, "old_hub_action", None):
        args.old_hub_action = DEFAULT_OLD_HUB_ACTION

    if handle_completed_state(
        args,
        state,
        logger,
        CompletedStateConfig(operation_label=OPERATION_LABEL_RESTORE, operation_noun=OPERATION_NOUN_RESTORE),
    ):
        return True
    handle_failed_state(
        args,
        state,
        logger,
        FailedStateConfig(
            resumable_phases=(
                Phase.PREFLIGHT,
                Phase.ACTIVATION,
                Phase.POST_ACTIVATION,
                Phase.FINALIZATION,
            ),
            operation_noun=OPERATION_NOUN_RESTORE,
        ),
    )

    if getattr(args, "validate_only", False):
        return run_validate_only_preflight(args, state, None, secondary, logger, hooks.preflight_handler)

    phase_flow: Tuple[PhaseFlowEntry, ...] = (
        (hooks.preflight_handler, (Phase.INIT, Phase.PREFLIGHT), Phase.PREFLIGHT),
        (hooks.restore_only_pause_handler, (Phase.PREFLIGHT,), Phase.PREFLIGHT),
        (hooks.activation_handler, (Phase.PREFLIGHT, Phase.ACTIVATION), Phase.ACTIVATION),
        (
            hooks.post_activation_handler,
            (Phase.ACTIVATION, Phase.POST_ACTIVATION),
            Phase.POST_ACTIVATION,
        ),
        (
            hooks.finalization_handler,
            (Phase.POST_ACTIVATION, Phase.FINALIZATION),
            Phase.FINALIZATION,
        ),
    )

    if not run_phase_flow(
        args,
        state,
        None,
        secondary,
        logger,
        phase_flow,
        PHASE_FLOW_NAME_RESTORE_ONLY,
        hooks.fail_phase,
        hooks.fail_unexpected_phase_state,
        hooks.on_phase_failure,
    ):
        return False

    if getattr(args, "dry_run", False):
        logger.info(DRY_RUN_RESTORE_ONLY_COMPLETION_MESSAGE)
        logger.info(DRY_RUN_RESTORE_ONLY_NEXT_STEPS_MESSAGE)
        return True

    state.set_phase(Phase.COMPLETED)
    logger.info(WORKFLOW_LEADING_BANNER)
    logger.info(RESTORE_ONLY_COMPLETED_SUCCESS_MESSAGE)
    logger.info(WORKFLOW_BANNER)
    logger.info(RESTORE_ONLY_COMPLETED_AT_MESSAGE, datetime.now().astimezone().isoformat())
    logger.info(WORKFLOW_STATE_FILE_MESSAGE, getattr(args, "state_file", None))
    logger.info(WORKFLOW_NEXT_STEPS_HEADER)
    for message in RESTORE_ONLY_NEXT_STEP_MESSAGES:
        logger.info(message)
    return True
