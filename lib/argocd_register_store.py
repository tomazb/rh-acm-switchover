"""Durable state for the Argo CD pause register.

The codec and store behind :class:`~lib.argocd_register.ArgocdPauseRegister`:
the on-disk entry schema, legacy sanitization, load/persist, entry
find/upsert/remove/mark helpers, the status snapshot, and the pause-state
reset.

Deliberately free of cluster concerns -- it imports ``StateManager`` and
``lib.constants`` only, never ``lib.argocd`` or ``lib.kube_client``. Readers
that hold state but no cluster connection (report artifacts) can therefore
read the register without dragging in the Kubernetes SDK.
"""

import copy
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Set

from lib.constants import (
    STATE_KEY_ARGOCD_DISCOVERY_NAMESPACES,
    STATE_KEY_ARGOCD_PAUSED_APP_HUB,
    STATE_KEY_ARGOCD_PAUSED_APPS,
    STATE_KEY_ARGOCD_RUN_ID,
)
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
    # Entries present in persisted state but dropped as unresumable (legacy
    # dry-run records, malformed values). Distinguishes "the register was
    # emptied by a successful resume" from "it held only records we cannot act
    # on" -- the two look identical through entry_count alone.
    discarded_entry_count: int = 0


class PauseRegisterStore:
    """Persistence for the pause register's entries and run metadata.

    Owns every read and write of the Argo CD pause state keys, so the entry
    schema and its filtering rules have exactly one implementation. The
    dry-run guard lives on the write methods here rather than at their call
    sites, so no caller can forget it (ADR-0001: dry-run records nothing).
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
    def _build_status(
        cls,
        entries: List[Dict[str, Any]],
        run_id: Optional[str],
        raw: Any = None,
    ) -> RegisterStatus:
        """Assemble a RegisterStatus from already-sanitized entries.

        Single assembly point so every reader of the register counts the same
        things and a new RegisterStatus field is added in one place. ``raw`` is
        the persisted value the entries came from, used only to report how many
        records sanitization dropped.
        """
        # A non-list, non-null persisted value is malformed durable state, not an
        # empty register. Counting it as discarded keeps resume-only in its error
        # path instead of treating the run id as leftover metadata and clearing
        # the very evidence needed to recover.
        if isinstance(raw, list):
            raw_count = len(raw)
        elif raw is None:
            raw_count = 0
        else:
            raw_count = len(entries) + 1
        return RegisterStatus(
            confirmed_paused_count=len(cls._applied_entries(entries)),
            run_id=run_id,
            entry_count=len(entries),
            discarded_entry_count=max(0, raw_count - len(entries)),
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
        raw = state_config.get(STATE_KEY_ARGOCD_PAUSED_APPS)
        return cls._build_status(
            cls._sanitize_entries(raw),
            state_config.get(STATE_KEY_ARGOCD_RUN_ID),
            raw,
        )

    def status(self) -> RegisterStatus:
        """Snapshot of the register: entry counts and run id."""
        raw = self.state.get_config(STATE_KEY_ARGOCD_PAUSED_APPS)
        return self._build_status(
            self._sanitize_entries(raw),
            self.state.get_config(STATE_KEY_ARGOCD_RUN_ID),
            raw,
        )

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

    def finish_cleanup(self) -> None:
        """Discard leftover run metadata for a register with no obligations left.

        resume() empties the entry list and clears the run id as two writes. A
        crash between them leaves a run id with an empty register -- no
        outstanding obligations, but metadata that makes the state look
        resumable. Completing the cleanup is always safe here because there is
        nothing left to resume; callers must check ``entry_count`` first.
        """
        self._clear()

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
