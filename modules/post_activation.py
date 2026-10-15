"""
Post-activation verification module for ACM switchover.
"""

# Runbook: Steps 6-10 (Method 1) / F6 (Method 2)

import base64
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException

from lib.constants import (
    CLUSTER_VERIFY_INTERVAL,
    CLUSTER_VERIFY_MAX_WORKERS,
    CLUSTER_VERIFY_TIMEOUT,
    DELETE_REQUEST_TIMEOUT,
    DISABLE_AUTO_IMPORT_ANNOTATION,
    INITIAL_CLUSTER_WAIT_TIMEOUT,
    KLUSTERLET_API_READ_TIMEOUT,
    KLUSTERLET_REASON_EXPECTED_ENDPOINT_CHANGED,
    KLUSTERLET_RECHECK_INTERVAL,
    KLUSTERLET_RECHECK_TIMEOUT,
    KLUSTERLET_RESULT_FAILED,
    KLUSTERLET_RESULT_NO_CONTEXT,
    KLUSTERLET_RESULT_UNREACHABLE,
    KLUSTERLET_RESULT_UNVERIFIED,
    KLUSTERLET_RESULT_VERIFIED,
    KLUSTERLET_RESULT_WORKER_TIMEOUT,
    KLUSTERLET_RESULT_WRONG_HUB,
    KLUSTERLET_WORKER_TIMEOUT,
    KLUSTERLET_WORKER_TIMEOUT_MESSAGE,
    LOCAL_CLUSTER_NAME,
    MANAGED_CLUSTER_AGENT_NAMESPACE,
    MANAGED_CLUSTER_API_GROUP,
    MANAGED_CLUSTER_API_VERSION,
    MANAGED_CLUSTER_EXPECTATION_WAIVER_FLAG,
    MANAGED_CLUSTER_PLURAL,
    MAX_KUBECONFIG_SIZE,
    OBSERVABILITY_NAMESPACE,
    OBSERVABILITY_POD_LABEL_SELECTOR,
    OBSERVABILITY_POD_TIMEOUT,
    OBSERVATORIUM_API_DEPLOYMENT,
    POD_READINESS_TOLERANCE,
    SECRET_VISIBILITY_INTERVAL,
    SECRET_VISIBILITY_TIMEOUT,
    THANOS_COMPACTOR_LABEL_SELECTOR,
    THANOS_COMPACTOR_STATEFULSET,
    kubeconfig_size_override_ignored,
)
from lib.endpoint_identity import (
    REASON_EXPECTED_ENDPOINT_MALFORMED,
    REASON_EXPECTED_ENDPOINT_UNAVAILABLE,
    REASON_NO_CONTEXT,
    classify_hub,
    managed_cluster_url,
    normalized_equal,
    parse_endpoint,
    redact,
    select_spoke_context,
)
from lib.exceptions import FatalError, SwitchoverError
from lib.kube_client import KubeClient
from lib.kubeconfig_merge import load_merged_kubeconfig_with_sources
from lib.kubeconfig_snapshot import (
    SnapshotError,
    SpokeSnapshot,
    build_api_client,
    build_snapshot,
    new_private_dir,
    remove_private_dir,
)
from lib.migration_evidence import MigrationEvidenceError
from lib.migration_post_activation import (
    managed_cluster_readiness,
    post_activation_completion,
    post_activation_names_evidence,
    post_activation_waiver,
    require_completed_restore,
)
from lib.run_record import RunRecord, StateStructureError
from lib.utils import Phase, StateManager, dry_run_skip
from lib.waiter import WaitConditionResult, wait_for_condition

logger = logging.getLogger("acm_switchover")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _secret_create_body(doc: Dict[str, Any]) -> client.V1Secret:
    """The import manifest's Secret document, whole, as a typed create body.

    The client's own model conversion builds it, so no field is selected by hand. kubernetes
    37.x models provide `from_dict`, the validation its generated methods apply to a mapping
    body; older clients only deserialize response-shaped input.
    """
    if hasattr(client.V1Secret, "from_dict"):
        return client.V1Secret.from_dict(doc)
    api = client.ApiClient()
    # The pre-37 `deserialize(response, response_type)` signature; 37.x's annotations differ.
    legacy_deserialize: Callable[..., Any] = api.deserialize
    # Serialized by the client first: YAML loads an unquoted timestamp as a datetime.
    return legacy_deserialize(SimpleNamespace(data=json.dumps(api.sanitize_for_serialization(doc))), "V1Secret")


class PostActivationVerification:
    """Handles post-activation verification on secondary hub."""

    def __init__(
        self,
        secondary_client: KubeClient,
        state_manager: StateManager,
        has_observability: bool,
        dry_run: bool = False,
        min_managed_clusters: int = 0,
        expected_managed_cluster_names: Optional[List[str]] = None,
        enforce_expected_managed_cluster_names: bool = False,
        managed_cluster_expectation_waiver: Optional[Dict] = None,
    ):
        self.secondary = secondary_client
        self.state = state_manager
        self.run_record = RunRecord(state_manager)
        self.has_observability = has_observability
        self.dry_run = dry_run
        self.min_managed_clusters = min_managed_clusters
        self.expected_managed_cluster_names = sorted(expected_managed_cluster_names or [])
        self.enforce_expected_managed_cluster_names = enforce_expected_managed_cluster_names
        # A validated waiver request (lib.validation), journaled only for the post_activation scope.
        self.managed_cluster_expectation_waiver = managed_cluster_expectation_waiver
        # True once a journaled or newly journaled waiver covers the post-activation name check.
        self._names_waived = False
        self._cached_managed_clusters: Optional[List[Dict]] = None  # Cache for managed clusters
        # R4-06 PR 3: where each merged kubeconfig entry came from, and the private
        # directory that holds this run's materialized spoke credentials.
        self._kubeconfig_sources: Dict[Tuple[str, str], str] = {}
        self._spoke_material_dir: Optional[str] = None

    def _step(self, step_name: str) -> AbstractContextManager:
        """Guard one step; a dry-run consults completion markers but never records one.

        A dry-run skips the step's work, so a marker it recorded would let a later
        executing run skip work that never ran.
        """
        if self.dry_run:
            return nullcontext(not self.state.is_step_completed(step_name))
        return self.state.step(step_name, logger)

    def _get_managed_clusters(self, force_refresh: bool = False) -> List[Dict]:
        """Get managed clusters with caching.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            List of managed cluster resources
        """
        if self._cached_managed_clusters is None or force_refresh:
            self._cached_managed_clusters = self._strict_managed_clusters()
        return self._cached_managed_clusters

    def _strict_managed_clusters(self) -> List[Dict]:
        """The positively complete ManagedCluster inventory; a failed read is never zero clusters."""
        outcome = self.secondary.list_managed_clusters_strict()
        if not outcome.is_success:
            raise FatalError(f"ManagedCluster inventory read failed ({outcome.status.value}: {outcome.reason})")
        return outcome.items

    def verify(self) -> bool:
        """
        Execute all post-activation verification steps.

        Returns:
            True if all verifications passed
        """
        logger.info("Starting post-activation verification...")

        try:
            # R4-04: the journal and the waiver are proven before any work mutates anything.
            journal = self._load_post_activation_journal()
            waiver = self._post_activation_waiver(journal)

            # Phase 1: Cluster connectivity verification, then its name evidence
            self._verify_cluster_connections()
            journal = self._record_names_evidence(journal, waiver)

            # Phase 2: Auto-import cleanup verification
            self._verify_auto_import_cleanup_step()

            # Phase 3: Observability verification (if present)
            if self.has_observability:
                self._verify_observability_full()
            else:
                logger.info("Skipping Observability verification (not detected)")

            # The last post-activation evidence: every required operation above succeeded.
            self._record_completion_evidence(journal)
            logger.info("Post-activation verification completed successfully")
            return True

        except SwitchoverError as e:
            logger.error("Post-activation verification failed: %s", e)
            self.state.add_error(str(e), Phase.POST_ACTIVATION.value)
            return False
        except (RuntimeError, ValueError) as e:
            logger.error("Unexpected error during post-activation verification: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", Phase.POST_ACTIVATION.value)
            return False
        except Exception as e:
            logger.error(
                "Programming error during post-activation verification: %s: %s",
                type(e).__name__,
                e,
            )
            raise

    def _verify_cluster_connections(self) -> None:
        """
        Verify ManagedClusters are connected, with klusterlet fix fallback.

        First tries a brief wait for clusters to connect automatically.
        If that fails, attempts to fix klusterlet connections and waits again.
        """
        klusterlet_step_checked = False

        with self._step("verify_clusters_connected") as should_run:
            if should_run:
                try:
                    # Initial brief wait - clusters may connect automatically
                    self._verify_managed_clusters_connected(timeout=INITIAL_CLUSTER_WAIT_TIMEOUT)
                except FatalError:
                    # A failed or malformed inventory read is not "not connected yet".
                    raise
                except SwitchoverError as e:
                    # Timeout - clusters not connected yet
                    logger.warning(
                        "ManagedClusters not connected after initial wait: %s",
                        e,
                    )
                    logger.info("Checking if klusterlets need to be fixed (may be pointing to old hub)...")
                    with self._step("verify_klusterlet_connections") as should_run_fix:
                        klusterlet_step_checked = True
                        if should_run_fix:
                            self._verify_klusterlet_connections()

                    # Now wait longer for clusters to reconnect after fix
                    logger.info("Waiting for ManagedClusters to reconnect after klusterlet fix...")
                    self._verify_managed_clusters_connected()

        # Verify klusterlet connections even when the clusters connected (wrong-hub drift check).
        # A per-cluster missing context is a non-fatal skip; an unloadable kubeconfig (GLM-H3,
        # #349) or an unproven cluster (R4-06, amendment §2.8) fails the step.
        # This may have already been done above if clusters didn't connect initially
        if not klusterlet_step_checked:
            with self._step("verify_klusterlet_connections") as should_run:
                if should_run:
                    self._verify_klusterlet_connections()

    def _verify_auto_import_cleanup_step(self) -> None:
        """Verify disable-auto-import annotations are cleared from ManagedClusters."""
        with self._step("verify_auto_import_cleanup") as should_run:
            if should_run:
                self._verify_disable_auto_import_cleared()

    def _verify_observability_full(self) -> None:
        """
        Execute all observability verification steps.

        Includes scaling up components, restarting API, verifying pods,
        and checking metrics collection.
        """
        with self._step("scale_up_observability_components") as should_run:
            if should_run:
                self._scale_up_observability_components()

        with self._step("restart_observatorium_api") as should_run:
            if should_run:
                self._restart_observatorium_api()

        with self._step("verify_observability_pods") as should_run:
            if should_run:
                self._verify_observability_pods()

        with self._step("verify_metrics_collection") as should_run:
            if should_run:
                self._verify_metrics_collection()

    @dry_run_skip(message="Skipping wait for ManagedCluster connections")
    def _verify_managed_clusters_connected(self, timeout: int = CLUSTER_VERIFY_TIMEOUT) -> Optional[Dict]:
        """Wait until the strict ManagedCluster inventory proves readiness (Available and Joined).

        Returns the accepted readiness decision (None in a dry-run).
        """

        latest: Dict = {}

        def _poll_clusters():
            decision = self._managed_cluster_readiness()
            latest.clear()
            latest.update(decision)
            if decision["ready"]:
                return WaitConditionResult.complete(self._readiness_detail(decision))
            return WaitConditionResult.pending(self._readiness_detail(decision))

        success = wait_for_condition(
            "ManagedCluster connections",
            _poll_clusters,
            timeout=timeout,
            interval=CLUSTER_VERIFY_INTERVAL,
            logger=logger,
        )

        if not success:
            if latest.get("code") == "expected_names_missing":
                raise SwitchoverError(
                    "Missing expected ManagedCluster(s) after activation (expected_names_missing): "
                    + ", ".join(latest["missing"])
                )
            detail = self._readiness_detail(latest) if latest else "no readiness decision"
            raise SwitchoverError(f"Timeout waiting for ManagedClusters to connect ({latest.get('code')}): {detail}")
        return dict(latest)

    def _effective_expected_names(self) -> List[str]:
        return list(self.expected_managed_cluster_names) if self.enforce_expected_managed_cluster_names else []

    def _managed_cluster_readiness(self) -> Dict:
        """One strict ManagedCluster inventory read and its readiness decision (names, floor, conditions)."""
        return self._evidence(
            managed_cluster_readiness,
            items=self._strict_managed_clusters(),
            expected_names=self._effective_expected_names(),
            min_managed_clusters=self.min_managed_clusters,
            names_waived=self._names_waived,
        )

    def _readiness_detail(self, decision: Dict) -> str:
        code = decision["code"]
        if code == "inventory_empty":
            return "no ManagedClusters found"
        if code == "expected_names_missing":
            return "missing expected ManagedCluster(s): " + ", ".join(decision["missing"])
        if code == "count_below_minimum":
            return f"found {decision['count']}/{self.min_managed_clusters} expected ManagedCluster(s)"
        if code == "clusters_not_ready":
            return "not Available and Joined: " + ", ".join(decision["pending"])
        if decision["count"] == 0:
            return "No non-local ManagedClusters to verify (only local-cluster exists)"
        return f"available and joined={decision['count']}/{decision['count']}"

    # -- R4-04 migration evidence: post_activation.names_verified_at (or the waiver), then completed_at --

    def _load_post_activation_journal(self) -> Optional[Dict]:
        """The migration journal post-activation records into.

        An executing run requires one with restore.completed_at: switchover, restore-only
        and full restore all journal at activation, so its absence means post-activation
        cannot prove a migration and must not record completion. A dry-run may have none
        (a preview journals nothing) and writes no evidence.
        """
        try:
            journal = self.run_record.migration_backups()
        except (MigrationEvidenceError, StateStructureError) as exc:
            raise FatalError(f"The recorded migration journal is invalid and blocks post-activation: {exc}") from exc
        if self.dry_run:
            return journal
        return self._evidence(require_completed_restore, journal)

    def _post_activation_waiver(self, journal: Optional[Dict]) -> Optional[Dict]:
        """The waiver covering the post-activation name check: the journaled one, else a post_activation request."""
        if self.dry_run:
            return None
        decision = self._evidence(
            post_activation_waiver,
            journal=journal,
            requested=self.managed_cluster_expectation_waiver,
            expected_names=self._effective_expected_names(),
            flag=MANAGED_CLUSTER_EXPECTATION_WAIVER_FLAG,
            journaled_at=_now_iso(),
        )
        waiver = decision["waiver"]
        if waiver is not None:
            logger.warning(
                "Expected ManagedCluster name check waived for post-activation by %s (%s); "
                "the count floor and readiness still apply",
                waiver["actor"],
                waiver["reason"],
            )
        self._names_waived = waiver is not None
        return waiver

    def _record_names_evidence(self, journal: Optional[Dict], waiver: Optional[Dict]) -> Optional[Dict]:
        """Prove readiness with the bounded strict wait, then record the name check or the waiver once.

        Runs on every attempt, completed step markers notwithstanding, with the same
        bounded wait the collection polls; recorded evidence is kept, and a failed
        proof fails the attempt without clearing it.
        """
        if self.dry_run:
            logger.info("[DRY-RUN] Would verify and record the post-activation ManagedCluster name evidence")
            return journal
        readiness = self._verify_managed_clusters_connected()
        decision = self._evidence(
            post_activation_names_evidence, journal=journal, readiness=readiness, waiver=waiver, now=_now_iso()
        )
        if decision["write"]:
            self._persist_journal(decision["candidate"])
        return decision["candidate"]

    def _record_completion_evidence(self, journal: Optional[Dict]) -> None:
        """Record post_activation.completed_at, the last post-activation evidence transition, once."""
        if self.dry_run:
            logger.info("[DRY-RUN] Would record post-activation completion evidence")
            return
        decision = self._evidence(post_activation_completion, journal=journal, now=_now_iso())
        if decision["write"]:
            self._persist_journal(decision["candidate"])
            logger.info("Recorded post-activation completion evidence")

    def _persist_journal(self, candidate: Dict) -> None:
        """Durably record a journal transition through RunRecord; a failure fails the phase."""
        try:
            self.run_record.record_migration_backups(candidate)
        except MigrationEvidenceError as exc:
            raise FatalError(f"The migration journal transition was refused: {exc}") from exc
        except OSError as exc:
            raise FatalError(f"The migration journal could not be recorded durably: {exc}") from exc

    @staticmethod
    def _evidence(function, *args, **kwargs):
        """Call a pure evidence helper, turning its fail-closed refusal into a FatalError."""
        try:
            return function(*args, **kwargs)
        except MigrationEvidenceError as exc:
            raise FatalError(f"Migration evidence refused ({exc})") from exc

    @dry_run_skip(message="Skipping scale-up of observability components")
    def _scale_up_observability_components(self):
        """Scale up observability components if they are scaled down to 0 replicas.

        When promoting a passive hub to active, observatorium-api and thanos-compact
        may be scaled down to 0 replicas. This method checks and scales them up to
        their default replica counts:
        - observatorium-api: 2 replicas (default for HA)
        - thanos-compact: 1 replica (default single replica)

        These defaults are consistent across RHACM versions 2.3 through 2.14.
        """
        logger.info("Checking observability components for scale-up...")

        # Default replica counts
        OBSERVATORIUM_API_REPLICAS = 2  # Default for HA
        THANOS_COMPACT_REPLICAS = 1  # Default single replica

        scaled_components = []

        # Check and scale observatorium-api deployment
        try:
            deployment = self.secondary.get_deployment(
                name=OBSERVATORIUM_API_DEPLOYMENT,
                namespace=OBSERVABILITY_NAMESPACE,
            )

            if deployment is None:
                raise SwitchoverError("observatorium-api deployment not found")
            else:
                current_replicas = deployment.get("spec", {}).get("replicas", 0)
                if current_replicas == 0:
                    logger.info(
                        "observatorium-api has 0 replicas, scaling up to %d (default for HA)",
                        OBSERVATORIUM_API_REPLICAS,
                    )
                    self.secondary.scale_deployment(
                        name=OBSERVATORIUM_API_DEPLOYMENT,
                        namespace=OBSERVABILITY_NAMESPACE,
                        replicas=OBSERVATORIUM_API_REPLICAS,
                    )
                    scaled_components.append(("observatorium-api", OBSERVATORIUM_API_REPLICAS))
                else:
                    logger.info(
                        "observatorium-api already has %d replica(s), no scale-up needed",
                        current_replicas,
                    )
        except Exception as e:
            if isinstance(e, SwitchoverError):
                raise
            raise SwitchoverError(f"Failed to check/scale observatorium-api: {e}") from e

        # Check and scale thanos-compact StatefulSet
        try:
            statefulset = self.secondary.get_statefulset(
                name=THANOS_COMPACTOR_STATEFULSET,
                namespace=OBSERVABILITY_NAMESPACE,
            )

            if statefulset is None:
                raise SwitchoverError("thanos-compact StatefulSet not found")
            else:
                current_replicas = statefulset.get("spec", {}).get("replicas", 0)
                if current_replicas == 0:
                    logger.info(
                        "thanos-compact has 0 replicas, scaling up to %d (default single replica)",
                        THANOS_COMPACT_REPLICAS,
                    )
                    self.secondary.scale_statefulset(
                        name=THANOS_COMPACTOR_STATEFULSET,
                        namespace=OBSERVABILITY_NAMESPACE,
                        replicas=THANOS_COMPACT_REPLICAS,
                    )
                    scaled_components.append(("thanos-compact", THANOS_COMPACT_REPLICAS))
                else:
                    logger.info(
                        "thanos-compact already has %d replica(s), no scale-up needed",
                        current_replicas,
                    )
        except Exception as e:
            if isinstance(e, SwitchoverError):
                raise
            raise SwitchoverError(f"Failed to check/scale thanos-compact: {e}") from e

        # Wait for scaled components to be ready
        if scaled_components:
            logger.info("Waiting for scaled components to be ready...")

            # Wait for observatorium-api if it was scaled
            if any(name == "observatorium-api" for name, _ in scaled_components):
                target = next(count for name, count in scaled_components if name == "observatorium-api")
                logger.info("Waiting for observatorium-api pods to be ready...")
                ready = self.secondary.wait_for_pods_ready(
                    namespace=OBSERVABILITY_NAMESPACE,
                    label_selector="app.kubernetes.io/name=observatorium-api",
                    timeout=OBSERVABILITY_POD_TIMEOUT,
                    expected_count=target,
                )
                if ready:
                    logger.info("observatorium-api pods are ready")
                else:
                    raise SwitchoverError("observatorium-api pods did not become ready in time")

            # Wait for thanos-compact if it was scaled
            if any(name == "thanos-compact" for name, _ in scaled_components):
                target = next(count for name, count in scaled_components if name == "thanos-compact")
                logger.info("Waiting for thanos-compact pods to be ready...")
                ready = self.secondary.wait_for_pods_ready(
                    namespace=OBSERVABILITY_NAMESPACE,
                    label_selector=THANOS_COMPACTOR_LABEL_SELECTOR,
                    timeout=OBSERVABILITY_POD_TIMEOUT,
                    expected_count=target,
                )
                if ready:
                    logger.info("thanos-compact pods are ready")
                else:
                    raise SwitchoverError("thanos-compact pods did not become ready in time")
        else:
            logger.info("No components needed scale-up")

    def _restart_observatorium_api(self):
        """Restart observatorium-api deployment (ACM 2.12 issue workaround)."""
        logger.info("Restarting observatorium-api deployment...")

        try:
            self.secondary.rollout_restart_deployment(
                name=OBSERVATORIUM_API_DEPLOYMENT,
                namespace=OBSERVABILITY_NAMESPACE,
            )

            logger.info("Triggered observatorium-api restart")

            # Skip pod waiting in dry-run mode since no actual restart happened
            if self.dry_run:
                logger.info("[DRY-RUN] Skipping wait for observatorium-api pods")
                return

            logger.info("Waiting for observatorium-api Deployment rollout to stabilize...")
            ready = self._wait_for_observatorium_api_rollout()

            if ready:
                logger.info("observatorium-api deployment rollout is ready")
                pods = self.secondary.get_pods(
                    namespace=OBSERVABILITY_NAMESPACE,
                    label_selector="app.kubernetes.io/name=observatorium-api",
                )
                start_times = [
                    pod.get("status", {}).get("startTime") for pod in pods if pod.get("status", {}).get("startTime")
                ]
                if start_times:
                    logger.info(
                        "observatorium-api pod start times: %s",
                        ", ".join(start_times),
                    )
            else:
                raise SwitchoverError("observatorium-api deployment rollout did not become ready in time")

        except ApiException as e:
            logger.error("Failed to restart observatorium-api: %s", e)
            if e.status == 404:
                raise SwitchoverError("observatorium-api deployment not found") from e
            else:
                raise
        except SwitchoverError:
            raise
        except Exception as e:
            logger.error("Failed to restart observatorium-api: %s", e)
            raise SwitchoverError(f"Failed to restart observatorium-api: {e}") from e

    def _wait_for_observatorium_api_rollout(self) -> bool:
        """Wait for the full observatorium-api Deployment rollout to recover after restart."""
        deadline = time.time() + OBSERVABILITY_POD_TIMEOUT
        poll_interval = 5

        while time.time() < deadline:
            deployment = self.secondary.get_deployment(
                name=OBSERVATORIUM_API_DEPLOYMENT,
                namespace=OBSERVABILITY_NAMESPACE,
            )

            if deployment:
                metadata = deployment.get("metadata", {})
                spec = deployment.get("spec", {})
                status = deployment.get("status", {})
                generation = int(metadata.get("generation", 0) or 0)
                observed_generation = int(status.get("observedGeneration", 0) or 0)
                desired_replicas = int(spec.get("replicas", 1) or 0)
                current_replicas = int(status.get("replicas", desired_replicas) or 0)
                updated_replicas = int(status.get("updatedReplicas", 0) or 0)
                available_replicas = int(status.get("availableReplicas", 0) or 0)
                ready_replicas = int(status.get("readyReplicas", 0) or 0)
                unavailable_replicas = int(status.get("unavailableReplicas", 0) or 0)

                if (
                    observed_generation >= generation
                    and updated_replicas >= desired_replicas
                    and available_replicas >= desired_replicas
                    and ready_replicas >= desired_replicas
                    and current_replicas <= updated_replicas
                    and unavailable_replicas == 0
                ):
                    return True

                logger.debug(
                    "Waiting for observatorium-api rollout: "
                    "observedGeneration=%s/%s replicas=%s updatedReplicas=%s/%s "
                    "availableReplicas=%s/%s readyReplicas=%s/%s unavailableReplicas=%s",
                    observed_generation,
                    generation,
                    current_replicas,
                    updated_replicas,
                    desired_replicas,
                    available_replicas,
                    desired_replicas,
                    ready_replicas,
                    desired_replicas,
                    unavailable_replicas,
                )
            else:
                logger.debug("Waiting for observatorium-api rollout: deployment not found yet")

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

        return False

    def _verify_observability_pods(self):
        """Verify all Observability pods are running and ready."""
        logger.info("Verifying Observability pod health...")

        # Use label selector scoped to ACM observability components.
        # The label app.kubernetes.io/part-of=observability is not consistently applied,
        # while OBSERVABILITY_POD_LABEL_SELECTOR is; it is the same scope the shared
        # teardown machine drains, so both must read one definition.
        pods = self.secondary.get_pods(
            namespace=OBSERVABILITY_NAMESPACE,
            label_selector=OBSERVABILITY_POD_LABEL_SELECTOR,
        )

        if not pods:
            raise SwitchoverError("No Observability pods found")

        critical_waiting_reasons = {
            "CrashLoopBackOff",
            "ImagePullBackOff",
            "ErrImagePull",
        }
        critical_terminated_reasons = {"Error", "OOMKilled"}

        running_pods = 0
        ready_pods = 0
        error_pods = []

        for pod in pods:
            pod_name = pod.get("metadata", {}).get("name")
            phase = pod.get("status", {}).get("phase", "unknown")
            pod_errors = []

            if phase == "Running":
                running_pods += 1
            elif phase in ("Failed", "Unknown"):
                pod_errors.append(f"phase={phase}")

            # Check ready condition
            conditions = pod.get("status", {}).get("conditions", [])
            for condition in conditions:
                if condition.get("type") == "Ready" and condition.get("status") == "True":
                    ready_pods += 1
                    break

            # Inspect container states for crash loops or repeated failures
            container_statuses = pod.get("status", {}).get("containerStatuses", [])
            for container in container_statuses:
                container_name = container.get("name", "container")
                state = container.get("state") or {}

                waiting_state = state.get("waiting")
                if waiting_state:
                    reason = waiting_state.get("reason", "waiting")
                    if reason in critical_waiting_reasons:
                        pod_errors.append(f"{container_name} waiting ({reason})")

                terminated_state = state.get("terminated")
                if terminated_state:
                    reason = terminated_state.get("reason", "terminated")
                    exit_code = terminated_state.get("exitCode")
                    if reason in critical_terminated_reasons or (exit_code is not None and exit_code != 0):
                        pod_errors.append(f"{container_name} terminated ({reason}, exit={exit_code})")

            if pod_errors:
                error_pods.append(f"{pod_name}: " + "; ".join(pod_errors))

        logger.info(
            "Observability pods: %d/%d running, %d/%d ready",
            running_pods,
            len(pods),
            ready_pods,
            len(pods),
        )

        if error_pods:
            raise SwitchoverError("Observability pods in error state: " + ", ".join(error_pods))

        if ready_pods < len(pods) * POD_READINESS_TOLERANCE:
            raise SwitchoverError(
                f"Only {ready_pods}/{len(pods)} Observability pods ready; "
                "some pods may still be starting or unhealthy"
            )

    def _verify_metrics_collection(self):
        """Verify metrics collection is working (informational)."""
        logger.info("Verifying metrics collection...")

        # This is informational - we can't easily verify Grafana metrics from Python
        # without additional dependencies, so we'll just log guidance

        logger.info(
            "To verify metrics collection manually:\n"
            "  1. Access Grafana dashboard\n"
            "  2. Check 'ACM - Clusters Overview' for recent data (within 5 minutes)\n"
            "  3. Or query 'acm_managed_cluster_info' metric\n"
            "\n"
            "Metrics should appear within 5-10 minutes after observatorium-api restart."
        )
        self._log_grafana_route()

        # We could check if metrics-collector pods are running
        metrics_pods = self.secondary.get_pods(
            namespace=OBSERVABILITY_NAMESPACE, label_selector="app=metrics-collector"
        )

        if metrics_pods:
            logger.info("Found %d metrics-collector pod(s)", len(metrics_pods))
        else:
            logger.warning("No metrics-collector pods found")

    def _log_grafana_route(self):
        """Log Grafana route availability to help operators verify UI."""
        try:
            host = self.secondary.get_route_host(OBSERVABILITY_NAMESPACE, "grafana")
            if host:
                logger.info(
                    "Grafana route detected: https://%s (namespace: %s)",
                    host,
                    OBSERVABILITY_NAMESPACE,
                )
            else:
                logger.warning("Grafana route not found in Observability namespace")
        except Exception as exc:
            logger.warning("Unable to query Grafana route: %s", exc)

    def _verify_disable_auto_import_cleared(self):
        """Ensure disable-auto-import annotations are cleared from ManagedClusters.

        Annotations may be present on the secondary hub if the passive-sync
        backup was taken after PRIMARY_PREP added them on the primary hub.
        This method actively removes any stale annotations before verifying.
        """

        # Skip verification in dry-run mode since annotations weren't actually cleared
        if self.dry_run:
            logger.info("[DRY-RUN] Skipping disable-auto-import annotation verification")
            return

        logger.info("Ensuring disable-auto-import annotations are cleared...")
        # Force refresh to ensure we have fresh data after klusterlet verification
        # which may have taken time and annotations could have been cleared
        managed_clusters = self._get_managed_clusters(force_refresh=True)

        # Remove stale annotations that were synced from primary via backup/restore
        removed = []
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")
            if mc_name == LOCAL_CLUSTER_NAME:
                continue

            annotations = mc.get("metadata", {}).get("annotations") or {}
            if DISABLE_AUTO_IMPORT_ANNOTATION in annotations:
                try:
                    patch = {"metadata": {"annotations": {DISABLE_AUTO_IMPORT_ANNOTATION: None}}}
                    self.secondary.patch_custom_resource(
                        group=MANAGED_CLUSTER_API_GROUP,
                        version=MANAGED_CLUSTER_API_VERSION,
                        plural=MANAGED_CLUSTER_PLURAL,
                        name=mc_name,
                        patch=patch,
                    )
                    removed.append(mc_name)
                except Exception as exc:
                    logger.warning(
                        "Failed to remove disable-auto-import annotation from %s: %s",
                        mc_name,
                        exc,
                    )

        if removed:
            logger.info(
                "Removed stale disable-auto-import annotations from: %s",
                ", ".join(removed),
            )

        # Verify all annotations are now cleared
        managed_clusters = self._get_managed_clusters(force_refresh=True)
        flagged = []
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")
            if mc_name == LOCAL_CLUSTER_NAME:
                continue
            annotations = mc.get("metadata", {}).get("annotations") or {}
            if DISABLE_AUTO_IMPORT_ANNOTATION in annotations:
                flagged.append(mc_name or "unknown")

        if flagged:
            raise SwitchoverError("disable-auto-import annotation still present on: " + ", ".join(flagged))

        logger.info("All ManagedClusters cleared of disable-auto-import annotation")

    def _klusterlet_cluster_targets(self) -> List[Tuple[str, Optional[str], Optional[str]]]:
        """``(cluster_name, api_url, url_reason)`` for every non-local managed cluster.

        The URL is the first non-empty ``managedClusterClientConfigs[].url`` (amendment
        §2.5 rule 1); without a usable one ``api_url`` is None and ``url_reason`` says why.
        """

        cluster_targets = []
        for managed_cluster in self._get_managed_clusters():
            name = managed_cluster.get("metadata", {}).get("name")
            if not name or name == LOCAL_CLUSTER_NAME:
                continue
            api_url, url_reason = managed_cluster_url(managed_cluster)
            cluster_targets.append((name, api_url, url_reason))
        return cluster_targets

    def _check_klusterlet_target(
        self,
        kubeconfig_data: Dict,
        cluster_name: str,
        cluster_api_url: Optional[str],
        url_reason: Optional[str],
    ) -> Tuple[str, str, Any, Optional[str], Optional[str], Optional[str]]:
        """``(cluster, result, snapshot, expected_hub, api_url, reason)`` for one cluster.

        Every refusal before the spoke client exists - no usable URL, an ambiguous or
        near-matching context, an unusable expected endpoint - leaves the cluster
        uninspected (amendment §2.5, §2.4). There is no name-only fallback. The cluster's
        snapshot (amendment §6) is captured once here and is the only source of every spoke
        client this run builds for it: probe, remediation and re-check.
        """
        try:
            if cluster_api_url is None:
                return (cluster_name, KLUSTERLET_RESULT_UNVERIFIED, None, None, None, url_reason)
            context_name, context_reason = select_spoke_context(kubeconfig_data, cluster_api_url)
            if context_name is None:
                if context_reason == REASON_NO_CONTEXT:
                    return (cluster_name, KLUSTERLET_RESULT_NO_CONTEXT, None, None, None, None)
                return (cluster_name, KLUSTERLET_RESULT_UNVERIFIED, None, None, None, context_reason)
            logger.debug(
                "Matched cluster %s to context %s via API URL %s",
                cluster_name,
                context_name,
                redact(cluster_api_url),
            )
            expected_hub, expected_reason = self._expected_hub_endpoint(cluster_name)
            if expected_hub is None:
                return (cluster_name, KLUSTERLET_RESULT_UNVERIFIED, None, None, None, expected_reason)
            try:
                snapshot = self._spoke_snapshot(kubeconfig_data, context_name)
            except SnapshotError as refusal:
                # Credentials or TLS settings that cannot be proven: no client is ever built.
                return (cluster_name, KLUSTERLET_RESULT_UNVERIFIED, None, None, None, refusal.reason)
            result, reason = self._check_klusterlet_connection(snapshot, cluster_name, expected_hub, cluster_api_url)
            return (cluster_name, result, snapshot, expected_hub, cluster_api_url, reason)
        except Exception as exc:
            logger.debug("Error checking klusterlet for %s: %s", cluster_name, type(exc).__name__)
            return (cluster_name, KLUSTERLET_RESULT_FAILED, None, None, None, None)

    def _collect_klusterlet_future_results(self, future_fallbacks: Dict, operation: str) -> List[tuple]:
        done, not_done = wait(future_fallbacks.keys(), timeout=KLUSTERLET_WORKER_TIMEOUT)
        results = []
        for future in done:
            try:
                results.append(future.result())
            except Exception as exc:
                fallback = future_fallbacks[future]
                logger.error(
                    "Worker thread for %s failed while %s klusterlet: %s",
                    fallback[0],
                    operation,
                    exc,
                )
                results.append(fallback)
        for future in not_done:
            fallback = future_fallbacks[future]
            logger.warning(
                KLUSTERLET_WORKER_TIMEOUT_MESSAGE,
                operation,
                fallback[0],
                KLUSTERLET_WORKER_TIMEOUT,
            )
            future.cancel()
            results.append(fallback)
        return results

    def _check_klusterlet_targets(
        self,
        cluster_targets: List[Tuple[str, Optional[str], Optional[str]]],
        kubeconfig_data: Dict,
    ) -> Tuple[
        List[str],
        "List[_WrongHub]",
        List[str],
        List[str],
        List[str],
        List[Tuple[str, str]],
    ]:
        """Classify every cluster: verified, wrong_hub, skipped, timed out, failed, unverified."""
        logger.info(
            "Checking klusterlet connections for %d cluster(s) in parallel...",
            len(cluster_targets),
        )

        verified = []
        wrong_hub = []
        unreachable = []
        timed_out = []
        failed = []
        unverified = []

        executor = ThreadPoolExecutor(max_workers=CLUSTER_VERIFY_MAX_WORKERS)
        try:
            check_future_fallbacks = {
                executor.submit(
                    self._check_klusterlet_target,
                    kubeconfig_data,
                    name,
                    api_url,
                    url_reason,
                ): (name, KLUSTERLET_RESULT_WORKER_TIMEOUT, None, None, None, None)
                for name, api_url, url_reason in cluster_targets
            }
            for (
                cluster_name,
                result,
                snapshot,
                expected_hub,
                api_url,
                reason,
            ) in self._collect_klusterlet_future_results(check_future_fallbacks, "checking"):
                if result == KLUSTERLET_RESULT_VERIFIED:
                    verified.append(cluster_name)
                elif result == KLUSTERLET_RESULT_WRONG_HUB:
                    wrong_hub.append(_WrongHub(cluster_name, snapshot, expected_hub, api_url))
                elif result == KLUSTERLET_RESULT_UNVERIFIED:
                    unverified.append((cluster_name, reason or "unverified"))
                elif result == KLUSTERLET_RESULT_WORKER_TIMEOUT:
                    timed_out.append(cluster_name)
                elif result == KLUSTERLET_RESULT_FAILED:
                    failed.append(cluster_name)
                else:
                    unreachable.append(cluster_name)
        finally:
            # A timed-out worker can still be using this run's credential files; it finishes
            # before they are removed (amendment §2.8). Its result stays the timeout.
            executor.shutdown(wait=True, cancel_futures=True)

        return verified, wrong_hub, unreachable, timed_out, failed, unverified

    def _fix_klusterlet_target(
        self, cluster_name: str, snapshot: Any, api_url: str, expected_hub: str
    ) -> Tuple[str, bool]:
        success = self._force_klusterlet_reconnect(cluster_name, snapshot, api_url, expected_hub)
        return (cluster_name, success)

    def _fix_wrong_hub_klusterlets(self, wrong_hub: "List[_WrongHub]") -> Tuple[List[str], List[str]]:
        fixed = []
        fix_failed = []

        executor = ThreadPoolExecutor(max_workers=CLUSTER_VERIFY_MAX_WORKERS)
        try:
            fix_future_fallbacks = {
                executor.submit(
                    self._fix_klusterlet_target, target.cluster, target.snapshot, target.api_url, target.expected_hub
                ): (target.cluster, False)
                for target in wrong_hub
            }
            for cluster_name, success in self._collect_klusterlet_future_results(
                fix_future_fallbacks,
                "remediating",
            ):
                if success:
                    fixed.append(cluster_name)
                else:
                    fix_failed.append(cluster_name)
        finally:
            # Remediation mutates managed clusters. Python cannot stop a running
            # thread, so wait for bounded API calls to exit before reporting.
            executor.shutdown(wait=True, cancel_futures=True)

        # A worker that finished after its timeout is still a failure, but what it really
        # did is reported, never the placeholder (amendment §2.8).
        for future, (cluster_name, _fallback) in fix_future_fallbacks.items():
            if cluster_name in fix_failed and future.done() and not future.cancelled() and future.exception() is None:
                if future.result()[1]:
                    logger.warning(
                        "Klusterlet remediation for %s completed after the worker timeout: the cluster was "
                        "changed, but it is reported as failed and must be re-verified",
                        cluster_name,
                    )
        return fixed, fix_failed

    def _recheck_klusterlet_target(
        self,
        cluster_name: str,
        snapshot: Any,
        expected_hub: str,
        api_url: str,
    ) -> Tuple[str, str]:
        try:
            result, _reason = self._check_klusterlet_connection(snapshot, cluster_name, expected_hub, api_url)
            return (cluster_name, result)
        except Exception as exc:
            logger.debug("Error re-checking klusterlet for %s: %s", cluster_name, type(exc).__name__)
            return (cluster_name, KLUSTERLET_RESULT_UNREACHABLE)

    def _remediated_klusterlet_state(
        self,
        wrong_hub: "List[_WrongHub]",
    ) -> Tuple[List[str], List[str]]:
        current_wrong_hub = []
        current_unverified = []
        executor = ThreadPoolExecutor(max_workers=CLUSTER_VERIFY_MAX_WORKERS)
        try:
            recheck_future_fallbacks = {
                executor.submit(
                    self._recheck_klusterlet_target,
                    target.cluster,
                    target.snapshot,
                    target.expected_hub,
                    target.api_url,
                ): (target.cluster, KLUSTERLET_RESULT_UNREACHABLE)
                for target in wrong_hub
            }
            for cluster_name, result in self._collect_klusterlet_future_results(
                recheck_future_fallbacks,
                "re-checking",
            ):
                if result == KLUSTERLET_RESULT_WRONG_HUB:
                    current_wrong_hub.append(cluster_name)
                elif result != KLUSTERLET_RESULT_VERIFIED:
                    current_unverified.append(cluster_name)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        return current_wrong_hub, current_unverified

    def _wait_for_remediated_klusterlets(
        self,
        wrong_hub: "List[_WrongHub]",
    ) -> Tuple[List[str], List[str]]:
        post_remediation_state: Dict[str, List[str]] = {
            "wrong_hub": [],
            "unverified": [],
        }

        def recheck_remediated_clusters() -> WaitConditionResult:
            current_wrong_hub, current_unverified = self._remediated_klusterlet_state(wrong_hub)
            post_remediation_state["wrong_hub"] = current_wrong_hub
            post_remediation_state["unverified"] = current_unverified

            if not current_wrong_hub and not current_unverified:
                return WaitConditionResult.complete("all remediated klusterlets verified")

            pending_details = []
            if current_wrong_hub:
                pending_details.append(f"still wrong hub: {', '.join(current_wrong_hub)}")
            if current_unverified:
                pending_details.append(f"unverified: {', '.join(current_unverified)}")
            return WaitConditionResult.pending("; ".join(pending_details))

        wait_for_condition(
            description="post-remediation klusterlet hub convergence",
            condition_fn=recheck_remediated_clusters,
            timeout=KLUSTERLET_RECHECK_TIMEOUT,
            interval=KLUSTERLET_RECHECK_INTERVAL,
            logger=logger,
        )
        return post_remediation_state["wrong_hub"], post_remediation_state["unverified"]

    def _remediate_wrong_hub_klusterlets(self, wrong_hub: "List[_WrongHub]") -> None:
        logger.warning(
            "Klusterlet connected to wrong hub for %d cluster(s): %s - attempting to fix...",
            len(wrong_hub),
            ", ".join(target.cluster for target in wrong_hub),
        )

        fixed, fix_failed = self._fix_wrong_hub_klusterlets(wrong_hub)
        if fixed:
            logger.info(
                "✓ Fixed klusterlet connection for %d cluster(s): %s",
                len(fixed),
                ", ".join(fixed),
            )
        if fix_failed:
            logger.warning(
                "✗ Failed to fix klusterlet for %d cluster(s): %s",
                len(fix_failed),
                ", ".join(fix_failed),
            )

        post_remediation_wrong_hub, post_remediation_unverified = self._wait_for_remediated_klusterlets(wrong_hub)

        fatal_messages = []
        if fix_failed:
            fatal_messages.append(f"Klusterlet remediation failed for cluster(s): {', '.join(fix_failed)}")
        if post_remediation_wrong_hub:
            fatal_messages.append(
                "Klusterlet still connected to the wrong hub after remediation: "
                f"{', '.join(post_remediation_wrong_hub)}"
            )
        if post_remediation_unverified:
            fatal_messages.append(
                "Klusterlet remediation could not be verified for cluster(s): "
                f"{', '.join(post_remediation_unverified)}"
            )
        if fatal_messages:
            raise SwitchoverError("; ".join(fatal_messages))

    @dry_run_skip(message="Skipping klusterlet connection verification")
    def _verify_klusterlet_connections(self):
        """
        Verify and fix klusterlet agents on managed clusters to connect to the new hub.

        When both hubs have the same cluster imported (due to passive sync restoring
        ManagedCluster resources), the klusterlet may remain connected to the old hub.
        This method detects such cases and forces the klusterlet to reconnect by:
        1. Patching or creating the bootstrap-hub-kubeconfig secret on the managed cluster
        2. Restarting the klusterlet deployment

        Endpoint identity (R4-06, amendment 2026-10-15 §2): each cluster's spoke context
        is the one whose server normalizes equal to the ManagedCluster's own API URL
        (no name-only fallback), and the expected hub is the server in that cluster's
        ``<cluster>-import`` bootstrap kubeconfig - the value remediation writes. A
        cluster with no matching context at all is skipped (non-fatal). A cluster that
        cannot be proven - no usable API URL, an ambiguous or near-matching context, an
        unusable expected endpoint, a client bound elsewhere, or an observed hub that
        differs only in scheme, port or path - is ``unverified``: never inspected or
        mutated past that point, never reported verified, and it fails the step before
        any remediation in the run, as ``failed`` and timeouts do. A kubeconfig that
        cannot be loaded and validated raises before any cluster is read (#349), so
        verify() fails and the step is not checkpointed. A remediated cluster's re-check
        is strict: failed remediation, persistent wrong-hub state, or an unverifiable
        re-check fails post-activation instead of reporting success.
        """

        logger.info("Verifying klusterlet connections to new hub...")

        kubeconfig_data = self._load_kubeconfig_data()
        # Every spoke client of this run is built from credentials materialized here
        # (amendment §6); the directory outlives every worker and is always removed.
        self._spoke_material_dir = new_private_dir()
        try:
            self._verify_klusterlet_targets(kubeconfig_data)
        finally:
            remove_private_dir(self._spoke_material_dir)
            self._spoke_material_dir = None

    def _verify_klusterlet_targets(self, kubeconfig_data: Dict) -> None:
        cluster_targets = self._klusterlet_cluster_targets()
        if not cluster_targets:
            logger.info("No managed clusters to verify klusterlet connections")
            return

        verified, wrong_hub, unreachable, timed_out, failed, unverified = self._check_klusterlet_targets(
            cluster_targets,
            kubeconfig_data,
        )

        if verified:
            logger.info(
                "✓ Klusterlet verified for %d cluster(s): %s",
                len(verified),
                ", ".join(verified),
            )
        # Every cluster is classified before any remediation; any cluster that is not
        # proven blocks remediation for all of them (amendment §2.8).
        blocking = []
        if timed_out:
            blocking.append(f"Klusterlet verification timed out for cluster(s): {', '.join(timed_out)}")
        if failed:
            blocking.append(f"Klusterlet verification failed for cluster(s): {', '.join(failed)}")
        if unverified:
            blocking.append(
                "Klusterlet hub connection could not be verified (operator review required) for cluster(s): "
                + ", ".join(f"{name} ({reason})" for name, reason in unverified)
            )
        if blocking:
            raise SwitchoverError("; ".join(blocking))
        if wrong_hub:
            self._remediate_wrong_hub_klusterlets(wrong_hub)
        if unreachable:
            logger.info(
                "Klusterlet verification skipped for %d cluster(s) (unreachable or uninspectable): %s",
                len(unreachable),
                ", ".join(unreachable),
            )

    def _force_klusterlet_reconnect(self, cluster_name: str, snapshot: Any, api_url: str, expected_hub: str) -> bool:
        """
        Force a managed cluster's klusterlet to reconnect to the new hub.

        This is needed when both hubs have the same cluster imported (due to passive
        sync) and the klusterlet is still connected to the old hub. The fix involves:
        1. Getting the import secret from the new hub
        2. Patching or creating the bootstrap-hub-kubeconfig secret on the managed cluster
        3. Restarting the klusterlet deployment

        Note: This method intentionally uses raw kubernetes client APIs instead of
        KubeClient because it connects to managed clusters (not the hub) using
        dynamically-loaded kubeconfig contexts. KubeClient is designed for persistent
        hub connections with specific retry/validation behavior that doesn't apply here.

        Args:
            cluster_name: Name of the ManagedCluster
            snapshot: The cluster's credential snapshot (amendment §6); the client is
                built from it alone, never from the kubeconfig files
            api_url: The ManagedCluster API URL the context was proven against; the
                client must be bound to it before anything is written
            expected_hub: The expected hub endpoint the classification validated; the
                re-read import manifest must still carry it, or nothing is written

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Force-reconnecting klusterlet for %s to new hub...", cluster_name)

            # Step 1: Get the import secret from the new hub
            import_yaml = self._get_import_secret(cluster_name)
            if not import_yaml:
                return False
            # The manifest is re-read here; it must still name the endpoint that was validated
            # and classified, or a replaced secret could write an unvalidated hub (amendment §2.4).
            found, manifest_server = _bootstrap_server_from_import(import_yaml)
            if not found or not normalized_equal(manifest_server, expected_hub):
                if not found:
                    refusal = REASON_EXPECTED_ENDPOINT_UNAVAILABLE
                elif parse_endpoint(manifest_server)[0] is None:
                    refusal = REASON_EXPECTED_ENDPOINT_MALFORMED
                else:
                    refusal = KLUSTERLET_REASON_EXPECTED_ENDPOINT_CHANGED
                logger.warning(
                    "Not reconnecting klusterlet for %s: the import secret changed after verification (%s)",
                    cluster_name,
                    refusal,
                )
                return False

            # Build isolated per-context clients once; all steps share the same
            # ApiClient so no further global kubeconfig mutation is needed.
            v1, apps_v1 = self._build_managed_cluster_clients(snapshot, api_url)

            # Step 2: Apply the import manifest to update the bootstrap secret
            bootstrap_namespace = self._apply_import_manifest(v1, import_yaml, cluster_name)

            # Step 3: Wait for secret to be visible, then restart the klusterlet deployment
            self._wait_for_secret_visibility(v1, cluster_name, bootstrap_namespace)
            self._restart_klusterlet(apps_v1, cluster_name, bootstrap_namespace)

            logger.info("Force-reconnected klusterlet for %s", cluster_name)
            return True

        except SnapshotError as refusal:
            logger.warning("Not reconnecting klusterlet for %s: %s", cluster_name, refusal.reason)
            return False
        except Exception as e:
            # Never the exception text: the client loader re-reads kubeconfig files, and a
            # constructor error quotes the offending value (a token or a key).
            logger.warning(
                "Failed to force-reconnect klusterlet for %s: %s%s",
                cluster_name,
                type(e).__name__,
                f" status={e.status}" if isinstance(e, ApiException) else "",
            )
            return False

    def _build_managed_cluster_clients(self, snapshot: SpokeSnapshot, api_url: str) -> tuple:
        """Build isolated CoreV1Api and AppsV1Api for a managed cluster from its snapshot.

        The client is constructed from the snapshot's captured credentials only, materialized
        in this run's private directory, with the selected context named explicitly; it must
        prove effective TLS verification, its own credential material and the proven endpoint
        before it is returned (amendment §6, §2.7). The kubeconfig files are never re-read.

        Raises:
            SnapshotError: a sanitized refusal; no client is returned and nothing is read
        """
        if self._spoke_material_dir is None:
            raise SnapshotError("client_construction_failed")
        api_client = build_api_client(snapshot, self._spoke_material_dir, api_url)
        return client.CoreV1Api(api_client=api_client), client.AppsV1Api(api_client=api_client)

    def _spoke_snapshot(self, kubeconfig_data: Dict, context_name: str) -> SpokeSnapshot:
        """Capture the selected context from the validated merge (amendment §6), or raise."""
        context = next(entry for entry in kubeconfig_data.get("contexts", []) if entry["name"] == context_name)
        body = context.get("context") or {}
        cluster_name = body.get("cluster")
        user_name = body.get("user") or None
        cluster = next(entry for entry in kubeconfig_data.get("clusters", []) if entry["name"] == cluster_name)
        user = next((entry for entry in kubeconfig_data.get("users", []) if entry["name"] == user_name), None)
        return build_snapshot(
            context_name,
            cluster.get("cluster") or {},
            self._source_dir("clusters", cluster_name),
            (user.get("user") or {}) if user is not None else None,
            self._source_dir("users", user_name) if user_name else "",
            user_name,
            MAX_KUBECONFIG_SIZE,
        )

    def _source_dir(self, section: str, name: Optional[str]) -> str:
        source = self._kubeconfig_sources.get((section, name or ""))
        return os.path.dirname(source) if source else os.getcwd()

    def _expected_hub_endpoint(self, cluster_name: str) -> Tuple[Optional[str], Optional[str]]:
        """The expected hub endpoint for one cluster (amendment §2.4), or the reason it is unusable.

        It is the ``server`` of the ``bootstrap-hub-kubeconfig`` Secret carried in the
        cluster's ``<cluster>-import`` secret on the new hub: the value remediation writes,
        so a successful repair re-verifies. Returns ``(server, None)``, or ``(None,
        expected_endpoint_unavailable | expected_endpoint_malformed)``.
        """
        import_yaml = self._get_import_secret(cluster_name)
        if not import_yaml:
            return None, REASON_EXPECTED_ENDPOINT_UNAVAILABLE
        found, server = _bootstrap_server_from_import(import_yaml)
        if not found:
            return None, REASON_EXPECTED_ENDPOINT_UNAVAILABLE
        if parse_endpoint(server)[0] is None:
            return None, REASON_EXPECTED_ENDPOINT_MALFORMED
        return server, None

    def _get_import_secret(self, cluster_name: str) -> Optional[str]:
        """Get and decode the import secret from the new hub.

        Args:
            cluster_name: Name of the ManagedCluster

        Returns:
            Decoded import YAML string, or None if not found
        """
        import_secret = self.secondary.get_secret(
            namespace=cluster_name,
            name=f"{cluster_name}-import",
        )
        if not import_secret:
            logger.warning("No import secret found for %s on new hub", cluster_name)
            return None

        import_yaml_b64 = import_secret.get("data", {}).get("import.yaml", "")
        if not import_yaml_b64:
            logger.warning("Import secret for %s has no import.yaml data", cluster_name)
            return None

        return base64.b64decode(import_yaml_b64).decode("utf-8")

    def _apply_import_manifest(self, v1: client.CoreV1Api, import_yaml: str, cluster_name: str) -> str:
        """Apply the import manifest to patch or create the bootstrap secret.

        Args:
            v1: CoreV1Api bound to the managed cluster's context
            import_yaml: Decoded import YAML string
            cluster_name: Name of the ManagedCluster
        """

        # Parse the import YAML and apply each resource
        import_docs = list(yaml.safe_load_all(import_yaml))
        bootstrap_namespace = ""

        for doc in import_docs:
            if not doc:
                continue
            kind = doc.get("kind", "")
            name = doc.get("metadata", {}).get("name", "")
            namespace = doc.get("metadata", {}).get("namespace") or MANAGED_CLUSTER_AGENT_NAMESPACE

            try:
                if kind == "Secret" and name == "bootstrap-hub-kubeconfig":
                    bootstrap_namespace = namespace
                    self._patch_or_create_bootstrap_secret(v1, namespace, doc, cluster_name)
                    # Only the first bootstrap Secret is the one whose server was validated
                    # (_bootstrap_server_from_import); a later duplicate is never applied.
                    break
            except ApiException as e:
                # Log only status/reason to avoid leaking sensitive data from exception body
                logger.debug(
                    "Error applying managed-cluster import resource: status=%s reason=%s",
                    getattr(e, "status", None),
                    getattr(e, "reason", None),
                )
                raise SwitchoverError(
                    "Failed to apply bootstrap-hub-kubeconfig secret on "
                    f"{cluster_name}: status={getattr(e, 'status', None)} "
                    f"reason={getattr(e, 'reason', None)}"
                ) from e

        if not bootstrap_namespace:
            raise SwitchoverError(f"Import manifest for {cluster_name} has no bootstrap-hub-kubeconfig secret")

        return bootstrap_namespace

    def _patch_or_create_bootstrap_secret(
        self,
        v1: client.CoreV1Api,
        namespace: str,
        body: Dict,
        cluster_name: str,
    ) -> None:
        """Patch an existing bootstrap secret, creating it when absent."""
        try:
            v1.patch_namespaced_secret(
                name="bootstrap-hub-kubeconfig",
                namespace=namespace,
                body=body,
                _request_timeout=DELETE_REQUEST_TIMEOUT,
            )
            logger.debug("Patched bootstrap-hub-kubeconfig secret on %s", cluster_name)
            return
        except ApiException as e:
            if e.status != 404:
                raise

        try:
            v1.create_namespaced_secret(
                namespace=namespace,
                body=_secret_create_body(body),
                _request_timeout=DELETE_REQUEST_TIMEOUT,
            )
            logger.debug("Created bootstrap-hub-kubeconfig secret on %s", cluster_name)
        except ApiException as e:
            if e.status != 409:
                raise
            v1.patch_namespaced_secret(
                name="bootstrap-hub-kubeconfig",
                namespace=namespace,
                body=body,
                _request_timeout=DELETE_REQUEST_TIMEOUT,
            )
            logger.debug("Patched bootstrap-hub-kubeconfig secret on %s after create conflict", cluster_name)

    def _wait_for_secret_visibility(self, v1: client.CoreV1Api, cluster_name: str, namespace: str) -> None:
        """Wait for the bootstrap-hub-kubeconfig secret to be visible.

        Args:
            v1: CoreV1Api bound to the managed cluster's context
            cluster_name: Name of the ManagedCluster
            namespace: Namespace containing the bootstrap-hub-kubeconfig secret
        """

        def secret_exists() -> WaitConditionResult:
            """Check if bootstrap-hub-kubeconfig secret exists."""
            try:
                v1.read_namespaced_secret(
                    name="bootstrap-hub-kubeconfig",
                    namespace=namespace,
                    _request_timeout=DELETE_REQUEST_TIMEOUT,
                )
                return WaitConditionResult.complete("secret exists")
            except ApiException as e:
                if e.status == 404:
                    return WaitConditionResult.pending("secret not found")
                return WaitConditionResult.pending(f"api status={getattr(e, 'status', 'unknown')}")

        secret_ready = wait_for_condition(
            description=f"bootstrap-hub-kubeconfig secret on {cluster_name}",
            condition_fn=secret_exists,
            timeout=SECRET_VISIBILITY_TIMEOUT,
            interval=SECRET_VISIBILITY_INTERVAL,
            logger=logger,
        )

        if not secret_ready:
            logger.warning(
                "bootstrap-hub-kubeconfig secret not visible on %s after timeout",
                cluster_name,
            )
            raise SwitchoverError(
                "bootstrap-hub-kubeconfig secret not visible on " f"{cluster_name} after {SECRET_VISIBILITY_TIMEOUT}s"
            )

    def _restart_klusterlet(self, apps_v1: client.AppsV1Api, cluster_name: str, namespace: str) -> None:
        """Restart the klusterlet deployment on the managed cluster.

        Args:
            apps_v1: AppsV1Api bound to the managed cluster's context
            cluster_name: Name of the ManagedCluster
            namespace: Namespace containing the klusterlet deployment
        """
        import time as time_module

        try:
            # Trigger a rollout restart by patching the deployment
            patch = {
                "spec": {
                    "template": {"metadata": {"annotations": {"acm-switchover/restart": str(int(time_module.time()))}}}
                }
            }
            apps_v1.patch_namespaced_deployment(
                name="klusterlet",
                namespace=namespace,
                body=patch,
                _request_timeout=DELETE_REQUEST_TIMEOUT,
            )
            logger.debug("Triggered klusterlet restart on %s", cluster_name)
        except ApiException as e:
            logger.warning("Failed to restart klusterlet on %s: status=%s reason=%s", cluster_name, e.status, e.reason)

    def _load_kubeconfig_data(self) -> Dict:
        """Load, validate and merge every KUBECONFIG file once, or raise SwitchoverError.

        Fail-closed (design 2026-07-29 section 1, #349): a missing, unreadable, oversized,
        invalid or structurally malformed file aborts the klusterlet step before any
        cluster is read; the size limit cannot be bypassed.
        """
        if kubeconfig_size_override_ignored(os.environ.get("ACM_KUBECONFIG_MAX_SIZE")):
            logger.warning(
                "ACM_KUBECONFIG_MAX_SIZE must be a positive byte count; using the default %d bytes "
                "(the kubeconfig size limit can no longer be disabled)",
                MAX_KUBECONFIG_SIZE,
            )
        merged, self._kubeconfig_sources = load_merged_kubeconfig_with_sources(
            os.environ.get("KUBECONFIG"), MAX_KUBECONFIG_SIZE
        )
        return merged

    def _check_klusterlet_connection(  # noqa: C901
        self, snapshot: Any, cluster_name: str, expected_hub: str, api_url: str
    ) -> Tuple[str, Optional[str]]:
        """
        Check if a managed cluster's klusterlet is connected to the expected hub.

        Note: This method intentionally uses raw kubernetes client APIs instead of
        KubeClient because it connects to managed clusters (not the hub) using
        dynamically-loaded kubeconfig contexts.

        Args:
            snapshot: The cluster's credential snapshot; the client is built from it alone
            cluster_name: ManagedCluster name (for logging)
            expected_hub: Expected hub API server URL (amendment §2.4)
            api_url: The ManagedCluster API URL the context was proven against

        Returns:
            ``(result, reason)``: "verified" if connected to the expected hub; "wrong_hub"
            only for a genuinely different host (amendment §2.3); "unverified" with a
            reason for anything that cannot be proven; "unreachable" for the documented
            non-fatal skips (no hub secret, no server, unreadable embedded kubeconfig);
            "failed" for client/API/probe failures that must fail closed.
        """
        try:
            # Build an isolated per-context client; does not mutate global config.
            v1, _ = self._build_managed_cluster_clients(snapshot, api_url)
        except SnapshotError as refusal:
            logger.debug("No klusterlet client for %s: %s", cluster_name, refusal.reason)
            return KLUSTERLET_RESULT_UNVERIFIED, refusal.reason
        except Exception as e:
            logger.debug("Failed to build klusterlet client for %s: %s", cluster_name, type(e).__name__)
            return KLUSTERLET_RESULT_FAILED, None

        # Get the hub-kubeconfig-secret
        try:
            secret = v1.read_namespaced_secret(
                name="hub-kubeconfig-secret",
                namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
                _request_timeout=KLUSTERLET_API_READ_TIMEOUT,
            )
        except ApiException as e:
            if e.status != 404:
                logger.debug("Failed to read hub-kubeconfig-secret for %s: status=%s", cluster_name, e.status)
                return KLUSTERLET_RESULT_FAILED, None
            try:
                secret = v1.read_namespaced_secret(
                    name="bootstrap-hub-kubeconfig",
                    namespace=MANAGED_CLUSTER_AGENT_NAMESPACE,
                    _request_timeout=KLUSTERLET_API_READ_TIMEOUT,
                )
            except ApiException as bootstrap_exc:
                if bootstrap_exc.status == 404:
                    return KLUSTERLET_RESULT_UNREACHABLE, None
                logger.debug(
                    "Failed to read bootstrap-hub-kubeconfig for %s: status=%s", cluster_name, bootstrap_exc.status
                )
                return KLUSTERLET_RESULT_FAILED, None
            except Exception as bootstrap_exc:
                logger.debug(
                    "Failed to read bootstrap-hub-kubeconfig for %s: %s", cluster_name, type(bootstrap_exc).__name__
                )
                return KLUSTERLET_RESULT_FAILED, None
        except Exception as e:
            logger.debug("Failed to read hub-kubeconfig-secret for %s: %s", cluster_name, type(e).__name__)
            return KLUSTERLET_RESULT_FAILED, None

        found, klusterlet_hub = _observed_hub_server(secret)
        if not found:
            # Absent or unreadable: the documented non-fatal skip (amendment §2.3).
            return KLUSTERLET_RESULT_UNREACHABLE, None

        result, reason = classify_hub(expected_hub, klusterlet_hub)
        logger.debug(
            "Cluster %s klusterlet hub %s, expected %s: %s%s",
            cluster_name,
            redact(klusterlet_hub),
            redact(expected_hub),
            result,
            f" ({reason})" if reason else "",
        )
        return result, reason


class _WrongHub(NamedTuple):
    """A cluster classified `wrong_hub`: everything its repair and re-check must reuse."""

    cluster: str
    snapshot: Any
    expected_hub: str
    api_url: str


def _server_from_kubeconfig_b64(encoded: Any) -> Tuple[bool, Any]:
    """``(present, server)`` from a base64 kubeconfig: the one "present server" rule (amendment §2.3).

    Absent - nothing encoded, undecodable or unparseable YAML, a non-mapping document, no
    clusters, a non-mapping cluster, no ``server`` key - is ``(False, None)``. A present
    ``server`` of any value is returned as-is for validation, so a null, empty or non-string
    server is malformed, not absent. Parser errors are never reported: they can quote secrets.
    """
    if not isinstance(encoded, str) or not encoded:
        return False, None
    try:
        kubeconfig_data = yaml.safe_load(base64.b64decode(encoded).decode("utf-8"))
    except Exception:  # noqa: BLE001 - unreadable embedded YAML is absent here
        return False, None
    if not isinstance(kubeconfig_data, dict):
        return False, None
    clusters = kubeconfig_data.get("clusters")
    if not isinstance(clusters, list) or not clusters or not isinstance(clusters[0], dict):
        return False, None
    cluster_info = clusters[0].get("cluster")
    if not isinstance(cluster_info, dict) or "server" not in cluster_info:
        return False, None
    return True, cluster_info["server"]


def _observed_hub_server(secret: Any) -> Tuple[bool, Any]:
    """``(present, server)`` from a spoke hub-kubeconfig secret; absent is Python's documented skip."""
    return _server_from_kubeconfig_b64((getattr(secret, "data", None) or {}).get("kubeconfig", ""))


def _bootstrap_server_from_import(import_yaml: str) -> Tuple[bool, Any]:
    """``(present, server)`` for the bootstrap-hub-kubeconfig Secret in an import manifest.

    The manifest is scanned lazily and the scan stops at the bootstrap Secret, so the rest of
    the manifest (CRDs, the klusterlet deployment) is never parsed for this lookup.
    """
    documents = yaml.safe_load_all(import_yaml)
    while True:
        try:
            document = next(documents)
        except StopIteration:
            return False, None
        except Exception:  # noqa: BLE001 - an unreadable manifest has no usable endpoint
            return False, None
        if not isinstance(document, dict) or document.get("kind") != "Secret":
            continue
        if (document.get("metadata") or {}).get("name") != "bootstrap-hub-kubeconfig":
            continue
        return _server_from_kubeconfig_b64((document.get("data") or {}).get("kubeconfig"))
