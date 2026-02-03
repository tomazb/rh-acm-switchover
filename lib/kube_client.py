"""
Kubernetes client wrapper for ACM resources.

This module includes comprehensive input validation for Kubernetes resource
names, namespaces, and other parameters to improve security and reliability.
"""

import errno
import functools
import logging
import socket
import time
from typing import Any, Callable, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from urllib3.exceptions import HTTPError, MaxRetryError, NewConnectionError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

from lib.validation import InputValidator, ValidationError

logger = logging.getLogger("acm_switchover")


def is_retryable_error(exception: BaseException) -> bool:
    """Check if exception is retryable.

    Handles:
    - API server errors (5xx) and rate limiting (429)
    - Network-related errors (connection failures, timeouts)
    - urllib3 errors (HTTPError, MaxRetryError, NewConnectionError, TimeoutError)
    """
    if isinstance(exception, ApiException):
        # Retry on server errors (5xx) and too many requests (429)
        return 500 <= exception.status < 600 or exception.status == 429
    # Network-related exceptions that should be retried
    if isinstance(exception, (HTTPError, MaxRetryError, NewConnectionError, Urllib3TimeoutError)):
        return True
    # Connection and timeout errors (network-specific)
    if isinstance(exception, (ConnectionError, TimeoutError)):
        return True
    # Socket timeout (specific network timeout)
    if isinstance(exception, socket.timeout):
        return True
    # OSError with network-related errno values (avoid retrying file/permission errors)
    # Use getattr for errno constants that may not exist on all platforms (e.g., Windows)
    retryable_errnos = {
        errno.ECONNREFUSED,  # Connection refused
        errno.ECONNRESET,  # Connection reset by peer
        errno.ENETUNREACH,  # Network is unreachable
        errno.ETIMEDOUT,  # Connection timed out
        errno.EAGAIN,  # Resource temporarily unavailable (may be network-related)
    }
    # Add platform-specific errno constants if available
    for errno_name in ("ECONNABORTED", "EHOSTUNREACH", "EWOULDBLOCK"):
        errno_val = getattr(errno, errno_name, None)
        if errno_val is not None:
            retryable_errnos.add(errno_val)
    if isinstance(exception, OSError) and exception.errno in retryable_errnos:
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


def api_call(
    not_found_value: Any = None,
    log_on_error: bool = True,
    resource_desc: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Combined decorator for Kubernetes API calls with retry and exception handling.

    Combines retry logic (5xx/429 → exponential backoff) with standard exception handling:
    - 404 → return not_found_value
    - Retryable errors → re-raise for tenacity
    - Other errors → log and re-raise

    Args:
        not_found_value: Value to return when resource not found (404)
        log_on_error: Whether to log non-retryable errors before re-raising
        resource_desc: Description for error messages (defaults to method name)

    Usage:
        @api_call(not_found_value=None)
        def get_namespace(self, name: str) -> Optional[Dict]:
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        desc = resource_desc or func.__name__.replace("_", " ")

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except ApiException as e:
                if e.status == 404:
                    return not_found_value
                if is_retryable_error(e):
                    raise
                if log_on_error:
                    logger.error("Failed to %s: %s", desc, e)
                raise

        # Apply retry decorator
        return retry_api_call(wrapper)

    return decorator


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

        # Load config for specific context with clearer error handling
        try:
            config.load_kube_config(context=context)
        except ConfigException as exc:
            logger.error("Failed to load kubeconfig for context %s: %s", context or "default", exc)
            raise

        # Create per-instance configuration to avoid affecting other clients
        configuration = client.Configuration.get_default_copy()
        # Tenacity handles retries for API calls; disable urllib3 retries to avoid double retry layers.
        # NOTE: With this setting, the underlying HTTP client will not retry failed requests on its own.
        #       Any operation that is not wrapped by the Tenacity-based retry decorator (e.g., @retry_api_call),
        #       or if Tenacity is disabled/misconfigured, will perform no automatic retries and may fail on
        #       transient network or server errors.
        configuration.retries = 0

        if disable_hostname_verification and hasattr(configuration, "assert_hostname"):
            configuration.assert_hostname = False
            logger.warning(
                "INSECURE TLS: Hostname verification disabled for context %s. Only use in trusted labs.",
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

    def _validate_resource_inputs(
        self,
        namespace: Optional[str] = None,
        name: Optional[str] = None,
        resource_type: str = "resource",
    ) -> None:
        """Validate namespace and/or resource name before API calls.

        Args:
            namespace: Namespace to validate (skipped if None)
            name: Resource name to validate (skipped if None)
            resource_type: Resource type for error messages (e.g., "secret", "deployment")

        Raises:
            ValidationError: If namespace or name is invalid
        """
        if namespace is not None:
            InputValidator.validate_kubernetes_namespace(namespace)
        if name is not None:
            InputValidator.validate_kubernetes_name(name, resource_type)

    @api_call(not_found_value=None, resource_desc="get namespace")
    def get_namespace(self, name: str) -> Optional[Dict]:
        """Check if namespace exists.

        Args:
            name: Namespace name

        Returns:
            Namespace dict or None if not found

        Raises:
            ValidationError: If namespace name is invalid
        """
        self._validate_resource_inputs(namespace=name)

        ns = self.core_v1.read_namespace(name)
        return ns.to_dict()

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

    @api_call(not_found_value=[], resource_desc="list namespaces")
    def list_namespaces(self) -> List[Dict]:
        """List all namespaces.

        Returns:
            List of namespace dictionaries

        Raises:
            None (uses api_call decorator for error handling)
        """
        result = self.core_v1.list_namespace()
        return [ns.to_dict() for ns in result.items]

    @api_call(not_found_value=None, log_on_error=False)
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
        self._validate_resource_inputs(namespace, name, "secret")

        secret = self.core_v1.read_namespaced_secret(name=name, namespace=namespace)
        return secret.to_dict()

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
    @api_call(not_found_value=None, resource_desc="read configmap")
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
        self._validate_resource_inputs(namespace, name, "ConfigMap")

        cm = self.core_v1.read_namespaced_config_map(name=name, namespace=namespace)
        return cm.to_dict()

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
        self._validate_resource_inputs(namespace, name, "ConfigMap")

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

    @api_call(not_found_value=True, resource_desc="delete configmap")
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
        self._validate_resource_inputs(namespace, name, "ConfigMap")

        if self.dry_run:
            logger.info("[DRY-RUN] Would delete ConfigMap %s/%s", namespace, name)
            return True

        self.core_v1.delete_namespaced_config_map(name=name, namespace=namespace)
        return True

    @api_call(not_found_value=True, resource_desc="delete pod")
    def delete_pod(self, namespace: str, name: str) -> bool:
        """Delete a Pod; return True if deleted or absent.

        Args:
            namespace: Namespace name
            name: Pod name

        Returns:
            True if deleted or absent

        Raises:
            ValidationError: If namespace or Pod name is invalid
        """
        self._validate_resource_inputs(namespace, name, "Pod")

        if self.dry_run:
            logger.info("[DRY-RUN] Would delete Pod %s/%s", namespace, name)
            return True

        self.core_v1.delete_namespaced_pod(name=name, namespace=namespace)
        return True

    @api_call(not_found_value=None, resource_desc="read Route")
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
        self._validate_resource_inputs(namespace, name, "Route")

        route = self.custom_api.get_namespaced_custom_object(
            group="route.openshift.io",
            version="v1",
            namespace=namespace,
            plural="routes",
            name=name,
        )
        return route.get("spec", {}).get("host")

    @api_call(not_found_value=None, log_on_error=False)
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
        self._validate_resource_inputs(namespace, name, "custom resource")

        if namespace:
            resource = self.custom_api.get_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                name=name,
            )
        else:
            resource = self.custom_api.get_cluster_custom_object(group=group, version=version, plural=plural, name=name)
        return resource

    @retry_api_call
    def list_custom_resources(
        self,
        group: str,
        version: str,
        plural: str,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> List[Dict]:
        """
        List custom resources.

        Args:
            group: API group
            version: API version
            plural: Resource plural
            namespace: Namespace (None for cluster-scoped)
            label_selector: Label selector filter
            max_items: Maximum number of items to return (None for unlimited).
                      Use this to prevent memory exhaustion on large clusters.
                      When set, a server-side `limit` is passed to the API calls.

        Returns:
            List of resource dicts, limited to max_items if specified

        Raises:
            ValidationError: If namespace is invalid
        """
        self._validate_resource_inputs(namespace=namespace)

        items: List[Dict] = []
        continue_token: Optional[str] = None

        while True:
            # Check if we've hit the limit before fetching more
            if max_items is not None and len(items) >= max_items:
                logger.debug("Hit max_items limit %d, stopping fetch", max_items)
                break

            remaining = None
            if max_items is not None:
                remaining = max_items - len(items)
                if remaining <= 0:
                    break

            try:
                if namespace:
                    result = self.custom_api.list_namespaced_custom_object(
                        group=group,
                        version=version,
                        namespace=namespace,
                        plural=plural,
                        label_selector=label_selector,
                        _continue=continue_token,
                        limit=remaining,
                    )
                else:
                    result = self.custom_api.list_cluster_custom_object(
                        group=group,
                        version=version,
                        plural=plural,
                        label_selector=label_selector,
                        _continue=continue_token,
                        limit=remaining,
                    )
            except ApiException as e:
                if e.status == 404:
                    return []
                if is_retryable_error(e):
                    raise
                raise

            page_items = result.get("items", [])

            # If we have a limit, only take what we need
            if max_items is not None:
                items.extend(page_items[:remaining])
            else:
                items.extend(page_items)

            metadata = result.get("metadata") or {}
            continue_token = metadata.get("continue")

            # Stop if no more pages or we've hit the limit
            if not continue_token or (max_items is not None and len(items) >= max_items):
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
        self._validate_resource_inputs(namespace, name, "custom resource")

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
        resource_name = body.get("metadata", {}).get("name")
        self._validate_resource_inputs(namespace, resource_name, "custom resource")

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

    @api_call(not_found_value=True, resource_desc="delete custom resource")
    def delete_custom_resource(
        self,
        group: str,
        version: str,
        plural: str,
        name: str,
        namespace: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> bool:
        """Delete a custom resource.

        Args:
            group: API group
            version: API version
            plural: Resource plural
            name: Resource name
            namespace: Namespace (None for cluster-scoped)
            timeout_seconds: Request timeout in seconds. If None, uses client default.
                           Prevents hanging on stuck API calls.

        Returns:
            True if deleted or already absent (idempotent)

        Raises:
            ValidationError: If resource name or namespace is invalid
        """
        self._validate_resource_inputs(namespace, name, "custom resource")

        if self.dry_run:
            logger.info("[DRY-RUN] Would delete %s/%s", plural, name)
            return True

        # Build optional kwargs for timeout
        kwargs: Dict[str, Any] = {}
        if timeout_seconds is not None:
            kwargs["_request_timeout"] = timeout_seconds

        if namespace:
            self.custom_api.delete_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                name=name,
                **kwargs,
            )
        else:
            self.custom_api.delete_cluster_custom_object(
                group=group, version=version, plural=plural, name=name, **kwargs
            )
        return True

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

    @api_call(not_found_value=None, resource_desc="get deployment")
    def get_deployment(self, name: str, namespace: str) -> Optional[Dict]:
        """Get a deployment by name.

        Args:
            name: Deployment name
            namespace: Namespace name

        Returns:
            Deployment dict or None if not found

        Raises:
            ValidationError: If deployment name or namespace is invalid
        """
        self._validate_resource_inputs(namespace, name, "deployment")

        deployment = self.apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
        return deployment.to_dict()

    @api_call(not_found_value=None, resource_desc="get statefulset")
    def get_statefulset(self, name: str, namespace: str) -> Optional[Dict]:
        """Get a statefulset by name.

        Args:
            name: StatefulSet name
            namespace: Namespace name

        Returns:
            StatefulSet dict or None if not found

        Raises:
            ValidationError: If statefulset name or namespace is invalid
        """
        self._validate_resource_inputs(namespace, name, "statefulset")

        statefulset = self.apps_v1.read_namespaced_stateful_set(name=name, namespace=namespace)
        return statefulset.to_dict()

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
        self._validate_resource_inputs(namespace, name, "deployment")

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
        self._validate_resource_inputs(namespace, name, "statefulset")

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
        self._validate_resource_inputs(namespace, name, "deployment")

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

    @api_call(not_found_value=[], log_on_error=False)
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
        self._validate_resource_inputs(namespace=namespace)
        if label_selector is not None:
            # Basic validation - only check for non-empty string
            # Full label selector validation is complex (supports =, ==, !=, in, notin, exists, !exists)
            # and includes keys with prefixes like 'app.kubernetes.io/name'
            # Let the Kubernetes API validate the selector and return appropriate errors
            if not label_selector.strip():
                raise ValidationError("Label selector cannot be empty or whitespace-only")

        result = self.core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        return [pod.to_dict() for pod in result.items]

    def list_pods(
        self,
        namespace: str,
        label_selector: Optional[str] = None,
    ) -> List[Dict]:
        """Backward compatible alias for get_pods."""
        return self.get_pods(namespace, label_selector)

    @api_call(not_found_value="", log_on_error=False, resource_desc="get pod logs")
    def get_pod_logs(
        self,
        name: str,
        namespace: str,
        container: Optional[str] = None,
        tail_lines: Optional[int] = None,
    ) -> str:
        """Retrieve logs for a pod.

        Args:
            name: Pod name
            namespace: Namespace name
            container: Optional container name
            tail_lines: Optional number of lines from the end of the logs

        Returns:
            Log content string (empty if not found)
        """
        self._validate_resource_inputs(namespace, name, "pod")

        if self.dry_run:
            logger.info("[DRY-RUN] Would read logs for pod %s/%s", namespace, name)
            return ""

        kwargs: Dict[str, Any] = {}
        if container:
            kwargs["container"] = container
        if tail_lines is not None:
            # Validate tail_lines to fail fast with clear, actionable errors
            try:
                tail_lines_int = int(tail_lines)
            except (TypeError, ValueError) as exc:
                raise ValidationError("tail_lines must be a non-negative integer") from exc
            if tail_lines_int < 0:
                raise ValidationError("tail_lines must be a non-negative integer")
            kwargs["tail_lines"] = tail_lines_int

        return self.core_v1.read_namespaced_pod_log(name=name, namespace=namespace, **kwargs) or ""

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
