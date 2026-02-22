"""
Primary hub preparation module for ACM switchover.
"""

# Runbook: Steps 1-3 (Method 1) / F1-F3 (Method 2)

import logging
from typing import Any, Dict, Optional

from kubernetes.client.rest import ApiException

from lib import argocd as argocd_lib
from lib.constants import (
    BACKUP_NAMESPACE,
    DISABLE_AUTO_IMPORT_ANNOTATION,
    LOCAL_CLUSTER_NAME,
    OBSERVABILITY_NAMESPACE,
    THANOS_COMPACTOR_LABEL_SELECTOR,
    THANOS_COMPACTOR_STATEFULSET,
    THANOS_SCALE_DOWN_WAIT,
)
from lib.exceptions import SwitchoverError
from lib.kube_client import KubeClient
from lib.utils import StateManager, is_acm_version_ge

logger = logging.getLogger("acm_switchover")


class PrimaryPreparation:
    """Handles preparation steps on primary hub."""

    def __init__(
        self,
        primary_client: KubeClient,
        state_manager: StateManager,
        acm_version: str,
        has_observability: bool,
        dry_run: bool = False,
        argocd_manage: bool = False,
        secondary_client: Optional[KubeClient] = None,
    ):
        self.primary = primary_client
        self.state = state_manager
        self.acm_version = acm_version
        self.has_observability = has_observability
        self.dry_run = dry_run
        self.argocd_manage = argocd_manage
        self.secondary = secondary_client

    def prepare(self) -> bool:
        """
        Execute all primary hub preparation steps.

        Returns:
            True if all steps completed successfully
        """
        logger.info("Starting primary hub preparation...")

        try:
            # Optional: Pause Argo CD auto-sync for ACM-touching Applications (both hubs)
            if self.argocd_manage:
                with self.state.step("pause_argocd_apps", logger) as should_run:
                    if should_run:
                        self._pause_argocd_acm_apps()

            # Step 1: Pause BackupSchedule
            with self.state.step("pause_backup_schedule", logger) as should_run:
                if should_run:
                    self._pause_backup_schedule()

            # Step 2: Add disable-auto-import annotations
            with self.state.step("disable_auto_import", logger) as should_run:
                if should_run:
                    self._disable_auto_import()

            # Step 3: Scale down Thanos compactor (if Observability present)
            if self.has_observability:
                with self.state.step("scale_down_thanos", logger) as should_run:
                    if should_run:
                        self._scale_down_thanos_compactor()
                        logger.info(
                            "Optional: pause Observatorium API on the old hub manually if needed (runbook Step 3)."
                        )
            else:
                logger.info("Skipping Thanos compactor scaling (Observability not detected)")

            logger.info("Primary hub preparation completed successfully")
            return True

        except SwitchoverError as e:
            logger.error("Primary hub preparation failed: %s", e)
            self.state.add_error(str(e), "primary_preparation")
            return False
        except Exception as e:
            logger.error("Unexpected error during primary preparation: %s", e)
            self.state.add_error(f"Unexpected: {str(e)}", "primary_preparation")
            return False

    def _pause_argocd_acm_apps(self) -> None:
        """Pause auto-sync for ACM-touching Argo CD Applications on primary and optionally secondary hub."""
        hubs = [(self.primary, "primary")] + ([(self.secondary, "secondary")] if self.secondary else [])
        discoveries = []
        for client, hub_label in hubs:
            discovery = argocd_lib.detect_argocd_installation(client)
            discoveries.append((client, hub_label, discovery))
        if not any(discovery.has_applications_crd for _, _, discovery in discoveries):
            logger.info("Argo CD Applications CRD not found on any hub; skipping Argo CD pause")
            return
        run_id = argocd_lib.run_id_or_new(self.state.get_config("argocd_run_id"))
        self.state.set_config("argocd_run_id", run_id)
        self.state.set_config("argocd_pause_dry_run", self.dry_run)
        paused_apps = list(self.state.get_config("argocd_paused_apps") or [])

        for client, hub_label, discovery in discoveries:
            if not discovery.has_applications_crd:
                logger.info("Argo CD Applications CRD not found on %s; skipping Argo CD pause", hub_label)
                continue
            apps = argocd_lib.list_argocd_applications(client, namespaces=None)
            acm_apps = argocd_lib.find_acm_touching_apps(apps)
            for impact in acm_apps:
                result = argocd_lib.pause_autosync(client, impact.app, run_id)
                if result.patched:
                    entry: Dict[str, Any] = {
                        "hub": hub_label,
                        "namespace": result.namespace,
                        "name": result.name,
                        "original_sync_policy": result.original_sync_policy,
                    }
                    if self.dry_run:
                        entry["dry_run"] = True
                        logger.info(
                            "  [DRY-RUN] Would pause Argo CD Application %s/%s on %s",
                            result.namespace,
                            result.name,
                            hub_label,
                        )
                    else:
                        logger.info(
                            "  Paused Argo CD Application %s/%s on %s",
                            result.namespace,
                            result.name,
                            hub_label,
                        )
                    paused_apps.append(entry)
                    # Persist after each success so a crash doesn't lose track of paused apps.
                    self.state.set_config("argocd_paused_apps", paused_apps)
                    self.state.save_state()
                else:
                    logger.debug("  Skip %s/%s (no auto-sync)", result.namespace, result.name)
        logger.info(
            "Argo CD: %d Application(s) paused (run_id=%s). Left paused by default; use --argocd-resume-after-switchover or --argocd-resume-only after retargeting Git.",
            len(paused_apps),
            run_id,
        )

    def _pause_backup_schedule(self):
        """Pause BackupSchedule (version-aware)."""
        logger.info("Pausing BackupSchedule...")

        # Get BackupSchedule
        backup_schedules = self.primary.list_custom_resources(
            group="cluster.open-cluster-management.io",
            version="v1beta1",
            plural="backupschedules",
            namespace=BACKUP_NAMESPACE,
            max_items=1,
        )

        if not backup_schedules:
            logger.warning("No BackupSchedule found to pause")
            return

        # Assume first BackupSchedule (typically only one exists)
        bs = backup_schedules[0]
        bs_name = bs.get("metadata", {}).get("name")

        if not bs_name:
            logger.error("BackupSchedule found but has no name in metadata")
            return

        # Check if already paused
        if bs.get("spec", {}).get("paused") is True:
            logger.info("BackupSchedule %s is already paused", bs_name)
            # Still save to state for finalization (in case new hub needs it)
            if not self.state.get_config("saved_backup_schedule"):
                self.state.set_config("saved_backup_schedule", bs)
            return

        # Always save the BackupSchedule to state for finalization
        # This allows the new hub to recreate the schedule if it doesn't have one
        # (common in passive sync scenarios where secondary only had a Restore)
        self.state.set_config("saved_backup_schedule", bs)

        # ACM 2.12+ supports pausing via spec.paused
        if is_acm_version_ge(self.acm_version, "2.12.0"):
            logger.info("Using spec.paused for ACM %s", self.acm_version)

            patch = {"spec": {"paused": True}}
            self.primary.patch_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=bs_name,
                patch=patch,
                namespace=BACKUP_NAMESPACE,
            )

            logger.info("BackupSchedule %s paused successfully (saved to state)", bs_name)
        else:
            # ACM 2.11: Need to delete BackupSchedule
            logger.info("ACM %s requires deleting BackupSchedule", self.acm_version)

            self.primary.delete_custom_resource(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                plural="backupschedules",
                name=bs_name,
                namespace=BACKUP_NAMESPACE,
            )

            logger.info("BackupSchedule %s deleted (saved to state)", bs_name)

    def _disable_auto_import(self):
        """Add disable-auto-import annotation to all ManagedClusters."""
        logger.info("Disabling auto-import on ManagedClusters...")

        managed_clusters = self.primary.list_managed_clusters()

        if not managed_clusters:
            logger.warning("No ManagedClusters found")
            return

        count = 0
        for mc in managed_clusters:
            mc_name = mc.get("metadata", {}).get("name")

            # Skip local-cluster
            if mc_name == LOCAL_CLUSTER_NAME:
                logger.debug("Skipping local-cluster")
                continue

            # Check if annotation already exists
            annotations = mc.get("metadata", {}).get("annotations", {})
            if DISABLE_AUTO_IMPORT_ANNOTATION in annotations:
                logger.debug(
                    "ManagedCluster %s already has disable-auto-import annotation",
                    mc_name,
                )
                continue

            # Add annotation
            patch = {"metadata": {"annotations": {DISABLE_AUTO_IMPORT_ANNOTATION: ""}}}

            self.primary.patch_managed_cluster(name=mc_name, patch=patch)

            count += 1
            logger.debug("Added disable-auto-import annotation to %s", mc_name)

        logger.info("Disabled auto-import on %s ManagedCluster(s)", count)

    def _scale_down_thanos_compactor(self):
        """Scale down Thanos compactor StatefulSet."""
        logger.info("Scaling down Thanos compactor...")

        try:
            self.primary.scale_statefulset(
                name=THANOS_COMPACTOR_STATEFULSET,
                namespace=OBSERVABILITY_NAMESPACE,
                replicas=0,
            )

            # Skip verification in dry-run mode
            if self.dry_run:
                logger.info("[DRY-RUN] Skipping Thanos compactor pod verification")
                return

            # Wait a moment and verify no pods running
            import time

            time.sleep(THANOS_SCALE_DOWN_WAIT)

            pods = self.primary.get_pods(
                namespace=OBSERVABILITY_NAMESPACE,
                label_selector=THANOS_COMPACTOR_LABEL_SELECTOR,
            )

            if pods:
                logger.warning("Thanos compactor still has %s pod(s) running", len(pods))
            else:
                logger.info("Thanos compactor scaled down successfully")

        except (RuntimeError, ValueError) as e:
            logger.error("Failed to scale down Thanos compactor: %s", e)
            raise
        except ApiException as e:
            # Don't fail the whole preparation if this is optional
            if e.status == 404:
                logger.warning("Thanos compactor StatefulSet not found (may not exist)")
            else:
                logger.error("Failed to scale down Thanos compactor: %s", e)
                raise
        except Exception as e:
            logger.error("Failed to scale down Thanos compactor: %s", e)
            raise
