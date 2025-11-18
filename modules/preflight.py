"""
Pre-flight validation module for ACM switchover.
"""

import logging
from typing import Dict, List, Tuple

from lib.kube_client import KubeClient
from lib.utils import is_acm_version_ge

logger = logging.getLogger("acm_switchover")


class ValidationError(Exception):
    """Validation check failed."""
    pass


class PreflightValidator:
    """Performs comprehensive pre-flight validation checks."""
    
    def __init__(
        self,
        primary_client: KubeClient,
        secondary_client: KubeClient,
        method: str = "passive"
    ):
        self.primary = primary_client
        self.secondary = secondary_client
        self.method = method
        self.validation_results = []
        
    def add_result(self, check: str, passed: bool, message: str, critical: bool = True):
        """Record validation result."""
        self.validation_results.append({
            "check": check,
            "passed": passed,
            "message": message,
            "critical": critical
        })
        
        if passed:
            logger.info(f"✓ {check}: {message}")
        elif critical:
            logger.error(f"✗ {check}: {message}")
        else:
            logger.warning(f"⚠ {check}: {message}")
    
    def validate_all(self) -> bool:
        """
        Run all validation checks.
        
        Returns:
            True if all critical validations pass
        """
        logger.info("Starting pre-flight validation...")
        
        # Check namespace existence
        self._check_namespaces()
        
        # Detect ACM version
        primary_version = self._detect_acm_version(self.primary, "primary")
        secondary_version = self._detect_acm_version(self.secondary, "secondary")
        
        # Validate version matching
        self._validate_version_matching(primary_version, secondary_version)
        
        # Check OADP operator
        self._check_oadp_operator(self.primary, "primary")
        self._check_oadp_operator(self.secondary, "secondary")
        
        # Check DataProtectionApplication
        self._check_dpa(self.primary, "primary")
        self._check_dpa(self.secondary, "secondary")
        
        # Check backup status
        self._check_backup_status()
        
        # CRITICAL: Check ClusterDeployment preserveOnDelete
        self._check_cluster_deployments_preserve()
        
        # Check passive sync (Method 1 only)
        if self.method == "passive":
            self._check_passive_sync()
        
        # Detect optional components
        has_observability = self._detect_observability()
        
        # Print summary
        self._print_summary()
        
        # Check if any critical validation failed
        critical_failures = [r for r in self.validation_results if not r["passed"] and r["critical"]]
        
        return len(critical_failures) == 0, {
            "primary_version": primary_version,
            "secondary_version": secondary_version,
            "has_observability": has_observability
        }
    
    def _check_namespaces(self):
        """Check required namespaces exist."""
        required_ns = [
            "open-cluster-management",
            "open-cluster-management-backup"
        ]
        
        for ns in required_ns:
            # Check primary
            if self.primary.namespace_exists(ns):
                self.add_result(
                    f"Namespace {ns} (primary)",
                    True,
                    "exists",
                    critical=True
                )
            else:
                self.add_result(
                    f"Namespace {ns} (primary)",
                    False,
                    "not found",
                    critical=True
                )
            
            # Check secondary
            if self.secondary.namespace_exists(ns):
                self.add_result(
                    f"Namespace {ns} (secondary)",
                    True,
                    "exists",
                    critical=True
                )
            else:
                self.add_result(
                    f"Namespace {ns} (secondary)",
                    False,
                    "not found",
                    critical=True
                )
    
    def _detect_acm_version(self, kube_client: KubeClient, hub_name: str) -> str:
        """Detect ACM version from MultiClusterHub resource."""
        try:
            mch = kube_client.get_custom_resource(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                name="multiclusterhub",
                namespace="open-cluster-management"
            )
            
            if not mch:
                # Try listing
                mchs = kube_client.list_custom_resources(
                    group="operator.open-cluster-management.io",
                    version="v1",
                    plural="multiclusterhubs",
                    namespace="open-cluster-management"
                )
                if mchs:
                    mch = mchs[0]
            
            if mch:
                version = mch.get('status', {}).get('currentVersion', 'unknown')
                self.add_result(
                    f"ACM version ({hub_name})",
                    True,
                    f"detected: {version}",
                    critical=True
                )
                return version
            else:
                self.add_result(
                    f"ACM version ({hub_name})",
                    False,
                    "MultiClusterHub not found",
                    critical=True
                )
                return "unknown"
        except Exception as e:
            self.add_result(
                f"ACM version ({hub_name})",
                False,
                f"error detecting version: {e}",
                critical=True
            )
            return "unknown"
    
    def _validate_version_matching(self, primary_version: str, secondary_version: str):
        """Validate ACM versions match between hubs."""
        if primary_version == "unknown" or secondary_version == "unknown":
            self.add_result(
                "ACM version matching",
                False,
                "cannot verify - version detection failed",
                critical=True
            )
            return
        
        if primary_version == secondary_version:
            self.add_result(
                "ACM version matching",
                True,
                f"both hubs running {primary_version}",
                critical=True
            )
        else:
            self.add_result(
                "ACM version matching",
                False,
                f"version mismatch - primary: {primary_version}, secondary: {secondary_version}",
                critical=True
            )
    
    def _check_oadp_operator(self, kube_client: KubeClient, hub_name: str):
        """Check OADP operator is installed."""
        try:
            # Check for OADP namespace
            if kube_client.namespace_exists("openshift-adp"):
                # Check for Velero deployment
                pods = kube_client.get_pods(
                    namespace="openshift-adp",
                    label_selector="app.kubernetes.io/name=velero"
                )
                
                if pods:
                    self.add_result(
                        f"OADP operator ({hub_name})",
                        True,
                        f"installed, {len(pods)} Velero pod(s) found",
                        critical=True
                    )
                else:
                    self.add_result(
                        f"OADP operator ({hub_name})",
                        False,
                        "namespace exists but no Velero pods found",
                        critical=True
                    )
            else:
                self.add_result(
                    f"OADP operator ({hub_name})",
                    False,
                    "openshift-adp namespace not found",
                    critical=True
                )
        except Exception as e:
            self.add_result(
                f"OADP operator ({hub_name})",
                False,
                f"error checking OADP: {e}",
                critical=True
            )
    
    def _check_dpa(self, kube_client: KubeClient, hub_name: str):
        """Check DataProtectionApplication is configured."""
        try:
            dpas = kube_client.list_custom_resources(
                group="oadp.openshift.io",
                version="v1alpha1",
                plural="dataprotectionapplications",
                namespace="openshift-adp"
            )
            
            if dpas:
                dpa = dpas[0]
                dpa_name = dpa.get('metadata', {}).get('name', 'unknown')
                
                # Check if reconciled
                conditions = dpa.get('status', {}).get('conditions', [])
                reconciled = any(
                    c.get('type') == 'Reconciled' and c.get('status') == 'True'
                    for c in conditions
                )
                
                if reconciled:
                    self.add_result(
                        f"DataProtectionApplication ({hub_name})",
                        True,
                        f"{dpa_name} is reconciled",
                        critical=True
                    )
                else:
                    self.add_result(
                        f"DataProtectionApplication ({hub_name})",
                        False,
                        f"{dpa_name} exists but not reconciled",
                        critical=True
                    )
            else:
                self.add_result(
                    f"DataProtectionApplication ({hub_name})",
                    False,
                    "no DataProtectionApplication found",
                    critical=True
                )
        except Exception as e:
            self.add_result(
                f"DataProtectionApplication ({hub_name})",
                False,
                f"error checking DPA: {e}",
                critical=True
            )
    
    def _check_backup_status(self):
        """Check backup status on primary hub."""
        try:
            backups = self.primary.list_custom_resources(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backups",
                namespace="open-cluster-management-backup"
            )
            
            if not backups:
                self.add_result(
                    "Backup status",
                    False,
                    "no backups found",
                    critical=True
                )
                return
            
            # Sort by creation timestamp
            backups.sort(
                key=lambda b: b.get('metadata', {}).get('creationTimestamp', ''),
                reverse=True
            )
            
            latest_backup = backups[0]
            backup_name = latest_backup.get('metadata', {}).get('name', 'unknown')
            status = latest_backup.get('status', {})
            phase = status.get('phase', 'unknown')
            
            # Check if any backup is in progress
            in_progress_backups = [
                b.get('metadata', {}).get('name')
                for b in backups
                if b.get('status', {}).get('phase') == 'InProgress'
            ]
            
            if in_progress_backups:
                self.add_result(
                    "Backup status",
                    False,
                    f"backup(s) in progress: {', '.join(in_progress_backups)}",
                    critical=True
                )
            elif phase == 'Finished':
                self.add_result(
                    "Backup status",
                    True,
                    f"latest backup {backup_name} completed successfully",
                    critical=True
                )
            else:
                self.add_result(
                    "Backup status",
                    False,
                    f"latest backup {backup_name} in unexpected state: {phase}",
                    critical=True
                )
        except Exception as e:
            self.add_result(
                "Backup status",
                False,
                f"error checking backups: {e}",
                critical=True
            )
    
    def _check_cluster_deployments_preserve(self):
        """CRITICAL: Check all ClusterDeployments have spec.preserveOnDelete=true."""
        try:
            cluster_deployments = self.primary.list_custom_resources(
                group="hive.openshift.io",
                version="v1",
                plural="clusterdeployments"
            )
            
            if not cluster_deployments:
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    "no ClusterDeployments found (no Hive-managed clusters)",
                    critical=False
                )
                return
            
            missing_preserve = []
            for cd in cluster_deployments:
                name = cd.get('metadata', {}).get('name', 'unknown')
                namespace = cd.get('metadata', {}).get('namespace', 'unknown')
                preserve = cd.get('spec', {}).get('preserveOnDelete', False)
                
                if not preserve:
                    missing_preserve.append(f"{namespace}/{name}")
            
            if missing_preserve:
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    False,
                    f"ClusterDeployments missing preserveOnDelete=true: {', '.join(missing_preserve)}. " +
                    "This is CRITICAL - deleting these ManagedClusters will DESTROY the underlying infrastructure!",
                    critical=True
                )
            else:
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    f"all {len(cluster_deployments)} ClusterDeployments have preserveOnDelete=true",
                    critical=True
                )
        except Exception as e:
            # If Hive CRD doesn't exist, that's OK (no Hive-managed clusters)
            if "404" in str(e):
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    "Hive CRDs not found (no Hive-managed clusters)",
                    critical=False
                )
            else:
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    False,
                    f"error checking ClusterDeployments: {e}",
                    critical=True
                )
    
    def _check_passive_sync(self):
        """Check passive sync restore status (Method 1 only)."""
        try:
            restore = self.secondary.get_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="restores",
                name="restore-acm-passive-sync",
                namespace="open-cluster-management-backup"
            )
            
            if not restore:
                self.add_result(
                    "Passive sync restore",
                    False,
                    "restore-acm-passive-sync not found on secondary hub",
                    critical=True
                )
                return
            
            status = restore.get('status', {})
            phase = status.get('phase', 'unknown')
            message = status.get('lastMessage', '')
            
            if phase == 'Enabled':
                self.add_result(
                    "Passive sync restore",
                    True,
                    f"passive sync enabled and running: {message}",
                    critical=True
                )
            else:
                self.add_result(
                    "Passive sync restore",
                    False,
                    f"passive sync in unexpected state: {phase} - {message}",
                    critical=True
                )
        except Exception as e:
            self.add_result(
                "Passive sync restore",
                False,
                f"error checking passive sync: {e}",
                critical=True
            )
    
    def _detect_observability(self) -> bool:
        """Detect if ACM Observability is deployed."""
        # Check on both hubs
        primary_has_obs = self.primary.namespace_exists("open-cluster-management-observability")
        secondary_has_obs = self.secondary.namespace_exists("open-cluster-management-observability")
        
        if primary_has_obs and secondary_has_obs:
            self.add_result(
                "ACM Observability",
                True,
                "detected on both hubs",
                critical=False
            )
            return True
        elif primary_has_obs:
            self.add_result(
                "ACM Observability",
                True,
                "detected on primary hub only",
                critical=False
            )
            return True
        elif secondary_has_obs:
            self.add_result(
                "ACM Observability",
                True,
                "detected on secondary hub only",
                critical=False
            )
            return True
        else:
            self.add_result(
                "ACM Observability",
                True,
                "not detected (optional component)",
                critical=False
            )
            return False
    
    def _print_summary(self):
        """Print validation summary."""
        passed = sum(1 for r in self.validation_results if r["passed"])
        total = len(self.validation_results)
        critical_failed = sum(1 for r in self.validation_results if not r["passed"] and r["critical"])
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Validation Summary: {passed}/{total} checks passed")
        
        if critical_failed > 0:
            logger.error(f"{critical_failed} critical validation(s) failed!")
            logger.info("\nFailed checks:")
            for result in self.validation_results:
                if not result["passed"] and result["critical"]:
                    logger.error(f"  ✗ {result['check']}: {result['message']}")
        else:
            logger.info("All critical validations passed!")
        
        logger.info(f"{'='*60}\n")
