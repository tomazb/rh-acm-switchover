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
from typing import Any, Optional, Tuple

from lib import (
    KubeClient,
    Phase,
    StateManager,
    __version__,
    __version_date__,
)
from lib import argocd as argocd_lib
from lib import (
    argocd_resume,
    cli_outcomes,
    operation_runners,
    runtime_bootstrap,
    setup_logging,
    validate_decommission_permissions,
)
from lib.argocd_register import ArgocdPauseRegister
from lib.constants import (
    EXIT_FAILURE,
    EXIT_INTERRUPT,
    EXIT_SUCCESS,
    HUB_ROLE_PRIMARY,
    HUB_ROLE_SECONDARY,
    MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT,
    MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED,
    MANAGED_CLUSTER_EXPECTATION_EXPLICIT_MINIMUM,
    MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY,
    OBSERVABILITY_NAMESPACE,
    STEP_PAUSE_ARGOCD_APPS,
    TOKEN_DURATION_DEFAULT,
)
from lib.exceptions import StateLoadError, StateLockError, SwitchoverError
from lib.gitops_detector import GitOpsCollector
from lib.report_artifacts import validate_report_artifact_directory
from lib.run_record import HubFacts, RunRecord
from lib.validation import InputValidator, ValidationError
from modules import (
    Decommission,
    Finalization,
    PostActivationVerification,
    PrimaryPreparation,
    SecondaryActivation,
)
from modules.preflight_coordinator import PreflightValidator


def _missing_parse_required_args(args: argparse.Namespace) -> list[str]:
    """Return conditionally required arguments missing after argparse parses modes."""

    setup_requested = getattr(args, "setup", False)
    argocd_resume_only_requested = getattr(args, "argocd_resume_only", False)
    restore_only_requested = getattr(args, "restore_only", False)

    standalone_mode_requested = setup_requested or argocd_resume_only_requested or restore_only_requested
    missing: list[str] = []

    if not (restore_only_requested or argocd_resume_only_requested) and not getattr(args, "primary_context", None):
        missing.append("--primary-context")

    if not standalone_mode_requested:
        if not getattr(args, "method", None):
            missing.append("--method")
        if not getattr(args, "old_hub_action", None):
            missing.append("--old-hub-action")

    return missing


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

  # Restore-only (single hub, restore from S3 backups — no primary hub needed)
  %(prog)s --restore-only --secondary-context new-hub

  # Restore-only with pre-validation
  %(prog)s --restore-only --validate-only --secondary-context new-hub

  # Restore-only dry-run
  %(prog)s --restore-only --dry-run --secondary-context new-hub
        """,
    )

    # Context arguments
    parser.add_argument(
        "--primary-context",
        help="Kubernetes context for primary hub (required unless --restore-only/--argocd-resume-only)",
    )
    parser.add_argument(
        "--secondary-context",
        help="Kubernetes context for secondary hub (required except --decommission/--setup)",
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
    mode_group.add_argument(
        "--argocd-resume-only",
        action="store_true",
        help=(
            "Load state file and restore Argo CD auto-sync for previously paused Applications, then exit. "
            "Use after retargeting Git or for failback to original primary."
        ),
    )

    # Restore-only is a separate operation type (not in mode_group so it can
    # combine with --dry-run and --validate-only)
    parser.add_argument(
        "--restore-only",
        action="store_true",
        help=(
            "Restore managed clusters from existing S3 backups onto a single hub. "
            "No primary hub required. Implies --method full."
        ),
    )

    # Switchover options
    parser.add_argument(
        "--method",
        choices=["passive", "full"],
        help=(
            "Switchover method: passive (continuous sync) or full (one-time restore) "
            "(required unless --setup/--restore-only/--argocd-resume-only)"
        ),
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
    parser.add_argument(
        "--activation-method",
        choices=["patch", "restore"],
        default="patch",
        help=(
            "Activation method for passive restore: patch (default) or restore "
            "(delete passive sync and create restore-acm-activate)."
        ),
    )
    parser.add_argument(
        "--min-managed-clusters",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Minimum number of non-local ManagedClusters expected on the secondary hub after restore. "
            "Activation fails if fewer than N clusters are found. Default derives the expected count "
            "from primary preflight; explicit 0 allows an empty hub."
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
        "--report-dir",
        default=None,
        help=(
            "Directory for machine-readable JSON report artifacts "
            "(preflight-report.json, switchover-report.json, restore-only-report.json, or decommission-report.json)"
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
        help=(
            "Action for old primary hub after switchover "
            "(required unless --setup/--restore-only/--argocd-resume-only): "
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
        default=TOKEN_DURATION_DEFAULT,
        help=f"Token validity duration for generated kubeconfigs (default: {TOKEN_DURATION_DEFAULT})",
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
    setup_group.add_argument(
        "--include-decommission",
        action="store_true",
        help=(
            "With --setup, also deploy and validate the optional decommission RBAC extension "
            "needed for old-hub teardown."
        ),
    )

    # Optional features
    parser.add_argument(
        "--skip-observability-checks",
        action="store_true",
        help="Skip Observability-related steps even if detected",
    )
    parser.add_argument(
        "--disable-observability-on-secondary",
        action="store_true",
        help=(
            "Deprecated compatibility flag: old-hub secondary flows now delete "
            "MultiClusterObservability automatically (not for decommission)"
        ),
    )
    parser.add_argument(
        "--skip-gitops-check",
        action="store_true",
        help="Disable GitOps marker detection (ArgoCD, Flux) to skip drift warnings",
    )
    parser.add_argument(
        "--skip-rbac-validation",
        action="store_true",
        help="Skip RBAC permission validation during pre-flight checks",
    )
    parser.add_argument(
        "--argocd-manage",
        action="store_true",
        help=(
            "Pause auto-sync for ACM-touching Argo CD Applications during switchover. "
            "Applications are left paused; resume explicitly with --argocd-resume-only after updating Git."
        ),
    )
    parser.add_argument(
        "--argocd-resume-on-failure",
        action="store_true",
        help=(
            "When used with --argocd-manage, attempt to resume paused Argo CD Applications "
            "if the switchover fails. Best-effort: resume errors are logged but do not mask "
            "the original failure."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non-interactive mode for decommission (dangerous)",
    )

    # Logging
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force execution even with stale state file (use with caution)",
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="Log output format (text or json)",
    )

    args = parser.parse_args()
    missing_required_args = _missing_parse_required_args(args)
    if missing_required_args:
        parser.error("the following arguments are required: " + ", ".join(missing_required_args))

    return args


def validate_args(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Validate argument combinations and input values."""
    try:
        # Perform comprehensive input validation
        # Note: validate_all_cli_args already checks that secondary_context is
        # provided when not in decommission mode
        InputValidator.validate_all_cli_args(args)

        # Resolve the state dir early so an unsafe ACM_SWITCHOVER_STATE_DIR
        # fails here with a clean message. The safety posture itself lives in
        # runtime_bootstrap.get_default_state_dir (shared with show_state).
        if not getattr(args, "state_file", None):
            runtime_bootstrap.get_default_state_dir()
        else:
            # Validate user-specified state file path to prevent unsafe locations
            InputValidator.validate_safe_filesystem_path(args.state_file, "--state-file")

        if getattr(args, "report_dir", None):
            validate_report_artifact_directory(args.report_dir, "--report-dir")

        if getattr(args, "validate_only", False) and getattr(args, "argocd_manage", False):
            logger.warning("--argocd-manage has no effect with --validate-only; continuing without Argo CD management.")

    except ValidationError as e:
        logger.error("Validation error: %s", str(e))
        sys.exit(EXIT_FAILURE)
    except Exception as e:
        logger.error("Unexpected validation error: %s", str(e))
        sys.exit(EXIT_FAILURE)


def _build_switchover_runner_hooks() -> operation_runners.SwitchoverRunnerHooks:
    return operation_runners.SwitchoverRunnerHooks(
        preflight_handler=_run_phase_preflight,
        primary_prep_handler=_run_phase_primary_prep,
        activation_handler=_run_phase_activation,
        post_activation_handler=_run_phase_post_activation,
        finalization_handler=_run_phase_finalization,
        fail_phase=_fail_phase,
        fail_unexpected_phase_state=_fail_unexpected_phase_state,
        on_phase_failure=_attempt_argocd_resume_on_failure,
    )


def _build_restore_only_runner_hooks() -> operation_runners.RestoreOnlyRunnerHooks:
    return operation_runners.RestoreOnlyRunnerHooks(
        preflight_handler=_run_phase_preflight,
        restore_only_pause_handler=_run_restore_only_argocd_pause,
        activation_handler=_run_phase_activation,
        post_activation_handler=_run_phase_post_activation,
        finalization_handler=_run_phase_finalization,
        fail_phase=_fail_phase,
        fail_unexpected_phase_state=_fail_unexpected_phase_state,
        on_phase_failure=_attempt_argocd_resume_on_failure,
    )


def _build_operation_dispatch_hooks() -> operation_runners.OperationDispatchHooks:
    return operation_runners.OperationDispatchHooks(
        decommission_runner=run_decommission,
        restore_only_runner=run_restore_only,
        switchover_runner=run_switchover,
    )


def run_switchover(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
):
    """Execute the main switchover workflow."""
    # The public wrapper owns dry-run rollback because the library runner still
    # performs durable phase bookkeeping to exercise the real workflow path.
    dry_run_snapshot = state.capture_state_snapshot() if getattr(args, "dry_run", False) else None
    try:
        return _run_switchover_impl(args, state, primary, secondary, logger)
    finally:
        if dry_run_snapshot is not None:
            state.restore_state_snapshot(dry_run_snapshot)


def _run_switchover_impl(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
):
    """Execute the main switchover workflow."""
    return operation_runners.run_switchover_impl(
        args,
        state,
        primary,
        secondary,
        logger,
        hooks=_build_switchover_runner_hooks(),
    )


def _run_restore_only_argocd_pause(
    args: argparse.Namespace,
    state: StateManager,
    _primary: Optional[KubeClient],
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    """Pause ArgoCD auto-sync on secondary hub before restore-only activation.

    This is the restore-only equivalent of PrimaryPrep._pause_argocd_acm_apps,
    targeting only the secondary hub. Uses the same state keys (argocd_run_id,
    argocd_paused_apps) so --argocd-resume-only works after restore completes.
    """
    if not getattr(args, "argocd_manage", False):
        return True
    if state.is_step_completed(STEP_PAUSE_ARGOCD_APPS):
        logger.info("Argo CD pause already completed, skipping")
        return True

    try:
        register = ArgocdPauseRegister(state, dry_run=getattr(args, "dry_run", False))
        summary = register.pause_hubs([(secondary, HUB_ROLE_SECONDARY)])
    except Exception as exc:
        return _fail_phase(state, f"Argo CD pause on secondary hub failed: {exc}", logger)

    if summary.blocked:
        return _fail_phase(
            state,
            f"Argo CD auto-sync pause blocked for {summary.blocked} Application(s); "
            "pause the owning ApplicationSet first",
            logger,
        )

    if summary.failed:
        return _fail_phase(
            state,
            f"Argo CD auto-sync pause failed for {summary.failed} Application(s)",
            logger,
        )

    if summary.run_id is not None:
        logger.info(
            "Argo CD: %d Application(s) %s on secondary hub (run_id=%s). "
            "Left paused by default; use --argocd-resume-only after retargeting Git.",
            summary.newly_paused,
            "would be paused" if summary.dry_run else "paused",
            summary.run_id,
        )
    if not getattr(args, "dry_run", False):
        state.mark_step_completed(STEP_PAUSE_ARGOCD_APPS)
    return True


def run_restore_only(
    args: argparse.Namespace,
    state: StateManager,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    """Execute restore-only workflow for single-hub restore from backup."""
    # Keep dry-run rollback at the wrapper boundary so restore-only and full
    # switchover discard all durable state written by shared runner helpers.
    dry_run_snapshot = state.capture_state_snapshot() if getattr(args, "dry_run", False) else None
    try:
        return _run_restore_only_impl(args, state, secondary, logger)
    finally:
        if dry_run_snapshot is not None:
            state.restore_state_snapshot(dry_run_snapshot)


def _run_restore_only_impl(
    args: argparse.Namespace,
    state: StateManager,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    """Execute restore-only workflow for single-hub restore from backup.

    This is a simplified variant of run_switchover() that skips PRIMARY_PREP
    (no primary hub exists) and runs finalization with old_hub_action="none".
    """
    return operation_runners.run_restore_only_impl(
        args,
        state,
        secondary,
        logger,
        hooks=_build_restore_only_runner_hooks(),
    )


def _attempt_argocd_resume_on_failure(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> None:
    """Best-effort resume of paused ArgoCD Applications after a switchover failure.

    Called when a phase handler returns False and --argocd-resume-on-failure is set.
    Resume errors are logged but never mask the original failure.
    """
    argocd_resume.attempt_argocd_resume_on_failure(
        args,
        state,
        primary,
        secondary,
        logger,
        kube_client_factory=KubeClient,
    )


def _fail_phase(state: StateManager, message: str, logger: logging.Logger) -> bool:
    """Record a phase failure with consistent error metadata and return False.

    F8 fix: Only append the generic wrapper message when the phase module
    has NOT already recorded a specific error for the current phase. This
    keeps the most recent (and most actionable) error visible to resume
    logic and troubleshooting output.
    """
    logger.error(message)
    current_phase = state.get_current_phase().value
    errors = state.get_errors()
    last_error = errors[-1] if errors else {}
    retry_error_baseline = state.get_retry_error_baseline()
    retry_has_no_new_phase_error = (
        isinstance(retry_error_baseline, dict)
        and retry_error_baseline.get("phase") == current_phase
        and len(errors) == retry_error_baseline.get("count", -1)
    )
    # If the module already recorded an error for this phase, don't overwrite
    # it with the generic wrapper message.
    if retry_has_no_new_phase_error or last_error.get("phase") != current_phase:
        state.add_error(message, phase=current_phase)
    state.set_phase(Phase.FAILED)
    return False


def _fail_unexpected_phase_state(
    state: StateManager,
    expected_phase: Phase,
    logger: logging.Logger,
) -> bool:
    """Fail when a successful phase handler leaves an impossible resume state."""

    observed_phase = state.get_current_phase()
    message = (
        f"Phase handler reported success but left state in phase '{observed_phase.value}'; "
        f"expected phase '{expected_phase.value}'."
    )
    logger.error(message)
    state.add_error(message, phase=expected_phase.value)
    state.set_phase(Phase.FAILED)
    return False


def _run_phase_preflight(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 1: PRE-FLIGHT VALIDATION", logger)

    state.set_phase(Phase.PREFLIGHT)

    is_restore_only = getattr(args, "restore_only", False)
    effective_argocd_manage = getattr(args, "argocd_manage", False) and not getattr(args, "validate_only", False)
    validator = PreflightValidator(
        primary,
        secondary,
        args.method,
        skip_rbac_validation=args.skip_rbac_validation,
        include_decommission=getattr(args, "old_hub_action", None) == "decommission",
        include_old_hub_finalization=(
            getattr(args, "old_hub_action", None) == "secondary"
            and not getattr(args, "skip_observability_checks", False)
        ),
        argocd_manage=effective_argocd_manage,
        skip_gitops_check=getattr(args, "skip_gitops_check", False),
        restore_only=is_restore_only,
    )
    passed, config = validator.validate_all()
    run_record = RunRecord(state)
    run_record.record_preflight_results(
        validator.reporter.results,
        passed=passed,
        critical_failures=len(validator.reporter.critical_failures()),
    )

    if not passed:
        return _fail_phase(state, "Pre-flight validation failed! Cannot proceed.", logger)

    if is_restore_only:
        primary_version = "unknown"
        primary_obs_detected = False
        primary_obs_enabled = False
        expected_managed_cluster_names: list[str] = []
    else:
        primary_version = config["primary_version"]
        primary_obs_detected = config["primary_observability_detected"]
        primary_obs_enabled = primary_obs_detected and not args.skip_observability_checks
        expected_managed_cluster_names = list(config.get("expected_managed_cluster_names", []))

    secondary_obs_detected = config["secondary_observability_detected"]
    secondary_obs_enabled = secondary_obs_detected and not args.skip_observability_checks
    run_record.record_hub_facts(
        HubFacts(
            primary_version=primary_version,
            primary_observability_detected=primary_obs_detected,
            primary_has_observability=primary_obs_enabled,
            secondary_version=config["secondary_version"],
            secondary_observability_detected=secondary_obs_detected,
            secondary_has_observability=secondary_obs_enabled,
            has_observability=primary_obs_enabled or secondary_obs_enabled,
        )
    )
    if is_restore_only:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY
    elif getattr(args, "min_managed_clusters", None) is None:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT
    elif getattr(args, "min_managed_clusters", 0) == 0:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED
    else:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_MINIMUM
    run_record.record_managed_cluster_expectation(
        names=expected_managed_cluster_names,
        count=len(expected_managed_cluster_names),
        mode=expectation_mode,
    )

    if not getattr(args, "skip_gitops_check", False):
        _report_argocd_acm_impact(
            primary,
            secondary,
            logger,
            argocd_manage=effective_argocd_manage,
        )

    if args.validate_only:
        logger.info("\n✓ Validation complete. Exiting (--validate-only mode)")
        return True

    logger.info("\n✓ Pre-flight validation passed!")
    return True


def _report_argocd_acm_impact(
    primary: Optional[KubeClient],
    secondary: KubeClient,
    logger: logging.Logger,
    argocd_manage: bool = False,
) -> None:
    """Run Argo CD detection and log aggregate ACM Application advisories."""
    all_acm_apps: list = []
    hub_pairs = []
    if primary is not None:
        hub_pairs.append((HUB_ROLE_PRIMARY, primary))
    hub_pairs.append((HUB_ROLE_SECONDARY, secondary))
    for label, client in hub_pairs:
        try:
            discovery = argocd_lib.detect_argocd_installation(client, public_advisory=True)
            if not discovery.has_applications_crd:
                logger.info(
                    "[%s] Argo CD advisory: Applications CRD not found; check skipped",
                    label,
                )
                continue
            apps = argocd_lib.list_argocd_applications(client, namespaces=None, public_advisory=True)
            acm_apps = argocd_lib.find_acm_touching_apps(apps, public_advisory=True)
            if not acm_apps:
                logger.info(
                    "[%s] Argo CD advisory: 0 ACM-touching Application(s) detected",
                    label,
                )
                continue
            all_acm_apps.extend(acm_apps)
            logger.warning(
                "[%s] Argo CD advisory: %d ACM-touching Application(s) detected; "
                "pause or scope declarative management before switchover.",
                label,
                len(acm_apps),
            )
        except Exception:
            logger.warning(
                "[%s] Unable to complete Argo CD check; continuing without blocking switchover.",
                label,
            )

    if not argocd_manage and all_acm_apps:
        autosync_count = sum(
            1 for a in all_acm_apps if (a.app.get("spec", {}) or {}).get("syncPolicy", {}).get("automated")
        )
        if autosync_count:
            if primary is None:
                logger.warning(
                    "\n⚠ Argo CD advisory: %d ACM-touching Application(s) with auto-sync detected.\n"
                    "  Use --argocd-manage to pause auto-sync on the secondary hub before restore.\n"
                    "  Without pausing, Argo CD may revert restored resources.\n"
                    "  To suppress: --skip-gitops-check",
                    autosync_count,
                )
            else:
                logger.warning(
                    "\n⚠ Argo CD advisory: %d ACM-touching Application(s) with auto-sync detected.\n"
                    "  Consider --argocd-manage to pause auto-sync during switchover.\n"
                    "  Without pausing, Argo CD may revert switchover changes.\n"
                    "  To suppress: --skip-gitops-check",
                    autosync_count,
                )


def _run_phase_primary_prep(
    args: argparse.Namespace,
    state: StateManager,
    primary: KubeClient,
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 2: PRIMARY HUB PREPARATION", logger)
    state.set_phase(Phase.PRIMARY_PREP)

    facts = RunRecord(state).hub_facts()
    prep = PrimaryPreparation(
        primary,
        state,
        facts.primary_version,
        facts.primary_has_observability,
        dry_run=args.dry_run,
        argocd_manage=getattr(args, "argocd_manage", False),
        secondary_client=secondary,
    )

    if not prep.prepare():
        return _fail_phase(state, "Primary hub preparation failed!", logger)

    logger.info("\n✓ Primary hub preparation complete!")
    return True


def _run_phase_activation(
    args: argparse.Namespace,
    state: StateManager,
    _primary: Optional[KubeClient],
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 3: SECONDARY HUB ACTIVATION", logger)
    state.set_phase(Phase.ACTIVATION)
    min_managed_clusters, expected_names, enforce_expected_names = _resolve_managed_cluster_expectation(args, state)

    activation = SecondaryActivation(
        secondary_client=secondary,
        state_manager=state,
        method=args.method,
        activation_method=getattr(args, "activation_method", "patch"),
        manage_auto_import_strategy=getattr(args, "manage_auto_import_strategy", False),
        old_hub_action=getattr(args, "old_hub_action", "none"),
        min_managed_clusters=min_managed_clusters,
        expected_managed_cluster_names=expected_names,
        enforce_expected_managed_cluster_names=enforce_expected_names,
    )

    if not activation.activate():
        return _fail_phase(state, "Secondary hub activation failed!", logger)

    logger.info("\n✓ Secondary hub activation complete!")
    return True


def _run_phase_post_activation(
    args: argparse.Namespace,
    state: StateManager,
    _primary: Optional[KubeClient],
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 4: POST-ACTIVATION VERIFICATION", logger)
    state.set_phase(Phase.POST_ACTIVATION)
    min_managed_clusters, expected_names, enforce_expected_names = _resolve_managed_cluster_expectation(args, state)

    facts = RunRecord(state).hub_facts()
    verification = PostActivationVerification(
        secondary,
        state,
        facts.secondary_has_observability,
        dry_run=args.dry_run,
        min_managed_clusters=min_managed_clusters,
        expected_managed_cluster_names=expected_names,
        enforce_expected_managed_cluster_names=enforce_expected_names,
    )

    if not verification.verify():
        return _fail_phase(state, "Post-activation verification failed!", logger)

    logger.info("\n✓ Post-activation verification complete!")
    return True


def _resolve_managed_cluster_expectation(
    args: argparse.Namespace,
    state: StateManager,
) -> tuple[int, list[str], bool]:
    """Return effective ManagedCluster count/name enforcement for activation phases."""
    raw_min = getattr(args, "min_managed_clusters", None)
    expectation = RunRecord(state).managed_cluster_expectation()
    expected_names = list(expectation.names)
    expected_count = expectation.count

    if raw_min is None:
        if expectation.mode is None:
            raise SwitchoverError(
                "No managed-cluster expectation is recorded in the state file (preflight has not "
                "run in this state, or the state predates expectation recording). Refusing to skip "
                "managed-cluster verification: pass --min-managed-clusters explicitly, or re-run "
                "preflight to record the expectation."
            )
        if expectation.mode == MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY and expected_count == 0 and not expected_names:
            return 1, [], False
        return expected_count, expected_names, bool(expected_names)
    if raw_min == 0:
        return 0, [], False
    return int(raw_min), [], False


def _run_phase_finalization(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: KubeClient,
    logger: logging.Logger,
) -> bool:
    _log_phase_banner("PHASE 5: FINALIZATION", logger)
    state.set_phase(Phase.FINALIZATION)

    is_restore_only = getattr(args, "restore_only", False)
    old_hub_action = "none" if is_restore_only else args.old_hub_action
    facts = RunRecord(state).hub_facts()
    finalization = Finalization(
        secondary_client=secondary,
        state_manager=state,
        acm_version=facts.secondary_version,
        primary_client=primary,
        primary_has_observability=facts.primary_has_observability,
        dry_run=args.dry_run,
        old_hub_action=old_hub_action,
        manage_auto_import_strategy=getattr(args, "manage_auto_import_strategy", False),
        disable_observability_on_secondary=getattr(args, "disable_observability_on_secondary", False),
        restore_only=is_restore_only,
    )

    if not finalization.finalize():
        return _fail_phase(state, "Finalization failed!", logger)

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
    has_observability = primary.namespace_exists(OBSERVABILITY_NAMESPACE)
    if has_observability:
        logger.info(
            "Observability detected on hub (namespace %s exists)",
            OBSERVABILITY_NAMESPACE,
        )

    if not getattr(args, "skip_rbac_validation", False):
        try:
            validate_decommission_permissions(
                primary_client=primary,
                skip_observability=not has_observability,
            )
        except ValidationError as exc:
            logger.error("RBAC validation failed: %s", exc)
            logger.warning(
                "Decommission requires the opt-in decommission RBAC extension. "
                "You can skip this check with --skip-rbac-validation if you have already verified permissions."
            )
            return False
    else:
        logger.info("RBAC validation skipped (--skip-rbac-validation specified)")

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


def _report_target(args: argparse.Namespace) -> tuple[str, str]:
    """Return report type and filename for the current Python CLI operation."""
    return cli_outcomes.report_target(args)


def _phase_report_from_state(state_snapshot: dict) -> dict:
    """Build a compact phase map from durable state."""
    return cli_outcomes.phase_report_from_state(state_snapshot)


def _write_python_report(
    args: argparse.Namespace,
    state: Optional[StateManager],
    status: str,
    logger: logging.Logger,
) -> None:
    """Write a Python CLI report artifact when --report-dir is set."""
    cli_outcomes.write_python_report(args, state, status, logger)


def _build_cli_operation_hooks() -> cli_outcomes.CliOperationHooks:
    return cli_outcomes.CliOperationHooks(
        bind_runtime_hub_identities=_bind_runtime_hub_identities,
        run_argocd_resume_only=_run_argocd_resume_only,
        execute_operation=_execute_operation,
        write_python_report=_write_python_report,
        gitops_reporter_factory=GitOpsCollector.get_instance,
    )


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
    import subprocess  # nosec B404

    # Note: admin_kubeconfig validation is already done by InputValidator.validate_all_cli_args()
    # We just need to check if the file exists
    if not os.path.isfile(args.admin_kubeconfig):
        logger.error("Admin kubeconfig file not found: %s", args.admin_kubeconfig)
        return False

    script_dir = os.path.dirname(os.path.abspath(__file__))
    setup_script = os.path.join(script_dir, "scripts", "setup-rbac.sh")

    if not os.path.isfile(setup_script):
        logger.error("Setup script not found: %s", setup_script)
        return False

    # Build command
    cmd = [
        setup_script,
        "--admin-kubeconfig",
        args.admin_kubeconfig,
        "--context",
        args.primary_context,
        "--role",
        args.role,
        "--token-duration",
        args.token_duration,
        "--output-dir",
        args.output_dir,
    ]

    if args.skip_kubeconfig_generation:
        cmd.append("--skip-kubeconfig")

    if getattr(args, "include_decommission", False):
        cmd.append("--include-decommission")

    if args.dry_run:
        cmd.append("--dry-run")

    logger.info("Running RBAC setup...")
    logger.info("  Context: %s", args.primary_context)
    logger.info("  Role: %s", args.role)
    logger.info("  Token duration: %s", args.token_duration)
    logger.info("  Output directory: %s", args.output_dir)
    logger.info("  Include decommission RBAC: %s", getattr(args, "include_decommission", False))

    try:
        result = subprocess.run(  # nosec B603
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


def _reject_dry_run_reset_state(args: argparse.Namespace, logger: logging.Logger) -> None:
    """Fail closed if --reset-state is combined with --dry-run (parity audit H10).

    Capture-before-delete is not viable here: a corrupt, unparseable state file
    is the primary --reset-state use case, so there may be nothing to snapshot.
    """
    if getattr(args, "reset_state", False) and getattr(args, "dry_run", False):
        logger.error(
            "--reset-state cannot be combined with --dry-run: a dry run must never "
            "delete durable switchover state (parity audit H10). Run --reset-state "
            "without --dry-run if you really intend to discard the state file."
        )
        sys.exit(EXIT_FAILURE)


def _prepare_runtime(
    args: argparse.Namespace,
    logger: logging.Logger,
    resolved_state_file: str,
) -> runtime_bootstrap.RuntimeContext:
    """Create state and clients while preserving existing entrypoint ordering."""
    _reject_dry_run_reset_state(args, logger)

    if getattr(args, "reset_state", False):
        # --reset-state: delete existing state file before loading so StateManager
        # starts fresh.  We handle this before constructing StateManager to allow
        # recovery from a corrupt state file via the flag.
        if os.path.exists(resolved_state_file):
            logger.warning("Resetting state file: %s", resolved_state_file)
            try:
                os.remove(resolved_state_file)
            except OSError as exc:
                logger.error("Failed to remove state file: %s", exc)
                sys.exit(EXIT_FAILURE)

    try:
        state = StateManager(resolved_state_file)
    except (StateLoadError, StateLockError) as exc:
        logger.error("")
        logger.error("FATAL: Cannot initialize switchover state file.")
        logger.error("%s", exc)
        logger.error("")
        if isinstance(exc, StateLoadError):
            logger.error("To start a fresh switchover run:")
            logger.error("  --reset-state  (removes and recreates the state file)")
            logger.error("  or manually remove: %s", resolved_state_file)
        sys.exit(EXIT_FAILURE)

    should_bind_state = not getattr(args, "argocd_resume_only", False) and not getattr(args, "decommission", False)
    should_record_state_errors = not getattr(args, "decommission", False)

    dry_run_state_guard = None
    if should_bind_state:
        if getattr(args, "dry_run", False):
            # Capture BEFORE ensure_contexts: its context-mismatch reset flushes
            # to disk, and a later snapshot would only preserve the wiped state
            # (parity audit finding H10). main() restores this guard after the
            # rehearsal completes.
            dry_run_state_guard = state.capture_state_snapshot()
        try:
            state.ensure_contexts(getattr(args, "primary_context", None), getattr(args, "secondary_context", None))
        except BaseException:
            # H10 guard: ensure_contexts flushes its context-mismatch reset as
            # a critical checkpoint, so an interrupt or I/O error raised during
            # the call can leave the wiped state on disk with main()'s finally
            # never reached. Restore before propagating. If the restore write
            # fails too (same disk fault), let it propagate — the original
            # exception is preserved as __context__.
            if dry_run_state_guard is not None:
                state.restore_state_snapshot(dry_run_state_guard)
            raise

    try:
        primary, secondary = _initialize_clients(args, logger)
    except Exception as exc:
        logger.error("Failed to initialize Kubernetes clients: %s", exc)
        # H10 guard: restore dry-run state before exiting, so the rehearsal's
        # on-disk effects (including the ensure_contexts reset) are always
        # rolled back, even on client-init failure.
        if dry_run_state_guard is not None:
            state.restore_state_snapshot(dry_run_state_guard)
        sys.exit(EXIT_FAILURE)
    except BaseException:
        # H10 guard: _initialize_clients performs live network connects, so a
        # Ctrl-C (KeyboardInterrupt) or SystemExit here is realistic and is
        # not caught by the Exception arm above. Restore the dry-run state
        # before propagating so the rehearsal's on-disk effects are always
        # rolled back, even on interruption.
        if dry_run_state_guard is not None:
            state.restore_state_snapshot(dry_run_state_guard)
        raise

    return runtime_bootstrap.RuntimeContext(
        state_file=resolved_state_file,
        state=state,
        primary=primary,
        secondary=secondary,
        should_bind_state=should_bind_state,
        should_record_state_errors=should_record_state_errors,
        dry_run_state_guard=dry_run_state_guard,
    )


def _bind_runtime_hub_identities(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
) -> None:
    """Validate and bind live hub identities inside the guarded main flow."""
    state.ensure_hub_identities(
        _collect_hub_identities(primary, secondary),
        allow_legacy_backfill=getattr(args, "force", False),
        persist=not (getattr(args, "dry_run", False) or getattr(args, "validate_only", False)),
    )


def main():
    """Main entry point."""
    args = parse_args()
    state: Optional[StateManager] = None

    # Set up logging early so validate_args can use logger
    logger = setup_logging(args.verbose, args.log_format)

    validate_args(args, logger)
    try:
        resolved_state_file = _resolve_state_file(
            args.state_file,
            getattr(args, "primary_context", None),
            args.secondary_context,
            argocd_resume_only=getattr(args, "argocd_resume_only", False),
        )
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(EXIT_FAILURE)
    args.state_file = resolved_state_file

    logger.info("ACM Hub Switchover Automation v%s (%s)", __version__, __version_date__)
    logger.info("Started at: %s", datetime.now(timezone.utc).isoformat())
    logger.info("Using state file: %s", resolved_state_file)

    # Configure GitOps detection based on CLI flag
    if args.skip_gitops_check:
        GitOpsCollector.get_instance().set_enabled(False)
        logger.debug("GitOps marker detection disabled")

    # Setup mode doesn't need state tracking or Kubernetes clients
    # It uses the admin-kubeconfig directly via the shell script
    if args.setup:
        sys.exit(
            cli_outcomes.run_setup_mode(
                args,
                logger,
                run_setup=run_setup,
                exit_success=EXIT_SUCCESS,
                exit_failure=EXIT_FAILURE,
                exit_interrupt=EXIT_INTERRUPT,
            )
        )

    if getattr(args, "argocd_resume_only", False) and not os.path.exists(resolved_state_file):
        logger.error(
            "State file not found for --argocd-resume-only: %s. "
            "Run a switchover with Argo CD management first or pass --state-file explicitly.",
            resolved_state_file,
        )
        sys.exit(EXIT_FAILURE)

    runtime = _prepare_runtime(args, logger, resolved_state_file)
    state = runtime.state

    try:
        operation_exit_code = cli_outcomes.run_operation_mode(
            args,
            state,
            runtime.primary,
            runtime.secondary,
            logger,
            should_bind_state=runtime.should_bind_state,
            should_record_state_errors=runtime.should_record_state_errors,
            hooks=_build_cli_operation_hooks(),
            exit_success=EXIT_SUCCESS,
            exit_failure=EXIT_FAILURE,
            exit_interrupt=EXIT_INTERRUPT,
        )
    finally:
        if runtime.dry_run_state_guard is not None:
            # H10 guard: put the state file back exactly as it was before the
            # dry-run rehearsal, including a context-mismatch reset that
            # ensure_contexts may have flushed in _prepare_runtime.
            state.restore_state_snapshot(runtime.dry_run_state_guard)
    sys.exit(operation_exit_code)


def _initialize_clients(
    args: argparse.Namespace,
    logger: logging.Logger,
) -> Tuple[Optional[KubeClient], Optional[KubeClient]]:
    """Create Kubernetes clients for provided contexts."""
    return runtime_bootstrap.initialize_clients(args, logger, client_factory=KubeClient)


def _collect_hub_identities(
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
) -> dict[str, dict[str, Optional[str]]]:
    """Read live cluster identities for available hub clients."""
    return runtime_bootstrap.collect_hub_identities(primary, secondary)


def _get_default_state_dir() -> str:
    return runtime_bootstrap.get_default_state_dir()


def _resolve_state_file(
    requested_path: Optional[str],
    primary_ctx: Optional[str],
    secondary_ctx: Optional[str],
    argocd_resume_only: bool = False,
) -> str:
    """Derive the state file path based on contexts unless user provided one.

    Note: restore-only mode needs no special handling here because
    --restore-only forbids --primary-context, so primary_ctx is None,
    and the runtime bootstrap default-state builder naturally
    produces the correct "switchover-restore-only__<sec>.json" filename.
    """
    return runtime_bootstrap.resolve_state_file(
        requested_path=requested_path,
        primary_ctx=primary_ctx,
        secondary_ctx=secondary_ctx,
        argocd_resume_only=argocd_resume_only,
    )


def _prepare_argocd_resume_clients(
    args: argparse.Namespace,
    state: StateManager,
    paused_hub_roles: set[str],
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
    *,
    allow_primary_load_from_state: bool,
) -> tuple[Optional[KubeClient], Optional[KubeClient]]:
    """Resolve client mapping and validate hub identity bindings before resume."""
    return argocd_resume.prepare_argocd_resume_clients(
        args,
        state,
        paused_hub_roles,
        primary,
        secondary,
        logger,
        allow_primary_load_from_state=allow_primary_load_from_state,
        kube_client_factory=KubeClient,
    )


def _run_argocd_resume_only(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    """Load state and restore Argo CD auto-sync for previously paused Applications, then exit."""
    return argocd_resume.run_argocd_resume_only(
        args,
        state,
        primary,
        secondary,
        logger,
        kube_client_factory=KubeClient,
    )


def _execute_operation(
    args: argparse.Namespace,
    state: StateManager,
    primary: Optional[KubeClient],
    secondary: Optional[KubeClient],
    logger: logging.Logger,
) -> bool:
    """Execute the operation requested by CLI flags."""
    return operation_runners.execute_operation(
        args,
        state,
        primary,
        secondary,
        logger,
        hooks=_build_operation_dispatch_hooks(),
    )


if __name__ == "__main__":
    main()
