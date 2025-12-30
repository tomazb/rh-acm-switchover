"""Cluster-related validation checks."""

from typing import Any, Dict, List

from lib.kube_client import KubeClient

from .base_validator import BaseValidator


class ClusterDeploymentValidator(BaseValidator):
    """Verifies preserveOnDelete is set for Hive clusters."""

    def run(self, primary: KubeClient) -> None:
        """Run validation with primary client.

        Args:
            primary: Primary hub KubeClient instance
        """
        try:
            cluster_deployments = primary.list_custom_resources(
                group="hive.openshift.io",
                version="v1",
                plural="clusterdeployments",
            )

            if not cluster_deployments:
                self.add_result(
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
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    False,
                    "ClusterDeployments missing preserveOnDelete=true: "
                    + ", ".join(missing)
                    + ". This is CRITICAL - deleting these ManagedClusters will DESTROY the underlying infrastructure!",
                    critical=True,
                )
            else:
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    f"all {len(cluster_deployments)} ClusterDeployments have preserveOnDelete=true",
                    critical=True,
                )
        except (RuntimeError, ValueError, Exception) as exc:
            if "404" in str(exc):
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    True,
                    "Hive CRDs not found (no Hive-managed clusters)",
                    critical=False,
                )
            else:
                self.add_result(
                    "ClusterDeployment preserveOnDelete",
                    False,
                    f"error checking ClusterDeployments: {exc}",
                    critical=True,
                )
