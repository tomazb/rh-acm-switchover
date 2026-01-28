"""
Post-activation verification module for ACM switchover.
"""

import base64
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from lib.constants import (
    CLUSTER_VERIFY_INTERVAL,
    CLUSTER_VERIFY_MAX_WORKERS,
    CLUSTER_VERIFY_TIMEOUT,
    DEFAULT_KUBECONFIG_SIZE,
    INITIAL_CLUSTER_WAIT_TIMEOUT,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MAX_KUBECONFIG_SIZE,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_POD_TIMEOUT,
    OBSERVATORIUM_API_DEPLOYMENT,
    DISABLE_AUTO_IMPORT_ANNOTATION,
    POD_READINESS_TOLERANCE,
    SECRET_VISIBILITY_INTERVAL,
    SECRET_VISIBILITY_TIMEOUT,
    THANOS_COMPACTOR_LABEL_SELECTOR,
    THANOS_COMPACTOR_STATEFULSET,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import StateManager, dry_run_skip
from lib.waiter import wait_for_condition

logger = logging.getLogger("acm_switchover")


class PostActivationVerification:
    """Handles post-activation verification on secondary hub."""

    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        has_observability: bool,
        dry_run: bool = False,
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.has_observability = has_observability
        self.dry_run = dry_run
        self._cached_managed_clusters: Optional[List[Dict]] = None  # Cache for managed clusters
        # Kubeconfig caching to reduce repeated file I/O (findings #10)
        self._kubeconfig_cache: Optional[Dict] = None
        self._kubeconfig_paths: List[str] = []
        self._kubeconfig_mtime: Dict[str, float] = {}

    def _get_managed_clusters(self, force_refresh: bool = False) -> List[Dict]:
        """Get managed clusters with caching.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            List of managed cluster resources
        """
        if self._cached_managed_clusters is None or force_refresh:
            self._cached_managed_clusters = self.secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
            )
        return self._cached_managed_clusters

    def verify(self) -> bool:
        """
        Execute all post-activation verification steps.

        Returns:
            True if all verifications passed
        """
        logger.info("Starting post-activation verification...")

        try:
            # Phase 1: Cluster connectivity verification
            self._verify_cluster_connections()

            # Phase 2: Auto-import cleanup verification
            self._verify_auto_import_cleanup_step()

            # Phase 3: Observability verification (if present)
            if self.has_observability:
                self._verify_observability_full()
            else:
                logger.info("Skipping Observability verification (not detected)")

            logger.info("Post-activation verification completed successfully")
            return True

        except SwitchoverError as e:
            logger.error("Post-activation verification failed: %s", e)
            self.state.add_error(str(e), "post_activation_verification")
            return False
        except Exception as e:
            logger.error("Unexpected error during post-activation verification: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", "post_activation_verification")
            return False

    def _verify_cluster_connections(self) -> None:
        """
        Verify ManagedClusters are connected, with klusterlet fix fallback.

        First tries a brief wait for clusters to connect automatically.
        If that fails, attempts to fix klusterlet connections and waits again.
        """
        with self.state.step("verify_clusters_connected", logger) as should_run:
            if should_run:
                try:
                    # Initial brief wait - clusters may connect automatically
                    self._verify_managed_clusters_connected(timeout=INITIAL_CLUSTER_WAIT_TIMEOUT)
                except SwitchoverError as e:
                    # Timeout - clusters not connected yet
                    logger.warning(
                        "ManagedClusters not connected after initial wait: %s",
                        e,
                    )
                    logger.info(
                        "Checking if klusterlets need to be fixed (may be pointing to old hub)..."
                    )
                    # Try to fix klusterlet connections first
                    self._verify_klusterlet_connections()
                    self.state.mark_step_completed("verify_klusterlet_connections")

                    # Now wait longer for clusters to reconnect after fix
                    logger.info(
                        "Waiting for ManagedClusters to reconnect after klusterlet fix..."
                    )
                    self._verify_managed_clusters_connected()

        # Optional: Verify klusterlet connections (non-blocking)
        # This may have already been done above if clusters didn't connect initially
        with self.state.step("verify_klusterlet_connections", logger) as should_run:
            if should_run:
                self._verify_klusterlet_connections()

    def _verify_auto_import_cleanup_step(self) -> None:
        """Verify disable-auto-import annotations are cleared from ManagedClusters."""
        with self.state.step("verify_auto_import_cleanup", logger) as should_run:
            if should_run:
                self._verify_disable_auto_import_cleared()

    def _verify_observability_full(self) -> None:
        """
        Execute all observability verification steps.

        Includes scaling up components, restarting API, verifying pods,
        and checking metrics collection.
        """
        with self.state.step("scale_up_observability_components", logger) as should_run:
            if should_run:
                self._scale_up_observability_components()

        with self.state.step("restart_observatorium_api", logger) as should_run:
            if should_run:
                self._restart_observatorium_api()

        with self.state.step("verify_observability_pods", logger) as should_run:
            if should_run:
                self._verify_observability_pods()

        with self.state.step("verify_metrics_collection", logger) as should_run:
            if should_run:
                self._verify_metrics_collection()

    @dry_run_skip(message="Skipping wait for ManagedCluster connections")
    def _verify_managed_clusters_connected(self, timeout: int = CLUSTER_VERIFY_TIMEOUT):
        """Verify all ManagedClusters are connected."""

        latest_status = {
            "available": 0,
            "joined": 0,
            "total": 0,
            "pending": [],
        }

        def _poll_clusters():
            nonlocal latest_status
            managed_clusters = self.secondary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
            )

            if not managed_clusters:
                latest_status = {"available": 0, "joined": 0, "total": 0, "pending": []}
                return False, "no ManagedClusters found"

            total_clusters = 0
            available_clusters = 0
            joined_clusters = 0
            pending_clusters = []

            for mc in managed_clusters:
                mc_name = mc.get("metadata", {}).get("name")

                if mc_name == LOCAL_CLUSTER_NAME:
                    continue

                total_clusters += 1
                conditions = mc.get("status", {}).get("conditions", [])

                is_available = any(
                    c.get("type") == "ManagedClusterConditionAvailable" and c.get("status") == "True"
                    for c in conditions
                )
                is_joined = any(
                    c.get("type") == "ManagedClusterJoined" and c.get("status") == "True" for c in conditions
                )

                if is_available:
                    available_clusters += 1
                if is_joined:
                    joined_clusters += 1
                if not (is_available and is_joined):
                    pending_clusters.append(mc_name or "unknown")

            latest_status = {
                "available": available_clusters,
                "joined": joined_clusters,
                "total": total_clusters,
                "pending": pending_clusters,
            }

            # If there are no non-local ManagedClusters, that's OK - nothing to wait for
            if total_clusters == 0:
                return (
                    True,
                    "No non-local ManagedClusters to verify (only local-cluster exists)",
                )

            is_ready = available_clusters == total_clusters and joined_clusters == total_clusters
            detail = f"available={available_clusters}/{total_clusters}, " f"joined={joined_clusters}/{total_clusters}"
            return is_ready, detail

        success = wait_for_condition(
            "ManagedCluster connections",
            _poll_clusters,
            timeout=timeout,
            interval=CLUSTER_VERIFY_INTERVAL,
            logger=logger,
        )

        if not success:
            raise SwitchoverError(
                "Timeout waiting for ManagedClusters to connect. "
                f"{latest_status['available']}/{latest_status['total']} available, "
                f"{latest_status['joined']}/{latest_status['total']} joined"
            )

    @dry_run_skip(message="Skipping scale-up of observability components")
    def _scale_up_observability_components(self):
        """Scale up observability components if they are scaled down to 0 replicas.

        When promoting a passive hub to active, observatorium-api and thanos-compact
        may be scaled down to 0 replicas. This method checks and scales them up to
        their default replica counts:
        - observatorium-api: 2 replicas (default for HA)
        - thanos-compact: 1 replica (default single replica)

        These defaults are consistent across RHACM versions 2.3 through 2.14.
        """
        logger.info("Checking observability components for scale-up...")

        # Default replica counts
        OBSERVATORIUM_API_REPLICAS = 2  # Default for HA
        THANOS_COMPACT_REPLICAS = 1  # Default single replica

        scaled_components = []

        # Check and scale observatorium-api deployment
        try:
            deployment = self.secondary.get_deployment(
                name=OBSERVATORIUM_API_DEPLOYMENT,
                namespace=OBSERVABILITY_NAMESPACE,
            )

            if deployment is None:
                logger.warning("observatorium-api deployment not found, skipping scale-up")
            else:
                current_replicas = deployment.get("spec", {}).get("replicas", 0)
                if current_replicas == 0:
                    logger.info(
                        "observatorium-api has 0 replicas, scaling up to %d (default for HA)",
                        OBSERVATORIUM_API_REPLICAS,
                    )
                    self.secondary.scale_deployment(
                        name=OBSERVATORIUM_API_DEPLOYMENT,
                        namespace=OBSERVABILITY_NAMESPACE,
                        replicas=OBSERVATORIUM_API_REPLICAS,
                    )
                    scaled_components.append(("observatorium-api", OBSERVATORIUM_API_REPLICAS))
                else:
                    logger.info(
                        "observatorium-api already has %d replica(s), no scale-up needed",
                        current_replicas,
                    )
        except (ApiException, Exception) as e:
            logger.warning("Failed to check/scale observatorium-api: %s", e)
            # Continue with thanos-compact check

        # Check and scale thanos-compact StatefulSet
        try:
            statefulset = self.secondary.get_statefulset(
                name=THANOS_COMPACTOR_STATEFULSET,
                namespace=OBSERVABILITY_NAMESPACE,
            )

            if statefulset is None:
                logger.warning("thanos-compact StatefulSet not found, skipping scale-up")
            else:
                current_replicas = statefulset.get("spec", {}).get("replicas", 0)
                if current_replicas == 0:
                    logger.info(
                        "thanos-compact has 0 replicas, scaling up to %d (default single replica)",
                        THANOS_COMPACT_REPLICAS,
                    )
                    self.secondary.scale_statefulset(
                        name=THANOS_COMPACTOR_STATEFULSET,
                        namespace=OBSERVABILITY_NAMESPACE,
                        replicas=THANOS_COMPACT_REPLICAS,
                    )
                    scaled_components.append(("thanos-compact", THANOS_COMPACT_REPLICAS))
                else:
                    logger.info(
                        "thanos-compact already has %d replica(s), no scale-up needed",
                        current_replicas,
                    )
        except (ApiException, Exception) as e:
            logger.warning("Failed to check/scale thanos-compact: %s", e)
            # Continue to wait for any components that were scaled

        # Wait for scaled components to be ready
        if scaled_components:
            logger.info("Waiting for scaled components to be ready...")

            # Wait for observatorium-api if it was scaled
            if any(name == "observatorium-api" for name, _ in scaled_components):
                logger.info("Waiting for observatorium-api pods to be ready...")
                ready = self.secondary.wait_for_pods_ready(
                    namespace=OBSERVABILITY_NAMESPACE,
                    label_selector="app.kubernetes.io/name=observatorium-api",
                    timeout=OBSERVABILITY_POD_TIMEOUT,
                )
                if ready:
                    logger.info("observatorium-api pods are ready")
                else:
                    logger.warning("observatorium-api pods did not become ready in time")

            # Wait for thanos-compact if it was scaled
            if any(name == "thanos-compact" for name, _ in scaled_components):
                logger.info("Waiting for thanos-compact pods to be ready...")
                ready = self.secondary.wait_for_pods_ready(
                    namespace=OBSERVABILITY_NAMESPACE,
                    label_selector=THANOS_COMPACTOR_LABEL_SELECTOR,
                    timeout=OBSERVABILITY_POD_TIMEOUT,
                )
                if ready:
                    logger.info("thanos-compact pods are ready")
                else:
                    logger.warning("thanos-compact pods did not become ready in time")
        else:
            logger.info("No components needed scale-up")

    def _restart_observatorium_api(self):
        """Restart observatorium-api deployment (ACM 2.12 issue workaround)."""
        logger.info("Restarting observatorium-api deployment...")

        try:
            self.secondary.rollout_restart_deployment(
                name=OBSERVATORIUM_API_DEPLOYMENT,
                namespace=OBSERVABILITY_NAMESPACE,
            )

            logger.info("Triggered observatorium-api restart")

            # Skip pod waiting in dry-run mode since no actual restart happened
            if self.dry_run:
                logger.info("[DRY-RUN] Skipping wait for observatorium-api pods")
                return

            # Wait for pods to be ready
            logger.info("Waiting for observatorium-api pods to be ready...")
            ready = self.secondary.wait_for_pods_ready(
                namespace=OBSERVABILITY_NAMESPACE,
                label_selector="app.kubernetes.io/name=observatorium-api",
                timeout=OBSERVABILITY_POD_TIMEOUT,
            )

            if ready:
                logger.info("observatorium-api pods are ready")
                pods = self.secondary.get_pods(
                    namespace=OBSERVABILITY_NAMESPACE,
                    label_selector="app.kubernetes.io/name=observatorium-api",
                )
                start_times = [
                    pod.get("status", {}).get("startTime") for pod in pods if pod.get("status", {}).get("startTime")
                ]
                if start_times:
                    logger.info(
                        "observatorium-api pod start times: %s",
                        ", ".join(start_times),
                    )
            else:
                logger.warning("observatorium-api pods did not become ready in time")

        except (ApiException, Exception) as e:
            logger.error("Failed to restart observatorium-api: %s", e)
            if "not found" in str(e).lower():
                logger.warning("observatorium-api deployment not found")
            else:
                raise

    def _verify_observability_pods(self):
        """Verify all Observability pods are running and ready."""
        logger.info("Verifying Observability pod health...")

        # Use label selector scoped to ACM observability components.
        # The label app.kubernetes.io/part-of=observability is not consistently applied,
        # while observability.open-cluster-management.io/name=observability is.
        pods = self.secondary.get_pods(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector="observability.open-cluster-management.io/name=observability",
        )

        if not pods:
            logger.warning("No Observability pods found")
            return

        critical_waiting_reasons = {
            "CrashLoopBackOff",
            "ImagePullBackOff",
            "ErrImagePull",
        }
        critical_terminated_reasons = {"Error", "OOMKilled"}

        running_pods = 0
        ready_pods = 0
        error_pods = []

        for pod in pods:
            pod_name = pod.get("metadata", {}).get("name")
            phase = pod.get("status", {}).get("phase", "unknown")
            pod_errors = []

            if phase == "Running":
                running_pods += 1
            elif phase in ("Failed", "Unknown"):
                pod_errors.append(f"phase={phase}")

            # Check ready condition
            conditions = pod.get("status", {}).get("conditions", [])
            for condition in conditions:
                if condition.get("type") == "Ready" and condition.get("status") == "True":
                    ready_pods += 1
                    break

            # Inspect container states for crash loops or repeated failures
            container_statuses = pod.get("status", {}).get("containerStatuses", [])
            for container in container_statuses:
                container_name = container.get("name", "container")
                state = container.get("state") or {}

                waiting_state = state.get("waiting")
                if waiting_state:
                    reason = waiting_state.get("reason", "waiting")
                    if reason in critical_waiting_reasons:
                        pod_errors.append(f"{container_name} waiting ({reason})")

                terminated_state = state.get("terminated")
                if terminated_state:
                    reason = terminated_state.get("reason", "terminated")
                    exit_code = terminated_state.get("exitCode")
                    if reason in critical_terminated_reasons or (exit_code is not None and exit_code != 0):
                        pod_errors.append(f"{container_name} terminated ({reason}, exit={exit_code})")

            if pod_errors:
                error_pods.append(f"{pod_name}: " + "; ".join(pod_errors))

        logger.info(
            "Observability pods: %d/%d running, %d/%d ready",
            running_pods,
            len(pods),
            ready_pods,
            len(pods),
        )

        if error_pods:
            logger.warning("Pods in error state: %s", ", ".join(error_pods))

        if ready_pods < len(pods) * POD_READINESS_TOLERANCE:
            logger.warning(
                "Only %d/%d pods ready. Some pods may still be starting.",
                ready_pods,
                len(pods),
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
        self._log_grafana_route()

        # We could check if metrics-collector pods are running
        metrics_pods = self.secondary.get_pods(
            namespace=OBSERVABILITY_NAMESPACE, label_selector="app=metrics-collector"
        )

        if metrics_pods:
            logger.info("Found %d metrics-collector pod(s)", len(metrics_pods))
        else:
            logger.warning("No metrics-collector pods found")

    def _log_grafana_route(self):
        """Log Grafana route availability to help operators verify UI."""
        try:
            host = self.secondary.get_route_host(OBSERVABILITY_NAMESPACE, "grafana")
            if host:
                logger.info(
                    "Grafana route detected: https://%s (namespace: %s)",
                    host,
                    OBSERVABILITY_NAMESPACE,
                )
            else:
                logger.warning("Grafana route not found in Observability namespace")
        except (ApiException, Exception) as exc:
            logger.warning("Unable to query Grafana route: %s", exc)

    def _verify_disable_auto_import_cleared(self):
        """Ensure disable-auto-import annotations were removed after activation."""

        # Skip verification in dry-run mode since annotations weren't actually cleared
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping disable-auto-import annotation verification")
            return

        logger.info("Ensuring disable-auto-import annotations are cleared...")
        # Force refresh to ensure we have fresh data after klusterlet verification
        # which may have taken time and annotations could have been cleared
        managed_clusters = self._get_managed_clusters(force_refresh=True)

        flagged = []
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")
            if mc_name == LOCAL_CLUSTER_NAME:
                continue

            annotations = mc.get("metadata", {}).get("annotations") or {}
            if DISABLE_AUTO_IMPORT_ANNOTATION in annotations:
                flagged.append(mc_name or "unknown")

        if flagged:
            raise SwitchoverError("disable-auto-import annotation still present on: " + ", ".join(flagged))

        logger.info("All ManagedClusters cleared disable-auto-import annotation")

    @dry_run_skip(message="Skipping klusterlet connection verification")
    def _verify_klusterlet_connections(self):
        """
        Verify and fix klusterlet agents on managed clusters to connect to the new hub.

        When both hubs have the same cluster imported (due to passive sync restoring
        ManagedCluster resources), the klusterlet may remain connected to the old hub.
        This method detects such cases and forces the klusterlet to reconnect by:
        1. Deleting the bootstrap-hub-kubeconfig secret on the managed cluster
        2. Re-applying the import manifest from the new hub
        3. Restarting the klusterlet deployment

        If we can't connect to a managed cluster (no context available), we log a
        warning but don't fail the switchover.
        """

        logger.info("Verifying klusterlet connections to new hub...")

        # Get the new hub's API server URL
        new_hub_server = self._get_hub_api_server()
        if not new_hub_server:
            logger.warning("Could not determine new hub API server URL, skipping klusterlet verification")
            return

        # Load kubeconfig data for context lookup
        # Use max_size=0 to bypass size check for critical klusterlet verification
        kubeconfig_data = self._load_kubeconfig_data(max_size=0)
        if not kubeconfig_data:
            logger.warning("Could not load kubeconfig, skipping klusterlet verification")
            return

        # Get list of managed clusters with their API server URLs
        managed_clusters = self._get_managed_clusters()

        # Build list of (cluster_name, api_url) tuples, excluding local-cluster
        cluster_info = []
        for mc in managed_clusters:
            name = mc.get("metadata", {}).get("name")
            if name and name != LOCAL_CLUSTER_NAME:
                # Get API server URL from ManagedCluster spec
                client_configs = mc.get("spec", {}).get("managedClusterClientConfigs", [])
                api_url = client_configs[0].get("url", "") if client_configs else ""
                cluster_info.append((name, api_url))

        if not cluster_info:
            logger.info("No managed clusters to verify klusterlet connections")
            return

        # Try to verify each cluster's klusterlet connection in parallel
        def check_cluster(cluster_name: str, cluster_api_url: str) -> tuple:
            """Check a single cluster's klusterlet connection. Returns (cluster_name, result, context_name)."""
            try:
                context_name = self._find_context_by_api_url(kubeconfig_data, cluster_api_url, cluster_name)
                if not context_name:
                    return (cluster_name, "no_context", None)

                result = self._check_klusterlet_connection(context_name, cluster_name, new_hub_server)
                return (cluster_name, result, context_name)
            except (ApiException, Exception) as e:
                logger.debug("Error checking klusterlet for %s: %s", cluster_name, e)
                return (cluster_name, "unreachable", None)

        logger.info("Checking klusterlet connections for %d cluster(s) in parallel...", len(cluster_info))

        # Collect results from futures to avoid shared mutable state
        verified = []
        wrong_hub = []
        unreachable = []

        with ThreadPoolExecutor(max_workers=CLUSTER_VERIFY_MAX_WORKERS) as executor:
            futures = [executor.submit(check_cluster, name, api_url) for name, api_url in cluster_info]
            for future in as_completed(futures):
                cluster_name, result, context_name = future.result()
                if result == "verified":
                    verified.append(cluster_name)
                elif result == "wrong_hub":
                    wrong_hub.append((cluster_name, context_name))
                else:  # unreachable, no_context, or error
                    unreachable.append(cluster_name)

        # Log initial results
        if verified:
            logger.info(
                "✓ Klusterlet verified for %d cluster(s): %s",
                len(verified),
                ", ".join(verified),
            )

        def fix_cluster(cluster_name: str, context_name: str) -> tuple:
            """Fix a single cluster's klusterlet connection. Returns (cluster_name, success)."""
            success = self._force_klusterlet_reconnect(cluster_name, context_name)
            return (cluster_name, success)

        # Fix clusters connected to wrong hub (also in parallel)
        if wrong_hub:
            logger.warning(
                "Klusterlet connected to wrong hub for %d cluster(s): %s - attempting to fix...",
                len(wrong_hub),
                ", ".join([c[0] for c in wrong_hub]),
            )

            # Collect results from futures to avoid shared mutable state
            fixed = []
            fix_failed = []

            with ThreadPoolExecutor(max_workers=CLUSTER_VERIFY_MAX_WORKERS) as executor:
                futures = [executor.submit(fix_cluster, name, ctx) for name, ctx in wrong_hub]
                for future in as_completed(futures):
                    cluster_name, success = future.result()
                    if success:
                        fixed.append(cluster_name)
                    else:
                        fix_failed.append(cluster_name)

            if fixed:
                logger.info(
                    "✓ Fixed klusterlet connection for %d cluster(s): %s",
                    len(fixed),
                    ", ".join(fixed),
                )
            if fix_failed:
                logger.warning(
                    "✗ Failed to fix klusterlet for %d cluster(s): %s",
                    len(fix_failed),
                    ", ".join(fix_failed),
                )

        if unreachable:
            logger.info(
                "Klusterlet verification skipped for %d cluster(s) (no context available): %s",
                len(unreachable),
                ", ".join(unreachable),
            )

    def _force_klusterlet_reconnect(self, cluster_name: str, context_name: str) -> bool:
        """
        Force a managed cluster's klusterlet to reconnect to the new hub.

        This is needed when both hubs have the same cluster imported (due to passive
        sync) and the klusterlet is still connected to the old hub. The fix involves:
        1. Getting the import secret from the new hub
        2. Deleting the bootstrap-hub-kubeconfig secret on the managed cluster
        3. Re-applying the import manifest (which recreates the bootstrap secret)
        4. Restarting the klusterlet deployment

        Note: This method intentionally uses raw kubernetes client APIs instead of
        KubeClient because it connects to managed clusters (not the hub) using
        dynamically-loaded kubeconfig contexts. KubeClient is designed for persistent
        hub connections with specific retry/validation behavior that doesn't apply here.

        Args:
            cluster_name: Name of the ManagedCluster
            context_name: Kubeconfig context to use for connecting to the cluster

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Force-reconnecting klusterlet for %s to new hub...", cluster_name)

            # Step 1: Get the import secret from the new hub
            import_yaml = self._get_import_secret(cluster_name)
            if not import_yaml:
                return False

            # Step 2: Connect to the managed cluster and delete the bootstrap secret
            self._delete_bootstrap_secret(context_name, cluster_name)

            # Step 3: Apply the import manifest to recreate the bootstrap secret
            self._apply_import_manifest(context_name, import_yaml, cluster_name)

            # Step 4: Wait for secret to be visible, then restart the klusterlet deployment
            self._wait_for_secret_visibility(context_name, cluster_name)
            self._restart_klusterlet(context_name, cluster_name)

            logger.info("Force-reconnected klusterlet for %s", cluster_name)
            return True

        except (ApiException, Exception) as e:
            logger.warning("Failed to force-reconnect klusterlet for %s: %s", cluster_name, e)
            return False

    def _get_import_secret(self, cluster_name: str) -> Optional[str]:
        """Get and decode the import secret from the new hub.

        Args:
            cluster_name: Name of the ManagedCluster

        Returns:
            Decoded import YAML string, or None if not found
        """
        import_secret = self.secondary.get_secret(
            namespace=cluster_name,
            name=f"{cluster_name}-import",
        )
        if not import_secret:
            logger.warning("No import secret found for %s on new hub", cluster_name)
            return None

        import_yaml_b64 = import_secret.get("data", {}).get("import.yaml", "")
        if not import_yaml_b64:
            logger.warning("Import secret for %s has no import.yaml data", cluster_name)
            return None

        return base64.b64decode(import_yaml_b64).decode("utf-8")

    def _delete_bootstrap_secret(self, context_name: str, cluster_name: str) -> None:
        """Delete the bootstrap-hub-kubeconfig secret on the managed cluster.

        Args:
            context_name: Kubeconfig context to use for connecting to the cluster
            cluster_name: Name of the ManagedCluster
        """
        # Load the correct context before creating API client
        config.load_kube_config(context=context_name)
        v1 = client.CoreV1Api()
        try:
            v1.delete_namespaced_secret(
                name="bootstrap-hub-kubeconfig",
                namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
            )
            logger.debug("Deleted bootstrap-hub-kubeconfig secret on %s", cluster_name)
        except ApiException as e:
            if e.status != 404:
                logger.warning("Failed to delete bootstrap secret on %s: %s", cluster_name, e)

    def _apply_import_manifest(self, context_name: str, import_yaml: str, cluster_name: str) -> None:
        """Apply the import manifest to recreate the bootstrap secret.

        Args:
            context_name: Kubeconfig context to use for connecting to the cluster
            import_yaml: Decoded import YAML string
            cluster_name: Name of the ManagedCluster
        """
        # Load the correct context before creating API clients
        config.load_kube_config(context=context_name)
        v1 = client.CoreV1Api()

        # Parse the import YAML and apply each resource
        import_docs = list(yaml.safe_load_all(import_yaml))

        for doc in import_docs:
            if not doc:
                continue
            kind = doc.get("kind", "")
            name = doc.get("metadata", {}).get("name", "")
            namespace = doc.get("metadata", {}).get("namespace")

            try:
                if kind == "Secret" and name == "bootstrap-hub-kubeconfig":
                    # This is the key secret we need to recreate
                    v1.create_namespaced_secret(
                        namespace=namespace,
                        body=doc,
                    )
                    logger.debug(
                        "Created bootstrap-hub-kubeconfig secret on %s",
                        cluster_name,
                    )
            except ApiException as e:
                if e.status == 409:  # Already exists
                    pass
                else:
                    # Log only status/reason to avoid leaking sensitive data from exception body
                    logger.debug(
                        "Error applying %s/%s: status=%s reason=%s",
                        kind,
                        name,
                        getattr(e, 'status', None),
                        getattr(e, 'reason', None),
                    )

    def _wait_for_secret_visibility(self, context_name: str, cluster_name: str) -> None:
        """Wait for the bootstrap-hub-kubeconfig secret to be visible.

        Args:
            context_name: Kubeconfig context to use for connecting to the cluster
            cluster_name: Name of the ManagedCluster
        """
        # Load the correct context before creating API client
        config.load_kube_config(context=context_name)
        v1 = client.CoreV1Api()

        def secret_exists() -> tuple:
            """Check if bootstrap-hub-kubeconfig secret exists."""
            try:
                v1.read_namespaced_secret(
                    name="bootstrap-hub-kubeconfig",
                    namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
                )
                return (True, "secret exists")
            except ApiException as e:
                if e.status == 404:
                    return (False, "secret not found")
                return (False, f"error: {e.status}")

        secret_ready = wait_for_condition(
            description=f"bootstrap-hub-kubeconfig secret on {cluster_name}",
            condition_fn=secret_exists,
            timeout=SECRET_VISIBILITY_TIMEOUT,
            interval=SECRET_VISIBILITY_INTERVAL,
            logger=logger,
        )

        if not secret_ready:
            logger.warning("bootstrap-hub-kubeconfig secret not visible on %s after timeout", cluster_name)

    def _restart_klusterlet(self, context_name: str, cluster_name: str) -> None:
        """Restart the klusterlet deployment on the managed cluster.

        Args:
            context_name: Kubeconfig context to use for connecting to the cluster
            cluster_name: Name of the ManagedCluster
        """
        import time as time_module

        # Load the correct context before creating API client
        config.load_kube_config(context=context_name)
        apps_v1 = client.AppsV1Api()
        try:
            # Trigger a rollout restart by patching the deployment
            patch = {
                "spec": {
                    "template": {
                        "metadata": {"annotations": {"acm-switchover/restart": str(int(time_module.time()))}}
                    }
                }
            }
            apps_v1.patch_namespaced_deployment(
                name="klusterlet",
                namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
                body=patch,
            )
            logger.debug("Triggered klusterlet restart on %s", cluster_name)
        except ApiException as e:
            logger.warning("Failed to restart klusterlet on %s: %s", cluster_name, e)

    def _get_hub_api_server(self) -> str:
        """Get the API server URL for the new hub.

        Note: Uses max_size=0 to bypass kubeconfig size limits since this is a
        critical operation for klusterlet verification. Large kubeconfigs in
        multi-context environments must still be readable for hub lookup.
        """
        try:
            # Bypass size check (max_size=0) for critical hub lookup
            kubeconfig_data = self._load_kubeconfig_data(max_size=0)
            if not kubeconfig_data:
                return ""

            # Find the context matching secondary
            for ctx in kubeconfig_data.get("contexts", []):
                if ctx.get("name") == self.secondary.context:
                    cluster_name = ctx.get("context", {}).get("cluster")
                    # Find the cluster with matching name
                    for cluster in kubeconfig_data.get("clusters", []):
                        if cluster.get("name") == cluster_name:
                            return cluster.get("cluster", {}).get("server", "")
        except (ApiException, Exception) as e:
            logger.debug("Error getting hub API server: %s", e)

        return ""

    def _load_kubeconfig_data(self, max_size: Optional[int] = None, force_reload: bool = False) -> dict:
        """Load and merge kubeconfig data from all KUBECONFIG paths.

        Handles the KUBECONFIG environment variable which can contain multiple
        colon-separated paths (e.g., '/path/one:/path/two:~/.kube/config').
        Contexts, clusters, and users are merged from all files.

        Uses caching to avoid repeated file I/O when called multiple times.
        Cache is invalidated if any kubeconfig file has been modified.

        Args:
            max_size: Maximum file size in bytes. If None, uses MAX_KUBECONFIG_SIZE.
                     If 0 or negative, bypasses size check entirely (for critical operations).
            force_reload: If True, bypass cache and reload from files.

        Returns:
            Merged kubeconfig data dict with contexts, clusters, and users.
        """
        try:
            # Check if we can use cached data
            if not force_reload and self._kubeconfig_cache is not None:
                # Verify no files have been modified since caching
                cache_valid = True
                for path in self._kubeconfig_paths:
                    if os.path.exists(path):
                        current_mtime = os.path.getmtime(path)
                        if self._kubeconfig_mtime.get(path, 0) != current_mtime:
                            cache_valid = False
                            break
                    else:
                        # File was deleted
                        cache_valid = False
                        break

                if cache_valid:
                    return self._kubeconfig_cache
            kubeconfig_env = os.environ.get("KUBECONFIG", "")
            if kubeconfig_env:
                # Split on colon (Unix path separator for KUBECONFIG)
                paths = [p.strip() for p in kubeconfig_env.split(":") if p.strip()]
            else:
                paths = [os.path.expanduser("~/.kube/config")]

            # Determine size limit: use provided max_size, or default to MAX_KUBECONFIG_SIZE
            if max_size is None:
                # Check if MAX_KUBECONFIG_SIZE is set to 0 or negative to disable checking
                if MAX_KUBECONFIG_SIZE <= 0:
                    # Size checking disabled via environment variable
                    size_limit = None
                    check_size = False
                else:
                    size_limit = MAX_KUBECONFIG_SIZE
                    check_size = True
            elif max_size <= 0:
                # Bypass size check for critical operations
                size_limit = None
                check_size = False
            else:
                size_limit = max_size
                check_size = True

            # Merge kubeconfig data from all paths
            merged: dict = {"contexts": [], "clusters": [], "users": []}

            for path in paths:
                expanded_path = os.path.expanduser(path)
                if not os.path.exists(expanded_path):
                    logger.debug("Kubeconfig path does not exist: %s", os.path.basename(expanded_path))
                    continue

                try:
                    # Check file size before loading to prevent memory exhaustion
                    if check_size:
                        kubeconfig_size = os.path.getsize(expanded_path)
                        if kubeconfig_size > size_limit:
                            logger.warning(
                                "Kubeconfig file too large: %s (%d bytes, max %d bytes). Skipping.",
                                os.path.basename(expanded_path),
                                kubeconfig_size,
                                size_limit,
                            )
                            continue
                    else:
                        # Size check bypassed - log warning but continue loading
                        # Only warn if size checking is meaningful (MAX_KUBECONFIG_SIZE > 0)
                        # and file exceeds the default limit (not the overridden value)
                        if MAX_KUBECONFIG_SIZE > 0:
                            kubeconfig_size = os.path.getsize(expanded_path)
                            if kubeconfig_size > DEFAULT_KUBECONFIG_SIZE:
                                logger.warning(
                                    "Kubeconfig file large: %s (%d bytes, exceeds default limit %d bytes). "
                                    "Loading anyway for critical operation.",
                                    os.path.basename(expanded_path),
                                    kubeconfig_size,
                                    DEFAULT_KUBECONFIG_SIZE,
                                )

                    with open(expanded_path) as f:
                        data = yaml.safe_load(f) or {}
                        merged["contexts"].extend(data.get("contexts", []))
                        merged["clusters"].extend(data.get("clusters", []))
                        merged["users"].extend(data.get("users", []))
                except (OSError, yaml.YAMLError) as e:
                    logger.debug("Error loading kubeconfig %s: %s", os.path.basename(expanded_path), e)
                    continue

            # Update cache with loaded data and file modification times
            self._kubeconfig_cache = merged
            self._kubeconfig_paths = [os.path.expanduser(p) for p in paths]
            self._kubeconfig_mtime = {
                path: os.path.getmtime(path)
                for path in self._kubeconfig_paths
                if os.path.exists(path)
            }

            return merged

        except Exception as e:
            logger.debug("Error loading kubeconfig: %s", e)
            return {}

    def _find_context_by_api_url(self, kubeconfig_data: dict, api_url: str, cluster_name: str) -> str:
        """
        Find a kubeconfig context that matches the given API server URL.

        This is smarter than name-based matching because context names often
        differ from ManagedCluster names (e.g., "admin@prod1" vs "prod1").

        Args:
            kubeconfig_data: Parsed kubeconfig as dict
            api_url: The API server URL from ManagedCluster spec
            cluster_name: The ManagedCluster name (for fallback and logging)

        Returns:
            Context name if found, empty string otherwise
        """
        if not api_url:
            # Fallback to name-based matching if no API URL
            logger.debug("No API URL for %s, trying name-based matching", cluster_name)
            try:
                contexts, _ = config.list_kube_config_contexts()
                for ctx in contexts:
                    if ctx.get("name") == cluster_name:
                        return cluster_name
            except (config.ConfigException, Exception) as e:
                # Failed to match context by name; returning empty string as fallback
                logger.debug("Exception during name-based context matching for %s: %s", cluster_name, e)
            return ""

        # Normalize the API URL for comparison (extract host)
        api_host = re.sub(r"https://([^:/]+).*", r"\1", api_url)

        # Build a map of cluster server URL -> cluster name
        cluster_servers = {}
        for cluster in kubeconfig_data.get("clusters", []):
            server = cluster.get("cluster", {}).get("server", "")
            if server:
                server_host = re.sub(r"https://([^:/]+).*", r"\1", server)
                cluster_servers[server_host] = cluster.get("name")

        # Find which kubeconfig cluster matches this API URL
        matching_cluster = cluster_servers.get(api_host)
        if not matching_cluster:
            logger.debug("No kubeconfig cluster matches API URL %s", api_url)
            return ""

        # Find the context that uses this cluster
        for ctx in kubeconfig_data.get("contexts", []):
            if ctx.get("context", {}).get("cluster") == matching_cluster:
                context_name = ctx.get("name", "")
                logger.debug(
                    "Matched cluster %s to context %s via API URL %s",
                    cluster_name,
                    context_name,
                    api_url,
                )
                return context_name

        return ""

    def _check_klusterlet_connection(self, context_name: str, cluster_name: str, expected_hub: str) -> str:
        """
        Check if a managed cluster's klusterlet is connected to the expected hub.

        Note: This method intentionally uses raw kubernetes client APIs instead of
        KubeClient because it connects to managed clusters (not the hub) using
        dynamically-loaded kubeconfig contexts.

        Args:
            context_name: Kubeconfig context name to use for connecting
            cluster_name: ManagedCluster name (for logging)
            expected_hub: Expected hub API server URL

        Returns:
            "verified" if connected to correct hub
            "wrong_hub" if connected to different hub
            "unreachable" if can't connect to cluster
        """
        try:
            # Try to load kubeconfig using the discovered context name
            config.load_kube_config(context=context_name)
            v1 = client.CoreV1Api()

            # Get the hub-kubeconfig-secret
            try:
                secret = v1.read_namespaced_secret(
                    name="hub-kubeconfig-secret",
                    namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
                )
            except ApiException as e:
                if e.status == 404:
                    # Try bootstrap secret as fallback
                    secret = v1.read_namespaced_secret(
                        name="bootstrap-hub-kubeconfig",
                        namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
                    )
                else:
                    raise

            # Decode and parse kubeconfig from secret
            secret_kubeconfig = (secret.data or {}).get("kubeconfig", "")
            if not secret_kubeconfig:
                return "unreachable"

            kubeconfig_yaml = base64.b64decode(secret_kubeconfig).decode("utf-8")

            # Extract server URL from kubeconfig using proper YAML parsing
            try:
                kubeconfig_data = yaml.safe_load(kubeconfig_yaml)
                if not kubeconfig_data:
                    return "unreachable"
                # Server URL is in clusters[0].cluster.server
                clusters = kubeconfig_data.get("clusters", [])
                if not clusters:
                    return "unreachable"
                # Verify clusters[0] is a dict before accessing it
                if not isinstance(clusters[0], dict):
                    return "unreachable"
                cluster_info = clusters[0].get("cluster", {})
                # Verify cluster_info is a dict before accessing server
                if not isinstance(cluster_info, dict):
                    return "unreachable"
                klusterlet_hub = cluster_info.get("server", "")
                if not klusterlet_hub:
                    return "unreachable"
            except (yaml.YAMLError, AttributeError, TypeError, IndexError):
                return "unreachable"

            # Compare hostnames (ignore port differences)
            expected_host = re.sub(r"https://([^:/]+).*", r"\1", expected_hub)
            klusterlet_host = re.sub(r"https://([^:/]+).*", r"\1", klusterlet_hub)

            if expected_host == klusterlet_host:
                logger.debug("Cluster %s klusterlet verified (API server endpoint matched expected)", cluster_name)
                return "verified"
            else:
                logger.debug(
                    "Cluster %s klusterlet not verified (API server endpoint did not match expected)", cluster_name
                )
                return "wrong_hub"

        except config.ConfigException:
            # Context doesn't exist
            return "unreachable"
        except Exception as e:
            logger.debug("Error checking klusterlet for %s: %s", cluster_name, e)
            return "unreachable"
