# Audit H1/H10 Resume-Safety Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Provenance:** This plan was originally drafted against the parity-audit
> spec, mirroring the (since-closed) draft PR #220 (branch `fix/audit-h1-h10`,
> commit `3d6383cd`); it was executed and hardened in this PR (#222). It is
> written so the change can be reviewed against — or re-executed from — the
> parity-audit spec (`docs/ansible-collection/parity-audit-2026-08-03.md`,
> findings H1 and H10).

**Goal:** Close two Python-side safety bugs from the 2026-08-03 parity audit: a resumed run silently disabling all managed-cluster enforcement when the state file carries no expectation record (H1), and `--dry-run` destructively flushing a context-mismatch reset over a real in-progress state file (H10).

**Architecture:** H1 becomes a fail-closed `SwitchoverError` raised inside `_resolve_managed_cluster_expectation` when neither an explicit `--min-managed-clusters` nor a recorded expectation exists — matching the collection's posture, which asserts the checkpoint carries the expectation (`roles/preflight/tasks/main.yml:26-51`). H10 is fixed by snapshotting the state file *before* `ensure_contexts` can flush its reset (a new `dry_run_state_guard` field on `RuntimeContext`, captured in `_prepare_runtime` only under `--dry-run` for state-bound operations) and restoring that snapshot in a `finally` block in `main()` after the rehearsal. Real runs keep the reset unchanged; unbound operations (`--argocd-resume-only`, `--decommission`) take no guard.

**Tech Stack:** Python 3, pytest (unit marker), `StateManager` snapshot APIs (`lib/utils.py:506,510`), `RunRecord` expectation record (`lib/run_record.py:209`).

## Global Constraints

- Base branch: `ansible` (never `main`) — parity via `coexistence.md`.
- Formatting: `black --line-length 120 <files>` (CI enforces 120; repo has no black config, default 88 is wrong).
- Imports: `isort` clean; `flake8` findings must be byte-identical to base.
- Test command: `python -m pytest tests/ -q` — full suite must pass (base: 3165 passed, 29 skipped; after this plan's tasks: 3172 passed; final shipped result including the review-driven hardening tests added during execution: 3178 passed).
- Test-first: both defects must be reproduced as failing tests before the fixes land.
- Behaviour-preserving everywhere else: the three prior expectation paths (recorded, restore-only, explicit `--min-managed-clusters`) are pinned unchanged by tests.
- Commit messages: conventional-commit style, no Co-Authored-By / AI-attribution trailers.

## File Structure

- `tests/test_resume_safety_guards.py` — **new**; one file for both audit guards (H1 + H10), module docstring names the audit findings.
- `acm_switchover.py` — modify `_resolve_managed_cluster_expectation` (H1), `_prepare_runtime` and `main()` (H10); import `SwitchoverError`.
- `lib/runtime_bootstrap.py` — `RuntimeContext` gains `dry_run_state_guard: Optional[dict] = None`.
- `tests/test_cli_auto_import.py` — one existing test's partial `Namespace` now needs an explicit `min_managed_clusters` (its old setup relied on the silently-disabled path this plan removes).
- `tests/test_main.py` — two hand-built `RuntimeContext(...)` stubs gain the new field explicitly.

---

### Task 1: H1 — fail closed on missing managed-cluster expectation

**Files:**
- Create: `tests/test_resume_safety_guards.py`
- Modify: `acm_switchover.py:54` (import), `acm_switchover.py:870-873` (resolver)
- Modify: `tests/test_cli_auto_import.py:88-100`

**Interfaces:**
- Consumes: `RunRecord(state).managed_cluster_expectation()` → object with `.mode` (`None` when no record), `.names`, `.count`; `RunRecord(state).record_managed_cluster_expectation(names, count: int, mode: str)`; `SwitchoverError` from `lib.exceptions`; `MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY == "restore_only"` (`lib/constants.py:188`).
- Produces: `_resolve_managed_cluster_expectation(args, state)` now raises `SwitchoverError` when `args.min_managed_clusters is None` **and** `expectation.mode is None`; the error message contains both `--min-managed-clusters` and `preflight`. All other paths keep the existing `tuple[int, list[str], bool]` returns.

- [ ] **Step 1: Write the failing test file (H1 class + shared `_args` helper)**

Create `tests/test_resume_safety_guards.py`:

```python
"""Guards from the 2026-08-03 parity audit (findings H1 and H10).

H1: a resumed run whose state carries no managed-cluster expectation must
fail closed instead of silently disabling all enforcement.
H10: --dry-run must never destroy a real in-progress state file, even when
the invocation's contexts differ from the file's (ensure_contexts reset).
"""

import argparse

import pytest

from acm_switchover import _resolve_managed_cluster_expectation
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.utils import StateManager

pytestmark = pytest.mark.unit


def _args(**overrides):
    defaults = {
        "min_managed_clusters": None,
        "dry_run": False,
        "argocd_resume_only": False,
        "decommission": False,
        "primary_context": "hub-a",
        "secondary_context": "hub-b",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestExpectationFailsClosed:
    def test_missing_expectation_record_raises(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))

        with pytest.raises(SwitchoverError) as exc_info:
            _resolve_managed_cluster_expectation(_args(), state)

        message = str(exc_info.value)
        assert "--min-managed-clusters" in message
        assert "preflight" in message

    def test_recorded_expectation_still_resolves(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))
        RunRecord(state).record_managed_cluster_expectation(names=["c1", "c2"], count=2, mode="derived_from_preflight")

        assert _resolve_managed_cluster_expectation(_args(), state) == (2, ["c1", "c2"], True)

    def test_restore_only_mode_still_resolves(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))
        RunRecord(state).record_managed_cluster_expectation(names=[], count=0, mode="restore_only")

        assert _resolve_managed_cluster_expectation(_args(), state) == (1, [], False)

    def test_explicit_min_overrides_missing_record(self, tmp_path):
        state = StateManager(str(tmp_path / "switchover-x.json"))

        assert _resolve_managed_cluster_expectation(_args(min_managed_clusters=3), state) == (3, [], False)
        assert _resolve_managed_cluster_expectation(_args(min_managed_clusters=0), state) == (0, [], False)
```

Note: three of the four tests pin *existing* behaviour (recorded, restore-only, explicit-min paths). Only the first reproduces the H1 defect.

- [ ] **Step 2: Run the class to verify the reproduction fails**

Run: `python -m pytest tests/test_resume_safety_guards.py -v`
Expected: `test_missing_expectation_record_raises` **FAILS** (`DID NOT RAISE SwitchoverError` — current code returns `(0, [], False)`); the other three **PASS**.

- [ ] **Step 3: Implement the fail-closed raise**

In `acm_switchover.py`, extend the exceptions import (line 54):

```python
from lib.exceptions import StateLoadError, StateLockError, SwitchoverError
```

In `_resolve_managed_cluster_expectation` (currently `acm_switchover.py:860`), insert the mode-is-None check at the top of the `raw_min is None` branch, *before* the restore-only special case:

```python
    if raw_min is None:
        if expectation.mode is None:
            raise SwitchoverError(
                "No managed-cluster expectation is recorded in the state file (preflight has not "
                "run in this state, or the state predates expectation recording). Refusing to skip "
                "managed-cluster verification on a resumed run: pass --min-managed-clusters "
                "explicitly, or re-run preflight to record the expectation."
            )
        if expectation.mode == MANAGED_CLUSTER_EXPECTATION_RESTORE_ONLY and expected_count == 0 and not expected_names:
            return 1, [], False
        return expected_count, expected_names, bool(expected_names)
```

Ordering matters: an explicit `--min-managed-clusters` (raw_min not None) must still bypass the raise entirely — that is the operator's escape hatch the error message advertises.

- [ ] **Step 4: Run the class to verify all four pass**

Run: `python -m pytest tests/test_resume_safety_guards.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Repair the one test that leaned on the removed silent path**

`tests/test_cli_auto_import.py::test_getattr_fallback_defaults_to_false` builds a partial `Namespace` without `min_managed_clusters`; with no recorded expectation in its mocked state, the resolver now fails closed. Give it an explicit value — its subject is the `manage_auto_import_strategy` getattr fallback, not expectation resolution:

```python
        # Namespace WITHOUT manage_auto_import_strategy to exercise getattr fallback.
        # min_managed_clusters is set explicitly: with no recorded expectation in
        # state, the resolver now fails closed instead of disabling enforcement.
        args = Namespace(
            method="full",
            dry_run=False,
            old_hub_action="none",
            restore_only=False,
            min_managed_clusters=1,
        )
```

- [ ] **Step 6: Run the touched suites**

Run: `python -m pytest tests/test_resume_safety_guards.py tests/test_cli_auto_import.py -q`
Expected: all PASS.

- [ ] **Step 7: Format and commit**

```bash
black --line-length 120 acm_switchover.py tests/test_resume_safety_guards.py tests/test_cli_auto_import.py
isort acm_switchover.py tests/test_resume_safety_guards.py tests/test_cli_auto_import.py
git add acm_switchover.py tests/test_resume_safety_guards.py tests/test_cli_auto_import.py
git commit -m "fix: fail closed on missing managed-cluster expectation (audit H1)"
```

---

### Task 2: H10 — dry-run must never destroy a real in-progress state file

**Files:**
- Modify: `lib/runtime_bootstrap.py:19-26` (`RuntimeContext` dataclass)
- Modify: `acm_switchover.py:1099-1131` (`_prepare_runtime`), `acm_switchover.py:1187-1215` (`main()`)
- Modify: `tests/test_resume_safety_guards.py` (add `TestDryRunStateGuard`)
- Modify: `tests/test_main.py:1781`, `tests/test_main.py:3305` (hand-built `RuntimeContext` stubs)

**Interfaces:**
- Consumes: `StateManager.capture_state_snapshot() -> Dict[str, Any]` and `StateManager.restore_state_snapshot(snapshot: Dict[str, Any]) -> None` (`lib/utils.py:506,510` — already on base, no changes needed); `StateManager.ensure_contexts(primary, secondary)` (flushes a reset to disk on context mismatch — the H10 hazard); `_initialize_clients` (patched in tests).
- Produces: `RuntimeContext.dry_run_state_guard: Optional[dict] = None` — non-None exactly when `args.dry_run` and the operation binds state; `main()` restores it in a `finally` after `run_operation_mode`, so the rehearsal's on-disk effects (including the `ensure_contexts` reset) are always rolled back, even on failure.

- [ ] **Step 1: Write the failing tests**

Extend the imports at the top of `tests/test_resume_safety_guards.py`:

```python
import argparse
import json
import logging
from unittest.mock import patch

import pytest

from acm_switchover import _prepare_runtime, _resolve_managed_cluster_expectation
from lib.exceptions import SwitchoverError
from lib.run_record import RunRecord
from lib.utils import StateManager
```

Append the class:

```python
class TestDryRunStateGuard:
    def _progressed_state_file(self, tmp_path):
        path = tmp_path / "switchover-guard.json"
        state = StateManager(str(path))
        state.ensure_contexts("old-primary", "old-secondary")
        state.mark_step_completed("preflight_validation")
        state.flush_state()
        return path

    def test_dry_run_guard_predates_context_reset(self, tmp_path, caplog):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=True, primary_context="new-primary", secondary_context="new-secondary")

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.dry_run_state_guard is not None
        # The guard captured the state BEFORE ensure_contexts reset it.
        assert ctx.dry_run_state_guard["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in ctx.dry_run_state_guard["completed_steps"])

        # Restoring the guard brings the on-disk file back to the original run.
        ctx.state.restore_state_snapshot(ctx.dry_run_state_guard)
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "old-primary", "secondary": "old-secondary"}
        assert any(step.get("name") == "preflight_validation" for step in on_disk["completed_steps"])

    def test_real_run_has_no_guard_and_keeps_reset(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=False, primary_context="new-primary", secondary_context="new-secondary")

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.dry_run_state_guard is None
        on_disk = json.loads(path.read_text())
        assert on_disk["contexts"] == {"primary": "new-primary", "secondary": "new-secondary"}

    def test_dry_run_guard_absent_for_unbound_operations(self, tmp_path):
        path = self._progressed_state_file(tmp_path)
        args = _args(dry_run=True, argocd_resume_only=True)

        with patch("acm_switchover._initialize_clients", return_value=(None, None)):
            ctx = _prepare_runtime(args, logging.getLogger("test"), str(path))

        assert ctx.dry_run_state_guard is None
```

- [ ] **Step 2: Run the class to verify the reproduction fails**

Run: `python -m pytest tests/test_resume_safety_guards.py::TestDryRunStateGuard -v`
Expected: `test_dry_run_guard_predates_context_reset` **FAILS** (`AttributeError: 'RuntimeContext' object has no attribute 'dry_run_state_guard'`); the other two fail the same way at their first guard assertion.

- [ ] **Step 3: Add the guard field to `RuntimeContext`**

In `lib/runtime_bootstrap.py`, append to the dataclass (after `should_record_state_errors: bool`):

```python
    # Snapshot taken BEFORE ensure_contexts under --dry-run, so a rehearsal can
    # never destroy a real in-progress state file via the context-mismatch
    # reset (parity audit finding H10). None outside dry-run.
    dry_run_state_guard: Optional[dict] = None
```

- [ ] **Step 4: Capture the guard in `_prepare_runtime`**

In `acm_switchover.py:_prepare_runtime`, the current code is:

```python
    if should_bind_state:
        state.ensure_contexts(getattr(args, "primary_context", None), getattr(args, "secondary_context", None))
```

Replace with:

```python
    dry_run_state_guard = None
    if should_bind_state:
        if getattr(args, "dry_run", False):
            # Capture BEFORE ensure_contexts: its context-mismatch reset flushes
            # to disk, and a later snapshot would only preserve the wiped state
            # (parity audit finding H10). main() restores this guard after the
            # rehearsal completes.
            dry_run_state_guard = state.capture_state_snapshot()
        state.ensure_contexts(getattr(args, "primary_context", None), getattr(args, "secondary_context", None))
```

and thread it into the returned context:

```python
    return RuntimeContext(
        state=state,
        primary=primary,
        secondary=secondary,
        should_bind_state=should_bind_state,
        should_record_state_errors=should_record_state_errors,
        dry_run_state_guard=dry_run_state_guard,
    )
```

- [ ] **Step 5: Restore the guard in `main()` after the rehearsal**

In `main()`, wrap the existing `run_operation_mode` call in `try`/`finally` (the call itself is unchanged — only indented):

```python
    try:
        operation_exit_code = cli_outcomes.run_operation_mode(
            args,
            state,
            runtime.primary,
            runtime.secondary,
            logger,
            should_bind_state=runtime.should_bind_state,
            should_record_state_errors=runtime.should_record_state_errors,
            hooks=_build_cli_operation_hooks(),
            exit_success=EXIT_SUCCESS,
            exit_failure=EXIT_FAILURE,
            exit_interrupt=EXIT_INTERRUPT,
        )
    finally:
        if runtime.dry_run_state_guard is not None:
            # H10 guard: put the state file back exactly as it was before the
            # dry-run rehearsal, including a context-mismatch reset that
            # ensure_contexts may have flushed in _prepare_runtime.
            state.restore_state_snapshot(runtime.dry_run_state_guard)
    sys.exit(operation_exit_code)
```

`finally` (not a success-path restore) is deliberate: a rehearsal that raises must still roll back the flushed reset.

- [ ] **Step 6: Run the class to verify all three pass**

Run: `python -m pytest tests/test_resume_safety_guards.py -v`
Expected: 7 PASS (4 from Task 1 + 3 new).

- [ ] **Step 7: Pin the grown contract in the hand-built `test_main.py` stubs**

Two tests construct `RuntimeContext(...)` by hand (`tests/test_main.py:1781` and `:3305`). The new field has a default, so they still run — add it explicitly anyway to pin the grown contract:

```python
            dry_run_state_guard=None,
```

(added as the last keyword in both constructor calls).

- [ ] **Step 8: Run the touched suites**

Run: `python -m pytest tests/test_resume_safety_guards.py tests/test_main.py -q`
Expected: all PASS.

- [ ] **Step 9: Format and commit**

```bash
black --line-length 120 acm_switchover.py lib/runtime_bootstrap.py tests/test_resume_safety_guards.py tests/test_main.py
isort acm_switchover.py lib/runtime_bootstrap.py tests/test_resume_safety_guards.py tests/test_main.py
git add acm_switchover.py lib/runtime_bootstrap.py tests/test_resume_safety_guards.py tests/test_main.py
git commit -m "fix: guard dry-run against destroying real state file (audit H10)"
```

---

### Task 3: Full-suite regression and lint gates

**Files:**
- No new files; verification only.

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces: green full suite and byte-identical lint baseline — the PR-readiness evidence quoted in the PR body.

- [ ] **Step 1: Full test suite**

Run: `python -m pytest tests/ -q`
Expected: **3172 passed, 29 skipped** (base was 3165 passed; +7 new tests, no regressions). Final shipped result after the review-driven hardening tests added during execution: **3178 passed, 29 skipped**.

- [ ] **Step 2: Formatting gates**

Run (scoped to the files touched by this plan — see File Structure — to avoid traversing venvs/generated dirs):
`black --line-length 120 --check acm_switchover.py lib/runtime_bootstrap.py tests/test_resume_safety_guards.py tests/test_cli_auto_import.py tests/test_main.py && isort --check-only acm_switchover.py lib/runtime_bootstrap.py tests/test_resume_safety_guards.py tests/test_cli_auto_import.py tests/test_main.py`
Expected: clean (no diffs).

- [ ] **Step 3: flake8 baseline comparison**

```bash
flake8 > /tmp/flake8-head.txt; git stash --include-untracked && flake8 > /tmp/flake8-base.txt; git stash pop
diff /tmp/flake8-base.txt /tmp/flake8-head.txt
```

Expected: empty diff — findings byte-identical to base.

- [ ] **Step 4: Push and open PR against `ansible`**

```bash
git push -u origin fix/audit-h1-h10
gh pr create --base ansible --title "fix: fail closed on missing managed-cluster expectation; guard dry-run state (audit H1/H10)" --draft
```

PR body: summarize H1 and H10 as in the audit (§2 rows H1/H10), state the test-first evidence and the two contract-growth test updates, quote the full-suite counts.

---

## Self-Review Notes

- **Spec coverage:** H1 (audit table row `acm_switchover.py:876-881`) → Task 1; H10 (row `lib/utils.py:696-713` via `acm_switchover.py:1103`) → Task 2. The audit's other Python-side findings (H3, C-class, M-class) are out of scope here by design — this plan is the "two Python-side safety bugs independent of parity" slice the audit itself calls out (§ recommendation 3).
- **Type consistency:** `dry_run_state_guard` is `Optional[dict]` in the dataclass, produced by `capture_state_snapshot() -> Dict[str, Any]`, consumed by `restore_state_snapshot(snapshot)` — names match across Tasks 2's steps and both test classes.
- **Pinned-unchanged paths:** recorded expectation, restore-only sentinel, explicit `--min-managed-clusters` (including `0` = explicit disable) each have a dedicated test in Task 1; real-run reset retention and unbound-operation exemption each have one in Task 2.
