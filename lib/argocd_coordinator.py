"""
Argo CD pause coordination for ACM switchover.

Centralizes the shared logic for pausing ArgoCD auto-sync across one or more
hubs. Used by both PrimaryPreparation (full switchover) and restore-only mode.
"""

import copy
import logging
from typing import Any, Dict, List, Optional, Tuple

from lib import argocd as argocd_lib
from lib.kube_client import KubeClient
from lib.utils import StateManager

logger = logging.getLogger("acm_switchover")


class ArgoCDPauseCoordinator:
    """Coordinates ArgoCD auto-sync pause across one or more hubs.

    Handles detection, listing, filtering, entry recovery, pause execution,
    and state persistence. Callers are responsible for error-style adaptation
    (raising SwitchoverError vs returning bool).
    """

    def __init__(self, state: StateManager, dry_run: bool = False):
        self.state = state
        self.dry_run = dry_run

    @staticmethod
    def _pause_entry_matches(entry: Dict[str, Any], hub: str, namespace: str, name: str) -> bool:
        """Return True when an Argo CD pause-state entry matches one Application."""
        return entry.get("hub") == hub and entry.get("namespace") == namespace and entry.get("name") == name

    @staticmethod
    def _is_pause_applied(entry: Dict[str, Any]) -> bool:
        """Treat missing pause_applied as legacy-applied unless the entry is dry-run only."""
        return entry.get("pause_applied", not entry.get("dry_run", False))

    @staticmethod
    def _pause_marker_matches(app: Dict[str, Any], run_id: str) -> bool:
        """Return True when the live Application carries this run's pause marker."""
        annotations = (app.get("metadata") or {}).get("annotations") or {}
        return annotations.get(argocd_lib.ARGOCD_PAUSED_BY_ANNOTATION) == run_id

    def _find_pause_entry(
        self,
        paused_apps: List[Dict[str, Any]],
        hub: str,
        namespace: str,
        name: str,
    ) -> Optional[Dict[str, Any]]:
        for entry in paused_apps:
            if self._pause_entry_matches(entry, hub, namespace, name):
                return entry
        return None

    def _persist_paused_apps(self, paused_apps: List[Dict[str, Any]]) -> None:
        """Persist a deep copy so StateManager notices nested entry changes."""
        self.state.set_config("argocd_paused_apps", copy.deepcopy(paused_apps))

    def _upsert_pause_entry(
        self,
        paused_apps: List[Dict[str, Any]],
        hub: str,
        namespace: str,
        name: str,
        original_sync_policy: Dict[str, Any],
        *,
        pause_applied: bool,
    ) -> Dict[str, Any]:
        entry = self._find_pause_entry(paused_apps, hub, namespace, name)
        if entry is None:
            entry = {"hub": hub, "namespace": namespace, "name": name}
            paused_apps.append(entry)

        entry["original_sync_policy"] = original_sync_policy
        entry["pause_applied"] = pause_applied
        entry.pop("pause_state", None)
        entry.pop("pause_run_id", None)
        if self.dry_run:
            entry["dry_run"] = True
        else:
            entry.pop("dry_run", None)
        return entry

    def _remove_pause_entry(
        self,
        paused_apps: List[Dict[str, Any]],
        hub: str,
        namespace: str,
        name: str,
    ) -> None:
        paused_apps[:] = [entry for entry in paused_apps if not self._pause_entry_matches(entry, hub, namespace, name)]

    def pause_hubs(self, hubs: List[Tuple[KubeClient, str]]) -> Tuple[List[Dict[str, Any]], int]:
        """Pause ArgoCD auto-sync for ACM-touching Applications on the given hubs.

        Args:
            hubs: List of (KubeClient, hub_label) tuples to process.

        Returns:
            Tuple of (paused_apps list, failure_count).

        Raises:
            Exception: Propagated from ArgoCD detection or application listing.
        """
        discoveries = []
        for client, hub_label in hubs:
            discovery = argocd_lib.detect_argocd_installation(client)
            discoveries.append((client, hub_label, discovery))

        if not any(discovery.has_applications_crd for _, _, discovery in discoveries):
            logger.info("Argo CD Applications CRD not found on any hub; skipping Argo CD pause")
            self.state.set_config("argocd_paused_apps", [])
            self.state.set_config("argocd_run_id", None)
            self.state.set_config("argocd_pause_dry_run", False)
            return [], 0

        run_id = argocd_lib.run_id_or_new(self.state.get_config("argocd_run_id"))
        self.state.set_config("argocd_run_id", run_id)
        self.state.set_config("argocd_pause_dry_run", self.dry_run)
        paused_apps: List[Dict[str, Any]] = copy.deepcopy(self.state.get_config("argocd_paused_apps") or [])
        pause_failures = 0

        applications_by_hub: List[Tuple[KubeClient, str, List[Dict[str, Any]]]] = []
        pause_blockers = []

        for client, hub_label, discovery in discoveries:
            if not discovery.has_applications_crd:
                logger.info(
                    "Argo CD Applications CRD not found on %s; skipping Argo CD pause",
                    hub_label,
                )
                continue

            apps = argocd_lib.list_argocd_applications(client, namespaces=None)
            applications_by_hub.append((client, hub_label, apps))
            blockers = argocd_lib.find_argocd_pause_blockers(apps)
            for blocker in blockers:
                logger.error("Argo CD pause blocked on %s: %s", hub_label, blocker.message)
            pause_blockers.extend(blockers)

        if pause_blockers:
            return paused_apps, len(pause_blockers)

        for client, hub_label, apps in applications_by_hub:
            acm_apps = argocd_lib.find_acm_touching_apps(apps)

            for impact in acm_apps:
                meta = impact.app.get("metadata", {}) or {}
                namespace = meta.get("namespace", "")
                name = meta.get("name", "")
                sync_policy = dict((impact.app.get("spec", {}) or {}).get("syncPolicy") or {})
                has_automated = argocd_lib.is_autosync_enabled(impact.app)
                existing_entry = self._find_pause_entry(paused_apps, hub_label, namespace, name)

                # Entry recovery: recorded in state but not yet confirmed applied
                if existing_entry:
                    if not self._is_pause_applied(existing_entry) and not self.dry_run and not has_automated:
                        expected_run_id = existing_entry.get("pause_run_id") or run_id
                        if not self._pause_marker_matches(impact.app, expected_run_id):
                            self._remove_pause_entry(paused_apps, hub_label, namespace, name)
                            self._persist_paused_apps(paused_apps)
                            logger.debug(
                                "  Removed unconfirmed Argo CD pause state for %s/%s on %s (marker missing)",
                                namespace,
                                name,
                                hub_label,
                            )
                            continue
                        existing_entry["pause_applied"] = True
                        existing_entry.pop("dry_run", None)
                        existing_entry.pop("pause_state", None)
                        existing_entry.pop("pause_run_id", None)
                        self._persist_paused_apps(paused_apps)
                        logger.info(
                            "  Recovered Argo CD pause state for %s/%s on %s",
                            namespace,
                            name,
                            hub_label,
                        )
                        continue
                    # Clobber guard: already paused and recorded
                    if self._is_pause_applied(existing_entry) and not has_automated:
                        logger.debug(
                            "  Skip %s/%s (already paused and recorded)",
                            namespace,
                            name,
                        )
                        continue

                if not has_automated:
                    logger.debug("  Skip %s/%s (no auto-sync)", namespace, name)
                    continue

                # Upsert provisional entry (pause_applied=False), persist before API call
                entry = self._upsert_pause_entry(
                    paused_apps,
                    hub_label,
                    namespace,
                    name,
                    sync_policy,
                    pause_applied=False,
                )
                self._persist_paused_apps(paused_apps)

                result = argocd_lib.pause_autosync(client, impact.app, run_id)

                if result.patched:
                    entry["original_sync_policy"] = result.original_sync_policy
                    entry["pause_applied"] = not self.dry_run
                    entry.pop("pause_state", None)
                    entry.pop("pause_run_id", None)
                    if self.dry_run:
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
                    self._persist_paused_apps(paused_apps)
                elif result.error:
                    if result.patch_applied is True:
                        entry["original_sync_policy"] = result.original_sync_policy
                        entry["pause_applied"] = True
                        entry.pop("pause_state", None)
                        entry.pop("pause_run_id", None)
                        if not self.dry_run:
                            entry.pop("dry_run", None)
                        self._persist_paused_apps(paused_apps)
                    elif result.patch_applied is False:
                        self._remove_pause_entry(paused_apps, hub_label, namespace, name)
                        self._persist_paused_apps(paused_apps)
                    else:
                        entry["original_sync_policy"] = result.original_sync_policy
                        entry["pause_applied"] = False
                        entry["pause_state"] = "unknown"
                        entry["pause_run_id"] = run_id
                        self._persist_paused_apps(paused_apps)
                    pause_failures += 1
                else:
                    self._remove_pause_entry(paused_apps, hub_label, namespace, name)
                    self._persist_paused_apps(paused_apps)
                    logger.debug("  Skip %s/%s (no auto-sync)", result.namespace, result.name)

        return paused_apps, pause_failures
