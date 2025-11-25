"""
Kubernetes client wrapper for ACM resources.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception,
    before_sleep_log,
)
from urllib3.exceptions import HTTPError

logger = logging.getLogger("acm_switchover")


def is_retryable_error(exception: Exception) -> bool:
    """Check if exception is retryable."""
    if isinstance(exception, ApiException):
        # Retry on server errors (5xx) and too many requests (429)
        return 500 <= exception.status < 600 or exception.status == 429
    if isinstance(exception, HTTPError):
        return True
    return False


# Standard retry decorator for API calls
retry_api_call = retry(
    retry=retry_if_exception(is_retryable_error),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.DEBUG),
    reraise=True,
)


class KubeClient:
    """Wrapper for Kubernetes API client with ACM-specific helpers."""

    def __init__(
        self,
        context: Optional[str] = None,
        dry_run: bool = False,
        request_timeout: int = 30,
        disable_hostname_verification: bool = False,
    ) -> None:
        """
        Initialize Kubernetes client for specific context.

        Args:
            context: Kubernetes context name
            dry_run: If True, don't make actual changes
            request_timeout: API request timeout in seconds
            disable_hostname_verification: If True, skip TLS hostname verification (not recommended)
        """
        self.context = context
        self.dry_run = dry_run
        self.disable_hostname_verification = disable_hostname_verification

        # Load config for specific context
        config.load_kube_config(context=context)

        # Configure default timeouts
        configuration = client.Configuration.get_default_copy()
        configuration.retries = 3

        if disable_hostname_verification and hasattr(configuration, "assert_hostname"):
            configuration.assert_hostname = False
            logger.warning(
                "Hostname verification disabled for context: %s",
                context or "default",
            )

        client.Configuration.set_default(configuration)

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.custom_api = client.CustomObjectsApi()
        
        # Set timeout on API clients
        self.core_v1.api_client.configuration.timeout = request_timeout
        self.apps_v1.api_client.configuration.timeout = request_timeout
        self.custom_api.api_client.configuration.timeout = request_timeout

        logger.info(
            f"Initialized Kubernetes client for context: {context or 'default'} (timeout: {request_timeout}s)"
        )

    @retry_api_call
    def get_namespace(self, name: str) -> Optional[Dict]:
        """Check if namespace exists.

        Args:
            name: Namespace name

        Returns:
            Namespace dict or None if not found
        """
        try:
            ns = self.core_v1.read_namespace(name)
            return ns.to_dict()
        except ApiException as e:
            if e.status == 404:
                return None
            # Re-raise retryable errors for tenacity to catch
            if is_retryable_error(e):
                raise
            logger.error(f"Failed to get namespace {name}: {e}")
            raise

    def namespace_exists(self, name: str) -> bool:
        """Check if namespace exists."""
        return self.get_namespace(name) is not None

    @retry_api_call
    def secret_exists(self, namespace: str, name: str) -> bool:
        """Check if a secret exists."""
        try:
            self.core_v1.read_namespaced_secret(name=name, namespace=namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            if is_retryable_error(e):
                raise
            logger.error("Failed to read secret %s/%s: %s", namespace, name, e)
            raise

    @retry_api_call
    def get_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        namespace: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Get a custom resource.

        Args:
            group: API group (e.g., 'cluster.open-cluster-management.io')
            version: API version (e.g., 'v1')
            plural: Resource plural (e.g., 'managedclusters')
            name: Resource name
            namespace: Namespace (None for cluster-scoped)

        Returns:
            Resource dict or None if not found
        """
        try:
            if namespace:
                resource = self.custom_api.get_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                )
            else:
                resource = self.custom_api.get_cluster_custom_object(
                    group=group, version=version, plural=plural, name=name
                )
            return resource
        except ApiException as e:
            if e.status == 404:
                return None
            if is_retryable_error(e):
                raise
            raise

    @retry_api_call
    def list_custom_resources(
        self,
        group: str,
        version: str,
        plural: str,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
    ) -> List[Dict]:
        """
        List custom resources.

        Args:
            group: API group
            version: API version
            plural: Resource plural
            namespace: Namespace (None for cluster-scoped)
            label_selector: Label selector filter

        Returns:
            List of resource dicts
        """
        items: List[Dict] = []
        continue_token: Optional[str] = None

        while True:
            try:
                if namespace:
                    result = self.custom_api.list_namespaced_custom_object(
                        group=group,
                        version=version,
                        namespace=namespace,
                        plural=plural,
                        label_selector=label_selector,
                        _continue=continue_token,
                    )
                else:
                    result = self.custom_api.list_cluster_custom_object(
                        group=group,
                        version=version,
                        plural=plural,
                        label_selector=label_selector,
                        _continue=continue_token,
                    )
            except ApiException as e:
                if e.status == 404:
                    return []
                if is_retryable_error(e):
                    raise
                raise

            items.extend(result.get("items", []))

            metadata = result.get("metadata") or {}
            continue_token = metadata.get("continue")

            if not continue_token:
                break

        return items

    @retry_api_call
    def patch_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        patch: Dict[str, Any],
        namespace: Optional[str] = None,
    ) -> Dict:
        """
        Patch a custom resource.

        Args:
            group: API group
            version: API version
            plural: Resource plural
            name: Resource name
            patch: Patch dict
            namespace: Namespace (None for cluster-scoped)

        Returns:
            Patched resource dict
        """
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would patch {plural}/{name} with: {patch}")
            return {}

        try:
            if namespace:
                result = self.custom_api.patch_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                    body=patch,
                )
            else:
                result = self.custom_api.patch_cluster_custom_object(
                    group=group, version=version, plural=plural, name=name, body=patch
                )
            return result
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error(f"Failed to patch {plural}/{name}: {e}")
            raise

    @retry_api_call
    def create_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        body: Dict[str, Any],
        namespace: Optional[str] = None,
    ) -> Dict:
        """Create a custom resource."""
        if self.dry_run:
            logger.info(
                f"[DRY-RUN] Would create {plural}: {body.get('metadata', {}).get('name')}"
            )
            return body

        try:
            if namespace:
                result = self.custom_api.create_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural,
                    body=body,
                )
            else:
                result = self.custom_api.create_cluster_custom_object(
                    group=group, version=version, plural=plural, body=body
                )
            return result
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error(f"Failed to create {plural}: {e}")
            raise

    @retry_api_call
    def delete_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        namespace: Optional[str] = None,
    ) -> bool:
        """Delete a custom resource."""
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would delete {plural}/{name}")
            return True

        try:
            if namespace:
                self.custom_api.delete_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                )
            else:
                self.custom_api.delete_cluster_custom_object(
                    group=group, version=version, plural=plural, name=name
                )
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            if is_retryable_error(e):
                raise
            logger.error(f"Failed to delete {plural}/{name}: {e}")
            raise

    def list_managed_clusters(self) -> List[Dict]:
        """List all ManagedCluster resources."""
        return self.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
        )

    def patch_managed_cluster(self, name: str, patch: Dict[str, Any]) -> Dict:
        """Patch a ManagedCluster resource."""
        return self.patch_custom_resource(
            group="cluster.open-cluster-management.io",
            version="v1",
            plural="managedclusters",
            name=name,
            patch=patch,
        )

    @retry_api_call
    def scale_deployment(self, name: str, namespace: str, replicas: int) -> Dict:
        """Scale a deployment."""
        if self.dry_run:
            logger.info(
                f"[DRY-RUN] Would scale deployment {namespace}/{name} to {replicas} replicas"
            )
            return {}

        try:
            body = {"spec": {"replicas": replicas}}
            result = self.apps_v1.patch_namespaced_deployment_scale(
                name=name, namespace=namespace, body=body
            )
            return result.to_dict()
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error(f"Failed to scale deployment {namespace}/{name}: {e}")
            raise

    @retry_api_call
    def scale_statefulset(self, name: str, namespace: str, replicas: int) -> Dict:
        """Scale a statefulset."""
        if self.dry_run:
            logger.info(
                f"[DRY-RUN] Would scale statefulset {namespace}/{name} to {replicas} replicas"
            )
            return {}

        try:
            body = {"spec": {"replicas": replicas}}
            result = self.apps_v1.patch_namespaced_stateful_set_scale(
                name=name, namespace=namespace, body=body
            )
            return result.to_dict()
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error(f"Failed to scale statefulset {namespace}/{name}: {e}")
            raise

    @retry_api_call
    def rollout_restart_deployment(self, name: str, namespace: str) -> Dict:
        """Restart a deployment by updating restart annotation."""
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would restart deployment {namespace}/{name}")
            return {}

        try:
            now = time.strftime("%Y%m%d%H%M%S")
            body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {"kubectl.kubernetes.io/restartedAt": now}
                        }
                    }
                }
            }
            result = self.apps_v1.patch_namespaced_deployment(
                name=name, namespace=namespace, body=body
            )
            return result.to_dict()
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error(f"Failed to restart deployment {namespace}/{name}: {e}")
            raise

    @retry_api_call
    def get_pods(
        self, namespace: str, label_selector: Optional[str] = None
    ) -> List[Dict]:
        """List pods in a namespace."""
        try:
            result = self.core_v1.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector
            )
            return [pod.to_dict() for pod in result.items]
        except ApiException as e:
            if e.status == 404:
                return []
            if is_retryable_error(e):
                raise
            raise

    def list_pods(
        self,
        namespace: str,
        label_selector: Optional[str] = None,
    ) -> List[Dict]:
        """Backward compatible alias for get_pods."""
        return self.get_pods(namespace, label_selector)

    def wait_for_pods_ready(
        self,
        namespace: str,
        label_selector: str,
        timeout: int = 300,
        expected_count: Optional[int] = None,
    ) -> bool:
        """
        Wait for pods to be ready.

        Args:
            namespace: Namespace
            label_selector: Label selector
            timeout: Timeout in seconds
            expected_count: Expected number of ready pods

        Returns:
            True if pods are ready within timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            pods = self.get_pods(namespace, label_selector)

            if expected_count is not None and len(pods) < expected_count:
                logger.debug(
                    "Waiting for %s pods, found %s", expected_count, len(pods)
                )
                time.sleep(5)
                continue

            ready_count = 0
            for pod in pods:
                conditions = pod.get("status", {}).get("conditions", [])
                for condition in conditions:
                    if (
                        condition.get("type") == "Ready"
                        and condition.get("status") == "True"
                    ):
                        ready_count += 1
                        break

            if expected_count is None:
                if ready_count == len(pods) and len(pods) > 0:
                    logger.info(f"All {ready_count} pods ready in {namespace}")
                    return True
            else:
                if ready_count >= expected_count:
                    logger.info(
                        "Got %s/%s ready pods in %s (total pods: %s)",
                        ready_count,
                        expected_count,
                        namespace,
                        len(pods),
                    )
                    return True

            logger.debug(f"{ready_count}/{len(pods)} pods ready in {namespace}")
            time.sleep(5)

        logger.error(f"Timeout waiting for pods in {namespace}")
        return False
