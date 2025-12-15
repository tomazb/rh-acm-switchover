"""
Kubernetes client wrapper for ACM resources.

This module includes comprehensive input validation for Kubernetes resource
names, namespaces, and other parameters to improve security and reliability.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from urllib3.exceptions import HTTPError

from lib.validation import InputValidator, ValidationError

logger = logging.getLogger("acm_switchover")


def is_retryable_error(exception: BaseException) -> bool:
    """Check if exception is retryable."""
    if isinstance(exception, ApiException):
        # Retry on server errors (5xx) and too many requests (429)
        return 500 <= exception.status < 600 or exception.status == 429
    if isinstance(exception, HTTPError):
        return True
    return False


def _should_retry(exception: BaseException) -> bool:
    """Custom retry condition using is_retryable_error."""
    if not isinstance(exception, Exception):
        return False
    return is_retryable_error(exception)


# Standard retry decorator for API calls
retry_api_call = retry(
    retry=retry_if_exception(_should_retry),
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

        # Create per-instance configuration to avoid affecting other clients
        configuration = client.Configuration.get_default_copy()
        configuration.retries = 3

        if disable_hostname_verification and hasattr(configuration, "assert_hostname"):
            configuration.assert_hostname = False
            logger.warning(
                "Hostname verification disabled for context: %s",
                context or "default",
            )

        # Create API clients with this specific configuration
        api_client = client.ApiClient(configuration)
        self.core_v1 = client.CoreV1Api(api_client)
        self.apps_v1 = client.AppsV1Api(api_client)
        self.custom_api = client.CustomObjectsApi(api_client)

        # Set timeout on API clients
        self.core_v1.api_client.configuration.timeout = request_timeout
        self.apps_v1.api_client.configuration.timeout = request_timeout
        self.custom_api.api_client.configuration.timeout = request_timeout

        logger.info(
            "Initialized Kubernetes client for context: %s (timeout: %ss)",
            context or "default",
            request_timeout,
        )

    @retry_api_call
    def get_namespace(self, name: str) -> Optional[Dict]:
        """Check if namespace exists.

        Args:
            name: Namespace name

        Returns:
            Namespace dict or None if not found

        Raises:
            ValidationError: If namespace name is invalid
        """
        try:
            # Validate namespace name before making API call
            InputValidator.validate_kubernetes_namespace(name)

            ns = self.core_v1.read_namespace(name)
            return ns.to_dict()
        except ApiException as e:
            if e.status == 404:
                return None
            # Re-raise retryable errors for tenacity to catch
            if is_retryable_error(e):
                raise
            logger.error("Failed to get namespace %s: %s", name, e)
            raise

    def namespace_exists(self, name: str) -> bool:
        """Check if namespace exists.

        Args:
            name: Namespace name

        Returns:
            True if namespace exists

        Raises:
            ValidationError: If namespace name is invalid
        """
        return self.get_namespace(name) is not None

    @retry_api_call
    def get_secret(self, namespace: str, name: str) -> Optional[Dict]:
        """Get a secret by name.

        Args:
            namespace: Namespace name
            name: Secret name

        Returns:
            Secret dict or None if not found

        Raises:
            ValidationError: If namespace or secret name is invalid
        """
        try:
            # Validate inputs before making API call
            InputValidator.validate_kubernetes_namespace(namespace)
            InputValidator.validate_kubernetes_name(name, "secret")

            secret = self.core_v1.read_namespaced_secret(name=name, namespace=namespace)
            return secret.to_dict()
        except ApiException as e:
            if e.status == 404:
                return None
            if is_retryable_error(e):
                raise
            logger.error("Failed to get secret %s/%s: status=%s reason=%s", namespace, name, e.status, e.reason)
            raise

    def secret_exists(self, namespace: str, name: str) -> bool:
        """Check if a secret exists.

        Args:
            namespace: Namespace name
            name: Secret name

        Returns:
            True if secret exists

        Raises:
            ValidationError: If namespace or secret name is invalid
        """
        return self.get_secret(namespace, name) is not None

    # =============================
    # ConfigMap helpers (core/v1)
    # =============================
    @retry_api_call
    def get_configmap(self, namespace: str, name: str) -> Optional[Dict]:
        """Get a namespaced ConfigMap as dict or None if not found.

        Args:
            namespace: Namespace name
            name: ConfigMap name

        Returns:
            ConfigMap dict or None if not found

        Raises:
            ValidationError: If namespace or ConfigMap name is invalid
        """
        try:
            # Validate inputs before making API call
            InputValidator.validate_kubernetes_namespace(namespace)
            InputValidator.validate_kubernetes_name(name, "ConfigMap")

            cm = self.core_v1.read_namespaced_config_map(name=name, namespace=namespace)
            return cm.to_dict()
        except ApiException as e:
            if e.status == 404:
                return None
            if is_retryable_error(e):
                raise
            logger.error("Failed to read configmap %s/%s: %s", namespace, name, e)
            raise

    def exists_configmap(self, namespace: str, name: str) -> bool:
        """Check if ConfigMap exists.

        Args:
            namespace: Namespace name
            name: ConfigMap name

        Returns:
            True if ConfigMap exists

        Raises:
            ValidationError: If namespace or ConfigMap name is invalid
        """
        return self.get_configmap(namespace, name) is not None

    @retry_api_call
    def create_or_patch_configmap(self, namespace: str, name: str, data: Dict[str, str]) -> Dict:
        """Create or patch a ConfigMap's data field.

        If CM exists, patch data; otherwise create it.

        Args:
            namespace: Namespace name
            name: ConfigMap name
            data: ConfigMap data

        Returns:
            Created or patched ConfigMap dict

        Raises:
            ValidationError: If namespace or ConfigMap name is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_namespace(namespace)
        InputValidator.validate_kubernetes_name(name, "ConfigMap")

        if self.dry_run:
            logger.info(
                "[DRY-RUN] Would create/patch ConfigMap %s/%s with data keys: %s",
                namespace,
                name,
                list(data.keys()),
            )
            return {"metadata": {"name": name, "namespace": namespace}, "data": data}

        try:
            existing = self.get_configmap(namespace, name)
            if existing is None:
                body = {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": name, "namespace": namespace},
                    "data": data,
                }
                result = self.core_v1.create_namespaced_config_map(namespace=namespace, body=body)
                return result.to_dict()
            # Patch existing
            body = {"data": data}
            result = self.core_v1.patch_namespaced_config_map(name=name, namespace=namespace, body=body)
            return result.to_dict()
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error("Failed to create/patch configmap %s/%s: %s", namespace, name, e)
            raise

    @retry_api_call
    def delete_configmap(self, namespace: str, name: str) -> bool:
        """Delete a ConfigMap; return True if deleted or absent.

        Args:
            namespace: Namespace name
            name: ConfigMap name

        Returns:
            True if deleted or absent

        Raises:
            ValidationError: If namespace or ConfigMap name is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_namespace(namespace)
        InputValidator.validate_kubernetes_name(name, "ConfigMap")

        if self.dry_run:
            logger.info("[DRY-RUN] Would delete ConfigMap %s/%s", namespace, name)
            return True
        try:
            self.core_v1.delete_namespaced_config_map(name=name, namespace=namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return True
            if is_retryable_error(e):
                raise
            logger.error("Failed to delete configmap %s/%s: %s", namespace, name, e)
            raise

    @retry_api_call
    def get_route_host(self, namespace: str, name: str) -> Optional[str]:
        """Fetch the hostname for an OpenShift Route.

        Args:
            namespace: Namespace name
            name: Route name

        Returns:
            Route hostname or None if not found

        Raises:
            ValidationError: If namespace or Route name is invalid
        """
        try:
            # Validate inputs before making API call
            InputValidator.validate_kubernetes_namespace(namespace)
            InputValidator.validate_kubernetes_name(name, "Route")

            route = self.custom_api.get_namespaced_custom_object(
                group="route.openshift.io",
                version="v1",
                namespace=namespace,
                plural="routes",
                name=name,
            )
            return route.get("spec", {}).get("host")
        except ApiException as e:
            if e.status == 404:
                return None
            if is_retryable_error(e):
                raise
            logger.error("Failed to read Route %s/%s: %s", namespace, name, e)
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

        Raises:
            ValidationError: If resource name or namespace is invalid
        """
        try:
            # Validate inputs before making API call
            InputValidator.validate_kubernetes_name(name, "custom resource")
            if namespace:
                InputValidator.validate_kubernetes_namespace(namespace)

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

        Raises:
            ValidationError: If resource name or namespace is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_name(name, "custom resource")
        if namespace:
            InputValidator.validate_kubernetes_namespace(namespace)

        if self.dry_run:
            logger.info("[DRY-RUN] Would patch %s/%s with: %s", plural, name, patch)
            return {}

        logger.debug(
            "KUBE_CLIENT patch_custom_resource: group=%s, version=%s, " "plural=%s, name=%s, namespace=%s, patch=%s",
            group,
            version,
            plural,
            name,
            namespace,
            patch,
        )

        try:
            if namespace:
                logger.debug("KUBE_CLIENT: Calling patch_namespaced_custom_object...")
                result = self.custom_api.patch_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                    body=patch,
                )
                logger.debug("KUBE_CLIENT: patch_namespaced_custom_object returned successfully")
            else:
                logger.debug("KUBE_CLIENT: Calling patch_cluster_custom_object...")
                result = self.custom_api.patch_cluster_custom_object(
                    group=group, version=version, plural=plural, name=name, body=patch
                )
                logger.debug("KUBE_CLIENT: patch_cluster_custom_object returned successfully")

            logger.debug(
                "KUBE_CLIENT: Patch result keys: %s",
                list(result.keys()) if result else "None",
            )
            return result
        except ApiException as e:
            logger.error(
                "KUBE_CLIENT: ApiException during patch: status=%s, reason=%s, body=%s",
                e.status,
                e.reason,
                e.body[:500] if e.body else "None",
            )
            if is_retryable_error(e):
                raise
            logger.error("Failed to patch %s/%s: %s", plural, name, e)
            raise
        except Exception as e:
            logger.error(
                "KUBE_CLIENT: Unexpected exception during patch: %s: %s",
                type(e).__name__,
                e,
            )
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
        """Create a custom resource.

        Args:
            group: API group
            version: API version
            plural: Resource plural
            body: Resource body dict
            namespace: Namespace (None for cluster-scoped)

        Returns:
            Created resource dict

        Raises:
            ValidationError: If resource name or namespace is invalid
        """
        # Validate resource name from body
        resource_name = body.get("metadata", {}).get("name")
        if resource_name:
            InputValidator.validate_kubernetes_name(resource_name, "custom resource")
        if namespace:
            InputValidator.validate_kubernetes_namespace(namespace)

        if self.dry_run:
            logger.info(
                "[DRY-RUN] Would create %s: %s",
                plural,
                body.get("metadata", {}).get("name"),
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
            logger.error("Failed to create %s: %s", plural, e)
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
        """Delete a custom resource.

        Args:
            group: API group
            version: API version
            plural: Resource plural
            name: Resource name
            namespace: Namespace (None for cluster-scoped)

        Returns:
            True if deleted or absent

        Raises:
            ValidationError: If resource name or namespace is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_name(name, "custom resource")
        if namespace:
            InputValidator.validate_kubernetes_namespace(namespace)

        if self.dry_run:
            logger.info("[DRY-RUN] Would delete %s/%s", plural, name)
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
                self.custom_api.delete_cluster_custom_object(group=group, version=version, plural=plural, name=name)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            if is_retryable_error(e):
                raise
            logger.error("Failed to delete %s/%s: %s", plural, name, e)
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
        """Scale a deployment.

        Args:
            name: Deployment name
            namespace: Namespace name
            replicas: Number of replicas

        Returns:
            Scaled deployment dict

        Raises:
            ValidationError: If deployment name or namespace is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_name(name, "deployment")
        InputValidator.validate_kubernetes_namespace(namespace)

        if self.dry_run:
            logger.info(
                "[DRY-RUN] Would scale deployment %s/%s to %s replicas",
                namespace,
                name,
                replicas,
            )
            return {}

        try:
            body = {"spec": {"replicas": replicas}}
            result = self.apps_v1.patch_namespaced_deployment_scale(name=name, namespace=namespace, body=body)
            return result.to_dict()
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error("Failed to scale deployment %s/%s: %s", namespace, name, e)
            raise

    @retry_api_call
    def scale_statefulset(self, name: str, namespace: str, replicas: int) -> Dict:
        """Scale a statefulset.

        Args:
            name: StatefulSet name
            namespace: Namespace name
            replicas: Number of replicas

        Returns:
            Scaled StatefulSet dict

        Raises:
            ValidationError: If StatefulSet name or namespace is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_name(name, "statefulset")
        InputValidator.validate_kubernetes_namespace(namespace)

        if self.dry_run:
            logger.info(
                "[DRY-RUN] Would scale statefulset %s/%s to %s replicas",
                namespace,
                name,
                replicas,
            )
            return {}

        try:
            body = {"spec": {"replicas": replicas}}
            result = self.apps_v1.patch_namespaced_stateful_set_scale(name=name, namespace=namespace, body=body)
            return result.to_dict()
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error("Failed to scale statefulset %s/%s: %s", namespace, name, e)
            raise

    @retry_api_call
    def rollout_restart_deployment(self, name: str, namespace: str) -> Dict:
        """Restart a deployment by updating restart annotation.

        Args:
            name: Deployment name
            namespace: Namespace name

        Returns:
            Restarted deployment dict

        Raises:
            ValidationError: If deployment name or namespace is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_name(name, "deployment")
        InputValidator.validate_kubernetes_namespace(namespace)

        if self.dry_run:
            logger.info("[DRY-RUN] Would restart deployment %s/%s", namespace, name)
            return {}

        try:
            now = time.strftime("%Y%m%d%H%M%S")
            body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
            result = self.apps_v1.patch_namespaced_deployment(name=name, namespace=namespace, body=body)
            return result.to_dict()
        except ApiException as e:
            if is_retryable_error(e):
                raise
            logger.error("Failed to restart deployment %s/%s: %s", namespace, name, e)
            raise

    @retry_api_call
    def get_pods(self, namespace: str, label_selector: Optional[str] = None) -> List[Dict]:
        """List pods in a namespace.

        Args:
            namespace: Namespace name
            label_selector: Optional label selector

        Returns:
            List of pod dicts

        Raises:
            ValidationError: If namespace is invalid
        """
        # Validate inputs before making API call
        InputValidator.validate_kubernetes_namespace(namespace)
        if label_selector is not None:
            # Basic validation - only check for non-empty string
            # Full label selector validation is complex (supports =, ==, !=, in, notin, exists, !exists)
            # and includes keys with prefixes like 'app.kubernetes.io/name'
            # Let the Kubernetes API validate the selector and return appropriate errors
            if not label_selector.strip():
                raise ValidationError("Label selector cannot be empty or whitespace-only")

        try:
            result = self.core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
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
                logger.debug("Waiting for %s pods, found %s", expected_count, len(pods))
                time.sleep(5)
                continue

            ready_count = 0
            for pod in pods:
                conditions = pod.get("status", {}).get("conditions", [])
                for condition in conditions:
                    if condition.get("type") == "Ready" and condition.get("status") == "True":
                        ready_count += 1
                        break

            if expected_count is None:
                if ready_count == len(pods) and len(pods) > 0:
                    logger.info("All %s pods ready in %s", ready_count, namespace)
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

            logger.debug("%s/%s pods ready in %s", ready_count, len(pods), namespace)
            time.sleep(5)

        logger.error("Timeout waiting for pods in %s", namespace)
        return False
