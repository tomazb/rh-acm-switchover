# PR 35: Canonical Phase-Name Mapping Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the byte-identical `lib/utils.py` `REPORT_PHASE_NAMES` and `lib/workflow.py` `_CANONICAL_RESUME_START_PHASES` dicts into one `CANONICAL_PHASE_NAMES` mapping in `lib/utils.py`, used by all three consumers.

**Architecture:** Pure rename-and-delete refactor per the approved design (`docs/superpowers/specs/2026-07-02-pr35-phase-name-dedup-design.md`). The mapping stays next to the `Phase` enum in `lib/utils.py`; `workflow.py` and `cli_outcomes.py` already import from `lib.utils`, so no new import edges. No value changes, no compatibility alias (zero external references).

**Tech Stack:** Python 3, pytest, black/isort (line-length 120).

## Global Constraints

- `black --line-length 120` and `isort --profile black --line-length 120` on touched files.
- No behavior change; mapping contents identical before and after.
- Base branch: `ansible`; PR branch `refactor/thermos-35-phase-name-dedup`.

---

### Task 1: Red-first canonical-mapping test

**Files:**
- Create: `tests/test_phase_name_canonical.py`

**Interfaces:**
- Produces: tests that Task 2 turns green; relies on Task 2 exporting `CANONICAL_PHASE_NAMES` from `lib.utils`.

- [ ] **Step 1: Write the failing tests**

```python
"""Guardrails for the single canonical Phase -> report/resume name mapping (Thermos R2-M4)."""

import lib.cli_outcomes as cli_outcomes
import lib.utils as utils
import lib.workflow as workflow
from lib.utils import CANONICAL_PHASE_NAMES, Phase


def test_canonical_phase_names_cover_exactly_the_executable_phases():
    """INIT/COMPLETED/FAILED are lifecycle markers, not executable phases, and stay unmapped."""
    assert set(CANONICAL_PHASE_NAMES) == {
        Phase.PREFLIGHT,
        Phase.PRIMARY_PREP,
        Phase.SECONDARY_VERIFY,
        Phase.ACTIVATION,
        Phase.POST_ACTIVATION,
        Phase.FINALIZATION,
    }


def test_legacy_secondary_verify_folds_into_activation():
    assert CANONICAL_PHASE_NAMES[Phase.SECONDARY_VERIFY] == "activation"
    assert CANONICAL_PHASE_NAMES[Phase.ACTIVATION] == "activation"


def test_single_mapping_object_shared_by_all_consumers():
    """workflow and cli_outcomes must use the exact same dict object as lib.utils."""
    assert workflow.CANONICAL_PHASE_NAMES is CANONICAL_PHASE_NAMES
    assert cli_outcomes.CANONICAL_PHASE_NAMES is CANONICAL_PHASE_NAMES


def test_old_duplicate_names_are_gone():
    assert not hasattr(utils, "REPORT_PHASE_NAMES")
    assert not hasattr(workflow, "_CANONICAL_RESUME_START_PHASES")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_phase_name_canonical.py -q`
Expected: FAIL at import time — `ImportError: cannot import name 'CANONICAL_PHASE_NAMES' from 'lib.utils'`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_phase_name_canonical.py
git commit -m "test: add red canonical phase-name mapping guardrails"
```

### Task 2: Rename in lib/utils.py, delete workflow copy, update consumers

**Files:**
- Modify: `lib/utils.py:126-135,521` (mapping definition + `mark_step_completed`)
- Modify: `lib/workflow.py:76-84,246` (delete dict, import + use shared one)
- Modify: `lib/cli_outcomes.py:21,55,102` (import + both use sites)

**Interfaces:**
- Produces: `CANONICAL_PHASE_NAMES: Dict[Phase, str]` exported from `lib.utils`.

- [ ] **Step 1: Rename the mapping in `lib/utils.py`**

Replace lines 126-135 with:

```python
# Canonical phase names keyed by execution phase — the single source for
# (a) report-artifact phase labels and (b) resume-start phase labels.
# Legacy secondary-verify folds into activation.
CANONICAL_PHASE_NAMES = {
    Phase.PREFLIGHT: "preflight",
    Phase.PRIMARY_PREP: "primary_prep",
    Phase.SECONDARY_VERIFY: "activation",
    Phase.ACTIVATION: "activation",
    Phase.POST_ACTIVATION: "post_activation",
    Phase.FINALIZATION: "finalization",
}
```

and update the use at line 521:

```python
                report_phase = CANONICAL_PHASE_NAMES.get(Phase(self.state.get("current_phase")))
```

- [ ] **Step 2: Update `lib/workflow.py`**

Delete the `_CANONICAL_RESUME_START_PHASES` dict (lines 76-84), change the
import at line 42 to:

```python
from lib.utils import CANONICAL_PHASE_NAMES, Phase, StateManager
```

and the use at line 246 to:

```python
    resume_start_phase = CANONICAL_PHASE_NAMES.get(current_phase)
```

- [ ] **Step 3: Update `lib/cli_outcomes.py`**

Change the import at line 21 to:

```python
from lib.utils import CANONICAL_PHASE_NAMES, Phase, StateIdentityMismatch, StateManager
```

and the two use sites:

```python
_REPORT_PHASE_VALUES = frozenset(CANONICAL_PHASE_NAMES.values())
```

```python
        failed_phase = {phase.value: name for phase, name in CANONICAL_PHASE_NAMES.items()}.get(failed_phase_value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_phase_name_canonical.py tests/test_utils.py tests/test_main_phase_flow.py tests/test_cli_outcomes.py tests/test_report_artifacts.py tests/test_main.py -q`
Expected: all PASS.

- [ ] **Step 5: Verify no stragglers, format, commit**

Run: `grep -rn "REPORT_PHASE_NAMES\|_CANONICAL_RESUME_START_PHASES" lib/ modules/ acm_switchover.py`
Expected: no output.

```bash
black --line-length 120 lib/utils.py lib/workflow.py lib/cli_outcomes.py tests/test_phase_name_canonical.py
isort --profile black --line-length 120 lib/utils.py lib/workflow.py lib/cli_outcomes.py tests/test_phase_name_canonical.py
git add -A
git commit -m "refactor: collapse duplicate phase-name dicts into CANONICAL_PHASE_NAMES (R2-M4)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 35)

- [ ] **Step 1: Full gate**

Run: `./run_tests.sh`
Expected: PASS (record lane counts).

- [ ] **Step 2: Update tracker row 35**

Set status `ready_for_review`; record branch, worktree, spec/plan paths, verification evidence.

- [ ] **Step 3: Push and open draft PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 35 ready for review in tracker"
git push -u origin refactor/thermos-35-phase-name-dedup
gh pr create --draft --base ansible --title "Thermos PR 35: single canonical phase-name mapping (R2-M4)" --body "<summary + verification evidence>"
```
