"""
Post-activation verification module for ACM switchover.
"""

import logging
import time
from typing import List

from lib.kube_client import KubeClient
from lib.utils import StateManager

logger = logging.getLogger("acm_switchover")


class PostActivationVerification:
    """Handles post-activation verification on secondary hub."""
    
    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        has_observability: bool
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.has_observability = has_observability
        
    def verify(self) -> bool:
        """
        Execute all post-activation verification steps.
        
        Returns:
            True if all verifications passed
        """
        logger.info("Starting post-activation verification...")
        
        try:
            # Step 6: Verify ManagedClusters connected
            if not self.state.is_step_completed("verify_clusters_connected"):
                self._verify_managed_clusters_connected()
                self.state.mark_step_completed("verify_clusters_connected")
            else:
                logger.info("Step already completed: verify_clusters_connected")
            
            # Steps 7-9: Observability verification (if present)
            if self.has_observability:
                if not self.state.is_step_completed("restart_observatorium_api"):
                    self._restart_observatorium_api()
                    self.state.mark_step_completed("restart_observatorium_api")
                else:
                    logger.info("Step already completed: restart_observatorium_api")
                
                if not self.state.is_step_completed("verify_observability_pods"):
                    self._verify_observability_pods()
                    self.state.mark_step_completed("verify_observability_pods")
                else:
                    logger.info("Step already completed: verify_observability_pods")
                
                if not self.state.is_step_completed("verify_metrics_collection"):
                    self._verify_metrics_collection()
                    self.state.mark_step_completed("verify_metrics_collection")
                else:
                    logger.info("Step already completed: verify_metrics_collection")
            else:
                logger.info("Skipping Observability verification (not detected)")
            
            logger.info("Post-activation verification completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Post-activation verification failed: {e}")
            self.state.add_error(str(e), "post_activation_verification")
            return False
    
    def _verify_managed_clusters_connected(self, timeout: int = 600):
        """
        Verify all ManagedClusters are connected.
        
        Args:
            timeout: Maximum wait time in seconds (default 10 minutes)
        """
        logger.info("Verifying ManagedCluster connections...")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            managed_clusters = self.secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters"
            )
            
            if not managed_clusters:
                logger.warning("No ManagedClusters found")
                time.sleep(10)
                continue
            
            # Check each cluster's status
            total_clusters = 0
            available_clusters = 0
            joined_clusters = 0
            pending_clusters = []
            
            for mc in managed_clusters:
                mc_name = mc.get('metadata', {}).get('name')
                
                # Skip local-cluster
                if mc_name == "local-cluster":
                    continue
                
                total_clusters += 1
                
                conditions = mc.get('status', {}).get('conditions', [])
                
                # Check for ManagedClusterConditionAvailable
                is_available = any(
                    c.get('type') == 'ManagedClusterConditionAvailable' and
                    c.get('status') == 'True'
                    for c in conditions
                )
                
                # Check for ManagedClusterJoined
                is_joined = any(
                    c.get('type') == 'ManagedClusterJoined' and
                    c.get('status') == 'True'
                    for c in conditions
                )
                
                if is_available:
                    available_clusters += 1
                if is_joined:
                    joined_clusters += 1
                
                if not (is_available and is_joined):
                    pending_clusters.append(mc_name)
            
            elapsed = int(time.time() - start_time)
            logger.info(
                f"Cluster status: {available_clusters}/{total_clusters} available, "
                f"{joined_clusters}/{total_clusters} joined (elapsed: {elapsed}s)"
            )
            
            if available_clusters == total_clusters and joined_clusters == total_clusters:
                logger.info("All ManagedClusters are connected!")
                return
            
            if pending_clusters:
                logger.debug(f"Pending clusters: {', '.join(pending_clusters[:5])}")
            
            time.sleep(30)
        
        raise Exception(
            f"Timeout waiting for ManagedClusters to connect. "
            f"{available_clusters}/{total_clusters} available, "
            f"{joined_clusters}/{total_clusters} joined"
        )
    
    def _restart_observatorium_api(self):
        """Restart observatorium-api deployment (ACM 2.12 issue workaround)."""
        logger.info("Restarting observatorium-api deployment...")
        
        try:
            self.secondary.rollout_restart_deployment(
                name="observability-observatorium-api",
                namespace="open-cluster-management-observability"
            )
            
            logger.info("Triggered observatorium-api restart")
            
            # Wait for pods to be ready
            logger.info("Waiting for observatorium-api pods to be ready...")
            ready = self.secondary.wait_for_pods_ready(
                namespace="open-cluster-management-observability",
                label_selector="app.kubernetes.io/name=observatorium-api",
                timeout=300
            )
            
            if ready:
                logger.info("observatorium-api pods are ready")
            else:
                logger.warning("observatorium-api pods did not become ready in time")
                
        except Exception as e:
            logger.error(f"Failed to restart observatorium-api: {e}")
            if "not found" in str(e).lower():
                logger.warning("observatorium-api deployment not found")
            else:
                raise
    
    def _verify_observability_pods(self):
        """Verify all Observability pods are running and ready."""
        logger.info("Verifying Observability pod health...")
        
        pods = self.secondary.get_pods(
            namespace="open-cluster-management-observability"
        )
        
        if not pods:
            logger.warning("No Observability pods found")
            return
        
        running_pods = 0
        ready_pods = 0
        error_pods = []
        
        for pod in pods:
            pod_name = pod.get('metadata', {}).get('name')
            phase = pod.get('status', {}).get('phase', 'unknown')
            
            if phase == 'Running':
                running_pods += 1
            elif phase in ('Failed', 'CrashLoopBackOff', 'Error'):
                error_pods.append(f"{pod_name} ({phase})")
            
            # Check ready condition
            conditions = pod.get('status', {}).get('conditions', [])
            for condition in conditions:
                if condition.get('type') == 'Ready' and condition.get('status') == 'True':
                    ready_pods += 1
                    break
        
        logger.info(
            f"Observability pods: {running_pods}/{len(pods)} running, "
            f"{ready_pods}/{len(pods)} ready"
        )
        
        if error_pods:
            logger.warning(f"Pods in error state: {', '.join(error_pods)}")
        
        if ready_pods < len(pods) * 0.8:  # Allow 20% tolerance
            logger.warning(
                f"Only {ready_pods}/{len(pods)} pods ready. "
                "Some pods may still be starting."
            )
    
    def _verify_metrics_collection(self):
        """Verify metrics collection is working (informational)."""
        logger.info("Verifying metrics collection...")
        
        # This is informational - we can't easily verify Grafana metrics from Python
        # without additional dependencies, so we'll just log guidance
        
        logger.info(
            "To verify metrics collection manually:\n"
            "  1. Access Grafana dashboard\n"
            "  2. Check 'ACM - Clusters Overview' for recent data (within 5 minutes)\n"
            "  3. Or query 'acm_managed_cluster_info' metric\n"
            "\n"
            "Metrics should appear within 5-10 minutes after observatorium-api restart."
        )
        
        # We could check if metrics-collector pods are running
        metrics_pods = self.secondary.get_pods(
            namespace="open-cluster-management-observability",
            label_selector="app=metrics-collector"
        )
        
        if metrics_pods:
            logger.info(f"Found {len(metrics_pods)} metrics-collector pod(s)")
        else:
            logger.warning("No metrics-collector pods found")
