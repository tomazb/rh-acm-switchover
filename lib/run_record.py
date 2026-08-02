"""The switchover run record: named, typed cross-phase operations.

Each operation documents its writer, reader, and ordering contract. The
key literals below are an implementation detail of this module: no other
production code may read or write them (guardrail:
tests/test_run_record_guardrails.py). Durability, locking, and atomic
writes belong to StateManager; this facade owns only the vocabulary.

On-disk schema is unchanged: every operation reads and writes the exact
config keys the tool has always used, so existing state files remain
resumable and show_state renders historical files identically.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from lib.constants import (
    EXPECTED_MANAGED_CLUSTER_COUNT_KEY,
    EXPECTED_MANAGED_CLUSTER_NAMES_KEY,
    MANAGED_CLUSTER_EXPECTATION_KEY,
)

_KEY_PRIMARY_VERSION = "primary_version"
_KEY_PRIMARY_OBS_DETECTED = "primary_observability_detected"
_KEY_PRIMARY_HAS_OBS = "primary_has_observability"
_KEY_SECONDARY_VERSION = "secondary_version"
_KEY_SECONDARY_OBS_DETECTED = "secondary_observability_detected"
_KEY_SECONDARY_HAS_OBS = "secondary_has_observability"
_KEY_HAS_OBS = "has_observability"
_KEY_PREFLIGHT_RESULTS = "preflight_results"
_KEY_PREFLIGHT_SUMMARY = "preflight_summary"
_KEY_AUTO_IMPORT_SET = "auto_import_strategy_set"
_KEY_SAVED_BACKUP_SCHEDULE = "saved_backup_schedule"
_KEY_BACKUP_WATCH_STARTED_AT = "backup_schedule_enabled_at"
_KEY_NEW_BACKUP_DETECTED = "new_backup_detected"
_KEY_NEW_BACKUP_NAME = "post_switchover_backup_name"
_KEY_ARCHIVED_RESTORES = "archived_restores"


@dataclass(frozen=True)
class HubFacts:
    """Versions and observability posture discovered by preflight.

    Written once by the CLI preflight phase; read by primary_prep,
    activation, post_activation, finalization, and the report writers.
    """

    primary_version: str = "unknown"
    primary_observability_detected: bool = False
    primary_has_observability: bool = False
    secondary_version: str = "unknown"
    secondary_observability_detected: bool = False
    secondary_has_observability: bool = False
    has_observability: bool = False


@dataclass(frozen=True)
class ManagedClusterExpectation:
    """Expected managed clusters, set by preflight, enforced post-activation."""

    names: tuple = field(default_factory=tuple)
    count: int = 0
    mode: Optional[str] = None


class RunRecord:
    """Named operations over the cross-phase facts of one switchover run."""

    def __init__(self, state) -> None:
        self._state = state

    # -- internal accessors (single indirection point for Task 10 rename) --

    def _set(self, key: str, value: Any) -> None:
        self._state.set_config(key, value)

    def _get(self, key: str, default: Any = None) -> Any:
        return self._state.get_config(key, default)

    # -- hub facts: written by CLI preflight, read by every later phase --

    def record_hub_facts(self, facts: HubFacts) -> None:
        """Persist preflight-discovered hub facts. Write before any phase runs."""
        for key, value in (
            (_KEY_PRIMARY_VERSION, facts.primary_version),
            (_KEY_PRIMARY_OBS_DETECTED, facts.primary_observability_detected),
            (_KEY_PRIMARY_HAS_OBS, facts.primary_has_observability),
            (_KEY_SECONDARY_VERSION, facts.secondary_version),
            (_KEY_SECONDARY_OBS_DETECTED, facts.secondary_observability_detected),
            (_KEY_SECONDARY_HAS_OBS, facts.secondary_has_observability),
            (_KEY_HAS_OBS, facts.has_observability),
        ):
            self._set(key, value)

    def hub_facts(self) -> HubFacts:
        """Never-recorded reads return HubFacts() defaults ("unknown"/False)."""
        defaults = HubFacts()
        return HubFacts(
            primary_version=str(self._get(_KEY_PRIMARY_VERSION, defaults.primary_version)),
            primary_observability_detected=bool(self._get(_KEY_PRIMARY_OBS_DETECTED, False)),
            primary_has_observability=bool(self._get(_KEY_PRIMARY_HAS_OBS, False)),
            secondary_version=str(self._get(_KEY_SECONDARY_VERSION, defaults.secondary_version)),
            secondary_observability_detected=bool(self._get(_KEY_SECONDARY_OBS_DETECTED, False)),
            secondary_has_observability=bool(self._get(_KEY_SECONDARY_HAS_OBS, False)),
            has_observability=bool(self._get(_KEY_HAS_OBS, False)),
        )

    # -- managed-cluster expectation: preflight -> post-activation checks --

    def record_managed_cluster_expectation(self, names, count: int, mode: str) -> None:
        """Persist the expectation preflight derived. Written by the CLI
        preflight block; read by _resolve_managed_cluster_expectation before
        activation and post-activation."""
        self._set(EXPECTED_MANAGED_CLUSTER_NAMES_KEY, list(names))
        self._set(EXPECTED_MANAGED_CLUSTER_COUNT_KEY, int(count))
        self._set(MANAGED_CLUSTER_EXPECTATION_KEY, mode)

    def managed_cluster_expectation(self) -> ManagedClusterExpectation:
        names = tuple(self._get(EXPECTED_MANAGED_CLUSTER_NAMES_KEY, []) or [])
        count = int(self._get(EXPECTED_MANAGED_CLUSTER_COUNT_KEY, len(names)) or 0)
        return ManagedClusterExpectation(
            names=names,
            count=count,
            mode=self._get(MANAGED_CLUSTER_EXPECTATION_KEY, None),
        )

    # -- preflight results: CLI -> report writers --

    def record_preflight_results(self, results, passed: bool, critical_failures: int) -> None:
        """Persist preflight results and their summary for report artifacts."""
        results = list(results)
        self._set(_KEY_PREFLIGHT_RESULTS, results)
        self._set(
            _KEY_PREFLIGHT_SUMMARY,
            {"passed": passed, "critical_failures": critical_failures, "total": len(results)},
        )
