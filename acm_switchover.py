#!/usr/bin/env python3
"""
ACM Hub Switchover Automation Script

Automates the switchover process from a primary Red Hat Advanced Cluster
Management (ACM) hub to a secondary hub cluster.

Features:
- Idempotent execution with state management
- Comprehensive pre-flight validation
- Auto-detection of ACM version and optional components
- Dry-run and validate-only modes
- Support for both passive sync and full restore methods
- Rollback capability
- Interactive decommission of old hub
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, cast

from lib import (
    KubeClient,
    Phase,
    StateManager,
    confirm_action,
    setup_logging,
)
from lib.constants import EXIT_FAILURE, EXIT_INTERRUPT, EXIT_SUCCESS
from modules import (
    PreflightValidator,
    PrimaryPreparation,
    SecondaryActivation,
    PostActivationVerification,
    Finalization,
    Rollback,
    Decommission,
)

PhaseHandler = Callable[
    [argparse.Namespace, StateManager, KubeClient, KubeClient, logging.Logger],
    bool,
]


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="ACM Hub Switchover Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate only (no changes)
  %(prog)s --validate-only --primary-context primary-hub --secondary-context secondary-hub
  
  # Dry-run to see planned actions
  %(prog)s --dry-run --primary-context primary-hub --secondary-context secondary-hub
  
  # Execute switchover (Method 1 - passive sync)
  %(prog)s --primary-context primary-hub --secondary-context secondary-hub --method passive
  
  # Execute switchover (Method 2 - full restore)
  %(prog)s --primary-context primary-hub --secondary-context secondary-hub --method full
  
  # Rollback to primary hub
  %(prog)s --rollback --primary-context primary-hub --secondary-context secondary-hub
  
  # Decommission old hub
  %(prog)s --decommission --primary-context old-hub
        """,
    )

    # Context arguments
    parser.add_argument(
        "--primary-context", required=True, help="Kubernetes context for primary hub"
    )
    parser.add_argument(
        "--secondary-context",
        help="Kubernetes context for secondary hub (required for switchover/rollback)",
    )

    # Operation mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--validate-only",
        action="store_true",
        help="Run validation checks only, make no changes",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without executing them",
    )
    mode_group.add_argument(
        "--rollback", action="store_true", help="Rollback to primary hub"
    )
    mode_group.add_argument(
        "--decommission", action="store_true", help="Decommission old hub (interactive)"
    )

    # Switchover options
    parser.add_argument(
        "--method",
        choices=["passive", "full"],
        default="passive",
        help="Switchover method: passive (continuous sync) or full (one-time restore)",
    )

    # State management
    parser.add_argument(
        "--state-file",
        default=None,
        help=(
            "Path to state file for idempotent execution "
            "(defaults to .state/switchover-<primary>__<secondary>.json)"
        ),
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset state file and start fresh (use with caution)",
    )

    # Optional features
    parser.add_argument(
        "--skip-observability-checks",
        action="store_true",
        help="Skip Observability-related steps even if detected",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non-interactive mode for decommission (dangerous)",
    )

    # Logging
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="Log output format (text or json)",
    )

    return parser.parse_args()


def validate_args(args):
    """Validate argument combinations."""
    if not args.decommission and not args.secondary_context:
        print(
            "Error: --secondary-context is required for switchover/rollback operations"
        )
        sys.exit(1)


def run_switchover(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
):
    """Execute the main switchover workflow."""

    if secondary is None:
        raise ValueError("Secondary client is required for switchover")

    phase_flow: Tuple[Tuple[PhaseHandler, Iterable[Phase]], ...] = (
        (_run_phase_preflight, (Phase.INIT, Phase.PREFLIGHT)),
        (_run_phase_primary_prep, (Phase.PREFLIGHT, Phase.PRIMARY_PREP)),
        (
            _run_phase_activation,
            (Phase.PREFLIGHT, Phase.PRIMARY_PREP, Phase.ACTIVATION),
        ),
        (_run_phase_post_activation, (Phase.ACTIVATION, Phase.POST_ACTIVATION)),
        (_run_phase_finalization, (Phase.POST_ACTIVATION, Phase.FINALIZATION)),
    )

    for handler, allowed_phases in phase_flow:
        if state.get_current_phase() in allowed_phases:
            result = handler(args, state, primary, secondary, logger)
            if args.validate_only:
                return result
            if not result:
                return False

    state.set_phase(Phase.COMPLETED)

    logger.info("\n" + "=" * 60)
    logger.info("SWITCHOVER COMPLETED SUCCESSFULLY!")
    logger.info("=" * 60)
    logger.info(f"\nSwitchover completed at: {datetime.utcnow().isoformat()}")
    logger.info(f"State file: {args.state_file}")
    logger.info("\nNext steps:")
    logger.info("  1. Inform stakeholders that switchover is complete")
    logger.info("  2. Provide new hub connection details")
    logger.info("  3. Verify applications are functioning correctly")
    logger.info("  4. Optionally decommission old hub with: --decommission")

    return True


def _run_phase_preflight(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 1: PRE-FLIGHT VALIDATION", logger)

    state.set_phase(Phase.PREFLIGHT)

    validator = PreflightValidator(primary, secondary, args.method)
    passed, config = cast(
        Tuple[bool, Dict[str, Any]],
        validator.validate_all(),
    )

    if not passed:
        logger.error("Pre-flight validation failed! Cannot proceed.")
        state.set_phase(Phase.FAILED)
        return False

    state.set_config("primary_version", config["primary_version"])
    state.set_config("secondary_version", config["secondary_version"])
    state.set_config(
        "primary_observability_detected",
        config["primary_observability_detected"],
    )
    state.set_config(
        "secondary_observability_detected",
        config["secondary_observability_detected"],
    )

    primary_obs_enabled = (
        config["primary_observability_detected"] and not args.skip_observability_checks
    )
    secondary_obs_enabled = (
        config["secondary_observability_detected"] and not args.skip_observability_checks
    )

    state.set_config("primary_has_observability", primary_obs_enabled)
    state.set_config("secondary_has_observability", secondary_obs_enabled)
    state.set_config("has_observability", primary_obs_enabled or secondary_obs_enabled)

    if args.validate_only:
        logger.info("\n✓ Validation complete. Exiting (--validate-only mode)")
        return True

    logger.info("\n✓ Pre-flight validation passed!")
    return True


def _run_phase_primary_prep(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 2: PRIMARY HUB PREPARATION", logger)
    state.set_phase(Phase.PRIMARY_PREP)

    prep = PrimaryPreparation(
        primary,
        state,
        state.get_config("primary_version", "unknown"),
        state.get_config("primary_has_observability", False),
    )

    if not prep.prepare():
        logger.error("Primary hub preparation failed!")
        state.set_phase(Phase.FAILED)
        return False

    logger.info("\n✓ Primary hub preparation complete!")
    return True


def _run_phase_activation(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 3: SECONDARY HUB ACTIVATION", logger)
    state.set_phase(Phase.ACTIVATION)

    activation = SecondaryActivation(secondary, state, args.method)

    if not activation.activate():
        logger.error("Secondary hub activation failed!")
        state.set_phase(Phase.FAILED)
        return False

    logger.info("\n✓ Secondary hub activation complete!")
    return True


def _run_phase_post_activation(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 4: POST-ACTIVATION VERIFICATION", logger)
    state.set_phase(Phase.POST_ACTIVATION)

    verification = PostActivationVerification(
        secondary,
        state,
        state.get_config("secondary_has_observability", False),
    )

    if not verification.verify():
        logger.error("Post-activation verification failed!")
        state.set_phase(Phase.FAILED)
        return False

    logger.info("\n✓ Post-activation verification complete!")
    return True


def _run_phase_finalization(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 5: FINALIZATION", logger)
    state.set_phase(Phase.FINALIZATION)

    finalization = Finalization(
        secondary,
        state,
        state.get_config("secondary_version", "unknown"),
        primary_client=primary,
        primary_has_observability=state.get_config("primary_has_observability", False),
    )

    if not finalization.finalize():
        logger.error("Finalization failed!")
        state.set_phase(Phase.FAILED)
        return False

    logger.info("\n✓ Finalization complete!")
    return True


def _log_phase_banner(title: str, logger: logging.Logger) -> None:
    """Log a standardized banner around key phases."""
    logger.info("\n" + "=" * 60)
    logger.info(title)
    logger.info("=" * 60)


def run_rollback(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
):
    """Execute rollback to primary hub."""
    logger.warning("\n" + "=" * 60)
    logger.warning("ROLLBACK MODE")
    logger.warning("=" * 60)

    if not confirm_action(
        "\nAre you sure you want to rollback to the primary hub?", default=False
    ):
        logger.info("Rollback cancelled by user")
        return False

    state.set_phase(Phase.ROLLBACK)

    rollback = Rollback(
        primary,
        secondary,
        state,
        state.get_config("primary_version", "unknown"),
        state.get_config("primary_has_observability", False),
    )

    if rollback.rollback():
        logger.info("\n✓ Rollback completed successfully!")
        logger.info(
            "Allow 5-10 minutes for ManagedClusters to reconnect to primary hub"
        )
        state.reset()
        return True
    else:
        logger.error("Rollback failed!")
        return False


def run_decommission(
    args: argparse.Namespace,
    primary: KubeClient,
    state: StateManager,
    logger: logging.Logger,
):
    """Execute decommission of old hub."""
    decom = Decommission(
        primary, state.get_config("primary_has_observability", False)
    )

    logger.info("Starting decommission workflow")

    return decom.decommission(interactive=not args.non_interactive)


def main():
    """Main entry point."""
    args = parse_args()
    validate_args(args)
    resolved_state_file = _resolve_state_file(
        args.state_file or DEFAULT_STATE_FILE,
        args.primary_context,
        args.secondary_context,
    )
    args.state_file = resolved_state_file

    logger = setup_logging(args.verbose, args.log_format)
    logger.info("ACM Hub Switchover Automation")
    logger.info(f"Started at: {datetime.utcnow().isoformat()}")
    logger.info(f"Using state file: {resolved_state_file}")

    state = StateManager(resolved_state_file)

    if args.reset_state:
        logger.warning("Resetting state file...")
        state.reset()
    state.ensure_contexts(args.primary_context, args.secondary_context)

    try:
        primary, secondary = _initialize_clients(args, logger)
    except Exception as exc:  # pragma: no cover - fatal init error
        logger.error(f"Failed to initialize Kubernetes clients: {exc}")
        sys.exit(EXIT_FAILURE)

    try:
        success = _execute_operation(args, state, primary, secondary, logger)
    except KeyboardInterrupt:
        logger.warning("\n\nOperation interrupted by user")
        logger.info(f"State saved to: {args.state_file}")
        logger.info("Re-run the same command to resume from last successful step")
        sys.exit(EXIT_INTERRUPT)
    except Exception as exc:
        logger.error(f"\n✗ Unexpected error: {exc}", exc_info=args.verbose)
        state.add_error(str(exc))
        sys.exit(EXIT_FAILURE)

    if success:
        logger.info("\n✓ Operation completed successfully!")
        sys.exit(EXIT_SUCCESS)

    logger.error("\n✗ Operation failed!")
    sys.exit(EXIT_FAILURE)


def _initialize_clients(
    args: argparse.Namespace,
    logger: logging.Logger,
) -> Tuple[KubeClient, Optional[KubeClient]]:
    """Create Kubernetes clients for provided contexts."""

    logger.info(f"Connecting to primary hub: {args.primary_context}")
    primary = KubeClient(args.primary_context, dry_run=args.dry_run)

    secondary = None
    if args.secondary_context:
        logger.info(f"Connecting to secondary hub: {args.secondary_context}")
        secondary = KubeClient(args.secondary_context, dry_run=args.dry_run)

    return primary, secondary


def _execute_operation(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    """Execute the operation requested by CLI flags."""

    if args.decommission:
        return run_decommission(args, primary, state, logger)

    if args.rollback:
        if secondary is None:
            raise ValueError("Secondary context is required for rollback")
        return run_rollback(args, state, primary, secondary, logger)

    if secondary is None:
        raise ValueError("Secondary context is required for switchover")

    return run_switchover(args, state, primary, secondary, logger)


if __name__ == "__main__":
    main()
DEFAULT_STATE_FILE = ".state/switchover-state.json"


def _sanitize_context_identifier(value: str) -> str:
    """Sanitize context string to be filesystem friendly."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def _resolve_state_file(
    requested_path: Optional[str], primary_ctx: str, secondary_ctx: Optional[str]
) -> str:
    """Derive the state file path based on contexts unless user provided one."""
    if requested_path and requested_path != DEFAULT_STATE_FILE:
        return requested_path

    secondary_label = secondary_ctx or "none"
    slug = f"{_sanitize_context_identifier(primary_ctx)}__{_sanitize_context_identifier(secondary_label)}"
    return os.path.join(".state", f"switchover-{slug}.json")
