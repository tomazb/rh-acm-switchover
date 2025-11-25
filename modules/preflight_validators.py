"""Helper classes for ACM pre-flight validation."""

from __future__ import annotations

import logging
import shutil
from typing import Any, Dict, List, Sequence, Tuple

from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
    THANOS_OBJECT_STORAGE_SECRET,
)
from lib.kube_client import KubeClient

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
        except Exception as exc:  # pragma: no cover - kube errors
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
            if kube_client.namespace_exists("openshift-adp"):
                pods = kube_client.get_pods(
                    namespace="openshift-adp",
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
                    "openshift-adp namespace not found",
                    critical=True,
                )
        except Exception as exc:
            self.reporter.add_result(
                f"OADP operator ({hub_name})",
                False,
                f"error checking OADP: {exc}",
                critical=True,
            )

    def _check_dpa(self, kube_client: KubeClient, hub_name: str) -> None:
        try:
            dpas = kube_client.list_custom_resources(
                group="oadp.openshift.io",
                version="v1alpha1",
                plural="dataprotectionapplications",
                namespace="openshift-adp",
            )

            if dpas:
                dpa = dpas[0]
                dpa_name = dpa.get("metadata", {}).get("name", "unknown")
                conditions = dpa.get("status", {}).get("conditions", [])
                reconciled = any(
                    c.get("type") == "Reconciled" and c.get("status") == "True"
                    for c in conditions
                )

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
        except Exception as exc:
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

    def run(self, primary: KubeClient) -> None:
        try:
            backups = primary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
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
                b.get("metadata", {}).get("name")
                for b in backups
                if b.get("status", {}).get("phase") == "InProgress"
            ]

            if in_progress:
                self.reporter.add_result(
                    "Backup status",
                    False,
                    f"backup(s) in progress: {', '.join(in_progress)}",
                    critical=True,
                )
            elif phase == "Finished":
                self.reporter.add_result(
                    "Backup status",
                    True,
                    f"latest backup {backup_name} completed successfully",
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    "Backup status",
                    False,
                    f"latest backup {backup_name} in unexpected state: {phase}",
                    critical=True,
                )
        except Exception as exc:
            self.reporter.add_result(
                "Backup status",
                False,
                f"error checking backups: {exc}",
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
        except Exception as exc:
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
            restore = secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name="restore-acm-passive-sync",
                namespace=BACKUP_NAMESPACE,
            )

            if not restore:
                self.reporter.add_result(
                    "Passive sync restore",
                    False,
                    "restore-acm-passive-sync not found on secondary hub",
                    critical=True,
                )
                return

            status = restore.get("status", {})
            phase = status.get("phase", "unknown")
            message = status.get("lastMessage", "")

            if phase == "Enabled":
                self.reporter.add_result(
                    "Passive sync restore",
                    True,
                    f"passive sync enabled and running: {message}",
                    critical=True,
                )
            else:
                self.reporter.add_result(
                    "Passive sync restore",
                    False,
                    f"passive sync in unexpected state: {phase} - {message}",
                    critical=True,
                )
        except Exception as exc:
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


class ObservabilityPrereqValidator:
    """Checks additional Observability requirements on the secondary hub."""

    def __init__(self, reporter: ValidationReporter) -> None:
        self.reporter = reporter

    def run(self, secondary: KubeClient) -> None:
        if not secondary.namespace_exists(OBSERVABILITY_NAMESPACE):
            return

        if secondary.secret_exists(
            OBSERVABILITY_NAMESPACE, THANOS_OBJECT_STORAGE_SECRET
        ):
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
