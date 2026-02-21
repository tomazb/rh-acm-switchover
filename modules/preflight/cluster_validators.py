"""Cluster-related validation checks."""

import logging

from kubernetes.client.exceptions import ApiException

from lib.gitops_detector import record_gitops_markers
from lib.kube_client import KubeClient

from .base_validator import BaseValidator

logger = logging.getLogger("acm_switchover")


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
                metadata = cd.get("metadata", {})
                name = metadata.get("name", "unknown")
                namespace = metadata.get("namespace", "unknown")
                preserve = cd.get("spec", {}).get("preserveOnDelete", False)

                # Record GitOps markers if present (non-critical)
                try:
                    record_gitops_markers(
                        context="primary",
                        namespace=namespace,
                        kind="ClusterDeployment",
                        name=name,
                        metadata=metadata,
                    )
                except Exception as exc:
                    logger.warning(
                        "GitOps marker recording failed for ClusterDeployment %s/%s: %s",
                        namespace,
                        name,
                        exc,
                    )

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
        except ApiException as exc:
            if exc.status == 404:
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
                    f"API error checking ClusterDeployments: {exc.status} {exc.reason}",
                    critical=True,
                )
        except Exception as exc:
            self.add_result(
                "ClusterDeployment preserveOnDelete",
                False,
                f"error checking ClusterDeployments: {exc}",
                critical=True,
            )
