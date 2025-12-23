#!/usr/bin/env python3
"""
Standalone RBAC permission checker for ACM Switchover.

This script validates that the current user or service account has the required
RBAC permissions to execute ACM switchover operations.
"""

import argparse
import logging
import sys

from lib import KubeClient, RBACValidator, __version__, __version_date__, setup_logging


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate RBAC permissions for ACM Switchover",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check permissions on current context
  %(prog)s

  # Check permissions on specific context
  %(prog)s --context primary-hub

  # Check permissions for both hubs
  %(prog)s --primary-context primary-hub --secondary-context secondary-hub

  # Check with decommission permissions
  %(prog)s --include-decommission

  # Skip observability checks
  %(prog)s --skip-observability

  # Check as validator role (read-only, expects write denials)
  %(prog)s --role validator

  # Check as operator role (full permissions)
  %(prog)s --role operator --include-decommission

  # Check RBAC on a managed cluster (spoke)
  %(prog)s --context prod1 --managed-cluster --role operator

  # Validate read-only access on managed cluster
  %(prog)s --context prod1 --managed-cluster --role validator
        """,
    )

    parser.add_argument(
        "--context",
        help="Kubernetes context to check (uses current context if not specified)",
    )
    parser.add_argument(
        "--primary-context",
        help="Primary hub context (for checking both hubs)",
    )
    parser.add_argument(
        "--secondary-context",
        help="Secondary hub context (for checking both hubs)",
    )
    parser.add_argument(
        "--include-decommission",
        action="store_true",
        help="Include decommission permission checks",
    )
    parser.add_argument(
        "--skip-observability",
        action="store_true",
        help="Skip observability namespace checks",
    )
    parser.add_argument(
        "--managed-cluster",
        action="store_true",
        help="Validate as a managed cluster (check open-cluster-management-agent namespace "
             "instead of hub namespaces). Use this when checking RBAC on spoke clusters.",
    )
    parser.add_argument(
        "--role",
        choices=["operator", "validator"],
        default="operator",
        help="Role to validate permissions for (default: operator). "
             "Use 'validator' for read-only service accounts.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Set up logging
    setup_logging(verbose=args.verbose, log_format="text")
    logger = logging.getLogger("acm_switchover")

    logger.info("ACM Switchover RBAC Checker v%s (%s)", __version__, __version_date__)

    try:
        # Determine which contexts to check
        if args.primary_context and args.secondary_context:
            # Check both hubs
            logger.info("Checking RBAC permissions on both hubs...")

            primary_client = KubeClient(context=args.primary_context)
            secondary_client = KubeClient(context=args.secondary_context)

            # Validate primary
            logger.info("\n" + "=" * 80)
            logger.info("PRIMARY HUB (%s) - Role: %s", args.primary_context, args.role)
            logger.info("=" * 80)
            primary_validator = RBACValidator(primary_client, role=args.role)
            primary_valid, _ = primary_validator.validate_all_permissions(
                include_decommission=args.include_decommission,
                skip_observability=args.skip_observability,
            )
            primary_report = primary_validator.generate_permission_report(
                include_decommission=args.include_decommission,
                skip_observability=args.skip_observability,
            )
            print(primary_report)

            # Validate secondary
            logger.info("\n" + "=" * 80)
            logger.info("SECONDARY HUB (%s) - Role: %s", args.secondary_context, args.role)
            logger.info("=" * 80)
            secondary_validator = RBACValidator(secondary_client, role=args.role)
            secondary_valid, _ = secondary_validator.validate_all_permissions(
                include_decommission=False,
                skip_observability=args.skip_observability,
            )
            secondary_report = secondary_validator.generate_permission_report(
                include_decommission=False,  # Decommission only on primary
                skip_observability=args.skip_observability,
            )
            print(secondary_report)

            # Check if both passed

            if primary_valid and secondary_valid:
                logger.info("\n✓ All permissions validated on both hubs")
                sys.exit(0)
            else:
                logger.error("\n✗ Permission validation failed on one or more hubs")
                sys.exit(1)

        else:
            # Check single context
            context = args.context or args.primary_context or args.secondary_context
            if context:
                logger.info("Checking RBAC permissions on context: %s", context)
            else:
                logger.info("Checking RBAC permissions on current context")

            client = KubeClient(context=context)
            validator = RBACValidator(client, role=args.role)

            logger.info("Validating for role: %s", args.role)

            # Check if this is a managed cluster validation
            if args.managed_cluster:
                logger.info("Validating as managed cluster (open-cluster-management-agent namespace)")
                all_valid, errors = validator.validate_managed_cluster_permissions()

                # Generate a simple report for managed cluster
                report = ["=" * 80]
                report.append("MANAGED CLUSTER RBAC PERMISSION VALIDATION REPORT")
                report.append("=" * 80)
                report.append("")

                if all_valid:
                    report.append("✓ STATUS: ALL PERMISSIONS VALIDATED")
                    report.append("")
                    report.append("The current user/service account has all required permissions")
                    report.append("for klusterlet operations on this managed cluster.")
                else:
                    report.append("✗ STATUS: PERMISSION VALIDATION FAILED")
                    report.append("")
                    report.append("The following permissions are missing:")
                    report.append("")
                    for error in errors:
                        report.append(f"  - {error}")
                    report.append("")
                    report.append("REMEDIATION:")
                    report.append("")
                    report.append("Deploy managed cluster RBAC using one of:")
                    report.append("  1. ACM Policy: kubectl apply -f deploy/acm-policies/policy-managed-cluster-rbac.yaml")
                    report.append("  2. Direct apply: See docs/deployment/rbac-deployment.md")

                report.append("=" * 80)
                print("\n".join(report))
            else:
                # Validate hub permissions
                all_valid, _ = validator.validate_all_permissions(
                    include_decommission=args.include_decommission,
                    skip_observability=args.skip_observability,
                )
                report = validator.generate_permission_report(
                    include_decommission=args.include_decommission,
                    skip_observability=args.skip_observability,
                )
                print(report)

            # Exit with appropriate code
            if all_valid:
                sys.exit(0)
            else:
                sys.exit(1)

    except Exception as e:
        logger.error("Error during RBAC validation: %s", str(e))
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
