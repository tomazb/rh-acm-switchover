"""
Decommission module for old primary hub.
"""

# Runbook: Step 14 (decommission) and Rollback references where applicable

import logging

from kubernetes.client.exceptions import ApiException

from lib.constants import (
    ACM_NAMESPACE,
    ACM_OPERATOR_POD_PREFIX,
    DECOMMISSION_POD_INTERVAL,
    DECOMMISSION_POD_TIMEOUT,
    DELETE_REQUEST_TIMEOUT,
    HIVE_CLUSTERDEPLOYMENT_API_GROUP,
    HIVE_CLUSTERDEPLOYMENT_API_VERSION,
    HIVE_CLUSTERDEPLOYMENT_PLURAL,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_DELETE_INTERVAL,
    MANAGED_CLUSTER_DELETE_TIMEOUT,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import confirm_action
from lib.waiter import WaitConditionResult, wait_for_condition

logger = logging.getLogger("acm_switchover")


class Decommission:
    """Handles decommissioning of old primary hub."""

    def __init__(self, primary_client: KubeClient, has_observability: bool, dry_run: bool = False):
        self.primary = primary_client
        self.has_observability = has_observability
        self.dry_run = dry_run

    def decommission(self, interactive: bool = True) -> bool:
        """
        Decommission old primary hub.

        Args:
            interactive: If True, prompt for confirmation at each step

        Returns:
            True if decommission completed successfully
        """
        logger.warning("=" * 60)
        logger.warning("DECOMMISSION MODE - This will remove ACM from the old hub!")
        logger.warning("=" * 60)

        if interactive:
            if not confirm_action(
                "\nAre you sure you want to proceed with decommissioning the old hub?",
                default=False,
            ):
                logger.info("Decommission cancelled by user")
                return False

        try:
            # Step 14.1-14.2: Delete MultiClusterObservability
            if self.has_observability:
                if not interactive or confirm_action("\nDelete MultiClusterObservability resource?", default=False):
                    self._delete_observability()
                else:
                    logger.info("Skipped: Delete MultiClusterObservability")

            # Step 14.3: Delete ManagedClusters
            if not interactive or confirm_action(
                "\nDelete ManagedCluster resources (excluding local-cluster)?",
                default=False,
            ):
                self._delete_managed_clusters()
            else:
                logger.info("Skipped: Delete ManagedClusters")

            # Step 14.4-14.5: Delete MultiClusterHub
            if not interactive or confirm_action(
                "\nDelete MultiClusterHub resource? (This will remove all ACM components)",
                default=False,
            ):
                self._delete_multiclusterhub()
            else:
                logger.info("Skipped: Delete MultiClusterHub")

            if self.dry_run:
                logger.info("[DRY-RUN] Decommission steps completed (no changes made)")

            logger.info("Decommission completed")
            return True

        except SwitchoverError as e:
            logger.error("Decommission failed: %s", e)
            return False
        except Exception as e:
            logger.exception("Unexpected error during decommission: %s", e)
            return False

    def _delete_observability(self):
        """Delete MultiClusterObservability resource."""
        logger.info("Deleting MultiClusterObservability resource...")

        # List all MultiClusterObservability resources
        mcos = self.primary.list_custom_resources(
            group="observability.open-cluster-management.io",
            version="v1beta2",
            plural="multiclusterobservabilities",
        )

        if not mcos:
            logger.info("No MultiClusterObservability resources found")
            return

        for mco in mcos:
            mco_name = mco.get("metadata", {}).get("name")

            if self.dry_run:
                logger.info("[DRY-RUN] Would delete MultiClusterObservability: %s", mco_name)
                continue

            logger.info("Deleting MultiClusterObservability: %s", mco_name)

            self.primary.delete_custom_resource(
                group="observability.open-cluster-management.io",
                version="v1beta2",
                plural="multiclusterobservabilities",
                name=mco_name,
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            )

        if self.dry_run:
            logger.info("[DRY-RUN] Skipping wait for observability termination")
            return

        def _observability_terminated():
            pods = self.primary.get_pods(namespace=OBSERVABILITY_NAMESPACE)
            if not pods:
                return WaitConditionResult.complete("all observability pods terminated")
            return WaitConditionResult.pending(f"{len(pods)} pod(s) remaining")

        success = wait_for_condition(
            "Observability pod termination",
            _observability_terminated,
            timeout=OBSERVABILITY_TERMINATE_TIMEOUT,
            interval=OBSERVABILITY_TERMINATE_INTERVAL,
            logger=logger,
        )

        if not success:
            remaining = self.primary.get_pods(namespace=OBSERVABILITY_NAMESPACE)
            if remaining:
                raise SwitchoverError(f"Observability pods still running after {OBSERVABILITY_TERMINATE_TIMEOUT}s")

    def _delete_managed_clusters(self):
        """Delete ManagedCluster resources (excluding local-cluster)."""
        logger.info("Deleting ManagedCluster resources...")

        managed_clusters = self.primary.list_managed_clusters()

        if not managed_clusters:
            logger.info("No ManagedClusters found")
            return

        delete_targets = []
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")

            # Skip local-cluster
            if mc_name == LOCAL_CLUSTER_NAME:
                logger.info("Skipping local-cluster")
                continue

            delete_targets.append(mc_name)

        if delete_targets and not self.dry_run:
            self._verify_managed_cluster_delete_safety(delete_targets)

        deleted_count = 0
        for mc_name in delete_targets:

            if self.dry_run:
                logger.info("[DRY-RUN] Would delete ManagedCluster: %s", mc_name)
                deleted_count += 1
                continue

            logger.info("Deleting ManagedCluster: %s", mc_name)

            self.primary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1",
                plural="managedclusters",
                name=mc_name,
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            )

            deleted_count += 1

        if self.dry_run:
            logger.info("[DRY-RUN] Would delete %s ManagedCluster(s)", deleted_count)
        else:
            logger.info("Deleted %s ManagedCluster(s)", deleted_count)

        # Wait for ManagedClusters to be fully removed (finalizers to complete)
        # This is required before MCH deletion because the MCH admission webhook
        # rejects deletion when ManagedCluster resources still exist
        if deleted_count > 0 and not self.dry_run:
            logger.info("Waiting for ManagedCluster finalizers to complete...")

            def _managed_clusters_removed():
                remaining = self.primary.list_managed_clusters()
                # Filter out local-cluster
                non_local = [mc for mc in remaining if mc.get("metadata", {}).get("name") != LOCAL_CLUSTER_NAME]
                if not non_local:
                    return WaitConditionResult.complete("all ManagedClusters removed (except local-cluster)")
                names = [mc.get("metadata", {}).get("name") for mc in non_local]
                return WaitConditionResult.pending(f"{len(non_local)} ManagedCluster(s) remaining: {', '.join(names)}")

            success = wait_for_condition(
                "ManagedCluster removal",
                _managed_clusters_removed,
                timeout=MANAGED_CLUSTER_DELETE_TIMEOUT,
                interval=MANAGED_CLUSTER_DELETE_INTERVAL,
                logger=logger,
            )

            if not success:
                raise SwitchoverError(
                    f"ManagedClusters not fully removed after {MANAGED_CLUSTER_DELETE_TIMEOUT}s. "
                    "Cannot proceed with MultiClusterHub deletion."
                )

            logger.info("All ManagedClusters removed successfully")

    def _verify_managed_cluster_delete_safety(self, managed_cluster_names: list[str]) -> None:
        """Verify matching Hive ClusterDeployments are safe before deleting ManagedClusters."""
        try:
            cluster_deployments = self.primary.list_custom_resources(
                group=HIVE_CLUSTERDEPLOYMENT_API_GROUP,
                version=HIVE_CLUSTERDEPLOYMENT_API_VERSION,
                plural=HIVE_CLUSTERDEPLOYMENT_PLURAL,
            )
        except ApiException as exc:
            if exc.status == 404:
                logger.info(
                    "Hive ClusterDeployment API not found; no ClusterDeployments require preserveOnDelete verification"
                )
                return
            raise SwitchoverError(
                "Unable to verify ClusterDeployment preserveOnDelete safety before deleting ManagedClusters: "
                f"API error {exc.status} {exc.reason}"
            ) from exc
        except Exception as exc:
            raise SwitchoverError(
                "Unable to verify ClusterDeployment preserveOnDelete safety before deleting ManagedClusters: " f"{exc}"
            ) from exc

        managed_cluster_name_set = set(managed_cluster_names)
        unsafe_matches = set()
        unverified_relationships = set()
        for cluster_deployment in cluster_deployments:
            matching_cluster_name, unverified_reason = self._cluster_deployment_relationship(
                cluster_deployment,
                managed_cluster_name_set,
            )
            if unverified_reason:
                metadata = cluster_deployment.get("metadata") or {}
                namespace = metadata.get("namespace", "unknown")
                name = metadata.get("name", "unknown")
                unverified_relationships.add(f"{namespace}/{name}: {unverified_reason}")
                continue
            if not matching_cluster_name:
                continue

            metadata = cluster_deployment.get("metadata") or {}
            spec = cluster_deployment.get("spec") or {}
            preserve_on_delete = spec.get("preserveOnDelete", False)
            if not preserve_on_delete:
                namespace = metadata.get("namespace", "unknown")
                name = metadata.get("name", "unknown")
                unsafe_matches.add(f"{matching_cluster_name} ({namespace}/{name})")

        if unverified_relationships:
            raise SwitchoverError(
                "Cannot verify ManagedCluster relationship for Hive ClusterDeployments before deleting "
                "ManagedClusters: "
                f"{', '.join(sorted(unverified_relationships))}. "
                "Review the ClusterDeployment ownership and set explicit clusterName metadata before decommission."
            )

        if unsafe_matches:
            raise SwitchoverError(
                "Cannot delete ManagedClusters because matching Hive ClusterDeployments "
                "do not have spec.preserveOnDelete=true: "
                f"{', '.join(sorted(unsafe_matches))}. Set preserveOnDelete=true before decommission."
            )

        logger.info(
            "Verified ClusterDeployment preserveOnDelete safety for ManagedCluster(s): %s",
            ", ".join(managed_cluster_names),
        )

    @staticmethod
    def _matching_managed_cluster_name(cluster_deployment: dict, managed_cluster_names: set[str]) -> str | None:
        """Return the ManagedCluster name represented by a Hive ClusterDeployment."""
        matching_cluster_name, _ = Decommission._cluster_deployment_relationship(
            cluster_deployment,
            managed_cluster_names,
        )
        return matching_cluster_name

    @staticmethod
    def _cluster_deployment_relationship(
        cluster_deployment: dict,
        managed_cluster_names: set[str],
    ) -> tuple[str | None, str | None]:
        """Classify a ClusterDeployment relationship to a ManagedCluster.

        Returns (matching_cluster_name, unverified_reason). A non-empty unverified_reason
        means the resource has a plausible relationship to a delete target but cannot be
        classified safely enough to proceed.
        """
        metadata = cluster_deployment.get("metadata") or {}
        spec = cluster_deployment.get("spec") or {}
        cluster_metadata = spec.get("clusterMetadata") or {}
        if not isinstance(cluster_metadata, dict):
            cluster_metadata = {}
        cluster_install_ref = spec.get("clusterInstallRef") or {}
        if not isinstance(cluster_install_ref, dict):
            cluster_install_ref = {}

        confirmed_candidates = []
        for source, candidate in (
            ("metadata.name", metadata.get("name")),
            ("spec.clusterName", spec.get("clusterName")),
            ("spec.clusterMetadata.clusterName", cluster_metadata.get("clusterName")),
        ):
            if candidate in managed_cluster_names:
                confirmed_candidates.append((source, candidate))

        namespace = metadata.get("namespace")
        install_ref_name = cluster_install_ref.get("name")
        if namespace in managed_cluster_names and install_ref_name == namespace:
            confirmed_candidates.append(("metadata.namespace/spec.clusterInstallRef.name", namespace))

        confirmed_names = sorted({candidate for _, candidate in confirmed_candidates})
        if len(confirmed_names) == 1:
            confirmed_name = confirmed_names[0]
            conflicting_plausible = []
            if namespace in managed_cluster_names and namespace != confirmed_name:
                conflicting_plausible.append(f"metadata.namespace={namespace}")
            if install_ref_name in managed_cluster_names and install_ref_name != confirmed_name:
                conflicting_plausible.append(f"spec.clusterInstallRef.name={install_ref_name}")
            if conflicting_plausible:
                sources = ", ".join(f"{source}={candidate}" for source, candidate in confirmed_candidates)
                return (
                    None,
                    "conflicting ManagedCluster identifiers " f"({sources}; {', '.join(conflicting_plausible)})",
                )
            return confirmed_names[0], None
        if len(confirmed_names) > 1:
            sources = ", ".join(f"{source}={candidate}" for source, candidate in confirmed_candidates)
            return None, f"conflicting ManagedCluster identifiers ({sources})"

        plausible_sources = []
        if namespace in managed_cluster_names:
            plausible_sources.append(f"metadata.namespace={namespace}")
        if install_ref_name in managed_cluster_names:
            plausible_sources.append(f"spec.clusterInstallRef.name={install_ref_name}")
        if plausible_sources:
            return None, f"plausible but unverified identifier(s) ({', '.join(plausible_sources)})"

        return None, None

    def _delete_multiclusterhub(self):
        """Delete MultiClusterHub resource."""
        logger.info("Deleting MultiClusterHub resource...")

        # Get MultiClusterHub
        mchs = self.primary.list_custom_resources(
            group="operator.open-cluster-management.io",
            version="v1",
            plural="multiclusterhubs",
            namespace=ACM_NAMESPACE,
        )

        if not mchs:
            logger.info("No MultiClusterHub resources found (already deleted or never created)")
            logger.info(
                "Note: ACM operator pods (%s-*) may still be running - "
                "this is expected as the operator is installed separately",
                ACM_OPERATOR_POD_PREFIX,
            )
            return

        for mch in mchs:
            mch_name = mch.get("metadata", {}).get("name")

            if self.dry_run:
                logger.info("[DRY-RUN] Would delete MultiClusterHub: %s", mch_name)
                continue

            logger.info("Deleting MultiClusterHub: %s", mch_name)
            logger.info("This may take up to 20 minutes...")

            self.primary.delete_custom_resource(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                name=mch_name,
                namespace=ACM_NAMESPACE,
                timeout_seconds=DELETE_REQUEST_TIMEOUT,
            )

        if self.dry_run:
            logger.info("[DRY-RUN] Skipping wait for ACM pod removal")
            return

        def _acm_pods_removed():
            """Check if ACM pods are removed (excluding operator pods which remain)."""
            pods = self.primary.get_pods(namespace=ACM_NAMESPACE)
            if not pods:
                return WaitConditionResult.complete("all ACM pods removed")
            # Filter out operator pods - they remain after MCH deletion
            non_operator_pods = [
                p for p in pods if not p.get("metadata", {}).get("name", "").startswith(ACM_OPERATOR_POD_PREFIX)
            ]
            if not non_operator_pods:
                operator_count = len(pods)
                return WaitConditionResult.complete(
                    f"all ACM pods removed (except {operator_count} operator pod(s) which remain)"
                )
            return WaitConditionResult.pending(f"{len(non_operator_pods)} non-operator pod(s) remaining")

        success = wait_for_condition(
            "ACM pod removal",
            _acm_pods_removed,
            timeout=DECOMMISSION_POD_TIMEOUT,
            interval=DECOMMISSION_POD_INTERVAL,
            logger=logger,
        )

        if not success:
            logger.warning(
                "Some ACM pods still running after %ss",
                DECOMMISSION_POD_TIMEOUT,
            )
        else:
            logger.info(
                "ACM components removed. Operator pods (%s-*) remain as expected.",
                ACM_OPERATOR_POD_PREFIX,
            )

        logger.info("Decommission complete. Backup data in object storage remains available for the new hub.")
