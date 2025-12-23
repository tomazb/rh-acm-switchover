"""Helper classes for ACM pre-flight validation.

This module includes comprehensive input validation for context names,
namespaces, and other external inputs to improve security and reliability.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

from lib.constants import (
    ACM_NAMESPACE,
    AUTO_IMPORT_STRATEGY_DEFAULT,
    AUTO_IMPORT_STRATEGY_KEY,
    AUTO_IMPORT_STRATEGY_SYNC,
    BACKUP_NAMESPACE,
    BACKUP_SCHEDULE_DEFAULT_NAME,
    IMPORT_CONTROLLER_CONFIGMAP,
    MCE_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
    RESTORE_PASSIVE_SYNC_NAME,
    SPEC_USE_MANAGED_SERVICE_ACCOUNT,
    THANOS_OBJECT_STORAGE_SECRET,
)
from lib.kube_client import KubeClient
from lib.utils import is_acm_version_ge
from lib.validation import InputValidator, ValidationError

logger = logging.getLogger("acm_switchover")


class ValidationReporter:
    """Collects validation results and handles summary logging."""

    def __init__(self) -> None:
        self.results: List[Dict[str, Any]] = []

    def add_result(
        self,
        check: str,
        passed: bool,
        message: str,
        critical: bool = True,
    ) -> None:
        self.results.append(
            {
                "check": check,
                "passed": passed,
                "message": message,
                "critical": critical,
            }
        )

        if passed:
            logger.info(f"✓ {check}: {message}")
        elif critical:
            logger.error(f"✗ {check}: {message}")
        else:
            logger.warning(f"⚠ {check}: {message}")

    def critical_failures(self) -> List[Dict[str, Any]]:
        return [r for r in self.results if not r["passed"] and r["critical"]]

    def print_summary(self) -> None:
        passed = sum(1 for r in self.results if r["passed"])
        total = len(self.results)
        critical_failed = len(self.critical_failures())

        logger.info("\n" + "=" * 60)
        logger.info(f"Validation Summary: {passed}/{total} checks passed")

        if critical_failed > 0:
            logger.error(f"{critical_failed} critical validation(s) failed!")
            logger.info("\nFailed checks:")
            for result in self.critical_failures():
                logger.error(f"  ✗ {result['check']}: {result['message']}")
        else:
            logger.info("All critical validations passed!")

        logger.info("=" * 60 + "\n")


class KubeconfigValidator:
    """Validates kubeconfig structure and token expiration for switchover contexts.

    Checks for:
    - Duplicate user credentials across merged configs (causes auth failures)
    - Expired or near-expiry SA tokens (parsed from JWT)
    - Connectivity to API servers
    """

    # Warn if token expires within this many hours
    TOKEN_EXPIRY_WARNING_HOURS = 4

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

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
            self.reporter.add_result(
                f"API Connectivity ({hub_label})",
                True,
                f"Successfully connected to {hub_label} hub API server",
                critical=True,
            )
        except Exception as e:
            self.reporter.add_result(
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
                our_contexts = {primary.context_name, secondary.context_name}
                affected = []
                for user, ctxs in duplicates.items():
                    affected_ctxs = [c for c in ctxs if c in our_contexts]
                    if len(affected_ctxs) > 0:
                        affected.append(f"user '{user}' in contexts: {', '.join(ctxs)}")

                if affected:
                    self.reporter.add_result(
                        "Kubeconfig User Names",
                        False,
                        f"Potential credential collision detected: {'; '.join(affected)}. "
                        "Consider regenerating kubeconfigs with unique --user names.",
                        critical=False,  # Warning, not fatal
                    )
                else:
                    self.reporter.add_result(
                        "Kubeconfig User Names",
                        True,
                        "No duplicate user names detected for switchover contexts",
                        critical=False,
                    )
            else:
                self.reporter.add_result(
                    "Kubeconfig User Names",
                    True,
                    "All user names are unique in kubeconfig",
                    critical=False,
                )
        except Exception as e:
            logger.debug("Could not check for duplicate users: %s", str(e))
            # Non-critical, skip if we can't check

    def _check_token_expiration(self, client: KubeClient, hub_label: str) -> None:
        """Check if the token for this context is expired or near expiry.

        Parses JWT token to extract expiration time. Uses direct YAML parsing
        to avoid relying on private kubernetes client internals.

        Supports:
        - Static token authentication (JWT tokens in kubeconfig)

        Warns for unsupported auth types:
        - exec/auth-provider based authentication
        - Client certificate authentication
        """
        try:
            import base64
            import json
            import os

            import yaml

            # Load kubeconfig using direct YAML parsing (public, stable approach)
            kubeconfig_paths = os.environ.get("KUBECONFIG", os.path.expanduser("~/.kube/config"))
            
            # Handle multiple kubeconfig paths (colon-separated)
            kubeconfig_data: Dict[str, Any] = {"users": [], "contexts": [], "clusters": []}
            for kc_path in kubeconfig_paths.split(":"):
                kc_path = kc_path.strip()
                if kc_path and os.path.exists(kc_path):
                    with open(kc_path, "r") as f:
                        kc = yaml.safe_load(f)
                        if kc:
                            kubeconfig_data["users"].extend(kc.get("users", []))
                            kubeconfig_data["contexts"].extend(kc.get("contexts", []))
                            kubeconfig_data["clusters"].extend(kc.get("clusters", []))

            # Find the context and associated user
            context_config = next(
                (c for c in kubeconfig_data.get("contexts", []) if c.get("name") == client.context),
                None,
            )
            if not context_config:
                logger.debug("Context %s not found in kubeconfig", client.context)
                return

            user_name = context_config.get("context", {}).get("user", "")
            if not user_name:
                return

            # Find the user configuration
            user_config = next(
                (u for u in kubeconfig_data.get("users", []) if u.get("name") == user_name),
                None,
            )
            if not user_config:
                return

            user_data = user_config.get("user", {})

            # Check for unsupported auth types and warn explicitly
            if user_data.get("exec"):
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,  # Pass - exec handles its own token refresh
                    "Using exec-based authentication (token refresh handled by exec plugin)",
                    critical=False,
                )
                return

            if user_data.get("auth-provider"):
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,  # Pass - auth-provider handles its own token refresh
                    "Using auth-provider authentication (token refresh handled by provider)",
                    critical=False,
                )
                return

            if user_data.get("client-certificate") or user_data.get("client-certificate-data"):
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,  # Pass - certs have their own expiry but we don't check them here
                    "Using client certificate authentication (certificate expiry not checked)",
                    critical=False,
                )
                return

            # Get the token
            token = user_data.get("token")
            if not token:
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,  # Pass but note
                    "No token found in kubeconfig - authentication method not determined",
                    critical=False,
                )
                return

            # Parse JWT (format: header.payload.signature)
            parts = token.split(".")
            if len(parts) != 3:
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,  # Pass but note
                    "Token is not a JWT - expiration cannot be determined",
                    critical=False,
                )
                return

            # Decode payload (middle part)
            # Add padding if needed for base64 decoding
            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding

            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp_timestamp = payload.get("exp")
            if not exp_timestamp:
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,  # Pass but note
                    "Token has no expiration claim (may be a long-lived token)",
                    critical=False,
                )
                return

            exp_time = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
            now = datetime.now(tz=timezone.utc)

            if exp_time < now:
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    False,
                    f"Token for {hub_label} hub has EXPIRED at {exp_time.isoformat()}. "
                    "Regenerate kubeconfig with fresh token.",
                    critical=True,
                )
            elif (exp_time - now).total_seconds() < self.TOKEN_EXPIRY_WARNING_HOURS * 3600:
                hours_left = (exp_time - now).total_seconds() / 3600
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,  # Pass but warn
                    f"Token for {hub_label} hub expires in {hours_left:.1f} hours. "
                    "Consider regenerating for longer operations.",
                    critical=False,
                )
            else:
                hours_left = (exp_time - now).total_seconds() / 3600
                self.reporter.add_result(
                    f"Token Expiration ({hub_label})",
                    True,
                    f"Token valid for {hours_left:.1f} more hours",
                    critical=False,
                )

        except Exception as e:
            logger.debug("Could not check token expiration for %s: %s", hub_label, str(e))
            # Non-critical - skip if we can't parse token


class ToolingValidator:
    """Validates required command-line tools exist for operator workflows."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self) -> None:
        oc_path = shutil.which("oc")
        kubectl_path = shutil.which("kubectl")

        if oc_path or kubectl_path:
            binary = "oc" if oc_path else "kubectl"
            self.reporter.add_result(
                "Cluster CLI",
                True,
                f"{binary} found in PATH",
                critical=True,
            )
        else:
            self.reporter.add_result(
                "Cluster CLI",
                False,
                "Neither oc nor kubectl found in PATH",
                critical=True,
            )

        jq_path = shutil.which("jq")
        if jq_path:
            self.reporter.add_result(
                "jq availability",
                True,
                "jq found",
                critical=False,
            )
        else:
            self.reporter.add_result(
                "jq availability",
                False,
                "jq not found (optional but recommended)",
                critical=False,
            )


class NamespaceValidator:
    """Ensures required namespaces are present on both hubs."""

    REQUIRED_NAMESPACES: Sequence[str] = (
        ACM_NAMESPACE,
        BACKUP_NAMESPACE,
    )

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, primary: KubeClient, secondary: KubeClient) -> None:
        for namespace in self.REQUIRED_NAMESPACES:
            self._check_namespace(primary, namespace, "primary")
            self._check_namespace(secondary, namespace, "secondary")

    def _check_namespace(
        self,
        kube_client: KubeClient,
        namespace: str,
        hub_label: str,
    ) -> None:
        try:
            # Validate namespace name before checking existence
            InputValidator.validate_kubernetes_namespace(namespace)

            if kube_client.namespace_exists(namespace):
                self.reporter.add_result(
                    f"Namespace {namespace} ({hub_label})",
                    True,
                    "exists",
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    f"Namespace {namespace} ({hub_label})",
                    False,
                    "not found",
                    critical=True,
                )
        except ValidationError as e:
            self.reporter.add_result(
                f"Namespace {namespace} ({hub_label})",
                False,
                f"invalid namespace name: {str(e)}",
                critical=True,
            )


class VersionValidator:
    """Detects ACM versions and ensures they match between hubs."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, primary: KubeClient, secondary: KubeClient) -> Tuple[str, str]:
        primary_version = self._detect_version(primary, "primary")
        secondary_version = self._detect_version(secondary, "secondary")
        self._validate_match(primary_version, secondary_version)
        return primary_version, secondary_version

    def _detect_version(self, kube_client: KubeClient, hub_name: str) -> str:
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
                self.reporter.add_result(
                    f"ACM version ({hub_name})",
                    True,
                    f"detected: {version}",
                    critical=True,
                )
                return version

            self.reporter.add_result(
                f"ACM version ({hub_name})",
                False,
                "MultiClusterHub not found",
                critical=True,
            )
            return "unknown"
        except (RuntimeError, ValueError, Exception) as exc:  # pragma: no cover - kube errors
            self.reporter.add_result(
                f"ACM version ({hub_name})",
                False,
                f"error detecting version: {exc}",
                critical=True,
            )
            return "unknown"

    def _validate_match(self, primary_version: str, secondary_version: str) -> None:
        if "unknown" in (primary_version, secondary_version):
            self.reporter.add_result(
                "ACM version matching",
                False,
                "cannot verify - version detection failed",
                critical=True,
            )
            return

        if primary_version == secondary_version:
            self.reporter.add_result(
                "ACM version matching",
                True,
                f"both hubs running {primary_version}",
                critical=True,
            )
        else:
            self.reporter.add_result(
                "ACM version matching",
                False,
                f"version mismatch - primary: {primary_version}, secondary: {secondary_version}",
                critical=True,
            )


class HubComponentValidator:
    """Validates per-hub components such as OADP and DPA."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, kube_client: KubeClient, hub_label: str) -> None:
        self._check_oadp_operator(kube_client, hub_label)
        self._check_dpa(kube_client, hub_label)

    def _check_oadp_operator(self, kube_client: KubeClient, hub_name: str) -> None:
        try:
            if kube_client.namespace_exists(BACKUP_NAMESPACE):
                pods = kube_client.get_pods(
                    namespace=BACKUP_NAMESPACE,
                    label_selector="app.kubernetes.io/name=velero",
                )

                if pods:
                    self.reporter.add_result(
                        f"OADP operator ({hub_name})",
                        True,
                        f"installed, {len(pods)} Velero pod(s) found",
                        critical=True,
                    )
                else:
                    self.reporter.add_result(
                        f"OADP operator ({hub_name})",
                        False,
                        "namespace exists but no Velero pods found",
                        critical=True,
                    )
            else:
                self.reporter.add_result(
                    f"OADP operator ({hub_name})",
                    False,
                    f"{BACKUP_NAMESPACE} namespace not found",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.reporter.add_result(
                f"OADP operator ({hub_name})",
                False,
                f"error checking OADP: {exc}",
                critical=True,
            )

    def _check_dpa(self, kube_client: KubeClient, hub_name: str) -> None:
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
                    self.reporter.add_result(
                        f"DataProtectionApplication ({hub_name})",
                        True,
                        f"{dpa_name} is reconciled",
                        critical=True,
                    )
                else:
                    self.reporter.add_result(
                        f"DataProtectionApplication ({hub_name})",
                        False,
                        f"{dpa_name} exists but not reconciled",
                        critical=True,
                    )
            else:
                self.reporter.add_result(
                    f"DataProtectionApplication ({hub_name})",
                    False,
                    "no DataProtectionApplication found",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.reporter.add_result(
                f"DataProtectionApplication ({hub_name})",
                False,
                f"error checking DPA: {exc}",
                critical=True,
            )


class BackupValidator:
    """Ensures backups exist and no job is stuck."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def _get_backup_age_info(self, completion_timestamp: str | None) -> str:
        """
        Calculate backup age and return human-readable info with freshness indicator.

        Args:
            completion_timestamp: ISO 8601 timestamp string from backup.status.completionTimestamp

        Returns:
            Human-readable age string with freshness indicator, or empty string if timestamp unavailable
        """
        if not completion_timestamp:
            return ""

        try:
            # Parse ISO 8601 timestamp (Kubernetes format: 2025-12-03T10:15:30Z)
            completion_dt = datetime.fromisoformat(completion_timestamp.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)

            # Calculate age
            age_seconds = int((now_dt - completion_dt).total_seconds())

            # Format human-readable age
            if age_seconds < 60:
                age_display = f"{age_seconds}s"
            elif age_seconds < 3600:
                age_minutes = age_seconds // 60
                age_display = f"{age_minutes}m"
            elif age_seconds < 86400:
                age_hours = age_seconds // 3600
                age_minutes = (age_seconds % 3600) // 60
                age_display = f"{age_hours}h{age_minutes}m"
            else:
                age_days = age_seconds // 86400
                age_hours = (age_seconds % 86400) // 3600
                age_display = f"{age_days}d{age_hours}h"

            # Determine freshness indicator
            if age_seconds < 3600:  # < 1 hour
                freshness = "FRESH"
            elif age_seconds < 86400:  # < 24 hours
                freshness = "acceptable"
            else:  # >= 24 hours
                freshness = "consider running a fresh backup"

            return f"age: {age_display}, {freshness}"
        except (ValueError, AttributeError) as e:
            logger.debug("Failed to parse backup timestamp %s: %s", completion_timestamp, e)
            return ""

    def run(self, primary: KubeClient) -> None:
        try:
            backups = primary.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
            )

            if not backups:
                self.reporter.add_result(
                    "Backup status",
                    False,
                    "no backups found",
                    critical=True,
                )
                return

            backups.sort(
                key=lambda b: b.get("metadata", {}).get("creationTimestamp", ""),
                reverse=True,
            )

            latest_backup = backups[0]
            backup_name = latest_backup.get("metadata", {}).get("name", "unknown")
            phase = latest_backup.get("status", {}).get("phase", "unknown")

            in_progress = [
                b.get("metadata", {}).get("name") for b in backups if b.get("status", {}).get("phase") == "InProgress"
            ]

            if in_progress:
                self.reporter.add_result(
                    "Backup status",
                    False,
                    f"backup(s) in progress: {', '.join(in_progress)}",
                    critical=True,
                )
            elif phase == "Completed":
                # Get backup completion timestamp to calculate age
                completion_ts = latest_backup.get("status", {}).get("completionTimestamp")
                age_info = self._get_backup_age_info(completion_ts)

                message = f"latest backup {backup_name} completed successfully"
                if age_info:
                    message += f" ({age_info})"

                self.reporter.add_result(
                    "Backup status",
                    True,
                    message,
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    "Backup status",
                    False,
                    f"latest backup {backup_name} in unexpected state: {phase}",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.reporter.add_result(
                "Backup status",
                False,
                f"error checking backups: {exc}",
                critical=True,
            )


class BackupScheduleValidator:
    """Validates BackupSchedule has useManagedServiceAccount enabled for passive sync.

    The useManagedServiceAccount setting is critical for passive sync switchover.
    When enabled, the hub creates a ManagedServiceAccount for each managed cluster,
    allowing klusterlet agents to automatically reconnect to the new hub after
    the restore activates managed clusters.

    Without this setting, managed clusters would require manual re-import after
    switchover because the klusterlet bootstrap-hub-kubeconfig still points to
    the old hub.
    """

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, primary: KubeClient) -> None:
        """Check that BackupSchedule has useManagedServiceAccount enabled.

        Args:
            primary: KubeClient for the primary hub
        """
        try:
            # Try to find a BackupSchedule resource
            backup_schedules = primary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                namespace=BACKUP_NAMESPACE,
            )

            if not backup_schedules:
                self.reporter.add_result(
                    "BackupSchedule configuration",
                    False,
                    "no BackupSchedule found - required for passive sync",
                    critical=True,
                )
                return

            # Check the first (typically only) BackupSchedule
            schedule = backup_schedules[0]
            schedule_name = schedule.get("metadata", {}).get("name", BACKUP_SCHEDULE_DEFAULT_NAME)
            spec = schedule.get("spec", {})
            use_msa = spec.get(SPEC_USE_MANAGED_SERVICE_ACCOUNT, False)

            if use_msa:
                self.reporter.add_result(
                    "BackupSchedule configuration",
                    True,
                    f"{schedule_name}: useManagedServiceAccount=true (managed clusters will auto-reconnect)",
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    "BackupSchedule configuration",
                    False,
                    f"{schedule_name}: useManagedServiceAccount is not enabled. "
                    "Managed clusters will NOT auto-reconnect to new hub after switchover. "
                    "Set spec.useManagedServiceAccount=true in BackupSchedule.",
                    critical=True,
                )

        except (RuntimeError, ValueError, Exception) as exc:
            self.reporter.add_result(
                "BackupSchedule configuration",
                False,
                f"error checking BackupSchedule: {exc}",
                critical=True,
            )


class ClusterDeploymentValidator:
    """Verifies preserveOnDelete is set for Hive clusters."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, primary: KubeClient) -> None:
        try:
            cluster_deployments = primary.list_custom_resources(
                group="hive.openshift.io",
                version="v1",
                plural="clusterdeployments",
            )

            if not cluster_deployments:
                self.reporter.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    "no ClusterDeployments found (no Hive-managed clusters)",
                    critical=False,
                )
                return

            missing = []
            for cd in cluster_deployments:
                name = cd.get("metadata", {}).get("name", "unknown")
                namespace = cd.get("metadata", {}).get("namespace", "unknown")
                preserve = cd.get("spec", {}).get("preserveOnDelete", False)
                if not preserve:
                    missing.append(f"{namespace}/{name}")

            if missing:
                self.reporter.add_result(
                    "ClusterDeployment preserveOnDelete",
                    False,
                    "ClusterDeployments missing preserveOnDelete=true: "
                    + ", ".join(missing)
                    + ". This is CRITICAL - deleting these ManagedClusters will DESTROY the underlying infrastructure!",
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    f"all {len(cluster_deployments)} ClusterDeployments have preserveOnDelete=true",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            if "404" in str(exc):
                self.reporter.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    "Hive CRDs not found (no Hive-managed clusters)",
                    critical=False,
                )
            else:
                self.reporter.add_result(
                    "ClusterDeployment preserveOnDelete",
                    False,
                    f"error checking ClusterDeployments: {exc}",
                    critical=True,
                )


class PassiveSyncValidator:
    """Checks the passive synchronization restore object."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, secondary: KubeClient) -> None:
        try:
            # Validate namespace and resource name before using them
            InputValidator.validate_kubernetes_namespace(BACKUP_NAMESPACE)
            InputValidator.validate_kubernetes_name(RESTORE_PASSIVE_SYNC_NAME, "restore")

            restore = secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name=RESTORE_PASSIVE_SYNC_NAME,
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                self.reporter.add_result(
                    "Passive sync restore",
                    False,
                    f"{RESTORE_PASSIVE_SYNC_NAME} not found on secondary hub",
                    critical=True,
                )
                return

            status = restore.get("status", {})
            phase = status.get("phase", "unknown")
            message = status.get("lastMessage", "")

            # "Enabled" = continuous sync running
            # "Finished" = initial sync completed successfully (still valid for switchover)
            if phase in ("Enabled", "Finished"):
                self.reporter.add_result(
                    "Passive sync restore",
                    True,
                    f"passive sync ready ({phase}): {message}",
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    "Passive sync restore",
                    False,
                    f"passive sync in unexpected state: {phase} - {message}",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            self.reporter.add_result(
                "Passive sync restore",
                False,
                f"error checking passive sync: {exc}",
                critical=True,
            )


class ObservabilityDetector:
    """Detects whether ACM Observability is deployed on each hub."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def detect(self, primary: KubeClient, secondary: KubeClient) -> Tuple[bool, bool]:
        try:
            # Validate namespace before checking existence
            InputValidator.validate_kubernetes_namespace(OBSERVABILITY_NAMESPACE)
        except ValidationError:
            # If observability namespace is invalid, it doesn't exist
            logger.debug("Observability namespace validation failed: %s", OBSERVABILITY_NAMESPACE)
            return False, False

        primary_has = primary.namespace_exists(OBSERVABILITY_NAMESPACE)
        secondary_has = secondary.namespace_exists(OBSERVABILITY_NAMESPACE)

        if primary_has and secondary_has:
            self.reporter.add_result(
                "ACM Observability",
                True,
                "detected on both hubs",
                critical=False,
            )
        elif primary_has:
            self.reporter.add_result(
                "ACM Observability",
                True,
                "detected on primary hub only",
                critical=False,
            )
        elif secondary_has:
            self.reporter.add_result(
                "ACM Observability",
                True,
                "detected on secondary hub only",
                critical=False,
            )
        else:
            self.reporter.add_result(
                "ACM Observability",
                True,
                "not detected (optional component)",
                critical=False,
            )

        return primary_has, secondary_has


class ManagedClusterBackupValidator:
    """Validates that all joined ManagedClusters are included in the latest backup."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, primary: KubeClient) -> None:
        """Check that all joined ManagedClusters are in the latest managed-clusters backup."""
        try:
            # Get all joined ManagedClusters (excluding local-cluster)
            managed_clusters = primary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
            )

            joined_clusters = []
            for mc in managed_clusters:
                mc_name = mc.get("metadata", {}).get("name", "unknown")
                if mc_name == "local-cluster":
                    continue

                # Check if cluster is joined (has Joined condition = True)
                conditions = mc.get("status", {}).get("conditions", [])
                is_joined = any(
                    c.get("type") == "ManagedClusterJoined" and c.get("status") == "True" for c in conditions
                )
                if is_joined:
                    joined_clusters.append(mc_name)

            if not joined_clusters:
                self.reporter.add_result(
                    "ManagedClusters in backup",
                    True,
                    "no joined ManagedClusters found (only local-cluster)",
                    critical=False,
                )
                return

            # Find the latest managed-clusters backup
            try:
                # Validate namespace before using it
                InputValidator.validate_kubernetes_namespace(BACKUP_NAMESPACE)
            except ValidationError as e:
                self.reporter.add_result(
                    "ManagedClusters in backup",
                    False,
                    f"invalid backup namespace: {str(e)}",
                    critical=True,
                )
                return

            backups = primary.list_custom_resources(
                group="velero.io",
                version="v1",
                plural="backups",
                namespace=BACKUP_NAMESPACE,
                label_selector="cluster.open-cluster-management.io/backup-schedule-type=managedClusters",
            )

            if not backups:
                self.reporter.add_result(
                    "ManagedClusters in backup",
                    False,
                    f"no managed-clusters backups found, but {len(joined_clusters)} joined cluster(s) exist: {', '.join(joined_clusters)}",
                    critical=True,
                )
                return

            # Sort by creation timestamp to find latest
            backups.sort(
                key=lambda b: b.get("metadata", {}).get("creationTimestamp", ""),
                reverse=True,
            )

            latest_backup = backups[0]
            backup_name = latest_backup.get("metadata", {}).get("name", "unknown")
            backup_time = latest_backup.get("metadata", {}).get("creationTimestamp", "unknown")
            phase = latest_backup.get("status", {}).get("phase", "unknown")

            if phase != "Completed":
                self.reporter.add_result(
                    "ManagedClusters in backup",
                    False,
                    f"latest managed-clusters backup {backup_name} is in state '{phase}', not Completed",
                    critical=True,
                )
                return

            # Check which clusters were created AFTER the backup
            clusters_not_in_backup = []
            for mc in managed_clusters:
                mc_name = mc.get("metadata", {}).get("name", "unknown")
                if mc_name == "local-cluster" or mc_name not in joined_clusters:
                    continue

                mc_created = mc.get("metadata", {}).get("creationTimestamp", "")
                # Compare timestamps (ISO format sorts correctly as strings)
                if mc_created > backup_time:
                    clusters_not_in_backup.append(f"{mc_name} (created {mc_created})")

            if clusters_not_in_backup:
                self.reporter.add_result(
                    "ManagedClusters in backup",
                    False,
                    f"the following ManagedClusters were imported AFTER the latest backup ({backup_name} from {backup_time}): "
                    f"{', '.join(clusters_not_in_backup)}. "
                    f"Run a new backup before switchover or these clusters will NOT be restored on the secondary hub!",
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    "ManagedClusters in backup",
                    True,
                    f"all {len(joined_clusters)} joined ManagedCluster(s) were imported before latest backup ({backup_name} from {backup_time})",
                    critical=True,
                )

        except (RuntimeError, ValueError, Exception) as exc:
            self.reporter.add_result(
                "ManagedClusters in backup",
                False,
                f"error checking ManagedClusters in backup: {exc}",
                critical=True,
            )


class AutoImportStrategyValidator:
    """Validate autoImportStrategy (ACM 2.14+) and provide guidance.

    Behavior is detect-only; never fails preflight critically.
    """

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def _strategy_for(self, client: KubeClient) -> str:
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
        # Primary hub
        if is_acm_version_ge(primary_version, "2.14.0"):
            strategy = self._strategy_for(primary)
            if strategy == "error":
                self.reporter.add_result(
                    "Auto-Import Strategy (primary)",
                    False,
                    "could not retrieve autoImportStrategy (connection or API error)",
                    critical=False,
                )
            elif strategy in ("default", AUTO_IMPORT_STRATEGY_DEFAULT):
                self.reporter.add_result(
                    "Auto-Import Strategy (primary)",
                    True,
                    f"default ({AUTO_IMPORT_STRATEGY_DEFAULT}) in effect",
                    critical=False,
                )
            else:
                self.reporter.add_result(
                    "Auto-Import Strategy (primary)",
                    False,
                    f"non-default strategy in use: {strategy}",
                    critical=False,
                )
        else:
            self.reporter.add_result(
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
                self.reporter.add_result(
                    "Auto-Import Strategy (secondary)",
                    False,
                    "could not retrieve autoImportStrategy (connection or API error)",
                    critical=False,
                )
                # Do not apply further hints if we couldn't read the config
                return
            elif count > 0 and strategy in ("default", AUTO_IMPORT_STRATEGY_DEFAULT):
                self.reporter.add_result(
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
                self.reporter.add_result(
                    "Auto-Import Strategy (secondary)",
                    True,
                    f"{AUTO_IMPORT_STRATEGY_SYNC} set (ensure to reset to default after activation)",
                    critical=False,
                )
            else:
                self.reporter.add_result(
                    "Auto-Import Strategy (secondary)",
                    True,
                    f"default ({AUTO_IMPORT_STRATEGY_DEFAULT}) in effect",
                    critical=False,
                )
        else:
            self.reporter.add_result(
                "Auto-Import Strategy (secondary)",
                True,
                f"ACM {secondary_version} (< 2.14) - not applicable",
                critical=False,
            )


class ObservabilityPrereqValidator:
    """Checks additional Observability requirements on the secondary hub."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, secondary: KubeClient) -> None:
        try:
            # Validate namespace and secret name before checking existence
            InputValidator.validate_kubernetes_namespace(OBSERVABILITY_NAMESPACE)
            InputValidator.validate_kubernetes_name(THANOS_OBJECT_STORAGE_SECRET, "secret")
        except ValidationError as e:
            logger.debug("Observability validation failed: %s", e)
            return

        if not secondary.namespace_exists(OBSERVABILITY_NAMESPACE):
            return

        if secondary.secret_exists(OBSERVABILITY_NAMESPACE, THANOS_OBJECT_STORAGE_SECRET):
            self.reporter.add_result(
                "Observability object storage secret",
                True,
                f"{THANOS_OBJECT_STORAGE_SECRET} present on secondary hub",
                critical=True,
            )
        else:
            self.reporter.add_result(
                "Observability object storage secret",
                False,
                f"{THANOS_OBJECT_STORAGE_SECRET} missing on secondary hub",
                critical=True,
            )
