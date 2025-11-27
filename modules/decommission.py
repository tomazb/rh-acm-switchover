"""
Decommission module for old primary hub.
"""

import logging

from lib.constants import (
    ACM_NAMESPACE,
    DECOMMISSION_POD_INTERVAL,
    DECOMMISSION_POD_TIMEOUT,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
)
from lib.kube_client import KubeClient
from lib.utils import confirm_action
from lib.waiter import wait_for_condition

logger = logging.getLogger("acm_switchover")


class Decommission:
    """Handles decommissioning of old primary hub."""

    def __init__(self, primary_client: KubeClient, has_observability: bool, dry_run: bool = False):
        self.primary = primary_client
        self.has_observability = has_observability
        self.dry_run = dry_run

    def decommission(self, interactive: bool = True) -> bool:
        """
        Decommission old primary hub.

        Args:
            interactive: If True, prompt for confirmation at each step

        Returns:
            True if decommission completed successfully
        """
        logger.warning("=" * 60)
        logger.warning("DECOMMISSION MODE - This will remove ACM from the old hub!")
        logger.warning("=" * 60)

        if interactive:
            if not confirm_action(
                "\nAre you sure you want to proceed with decommissioning the old hub?",
                default=False,
            ):
                logger.info("Decommission cancelled by user")
                return False

        try:
            # Step 12.1-12.2: Delete MultiClusterObservability
            if self.has_observability:
                if not interactive or confirm_action("\nDelete MultiClusterObservability resource?", default=False):
                    self._delete_observability()
                else:
                    logger.info("Skipped: Delete MultiClusterObservability")

            # Step 12.3: Delete ManagedClusters
            if not interactive or confirm_action(
                "\nDelete ManagedCluster resources (excluding local-cluster)?",
                default=False,
            ):
                self._delete_managed_clusters()
            else:
                logger.info("Skipped: Delete ManagedClusters")

            # Step 12.4-12.5: Delete MultiClusterHub
            if not interactive or confirm_action(
                "\nDelete MultiClusterHub resource? (This will remove all ACM components)",
                default=False,
            ):
                self._delete_multiclusterhub()
            else:
                logger.info("Skipped: Delete MultiClusterHub")

            if self.dry_run:
                logger.info("[DRY-RUN] Decommission steps completed (no changes made)")

            logger.info("Decommission completed")
            return True

        except Exception as e:
            logger.error(f"Decommission failed: {e}")
            return False

    def _delete_observability(self):
        """Delete MultiClusterObservability resource."""
        logger.info("Deleting MultiClusterObservability resource...")

        # List all MultiClusterObservability resources
        mcos = self.primary.list_custom_resources(
            group="observability.open-cluster-management.io",
            version="v1beta2",
            plural="multiclusterobservabilities",
        )

        if not mcos:
            logger.info("No MultiClusterObservability resources found")
            return

        for mco in mcos:
            mco_name = mco.get("metadata", {}).get("name")

            if self.dry_run:
                logger.info(f"[DRY-RUN] Would delete MultiClusterObservability: {mco_name}")
                continue

            logger.info(f"Deleting MultiClusterObservability: {mco_name}")

            self.primary.delete_custom_resource(
                group="observability.open-cluster-management.io",
                version="v1beta2",
                plural="multiclusterobservabilities",
                name=mco_name,
            )

        if self.dry_run:
            logger.info("[DRY-RUN] Skipping wait for observability termination")
            return

        def _observability_terminated():
            pods = self.primary.get_pods(namespace=OBSERVABILITY_NAMESPACE)
            if not pods:
                return True, "all observability pods terminated"
            return False, f"{len(pods)} pod(s) remaining"

        success = wait_for_condition(
            "Observability pod termination",
            _observability_terminated,
            timeout=OBSERVABILITY_TERMINATE_TIMEOUT,
            interval=OBSERVABILITY_TERMINATE_INTERVAL,
            logger=logger,
        )

        if not success:
            logger.warning(
                "Some Observability pods still running after %ss",
                OBSERVABILITY_TERMINATE_TIMEOUT,
            )

    def _delete_managed_clusters(self):
        """Delete ManagedCluster resources (excluding local-cluster)."""
        logger.info("Deleting ManagedCluster resources...")

        managed_clusters = self.primary.list_managed_clusters()

        if not managed_clusters:
            logger.info("No ManagedClusters found")
            return

        deleted_count = 0
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")

            # Skip local-cluster
            if mc_name == "local-cluster":
                logger.info(f"Skipping local-cluster")
                continue

            if self.dry_run:
                logger.info(f"[DRY-RUN] Would delete ManagedCluster: {mc_name}")
                deleted_count += 1
                continue

            logger.info(f"Deleting ManagedCluster: {mc_name}")

            self.primary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
                name=mc_name,
            )

            deleted_count += 1

        if self.dry_run:
            logger.info(f"[DRY-RUN] Would delete {deleted_count} ManagedCluster(s)")
        else:
            logger.info(f"Deleted {deleted_count} ManagedCluster(s)")

        # Note: We don't wait for deletion as clusters should have preserveOnDelete=true
        logger.info(
            "Note: ClusterDeployments should have preserveOnDelete=true, "
            "so underlying cluster infrastructure will not be affected."
        )

    def _delete_multiclusterhub(self):
        """Delete MultiClusterHub resource."""
        logger.info("Deleting MultiClusterHub resource...")

        # Get MultiClusterHub
        mchs = self.primary.list_custom_resources(
            group="operator.open-cluster-management.io",
            version="v1",
            plural="multiclusterhubs",
            namespace=ACM_NAMESPACE,
        )

        if not mchs:
            logger.info("No MultiClusterHub resources found")
            return

        for mch in mchs:
            mch_name = mch.get("metadata", {}).get("name")

            if self.dry_run:
                logger.info(f"[DRY-RUN] Would delete MultiClusterHub: {mch_name}")
                continue

            logger.info(f"Deleting MultiClusterHub: {mch_name}")
            logger.info("This may take up to 20 minutes...")

            self.primary.delete_custom_resource(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                name=mch_name,
                namespace=ACM_NAMESPACE,
            )

        if self.dry_run:
            logger.info("[DRY-RUN] Skipping wait for ACM pod removal")
            return

        def _acm_pods_removed():
            pods = self.primary.get_pods(namespace=ACM_NAMESPACE)
            if not pods:
                return True, "all ACM pods removed"
            return False, f"{len(pods)} pod(s) remaining"

        success = wait_for_condition(
            "ACM pod removal",
            _acm_pods_removed,
            timeout=DECOMMISSION_POD_TIMEOUT,
            interval=DECOMMISSION_POD_INTERVAL,
            logger=logger,
        )

        if not success:
            logger.warning(
                "Some ACM pods still running after %ss",
                DECOMMISSION_POD_TIMEOUT,
            )

        logger.info("Decommission complete. Backup data in object storage remains " "available for the new hub.")
