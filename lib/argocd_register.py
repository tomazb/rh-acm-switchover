"""
Argo CD pause coordination for ACM switchover.

Centralizes the shared logic for pausing ArgoCD auto-sync across one or more
hubs. Used by both PrimaryPreparation (full switchover) and restore-only mode.
"""

import copy
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

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


@dataclass
class RegisterStatus:
    """Snapshot of the pause register.

    ``confirmed_paused_count`` counts entries whose pause is confirmed applied;
    ``entry_count`` counts every entry resume() would attempt, including
    provisional ones written before an unverified patch.
    """

    confirmed_paused_count: int
    run_id: Optional[str]
    entry_count: int


@dataclass
class PauseSummary:
    """Aggregated result of one pause_hubs() run.

    Every counter describes work this run did (or, in dry-run, would do); none
    of them report pre-existing register contents. ``blocked`` and ``failed``
    are deliberately distinct: a blocker means the tool refused to pause (an
    ApplicationSet owns the Application), a failure means the patch itself
    did not succeed. ``dry_run`` tells callers which mode produced these
    counters, so they need not consult their own flag to phrase the outcome.

    Per counter:

    - ``newly_paused``: auto-sync was patched off this run (in dry-run, would
      have been).
    - ``already_paused``: a confirmed register entry whose Application already
      had auto-sync off — nothing to do, the clobber guard skipped it.
    - ``recovered``: the register claimed an unconfirmed pause and the live
      pause marker proved it had landed, so the entry was confirmed.
    - ``failed``: a pause patch was attempted and did not succeed.
    - ``blocked``: the tool refused to pause because an ApplicationSet owns
      the Application.
    """

    newly_paused: int = 0
    already_paused: int = 0
    recovered: int = 0
    failed: int = 0
    blocked: int = 0
    applications_crd_visible: bool = True
    run_id: Optional[str] = None
    dry_run: bool = False


class ArgocdPauseRegister:
    """The Argo CD pause register: pause, resume, and status across hubs.

    Invariant (ADR-0001): register entries are *unresolved resume
    obligations* — Applications this tool may have paused and has not yet
    confirmed resumed. An entry is confirmed (``pause_applied=True``),
    provisional (``pause_applied=False``, written before the patch), or
    unknown (``pause_state="unknown"``, ambiguous patch error). The latter
    two still mean the pause may have landed, so they are never discarded
    as "not really paused"; an entry leaves only when resume is proven
    complete. Dry-run records nothing. Handles detection, listing,
    filtering, entry recovery, pause execution, and state persistence.
    Callers are responsible for error-style adaptation (raising
    SwitchoverError vs returning bool).
    """

    def __init__(self, state: StateManager, *, dry_run: bool = False):
        self.state = state
        self.dry_run = dry_run

    @classmethod
    def _sanitize_entries(cls, raw: Any) -> List[Dict[str, Any]]:
        """Register entries from a raw state value (deep copy); non-dict and legacy dry-run entries dropped."""
        if not isinstance(raw, list):
            return []
        return [copy.deepcopy(entry) for entry in raw if isinstance(entry, dict) and not entry.get("dry_run")]

    def _load_entries(self) -> List[Dict[str, Any]]:
        """Current register entries (deep copy).

        Non-dict values are dropped, as are entries carrying a truthy legacy
        ``dry_run`` flag: dry-run predates ADR-0001 and never recorded real
        cluster mutations, so such entries are not resumable. Because they
        never survive this filter, an entry without ``pause_applied`` is
        always a legacy real pause and counts as applied.
        """
        return self._sanitize_entries(self.state.get_config(STATE_KEY_ARGOCD_PAUSED_APPS))

    def paused_hub_roles(self) -> Set[str]:
        """Hub roles referenced by the register — the hubs resume() would touch.

        Deliberately drawn from *all* entries, not only confirmed-applied ones:
        it mirrors what resume() iterates, and over-approximating the set of
        hubs whose identity must be validated is the safe direction.
        """
        return {
            entry.get(STATE_KEY_ARGOCD_PAUSED_APP_HUB)
            for entry in self._load_entries()
            if entry.get(STATE_KEY_ARGOCD_PAUSED_APP_HUB)
        }

    @classmethod
    def _applied_entries(cls, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Entries whose pause is confirmed applied on the cluster."""
        return [entry for entry in entries if cls._is_pause_applied(entry)]

    @classmethod
    def _build_status(cls, entries: List[Dict[str, Any]], run_id: Optional[str]) -> RegisterStatus:
        """Assemble a RegisterStatus from already-sanitized entries.

        Single assembly point so every reader of the register counts the same
        things and a new RegisterStatus field is added in one place.
        """
        return RegisterStatus(
            confirmed_paused_count=len(cls._applied_entries(entries)),
            run_id=run_id,
            entry_count=len(entries),
        )

    @classmethod
    def status_from_state_config(cls, state_config: Mapping[str, Any]) -> RegisterStatus:
        """Register snapshot read from a persisted state-file ``config`` mapping.

        For callers that hold raw state rather than a StateManager (report
        artifacts). Keeps the register the only reader of its own state keys
        and its own entry filtering rules.

        Takes the ``config`` sub-mapping, not the whole state document -- the
        parameter is named for what it consumes because passing the outer
        document would silently yield an empty register rather than an error.
        """
        return cls._build_status(
            cls._sanitize_entries(state_config.get(STATE_KEY_ARGOCD_PAUSED_APPS)),
            state_config.get(STATE_KEY_ARGOCD_RUN_ID),
        )

    def status(self) -> RegisterStatus:
        """Snapshot of the register: entry counts and run id."""
        return self._build_status(self._load_entries(), self.state.get_config(STATE_KEY_ARGOCD_RUN_ID))

    @staticmethod
    def _pause_entry_matches(entry: Dict[str, Any], hub: str, namespace: str, name: str) -> bool:
        """Return True when an Argo CD pause-state entry matches one Application."""
        return (
            entry.get(STATE_KEY_ARGOCD_PAUSED_APP_HUB) == hub
            and entry.get("namespace") == namespace
            and entry.get("name") == name
        )

    @staticmethod
    def _is_pause_applied(entry: Dict[str, Any]) -> bool:
        """Treat a missing pause_applied flag as legacy-applied (see _load_entries)."""
        return entry.get("pause_applied", True)

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

    def _clear(self) -> None:
        """Clear persisted Argo CD pause and discovery namespace state.

        No-op in dry-run: the guard lives here so no call site can forget it.
        """
        if self.dry_run:
            return
        self.state.set_config(STATE_KEY_ARGOCD_PAUSED_APPS, [])
        self.state.set_config(STATE_KEY_ARGOCD_RUN_ID, None)
        self.state.set_config(STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES, {})

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

        self._record_pause_state(entry, original_sync_policy, applied=pause_applied)
        return entry

    def _remove_pause_entry(
        self,
        paused_apps: List[Dict[str, Any]],
        hub: str,
        namespace: str,
        name: str,
    ) -> None:
        paused_apps[:] = [entry for entry in paused_apps if not self._pause_entry_matches(entry, hub, namespace, name)]

    def _forget(self, paused_apps: List[Dict[str, Any]], hub: str, namespace: str, name: str) -> None:
        """Drop one Application from the register and persist the result."""
        self._remove_pause_entry(paused_apps, hub, namespace, name)
        self._persist_paused_apps(paused_apps)

    @staticmethod
    def _record_pause_state(
        entry: Dict[str, Any],
        original_sync_policy: Optional[Dict[str, Any]] = None,
        *,
        applied: bool,
    ) -> None:
        """Record a known pause outcome: set the applied flag, clear provisional markers.

        Writes the provisional (``applied=False``) state as well — the outcome
        is known either way, only the pause itself may not have landed.

        ``original_sync_policy=None`` leaves any recorded policy untouched (the
        recovery path already has it). Note ``_mark_unknown`` gives the same
        parameter the opposite meaning: there ``None`` is stored.
        """
        if original_sync_policy is not None:
            entry["original_sync_policy"] = original_sync_policy
        entry["pause_applied"] = applied
        entry.pop("pause_state", None)
        entry.pop("pause_run_id", None)
        entry.pop("dry_run", None)

    @staticmethod
    def _mark_unknown(entry: Dict[str, Any], original_sync_policy: Optional[Dict[str, Any]], run_id: str) -> None:
        """Record that the patch outcome is unknown so a retry can verify it against the marker."""
        entry["original_sync_policy"] = original_sync_policy
        entry["pause_applied"] = False
        entry["pause_state"] = "unknown"
        entry["pause_run_id"] = run_id

    def resume(
        self,
        primary: Optional[KubeClient],
        secondary: Optional[KubeClient],
    ) -> argocd_lib.ResumeSummary:
        """
        Resume auto-sync for every registered Application.

        ADR-0001 invariant: restored and already-resumed entries are removed
        from the register immediately (persisted per entry); failures stay for
        retry.  When the register empties, all Argo CD pause state is cleared.
        """
        entries = self._load_entries()
        run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
        summary = argocd_lib.ResumeSummary(dry_run=self.dry_run)
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
                self._forget(entries, hub, ns, name)
                logger.info("  Resumed %s/%s on %s", ns, name, hub)
            elif argocd_lib.is_resume_noop(result):
                # A missing marker alone does not prove the Application was resumed.
                # Only an observed enabled auto-sync discharges the obligation;
                # autosync_enabled=None (unobserved) keeps the entry (ADR-0001).
                if result.autosync_enabled:
                    summary.already_resumed += 1
                    self._forget(entries, hub, ns, name)
                    logger.info("  Already resumed %s/%s on %s", ns, name, hub)
                else:
                    summary.failed += 1
                    logger.warning(
                        "  %s/%s on %s: pause marker is gone but auto-sync is still disabled — the "
                        "Application is still paused. Keeping the register entry; restore its sync "
                        "policy manually or re-run --argocd-resume-only once the marker is understood.",
                        ns,
                        name,
                        hub,
                    )
            else:
                summary.failed += 1
                logger.warning("  Failed %s/%s: %s", ns, name, result.skip_reason or "not restored")

        summary.remaining_in_register = len(entries)
        if not self.dry_run and not entries:
            self._clear()
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
        discoveries = self._discover(hubs)
        if not any(discovery.has_applications_crd for _, _, discovery in discoveries):
            return self._handle_no_applications_crd()

        existing_run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
        run_id = argocd_lib.run_id_or_new(existing_run_id)
        if not self.dry_run:
            self.state.set_config(STATE_KEY_ARGOCD_RUN_ID, run_id)
        summary = PauseSummary(run_id=run_id, dry_run=self.dry_run)
        paused_apps: List[Dict[str, Any]] = self._load_entries()

        applications_by_hub, pause_blockers = self._collect_applications(
            discoveries,
            reuse_recorded_namespaces=bool(existing_run_id),
        )
        if pause_blockers:
            summary.blocked = len(pause_blockers)
            return summary

        for client, hub_label, apps in applications_by_hub:
            for impact in argocd_lib.find_acm_touching_apps(apps):
                self._pause_application(client, hub_label, impact, paused_apps, run_id, summary)

        return summary

    @staticmethod
    def _discover(
        hubs: List[Tuple[KubeClient, str]],
    ) -> List[Tuple[KubeClient, str, "argocd_lib.ArgocdDiscoveryResult"]]:
        """Detect the Argo CD installation on every hub, preserving hub order."""
        return [(client, hub_label, argocd_lib.detect_argocd_installation(client)) for client, hub_label in hubs]

    def _collect_applications(
        self,
        discoveries: List[Tuple[KubeClient, str, "argocd_lib.ArgocdDiscoveryResult"]],
        *,
        reuse_recorded_namespaces: bool,
    ) -> Tuple[List[Tuple[KubeClient, str, List[Dict[str, Any]]]], List[Any]]:
        """List Applications per hub and collect every pause blocker found.

        Records the discovered Application namespaces on the first pass so a
        retry within the same run can list a trusted, scoped namespace set.
        """
        discovery_namespaces_by_hub = self._get_discovery_namespaces_by_hub() if reuse_recorded_namespaces else {}
        applications_by_hub: List[Tuple[KubeClient, str, List[Dict[str, Any]]]] = []
        pause_blockers: List[Any] = []

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
        return applications_by_hub, pause_blockers

    def _pause_application(
        self,
        client: KubeClient,
        hub_label: str,
        impact: "argocd_lib.AppImpact",
        paused_apps: List[Dict[str, Any]],
        run_id: str,
        summary: PauseSummary,
    ) -> None:
        """Pause one Application, reconciling whatever the register already says about it."""
        meta = impact.app.get("metadata", {}) or {}
        namespace = meta.get("namespace", "")
        name = meta.get("name", "")
        sync_policy = dict((impact.app.get("spec", {}) or {}).get("syncPolicy") or {})
        has_automated = argocd_lib.is_autosync_enabled(impact.app)
        existing_entry = self._find_pause_entry(paused_apps, hub_label, namespace, name)

        if existing_entry is not None and self._reconcile_recorded_entry(
            existing_entry,
            impact,
            paused_apps,
            hub_label,
            namespace,
            name,
            run_id,
            has_automated=has_automated,
            summary=summary,
        ):
            return

        if not has_automated:
            logger.debug("  Skip %s/%s (no auto-sync)", namespace, name)
            return

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
        self._apply_pause_result(entry, result, paused_apps, hub_label, namespace, name, run_id, summary=summary)

    def _reconcile_recorded_entry(
        self,
        existing_entry: Dict[str, Any],
        impact: "argocd_lib.AppImpact",
        paused_apps: List[Dict[str, Any]],
        hub_label: str,
        namespace: str,
        name: str,
        run_id: str,
        *,
        has_automated: bool,
        summary: PauseSummary,
    ) -> bool:
        """Handle an Application the register already knows about.

        Returns True when the entry was fully handled (recovered, dropped as
        unconfirmed, or skipped by the clobber guard) and no pause is needed.
        """
        if not self._is_pause_applied(existing_entry) and not self.dry_run and not has_automated:
            expected_run_id = existing_entry.get("pause_run_id") or run_id
            if not self._pause_marker_matches(impact.app, expected_run_id):
                self._forget(paused_apps, hub_label, namespace, name)
                logger.debug(
                    "  Removed unconfirmed Argo CD pause state for %s/%s on %s (marker missing)",
                    namespace,
                    name,
                    hub_label,
                )
                return True
            self._record_pause_state(existing_entry, applied=True)
            self._persist_paused_apps(paused_apps)
            summary.recovered += 1
            logger.info(
                "  Recovered Argo CD pause state for %s/%s on %s",
                namespace,
                name,
                hub_label,
            )
            return True

        # Clobber guard: already paused and recorded
        if self._is_pause_applied(existing_entry) and not has_automated:
            summary.already_paused += 1
            logger.debug(
                "  Skip %s/%s (already paused and recorded)",
                namespace,
                name,
            )
            return True

        return False

    def _apply_pause_result(
        self,
        entry: Dict[str, Any],
        result: "argocd_lib.PauseResult",
        paused_apps: List[Dict[str, Any]],
        hub_label: str,
        namespace: str,
        name: str,
        run_id: str,
        *,
        summary: PauseSummary,
    ) -> None:
        """Record one pause attempt: patched, failed with a known patch state, or skipped."""
        if result.patched:
            summary.newly_paused += 1
            self._record_pause_state(entry, result.original_sync_policy, applied=not self.dry_run)
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
                self._record_pause_state(entry, result.original_sync_policy, applied=True)
                self._persist_paused_apps(paused_apps)
            elif result.patch_applied is False:
                self._forget(paused_apps, hub_label, namespace, name)
            else:
                self._mark_unknown(entry, result.original_sync_policy, run_id)
                self._persist_paused_apps(paused_apps)
            summary.failed += 1
        else:
            self._forget(paused_apps, hub_label, namespace, name)
            logger.debug("  Skip %s/%s (no auto-sync)", result.namespace, result.name)

    def _handle_no_applications_crd(self) -> PauseSummary:
        """Preserve any non-empty register when the CRD is not visible; clear only a truly empty one.

        Entries are unresolved resume obligations (ADR-0001). A provisional or
        unknown entry means the pause may have landed, so discarding it destroys
        the only record needed to put the Application back -- the gate is on
        every sanitized entry, not just the confirmed-applied ones.
        """
        entries = self._load_entries()
        run_id = self.state.get_config(STATE_KEY_ARGOCD_RUN_ID)
        if entries:
            confirmed = len(self._applied_entries(entries))
            logger.warning(
                "Argo CD Applications CRD not visible on any hub but %d unresolved app(s) in the "
                "register (%d confirmed paused); keeping pause register (see ADR-0001). Resume "
                "with --argocd-resume-only, or clear the state file manually if Argo CD was "
                "permanently removed.",
                len(entries),
                confirmed,
            )
            return PauseSummary(applications_crd_visible=False, run_id=run_id, dry_run=self.dry_run)
        logger.info("Argo CD Applications CRD not found on any hub; skipping Argo CD pause")
        self._clear()
        # This run paused nothing and cleared the register, so it produced no run id.
        # Reporting the stale persisted one would make dry-run announce a pause that
        # never happened (the summary is the sole reporter -- see ADR-0001).
        return PauseSummary(applications_crd_visible=False, run_id=None, dry_run=self.dry_run)
