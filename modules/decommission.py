"""
Decommission module for old primary hub.
"""

import logging

from lib.constants import (
    ACM_NAMESPACE,
    ACM_OPERATOR_POD_PREFIX,
    DECOMMISSION_POD_INTERVAL,
    DECOMMISSION_POD_TIMEOUT,
    MANAGED_CLUSTER_DELETE_INTERVAL,
    MANAGED_CLUSTER_DELETE_TIMEOUT,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
)
from lib.exceptions import SwitchoverError
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

        except SwitchoverError as e:
            logger.error("Decommission failed: %s", e)
            return False
        except (RuntimeError, ValueError, Exception) as e:
            logger.error("Unexpected error during decommission: %s", e)
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
                logger.info("[DRY-RUN] Would delete MultiClusterObservability: %s", mco_name)
                continue

            logger.info("Deleting MultiClusterObservability: %s", mco_name)

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
                logger.info("Skipping local-cluster")
                continue

            if self.dry_run:
                logger.info("[DRY-RUN] Would delete ManagedCluster: %s", mc_name)
                deleted_count += 1
                continue

            logger.info("Deleting ManagedCluster: %s", mc_name)

            self.primary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
                name=mc_name,
            )

            deleted_count += 1

        if self.dry_run:
            logger.info("[DRY-RUN] Would delete %s ManagedCluster(s)", deleted_count)
        else:
            logger.info("Deleted %s ManagedCluster(s)", deleted_count)

        # Wait for ManagedClusters to be fully removed (finalizers to complete)
        # This is required before MCH deletion because the MCH admission webhook
        # rejects deletion when ManagedCluster resources still exist
        if deleted_count > 0 and not self.dry_run:
            logger.info("Waiting for ManagedCluster finalizers to complete...")

            def _managed_clusters_removed():
                remaining = self.primary.list_managed_clusters()
                # Filter out local-cluster
                non_local = [
                    mc for mc in remaining if mc.get("metadata", {}).get("name") != "local-cluster"
                ]
                if not non_local:
                    return True, "all ManagedClusters removed (except local-cluster)"
                names = [mc.get("metadata", {}).get("name") for mc in non_local]
                return False, f"{len(non_local)} ManagedCluster(s) remaining: {', '.join(names)}"

            success = wait_for_condition(
                "ManagedCluster removal",
                _managed_clusters_removed,
                timeout=MANAGED_CLUSTER_DELETE_TIMEOUT,
                interval=MANAGED_CLUSTER_DELETE_INTERVAL,
                logger=logger,
            )

            if not success:
                raise SwitchoverError(
                    f"ManagedClusters not fully removed after {MANAGED_CLUSTER_DELETE_TIMEOUT}s. "
                    "Cannot proceed with MultiClusterHub deletion."
                )

            logger.info("All ManagedClusters removed successfully")

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
            logger.info("No MultiClusterHub resources found (already deleted or never created)")
            logger.info(
                "Note: ACM operator pods (%s-*) may still be running - "
                "this is expected as the operator is installed separately",
                ACM_OPERATOR_POD_PREFIX,
            )
            return

        for mch in mchs:
            mch_name = mch.get("metadata", {}).get("name")

            if self.dry_run:
                logger.info("[DRY-RUN] Would delete MultiClusterHub: %s", mch_name)
                continue

            logger.info("Deleting MultiClusterHub: %s", mch_name)
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
            """Check if ACM pods are removed (excluding operator pods which remain)."""
            pods = self.primary.get_pods(namespace=ACM_NAMESPACE)
            if not pods:
                return True, "all ACM pods removed"
            # Filter out operator pods - they remain after MCH deletion
            non_operator_pods = [
                p for p in pods
                if not p.get("metadata", {}).get("name", "").startswith(ACM_OPERATOR_POD_PREFIX)
            ]
            if not non_operator_pods:
                operator_count = len(pods)
                return True, f"all ACM pods removed (except {operator_count} operator pod(s) which remain)"
            return False, f"{len(non_operator_pods)} non-operator pod(s) remaining"

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
        else:
            logger.info(
                "ACM components removed. Operator pods (%s-*) remain as expected.",
                ACM_OPERATOR_POD_PREFIX,
            )

        logger.info("Decommission complete. Backup data in object storage remains available for the new hub.")
