# Run Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execution model: **Opus 5**.

**Goal:** Replace the string-keyed `set_config`/`get_config` cross-phase interface with a `RunRecord` facade of named, typed operations, and converge the four raw-schema readers on a typed `RunSummary`.

**Architecture:** New `lib/run_record.py` owns the config-key vocabulary (composition over `StateManager`, which keeps locking/atomic-write/corruption handling). Consumers construct `RunRecord(state)` locally — the facade is stateless besides the reference, so instance identity does not matter. On-disk JSON is unchanged: every operation reads/writes the exact keys used today. At the end, `set_config`/`get_config` are renamed private and a guardrail test locks the seam.

**Tech Stack:** Python 3 stdlib (`dataclasses`), pytest. Formatting: `black --line-length 120` (repo has no black config file; 120 is mandatory). Lint: `flake8`.

**Spec:** `docs/superpowers/specs/2026-08-02-run-record-design.md`

## Global Constraints

- On-disk state schema unchanged — same key names, same value shapes. No migration logic.
- Pause-register keys (`argocd_paused_apps`, `argocd_run_id`, `argocd_discovery_namespaces`) stay behind `PauseRegisterStore`/`ArgocdPauseRegister`. Do not touch those modules except the final accessor rename.
- Key-name constants stay in `lib/constants.py` (parity tests may pin them); `lib/run_record.py` becomes their only production importer for the keys it owns.
- `black --line-length 120` on every touched file before each commit; `flake8` clean.
- Full test suite green at every commit: `python -m pytest tests/ -q`.
- No `Co-Authored-By` trailers in commits.
- Branch: continue on `feat/run-record-spec` (contains the spec), based on `origin/ansible`.

---

### Task 1: RunRecord facade — hub facts, managed-cluster expectation, preflight results

**Files:**
- Create: `lib/run_record.py`
- Create: `tests/test_run_record.py`

**Interfaces:**
- Consumes: `lib.utils.StateManager` (existing `set_config`/`get_config`, renamed `_set_config`/`_get_config` in Task 10 — use the public names until then via a single pair of internal helpers, see Step 3), key constants from `lib.constants`.
- Produces (later tasks rely on these exact names):
  - `HubFacts(primary_version, primary_observability_detected, primary_has_observability, secondary_version, secondary_observability_detected, secondary_has_observability, has_observability)` frozen dataclass, defaults `"unknown"`/`False`.
  - `ManagedClusterExpectation(names: tuple[str, ...], count: int, mode: Optional[str])` frozen dataclass, defaults `()`/`0`/`None`.
  - `RunRecord(state)` with `record_hub_facts(facts)`, `hub_facts()`, `record_managed_cluster_expectation(names, count, mode)`, `managed_cluster_expectation()`, `record_preflight_results(results, passed, critical_failures)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_record.py
"""Tests for the RunRecord facade — the named, typed cross-phase interface.

All tests go through the public interface only: no raw key literals, no
reaching into StateManager internals beyond constructing it.
"""

import pytest

from lib.run_record import HubFacts, ManagedClusterExpectation, RunRecord
from lib.utils import StateManager


@pytest.fixture
def state(tmp_path):
    return StateManager(str(tmp_path / "switchover-test.json"))


@pytest.fixture
def record(state):
    return RunRecord(state)


class TestHubFacts:
    def test_defaults_before_recording(self, record):
        facts = record.hub_facts()
        assert facts == HubFacts()
        assert facts.primary_version == "unknown"
        assert facts.has_observability is False

    def test_round_trip(self, record):
        written = HubFacts(
            primary_version="2.13.2",
            primary_observability_detected=True,
            primary_has_observability=True,
            secondary_version="2.14.0",
            secondary_observability_detected=False,
            secondary_has_observability=False,
            has_observability=True,
        )
        record.record_hub_facts(written)
        assert record.hub_facts() == written

    def test_survives_reload(self, state, record):
        record.record_hub_facts(HubFacts(secondary_version="2.14.0"))
        reloaded = RunRecord(StateManager(state.state_file))
        assert reloaded.hub_facts().secondary_version == "2.14.0"


class TestManagedClusterExpectation:
    def test_default_before_recording(self, record):
        assert record.managed_cluster_expectation() == ManagedClusterExpectation()

    def test_round_trip_normalizes_to_tuple(self, record):
        record.record_managed_cluster_expectation(
            names=["cluster-a", "cluster-b"], count=2, mode="derived_from_preflight"
        )
        exp = record.managed_cluster_expectation()
        assert exp.names == ("cluster-a", "cluster-b")
        assert exp.count == 2
        assert exp.mode == "derived_from_preflight"


class TestPreflightResults:
    def test_record_writes_results_and_summary(self, state, record):
        results = [{"check": "versions", "status": "pass", "message": "ok"}]
        record.record_preflight_results(results, passed=True, critical_failures=0)
        # Interface-only persistence: the raw file keeps today's key names.
        snapshot = state.capture_state_snapshot()
        assert snapshot["config"]["preflight_results"] == results
        assert snapshot["config"]["preflight_summary"] == {
            "passed": True,
            "critical_failures": 0,
            "total": 1,
        }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_record.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lib.run_record'`

- [ ] **Step 3: Write the implementation**

```python
# lib/run_record.py
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
```

(The `_KEY_AUTO_IMPORT_SET`…`_KEY_ARCHIVED_RESTORES` literals defined above
are consumed from Task 2 on; module-level constants do not trip flake8.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_record.py -v`
Expected: PASS (all)

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 lib/run_record.py tests/test_run_record.py
flake8 lib/run_record.py tests/test_run_record.py
git add lib/run_record.py tests/test_run_record.py
git commit -m "feat: add RunRecord facade with hub facts, expectation, preflight ops"
```

---

### Task 2: RunRecord — auto-import override, saved schedule, backup watch, archived restores, velero restore, resume phase

**Files:**
- Modify: `lib/run_record.py` (append methods to `RunRecord`)
- Modify: `tests/test_run_record.py` (append test classes)

**Interfaces:**
- Produces (exact names later tasks use):
  - `record_auto_import_override()`, `clear_auto_import_override()`, `auto_import_override_pending() -> bool`
  - `record_saved_backup_schedule(schedule: dict)`, `saved_backup_schedule() -> Optional[dict]`
  - `record_backup_watch_started(at_iso: str)`, `backup_watch_started_at() -> Optional[str]`
  - `record_new_backup(name: str)`, `new_backup() -> Optional[str]`
  - `record_archived_restores(restores: list)`
  - `record_pre_activation_velero_restore(name: Optional[str])`, `pre_activation_velero_restore() -> Optional[str]`
  - `record_resume_start_phase(phase_name: str)`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_run_record.py

class TestAutoImportOverride:
    def test_no_obligation_by_default(self, record):
        assert record.auto_import_override_pending() is False

    def test_record_then_clear(self, record):
        record.record_auto_import_override()
        assert record.auto_import_override_pending() is True
        record.clear_auto_import_override()
        assert record.auto_import_override_pending() is False


class TestSavedBackupSchedule:
    def test_none_by_default(self, record):
        assert record.saved_backup_schedule() is None

    def test_round_trip(self, record):
        bs = {"metadata": {"name": "schedule-acm"}, "spec": {"veleroSchedule": "0 */4 * * *"}}
        record.record_saved_backup_schedule(bs)
        assert record.saved_backup_schedule() == bs


class TestBackupWatch:
    def test_defaults(self, record):
        assert record.backup_watch_started_at() is None
        assert record.new_backup() is None

    def test_watch_start_resets_detection(self, record):
        record.record_new_backup("acm-backup-1")
        record.record_backup_watch_started("2026-08-02T18:00:00+00:00")
        assert record.backup_watch_started_at() == "2026-08-02T18:00:00+00:00"
        # A new watch window invalidates the previous detection flag but
        # keeps the last recorded name for the resume fast path.
        assert record.new_backup() == "acm-backup-1"

    def test_record_new_backup(self, state, record):
        record.record_new_backup("acm-backup-2")
        assert record.new_backup() == "acm-backup-2"
        snapshot = state.capture_state_snapshot()
        assert snapshot["config"]["new_backup_detected"] is True
        assert snapshot["config"]["post_switchover_backup_name"] == "acm-backup-2"


class TestArchivedRestores:
    def test_record(self, state, record):
        restores = [{"name": "restore-acm-passive-sync", "phase": "Finished"}]
        record.record_archived_restores(restores)
        assert state.capture_state_snapshot()["config"]["archived_restores"] == restores


class TestPreActivationVeleroRestore:
    def test_none_by_default(self, record):
        assert record.pre_activation_velero_restore() is None

    def test_round_trip_and_clear(self, record):
        record.record_pre_activation_velero_restore("velero-restore-1")
        assert record.pre_activation_velero_restore() == "velero-restore-1"
        record.record_pre_activation_velero_restore(None)
        assert record.pre_activation_velero_restore() is None


class TestResumeStartPhase:
    def test_record_writes_resume_summary_shape(self, state, record):
        record.record_resume_start_phase("activation")
        snapshot = state.capture_state_snapshot()
        assert snapshot["config"]["resume_summary"] == {"resume_start_phase": "activation"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_record.py -v`
Expected: new tests FAIL with `AttributeError: 'RunRecord' object has no attribute 'record_auto_import_override'` (etc.); Task 1 tests still PASS.

- [ ] **Step 3: Write the implementation**

First extend the `lib.constants` import block in `lib/run_record.py` with the
three keys this task consumes:

```python
from lib.constants import (
    EXPECTED_MANAGED_CLUSTER_COUNT_KEY,
    EXPECTED_MANAGED_CLUSTER_NAMES_KEY,
    MANAGED_CLUSTER_EXPECTATION_KEY,
    PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME,
    RESUME_START_PHASE_KEY,
    STATE_KEY_RESUME_SUMMARY,
)
```

```python
# append to class RunRecord in lib/run_record.py

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
        return self._get(_KEY_SAVED_BACKUP_SCHEDULE, None)

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

    # -- resume summary: workflow -> report/show_state --

    def record_resume_start_phase(self, phase_name: str) -> None:
        """A resumed run starts at `phase_name`; recorded for reports."""
        self._set(STATE_KEY_RESUME_SUMMARY, {RESUME_START_PHASE_KEY: phase_name})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_record.py -v`
Expected: PASS (all)

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 lib/run_record.py tests/test_run_record.py
flake8 lib/run_record.py tests/test_run_record.py
git add lib/run_record.py tests/test_run_record.py
git commit -m "feat: complete RunRecord operations for all nine cross-phase handoffs"
```

---

### Task 3: RunSummary — typed run-lifecycle view with tolerant snapshot parsing

**Files:**
- Modify: `lib/run_record.py`
- Modify: `tests/test_run_record.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (Tasks 7–8 rely on these exact names):
  - `StepRecord(name: str, phase: Optional[str], timestamp: Optional[str])` frozen dataclass
  - `ErrorRecord(error: str, phase: Optional[str], timestamp: Optional[str])` frozen dataclass
  - `RunSummary(current_phase: Optional[str], completed_steps: tuple, errors: tuple, preflight_results: tuple)` frozen dataclass with classmethod `from_snapshot(snapshot: Any) -> "RunSummary"`
  - `RunRecord.summary() -> RunSummary`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_run_record.py
from lib.run_record import ErrorRecord, RunSummary, StepRecord  # top of file with other imports


class TestRunSummary:
    def test_from_snapshot_happy_path(self):
        snapshot = {
            "current_phase": "finalization",
            "completed_steps": [
                {"name": "preflight_validation", "phase": "preflight", "timestamp": "t1"},
                {"name": "activate_managed_clusters", "phase": "activation", "timestamp": "t2"},
            ],
            "errors": [{"error": "boom", "phase": "activation", "timestamp": "t3"}],
            "config": {"preflight_results": [{"check": "versions", "status": "pass"}]},
        }
        summary = RunSummary.from_snapshot(snapshot)
        assert summary.current_phase == "finalization"
        assert summary.completed_steps == (
            StepRecord(name="preflight_validation", phase="preflight", timestamp="t1"),
            StepRecord(name="activate_managed_clusters", phase="activation", timestamp="t2"),
        )
        assert summary.errors == (ErrorRecord(error="boom", phase="activation", timestamp="t3"),)
        assert summary.preflight_results == ({"check": "versions", "status": "pass"},)

    @pytest.mark.parametrize(
        "snapshot",
        [
            None,
            "not a dict",
            {},
            {"completed_steps": "not a list", "errors": 7, "config": []},
            {"completed_steps": [None, "str", {"phase": 3}], "errors": [None, []]},
        ],
    )
    def test_from_snapshot_never_raises_on_malformed_input(self, snapshot):
        summary = RunSummary.from_snapshot(snapshot)
        assert isinstance(summary, RunSummary)
        for step in summary.completed_steps:
            assert isinstance(step, StepRecord)
        for err in summary.errors:
            assert isinstance(err, ErrorRecord)

    def test_live_summary_matches_snapshot_summary(self, state, record):
        state.mark_step_completed("preflight_validation")
        record.record_preflight_results([{"check": "versions", "status": "pass"}], passed=True, critical_failures=0)
        live = record.summary()
        offline = RunSummary.from_snapshot(state.capture_state_snapshot())
        assert live == offline
        assert live.completed_steps[0].name == "preflight_validation"
```

Note: check `StateManager.mark_step_completed`'s exact signature in
`lib/utils.py` before running — if it requires a phase argument, pass the
current phase; the assertion on `name` is what matters.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_record.py -v -k RunSummary`
Expected: FAIL with `ImportError: cannot import name 'RunSummary'`

- [ ] **Step 3: Write the implementation**

```python
# append to lib/run_record.py (dataclasses above class RunRecord)

@dataclass(frozen=True)
class StepRecord:
    name: str
    phase: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass(frozen=True)
class ErrorRecord:
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
```

```python
# append to class RunRecord

    def summary(self) -> RunSummary:
        """Typed lifecycle view of the bound state (live path)."""
        return RunSummary.from_snapshot(self._state.capture_state_snapshot())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_record.py -v`
Expected: PASS (all)

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 lib/run_record.py tests/test_run_record.py
flake8 lib/run_record.py tests/test_run_record.py
git add lib/run_record.py tests/test_run_record.py
git commit -m "feat: add RunSummary typed lifecycle view with tolerant snapshot parsing"
```

---

### Task 4: Migrate acm_switchover.py to RunRecord

**Files:**
- Modify: `acm_switchover.py:646-700` (preflight writes), `:795-796` (primary_prep reader), `:853` (post_activation reader), `:868-885` (`_resolve_managed_cluster_expectation`), `:901-903` (finalization reader)
- Test: existing `tests/test_main.py`, `tests/test_cli_auto_import.py`, `tests/test_state_handoff.py` (behaviour must not change; run them)

**Interfaces:**
- Consumes: `RunRecord`, `HubFacts`, `ManagedClusterExpectation` from Task 1.
- Produces: no new interfaces. Import line: `from lib.run_record import HubFacts, ManagedClusterExpectation, RunRecord`.

- [ ] **Step 1: Replace the preflight config writes**

Current code (`acm_switchover.py:646-700`, abbreviated to the state calls):

```python
    passed, config = validator.validate_all()
    preflight_results = list(validator.reporter.results)
    state.set_config("preflight_results", preflight_results)
    state.set_config(
        "preflight_summary",
        {
            "passed": passed,
            "critical_failures": len(validator.reporter.critical_failures()),
            "total": len(preflight_results),
        },
    )

    if not passed:
        return _fail_phase(state, "Pre-flight validation failed! Cannot proceed.", logger)

    if is_restore_only:
        state.set_config("primary_version", "unknown")
        state.set_config("primary_observability_detected", False)
        state.set_config("primary_has_observability", False)
        expected_managed_cluster_names: list[str] = []
    else:
        state.set_config("primary_version", config["primary_version"])
        state.set_config(
            "primary_observability_detected",
            config["primary_observability_detected"],
        )
        primary_obs_enabled = config["primary_observability_detected"] and not args.skip_observability_checks
        state.set_config("primary_has_observability", primary_obs_enabled)
        expected_managed_cluster_names = list(config.get("expected_managed_cluster_names", []))

    state.set_config("secondary_version", config["secondary_version"])
    state.set_config(
        "secondary_observability_detected",
        config["secondary_observability_detected"],
    )

    secondary_obs_enabled = config["secondary_observability_detected"] and not args.skip_observability_checks
    state.set_config("secondary_has_observability", secondary_obs_enabled)
    state.set_config(
        "has_observability",
        state.get_config("primary_has_observability", False) or secondary_obs_enabled,
    )
    expected_managed_cluster_count = len(expected_managed_cluster_names)
    state.set_config(EXPECTED_MANAGED_CLUSTER_NAMES_KEY, expected_managed_cluster_names)
    state.set_config(EXPECTED_MANAGED_CLUSTER_COUNT_KEY, expected_managed_cluster_count)
    if is_restore_only:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY
    elif getattr(args, "min_managed_clusters", None) is None:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT
    elif getattr(args, "min_managed_clusters", 0) == 0:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED
    else:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_MINIMUM
    state.set_config(MANAGED_CLUSTER_EXPECTATION_KEY, expectation_mode)
```

Replace with:

```python
    passed, config = validator.validate_all()
    run_record = RunRecord(state)
    run_record.record_preflight_results(
        validator.reporter.results,
        passed=passed,
        critical_failures=len(validator.reporter.critical_failures()),
    )

    if not passed:
        return _fail_phase(state, "Pre-flight validation failed! Cannot proceed.", logger)

    if is_restore_only:
        primary_version = "unknown"
        primary_obs_detected = False
        primary_obs_enabled = False
        expected_managed_cluster_names: list[str] = []
    else:
        primary_version = config["primary_version"]
        primary_obs_detected = config["primary_observability_detected"]
        primary_obs_enabled = primary_obs_detected and not args.skip_observability_checks
        expected_managed_cluster_names = list(config.get("expected_managed_cluster_names", []))

    secondary_obs_detected = config["secondary_observability_detected"]
    secondary_obs_enabled = secondary_obs_detected and not args.skip_observability_checks
    run_record.record_hub_facts(
        HubFacts(
            primary_version=primary_version,
            primary_observability_detected=primary_obs_detected,
            primary_has_observability=primary_obs_enabled,
            secondary_version=config["secondary_version"],
            secondary_observability_detected=secondary_obs_detected,
            secondary_has_observability=secondary_obs_enabled,
            has_observability=primary_obs_enabled or secondary_obs_enabled,
        )
    )
    if is_restore_only:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY
    elif getattr(args, "min_managed_clusters", None) is None:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_DERIVED_FROM_PREFLIGHT
    elif getattr(args, "min_managed_clusters", 0) == 0:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_EMPTY_ALLOWED
    else:
        expectation_mode = MANAGED_CLUSTER_EXPECTATION_EXPLICIT_MINIMUM
    run_record.record_managed_cluster_expectation(
        names=expected_managed_cluster_names,
        count=len(expected_managed_cluster_names),
        mode=expectation_mode,
    )
```

Behaviour note: the original wrote `primary_version` etc. only in the
`else` branch for non-restore-only and the restore-only trio in the `if`
branch — the replacement writes the same values through one `HubFacts`.
`EXPECTED_MANAGED_CLUSTER_NAMES_KEY`/`EXPECTED_MANAGED_CLUSTER_COUNT_KEY`/
`MANAGED_CLUSTER_EXPECTATION_KEY` imports become unused in this file once
`_resolve_managed_cluster_expectation` is migrated (Step 2) — remove them
from the import block then. The `MANAGED_CLUSTER_EXPECTATION_*` mode-value
constants stay imported.

- [ ] **Step 2: Replace the phase readers**

`acm_switchover.py:795-796` (in `_run_phase_primary_prep`):

```python
    prep = PrimaryPreparation(
        primary,
        state,
        state.get_config("primary_version", "unknown"),
        state.get_config("primary_has_observability", False),
```

becomes:

```python
    facts = RunRecord(state).hub_facts()
    prep = PrimaryPreparation(
        primary,
        state,
        facts.primary_version,
        facts.primary_has_observability,
```

`acm_switchover.py:853` (in `_run_phase_post_activation`):

```python
        state.get_config("secondary_has_observability", False),
```

becomes (add `facts = RunRecord(state).hub_facts()` above the constructor call):

```python
        facts.secondary_has_observability,
```

`acm_switchover.py:868-885` (`_resolve_managed_cluster_expectation`):

```python
    raw_min = getattr(args, "min_managed_clusters", None)
    expected_names = list(state.get_config(EXPECTED_MANAGED_CLUSTER_NAMES_KEY, []) or [])
    expected_count = int(state.get_config(EXPECTED_MANAGED_CLUSTER_COUNT_KEY, len(expected_names)) or 0)

    if raw_min is None:
        expectation_mode = state.get_config(MANAGED_CLUSTER_EXPECTATION_KEY, None)
        if expectation_mode == MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY and expected_count == 0 and not expected_names:
            return 1, [], False
        return expected_count, expected_names, bool(expected_names)
```

becomes:

```python
    raw_min = getattr(args, "min_managed_clusters", None)
    expectation = RunRecord(state).managed_cluster_expectation()
    expected_names = list(expectation.names)
    expected_count = expectation.count

    if raw_min is None:
        if expectation.mode == MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY and expected_count == 0 and not expected_names:
            return 1, [], False
        return expected_count, expected_names, bool(expected_names)
```

(the `raw_min == 0` / explicit-minimum tail is unchanged)

`acm_switchover.py:901-903` (in `_run_phase_finalization`), current:

```python
        acm_version=state.get_config("secondary_version", "unknown"),
        ...
        primary_has_observability=state.get_config("primary_has_observability", False),
```

becomes (add `facts = RunRecord(state).hub_facts()` before the constructor):

```python
        acm_version=facts.secondary_version,
        ...
        primary_has_observability=facts.primary_has_observability,
```

Add the import at the top of `acm_switchover.py`:

```python
from lib.run_record import HubFacts, RunRecord
```

- [ ] **Step 3: Run the affected suites**

Run: `python -m pytest tests/test_main.py tests/test_cli_auto_import.py tests/test_state_handoff.py tests/test_main_phase_flow.py -q`
Expected: PASS. These tests exercise the CLI phase wiring; failures here mean
a reader/writer mismatch — compare the key the test seeds against the
RunRecord op that now reads it. Tests that seed state via
`state.set_config("primary_version", ...)` still pass because the on-disk
keys are unchanged.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (3126+ tests; count grows with the new run-record tests)

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 acm_switchover.py
flake8 acm_switchover.py
git add acm_switchover.py
git commit -m "refactor: route CLI cross-phase state through RunRecord"
```

---

### Task 5: Migrate activation and finalization modules

**Files:**
- Modify: `modules/activation.py` (`__init__`, `:248`, `:256-259`, `:290-291`, `:596`, `:635`, `:663`, `:931-933`)
- Modify: `modules/finalization.py` (`__init__`, `:280-281`, `:365`, `:443`, `:461-462`, `:473-474`, `:515-516`, `:744`, `:771`, `:1520`, `:1535`, `:1542`, `:1566`, and `_get_backup_schedule_enabled_at`)
- Test: existing `tests/test_activation.py`, `tests/test_auto_import.py`, `tests/test_finalization.py`

**Interfaces:**
- Consumes: `RunRecord` ops from Tasks 1–2.
- Produces: each class gains `self.run_record = RunRecord(self.state)` (activation) / `RunRecord(self.state)` bound in `__init__` (finalization) — exact attribute name `self.run_record` in both.

- [ ] **Step 1: Bind the facade in both `__init__` methods**

In `modules/activation.py`, at the end of `SecondaryActivation.__init__`
(after `self.state = state` — locate the exact assignment first):

```python
        self.run_record = RunRecord(self.state)
```

Same for `Finalization.__init__` in `modules/finalization.py` (after its
state assignment; the attribute there is `self.state` via the
`state_manager` parameter — verify before editing).

Imports: `from lib.run_record import RunRecord` in both files.

- [ ] **Step 2: Replace activation call sites**

| Location | Before | After |
|---|---|---|
| `:248` | `self.state.set_config(PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME, None)` | `self.run_record.record_pre_activation_velero_restore(None)` |
| `:256-259` | `self.state.set_config(PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME, restore_before.get("status", {}).get("veleroManagedClustersRestoreName"))` | `self.run_record.record_pre_activation_velero_restore(restore_before.get("status", {}).get("veleroManagedClustersRestoreName"))` |
| `:290-291` | `self.state.set_config(PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME, None)` | `self.run_record.record_pre_activation_velero_restore(None)` |
| `:596` | `version = str(self.state.get_config("secondary_version", "unknown"))` | `version = self.run_record.hub_facts().secondary_version` |
| `:635` | `self.state.set_config("auto_import_strategy_set", True)` | `self.run_record.record_auto_import_override()` |
| `:663` | `version = str(self.state.get_config("secondary_version", "unknown"))` | `version = self.run_record.hub_facts().secondary_version` |
| `:931-933` | `self.state.get_config(PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME, None)` | `self.run_record.pre_activation_velero_restore()` |

Remove the now-unused `PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME`
import from `modules/activation.py`.

- [ ] **Step 3: Replace finalization call sites**

| Location | Before | After |
|---|---|---|
| `:280-281` | `self.state.set_config("backup_schedule_enabled_at", datetime.now(timezone.utc).isoformat())` + `self.state.set_config("new_backup_detected", False)` | `self.run_record.record_backup_watch_started(datetime.now(timezone.utc).isoformat())` |
| `:365` | `self.state.set_config("archived_restores", archived_restores)` | `self.run_record.record_archived_restores(archived_restores)` |
| `:443` | `recorded_backup_name = self.state.get_config("post_switchover_backup_name")` | `recorded_backup_name = self.run_record.new_backup()` |
| `:461-462`, `:473-474`, `:515-516` | `self.state.set_config("new_backup_detected", True)` + `self.state.set_config("post_switchover_backup_name", NAME)` | `self.run_record.record_new_backup(NAME)` (NAME is `recorded_backup_name` / `existing_backup_name` / `backup_name` respectively) |
| `:744` | `post_switchover_backup_name = self.state.get_config("post_switchover_backup_name")` | `post_switchover_backup_name = self.run_record.new_backup()` |
| `:771` | `if not self.state.get_config("post_switchover_backup_name"):` | `if not self.run_record.new_backup():` |
| `:1520` | `auto_import_strategy_set = self.state.get_config("auto_import_strategy_set", False)` | `auto_import_strategy_set = self.run_record.auto_import_override_pending()` |
| `:1535`, `:1542`, `:1566` | `self.state.set_config("auto_import_strategy_set", False)` | `self.run_record.clear_auto_import_override()` |

Also find `_get_backup_schedule_enabled_at` in `modules/finalization.py`
(search for `backup_schedule_enabled_at`) and replace its
`self.state.get_config("backup_schedule_enabled_at")` read with
`self.run_record.backup_watch_started_at()`.

- [ ] **Step 4: Run the affected suites, then the full suite**

Run: `python -m pytest tests/test_activation.py tests/test_auto_import.py tests/test_finalization.py -q`
Expected: PASS. Tests that seed raw keys (`state.set_config("auto_import_strategy_set", True)`)
still pass — same keys on disk. Tests that assert on
`state.get_config(...)` after calling the module still pass for the same reason.

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 modules/activation.py modules/finalization.py
flake8 modules/activation.py modules/finalization.py
git add modules/activation.py modules/finalization.py
git commit -m "refactor: route activation and finalization handoffs through RunRecord"
```

---

### Task 6: Migrate primary_prep, backup_schedule, workflow

**Files:**
- Modify: `modules/primary_prep.py:171-178`
- Modify: `modules/backup_schedule.py:132`
- Modify: `lib/workflow.py:236-242`
- Test: existing `tests/test_primary_prep.py`, `tests/test_backup_schedule.py`, `tests/test_main_phase_flow.py`

**Interfaces:**
- Consumes: `RunRecord.record_saved_backup_schedule`, `saved_backup_schedule`, `record_resume_start_phase`.

- [ ] **Step 1: primary_prep**

Bind `self.run_record = RunRecord(self.state)` in `PrimaryPreparation.__init__`
(import `from lib.run_record import RunRecord`). Then at `:171-178`:

```python
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
```

becomes:

```python
        if bs.get("spec", {}).get("paused") is True:
            logger.info("BackupSchedule %s is already paused", bs_name)
            # Still save to state for finalization (in case new hub needs it)
            if not self.run_record.saved_backup_schedule():
                self.run_record.record_saved_backup_schedule(bs)
            return

        # Always save the BackupSchedule to state for finalization
        # This allows the new hub to recreate the schedule if it doesn't have one
        # (common in passive sync scenarios where secondary only had a Restore)
        self.run_record.record_saved_backup_schedule(bs)
```

- [ ] **Step 2: backup_schedule**

`BackupScheduleManager` — bind `self.run_record = RunRecord(self.state)` in its
`__init__` (verify the attribute holding the StateManager; it is `self.state`
per `modules/backup_schedule.py:132`). Then:

```python
        saved_bs = self.state.get_config("saved_backup_schedule")
```

becomes:

```python
        saved_bs = self.run_record.saved_backup_schedule()
```

- [ ] **Step 3: workflow**

`lib/workflow.py:236-242`:

```python
    if current_phase != Phase.INIT and resume_start_phase is not None:
        state.set_config(
            STATE_KEY_RESUME_SUMMARY,
            {
                RESUME_START_PHASE_KEY: resume_start_phase,
            },
        )
```

becomes:

```python
    if current_phase != Phase.INIT and resume_start_phase is not None:
        RunRecord(state).record_resume_start_phase(resume_start_phase)
```

Import `from lib.run_record import RunRecord`; drop the now-unused
`STATE_KEY_RESUME_SUMMARY` / `RESUME_START_PHASE_KEY` imports from
`lib/workflow.py`.

- [ ] **Step 4: Run the affected suites, then the full suite**

Run: `python -m pytest tests/test_primary_prep.py tests/test_backup_schedule.py tests/test_main_phase_flow.py tests/properties/test_backup_schedule_properties.py -q`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 modules/primary_prep.py modules/backup_schedule.py lib/workflow.py
flake8 modules/primary_prep.py modules/backup_schedule.py lib/workflow.py
git add modules/primary_prep.py modules/backup_schedule.py lib/workflow.py
git commit -m "refactor: route saved-schedule and resume handoffs through RunRecord"
```

---

### Task 7: Converge the report readers on RunSummary

**Files:**
- Modify: `lib/cli_outcomes.py:75-107` (`phase_report_from_state`), `:117-125` (`write_python_report`)
- Modify: `lib/report_artifacts.py` (`_summarize_state`, `build_operation_report`, `:93`)
- Test: existing `tests/test_cli_outcomes.py` (if present — locate with `ls tests/ | grep -i outcome`), `tests/test_report_artifacts.py` (same), plus `tests/test_main.py`

**Interfaces:**
- Consumes: `RunSummary`, `StepRecord`, `ErrorRecord` from Task 3.
- Produces: `phase_report_from_state(summary: RunSummary) -> dict[str, dict]`; `build_operation_report(..., state_snapshot=..., phases=...)` signature unchanged externally but internally builds `RunSummary.from_snapshot(state_snapshot)`.

- [ ] **Step 1: Rewrite `phase_report_from_state` over RunSummary**

Current (`lib/cli_outcomes.py:75-107`) iterates raw dicts. Replace with:

```python
def phase_report_from_state(summary: RunSummary) -> dict[str, dict[str, Any]]:
    """Build a compact phase map from the typed run summary."""
    phases: dict[str, dict[str, Any]] = {}

    for step in summary.completed_steps:
        phase = step.phase if step.phase in _REPORT_PHASE_VALUES else fallback_phase_for_step(step.name)
        if not phase:
            continue
        phases.setdefault(phase, {"phase": phase, "status": "pass", "steps": []})["steps"].append(step.name)

    if summary.current_phase == Phase.FAILED.value:
        last_error = summary.errors[-1] if summary.errors else None
        failed_phase = _PHASE_VALUE_TO_REPORT_NAME.get(last_error.phase if last_error else None)
        if failed_phase:
            phases.setdefault(failed_phase, {"phase": failed_phase, "steps": []})["status"] = "fail"

    return phases
```

Import: `from lib.run_record import RunSummary`.
The tolerant-input branches (`not isinstance(state_snapshot, dict)`,
non-list `completed_steps`/`errors`) are now `RunSummary.from_snapshot`'s
job — delete them here.

- [ ] **Step 2: Update the caller in `write_python_report`**

```python
        state_snapshot = state.capture_state_snapshot()
        report = build_operation_report(
            ...
            state_snapshot=state_snapshot,
            phases=phase_report_from_state(state_snapshot),
        )
```

becomes:

```python
        state_snapshot = state.capture_state_snapshot()
        summary = RunSummary.from_snapshot(state_snapshot)
        report = build_operation_report(
            ...
            state_snapshot=state_snapshot,
            phases=phase_report_from_state(summary),
        )
```

Find every other caller of `phase_report_from_state` first:
`grep -rn "phase_report_from_state" lib/ modules/ acm_switchover.py tests/` —
production callers get the same `RunSummary.from_snapshot(...)` wrapping;
test callers are updated in Task 9.

- [ ] **Step 3: Update `lib/report_artifacts.py` internals**

In `build_operation_report`, immediately after the snapshot/config guards,
build `summary = RunSummary.from_snapshot(state_snapshot)` and:

- `_summarize_state(state_snapshot, status)` → `_summarize_state(summary, status)` with body:

```python
def _summarize_state(summary: RunSummary, status: str) -> dict[str, Any]:
    return {
        "passed": status == REPORT_STATUS_PASS,
        "completed_steps": len(summary.completed_steps),
        "error_count": len(summary.errors),
        "current_phase": summary.current_phase,
    }
```

- `:93` `raw_results = config.get("preflight_results") or []` →
  `raw_results = list(summary.preflight_results)`.
- The errors list the report embeds: read from `summary.errors` and convert
  with `dataclasses.asdict` (check how `errors` is currently shaped in the
  report body — preserve the exact output dict shape, keys `error`, `phase`,
  `timestamp`).
- `PauseRegisterStore.status_from_state_config(config)` stays exactly as is —
  the register seam is out of scope.

Import: `from lib.run_record import RunSummary`.

- [ ] **Step 4: Run the report/CLI suites, then the full suite**

Run: `python -m pytest tests/ -q -k "outcome or report or main"`
Expected: PASS after Task 9 updates test callers — at this commit, run the
full suite and fix only production wiring; if test files call
`phase_report_from_state(snapshot_dict)` directly, update those call sites
now (wrap with `RunSummary.from_snapshot`) since the signature changed.

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 lib/cli_outcomes.py lib/report_artifacts.py
flake8 lib/cli_outcomes.py lib/report_artifacts.py
git add -A
git commit -m "refactor: report readers consume RunSummary instead of raw snapshots"
```

---

### Task 8: show_state — RunSummary sections and shared state-dir resolution

**Files:**
- Modify: `show_state.py:111-121` (`_default_state_dir`), `print_state` phase/steps/errors sections
- Test: locate with `ls tests/ | grep -i show_state` (e.g. `tests/test_show_state.py`)

**Interfaces:**
- Consumes: `RunSummary.from_snapshot`, `lib.runtime_bootstrap.get_default_state_dir`.

- [ ] **Step 1: Replace `_default_state_dir`**

```python
def _default_state_dir() -> str:
    env_state_dir = os.environ.get(STATE_DIR_ENV_VAR)
    if env_state_dir and env_state_dir.strip():
        try:
            InputValidator.validate_safe_filesystem_path(env_state_dir.strip(), STATE_DIR_ENV_VAR)
            return env_state_dir.strip()
        except ValidationError:
            # Viewer tool: ignore unsafe env var and fall back to default
            return ".state"
    return ".state"
```

becomes:

```python
def _default_state_dir() -> str:
    # Deliberately identical to the CLI's resolution: the viewer must look
    # where the CLI writes, including ACM_SWITCHOVER_STATE_DIR edge cases.
    return get_default_state_dir()
```

with `from lib.runtime_bootstrap import get_default_state_dir` (verify the
function name at `lib/runtime_bootstrap.py:32-36` before importing; if the
helper takes arguments, mirror how `acm_switchover.py` calls it). Remove the
now-unused `InputValidator`/`ValidationError`/`STATE_DIR_ENV_VAR` imports if
nothing else in the file uses them.

Update or delete any test asserting the old fallback-on-unsafe-env
behaviour; add its replacement:

```python
def test_default_state_dir_matches_cli(monkeypatch, tmp_path):
    from lib.runtime_bootstrap import get_default_state_dir
    import show_state

    monkeypatch.setenv("ACM_SWITCHOVER_STATE_DIR", str(tmp_path / "custom-state"))
    assert show_state._default_state_dir() == get_default_state_dir()
```

- [ ] **Step 2: Render phase/steps/errors via RunSummary**

In `print_state`, before the sections:

```python
    summary = RunSummary.from_snapshot(state)
```

- Current Phase section: `phase = state.get("current_phase", "unknown")` →
  `phase = summary.current_phase or "unknown"`.
- Completed Steps loop: iterate `summary.completed_steps`, using
  `step.name` / `step.timestamp` in place of `step.get("name", "unknown")` /
  `step.get("timestamp", "")`.
- Errors loop: iterate `summary.errors`, using `err.phase or "unknown"`,
  `err.error`, `err.timestamp or ""`.

The Overview/Contexts sections and the generic Configuration dict dump keep
reading the raw loaded file — the viewer's job is to show whatever is in the
file, including keys this facade does not own.

Import: `from lib.run_record import RunSummary`.

- [ ] **Step 3: Run the show_state tests, then the full suite**

Run: `python -m pytest tests/ -q -k show_state`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 4: Format, lint, commit**

```bash
black --line-length 120 show_state.py
flake8 show_state.py
git add -A
git commit -m "refactor: show_state renders via RunSummary and shares CLI state-dir resolution"
```

---

### Task 9: Migrate tests off raw config keys

**Files:**
- Modify: test files found by `grep -rln "set_config\|get_config" tests/` **excluding** register-focused files (`tests/test_argocd_register*.py`, `tests/argocd_register_helpers.py`, `tests/test_argocd_resume_helpers.py`, `tests/test_main_argocd_resume.py`, `tests/properties/test_checkpoint_properties.py`, `tests/test_utils.py`, `tests/test_kube_client.py`)

**Interfaces:**
- Consumes: every RunRecord op from Tasks 1–3.

- [ ] **Step 1: Sweep the handoff keys**

For each non-register test file, mechanically replace seeding/assertion of
the nine handoff groups with RunRecord calls. The mapping (both directions —
`set_config` seeds and `get_config` assertions):

| Raw usage | Replacement |
|---|---|
| `state.set_config("auto_import_strategy_set", True)` | `RunRecord(state).record_auto_import_override()` |
| `state.set_config("auto_import_strategy_set", False)` | `RunRecord(state).clear_auto_import_override()` |
| `state.get_config("auto_import_strategy_set", False)` | `RunRecord(state).auto_import_override_pending()` |
| `state.set_config("secondary_version", V)` (and other hub-fact keys) | `RunRecord(state).record_hub_facts(HubFacts(secondary_version=V, ...))` — build one HubFacts per test with only the fields the test needs |
| `state.get_config("primary_has_observability", ...)` | `RunRecord(state).hub_facts().primary_has_observability` |
| `state.set_config("saved_backup_schedule", BS)` | `RunRecord(state).record_saved_backup_schedule(BS)` |
| `state.get_config("saved_backup_schedule")` | `RunRecord(state).saved_backup_schedule()` |
| `state.set_config("post_switchover_backup_name", N)` + `state.set_config("new_backup_detected", True)` | `RunRecord(state).record_new_backup(N)` |
| `state.get_config("post_switchover_backup_name")` | `RunRecord(state).new_backup()` |
| `state.set_config("backup_schedule_enabled_at", T)` | `RunRecord(state).record_backup_watch_started(T)` — note this also resets `new_backup_detected`; if a test seeds detection *after* the watch start, order the calls accordingly |
| `state.get_config("archived_restores", ...)` | assert via `state.capture_state_snapshot()["config"]["archived_restores"]` only inside `tests/test_run_record.py`; elsewhere prefer asserting on behaviour |
| `state.set_config(EXPECTED_MANAGED_CLUSTER_NAMES_KEY, ...)` trio | `RunRecord(state).record_managed_cluster_expectation(names=..., count=..., mode=...)` |
| `state.set_config("preflight_results", R)` | `RunRecord(state).record_preflight_results(R, passed=..., critical_failures=...)` |

Keys that belong to test-fixture scenarios of `StateManager` itself
(`"key"`, `"nested"`, `"scalar"`, `"stale"`, `"generated"`, etc. in
`tests/test_utils.py`, `tests/properties/`) are exercising the storage
layer, not a handoff — leave them; they move to the private accessor names
in Task 10.

- [ ] **Step 2: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. Any failure is a mapping error in Step 1 — diff the seeded
key against the RunRecord op's key constant in `lib/run_record.py`.

- [ ] **Step 3: Verify no handoff-key literals remain outside the facade**

Run:
```bash
grep -rn "set_config(\"\|get_config(\"" lib/ modules/ acm_switchover.py show_state.py
```
Expected: matches only in `lib/utils.py` (the definitions) and none with
string literals elsewhere. Then:
```bash
grep -rn "auto_import_strategy_set\|saved_backup_schedule\|post_switchover_backup_name\|new_backup_detected\|backup_schedule_enabled_at" tests/ --include="*.py" -l
```
Expected: only `tests/test_run_record.py` (snapshot-shape assertions).

- [ ] **Step 4: Format, lint, commit**

```bash
black --line-length 120 tests/
flake8 tests/
git add tests/
git commit -m "test: migrate suites from raw config keys to RunRecord operations"
```

---

### Task 10: Privatize the accessors and lock the seam

**Files:**
- Modify: `lib/utils.py:570-584` (rename), `lib/run_record.py` (`_set`/`_get` bodies), `lib/argocd_register.py:211` (+ reads at `:136`, `:208`, `:441`), `lib/argocd_register_store.py:78,150-153,190-201,215-223`
- Create: `tests/test_run_record_guardrails.py`
- Modify: `tests/test_utils.py`, `tests/properties/` files that call the storage layer directly

**Interfaces:**
- Consumes: everything above.
- Produces: `StateManager._set_config(key, value)` / `StateManager._get_config(key, default=None)` — private storage accessors. Allowed production callers: `lib/run_record.py`, `lib/argocd_register.py`, `lib/argocd_register_store.py` (documented allowance — the register's own seam, converging under issue #208).

- [ ] **Step 1: Write the failing guardrail test**

```python
# tests/test_run_record_guardrails.py
"""Seam lock for the run record (spec 2026-08-02-run-record-design.md).

The config-key vocabulary belongs to lib/run_record.py. StateManager's
storage accessors are private; the pause-register modules keep a narrow,
documented allowance (their seam converges separately under issue #208).
"""

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent

PRODUCTION_ROOTS = ["lib", "modules", "acm_switchover.py", "show_state.py", "check_rbac.py"]

# The only production modules allowed to touch StateManager's storage accessors.
ALLOWED = {
    REPO / "lib" / "utils.py",  # the definitions
    REPO / "lib" / "run_record.py",  # the vocabulary owner
    REPO / "lib" / "argocd_register.py",  # register allowance (issue #208)
    REPO / "lib" / "argocd_register_store.py",  # register allowance (issue #208)
}

ACCESSOR = re.compile(r"\.(?:_set_config|_get_config|set_config|get_config)\(")


def _production_files():
    for root in PRODUCTION_ROOTS:
        path = REPO / root
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def test_config_accessors_only_used_by_allowed_modules():
    offenders = []
    for path in _production_files():
        if path in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ACCESSOR.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, "config accessors outside the run-record seam:\n" + "\n".join(offenders)


def test_public_accessors_are_gone():
    utils_src = (REPO / "lib" / "utils.py").read_text(encoding="utf-8")
    assert "def set_config(" not in utils_src
    assert "def get_config(" not in utils_src
    assert "def _set_config(" in utils_src
    assert "def _get_config(" in utils_src
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_run_record_guardrails.py -v`
Expected: FAIL — `test_public_accessors_are_gone` (accessors still public).
`test_config_accessors_only_used_by_allowed_modules` should already PASS
(Tasks 4–8 removed the other callers) — if it fails, those call sites are
stragglers: fix them first.

- [ ] **Step 3: Rename and update the allowed callers**

- `lib/utils.py`: `def set_config` → `def _set_config`, `def get_config` → `def _get_config` (docstrings gain: "Private storage accessor — production access goes through lib/run_record.RunRecord; the pause-register modules hold a documented allowance (issue #208).").
- `lib/run_record.py` `_set`/`_get`: call `self._state._set_config` / `self._state._get_config`.
- `lib/argocd_register.py` and `lib/argocd_register_store.py`: mechanical rename at each call site (`grep -n "set_config\|get_config" lib/argocd_register.py lib/argocd_register_store.py`).
- Storage-layer tests (`tests/test_utils.py`, `tests/properties/test_checkpoint_properties.py`, `tests/properties/strategies.py`, register test helpers): rename to the private accessors — they test the storage layer and register seam, which is what the private surface is for. Every other test file must already be clean after Task 9; if the rename breaks one, that test belongs in the Task 9 mapping — migrate it, don't whitelist it.

- [ ] **Step 4: Run the guardrails, then the full suite**

Run: `python -m pytest tests/test_run_record_guardrails.py -v`
Expected: PASS (both)

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Format, lint, commit**

```bash
black --line-length 120 lib/utils.py lib/run_record.py lib/argocd_register.py lib/argocd_register_store.py tests/
flake8 lib/ modules/ tests/ acm_switchover.py show_state.py
git add -A
git commit -m "refactor: privatize StateManager config accessors behind the RunRecord seam"
```

---

### Task 11: Round-trip compatibility proof, CONTEXT.md, follow-up issue

**Files:**
- Modify: `tests/test_run_record.py` (compatibility test)
- Modify: `CONTEXT.md`
- No code changes.

- [ ] **Step 1: Write the interface-only persistence proof**

```python
# append to tests/test_run_record.py

class TestInterfaceOnlyPersistence:
    """A state file written by the pre-RunRecord tool loads identically."""

    def test_legacy_state_file_reads_through_run_record(self, tmp_path):
        # Shape produced by the previous release: raw keys in config.
        legacy = {
            "version": "1.0",
            "current_phase": "finalization",
            "completed_steps": [{"name": "activate_managed_clusters", "phase": "activation", "timestamp": "t"}],
            "errors": [],
            "config": {
                "primary_version": "2.13.2",
                "primary_has_observability": True,
                "secondary_version": "2.14.0",
                "auto_import_strategy_set": True,
                "saved_backup_schedule": {"metadata": {"name": "schedule-acm"}},
                "post_switchover_backup_name": "acm-backup-9",
                "new_backup_detected": True,
            },
        }
        state_file = tmp_path / "switchover-legacy.json"
        state_file.write_text(json.dumps(legacy))

        record = RunRecord(StateManager(str(state_file)))
        assert record.hub_facts().primary_version == "2.13.2"
        assert record.hub_facts().primary_has_observability is True
        assert record.auto_import_override_pending() is True
        assert record.saved_backup_schedule() == {"metadata": {"name": "schedule-acm"}}
        assert record.new_backup() == "acm-backup-9"
```

(add `import json` to the test file's imports)

Note: `StateManager.__init__` may validate/merge loaded state — if the
minimal legacy dict is rejected, copy the missing required top-level fields
(e.g. `created_at`, `contexts`) from a freshly created StateManager file:
create one with `StateManager(str(tmp_path / "fresh.json"))`, read the JSON
it writes, and overlay the `legacy` dict's fields onto it. The assertion
set stays the same.

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_run_record.py -v -k Legacy`
Expected: PASS

- [ ] **Step 3: Add the domain term to CONTEXT.md**

Append to the `## Language` section of `CONTEXT.md`:

```markdown
**Run record**:
The cross-phase facts of one switchover run — what preflight discovered and
what each phase has recorded for later phases or reports — exposed only as
named, typed operations on `RunRecord` (`lib/run_record.py`). The durable
file behind it belongs to `StateManager`; the key vocabulary belongs to
`RunRecord` alone.
_Avoid_: config keys, state config, set_config/get_config (outside the facade)
```

- [ ] **Step 4: File the follow-up issue for collection parity**

```bash
gh issue create --title "Run-record follow-up: converge collection checkpoint state on named operations" \
  --body "The Python CLI's cross-phase state now goes through the RunRecord facade (lib/run_record.py; spec docs/superpowers/specs/2026-08-02-run-record-design.md). The collection's module_utils/checkpoint.py still uses its own state access. Evaluate converging it on the same named-operation vocabulary, and whether a parity fixture should pin the shared key names."
```

- [ ] **Step 5: Full suite, format, commit**

```bash
python -m pytest tests/ -q
black --line-length 120 tests/test_run_record.py
git add tests/test_run_record.py CONTEXT.md
git commit -m "test: prove interface-only persistence; docs: define the run record term"
```

---

## Final verification (whole plan)

- [ ] `python -m pytest tests/ -q` — green.
- [ ] `flake8` over `lib/ modules/ tests/ acm_switchover.py show_state.py` — clean.
- [ ] `grep -rn "set_config(\"\|get_config(\"" lib/ modules/ acm_switchover.py show_state.py` — no string-literal accessor calls anywhere.
- [ ] `git log --oneline origin/ansible..HEAD` — one spec commit + one commit per task.
- [ ] Push `feat/run-record-spec` and open a draft PR against `ansible` titled "refactor: deepen the switchover run record (RunRecord facade)"; PR body links the spec and the 2026-08-02 architecture review, and notes: on-disk schema unchanged, register keys out of scope (#208), collection parity follow-up issue filed.
