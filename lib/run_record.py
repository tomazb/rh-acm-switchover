"""The switchover run record: named, typed cross-phase operations.

Each operation documents its writer, reader, and ordering contract. The
key literals below are an implementation detail of this module: no other
production code may read or write them (guardrail:
tests/test_run_record_guardrails.py). Durability,
locking, and atomic writes belong to StateManager; this facade owns only
the vocabulary.

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
    PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME,
    RESUME_START_PHASE_KEY,
    STATE_KEY_RESUME_SUMMARY,
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

_UNSET = object()


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


@dataclass(frozen=True)
class StepRecord:
    """One completed step as recorded by StateManager.mark_step_completed."""

    name: str
    phase: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass(frozen=True)
class ErrorRecord:
    """One recorded run error as written by StateManager.add_error."""

    error: str
    phase: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass(frozen=True)
class RunSummary:
    """Typed view of a run's lifecycle for report writers and show_state.

    Built from a live StateManager (RunRecord.summary()) or from a state
    snapshot read off disk (RunSummary.from_snapshot()); the two paths are
    equivalent for the same underlying state. from_snapshot never raises on
    malformed shapes — unknown or wrong-typed fields degrade to defaults,
    matching the historical tolerance of the report readers.
    """

    current_phase: Optional[str] = None
    completed_steps: tuple = field(default_factory=tuple)
    errors: tuple = field(default_factory=tuple)
    preflight_results: tuple = field(default_factory=tuple)

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "RunSummary":
        if not isinstance(snapshot, dict):
            return cls()

        steps = []
        raw_steps = snapshot.get("completed_steps", [])
        if isinstance(raw_steps, list):
            for step in raw_steps:
                if not isinstance(step, dict):
                    continue
                name = step.get("name", "")
                phase = step.get("phase")
                timestamp = step.get("timestamp")
                steps.append(
                    StepRecord(
                        name=name if isinstance(name, str) else "",
                        phase=phase if isinstance(phase, str) else None,
                        timestamp=timestamp if isinstance(timestamp, str) else None,
                    )
                )

        errors = []
        raw_errors = snapshot.get("errors", [])
        if isinstance(raw_errors, list):
            for err in raw_errors:
                if not isinstance(err, dict):
                    continue
                message = err.get("error", "")
                phase = err.get("phase")
                timestamp = err.get("timestamp")
                errors.append(
                    ErrorRecord(
                        error=message if isinstance(message, str) else "",
                        phase=phase if isinstance(phase, str) else None,
                        timestamp=timestamp if isinstance(timestamp, str) else None,
                    )
                )

        config = snapshot.get("config", {})
        if not isinstance(config, dict):
            config = {}
        raw_results = config.get(_KEY_PREFLIGHT_RESULTS) or []
        results = tuple(r for r in raw_results if isinstance(r, dict)) if isinstance(raw_results, list) else ()

        current_phase = snapshot.get("current_phase")
        return cls(
            current_phase=current_phase if isinstance(current_phase, str) else None,
            completed_steps=tuple(steps),
            errors=tuple(errors),
            preflight_results=results,
        )


class RunRecord:
    """Named operations over the cross-phase facts of one switchover run."""

    def __init__(self, state) -> None:
        self._state = state

    # -- internal accessors (indirection carries the _UNSET forwarding in _get) --

    def _set(self, key: str, value: Any) -> None:
        self._state._set_config(key, value)

    def _get(self, key: str, default: Any = _UNSET) -> Any:
        # Forward the default only when the caller supplied one, so reads keep
        # the exact single-argument _get_config call shape they had before the
        # facade existed (StateManager already defaults to None).
        if default is _UNSET:
            return self._state._get_config(key)
        return self._state._get_config(key, default)

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

    # -- auto-import override: activation writes, finalization discharges --

    def record_auto_import_override(self) -> None:
        """Activation set autoImportStrategy=ImportAndSync; finalization owes
        a reset. Written by SecondaryActivation._maybe_set_auto_import_strategy."""
        self._set(_KEY_AUTO_IMPORT_SET, True)

    def clear_auto_import_override(self) -> None:
        """Finalization proved the reset is complete (or the ConfigMap is gone)."""
        self._set(_KEY_AUTO_IMPORT_SET, False)

    def auto_import_override_pending(self) -> bool:
        """False means no reset obligation — never recorded, or already cleared."""
        return bool(self._get(_KEY_AUTO_IMPORT_SET, False))

    # -- saved backup schedule: primary_prep writes, backup_schedule restores --

    def record_saved_backup_schedule(self, schedule: dict) -> None:
        """Persist the paused BackupSchedule so the new hub can recreate it.
        Written by primary_prep before pausing; read by BackupScheduleManager."""
        self._set(_KEY_SAVED_BACKUP_SCHEDULE, schedule)

    def saved_backup_schedule(self) -> Optional[dict]:
        """None means primary_prep never saved one (nothing to restore)."""
        return self._get(_KEY_SAVED_BACKUP_SCHEDULE)

    # -- backup watch: finalization internal, crash-resume safe --

    def record_backup_watch_started(self, at_iso: str) -> None:
        """BackupSchedule enabled at `at_iso`; new-backup detection restarts.
        Ordering: written when finalization enables the schedule, before
        record_new_backup can fire for the new watch window."""
        self._set(_KEY_BACKUP_WATCH_STARTED_AT, at_iso)
        self._set(_KEY_NEW_BACKUP_DETECTED, False)

    def backup_watch_started_at(self) -> Optional[str]:
        return self._get(_KEY_BACKUP_WATCH_STARTED_AT, None)

    def record_new_backup(self, name: str) -> None:
        """A post-switchover ACM backup was observed; resume reuses it."""
        self._set(_KEY_NEW_BACKUP_DETECTED, True)
        self._set(_KEY_NEW_BACKUP_NAME, name)

    def new_backup(self) -> Optional[str]:
        """Last recorded post-switchover backup name; None if never detected."""
        return self._get(_KEY_NEW_BACKUP_NAME, None)

    # -- archived restores: finalization -> audit/report --

    def record_archived_restores(self, restores: list) -> None:
        """Audit trail of restore resources deleted before enabling backups."""
        self._set(_KEY_ARCHIVED_RESTORES, restores)

    # -- pre-activation velero restore: activation internal --

    def record_pre_activation_velero_restore(self, name: Optional[str]) -> None:
        """Velero restore name seen before the activation patch; None clears
        the new-restore-signal requirement."""
        self._set(PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME, name)

    def pre_activation_velero_restore(self) -> Optional[str]:
        return self._get(PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME, None)

    # -- resume summary: workflow -> test-release tooling / collection --

    def record_resume_start_phase(self, phase_name: str) -> None:
        """A resumed run starts at `phase_name`. Written by workflow on resume;
        no production report reads it — the readers are the test-release
        tooling and the Ansible collection's checkpoint_phase. show_state
        surfaces it only via its generic config listing."""
        self._set(STATE_KEY_RESUME_SUMMARY, {RESUME_START_PHASE_KEY: phase_name})

    # -- lifecycle view: read side for report writers and show_state --

    def summary(self) -> RunSummary:
        """Typed lifecycle view of the bound state (live path)."""
        return RunSummary.from_snapshot(self._state.capture_state_snapshot())
