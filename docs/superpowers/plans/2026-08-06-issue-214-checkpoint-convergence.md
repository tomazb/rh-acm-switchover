# Checkpoint Convergence and Default Posture (#214) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the auto-import reset obligation survive interruption at shipped defaults (cluster-as-register marker), converge the collection's checkpoint access on named operations, and settle the resume_summary divergence on Python's replace semantics.

**Architecture:** A marker annotation rides the `ImportAndSync` ConfigMap patch (atomic obligation, PR #223 pattern); finalization discharges by cluster observation; preflight warns on orphans. `module_utils/checkpoint.py` becomes the sole owner of checkpoint key literals; `checkpoint_phase` returns a flattened `facts` dict roles read instead of raw Jinja chains. Spec: `docs/superpowers/specs/2026-08-06-checkpoint-convergence-design.md`.

**Tech Stack:** Python 3 (Ansible action plugin + module_utils), Ansible role YAML, pytest (root suite + collection unit suite), yaml-contract tests.

> **Post-plan amendments (2026-08-07).** This plan is a dated design record;
> review fixes changed three contracts after execution, and the shipped tests —
> not the snippets below — are authoritative:
> 1. The resume sentinel `_acm_switchover_resume_recorded` carries the
>    controller PID (`str(os.getpid())`), not `True`, so a stale value from a
>    persistent fact cache cannot fence a later process
>    (`test_checkpoint_phase_runtime.py`).
> 2. The orphan-check read uses `ignore_errors: true`, never
>    `failed_when: false`, so the registered result keeps its real `failed`
>    state and the read-failure warning stays reachable
>    (`test_preflight_auto_import_orphan.py`); the primary hub is probed only
>    outside `restore_only` mode.
> 3. `checkpoint_facts` coerces floor-stringified scalars (digit strings,
>    Ansible bool vocabulary) instead of strict isinstance typing, and the
>    finalization discharge delete is gated on `mode == 'execute'`
>    (`test_checkpoint_facade.py`, `test_activation_auto_import.py`).

## Global Constraints

- Formatting: `black --line-length 120` (project has no black config; 120 is CI's).
- No `Co-Authored-By` / AI-attribution trailers in commits.
- No on-disk checkpoint key renames — `auto_import_strategy_changed` etc. keep their current names.
- `checkpoint.enabled` stays `false` in all five role defaults.
- Marker annotation constant: `acm-switchover.open-cluster-management.io/import-strategy-set-by` = `acm-switchover`.
- Collection root: `ansible_collections/tomazb/acm_switchover/` (abbreviated `COLL/` below).
- Run collection unit tests from repo root: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit -q`. Root suite: `python -m pytest tests/ -q`.

---

### Task 1: Facade — named accessors and key constants in module_utils/checkpoint.py

**Files:**
- Modify: `COLL/plugins/module_utils/checkpoint.py`
- Modify: `COLL/plugins/module_utils/constants.py` (marker constants)
- Test: `COLL/tests/unit/test_checkpoint_facade.py` (create)

**Interfaces:**
- Produces (used by Tasks 2, 4, 5, 7):
  - Key constants in `checkpoint.py`: `KEY_ARGOCD_RUN_ID = "argocd_run_id"`, `KEY_ARGOCD_DISCOVERY_NAMESPACES = "argocd_discovery_namespaces"`, `KEY_AUTO_IMPORT_STRATEGY_CHANGED = "auto_import_strategy_changed"`, `KEY_EXPECTED_MANAGED_CLUSTER_NAMES = "expected_managed_cluster_names"`, `KEY_EXPECTED_MANAGED_CLUSTER_COUNT = "expected_managed_cluster_count"`, `KEY_PRIMARY_HAS_OBSERVABILITY = "primary_has_observability"`, `KEY_SECONDARY_HAS_OBSERVABILITY = "secondary_has_observability"`, `KEY_SAVED_BACKUP_SCHEDULE = "saved_backup_schedule"`, `KEY_BACKUP_SCHEDULE_ENABLED_AT = "backup_schedule_enabled_at"`, `KEY_RESUME_SUMMARY = "resume_summary"`, `KEY_RESUME_START_PHASE = "resume_start_phase"`
  - `checkpoint_facts(checkpoint: dict) -> dict` — flattened named facts, malformed shapes degrade to defaults
  - `record_resume_start_phase(checkpoint: dict, phase: str) -> None` — REPLACE semantics
  - In `constants.py`: `AUTO_IMPORT_MARKER_ANNOTATION = "acm-switchover.open-cluster-management.io/import-strategy-set-by"`, `AUTO_IMPORT_MARKER_VALUE = "acm-switchover"`

- [ ] **Step 1: Write the failing tests**

Create `COLL/tests/unit/test_checkpoint_facade.py`:

```python
"""Tests for the named-operation facade over checkpoint dicts (issue #214)."""

from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    checkpoint_facts,
    record_resume_start_phase,
)


def test_checkpoint_facts_reads_named_values():
    checkpoint = {
        "operational_data": {
            "argocd_run_id": "run-1",
            "argocd_discovery_namespaces": {"openshift-gitops": ["app1"]},
            "auto_import_strategy_changed": True,
            "expected_managed_cluster_names": ["c1", "c2"],
            "expected_managed_cluster_count": 2,
            "primary_has_observability": True,
            "secondary_has_observability": False,
            "saved_backup_schedule": {"metadata": {"name": "sched"}},
            "backup_schedule_enabled_at": "2026-08-06T00:00:00+00:00",
            "resume_summary": {"resume_start_phase": "activation"},
        }
    }
    facts = checkpoint_facts(checkpoint)
    assert facts["argocd_run_id"] == "run-1"
    assert facts["argocd_discovery_namespaces"] == {"openshift-gitops": ["app1"]}
    assert facts["auto_import_strategy_changed"] is True
    assert facts["expected_managed_cluster_names"] == ["c1", "c2"]
    assert facts["expected_managed_cluster_count"] == 2
    assert facts["primary_has_observability"] is True
    assert facts["secondary_has_observability"] is False
    assert facts["saved_backup_schedule"] == {"metadata": {"name": "sched"}}
    assert facts["backup_schedule_enabled_at"] == "2026-08-06T00:00:00+00:00"
    assert facts["resume_start_phase"] == "activation"


def test_checkpoint_facts_degrades_malformed_shapes_to_defaults():
    for checkpoint in (None, [], {}, {"operational_data": "bogus"}, {"operational_data": {"resume_summary": "bogus"}}):
        facts = checkpoint_facts(checkpoint)
        assert facts["argocd_run_id"] == ""
        assert facts["argocd_discovery_namespaces"] == {}
        assert facts["auto_import_strategy_changed"] is False
        assert facts["expected_managed_cluster_names"] is None
        assert facts["expected_managed_cluster_count"] is None
        assert facts["primary_has_observability"] is None
        assert facts["secondary_has_observability"] is None
        assert facts["saved_backup_schedule"] is None
        assert facts["backup_schedule_enabled_at"] == ""
        assert facts["resume_start_phase"] == ""


def test_record_resume_start_phase_replaces_whole_summary():
    """Convergence on Python RunRecord.record_resume_start_phase: replace, not fill-if-unset."""
    checkpoint = {"operational_data": {"resume_summary": {"resume_start_phase": "preflight", "extra": "stale"}}}
    record_resume_start_phase(checkpoint, "activation")
    assert checkpoint["operational_data"]["resume_summary"] == {"resume_start_phase": "activation"}


def test_record_resume_start_phase_creates_operational_data():
    checkpoint = {}
    record_resume_start_phase(checkpoint, "post_activation")
    assert checkpoint["operational_data"]["resume_summary"] == {"resume_start_phase": "post_activation"}


def test_auto_import_marker_constants():
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
        AUTO_IMPORT_MARKER_ANNOTATION,
        AUTO_IMPORT_MARKER_VALUE,
    )

    assert AUTO_IMPORT_MARKER_ANNOTATION == "acm-switchover.open-cluster-management.io/import-strategy-set-by"
    assert AUTO_IMPORT_MARKER_VALUE == "acm-switchover"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_facade.py -q`
Expected: FAIL — `ImportError: cannot import name 'checkpoint_facts'`

- [ ] **Step 3: Implement the facade**

Append to `COLL/plugins/module_utils/checkpoint.py` (key constants near the top with the existing constants; functions at the end):

```python
# Named-operation vocabulary over checkpoint operational_data (issue #214).
# These literals are private to this module: roles read the flattened `facts`
# returned by checkpoint_phase, never raw operational_data keys (guardrail:
# tests/unit/test_checkpoint_vocabulary_guardrail.py).
KEY_ARGOCD_RUN_ID = "argocd_run_id"
KEY_ARGOCD_DISCOVERY_NAMESPACES = "argocd_discovery_namespaces"
KEY_AUTO_IMPORT_STRATEGY_CHANGED = "auto_import_strategy_changed"
KEY_EXPECTED_MANAGED_CLUSTER_NAMES = "expected_managed_cluster_names"
KEY_EXPECTED_MANAGED_CLUSTER_COUNT = "expected_managed_cluster_count"
KEY_PRIMARY_HAS_OBSERVABILITY = "primary_has_observability"
KEY_SECONDARY_HAS_OBSERVABILITY = "secondary_has_observability"
KEY_SAVED_BACKUP_SCHEDULE = "saved_backup_schedule"
KEY_BACKUP_SCHEDULE_ENABLED_AT = "backup_schedule_enabled_at"
KEY_RESUME_SUMMARY = "resume_summary"
KEY_RESUME_START_PHASE = "resume_start_phase"


def _operational_data(checkpoint) -> dict:
    if not isinstance(checkpoint, dict):
        return {}
    data = checkpoint.get("operational_data")
    return data if isinstance(data, dict) else {}


def checkpoint_facts(checkpoint) -> dict:
    """Flattened named view of a checkpoint's cross-phase facts.

    Malformed or missing shapes degrade to defaults (same tolerance model as
    the Python CLI's RunSummary.from_snapshot). Values that roles must be able
    to distinguish as never-recorded stay None.
    """
    data = _operational_data(checkpoint)
    namespaces = data.get(KEY_ARGOCD_DISCOVERY_NAMESPACES)
    saved_schedule = data.get(KEY_SAVED_BACKUP_SCHEDULE)
    resume_summary = data.get(KEY_RESUME_SUMMARY)
    if not isinstance(resume_summary, dict):
        resume_summary = {}
    return {
        KEY_ARGOCD_RUN_ID: data.get(KEY_ARGOCD_RUN_ID) or "",
        KEY_ARGOCD_DISCOVERY_NAMESPACES: namespaces if isinstance(namespaces, dict) else {},
        KEY_AUTO_IMPORT_STRATEGY_CHANGED: bool(data.get(KEY_AUTO_IMPORT_STRATEGY_CHANGED, False)),
        KEY_EXPECTED_MANAGED_CLUSTER_NAMES: data.get(KEY_EXPECTED_MANAGED_CLUSTER_NAMES),
        KEY_EXPECTED_MANAGED_CLUSTER_COUNT: data.get(KEY_EXPECTED_MANAGED_CLUSTER_COUNT),
        KEY_PRIMARY_HAS_OBSERVABILITY: data.get(KEY_PRIMARY_HAS_OBSERVABILITY),
        KEY_SECONDARY_HAS_OBSERVABILITY: data.get(KEY_SECONDARY_HAS_OBSERVABILITY),
        KEY_SAVED_BACKUP_SCHEDULE: saved_schedule if isinstance(saved_schedule, dict) else None,
        KEY_BACKUP_SCHEDULE_ENABLED_AT: data.get(KEY_BACKUP_SCHEDULE_ENABLED_AT) or "",
        KEY_RESUME_START_PHASE: resume_summary.get(KEY_RESUME_START_PHASE) or "",
    }


def record_resume_start_phase(checkpoint: dict, phase: str) -> None:
    """Record where this resumed run starts. Replace semantics — parity with
    Python RunRecord.record_resume_start_phase (last resume wins)."""
    data = checkpoint.get("operational_data")
    if not isinstance(data, dict):
        data = {}
        checkpoint["operational_data"] = data
    data[KEY_RESUME_SUMMARY] = {KEY_RESUME_START_PHASE: phase}
```

Append to `COLL/plugins/module_utils/constants.py`:

```python
# Ownership marker written atomically with the ImportAndSync ConfigMap patch
# (issue #214, audit C3). The cluster is the collection's register: finalization
# discharges the reset obligation when this marker is observed, regardless of
# checkpoint state.
AUTO_IMPORT_MARKER_ANNOTATION = "acm-switchover.open-cluster-management.io/import-strategy-set-by"
AUTO_IMPORT_MARKER_VALUE = "acm-switchover"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_facade.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Format and commit**

```bash
black --line-length 120 ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py ansible_collections/tomazb/acm_switchover/plugins/module_utils/constants.py ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_facade.py
git add -A && git commit -m "feat(ansible): add named-operation facade over checkpoint operational_data (#214)"
```

---

### Task 2: checkpoint_phase — `facts` output and resume_summary replace with process scoping

**Files:**
- Modify: `COLL/plugins/action/checkpoint_phase.py:174-203` (the `status == "enter"` branch)
- Test: `COLL/tests/unit/plugins/action/test_checkpoint_phase_runtime.py` (append)

**Interfaces:**
- Consumes: `checkpoint_facts`, `record_resume_start_phase` from Task 1.
- Produces (used by Task 3 role edits): `enter` result gains `facts` (dict from `checkpoint_facts`); when a resume is recorded, result gains `ansible_facts: {"_acm_switchover_resume_recorded": True}`. Later `enter` calls in the same process see `task_vars["_acm_switchover_resume_recorded"]` and do not overwrite.

- [ ] **Step 1: Write the failing tests**

Append to `COLL/tests/unit/plugins/action/test_checkpoint_phase_runtime.py` (reuse the existing `_make_checkpoint_action` and `_task_vars_with_operation_identity` helpers; write a checkpoint file with completed phases the way existing resume tests in this file do — follow the file's established fixture style for creating `tmp_path` checkpoint JSON):

```python
def _write_resumable_checkpoint(tmp_path, task_vars, completed=("preflight",)):
    """Persist a schema-2.0 checkpoint with completed phases for resume tests."""
    identity = build_operation_identity(
        hubs=task_vars.get("acm_switchover_hubs") or {},
        operation=task_vars.get("acm_switchover_operation") or {},
        collection_version=task_vars.get("acm_switchover_collection_version"),
        hub_identities=task_vars.get("acm_switchover_hub_identities") or {},
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    record = {
        "schema_version": "2.0",
        "phase": "preflight",
        "completed_phases": list(completed),
        "operational_data": {"argocd_run_id": "run-7"},
        "operation_identity": identity,
        "errors": [],
        "report_refs": [],
        "created_at": "2026-08-06T00:00:00+00:00",
        "updated_at": "2026-08-06T00:00:00+00:00",
    }
    checkpoint_path.write_text(json.dumps(record))
    return str(checkpoint_path)


def test_enter_returns_named_facts(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    path = _write_resumable_checkpoint(tmp_path, task_vars)
    action = _make_checkpoint_action(
        {"phase": "primary_prep", "status": "enter", "checkpoint": {"enabled": True, "path": path}}
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert result["facts"]["argocd_run_id"] == "run-7"
    assert result["facts"]["auto_import_strategy_changed"] is False
    assert result["facts"]["resume_start_phase"] == "primary_prep"


def test_resumed_enter_replaces_resume_summary_and_flags_process(tmp_path):
    """First non-completed enter of a process replaces resume_summary wholesale."""
    task_vars = _task_vars_with_operation_identity()
    path = _write_resumable_checkpoint(tmp_path, task_vars)
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["operational_data"]["resume_summary"] = {"resume_start_phase": "preflight", "stale": "yes"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)

    action = _make_checkpoint_action(
        {"phase": "activation", "status": "enter", "checkpoint": {"enabled": True, "path": path}}
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert result["ansible_facts"] == {"_acm_switchover_resume_recorded": True}
    with open(path, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["operational_data"]["resume_summary"] == {"resume_start_phase": "activation"}


def test_same_process_later_enter_does_not_overwrite_resume_summary(tmp_path):
    task_vars = _task_vars_with_operation_identity()
    task_vars["_acm_switchover_resume_recorded"] = True
    path = _write_resumable_checkpoint(tmp_path, task_vars)
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["operational_data"]["resume_summary"] = {"resume_start_phase": "primary_prep"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)

    action = _make_checkpoint_action(
        {"phase": "post_activation", "status": "enter", "checkpoint": {"enabled": True, "path": path}}
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert "ansible_facts" not in result
    with open(path, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["operational_data"]["resume_summary"] == {"resume_start_phase": "primary_prep"}


def test_fresh_run_records_no_resume_summary(tmp_path):
    """Empty completed_phases = not a resume: no resume_summary, no process flag."""
    task_vars = _task_vars_with_operation_identity()
    path = str(tmp_path / "checkpoint.json")
    action = _make_checkpoint_action(
        {"phase": "preflight", "status": "enter", "checkpoint": {"enabled": True, "path": path}}
    )
    result = action.run(task_vars=task_vars)
    assert result.get("failed") is not True
    assert "ansible_facts" not in result
    assert result["facts"]["resume_start_phase"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q -k "named_facts or resume_summary or fresh_run"`
Expected: FAIL — `KeyError: 'facts'` and merge-instead-of-replace assertions

- [ ] **Step 3: Implement**

In `COLL/plugins/action/checkpoint_phase.py`, add the facade imports to the existing module_utils import block:

```python
    checkpoint_facts,
    record_resume_start_phase,
```

Replace the `status == "enter"` branch body (lines 174-203) with:

```python
        if status == "enter":
            resume_summary_changed = False
            already_done = False if execution_mode == "validate" else not should_resume_phase(checkpoint_data, phase)
            if (
                not is_non_mutating
                and checkpoint_data.get("completed_phases")
                and not already_done
                and not task_vars.get("_acm_switchover_resume_recorded")
            ):
                # First executing phase of this process on a resumed checkpoint:
                # replace resume_summary wholesale (parity with Python RunRecord —
                # last resume wins). Later enters in the same process are fenced
                # by the _acm_switchover_resume_recorded fact returned below.
                record_resume_start_phase(checkpoint_data, phase)
                resume_summary_changed = True
            if (
                (operation_identity_changed or reset_from or resume_summary_changed)
                and backend == CHECKPOINT_BACKEND_FILE
                and not is_non_mutating
            ):
                if operation_identity_changed or resume_summary_changed:
                    checkpoint_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_result = self._save_checkpoint(path, checkpoint_data)
                if save_result is not None and save_result.get("failed"):
                    return save_result
            result = {
                "changed": False,
                "checkpoint": checkpoint_data,
                "skipped_phase": already_done,
                "facts": checkpoint_facts(checkpoint_data),
            }
            if resume_summary_changed:
                result["ansible_facts"] = {"_acm_switchover_resume_recorded": True}
            return result
```

- [ ] **Step 4: Run the full runtime test file**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q`
Expected: PASS. If a pre-existing test asserts the old fill-if-unset merge behavior, update that test to the replace contract (cite the spec in its docstring) — do not weaken new tests.

Also run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q`
Expected: PASS (update any fill-if-unset assertions the same way).

- [ ] **Step 5: Format and commit**

```bash
black --line-length 120 ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py
git add -A && git commit -m "feat(ansible): checkpoint_phase returns named facts; resume_summary converges on replace (#214)"
```

---

### Task 3: Roles read facts, not raw operational_data — plus vocabulary guardrail

**Files:**
- Modify: `COLL/roles/preflight/tasks/main.yml` (read chains near lines 23-90)
- Modify: `COLL/roles/activation/tasks/main.yml` (lines 19, 26, 81, 87, 108, 114)
- Modify: `COLL/roles/primary_prep/tasks/main.yml` (line 22 area)
- Modify: `COLL/roles/finalization/tasks/main.yml` (lines 13-51 restore set_facts)
- Test: `COLL/tests/unit/test_checkpoint_vocabulary_guardrail.py` (create)

**Interfaces:**
- Consumes: `_checkpoint_enter.facts.<name>` from Task 2. Fact names: `argocd_run_id`, `argocd_discovery_namespaces`, `auto_import_strategy_changed`, `expected_managed_cluster_names`, `expected_managed_cluster_count`, `primary_has_observability`, `secondary_has_observability`, `saved_backup_schedule`, `backup_schedule_enabled_at`, `resume_start_phase`.
- Produces: role YAML free of read-side `operational_data` chains; guardrail test enforcing it.

- [ ] **Step 1: Write the failing guardrail test**

Create `COLL/tests/unit/test_checkpoint_vocabulary_guardrail.py`:

```python
"""Guardrail: checkpoint key vocabulary lives in module_utils, not role YAML.

Mirror of the Python side's tests/test_run_record_guardrails.py: roles consume
the flattened `facts` dict returned by checkpoint_phase; raw operational_data
read chains in YAML bypass the facade and are forbidden (issue #214).
"""

import pathlib

ROLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "roles"

FORBIDDEN_PATTERNS = (
    ".get('operational_data'",
    '.get("operational_data"',
)


def test_roles_do_not_read_operational_data_directly():
    offenders = []
    for path in sorted(ROLES_DIR.rglob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in FORBIDDEN_PATTERNS):
            offenders.append(str(path.relative_to(ROLES_DIR)))
    assert not offenders, (
        "Role YAML must read checkpoint state via _checkpoint_enter.facts, "
        f"not raw operational_data chains. Offenders: {offenders}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_checkpoint_vocabulary_guardrail.py -q`
Expected: FAIL listing preflight, activation, primary_prep, finalization task files.

- [ ] **Step 3: Replace the read chains**

The uniform rewrite: `(((_checkpoint_enter | default({})) or {}).get('checkpoint', {}) or {}).get('operational_data', {}).get('<key>', <default>)` becomes `((_checkpoint_enter | default({})) or {}).get('facts', {}).get('<key>', <default>)`. Concretely:

`COLL/roles/activation/tasks/main.yml` — the run_id rehydrate task (line 19) and its guard (line 26):

```yaml
            'run_id': ((_checkpoint_enter | default({})) or {}).get('facts', {}).get('argocd_run_id')
```

```yaml
    - ((((_checkpoint_enter | default({})) or {}).get('facts', {}) or {}).get('argocd_run_id', '') | length) > 0
```

The two operational_data write blocks (fail-mark line 81/87, pass-mark line 108/114) merge prior values into their writes; the prior-value reads become:

```yaml
              ((_checkpoint_enter | default({})) or {}).get('facts', {}).get('argocd_discovery_namespaces', {})
```

```yaml
              ((_checkpoint_enter | default({})) or {}).get('facts', {}).get('auto_import_strategy_changed', false)
```

`COLL/roles/primary_prep/tasks/main.yml` line 22 — same `argocd_run_id` rewrite as activation.

`COLL/roles/preflight/tasks/main.yml` — three task groups use the same four keys (`expected_managed_cluster_names`, `expected_managed_cluster_count`, `primary_has_observability`, `secondary_has_observability`): the "Validate required checkpoint data when preflight is skipped" assert, the "Restore operational facts from checkpoint when preflight is skipped" set_fact, and that set_fact's `when:` guards. Every occurrence of both chain shapes —

```
(((_checkpoint_enter.checkpoint | default({})).get('operational_data', {})).get('<key>', none))
((_checkpoint_enter.checkpoint | default({})).get('operational_data', {})).get('<key>')
```

becomes:

```yaml
((_checkpoint_enter.facts | default({})).get('<key>', none))
```

(keep the trailing `is not none` where present; drop the `, none` default only in the set_fact value expressions that had a bare `.get('<key>')`). The facade returns `None` for never-recorded values of these four keys, so the existing `is not none` guards keep their meaning.

`COLL/roles/finalization/tasks/main.yml` — the three restore tasks:

```yaml
    - name: Restore backup verification baseline from checkpoint
      ansible.builtin.set_fact:
        acm_switchover_backup_schedule_enabled_at: >-
          {{ (_checkpoint_enter.facts | default({})).get('backup_schedule_enabled_at') }}
      when:
        - acm_switchover_execution.checkpoint.enabled | default(false)
        - ((_checkpoint_enter.facts | default({})).get('backup_schedule_enabled_at', '')) | length > 0

    - name: Restore saved BackupSchedule from checkpoint
      ansible.builtin.set_fact:
        acm_switchover_saved_backup_schedule: >-
          {{ (_checkpoint_enter.facts | default({})).get('saved_backup_schedule') }}
      when:
        - acm_switchover_execution.checkpoint.enabled | default(false)
        - (_checkpoint_enter.facts | default({})).get('saved_backup_schedule') is mapping

    - name: Restore deferred auto-import reset flag from checkpoint
      ansible.builtin.set_fact:
        _auto_import_strategy_changed: >-
          {{ (_checkpoint_enter.facts | default({})).get('auto_import_strategy_changed', false) | bool }}
      when:
        - acm_switchover_execution.checkpoint.enabled | default(false)
        - ((_checkpoint_enter.facts | default({})).get('auto_import_strategy_changed', false) | bool)
```

Leave all write-side `operational_data:` task arguments untouched.

- [ ] **Step 4: Run guardrail + affected contract tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit -q`
Expected: PASS. YAML contract tests that assert the old chain text (grep `operational_data` under `COLL/tests/unit/` to find them — e.g. activation/preflight contract tests) must be updated to assert the new `facts` chains; keep their intent, change the expected text.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "refactor(ansible): roles read checkpoint facts via named vocabulary; add guardrail (#214)"
```

---

### Task 4: Marker annotation rides the ImportAndSync patch

**Files:**
- Modify: `COLL/roles/activation/tasks/manage_auto_import.yml` ("Set autoImportStrategy to ImportAndSync" task)
- Test: `COLL/tests/unit/test_activation_auto_import.py` (append)

**Interfaces:**
- Consumes: `AUTO_IMPORT_MARKER_ANNOTATION` / `AUTO_IMPORT_MARKER_VALUE` from Task 1 (test-side import; YAML carries the literal).
- Produces: marked ConfigMap on cluster — the obligation register Tasks 5 and 6 observe.

- [ ] **Step 1: Write the failing test**

Append to `COLL/tests/unit/test_activation_auto_import.py`:

```python
def test_import_and_sync_patch_carries_ownership_marker():
    """Issue #214 / audit C3: the obligation marker must ride the mutation itself,
    so an interruption can never separate the change from its evidence."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.constants import (
        AUTO_IMPORT_MARKER_ANNOTATION,
        AUTO_IMPORT_MARKER_VALUE,
    )

    tasks = yaml.safe_load((ACTIVATION_TASKS / "manage_auto_import.yml").read_text())
    patch_task = next(t for t in tasks if t.get("name") == "Set autoImportStrategy to ImportAndSync")
    definition = patch_task["kubernetes.core.k8s"]["definition"]
    annotations = definition["metadata"].get("annotations", {})
    assert annotations.get(AUTO_IMPORT_MARKER_ANNOTATION) == AUTO_IMPORT_MARKER_VALUE
    assert definition["data"]["autoImportStrategy"] == "ImportAndSync"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py -q -k ownership_marker`
Expected: FAIL — no annotations in definition.

- [ ] **Step 3: Add the annotation**

In `COLL/roles/activation/tasks/manage_auto_import.yml`, task "Set autoImportStrategy to ImportAndSync", extend the definition metadata:

```yaml
    definition:
      apiVersion: v1
      kind: ConfigMap
      metadata:
        name: import-controller-config
        namespace: multicluster-engine
        annotations:
          # Ownership marker: written atomically with the mutation so the reset
          # obligation survives any interruption (issue #214, audit C3). Must
          # match AUTO_IMPORT_MARKER_ANNOTATION/VALUE in module_utils/constants.py.
          acm-switchover.open-cluster-management.io/import-strategy-set-by: acm-switchover
      data:
        autoImportStrategy: ImportAndSync
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(ansible): mark ImportAndSync ConfigMap with ownership annotation (#214, audit C3)"
```

---

### Task 5: Finalization discharges by cluster observation

**Files:**
- Modify: `COLL/roles/finalization/tasks/reset_auto_import.yml`
- Modify: `COLL/roles/finalization/tasks/main.yml` (the `reset_auto_import.yml` include `when:`)
- Test: `COLL/tests/unit/test_activation_auto_import.py` (append — finalization contract tests already live here)

**Interfaces:**
- Consumes: marker annotation from Task 4; legacy `_auto_import_strategy_changed` fact (in-run or checkpoint-restored, Task 3).
- Produces: discharge gate `marker present OR legacy signal`, value must still be `ImportAndSync`.

- [ ] **Step 1: Write the failing tests**

Append to `COLL/tests/unit/test_activation_auto_import.py`:

```python
def _load_reset_tasks():
    return yaml.safe_load((FINALIZATION_TASKS / "reset_auto_import.yml").read_text())


def _when_text(task):
    when = task.get("when", "")
    if isinstance(when, list):
        return " ".join(str(w) for w in when)
    return str(when)


def test_reset_read_is_not_gated_on_legacy_flag():
    """The CM read must always run in execute mode: marker observation is the
    primary discharge signal and cannot depend on in-memory state (audit C3)."""
    tasks = _load_reset_tasks()
    read_task = next(t for t in tasks if t.get("name") == "Read import-controller-config before reset")
    when = _when_text(read_task)
    assert "_auto_import_strategy_changed" not in when
    assert "!= 'dry_run'" in when


def test_reset_delete_discharges_on_marker_or_legacy_signal():
    tasks = _load_reset_tasks()
    delete_task = next(
        t for t in tasks if t.get("name") == "Delete import-controller-config to restore default autoImportStrategy"
    )
    when = _when_text(delete_task)
    assert "_auto_import_marker_present" in when
    assert "_auto_import_strategy_changed" in when
    assert "ImportAndSync" in when


def test_finalization_always_includes_reset_auto_import():
    """The include must not be fenced by feature flag or legacy fact — the
    observation inside reset_auto_import.yml decides (audit C3 orphan discharge)."""
    main = yaml.safe_load((FINALIZATION_TASKS / "main.yml").read_text())

    def _find_include(tasks):
        for task in tasks or []:
            if task.get("ansible.builtin.include_tasks") == "reset_auto_import.yml":
                return task
            for key in ("block", "rescue", "always"):
                if key in task:
                    found = _find_include(task[key])
                    if found:
                        return found
        return None

    include_task = _find_include(main)
    assert include_task is not None
    assert "when" not in include_task, "reset_auto_import include must be unconditional; inner tasks gate on mode"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_auto_import.py -q -k "reset_read or discharges or always_includes"`
Expected: FAIL (3 tests).

- [ ] **Step 3: Implement**

Rewrite `COLL/roles/finalization/tasks/reset_auto_import.yml`:

```yaml
---
# Reset autoImportStrategy to the controller default after backup/MCH verification.
#
# The cluster is the collection's register (issue #214, audit C3): the ownership
# marker written by activation's ImportAndSync patch is the primary discharge
# signal, so an interrupted run's obligation survives regardless of checkpoint
# state. The legacy _auto_import_strategy_changed signal keeps discharging
# obligations created by pre-marker collection versions.
#
# Mirrors the Python CLI: modules/finalization.py deletes
# multicluster-engine/import-controller-config after backup enable/verify,
# backup integrity, and MCH health checks complete.
- name: Read import-controller-config before reset
  kubernetes.core.k8s_info:
    api_version: v1
    kind: ConfigMap
    name: import-controller-config
    namespace: multicluster-engine
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context | default(omit) }}"
  register: _auto_import_reset_cm
  when:
    - acm_switchover_execution.mode | default('dry_run') != 'dry_run'

- name: Determine auto-import discharge obligation from cluster marker
  ansible.builtin.set_fact:
    _auto_import_marker_present: >-
      {{
        (
          (_auto_import_reset_cm.resources | default([]) | first | default({}))
          .get('metadata', {}).get('annotations', {})
          .get('acm-switchover.open-cluster-management.io/import-strategy-set-by', '')
        ) == 'acm-switchover'
      }}
  when:
    - acm_switchover_execution.mode | default('dry_run') != 'dry_run'

- name: Delete import-controller-config to restore default autoImportStrategy
  kubernetes.core.k8s:
    api_version: v1
    kind: ConfigMap
    name: import-controller-config
    namespace: multicluster-engine
    kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
    context: "{{ acm_switchover_hubs.secondary.context | default(omit) }}"
    state: absent
  register: acm_switchover_reset_auto_import_result
  when:
    - acm_switchover_execution.mode | default('dry_run') != 'dry_run'
    - >-
      (_auto_import_marker_present | default(false) | bool)
      or (_auto_import_strategy_changed | default(false) | bool)
    - >-
      (
        _auto_import_reset_cm.resources
        | default([])
        | first
        | default({})
      ).get('data', {}).get('autoImportStrategy', 'default') == 'ImportAndSync'
```

In `COLL/roles/finalization/tasks/main.yml`, remove the `when:` from the include:

```yaml
    - name: Reset auto-import strategy to default after backup and MCH verification
      ansible.builtin.include_tasks: reset_auto_import.yml
```

Note: the read task has no `failed_when` override — a read failure fails the task. That is deliberate (spec §5): finalization must not silently skip a discharge it cannot verify.

- [ ] **Step 4: Run tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit -q`
Expected: PASS (update any finalization contract test that asserted the old include gate text — keep intent, update expectation).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(ansible): discharge auto-import reset from cluster marker observation (#214, audit C3)"
```

---

### Task 6: Preflight orphan warning (both hubs)

**Files:**
- Create: `COLL/roles/preflight/tasks/check_auto_import_orphan.yml`
- Modify: `COLL/roles/preflight/tasks/main.yml` (include next to the GitOps drift include, inside the "Run preflight phase" block, before `discover_resources.yml`)
- Test: `COLL/tests/unit/test_preflight_auto_import_orphan.py` (create)

**Interfaces:**
- Consumes: marker annotation literal (Task 4); `acm_switchover_validation_results` list convention (`id`/`severity`/`status`/`message`/`details`/`recommended_action` — see `validate_gitops.yml`).
- Produces: warning entries with id `preflight-auto-import-orphan` (severity `warning`, status `fail`) for each hub carrying a marked `ImportAndSync` ConfigMap; id `preflight-auto-import-orphan-check` (severity `warning`, status `error`) when a hub's ConfigMap is unreadable. Non-blocking either way.

- [ ] **Step 1: Write the failing tests**

Create `COLL/tests/unit/test_preflight_auto_import_orphan.py`:

```python
"""Contract tests for the preflight auto-import orphan warning (issue #214, audit C3)."""

import pathlib

import yaml

PREFLIGHT_TASKS = pathlib.Path(__file__).resolve().parents[2] / "roles" / "preflight" / "tasks"
ORPHAN_FILE = PREFLIGHT_TASKS / "check_auto_import_orphan.yml"


def _flatten(tasks):
    result = []
    for task in tasks or []:
        result.append(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                result.extend(_flatten(task[key]))
    return result


def test_orphan_check_file_exists():
    assert ORPHAN_FILE.exists()


def test_orphan_check_reads_both_hubs_non_fatally():
    tasks = yaml.safe_load(ORPHAN_FILE.read_text())
    read_task = next(t for t in _flatten(tasks) if "kubernetes.core.k8s_info" in t)
    assert read_task.get("failed_when") is False, "read failure must degrade to a warning, not block preflight"
    loop_text = str(read_task.get("loop", ""))
    assert "primary" in loop_text and "secondary" in loop_text


def test_orphan_warning_uses_marker_and_import_and_sync():
    text = ORPHAN_FILE.read_text()
    assert "acm-switchover.open-cluster-management.io/import-strategy-set-by" in text
    assert "ImportAndSync" in text
    assert "preflight-auto-import-orphan" in text
    assert "acm_switchover_validation_results" in text


def test_preflight_main_includes_orphan_check_before_discovery():
    main = yaml.safe_load((PREFLIGHT_TASKS / "main.yml").read_text())
    includes = [t.get("ansible.builtin.include_tasks", "") for t in _flatten(main)]
    assert "check_auto_import_orphan.yml" in includes
    assert includes.index("check_auto_import_orphan.yml") < includes.index("discover_resources.yml")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_auto_import_orphan.py -q`
Expected: FAIL — file missing.

- [ ] **Step 3: Implement**

Create `COLL/roles/preflight/tasks/check_auto_import_orphan.yml`:

```yaml
---
# Audit C3 (issue #214): an interrupted prior run may have left
# autoImportStrategy=ImportAndSync carrying the collection's ownership marker.
# Warn — never block: activation may legitimately re-enable ImportAndSync this
# run, and finalization is the discharge point.
- name: Read import-controller-config for orphaned auto-import obligation
  kubernetes.core.k8s_info:
    api_version: v1
    kind: ConfigMap
    name: import-controller-config
    namespace: multicluster-engine
    kubeconfig: "{{ item.kubeconfig }}"
    context: "{{ item.context | default(omit) }}"
  register: _auto_import_orphan_reads
  failed_when: false
  loop:
    - hub: primary
      kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
      context: "{{ acm_switchover_hubs.primary.context | default('') }}"
    - hub: secondary
      kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
      context: "{{ acm_switchover_hubs.secondary.context | default('') }}"
  loop_control:
    label: "{{ item.hub }}"
  when: acm_switchover_execution.mode | default('dry_run') != 'dry_run'

- name: Record orphaned auto-import obligation warnings
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results + [
          {
            "id": "preflight-auto-import-orphan",
            "severity": "warning",
            "status": "fail",
            "message": "Hub '" ~ item.item.hub ~ "' has autoImportStrategy=ImportAndSync set by a previous acm-switchover run (unresolved reset obligation)",
            "details": {"hub": item.item.hub},
            "recommended_action": "Finalization of this run will reset it; if not running finalization, delete multicluster-engine/import-controller-config manually"
          }
        ]
      }}
  loop: "{{ _auto_import_orphan_reads.results | default([]) }}"
  loop_control:
    label: "{{ item.item.hub | default('unknown') }}"
  when:
    - acm_switchover_execution.mode | default('dry_run') != 'dry_run'
    - not (item.failed | default(false))
    - >-
      (
        (item.resources | default([]) | first | default({}))
        .get('metadata', {}).get('annotations', {})
        .get('acm-switchover.open-cluster-management.io/import-strategy-set-by', '')
      ) == 'acm-switchover'
    - >-
      (item.resources | default([]) | first | default({}))
      .get('data', {}).get('autoImportStrategy', 'default') == 'ImportAndSync'

- name: Record auto-import orphan check read failures
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results + [
          {
            "id": "preflight-auto-import-orphan-check",
            "severity": "warning",
            "status": "error",
            "message": "Could not read import-controller-config on hub '" ~ item.item.hub ~ "' to check for an orphaned auto-import obligation",
            "details": {"hub": item.item.hub},
            "recommended_action": "Verify hub connectivity and RBAC for reading ConfigMaps in multicluster-engine"
          }
        ]
      }}
  loop: "{{ _auto_import_orphan_reads.results | default([]) }}"
  loop_control:
    label: "{{ item.item.hub | default('unknown') }}"
  when:
    - acm_switchover_execution.mode | default('dry_run') != 'dry_run'
    - item.failed | default(false)
```

In `COLL/roles/preflight/tasks/main.yml`, inside the "Run preflight phase" block, immediately after the GitOps drift include:

```yaml
    - name: Check for orphaned auto-import obligation from a prior run
      ansible.builtin.include_tasks: check_auto_import_orphan.yml
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit -q`
Expected: PASS (if a preflight contract test pins the exact include sequence, update it to expect the new include).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(ansible): preflight warns on orphaned auto-import obligation (#214, audit C3)"
```

---

### Task 7: Cross-runtime parity fixture for shared key names

**Files:**
- Create: `tests/test_checkpoint_state_parity.py` (repo root tests/)

**Interfaces:**
- Consumes: `KEY_*` constants from Task 1; Python `lib/constants.py` (`STATE_KEY_RESUME_SUMMARY`, `RESUME_START_PHASE_KEY`, `EXPECTED_MANAGED_CLUSTER_NAMES_KEY`, `EXPECTED_MANAGED_CLUSTER_COUNT_KEY`) and `lib/run_record.py` private `_KEY_*` literals (importable from tests; the RunRecord guardrail restricts production code, not tests — verify with `python -m pytest tests/test_run_record_guardrails.py -q` after adding, and if the guardrail flags test imports, compare against string literals instead and note the mapping inline).

- [ ] **Step 1: Write the failing test**

Create `tests/test_checkpoint_state_parity.py`:

```python
"""Parity contract: cross-phase state key names shared between runtimes (issue #214).

The Python CLI persists cross-phase facts through the RunRecord facade
(lib/run_record.py); the collection persists them in checkpoint
operational_data (module_utils/checkpoint.py). Shared names are pinned equal;
intentional divergences are pinned explicitly so silent drift is impossible.
"""

import lib.constants as py_constants
import lib.run_record as py_run_record
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import checkpoint as ansible_checkpoint


def test_shared_key_names_match():
    assert ansible_checkpoint.KEY_RESUME_SUMMARY == py_constants.STATE_KEY_RESUME_SUMMARY
    assert ansible_checkpoint.KEY_RESUME_START_PHASE == py_constants.RESUME_START_PHASE_KEY
    assert ansible_checkpoint.KEY_EXPECTED_MANAGED_CLUSTER_NAMES == py_constants.EXPECTED_MANAGED_CLUSTER_NAMES_KEY
    assert ansible_checkpoint.KEY_EXPECTED_MANAGED_CLUSTER_COUNT == py_constants.EXPECTED_MANAGED_CLUSTER_COUNT_KEY
    assert ansible_checkpoint.KEY_PRIMARY_HAS_OBSERVABILITY == py_run_record._KEY_PRIMARY_HAS_OBS
    assert ansible_checkpoint.KEY_SECONDARY_HAS_OBSERVABILITY == py_run_record._KEY_SECONDARY_HAS_OBS
    assert ansible_checkpoint.KEY_SAVED_BACKUP_SCHEDULE == py_run_record._KEY_SAVED_BACKUP_SCHEDULE
    assert ansible_checkpoint.KEY_BACKUP_SCHEDULE_ENABLED_AT == py_run_record._KEY_BACKUP_WATCH_STARTED_AT


def test_intentional_divergences_are_pinned():
    """auto-import obligation: Python records auto_import_strategy_set (state
    file, always on); the collection records auto_import_strategy_changed
    (checkpoint) plus the cluster marker. Renaming either side without
    updating this contract is a parity break."""
    assert py_run_record._KEY_AUTO_IMPORT_SET == "auto_import_strategy_set"
    assert ansible_checkpoint.KEY_AUTO_IMPORT_STRATEGY_CHANGED == "auto_import_strategy_changed"
```

- [ ] **Step 2: Run test — expect PASS immediately** (constants exist from Task 1; this is a pin, not new behavior). If it fails, a constant name is wrong — fix the test or Task 1, not both.

Run: `python -m pytest tests/test_checkpoint_state_parity.py -q`
Expected: PASS.

Also run: `python -m pytest tests/test_run_record_guardrails.py -q`
Expected: PASS (confirms the guardrail tolerates the test-side `_KEY_*` imports; if not, switch the assertions to string literals as noted above).

- [ ] **Step 3: Commit**

```bash
git add tests/test_checkpoint_state_parity.py
git commit -m "test: pin cross-runtime checkpoint/state key-name parity (#214)"
```

---

### Task 8: Documentation, defaults comments, full verification

**Files:**
- Modify: `COLL/docs/coexistence.md`
- Modify: `COLL/roles/{preflight,primary_prep,activation,post_activation,finalization}/defaults/main.yml` (comment above `checkpoint:`)

**Interfaces:**
- Consumes: everything above. No code changes.

- [ ] **Step 1: Document the decisions in coexistence.md**

Add a section after the existing checkpoint/identity material:

```markdown
## Auto-import reset obligation (issue #214, audit C3/C4)

**The cluster is the collection's register** for the auto-import reset
obligation, exactly as it is for the Argo CD pause register: activation's
`ImportAndSync` patch writes the ownership annotation
`acm-switchover.open-cluster-management.io/import-strategy-set-by: acm-switchover`
in the same API call as the mutation. Finalization discharges by observation —
it deletes `multicluster-engine/import-controller-config` when the marker is
present (or the legacy `auto_import_strategy_changed` signal fires) and the
strategy is still `ImportAndSync`. Preflight reports a non-blocking warning
(`preflight-auto-import-orphan`) when either hub carries a marked
`ImportAndSync` ConfigMap left by an interrupted run.

Equivalence with the Python CLI: Python's state file is always on, so its
`auto_import_strategy_set` obligation survives interruption by construction.
The collection reaches the same invariant — an obligation is discharged only
when the reset is proven — via the cluster marker, without requiring
checkpointing to be enabled.

**Default posture (audit C4):** `checkpoint.enabled` remains `false` by
default. Without checkpointing the collection has no resume and no
hub-identity binding for resumed runs; enabling it is the operator's opt-in
for resumable executions. The safety obligations that must survive
interruption (Argo CD pause register, auto-import reset) live on the cluster
and do not depend on this setting.

**resume_summary:** both runtimes now use replace semantics — each resumed
process records the phase it started at (`resume_start_phase`), last resume
wins. Shared key names are pinned by `tests/test_checkpoint_state_parity.py`.
```

- [ ] **Step 2: Add the defaults comment**

Above the `checkpoint:` block in each of the five role defaults files:

```yaml
  # Opt-in resume: enabling checkpointing adds resumable execution with
  # hub-identity binding. Safety obligations that must survive interruption
  # (Argo CD pause register, auto-import reset marker) live on the cluster
  # and do NOT depend on this setting — see docs/coexistence.md.
```

- [ ] **Step 3: Full verification**

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit -q
python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario -q
python -m pytest tests/ -q
black --line-length 120 --check ansible_collections/tomazb/acm_switchover/plugins lib tests
```

Expected: all PASS, black clean.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs(ansible): record cluster-as-register auto-import decision and opt-in resume posture (#214)"
```

- [ ] **Step 5: Push branch and open draft PR**

```bash
git push -u origin issue-214-checkpoint-convergence
gh pr create --draft --base ansible --title "Converge collection checkpoint state on named operations; cluster-as-register auto-import obligation (#214)" --body "..."
```

PR body: summarize the four decisions (cluster marker for C3, opt-in posture documented for C4, facade + facts vocabulary, resume_summary replace), link issue #214 and the spec/plan docs, list the audit findings addressed. No AI-attribution footers or trailers (repo convention).
