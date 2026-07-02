from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from lib.constants import (
    REPORT_FILENAME_DECOMMISSION,
    REPORT_FILENAME_PREFLIGHT,
    REPORT_FILENAME_RESTORE_ONLY,
    REPORT_FILENAME_SWITCHOVER,
    REPORT_TYPE_DECOMMISSION,
    REPORT_TYPE_PREFLIGHT,
    REPORT_TYPE_RESTORE,
    REPORT_TYPE_SWITCHOVER,
)
from lib.exceptions import SwitchoverError
from lib.report_artifacts import SOURCE as PYTHON_REPORT_SOURCE
from lib.report_artifacts import build_operation_report, write_json_report_artifact
from lib.utils import CANONICAL_PHASE_NAMES, Phase, StateIdentityMismatch, StateManager

# Fallback for state entries recorded before completion-time phases existed.
# Matched by prefix so e.g. verify_backup_schedule_enabled still resolves.
_PHASE_BY_STEP_PREFIX = {
    "preflight": "preflight",
    "pause_argocd": "primary_prep",
    "pause_backup": "primary_prep",
    "disable_auto_import": "primary_prep",
    "scale_down": "primary_prep",
    "verify_passive_sync": "activation",
    "activate_managed_clusters": "activation",
    "create_full_restore": "activation",
    "wait_restore_completion": "activation",
    "apply_immediate_import": "activation",
    "verify_clusters_connected": "post_activation",
    "verify_klusterlet": "post_activation",
    "verify_auto_import_cleanup": "post_activation",
    "scale_up_observability": "post_activation",
    "restart_observatorium_api": "post_activation",
    "verify_observability_pods": "post_activation",
    "verify_metrics_collection": "post_activation",
    "enable_backup_schedule": "finalization",
    "verify_backup_schedule": "finalization",
    "fix_backup_collision": "finalization",
    "verify_new_backups": "finalization",
    "verify_backup_integrity": "finalization",
    "verify_mch_health": "finalization",
    "handle_old_hub": "finalization",
    "verify_old_hub_state": "finalization",
    "disable_observability_on_secondary": "finalization",
    "reset_auto_import_strategy": "finalization",
}

_REPORT_PHASE_VALUES = frozenset(CANONICAL_PHASE_NAMES.values())


def fallback_phase_for_step(step_name: str) -> Optional[str]:
    """Resolve a step name to its report phase via the static prefix map."""
    return next((value for prefix, value in _PHASE_BY_STEP_PREFIX.items() if step_name.startswith(prefix)), None)


def report_target(args: Any) -> tuple[str, str]:
    """Return report type and filename for the current Python CLI operation."""
    if getattr(args, "validate_only", False):
        return REPORT_TYPE_PREFLIGHT, REPORT_FILENAME_PREFLIGHT
    if getattr(args, "decommission", False):
        return REPORT_TYPE_DECOMMISSION, REPORT_FILENAME_DECOMMISSION
    if getattr(args, "restore_only", False):
        return REPORT_TYPE_RESTORE, REPORT_FILENAME_RESTORE_ONLY
    return REPORT_TYPE_SWITCHOVER, REPORT_FILENAME_SWITCHOVER


def phase_report_from_state(state_snapshot: dict) -> dict[str, dict[str, Any]]:
    """Build a compact phase map from durable state."""
    phases: dict[str, dict[str, Any]] = {}

    if not isinstance(state_snapshot, dict):
        return phases

    completed_steps = state_snapshot.get("completed_steps", [])
    if not isinstance(completed_steps, list):
        completed_steps = []

    for step in completed_steps:
        if not isinstance(step, dict):
            continue
        name = step.get("name", "")
        recorded_phase = step.get("phase")
        phase = recorded_phase if recorded_phase in _REPORT_PHASE_VALUES else fallback_phase_for_step(name)
        if not phase:
            continue
        phases.setdefault(phase, {"phase": phase, "status": "pass", "steps": []})["steps"].append(name)

    if state_snapshot.get("current_phase") == Phase.FAILED.value:
        errors = state_snapshot.get("errors", [])
        if not isinstance(errors, list):
            errors = []

        last_error = errors[-1] if errors else None
        failed_phase_value = last_error.get("phase") if isinstance(last_error, dict) else None
        failed_phase = {phase.value: name for phase, name in CANONICAL_PHASE_NAMES.items()}.get(failed_phase_value)
        if failed_phase:
            phases.setdefault(failed_phase, {"phase": failed_phase, "steps": []})["status"] = "fail"

    return phases


def write_python_report(args: Any, state: Optional[StateManager], status: str, logger: logging.Logger) -> None:
    """Write a Python CLI report artifact when --report-dir is set."""
    report_dir = getattr(args, "report_dir", None)
    if not report_dir or state is None:
        return

    try:
        report_type, filename = report_target(args)
        state_snapshot = state.capture_state_snapshot()
        report = build_operation_report(
            report_type=report_type,
            status=status,
            source=PYTHON_REPORT_SOURCE,
            args=args,
            state_snapshot=state_snapshot,
            phases=phase_report_from_state(state_snapshot),
        )
        destination = os.path.join(report_dir, filename)
        written_path = write_json_report_artifact(report, destination)
        logger.info("Wrote report artifact: %s", written_path)
    except Exception as exc:
        logger.error("Failed to write report artifact: %s", exc)


@dataclass(frozen=True)
class CliOperationHooks:
    bind_runtime_hub_identities: Callable[..., None]
    run_argocd_resume_only: Callable[..., bool]
    execute_operation: Callable[..., bool]
    write_python_report: Callable[..., None]
    gitops_reporter_factory: Callable[[], Any]


def run_setup_mode(
    args: Any,
    logger: logging.Logger,
    *,
    run_setup: Callable[[Any, logging.Logger], bool],
    exit_success: int,
    exit_failure: int,
    exit_interrupt: int,
) -> int:
    """Run setup mode and return the final process exit code."""
    try:
        success = run_setup(args, logger)
    except KeyboardInterrupt:
        logger.warning("\n\nSetup interrupted by user")
        return exit_interrupt
    except Exception as exc:
        logger.error("\n✗ Setup failed: %s", exc, exc_info=getattr(args, "verbose", False))
        return exit_failure

    if success:
        logger.info("\n✓ Setup completed successfully!")
        return exit_success

    logger.error("\n✗ Setup failed!")
    return exit_failure


def run_operation_mode(
    args: Any,
    state: StateManager,
    primary: Any,
    secondary: Any,
    logger: logging.Logger,
    *,
    should_bind_state: bool,
    should_record_state_errors: bool,
    hooks: CliOperationHooks,
    exit_success: int,
    exit_failure: int,
    exit_interrupt: int,
) -> int:
    """Run the prepared non-setup operation path and return the final exit code."""
    exit_code = exit_failure
    try:
        if should_bind_state:
            hooks.bind_runtime_hub_identities(args, state, primary, secondary)
        if getattr(args, "argocd_resume_only", False):
            success = hooks.run_argocd_resume_only(args, state, primary, secondary, logger)
        else:
            success = hooks.execute_operation(args, state, primary, secondary, logger)
    except KeyboardInterrupt:
        logger.warning("\n\nOperation interrupted by user")
        logger.info("State saved to: %s", getattr(args, "state_file", None))
        logger.info("Re-run the same command to resume from last successful step")
        exit_code = exit_interrupt
    except StateIdentityMismatch as exc:
        # The binding guard refused this state file; never write into it.
        # The finally block still emits a report from a read-only snapshot,
        # which is deliberate: it aids diagnosis without mutating the file.
        logger.error("\n✗ %s", exc)
        exit_code = exit_failure
    except SwitchoverError as exc:
        logger.error("\n✗ %s", exc)
        if should_record_state_errors:
            state.add_error(str(exc))
        exit_code = exit_failure
    except Exception as exc:
        logger.error("\n✗ Unexpected error: %s", exc, exc_info=getattr(args, "verbose", False))
        if should_record_state_errors:
            state.add_error(str(exc))
        exit_code = exit_failure
    else:
        if success:
            if getattr(args, "argocd_resume_only", False):
                logger.info("\n✓ Argo CD resume completed successfully!")
            else:
                logger.info("\n✓ Operation completed successfully!")
            exit_code = exit_success
        else:
            if getattr(args, "argocd_resume_only", False):
                logger.error("\n✗ Argo CD resume failed or had nothing to restore.")
            else:
                logger.error("\n✗ Operation failed!")
            exit_code = exit_failure
    finally:
        hooks.write_python_report(args, state, "pass" if exit_code == exit_success else "fail", logger)
        hooks.gitops_reporter_factory().print_report()

    return exit_code
