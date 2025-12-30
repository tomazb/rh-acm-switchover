"""Version and compatibility validation checks."""

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from lib.constants import (
    ACM_NAMESPACE,
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_NAMESPACE,
    IMPORT_CONTROLLER_CONFIGMAP,
    MCE_NAMESPACE,
)
from lib.kube_client import KubeClient
from lib.utils import is_acm_version_ge
from lib.validation import InputValidator, ValidationError

from .base_validator import BaseValidator

logger = logging.getLogger("acm_switchover")


class KubeconfigValidator(BaseValidator):
    """Validates kubeconfig structure and token expiration for switchover contexts.

    Checks for:
    - Duplicate user credentials across merged configs (causes auth failures)
    - Expired or near-expiry SA tokens (parsed from JWT)
    - Connectivity to API servers
    """

    # Warn if token expires within this many hours
    TOKEN_EXPIRY_WARNING_HOURS = 4

    def run(self, primary: KubeClient, secondary: KubeClient) -> None:
        """Run kubeconfig validation checks."""
        # Check connectivity (implicitly validated by KubeClient init, but verify)
        self._check_connectivity(primary, "primary")
        self._check_connectivity(secondary, "secondary")

        # Check for duplicate users in merged kubeconfig
        self._check_duplicate_users(primary, secondary)

        # Check token expiration
        self._check_token_expiration(primary, "primary")
        self._check_token_expiration(secondary, "secondary")

    def _check_connectivity(self, client: KubeClient, hub_label: str) -> None:
        """Verify API server is reachable."""
        try:
            # A simple API call to verify connectivity
            # The KubeClient should have already validated this, but let's confirm
            client.list_namespaces()
            self.add_result(
                f"API Connectivity ({hub_label})",
                True,
                f"Successfully connected to {hub_label} hub API server",
                critical=True,
            )
        except Exception as e:
            self.add_result(
                f"API Connectivity ({hub_label})",
                False,
                f"Cannot connect to {hub_label} hub: {str(e)}",
                critical=True,
            )

    def _check_duplicate_users(self, primary: KubeClient, secondary: KubeClient) -> None:
        """Check for duplicate user names across merged kubeconfigs.

        When merging kubeconfigs from multiple clusters with the same SA names,
        credentials can collide and cause authentication failures.
        """
        try:
            from kubernetes import config as k8s_config

            # Load the current kubeconfig to check for duplicates
            contexts, active_context = k8s_config.list_kube_config_contexts()
            if not contexts:
                return

            # Extract user names and check for potential collisions
            user_to_contexts: Dict[str, List[str]] = {}
            for ctx in contexts:
                ctx_name = ctx.get("name", "unknown")
                user_name = ctx.get("context", {}).get("user", "unknown")
                if user_name not in user_to_contexts:
                    user_to_contexts[user_name] = []
                user_to_contexts[user_name].append(ctx_name)

            # Find duplicate user names (potential credential collision)
            duplicates = {u: ctxs for u, ctxs in user_to_contexts.items() if len(ctxs) > 1}

            if duplicates:
                # Check if our contexts are affected
                our_contexts = {primary.context, secondary.context}
                affected = []
                for user, ctxs in duplicates.items():
                    affected_ctxs = [c for c in ctxs if c in our_contexts]
                    if len(affected_ctxs) > 0:
                        affected.append(f"user '{user}' in contexts: {', '.join(ctxs)}")

                if affected:
                    self.add_result(
                        "Kubeconfig User Names",
                        False,
                        f"Potential credential collision detected: {'; '.join(affected)}. "
                        "Consider regenerating kubeconfigs with unique --user names.",
                        critical=False,  # Warning, not fatal
                    )
                else:
                    self.add_result(
                        "Kubeconfig User Names",
                        True,
                        "No duplicate user names detected for switchover contexts",
                        critical=False,
                    )
            else:
                self.add_result(
                    "Kubeconfig User Names",
                    True,
                    "No duplicate user names detected",
                    critical=False,
                )
        except Exception as exc:
            self.add_result(
                "Kubeconfig User Names",
                False,
                f"error checking for duplicates: {exc}",
                critical=False,
            )

    def _check_token_expiration(self, client: KubeClient, hub_label: str) -> None:
        """Check service account token expiration."""
        try:
            # Get current configuration to extract token
            from kubernetes import config as k8s_config

            # Load the current kubeconfig
            k8s_config.load_kube_config(context=client.context)

            # Get the current configuration
            try:
                current_config = k8s_config.Configuration.get_default_copy()
            except Exception:
                # Fallback for older kubernetes client versions
                current_config = k8s_config.Configuration()

            # Extract token from Bearer auth
            auth_header = current_config.api_key.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                self.add_result(
                    f"Token Expiration ({hub_label})",
                    True,
                    "No Bearer token found (likely using basic auth or cert)",
                    critical=False,
                )
                return

            token = auth_header[7:]  # Remove "Bearer " prefix

            # Try to decode JWT token
            try:
                # Split token and decode payload
                parts = token.split(".")
                if len(parts) != 3:
                    raise ValueError("Invalid JWT format")

                # Decode payload (base64url encoded)
                payload = parts[1]
                # Add padding if needed
                padding = len(payload) % 4
                if padding:
                    payload += "=" * (4 - padding)
                decoded = base64.urlsafe_b64decode(payload)
                claims = json.loads(decoded)

                # Check expiration claim
                if "exp" in claims:
                    exp_timestamp = claims["exp"]
                    exp_datetime = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                    now = datetime.now(tz=timezone.utc)
                    
                    # Calculate hours until expiration
                    hours_until_expiry = (exp_datetime - now).total_seconds() / 3600

                    if hours_until_expiry < 0:
                        self.add_result(
                            f"Token Expiration ({hub_label})",
                            False,
                            f"Token expired {abs(hours_until_expiry):.1f} hours ago",
                            critical=True,
                        )
                    elif hours_until_expiry < self.TOKEN_EXPIRY_WARNING_HOURS:
                        self.add_result(
                            f"Token Expiration ({hub_label})",
                            False,
                            f"Token expires in {hours_until_expiry:.1f} hours (soon)",
                            critical=True,
                        )
                    else:
                        self.add_result(
                            f"Token Expiration ({hub_label})",
                            True,
                            f"Token valid for {hours_until_expiry:.1f} hours",
                            critical=False,
                        )
                else:
                    self.add_result(
                        f"Token Expiration ({hub_label})",
                        True,
                        "Token has no expiration claim",
                        critical=False,
                    )

            except (ValueError, json.JSONDecodeError, KeyError) as e:
                self.add_result(
                    f"Token Expiration ({hub_label})",
                    True,
                    f"Cannot decode token expiration: {e}",
                    critical=False,
                )

        except Exception as exc:
            self.add_result(
                f"Token Expiration ({hub_label})",
                False,
                f"error checking token: {exc}",
                critical=False,
            )


class VersionValidator(BaseValidator):
    """Detects ACM versions and ensures they match between hubs."""

    def run(self, primary: KubeClient, secondary: KubeClient) -> Tuple[str, str]:
        """Run version validation with both clients.

        Args:
            primary: Primary hub KubeClient instance
            secondary: Secondary hub KubeClient instance

        Returns:
            Tuple of (primary_version, secondary_version)
        """
        primary_version = self._detect_version(primary, "primary")
        secondary_version = self._detect_version(secondary, "secondary")
        self._validate_match(primary_version, secondary_version)
        return primary_version, secondary_version

    def _detect_version(self, kube_client: KubeClient, hub_name: str) -> str:
        """Detect ACM version on a hub.

        Args:
            kube_client: KubeClient instance
            hub_name: Name of the hub for logging

        Returns:
            Detected version string or "unknown"
        """
        try:
            mch = kube_client.get_custom_resource(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                name="multiclusterhub",
                namespace=ACM_NAMESPACE,
            )

            if not mch:
                mchs = kube_client.list_custom_resources(
                    group="operator.open-cluster-management.io",
                    version="v1",
                    plural="multiclusterhubs",
                    namespace=ACM_NAMESPACE,
                )
                if mchs:
                    mch = mchs[0]

            if mch:
                version = mch.get("status", {}).get("currentVersion", "unknown")
                self.add_result(
                    f"ACM version ({hub_name})",
                    True,
                    f"detected: {version}",
                    critical=True,
                )
                return version

            self.add_result(
                f"ACM version ({hub_name})",
                False,
                "MultiClusterHub not found",
                critical=True,
            )
            return "unknown"
        except (RuntimeError, ValueError, Exception) as exc:  # pragma: no cover - kube errors
            self.add_result(
                f"ACM version ({hub_name})",
                False,
                f"error detecting version: {exc}",
                critical=True,
            )
            return "unknown"

    def _validate_match(self, primary_version: str, secondary_version: str) -> None:
        """Validate that versions match between hubs.

        Args:
            primary_version: Version detected on primary hub
            secondary_version: Version detected on secondary hub
        """
        if "unknown" in (primary_version, secondary_version):
            self.add_result(
                "ACM version matching",
                False,
                "cannot verify - version detection failed",
                critical=True,
            )
            return

        if primary_version == secondary_version:
            self.add_result(
                "ACM version matching",
                True,
                f"both hubs running {primary_version}",
                critical=True,
            )
        else:
            self.add_result(
                "ACM version matching",
                False,
                f"version mismatch - primary: {primary_version}, secondary: {secondary_version}",
                critical=True,
            )


class HubComponentValidator(BaseValidator):
    """Validates per-hub components such as OADP and DPA."""

    def run(self, kube_client: KubeClient, hub_label: str) -> None:
        """Run component validation.

        Args:
            kube_client: KubeClient instance
            hub_label: Label for the hub (primary/secondary)
        """
        self._check_oadp_operator(kube_client, hub_label)
        self._check_dpa(kube_client, hub_label)

    def _check_oadp_operator(self, kube_client: KubeClient, hub_name: str) -> None:
        """Check OADP operator installation.

        Args:
            kube_client: KubeClient instance
            hub_name: Name of the hub for logging
        """
        try:
            if kube_client.namespace_exists(BACKUP_NAMESPACE):
                pods = kube_client.get_pods(
                    namespace=BACKUP_NAMESPACE,
                    label_selector="app.kubernetes.io/name=velero",
                )

                if pods:
                    self.add_result(
                        f"OADP operator ({hub_name})",
                        True,
                        f"installed, {len(pods)} Velero pod(s) found",
                        critical=True,
                    )
                else:
                    self.add_result(
                        f"OADP operator ({hub_name})",
                        False,
                        "namespace exists but no Velero pods found",
                        critical=True,
                    )
            else:
                self.add_result(
                    f"OADP operator ({hub_name})",
                    False,
                    f"{BACKUP_NAMESPACE} namespace not found",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.add_result(
                f"OADP operator ({hub_name})",
                False,
                f"error checking OADP: {exc}",
                critical=True,
            )

    def _check_dpa(self, kube_client: KubeClient, hub_name: str) -> None:
        """Check DataProtectionApplication status.

        Args:
            kube_client: KubeClient instance
            hub_name: Name of the hub for logging
        """
        try:
            # Validate namespace before using it
            InputValidator.validate_kubernetes_namespace(BACKUP_NAMESPACE)

            dpas = kube_client.list_custom_resources(
                group="oadp.openshift.io",
                version="v1alpha1",
                plural="dataprotectionapplications",
                namespace=BACKUP_NAMESPACE,
            )

            if dpas:
                dpa = dpas[0]
                dpa_name = dpa.get("metadata", {}).get("name", "unknown")
                conditions = dpa.get("status", {}).get("conditions", [])
                reconciled = any(c.get("type") == "Reconciled" and c.get("status") == "True" for c in conditions)

                if reconciled:
                    self.add_result(
                        f"DataProtectionApplication ({hub_name})",
                        True,
                        f"{dpa_name} is reconciled",
                        critical=True,
                    )
                else:
                    self.add_result(
                        f"DataProtectionApplication ({hub_name})",
                        False,
                        f"{dpa_name} exists but not reconciled",
                        critical=True,
                    )
            else:
                self.add_result(
                    f"DataProtectionApplication ({hub_name})",
                    False,
                    "no DataProtectionApplication found",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.add_result(
                f"DataProtectionApplication ({hub_name})",
                False,
                f"error checking DPA: {exc}",
                critical=True,
            )


class AutoImportStrategyValidator(BaseValidator):
    """Validate autoImportStrategy (ACM 2.14+) and provide guidance.

    Behavior is detect-only; never fails preflight critically.
    """

    def _strategy_for(self, client: KubeClient) -> str:
        """Get auto-import strategy for a client.

        Args:
            client: KubeClient instance

        Returns:
            Strategy string or error indicator
        """
        try:
            # Validate namespace and configmap name before using them
            InputValidator.validate_kubernetes_namespace(MCE_NAMESPACE)
            InputValidator.validate_kubernetes_name(IMPORT_CONTROLLER_CONFIGMAP, "configmap")
        except ValidationError:
            return "default"

        try:
            cm = client.get_configmap(MCE_NAMESPACE, IMPORT_CONTROLLER_CONFIGMAP)
        except Exception as exc:
            # API / connection / RBAC issues - treat as non-critical
            logger.debug("Error reading auto-import ConfigMap: %s", exc)
            return "error"

        if not cm:
            return "default"
        data = (cm or {}).get("data") or {}
        strategy = data.get(AUTO_IMPORT_STRATEGY_KEY, "")
        return strategy or "default"

    def _non_local_cluster_count(self, client: KubeClient) -> int:
        """Count non-local managed clusters.

        Args:
            client: KubeClient instance

        Returns:
            Number of non-local clusters
        """
        try:
            mcs = client.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
            )
        except Exception as exc:
            logger.debug("Error listing managedclusters for auto-import check: %s", exc)
            return 0

        return sum(1 for mc in mcs if mc.get("metadata", {}).get("name") != "local-cluster")

    def run(
        self,
        primary: KubeClient,
        secondary: KubeClient,
        primary_version: str,
        secondary_version: str,
    ) -> None:
        """Run auto-import strategy validation.

        Args:
            primary: Primary hub KubeClient instance
            secondary: Secondary hub KubeClient instance
            primary_version: ACM version on primary hub
            secondary_version: ACM version on secondary hub
        """
        # Primary hub
        if is_acm_version_ge(primary_version, "2.14.0"):
            strategy = self._strategy_for(primary)
            if strategy == "error":
                self.add_result(
                    "Auto-Import Strategy (primary)",
                    False,
                    "could not retrieve autoImportStrategy (connection or API error)",
                    critical=False,
                )
            elif strategy in ("default", AUTO_IMPORT_STRATEGY_DEFAULT):
                self.add_result(
                    "Auto-Import Strategy (primary)",
                    True,
                    f"default ({AUTO_IMPORT_STRATEGY_DEFAULT}) in effect",
                    critical=False,
                )
            else:
                self.add_result(
                    "Auto-Import Strategy (primary)",
                    False,
                    f"non-default strategy in use: {strategy}",
                    critical=False,
                )
        else:
            self.add_result(
                "Auto-Import Strategy (primary)",
                True,
                f"ACM {primary_version} (< 2.14) - not applicable",
                critical=False,
            )

        # Secondary hub
        if is_acm_version_ge(secondary_version, "2.14.0"):
            strategy = self._strategy_for(secondary)
            count = self._non_local_cluster_count(secondary)
            
            if strategy == "error":
                self.add_result(
                    "Auto-Import Strategy (secondary)",
                    False,
                    "could not retrieve autoImportStrategy (connection or API error)",
                    critical=False,
                )
                # Do not apply further hints if we couldn't read the config
                return
            elif count > 0 and strategy in ("default", AUTO_IMPORT_STRATEGY_DEFAULT):
                self.add_result(
                    "Auto-Import Strategy (secondary)",
                    False,
                    (
                        f"secondary has {count} existing managed cluster(s) and strategy is default ({AUTO_IMPORT_STRATEGY_DEFAULT}). "
                        f"Per runbook, consider temporarily setting {AUTO_IMPORT_STRATEGY_SYNC} on the destination hub before restore, "
                        f"then reset to default afterward."
                    ),
                    critical=False,
                )
            elif strategy == AUTO_IMPORT_STRATEGY_SYNC:
                self.add_result(
                    "Auto-Import Strategy (secondary)",
                    True,
                    f"{AUTO_IMPORT_STRATEGY_SYNC} set (ensure to reset to default after activation)",
                    critical=False,
                )
            else:
                self.add_result(
                    "Auto-Import Strategy (secondary)",
                    True,
                    f"default ({AUTO_IMPORT_STRATEGY_DEFAULT}) in effect",
                    critical=False,
                )
        else:
            self.add_result(
                "Auto-Import Strategy (secondary)",
                True,
                f"ACM {secondary_version} (< 2.14) - not applicable",
                critical=False,
            )
