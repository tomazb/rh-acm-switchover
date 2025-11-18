"""
Decommission module for old primary hub.
"""

import logging
import time

from lib.kube_client import KubeClient
from lib.utils import confirm_action

logger = logging.getLogger("acm_switchover")


class Decommission:
    """Handles decommissioning of old primary hub."""
    
    def __init__(self, primary_client: KubeClient, has_observability: bool):
        self.primary = primary_client
        self.has_observability = has_observability
        
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
                default=False
            ):
                logger.info("Decommission cancelled by user")
                return False
        
        try:
            # Step 12.1-12.2: Delete MultiClusterObservability
            if self.has_observability:
                if not interactive or confirm_action(
                    "\nDelete MultiClusterObservability resource?",
                    default=False
                ):
                    self._delete_observability()
                else:
                    logger.info("Skipped: Delete MultiClusterObservability")
            
            # Step 12.3: Delete ManagedClusters
            if not interactive or confirm_action(
                "\nDelete ManagedCluster resources (excluding local-cluster)?",
                default=False
            ):
                self._delete_managed_clusters()
            else:
                logger.info("Skipped: Delete ManagedClusters")
            
            # Step 12.4-12.5: Delete MultiClusterHub
            if not interactive or confirm_action(
                "\nDelete MultiClusterHub resource? (This will remove all ACM components)",
                default=False
            ):
                self._delete_multiclusterhub()
            else:
                logger.info("Skipped: Delete MultiClusterHub")
            
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
            plural="multiclusterobservabilities"
        )
        
        if not mcos:
            logger.info("No MultiClusterObservability resources found")
            return
        
        for mco in mcos:
            mco_name = mco.get('metadata', {}).get('name')
            
            logger.info(f"Deleting MultiClusterObservability: {mco_name}")
            
            self.primary.delete_custom_resource(
                group="observability.open-cluster-management.io",
                version="v1beta2",
                plural="multiclusterobservabilities",
                name=mco_name
            )
        
        # Wait for Observability pods to terminate
        logger.info("Waiting for Observability pods to terminate...")
        timeout = 300  # 5 minutes
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            pods = self.primary.get_pods(
                namespace="open-cluster-management-observability"
            )
            
            if not pods:
                logger.info("All Observability pods terminated")
                break
            
            logger.debug(f"{len(pods)} Observability pod(s) still running...")
            time.sleep(10)
        else:
            logger.warning(f"Some Observability pods still running after {timeout}s")
    
    def _delete_managed_clusters(self):
        """Delete ManagedCluster resources (excluding local-cluster)."""
        logger.info("Deleting ManagedCluster resources...")
        
        managed_clusters = self.primary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters"
        )
        
        if not managed_clusters:
            logger.info("No ManagedClusters found")
            return
        
        deleted_count = 0
        for mc in managed_clusters:
            mc_name = mc.get('metadata', {}).get('name')
            
            # Skip local-cluster
            if mc_name == "local-cluster":
                logger.info(f"Skipping local-cluster")
                continue
            
            logger.info(f"Deleting ManagedCluster: {mc_name}")
            
            self.primary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
                name=mc_name
            )
            
            deleted_count += 1
        
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
            namespace="open-cluster-management"
        )
        
        if not mchs:
            logger.info("No MultiClusterHub resources found")
            return
        
        for mch in mchs:
            mch_name = mch.get('metadata', {}).get('name')
            
            logger.info(f"Deleting MultiClusterHub: {mch_name}")
            logger.info("This may take up to 20 minutes...")
            
            self.primary.delete_custom_resource(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                name=mch_name,
                namespace="open-cluster-management"
            )
        
        # Wait for ACM pods to be removed
        logger.info("Waiting for ACM pods to be removed...")
        timeout = 1200  # 20 minutes
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            pods = self.primary.get_pods(
                namespace="open-cluster-management"
            )
            
            if not pods:
                logger.info("All ACM pods removed")
                break
            
            elapsed = int(time.time() - start_time)
            logger.info(f"{len(pods)} ACM pod(s) still running... (elapsed: {elapsed}s)")
            time.sleep(30)
        else:
            logger.warning(f"Some ACM pods still running after {timeout}s")
        
        logger.info(
            "Decommission complete. Backup data in object storage remains "
            "available for the new hub."
        )
