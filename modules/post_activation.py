"""
Post-activation verification module for ACM switchover.
"""

import base64
import logging
import re
from typing import List, Tuple

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from lib.constants import (
    CLUSTER_VERIFY_INTERVAL,
    CLUSTER_VERIFY_TIMEOUT,
    OBSERVABILITY_NAMESPACE,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import StateManager
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

            if not self.state.is_step_completed("verify_auto_import_cleanup"):
                self._verify_disable_auto_import_cleared()
                self.state.mark_step_completed("verify_auto_import_cleanup")
            else:
                logger.info("Step already completed: verify_auto_import_cleanup")

            # Optional: Verify klusterlet connections (non-blocking)
            if not self.state.is_step_completed("verify_klusterlet_connections"):
                self._verify_klusterlet_connections()
                self.state.mark_step_completed("verify_klusterlet_connections")
            else:
                logger.info("Step already completed: verify_klusterlet_connections")

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

        except SwitchoverError as e:
            logger.error("Post-activation verification failed: %s", e)
            self.state.add_error(str(e), "post_activation_verification")
            return False
        except Exception as e:
            logger.error("Unexpected error during post-activation verification: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", "post_activation_verification")
            return False

    def _verify_managed_clusters_connected(self, timeout: int = CLUSTER_VERIFY_TIMEOUT):
        """Verify all ManagedClusters are connected."""

        # Skip waiting in dry-run mode since no actual activation occurred
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping wait for ManagedCluster connections")
            return

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

                if mc_name == "local-cluster":
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
                return True, "No non-local ManagedClusters to verify (only local-cluster exists)"

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
            raise Exception(
                "Timeout waiting for ManagedClusters to connect. "
                f"{latest_status['available']}/{latest_status['total']} available, "
                f"{latest_status['joined']}/{latest_status['total']} joined"
            )

    def _restart_observatorium_api(self):
        """Restart observatorium-api deployment (ACM 2.12 issue workaround)."""
        logger.info("Restarting observatorium-api deployment...")

        try:
            self.secondary.rollout_restart_deployment(
                name="observability-observatorium-api",
                namespace="open-cluster-management-observability",
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
                timeout=300,
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

        except Exception as e:
            logger.error("Failed to restart observatorium-api: %s", e)
            if "not found" in str(e).lower():
                logger.warning("observatorium-api deployment not found")
            else:
                raise

    def _verify_observability_pods(self):
        """Verify all Observability pods are running and ready."""
        logger.info("Verifying Observability pod health...")

        pods = self.secondary.get_pods(namespace=OBSERVABILITY_NAMESPACE)

        if not pods:
            logger.warning("No Observability pods found")
            return

        critical_waiting_reasons = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"}
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
            running_pods, len(pods), ready_pods, len(pods)
        )

        if error_pods:
            logger.warning("Pods in error state: %s", ", ".join(error_pods))

        if ready_pods < len(pods) * 0.8:  # Allow 20% tolerance
            logger.warning(
                "Only %d/%d pods ready. Some pods may still be starting.",
                ready_pods, len(pods)
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
        except Exception as exc:
            logger.warning("Unable to query Grafana route: %s", exc)

    def _verify_disable_auto_import_cleared(self):
        """Ensure disable-auto-import annotations were removed after activation."""

        # Skip verification in dry-run mode since annotations weren't actually cleared
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping disable-auto-import annotation verification")
            return

        logger.info("Ensuring disable-auto-import annotations are cleared...")
        managed_clusters = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
        )

        flagged = []
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")
            if mc_name == "local-cluster":
                continue

            annotations = mc.get("metadata", {}).get("annotations") or {}
            if "import.open-cluster-management.io/disable-auto-import" in annotations:
                flagged.append(mc_name or "unknown")

        if flagged:
            raise Exception("disable-auto-import annotation still present on: " + ", ".join(flagged))

        logger.info("All ManagedClusters cleared disable-auto-import annotation")

    def _verify_klusterlet_connections(self):
        """
        Verify klusterlet agents on managed clusters are connected to the new hub.

        This is a non-blocking verification that attempts to connect to each managed
        cluster and verify its klusterlet is pointing to the new hub. If we can't
        connect to a managed cluster (no context available), we log a warning but
        don't fail the switchover.
        """
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping klusterlet connection verification")
            return

        logger.info("Verifying klusterlet connections to new hub...")

        # Get the new hub's API server URL
        new_hub_server = self._get_hub_api_server()
        if not new_hub_server:
            logger.warning("Could not determine new hub API server URL, skipping klusterlet verification")
            return

        # Get list of managed cluster names
        managed_clusters = self.secondary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
        )

        cluster_names = [
            mc.get("metadata", {}).get("name")
            for mc in managed_clusters
            if mc.get("metadata", {}).get("name") != "local-cluster"
        ]

        if not cluster_names:
            logger.info("No managed clusters to verify klusterlet connections")
            return

        # Try to verify each cluster's klusterlet connection
        verified = []
        failed = []
        unreachable = []

        for cluster_name in cluster_names:
            try:
                result = self._check_klusterlet_connection(cluster_name, new_hub_server)
                if result == "verified":
                    verified.append(cluster_name)
                elif result == "wrong_hub":
                    failed.append(cluster_name)
                else:  # unreachable or error
                    unreachable.append(cluster_name)
            except Exception as e:
                logger.debug("Error checking klusterlet for %s: %s", cluster_name, e)
                unreachable.append(cluster_name)

        # Log results
        if verified:
            logger.info(
                "✓ Klusterlet verified for %d cluster(s): %s",
                len(verified),
                ", ".join(verified),
            )

        if failed:
            logger.warning(
                "✗ Klusterlet NOT connected to new hub for %d cluster(s): %s",
                len(failed),
                ", ".join(failed),
            )

        if unreachable:
            logger.info(
                "Klusterlet verification skipped for %d cluster(s) (no context available): %s",
                len(unreachable),
                ", ".join(unreachable),
            )

    def _get_hub_api_server(self) -> str:
        """Get the API server URL for the new hub."""
        try:
            # Load the kubeconfig for the secondary (new hub) context
            contexts, _ = config.list_kube_config_contexts()
            for ctx in contexts:
                if ctx["name"] == self.secondary.context:
                    cluster_name = ctx["context"]["cluster"]
                    # Read the kubeconfig file to get cluster server
                    import os

                    kubeconfig_path = os.environ.get("KUBECONFIG", os.path.expanduser("~/.kube/config"))
                    with open(kubeconfig_path) as f:
                        import yaml

                        kc = yaml.safe_load(f)
                        for cluster in kc.get("clusters", []):
                            if cluster.get("name") == cluster_name:
                                return cluster.get("cluster", {}).get("server", "")
        except Exception as e:
            logger.debug("Error getting hub API server: %s", e)

        return ""

    def _check_klusterlet_connection(self, cluster_name: str, expected_hub: str) -> str:
        """
        Check if a managed cluster's klusterlet is connected to the expected hub.

        Args:
            cluster_name: Name of the managed cluster (used as context name)
            expected_hub: Expected hub API server URL

        Returns:
            "verified" if connected to correct hub
            "wrong_hub" if connected to different hub
            "unreachable" if can't connect to cluster
        """
        try:
            # Try to load kubeconfig for this cluster
            config.load_kube_config(context=cluster_name)
            v1 = client.CoreV1Api()

            # Get the hub-kubeconfig-secret
            try:
                secret = v1.read_namespaced_secret(
                    name="hub-kubeconfig-secret",
                    namespace="open-cluster-management-agent",
                )
            except ApiException as e:
                if e.status == 404:
                    # Try bootstrap secret as fallback
                    secret = v1.read_namespaced_secret(
                        name="bootstrap-hub-kubeconfig",
                        namespace="open-cluster-management-agent",
                    )
                else:
                    raise

            # Decode and parse kubeconfig from secret
            kubeconfig_data = secret.data.get("kubeconfig", "")
            if not kubeconfig_data:
                return "unreachable"

            kubeconfig_yaml = base64.b64decode(kubeconfig_data).decode("utf-8")

            # Extract server URL from kubeconfig
            server_match = re.search(r"server:\s*(https://[^\s]+)", kubeconfig_yaml)
            if not server_match:
                return "unreachable"

            klusterlet_hub = server_match.group(1)

            # Compare hostnames (ignore port differences)
            expected_host = re.sub(r"https://([^:/]+).*", r"\1", expected_hub)
            klusterlet_host = re.sub(r"https://([^:/]+).*", r"\1", klusterlet_hub)

            if expected_host == klusterlet_host:
                logger.debug("Cluster %s klusterlet → %s (correct)", cluster_name, klusterlet_hub)
                return "verified"
            else:
                logger.debug(
                    "Cluster %s klusterlet → %s (expected %s)",
                    cluster_name,
                    klusterlet_hub,
                    expected_hub,
                )
                return "wrong_hub"

        except config.ConfigException:
            # Context doesn't exist
            return "unreachable"
        except Exception as e:
            logger.debug("Error checking klusterlet for %s: %s", cluster_name, e)
            return "unreachable"
