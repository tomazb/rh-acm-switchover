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
- Reverse switchover capability (swap contexts to return to original hub)
- Interactive decommission of old hub
- Robust input validation for security and reliability
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, cast

from lib import (
    KubeClient,
    Phase,
    StateManager,
    __version__,
    __version_date__,
    setup_logging,
)
from lib.constants import EXIT_FAILURE, EXIT_INTERRUPT, EXIT_SUCCESS
from lib.validation import InputValidator, ValidationError
from modules import (
    Decommission,
    Finalization,
    PostActivationVerification,
    PreflightValidator,
    PrimaryPreparation,
    SecondaryActivation,
)

STATE_DIR_ENV_VAR = "ACM_SWITCHOVER_STATE_DIR"

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
  %(prog)s --validate-only --primary-context primary-hub --secondary-context secondary-hub --method passive --old-hub-action secondary

  # Dry-run to see planned actions
  %(prog)s --dry-run --primary-context primary-hub --secondary-context secondary-hub --method passive --old-hub-action secondary

  # Execute switchover (Method 1 - passive sync, keep old hub as secondary)
  %(prog)s --primary-context primary-hub --secondary-context secondary-hub --method passive --old-hub-action secondary

  # Execute switchover (Method 2 - full restore, decommission old hub)
  %(prog)s --primary-context primary-hub --secondary-context secondary-hub --method full --old-hub-action decommission

  # Reverse switchover (return to original hub - swap contexts)
  %(prog)s --primary-context secondary-hub --secondary-context primary-hub --method passive --old-hub-action secondary

  # Decommission old hub
  %(prog)s --decommission --primary-context old-hub --method passive --old-hub-action none
        """,
    )

    # Context arguments
    parser.add_argument("--primary-context", required=True, help="Kubernetes context for primary hub")
    parser.add_argument(
        "--secondary-context",
        help="Kubernetes context for secondary hub (required for switchover)",
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
    mode_group.add_argument("--decommission", action="store_true", help="Decommission old hub (interactive)")
    mode_group.add_argument(
        "--setup",
        action="store_true",
        help="Deploy RBAC resources and generate kubeconfigs for switchover",
    )

    # Switchover options
    parser.add_argument(
        "--method",
        choices=["passive", "full"],
        required=True,
        help="Switchover method: passive (continuous sync) or full (one-time restore)",
    )

    # Optional behavior
    parser.add_argument(
        "--manage-auto-import-strategy",
        action="store_true",
        help=(
            "Temporarily set ImportAndSync on destination hub when needed (ACM 2.14+) and reset it post-switchover. "
            "Default is detect-only."
        ),
    )

    # State management
    parser.add_argument(
        "--state-file",
        default=None,
        help=(
            "Path to state file for idempotent execution "
            "(defaults to $ACM_SWITCHOVER_STATE_DIR/switchover-<primary>__<secondary>.json when set, otherwise .state/...)"
        ),
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset state file and start fresh (use with caution)",
    )

    # Old hub handling after switchover (required)
    parser.add_argument(
        "--old-hub-action",
        choices=["secondary", "decommission", "none"],
        required=True,
        help=(
            "Action for old primary hub after switchover (REQUIRED): "
            "'secondary' sets up passive sync for failback capability, "
            "'decommission' removes ACM components, "
            "'none' leaves it unchanged for manual handling"
        ),
    )

    # Setup mode options (only used with --setup)
    setup_group = parser.add_argument_group("Setup Options (used with --setup)")
    setup_group.add_argument(
        "--admin-kubeconfig",
        help="Path to kubeconfig with cluster-admin privileges (required for --setup)",
    )
    setup_group.add_argument(
        "--role",
        choices=["operator", "validator", "both"],
        default="operator",
        help="RBAC role to deploy: operator, validator, or both (default: operator)",
    )
    setup_group.add_argument(
        "--token-duration",
        default="48h",
        help="Token validity duration for generated kubeconfigs (default: 48h)",
    )
    setup_group.add_argument(
        "--output-dir",
        default="./kubeconfigs",
        help="Output directory for generated kubeconfigs (default: ./kubeconfigs)",
    )
    setup_group.add_argument(
        "--skip-kubeconfig-generation",
        action="store_true",
        help="Skip kubeconfig generation during setup (deploy RBAC only)",
    )

    # Optional features
    parser.add_argument(
        "--skip-observability-checks",
        action="store_true",
        help="Skip Observability-related steps even if detected",
    )
    parser.add_argument(
        "--skip-rbac-validation",
        action="store_true",
        help="Skip RBAC permission validation during pre-flight checks",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non-interactive mode for decommission (dangerous)",
    )

    # Logging
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="Log output format (text or json)",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Validate argument combinations and input values."""
    try:
        # Perform comprehensive input validation
        # Note: validate_all_cli_args already checks that secondary_context is
        # provided when not in decommission mode
        InputValidator.validate_all_cli_args(args)

        # Validate state dir env var only when it would be used
        if not getattr(args, "state_file", None):
            env_state_dir = os.environ.get(STATE_DIR_ENV_VAR)
            if env_state_dir and env_state_dir.strip():
                InputValidator.validate_safe_filesystem_path(env_state_dir.strip(), STATE_DIR_ENV_VAR)
        else:
            # Validate user-specified state file path to prevent unsafe locations
            InputValidator.validate_safe_filesystem_path(args.state_file, "--state-file")

    except ValidationError as e:
        logger.error("Validation error: %s", str(e))
        sys.exit(EXIT_FAILURE)
    except Exception as e:
        logger.error("Unexpected validation error: %s", str(e))
        sys.exit(EXIT_FAILURE)


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
    logger.info("\nSwitchover completed at: %s", datetime.now().astimezone().isoformat())
    logger.info("State file: %s", args.state_file)
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

    validator = PreflightValidator(primary, secondary, args.method, skip_rbac_validation=args.skip_rbac_validation)
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

    primary_obs_enabled = config["primary_observability_detected"] and not args.skip_observability_checks
    secondary_obs_enabled = config["secondary_observability_detected"] and not args.skip_observability_checks

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
    _secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 2: PRIMARY HUB PREPARATION", logger)
    state.set_phase(Phase.PRIMARY_PREP)

    prep = PrimaryPreparation(
        primary,
        state,
        state.get_config("primary_version", "unknown"),
        state.get_config("primary_has_observability", False),
        dry_run=args.dry_run,
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
    _primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 3: SECONDARY HUB ACTIVATION", logger)
    state.set_phase(Phase.ACTIVATION)

    activation = SecondaryActivation(
        secondary_client=secondary,
        state_manager=state,
        method=args.method,
        manage_auto_import_strategy=args.manage_auto_import_strategy,
    )

    if not activation.activate():
        logger.error("Secondary hub activation failed!")
        state.set_phase(Phase.FAILED)
        return False

    logger.info("\n✓ Secondary hub activation complete!")
    return True


def _run_phase_post_activation(
    args: argparse.Namespace,
    state: StateManager,
    _primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 4: POST-ACTIVATION VERIFICATION", logger)
    state.set_phase(Phase.POST_ACTIVATION)

    verification = PostActivationVerification(
        secondary,
        state,
        state.get_config("secondary_has_observability", False),
        dry_run=args.dry_run,
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
        secondary_client=secondary,
        state_manager=state,
        acm_version=state.get_config("secondary_version", "unknown"),
        primary_client=primary,
        primary_has_observability=state.get_config("primary_has_observability", False),
        dry_run=args.dry_run,
        old_hub_action=args.old_hub_action,
        manage_auto_import_strategy=args.manage_auto_import_strategy,
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


def run_decommission(
    args: argparse.Namespace,
    primary: KubeClient,
    state: StateManager,
    logger: logging.Logger,
):
    """Execute decommission of old hub."""
    # Detect observability directly from the cluster, not from state file
    # The state file path may differ when running decommission standalone
    from lib.constants import OBSERVABILITY_NAMESPACE

    has_observability = primary.namespace_exists(OBSERVABILITY_NAMESPACE)
    if has_observability:
        logger.info("Observability detected on hub (namespace %s exists)", OBSERVABILITY_NAMESPACE)

    decom = Decommission(
        primary,
        has_observability,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        logger.info("[DRY-RUN] Starting decommission workflow (no changes will be made)")
    else:
        logger.info("Starting decommission workflow")

    return decom.decommission(interactive=not args.non_interactive)


def run_setup(
    args: argparse.Namespace,
    logger: logging.Logger,
) -> bool:
    """Execute RBAC setup using the setup-rbac.sh script.

    This mode deploys RBAC resources and optionally generates kubeconfigs
    for the switchover tool using cluster-admin credentials.

    Returns:
        True if setup completed successfully, False otherwise.
    """
    import subprocess

    script_dir = os.path.dirname(os.path.abspath(__file__))
    setup_script = os.path.join(script_dir, "scripts", "setup-rbac.sh")

    if not os.path.isfile(setup_script):
        logger.error("Setup script not found: %s", setup_script)
        return False

    # Build command
    cmd = [
        setup_script,
        "--admin-kubeconfig", args.admin_kubeconfig,
        "--context", args.primary_context,
        "--role", args.role,
        "--token-duration", args.token_duration,
        "--output-dir", args.output_dir,
    ]

    if args.skip_kubeconfig_generation:
        cmd.append("--skip-kubeconfig")

    if args.dry_run:
        cmd.append("--dry-run")

    logger.info("Running RBAC setup...")
    logger.info("  Context: %s", args.primary_context)
    logger.info("  Role: %s", args.role)
    logger.info("  Token duration: %s", args.token_duration)
    logger.info("  Output directory: %s", args.output_dir)

    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        logger.error("Failed to execute setup script. Ensure bash is available.")
        return False
    except Exception as e:
        logger.error("Setup failed: %s", str(e))
        return False


def main():
    """Main entry point."""
    args = parse_args()

    # Set up logging early so validate_args can use logger
    logger = setup_logging(args.verbose, args.log_format)

    validate_args(args, logger)
    resolved_state_file = _resolve_state_file(
        args.state_file,
        args.primary_context,
        args.secondary_context,
    )
    args.state_file = resolved_state_file

    logger.info("ACM Hub Switchover Automation v%s (%s)", __version__, __version_date__)
    logger.info("Started at: %s", datetime.now(timezone.utc).isoformat())
    logger.info("Using state file: %s", resolved_state_file)

    state = StateManager(resolved_state_file)

    if args.reset_state:
        logger.warning("Resetting state file...")
        state.reset()
    state.ensure_contexts(args.primary_context, args.secondary_context)

    try:
        primary, secondary = _initialize_clients(args, logger)
    except (ValueError, RuntimeError, Exception) as exc:  # pragma: no cover - fatal init error
        logger.error("Failed to initialize Kubernetes clients: %s", exc)
        sys.exit(EXIT_FAILURE)

    try:
        success = _execute_operation(args, state, primary, secondary, logger)
    except KeyboardInterrupt:
        logger.warning("\n\nOperation interrupted by user")
        logger.info("State saved to: %s", args.state_file)
        logger.info("Re-run the same command to resume from last successful step")
        sys.exit(EXIT_INTERRUPT)
    except (RuntimeError, ValueError, Exception) as exc:
        logger.error("\n✗ Unexpected error: %s", exc, exc_info=args.verbose)
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

    logger.info("Connecting to primary hub: %s", args.primary_context)
    primary = KubeClient(args.primary_context, dry_run=args.dry_run)

    secondary = None
    if args.secondary_context:
        logger.info("Connecting to secondary hub: %s", args.secondary_context)
        secondary = KubeClient(args.secondary_context, dry_run=args.dry_run)

    return primary, secondary


def _sanitize_context_identifier(value: str) -> str:
    """Sanitize context string to be filesystem friendly."""
    return InputValidator.sanitize_context_identifier(value)


def _get_default_state_dir() -> str:
    env_state_dir = os.environ.get(STATE_DIR_ENV_VAR)
    if env_state_dir and env_state_dir.strip():
        return env_state_dir.strip()
    return ".state"


def _resolve_state_file(requested_path: Optional[str], primary_ctx: str, secondary_ctx: Optional[str]) -> str:
    """Derive the state file path based on contexts unless user provided one."""
    if requested_path:
        return requested_path

    secondary_label = secondary_ctx or "none"
    slug = f"{_sanitize_context_identifier(primary_ctx)}__{_sanitize_context_identifier(secondary_label)}"
    return os.path.join(_get_default_state_dir(), f"switchover-{slug}.json")


def _execute_operation(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    """Execute the operation requested by CLI flags."""

    if args.setup:
        return run_setup(args, logger)

    if args.decommission:
        return run_decommission(args, primary, state, logger)

    if secondary is None:
        raise ValueError("Secondary context is required for switchover")

    return run_switchover(args, state, primary, secondary, logger)


if __name__ == "__main__":
    main()
