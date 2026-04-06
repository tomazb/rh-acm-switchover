# Pre-Merge Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the mypy type error on `_retry_error_baseline` and update stale `--argocd-check` references in active documentation before merging the branch.

**Architecture:** Two independent fixes — one 1-line Python change to declare an attribute, and documentation updates to 2 files replacing removed CLI flag references with auto-detection language.

**Tech Stack:** Python (mypy), Markdown

---

### Task 1: Fix mypy type error — declare `_retry_error_baseline` on StateManager

**Files:**
- Modify: `lib/utils.py:123` (inside `StateManager.__init__`)

**Step 1: Verify the mypy error exists**

Run:
```bash
source .venv/bin/activate && python -m mypy lib/ modules/ acm_switchover.py 2>&1 | grep error
```
Expected: `acm_switchover.py:385: error: "StateManager" has no attribute "_retry_error_baseline"  [attr-defined]`

**Step 2: Add the attribute declaration**

In `lib/utils.py`, inside `StateManager.__init__()`, after line 128 (`self._run_lock_path = ...`), add:

```python
        self._retry_error_baseline: Optional[Dict[str, Any]] = None
```

Ensure the `Optional`, `Dict`, and `Any` types are already imported (they are — check the imports at the top of `lib/utils.py`).

**Step 3: Verify mypy passes**

Run:
```bash
source .venv/bin/activate && python -m mypy lib/ modules/ acm_switchover.py 2>&1
```
Expected: `Success: no issues found in 28 source files`

**Step 4: Run tests to confirm no regressions**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/ -q --tb=short 2>&1 | tail -5
```
Expected: `941 passed, 26 skipped`

**Step 5: Commit**

```bash
git add lib/utils.py
git commit -m "fix(types): declare _retry_error_baseline attribute on StateManager

Resolves mypy attr-defined error. The attribute is transient (not
persisted to state file) and used only for retry baseline tracking
within a single run.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Update PRD to remove stale `--argocd-check` references

**Files:**
- Modify: `docs/project/prd.md` (3 locations)

**Step 1: Update UC-3 success criteria (line 101)**

Replace:
```markdown
- ACM-touching Argo CD Applications can be identified with `--argocd-check`
```
With:
```markdown
- ACM-touching Argo CD Applications are auto-detected when ArgoCD CRD is present
```

**Step 2: Update FR-1 requirement (line 142)**

Replace:
```markdown
- Optional Argo CD discovery and ACM-impact reporting must run when `--argocd-check` is set
```
With:
```markdown
- Argo CD discovery and ACM-impact reporting runs automatically when ArgoCD CRD is detected
```

**Step 3: Remove `--argocd-check` from CLI flags list (line 263)**

Delete this line:
```markdown
- `--argocd-check`
```

**Step 4: Verify no stale references remain in active docs**

Run:
```bash
grep -rn 'argocd-check' docs/project/ docs/development/ docs/operations/ docs/reference/
```
Expected: Only `docs/development/e2e-test-plan.md` matches (fixed in Task 3).

**Step 5: Commit**

```bash
git add docs/project/prd.md
git commit -m "docs: update PRD to reflect ArgoCD auto-detection (remove --argocd-check)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Update E2E test plan to remove stale `--argocd-check` references

**Files:**
- Modify: `docs/development/e2e-test-plan.md` (lines 84-85)

**Step 1: Update B2 test case (line 84)**

Replace the entire B2 row:
```markdown
| B2 | Preflight with argocd-check | `./scripts/preflight-check.sh --primary-context mgmt1 --secondary-context mgmt2 --method passive --argocd-check` | ArgoCD instances and ACM apps listed | ✅ PASS | Detected ArgoCD instances + ACM apps |
```
With:
```markdown
| B2 | Preflight with ArgoCD auto-detection | `./scripts/preflight-check.sh --primary-context mgmt1 --secondary-context mgmt2 --method passive` | ArgoCD auto-detected, instances and ACM apps listed | ✅ PASS | ArgoCD CRD detected, instances + ACM apps reported |
```

**Step 2: Update B3 test case (line 85)**

Replace the entire B3 row:
```markdown
| B3 | Postflight with argocd-check | `./scripts/postflight-check.sh --new-hub-context mgmt1 --old-hub-context mgmt2 --argocd-check` | ArgoCD state in postflight report | ✅ PASS | ArgoCD sync status checked |
```
With:
```markdown
| B3 | Postflight with ArgoCD auto-detection | `./scripts/postflight-check.sh --new-hub-context mgmt1 --old-hub-context mgmt2` | ArgoCD auto-detected, state in postflight report | ✅ PASS | ArgoCD sync status checked |
```

**Step 3: Verify no stale references remain in active docs**

Run:
```bash
grep -rn 'argocd-check' docs/project/ docs/development/ docs/operations/ docs/reference/
```
Expected: No matches.

**Step 4: Commit**

```bash
git add docs/development/e2e-test-plan.md
git commit -m "docs: update E2E test plan for ArgoCD auto-detection (remove --argocd-check)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Final verification

**Step 1: Run full verification**

```bash
source .venv/bin/activate
python -m mypy lib/ modules/ acm_switchover.py 2>&1
python -m pytest tests/ -q --tb=short 2>&1 | tail -5
grep -rn 'argocd-check' docs/project/ docs/development/ docs/operations/ docs/reference/
```

Expected:
- mypy: `Success: no issues found in 28 source files`
- pytest: `941 passed, 26 skipped`
- grep: no output (0 matches)
