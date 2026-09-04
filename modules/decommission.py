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
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_DELETE_INTERVAL,
    MANAGED_CLUSTER_DELETE_TIMEOUT,
    MANAGED_CLUSTER_PLURAL,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_TERMINATE_INTERVAL,
    OBSERVABILITY_TERMINATE_TIMEOUT,
)
from lib.decommission_outcome import (
    UNSUCCESSFUL_OUTCOMES,
    DecommissionResult,
    SubstepExecution,
    SubstepOutcome,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.run_record import RunRecord
from lib.utils import confirm_action
from lib.waiter import WaitConditionResult, format_public_list, wait_for_condition

logger = logging.getLogger("acm_switchover")


class Decommission:
    """Handles decommissioning of old primary hub."""

    #: The decommission substeps, in execution order. Each name appears exactly
    #: once here and is the key used in prompts, dispatch, and the result.
    _SUBSTEPS = ("observability", "managed_clusters", "multiclusterhub")

    _PROMPTS = {
        "observability": "\nDelete MultiClusterObservability resource?",
        "managed_clusters": "\nDelete ManagedCluster resources (excluding local-cluster)?",
        "multiclusterhub": "\nDelete MultiClusterHub resource? (This will remove all ACM components)",
    }

    def __init__(
        self,
        primary_client: KubeClient,
        has_observability: bool,
        *,
        run_record: RunRecord,
        dry_run: bool = False,
    ) -> None:
        self.primary = primary_client
        self.has_observability = has_observability
        # Keyword-only and required on purpose: a default would let a caller
        # silently opt out of the durable channel. This task opens the channel;
        # it writes no teardown record through it yet.
        self.run_record = run_record
        self.dry_run = dry_run

    def decommission(self, interactive: bool = True) -> DecommissionResult:
        """Run the requested teardown substeps.

        A refusal aborts the remaining substeps and yields an unsuccessful
        result; it is never persisted (it ends the run, and the summary is
        output rather than state).

        This aggregator deliberately handles nothing. An expected operational
        failure arrives as SubstepExecution(FAILED, changed=...) on the one
        execution-result channel, so the mutation a failing substep actually
        performed is aggregated before the early return. A programming error
        propagates instead of being laundered into a handled outcome
        (IV-R403-01).
        """
        self._log_decommission_banner()

        if interactive and not confirm_action(
            "\nAre you sure you want to proceed with decommissioning the old hub?",
            default=False,
        ):
            logger.info("Decommission cancelled by user")
            return DecommissionResult(
                substeps={},
                not_attempted=self._requested_substeps(),
                cancelled=True,
                changed=False,
                would_change=False,
            )

        if self.dry_run:
            # Read-only prediction. It records no outcome, no teardown record and
            # no phase, calls no delete primitive, and no later live run consumes
            # the answer.
            requested = self._requested_substeps()
            predicted = [self._preview_substep(substep) for substep in requested]
            logger.info("[DRY-RUN] Decommission preview complete (no changes made)")
            return DecommissionResult(
                substeps={},
                not_attempted=requested,
                changed=False,
                would_change=any(predicted),
            )

        outcomes: dict[str, SubstepOutcome] = {}
        changed = False
        for index, substep in enumerate(self._SUBSTEPS):
            if not self._substep_requested(substep):
                outcomes[substep] = SubstepOutcome.NOT_REQUESTED
                continue

            if interactive and not confirm_action(self._PROMPTS[substep], default=False):
                logger.error("Refused by operator: %s. Aborting decommission.", substep)
                outcomes[substep] = SubstepOutcome.REFUSED
                return DecommissionResult(
                    substeps=outcomes,
                    not_attempted=self._remaining_after(index),
                    changed=changed,
                )

            # The ONE execution-result channel.
            execution = self._run_substep(substep)

            outcomes[substep] = execution.outcome
            # Monotonic: once true in this invocation it stays true, on every
            # exit path including the early returns below.
            changed = changed or execution.changed

            if execution.outcome in UNSUCCESSFUL_OUTCOMES:
                return DecommissionResult(
                    substeps=outcomes,
                    not_attempted=self._remaining_after(index),
                    changed=changed,
                )

        logger.info("Decommission completed")
        return DecommissionResult(substeps=outcomes, not_attempted=(), changed=changed)

    @staticmethod
    def _log_decommission_banner() -> None:
        """Warn loudly before anything destructive is considered."""
        logger.warning("=" * 60)
        logger.warning("DECOMMISSION MODE - This will remove ACM from the old hub!")
        logger.warning("=" * 60)

    def _substep_requested(self, substep: str) -> bool:
        """Whether configuration asks for this substep at all."""
        if substep == "observability":
            return self.has_observability
        return True

    def _requested_substeps(self) -> tuple[str, ...]:
        return tuple(substep for substep in self._SUBSTEPS if self._substep_requested(substep))

    def _remaining_after(self, index: int) -> tuple[str, ...]:
        """The requested substeps after ``index``, which an abort leaves unattempted."""
        return tuple(substep for substep in self._SUBSTEPS[index + 1 :] if self._substep_requested(substep))

    def _run_substep(self, substep: str) -> SubstepExecution:
        """Dispatch one substep to its family method and return its execution unchanged.

        No exception handling of its own: each family method converts its own
        expected operational failures into a FAILED execution, at the point where
        it knows whether a delete was accepted.
        """
        dispatch = {
            "observability": self._delete_observability,
            "managed_clusters": self._delete_managed_clusters,
            "multiclusterhub": self._delete_multiclusterhub,
        }
        return dispatch[substep]()

    def _preview_substep(self, substep: str) -> bool:
        """Fresh read-only prediction: would this substep delete anything?

        Reads only. It issues no delete, waits for nothing and writes no
        RunRecord, so a dry run leaves behind no result or state authority.
        """
        if substep == "observability":
            names = self._resource_names(
                self.primary.list_custom_resources(
                    group="observability.open-cluster-management.io",
                    version="v1beta2",
                    plural="multiclusterobservabilities",
                )
            )
            if names:
                logger.info("[DRY-RUN] Would delete MultiClusterObservability: %s", format_public_list(names))
            return bool(names)

        if substep == "managed_clusters":
            names = [
                name
                for name in self._resource_names(self.primary.list_managed_clusters())
                if name != LOCAL_CLUSTER_NAME
            ]
            if names:
                logger.info("[DRY-RUN] Would delete %s ManagedCluster(s): %s", len(names), format_public_list(names))
            return bool(names)

        if substep == "multiclusterhub":
            names = self._resource_names(
                self.primary.list_custom_resources(
                    group="operator.open-cluster-management.io",
                    version="v1",
                    plural="multiclusterhubs",
                    namespace=ACM_NAMESPACE,
                )
            )
            if names:
                logger.info("[DRY-RUN] Would delete MultiClusterHub: %s", format_public_list(names))
            return bool(names)

        raise KeyError(substep)

    @staticmethod
    def _resource_names(resources) -> list:
        """Names of the listed resources, tolerating a None list answer."""
        return [resource.get("metadata", {}).get("name") for resource in resources or []]

    def _delete_observability(self) -> SubstepExecution:
        """Delete MultiClusterObservability resources.

        Family method on the one execution-result channel: an expected
        SwitchoverError-class failure becomes a FAILED execution here, where
        whether a delete was accepted is known.
        """
        logger.info("Deleting MultiClusterObservability resource...")
        changed = False

        try:
            # List all MultiClusterObservability resources
            mcos = self.primary.list_custom_resources(
                group="observability.open-cluster-management.io",
                version="v1beta2",
                plural="multiclusterobservabilities",
            )

            if not mcos:
                # PR C makes this absence proof strict; today it is the existing
                # non-strict list, taken at face value.
                logger.info("No MultiClusterObservability resources found")
                return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP)

            for mco in mcos:
                mco_name = mco.get("metadata", {}).get("name")

                logger.info("Deleting MultiClusterObservability: %s", mco_name)

                try:
                    self.primary.delete_custom_resource(
                        group="observability.open-cluster-management.io",
                        version="v1beta2",
                        plural="multiclusterobservabilities",
                        name=mco_name,
                        timeout_seconds=DELETE_REQUEST_TIMEOUT,
                    )
                    changed = True
                except ApiException as exc:
                    if exc.status == 404:
                        # Already gone: no mutation was performed by this invocation.
                        logger.info("MultiClusterObservability %s already gone (404), treating as success", mco_name)
                    else:
                        # Convert at the raise site so the failure travels the one
                        # execution-result channel and this invocation's aggregated
                        # `changed` reaches the caller. Status and reason only: the
                        # raw HTTP body must never reach a log or the state file.
                        raise SwitchoverError(
                            f"Failed to delete MultiClusterObservability {mco_name}: "
                            f"API error {exc.status} {exc.reason}"
                        ) from exc

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
        except SwitchoverError as exc:
            logger.error("Observability teardown failed: %s", exc)
            return SubstepExecution(SubstepOutcome.FAILED, changed=changed)

        return SubstepExecution(SubstepOutcome.COMPLETED, changed=changed)

    def _delete_managed_clusters(self) -> SubstepExecution:
        """Delete ManagedCluster resources (excluding local-cluster).

        Family method on the one execution-result channel: an expected
        SwitchoverError-class failure -- the Hive preserveOnDelete safety gate or
        the finalizer wait -- becomes a FAILED execution here, carrying whatever
        this invocation actually deleted.
        """
        logger.info("Deleting ManagedCluster resources...")
        changed = False

        try:
            managed_clusters = self.primary.list_managed_clusters()

            if not managed_clusters:
                # PR D makes this absence proof strict; today it is the existing
                # non-strict list, taken at face value.
                logger.info("No ManagedClusters found")
                return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP)

            delete_targets = []
            for mc in managed_clusters:
                mc_name = mc.get("metadata", {}).get("name")

                # Skip local-cluster
                if mc_name == LOCAL_CLUSTER_NAME:
                    logger.info("Skipping local-cluster")
                    continue

                delete_targets.append(mc_name)

            if delete_targets:
                self._verify_managed_cluster_delete_safety(delete_targets)

            deleted_count = 0
            for mc_name in delete_targets:
                logger.info("Deleting ManagedCluster: %s", mc_name)

                try:
                    self.primary.delete_custom_resource(
                        group=MANAGED_CLUSTER_API_GROUP,
                        version=MANAGED_CLUSTER_API_VERSION,
                        plural=MANAGED_CLUSTER_PLURAL,
                        name=mc_name,
                        timeout_seconds=DELETE_REQUEST_TIMEOUT,
                    )
                except ApiException as exc:
                    # Status and reason only, converted here so the deletes already
                    # accepted in this invocation are still reported.
                    raise SwitchoverError(
                        f"Failed to delete ManagedCluster {mc_name}: API error {exc.status} {exc.reason}"
                    ) from exc
                changed = True

                deleted_count += 1

            logger.info("Deleted %s ManagedCluster(s)", deleted_count)

            # Wait for ManagedClusters to be fully removed (finalizers to complete)
            # This is required before MCH deletion because the MCH admission webhook
            # rejects deletion when ManagedCluster resources still exist
            if deleted_count > 0:
                logger.info("Waiting for ManagedCluster finalizers to complete...")

                def _managed_clusters_removed():
                    remaining = self.primary.list_managed_clusters()
                    # Filter out local-cluster
                    non_local = [mc for mc in remaining if mc.get("metadata", {}).get("name") != LOCAL_CLUSTER_NAME]
                    if not non_local:
                        return WaitConditionResult.complete("all ManagedClusters removed (except local-cluster)")
                    names = [mc.get("metadata", {}).get("name") for mc in non_local]
                    return WaitConditionResult.pending(
                        f"{len(non_local)} ManagedCluster(s) remaining: {format_public_list(names)}"
                    )

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
        except SwitchoverError as exc:
            logger.error("ManagedCluster teardown failed: %s", exc)
            return SubstepExecution(SubstepOutcome.FAILED, changed=changed)

        return SubstepExecution(SubstepOutcome.COMPLETED, changed=changed)

    def _verify_managed_cluster_delete_safety(self, managed_cluster_names: list[str]) -> None:
        """Verify matching Hive ClusterDeployments are safe before deleting ManagedClusters."""
        try:
            cluster_deployments = self.primary.list_custom_resources(
                group=HIVE_CLUSTERDEPLOYMENT_API_GROUP,
                version=HIVE_CLUSTERDEPLOYMENT_API_VERSION,
                plural=HIVE_CLUSTERDEPLOYMENT_PLURAL,
            )
        except ApiException as exc:
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
            format_public_list(managed_cluster_names),
        )

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

    def _delete_multiclusterhub(self) -> SubstepExecution:
        """Delete the MultiClusterHub resource.

        Family method on the one execution-result channel: an expected
        SwitchoverError-class failure -- a rejected delete -- becomes a FAILED
        execution here, carrying whatever this invocation actually deleted. The
        pod-removal wait only warns on timeout and does not fail the substep.
        """
        logger.info("Deleting MultiClusterHub resource...")
        changed = False

        try:
            # Get MultiClusterHub
            mchs = self.primary.list_custom_resources(
                group="operator.open-cluster-management.io",
                version="v1",
                plural="multiclusterhubs",
                namespace=ACM_NAMESPACE,
            )

            if not mchs:
                # PR E makes this absence proof strict; today it is the existing
                # non-strict list, taken at face value.
                logger.info("No MultiClusterHub resources found (already deleted or never created)")
                logger.info(
                    "Note: ACM operator pods (%s-*) may still be running - "
                    "this is expected as the operator is installed separately",
                    ACM_OPERATOR_POD_PREFIX,
                )
                return SubstepExecution(SubstepOutcome.PRECONDITION_NOOP)

            for mch in mchs:
                mch_name = mch.get("metadata", {}).get("name")

                logger.info("Deleting MultiClusterHub: %s", mch_name)
                logger.info("This may take up to 20 minutes...")

                try:
                    self.primary.delete_custom_resource(
                        group="operator.open-cluster-management.io",
                        version="v1",
                        plural="multiclusterhubs",
                        name=mch_name,
                        namespace=ACM_NAMESPACE,
                        timeout_seconds=DELETE_REQUEST_TIMEOUT,
                    )
                except ApiException as exc:
                    # Status and reason only, converted here so the deletes already
                    # accepted in this invocation are still reported.
                    raise SwitchoverError(
                        f"Failed to delete MultiClusterHub {mch_name}: API error {exc.status} {exc.reason}"
                    ) from exc
                changed = True

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
        except SwitchoverError as exc:
            logger.error("MultiClusterHub teardown failed: %s", exc)
            return SubstepExecution(SubstepOutcome.FAILED, changed=changed)

        return SubstepExecution(SubstepOutcome.COMPLETED, changed=changed)
