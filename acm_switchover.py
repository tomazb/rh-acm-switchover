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
import sys
from datetime import datetime

from lib import (
    KubeClient,
    Phase,
    StateManager,
    setup_logging,
    confirm_action
)
from modules import (
    PreflightValidator,
    PrimaryPreparation,
    SecondaryActivation,
    PostActivationVerification,
    Finalization,
    Rollback,
    Decommission
)

logger = None


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='ACM Hub Switchover Automation',
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
        """
    )
    
    # Context arguments
    parser.add_argument(
        '--primary-context',
        required=True,
        help='Kubernetes context for primary hub'
    )
    parser.add_argument(
        '--secondary-context',
        help='Kubernetes context for secondary hub (required for switchover/rollback)'
    )
    
    # Operation mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        '--validate-only',
        action='store_true',
        help='Run validation checks only, make no changes'
    )
    mode_group.add_argument(
        '--dry-run',
        action='store_true',
        help='Show planned actions without executing them'
    )
    mode_group.add_argument(
        '--rollback',
        action='store_true',
        help='Rollback to primary hub'
    )
    mode_group.add_argument(
        '--decommission',
        action='store_true',
        help='Decommission old hub (interactive)'
    )
    
    # Switchover options
    parser.add_argument(
        '--method',
        choices=['passive', 'full'],
        default='passive',
        help='Switchover method: passive (continuous sync) or full (one-time restore)'
    )
    
    # State management
    parser.add_argument(
        '--state-file',
        default='.state/switchover-state.json',
        help='Path to state file for idempotent execution'
    )
    parser.add_argument(
        '--reset-state',
        action='store_true',
        help='Reset state file and start fresh (use with caution)'
    )
    
    # Optional features
    parser.add_argument(
        '--skip-observability-checks',
        action='store_true',
        help='Skip Observability-related steps even if detected'
    )
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Non-interactive mode for decommission (dangerous)'
    )
    
    # Logging
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    
    return parser.parse_args()


def validate_args(args):
    """Validate argument combinations."""
    if not args.decommission and not args.secondary_context:
        print("Error: --secondary-context is required for switchover/rollback operations")
        sys.exit(1)


def run_switchover(args, state: StateManager, primary: KubeClient, secondary: KubeClient):
    """Execute the main switchover workflow."""
    
    # Phase 1: Pre-flight Validation
    if state.get_current_phase() == Phase.INIT or state.get_current_phase() == Phase.PREFLIGHT:
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 1: PRE-FLIGHT VALIDATION")
        logger.info("=" * 60)
        
        state.set_phase(Phase.PREFLIGHT)
        
        validator = PreflightValidator(primary, secondary, args.method)
        passed, config = validator.validate_all()
        
        if not passed:
            logger.error("Pre-flight validation failed! Cannot proceed.")
            state.set_phase(Phase.FAILED)
            return False
        
        # Store detected configuration
        state.set_config("primary_version", config["primary_version"])
        state.set_config("secondary_version", config["secondary_version"])
        state.set_config("has_observability", config["has_observability"] and not args.skip_observability_checks)
        
        if args.validate_only:
            logger.info("\n✓ Validation complete. Exiting (--validate-only mode)")
            return True
        
        logger.info("\n✓ Pre-flight validation passed!")
    
    # Phase 2: Primary Hub Preparation
    if state.get_current_phase() in (Phase.PREFLIGHT, Phase.PRIMARY_PREP):
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 2: PRIMARY HUB PREPARATION")
        logger.info("=" * 60)
        
        state.set_phase(Phase.PRIMARY_PREP)
        
        prep = PrimaryPreparation(
            primary,
            state,
            state.get_config("primary_version", "unknown"),
            state.get_config("has_observability", False)
        )
        
        if not prep.prepare():
            logger.error("Primary hub preparation failed!")
            state.set_phase(Phase.FAILED)
            return False
        
        logger.info("\n✓ Primary hub preparation complete!")
    
    # Phase 3: Secondary Hub Activation
    if state.get_current_phase() in (Phase.PREFLIGHT, Phase.PRIMARY_PREP, Phase.ACTIVATION):
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 3: SECONDARY HUB ACTIVATION")
        logger.info("=" * 60)
        
        state.set_phase(Phase.ACTIVATION)
        
        activation = SecondaryActivation(secondary, state, args.method)
        
        if not activation.activate():
            logger.error("Secondary hub activation failed!")
            state.set_phase(Phase.FAILED)
            return False
        
        logger.info("\n✓ Secondary hub activation complete!")
    
    # Phase 4: Post-Activation Verification
    if state.get_current_phase() in (Phase.ACTIVATION, Phase.POST_ACTIVATION):
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 4: POST-ACTIVATION VERIFICATION")
        logger.info("=" * 60)
        
        state.set_phase(Phase.POST_ACTIVATION)
        
        verification = PostActivationVerification(
            secondary,
            state,
            state.get_config("has_observability", False)
        )
        
        if not verification.verify():
            logger.error("Post-activation verification failed!")
            state.set_phase(Phase.FAILED)
            return False
        
        logger.info("\n✓ Post-activation verification complete!")
    
    # Phase 5: Finalization
    if state.get_current_phase() in (Phase.POST_ACTIVATION, Phase.FINALIZATION):
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 5: FINALIZATION")
        logger.info("=" * 60)
        
        state.set_phase(Phase.FINALIZATION)
        
        finalization = Finalization(
            secondary,
            state,
            state.get_config("secondary_version", "unknown")
        )
        
        if not finalization.finalize():
            logger.error("Finalization failed!")
            state.set_phase(Phase.FAILED)
            return False
        
        logger.info("\n✓ Finalization complete!")
    
    # Mark as completed
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


def run_rollback(args, state: StateManager, primary: KubeClient, secondary: KubeClient):
    """Execute rollback to primary hub."""
    logger.warning("\n" + "=" * 60)
    logger.warning("ROLLBACK MODE")
    logger.warning("=" * 60)
    
    if not confirm_action(
        "\nAre you sure you want to rollback to the primary hub?",
        default=False
    ):
        logger.info("Rollback cancelled by user")
        return False
    
    state.set_phase(Phase.ROLLBACK)
    
    rollback = Rollback(
        primary,
        secondary,
        state,
        state.get_config("primary_version", "unknown"),
        state.get_config("has_observability", False)
    )
    
    if rollback.rollback():
        logger.info("\n✓ Rollback completed successfully!")
        logger.info("Allow 5-10 minutes for ManagedClusters to reconnect to primary hub")
        state.reset()
        return True
    else:
        logger.error("Rollback failed!")
        return False


def run_decommission(args, primary: KubeClient, state: StateManager):
    """Execute decommission of old hub."""
    decom = Decommission(
        primary,
        state.get_config("has_observability", False)
    )
    
    return decom.decommission(interactive=not args.non_interactive)


def main():
    """Main entry point."""
    global logger
    
    args = parse_args()
    validate_args(args)
    
    # Setup logging
    logger = setup_logging(args.verbose)
    
    logger.info("ACM Hub Switchover Automation")
    logger.info(f"Started at: {datetime.utcnow().isoformat()}")
    
    # Initialize state manager
    state = StateManager(args.state_file)
    
    if args.reset_state:
        logger.warning("Resetting state file...")
        state.reset()
    
    # Initialize Kubernetes clients
    try:
        logger.info(f"Connecting to primary hub: {args.primary_context}")
        primary = KubeClient(args.primary_context, dry_run=args.dry_run)
        
        if args.secondary_context:
            logger.info(f"Connecting to secondary hub: {args.secondary_context}")
            secondary = KubeClient(args.secondary_context, dry_run=args.dry_run)
        else:
            secondary = None
            
    except Exception as e:
        logger.error(f"Failed to initialize Kubernetes clients: {e}")
        sys.exit(1)
    
    # Execute requested operation
    try:
        if args.decommission:
            success = run_decommission(args, primary, state)
        elif args.rollback:
            success = run_rollback(args, state, primary, secondary)
        else:
            success = run_switchover(args, state, primary, secondary)
        
        if success:
            logger.info("\n✓ Operation completed successfully!")
            sys.exit(0)
        else:
            logger.error("\n✗ Operation failed!")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.warning("\n\nOperation interrupted by user")
        logger.info(f"State saved to: {args.state_file}")
        logger.info("Re-run the same command to resume from last successful step")
        sys.exit(130)
    except Exception as e:
        logger.error(f"\n✗ Unexpected error: {e}", exc_info=args.verbose)
        state.add_error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
