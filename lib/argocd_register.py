"""
Argo CD pause coordination for ACM switchover.

Centralizes the shared logic for pausing ArgoCD auto-sync across one or more
hubs. Used by both PrimaryPreparation (full switchover) and restore-only mode.
"""

import copy
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from lib import argocd as argocd_lib
from lib.constants import (
    HUB_ROLE_PRIMARY,
    HUB_ROLE_SECONDARY,
    STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES,
    STATE_KEY_ARGOCD_PAUSED_APP_HUB,
    STATE_KEY_ARGOCD_PAUSED_APPS,
    STATE_KEY_ARGOCD_RUN_ID,
)
from lib.kube_client import KubeClient
from lib.utils import StateManager

logger = logging.getLogger("acm_switchover")


def clear_argocd_pause_state(state: StateManager) -> None:
    """Clear persisted Argo CD pause and discovery namespace state."""
    state.set_config(STATE_KEY_ARGOCD_PAUSED_APPS, [])
    state.set_config(STATE_KEY_ARGOCD_RUN_ID, None)
    state.set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, {})


@dataclass
class RegisterStatus:
    """Snapshot of the pause register."""

    paused_count: int
    run_id: Optional[str]


@dataclass
class PauseSummary:
    """Aggregated result of one pause_hubs() run.

    Every counter describes work this run did (or, in dry-run, would do); none
    of them report pre-existing register contents. ``blocked`` and ``failed``
    are deliberately distinct: a blocker means the tool refused to pause (an
    ApplicationSet owns the Application), a failure means the patch itself
    did not succeed.
    """

    newly_paused: int = 0
    already_paused: int = 0
    recovered: int = 0
    failed: int = 0
    blocked: int = 0
    applications_crd_visible: bool = True
    run_id: Optional[str] = None


class ArgocdPauseRegister:
    """The Argo CD pause register: pause, resume, and status across hubs.

    Invariant (ADR-0001): register entries are exactly the Applications
    currently paused by this tool. Resume removes entries on success;
    dry-run records nothing. Handles detection, listing, filtering, entry
    recovery, pause execution, and state persistence. Callers are
    responsible for error-style adaptation (raising SwitchoverError vs
    returning bool).
    """

    def __init__(self, state: StateManager, dry_run: bool = False):
        self.state = state
        self.dry_run = dry_run

    @classmethod
    def _sanitize_entries(cls, raw: Any) -> List[Dict[str, Any]]:
        """Register entries from a raw state value (deep copy); non-dict and legacy dry-run entries dropped."""
        if not isinstance(raw, list):
            return []
        return [copy.deepcopy(entry) for entry in raw if isinstance(entry, dict) and not entry.get("dry_run")]

    def load_entries(self) -> List[Dict[str, Any]]:
        """Current register entries (deep copy); non-dict and legacy dry-run entries dropped."""
        return self._sanitize_entries(self.state.get_config(STATE_KEY_ARGOCD_PAUSED_APPS))

    @classmethod
    def status_from_config(cls, config: Mapping[str, Any]) -> RegisterStatus:
        """Register snapshot read from a raw state config mapping.

        For callers that hold a state snapshot rather than a StateManager
        (report artifacts). Keeps the register the only reader of its own
        state keys and its own entry filtering rules.
        """
        entries = cls._sanitize_entries(config.get(STATE_KEY_ARGOCD_PAUSED_APPS))
        applied = [entry for entry in entries if cls._is_pause_applied(entry)]
        return RegisterStatus(paused_count=len(applied), run_id=config.get(STATE_KEY_ARGOCD_RUN_ID))

    def status(self) -> RegisterStatus:
        """Snapshot of the register: confirmed-paused entry count and run id."""
        applied = [entry for entry in self.load_entries() if self._is_pause_applied(entry)]
        return RegisterStatus(paused_count=len(applied), run_id=self.state.get_config(STATE_KEY_ARGOCD_RUN_ID))

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
        """Persist a deep copy so StateManager notices nested entry changes.

        No-op in dry-run: the register records nothing it did not do (ADR-0001).
        """
        if self.dry_run:
            return
        self.state.set_config(STATE_KEY_ARGOCD_PAUSED_APPS, copy.deepcopy(paused_apps))

    def _get_discovery_namespaces_by_hub(self) -> Dict[str, List[str]]:
        stored = self.state.get_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES) or {}
        if not isinstance(stored, dict):
            return {}
        return copy.deepcopy(stored)

    def _persist_discovery_namespaces_by_hub(self, namespaces_by_hub: Dict[str, List[str]]) -> None:
        if self.dry_run:
            return
        self.state.set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, copy.deepcopy(namespaces_by_hub))

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

    def resume(
        self,
        primary: Optional[KubeClient],
        secondary: Optional[KubeClient],
        logger: logging.Logger,
    ) -> "argocd_lib.ResumeSummary":
        """
        Resume auto-sync for every registered Application.

        ADR-0001 invariant: restored and already-resumed entries are removed
        from the register immediately (persisted per entry); failures stay for
        retry.  When the register empties, all Argo CD pause state is cleared.
        """
        entries = self.load_entries()
        run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
        summary = argocd_lib.ResumeSummary()
        if not run_id or not entries:
            logger.info("No Argo CD paused apps in state; nothing to resume")
            return summary

        clients = {HUB_ROLE_PRIMARY: primary, HUB_ROLE_SECONDARY: secondary}
        for entry in list(entries):
            hub = entry.get(STATE_KEY_ARGOCD_PAUSED_APP_HUB)
            ns = entry.get("namespace")
            name = entry.get("name")
            original_sync_policy = entry.get("original_sync_policy")
            client = clients.get(hub)
            if not all([hub, ns, name, original_sync_policy is not None]) or client is None:
                summary.failed += 1
                logger.warning(
                    "  Skip entry (hub=%s, namespace=%s, name=%s): unusable record or no client", hub, ns, name
                )
                continue
            if self.dry_run:
                summary.restored += 1
                logger.info("  [DRY-RUN] Would resume Argo CD Application %s/%s on %s", ns, name, hub)
                continue
            result = argocd_lib.resume_autosync(client, ns, name, original_sync_policy, run_id)
            if result.restored:
                summary.restored += 1
                self._remove_pause_entry(entries, hub, ns, name)
                self._persist_paused_apps(entries)
                logger.info("  Resumed %s/%s on %s", ns, name, hub)
            elif argocd_lib.is_resume_noop(result):
                summary.already_resumed += 1
                self._remove_pause_entry(entries, hub, ns, name)
                self._persist_paused_apps(entries)
                logger.info("  Already resumed %s/%s on %s", ns, name, hub)
            else:
                summary.failed += 1
                logger.warning("  Failed %s/%s: %s", ns, name, result.skip_reason or "not restored")

        summary.remaining = len(entries)
        if not self.dry_run and not entries:
            clear_argocd_pause_state(self.state)
            logger.info("Argo CD pause register empty; cleared pause state.")
        return summary

    def pause_hubs(self, hubs: List[Tuple[KubeClient, str]]) -> PauseSummary:
        """Pause ArgoCD auto-sync for ACM-touching Applications on the given hubs.

        Args:
            hubs: List of (KubeClient, hub_label) tuples to process.

        Returns:
            A PauseSummary describing what this run did.

        Raises:
            Exception: Propagated from ArgoCD detection or application listing.
        """
        discoveries = []
        for client, hub_label in hubs:
            discovery = argocd_lib.detect_argocd_installation(client)
            discoveries.append((client, hub_label, discovery))

        if not any(discovery.has_applications_crd for _, _, discovery in discoveries):
            return self._handle_no_applications_crd()

        existing_run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
        run_id = argocd_lib.run_id_or_new(existing_run_id)
        if not self.dry_run:
            self.state.set_config(STATE_KEY_ARGOCD_RUN_ID, run_id)
        summary = PauseSummary(run_id=run_id)
        paused_apps: List[Dict[str, Any]] = self.load_entries()

        discovery_namespaces_by_hub = self._get_discovery_namespaces_by_hub() if existing_run_id else {}
        applications_by_hub: List[Tuple[KubeClient, str, List[Dict[str, Any]]]] = []
        pause_blockers = []

        for client, hub_label, discovery in discoveries:
            if not discovery.has_applications_crd:
                logger.info(
                    "Argo CD Applications CRD not found on %s; skipping Argo CD pause",
                    hub_label,
                )
                continue

            trusted_namespaces = argocd_lib.trusted_application_namespaces(discovery_namespaces_by_hub.get(hub_label))
            apps = argocd_lib.list_argocd_applications(client, namespaces=trusted_namespaces)
            if trusted_namespaces is None:
                discovery_namespaces_by_hub[hub_label] = argocd_lib.application_namespaces_from_discovery(apps)
            applications_by_hub.append((client, hub_label, apps))
            blockers = argocd_lib.find_argocd_pause_blockers(apps)
            for blocker in blockers:
                logger.error("Argo CD pause blocked on %s: %s", hub_label, blocker.message)
            pause_blockers.extend(blockers)

        self._persist_discovery_namespaces_by_hub(discovery_namespaces_by_hub)

        if pause_blockers:
            summary.blocked = len(pause_blockers)
            return summary

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
                        summary.recovered += 1
                        logger.info(
                            "  Recovered Argo CD pause state for %s/%s on %s",
                            namespace,
                            name,
                            hub_label,
                        )
                        continue
                    # Clobber guard: already paused and recorded
                    if self._is_pause_applied(existing_entry) and not has_automated:
                        summary.already_paused += 1
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
                    summary.newly_paused += 1
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
                    summary.failed += 1
                else:
                    self._remove_pause_entry(paused_apps, hub_label, namespace, name)
                    self._persist_paused_apps(paused_apps)
                    logger.debug("  Skip %s/%s (no auto-sync)", result.namespace, result.name)

        return summary

    def _handle_no_applications_crd(self) -> PauseSummary:
        """ADR-0001: preserve a non-empty register when the CRD is not visible; clear an empty one."""
        entries = self.load_entries()
        applied = [entry for entry in entries if self._is_pause_applied(entry)]
        run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
        if applied:
            logger.warning(
                "Argo CD Applications CRD not visible on any hub but %d app(s) recorded paused; "
                "keeping pause register (see ADR-0001). Resume with --argocd-resume-only, or "
                "clear the state file manually if Argo CD was permanently removed.",
                len(applied),
            )
            return PauseSummary(applications_crd_visible=False, run_id=run_id)
        logger.info("Argo CD Applications CRD not found on any hub; skipping Argo CD pause")
        if not self.dry_run:
            clear_argocd_pause_state(self.state)
            run_id = None
        return PauseSummary(applications_crd_visible=False, run_id=run_id)
