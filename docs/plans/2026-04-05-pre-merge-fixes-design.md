# Pre-Merge Fixes Design

**Date:** 2026-04-05
**Branch:** `claude/implement-gitops-detection-0tFI2`
**Goal:** Fix the two issues identified during merge readiness analysis.

## Context

Branch analysis (213 commits, 115 files, 20k+ lines) found all tests passing (941/941), no security issues, clean git history, and consistent versions (1.6.1). Two non-blocking but desirable fixes remain:

1. A mypy type error on a dynamically-assigned attribute
2. Stale `--argocd-check` references in active documentation

## Fix 1: Mypy type error — `_retry_error_baseline`

**Problem:** `acm_switchover.py:385` assigns `state._retry_error_baseline` and line 487 reads it back. `StateManager` doesn't declare this attribute, causing a mypy `attr-defined` error.

**Solution:** Add `self._retry_error_baseline: Optional[Dict[str, Any]] = None` to `StateManager.__init__()` in `lib/utils.py`. The attribute is transient (not persisted to state file) and only used for retry logic within a single run.

**Files:** `lib/utils.py` (1 line added)

## Fix 2: Stale `--argocd-check` references in active docs

**Problem:** The `--argocd-check` flag was removed in v1.6.0 (replaced by auto-detection), but two active docs still reference it.

**Changes:**

- `docs/project/prd.md`: Update 3 references to describe auto-detection behavior
- `docs/development/e2e-test-plan.md`: Update 2 test case descriptions (B2, B3) to reflect that ArgoCD detection is now automatic

**Not changed:** `docs/plans/` files — these are historical records documenting the removal itself.

## Verification

- `mypy lib/ modules/ acm_switchover.py` → 0 errors
- `pytest tests/` → 941 pass, 0 fail
- `grep -rn 'argocd-check' docs/project/ docs/development/ docs/operations/ docs/reference/` → 0 matches
