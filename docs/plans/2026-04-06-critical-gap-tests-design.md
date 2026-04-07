# Critical Remaining Test Gaps Design

**Date:** 2026-04-06  
**Status:** Approved design

## Goal

Close the most critical remaining test gaps with **business-meaningful tests**, not synthetic line-chasing tests.

This round targets all three remaining high-risk areas:

1. `modules/finalization.py`
2. `modules/primary_prep.py`
3. `modules/preflight/backup_validators.py`

The design optimizes for **highest-signal operator/business scenarios** first, with only a small amount of branch-focused coverage where a failure path is safety-critical.

## Chosen Approach

Use a **workflow-scenario-first** strategy with a small hybrid tail.

- Prefer tests that drive real module methods through operator-visible situations
- Mock only Kubernetes / Argo CD boundaries
- Use real `StateManager` instances only when state continuity is part of the contract
- Assert on outcomes that matter operationally:
  - whether the workflow blocks or proceeds
  - whether state is preserved or reset correctly
  - whether optional failures degrade safely
  - whether retries and reruns remain idempotent

## Scope

### 1. `modules/primary_prep.py`

Add tests for the highest-signal preparation outcomes:

- **Thanos scale-down error handling**
  - optional 404 on the StatefulSet should warn but not fail the whole prep flow
  - non-404 API errors should surface as real failures
  - runtime/value failures should propagate clearly
- **Argo CD pause failure handling**
  - failed pause attempts must not leave stale pause-state entries behind
- **Idempotent rerun behavior**
  - already-annotated managed clusters should not be patched again
  - already-paused applications should not be duplicated in state or re-paused incorrectly

### 2. `modules/finalization.py`

Add tests around the old-hub safety outcomes:

- **Old-hub observability shutdown reporting**
  - all components scaled down
  - some components still running after wait
  - dry-run reporting without mutation
- **autoImportStrategy reset behavior**
  - configmap missing after activation-set state
  - configmap already at non-sync value
  - configmap still sync and must be deleted
  - delete returns 404 and is treated as already complete

These tests should verify both the operator-visible result and the state transitions that make resume/retry safe.

### 3. `modules/preflight/backup_validators.py`

Add tests for go/no-go backup decisions:

- backups remain in progress after the wait window
- backups disappear after the wait and should fail preflight
- restore `FinishedWithErrors` with benign “already available” messages
- restore `FinishedWithErrors` with real failure messages

The point is to test whether preflight makes the **right operational decision**, not just whether a branch executes.

## Test Design Principles

1. **Use real module methods**
   - Avoid tests that only exercise helpers in isolation unless the helper itself represents a user-visible rule.

2. **Mock at the system boundary**
   - Kubernetes API calls
   - Argo CD library calls
   - time-based waiting where necessary

3. **Assert meaningful outcomes**
   - raised `SwitchoverError` vs warning-and-continue
   - state cleared vs preserved
   - duplicate work avoided on rerun
   - validation result marked pass/fail for the right reason

4. **Avoid fake coverage**
   - no tests that only prove `set_config()` round-trips
   - no tests that only prove argparse or Python inheritance behavior
   - no branch-only tests unless the branch corresponds to a safety-critical operator outcome

## Files to Change

- `tests/test_primary_prep.py`
- `tests/test_finalization.py`
- `tests/test_preflight_validators_unit.py`

## Verification Plan

1. Run focused pytest for the three touched files
2. Run the full `tests/` suite
3. Spot-check coverage for:
   - `modules/primary_prep.py`
   - `modules/finalization.py`
   - `modules/preflight/backup_validators.py`

## Out of Scope

- widening coverage for lower-risk modules
- cosmetic refactors to existing tests
- production code changes unless a stronger test exposes a real bug
