# PR29 Operation Runner Design

## Goal

Extract the switchover/restore-only runner and operation-dispatch seam from
`acm_switchover.py` into a focused helper module while preserving the current
phase semantics, resume behavior, dry-run rollback behavior, and
`acm_switchover.*` patch/import surface used by the existing test suite.

## Problem

After `PR 27` moved runtime/bootstrap logic into `lib/runtime_bootstrap.py`,
`acm_switchover.py` still carries a large runner cluster that mixes three
responsibilities:

- wrapper-level dry-run state snapshot handling in `run_switchover()` and
  `run_restore_only()`
- actual switchover and restore-only orchestration in
  `_run_switchover_impl()` and `_run_restore_only_impl()`
- top-level operation choice in `_execute_operation()`

Those functions are not just large; they also duplicate orchestration patterns:

- completed-state and failed-state preparation
- validate-only fast paths
- phase-flow tuple construction
- completion-banner and `Phase.COMPLETED` handling
- dispatch-time validation of missing secondary clients

That duplication makes the remaining orchestrator seam harder to review than it
needs to be and keeps too much runner wiring next to unrelated concerns in
`acm_switchover.py`.

## Scope

This design covers the `PR 29` operation-runner slice only:

- `acm_switchover.py`
- `lib/operation_runners.py`
- `tests/test_main.py`
- `tests/test_main_phase_flow.py`
- `tests/main_test_helpers.py`
- a new direct unit suite at `tests/test_operation_runners.py`

## Non-Goals

- No phase-handler extraction in this slice; `_run_phase_preflight()`,
  `_run_phase_primary_prep()`, `_run_phase_activation()`,
  `_run_phase_post_activation()`, and `_run_phase_finalization()` stay in
  `acm_switchover.py`
- No Argo CD resume/pause extraction in this slice;
  `_run_restore_only_argocd_pause()`, `_prepare_argocd_resume_clients()`,
  `_run_argocd_resume_only()`, and `_attempt_argocd_resume_on_failure()` remain
  in `acm_switchover.py`
- No CLI outcome/report extraction; `_report_target()`,
  `_phase_report_from_state()`, `_write_python_report()`, and the final
  success/failure/report shell in `main()` stay where they are
- No runtime/bootstrap changes in `lib/runtime_bootstrap.py`
- No collection-side refactor work

## Constraints

1. Preserve the `PR 27` seam. Runtime/bootstrap ownership stays in
   `lib/runtime_bootstrap.py`, and `PR 29` must not reopen that logic.
2. Preserve the `acm_switchover.*` patch/import surface for current tests. The
   first operation-runner slice must not force a broad test rewrite just to move
   code.
3. Keep `_run_restore_only_argocd_pause()` out of this slice. The operator
   chose to defer that helper to the later Argo CD safety slice in `PR 30`.
4. Preserve all current workflow semantics:
   - completed-state no-op handling
   - failed-state retry preparation
   - `resume_summary` persistence
   - validate-only preflight shortcuts
   - dry-run snapshot restore
   - `Phase.COMPLETED` transitions and completion messages
   - missing secondary-client error messages
5. Keep `run_decommission()` in `acm_switchover.py`. `PR 29` may route to it,
   but it should not absorb decommission implementation into the new module.

## Relationship To PR28

`PR 28` split the remaining `F44` backlog into three follow-up seams:

1. operation runners and dispatch
2. Argo CD resume safety
3. CLI outcome/report orchestration

This design covers seam 1 only. It intentionally leaves the Argo CD-specific
restore-only pause helper in place so `PR 29` stays focused on generic runner
orchestration instead of mixing in the safety-critical Argo CD path.

## Current Shape

Today the runner cluster looks like this:

- `run_switchover()` captures/restores dry-run state snapshots and calls
  `_run_switchover_impl()`
- `_run_switchover_impl()` handles completed/failed state prep, validate-only,
  switchover phase-flow wiring, completion state, and success logging
- `run_restore_only()` captures/restores dry-run state snapshots and calls
  `_run_restore_only_impl()`
- `_run_restore_only_impl()` handles restore-only defaults, completed/failed
  state prep, validate-only, restore-only phase-flow wiring, completion state,
  and success logging
- `_execute_operation()` chooses decommission vs restore-only vs switchover and
  enforces the secondary-client guardrails

These functions already align around one seam. They are the natural next unit to
move after runtime/bootstrap.

## Approaches Considered

### Approach 1: Extract Runner Implementations And Dispatch To A New Module

Create `lib/operation_runners.py` and move the real runner logic there, while
keeping thin compatibility wrappers in `acm_switchover.py`.

Pros:

- Best size reduction for the remaining non-Argo-CD seam
- Keeps phase handlers and Argo CD helpers in place
- Preserves the `acm_switchover.*` surface via wrapper functions and injected
  hooks
- Gives the new module a direct unit-test home

Cons:

- Requires explicit hook wiring so patched `acm_switchover` symbols still reach
  the extracted logic
- Introduces small compatibility-builder helpers in `acm_switchover.py`

### Approach 2: Extract Only Phase-Flow Tuple Builders

Move only the tuple construction for switchover and restore-only phase flows,
leaving the rest of the runner logic in `acm_switchover.py`.

Pros:

- Mechanically safe
- Smaller code movement

Cons:

- Leaves most duplication in place
- Does not meaningfully reduce the orchestrator seam
- Still leaves `_execute_operation()` and completion logic crowded in the main
  file

### Approach 3: Include `_run_restore_only_argocd_pause()` In PR29

Treat the restore-only pause helper as runner-local and move it together with
the restore-only runner.

Pros:

- Produces a cleaner restore-only runner boundary
- Reduces one more helper from `acm_switchover.py`

Cons:

- Mixes generic runner extraction with Argo CD state mutation concerns
- Conflicts with the chosen `PR 30` boundary for Argo CD-specific work
- Makes PR29 less isolated and harder to review

## Recommendation

Use Approach 1.

`PR 29` should create a focused `lib/operation_runners.py` module that owns the
real switchover/restore-only runner logic and operation dispatch, while
`acm_switchover.py` keeps thin wrappers that pass the current module-level
phase/failure hooks into the extracted code. `_run_restore_only_argocd_pause()`
stays in `acm_switchover.py` and is injected into the restore-only runner
instead of moving with it.

## Proposed Design

### New Module

Create `lib/operation_runners.py`.

This module should own:

- the extracted switchover runner implementation
- the extracted restore-only runner implementation
- the extracted operation-dispatch function
- small frozen dataclasses that carry the injected hooks needed to preserve
  `acm_switchover` compatibility

Recommended dataclasses:

- `SwitchoverRunnerHooks`
- `RestoreOnlyRunnerHooks`
- `OperationDispatchHooks`

The purpose of those hook objects is not abstraction for its own sake. They are
the compatibility seam that lets `acm_switchover.py` keep its current patchable
symbols while the extracted module receives explicit handler references.

### `acm_switchover.py` After PR29

Keep these public/module-level names in `acm_switchover.py`:

- `run_switchover()`
- `_run_switchover_impl()`
- `run_restore_only()`
- `_run_restore_only_impl()`
- `_execute_operation()`

But reduce them to thin wrappers:

- `run_switchover()` keeps the dry-run snapshot wrapper and delegates to
  `_run_switchover_impl()`
- `_run_switchover_impl()` delegates into `lib.operation_runners` using hooks
  built from current module-level symbols such as `_run_phase_preflight()` and
  `_attempt_argocd_resume_on_failure()`
- `run_restore_only()` keeps the dry-run snapshot wrapper and delegates to
  `_run_restore_only_impl()`
- `_run_restore_only_impl()` delegates into `lib.operation_runners` using hooks
  built from current module-level symbols, including
  `_run_restore_only_argocd_pause()` passed in as an injected restore-only step
- `_execute_operation()` delegates into `lib.operation_runners` using dispatch
  hooks that reference `run_decommission()`, `run_restore_only()`, and
  `run_switchover()`

This preserves compatibility while making the extracted module the true owner of
runner orchestration.

### What Must Stay In `acm_switchover.py`

These functions stay in the main file in this slice:

- `_run_phase_preflight()`
- `_run_phase_primary_prep()`
- `_run_phase_activation()`
- `_run_phase_post_activation()`
- `_run_phase_finalization()`
- `_fail_phase()`
- `_fail_unexpected_phase_state()`
- `_attempt_argocd_resume_on_failure()`
- `_run_restore_only_argocd_pause()`
- `run_decommission()`

The extracted module may call them only through injected hooks.

### Behavior That Must Not Change

The extracted runner module must preserve all current behavior for:

- switchover phase order and resume routing
- restore-only phase order, including the inserted restore-only Argo CD pause
  step passed in from `acm_switchover.py`
- `resume_summary` persistence written by `lib.workflow.run_phase_flow()`
- completed-state no-op behavior for recent runs
- failed-state retry preparation for resumable phases
- validate-only fast paths that only run preflight
- dry-run completion messages and dry-run state restoration
- `Phase.COMPLETED` marking and the success banners for switchover vs
  restore-only
- `ValueError("Secondary context is required for restore-only")`
- `ValueError("Secondary context is required for switchover")`

### Test Strategy

Add a new direct unit suite:

- `tests/test_operation_runners.py`

That file should directly test the extracted module for:

- decommission vs restore-only vs switchover dispatch
- missing-secondary guards in the extracted dispatch function
- switchover runner invocation of completed/failed-state helpers, validate-only,
  and `run_phase_flow()`
- restore-only runner invocation of its distinct phase-flow, including injected
  restore-only pause handler

Keep the existing regression surface in place:

- `tests/test_main_phase_flow.py` keeps proving that patched
  `acm_switchover._run_phase_*` symbols still drive switchover and restore-only
  call ordering
- `tests/test_main.py` keeps covering wrapper-level compatibility and selected
  phase wiring semantics
- `tests/main_test_helpers.py` may gain shared hook/dataclass fixtures if that
  keeps the new tests focused and readable

The important rule is layered verification:

- direct tests for the new module
- existing compatibility tests for the old `acm_switchover` surface

## Acceptance Criteria

1. `lib/operation_runners.py` becomes the owner of the runner/dispatch logic for
   switchover, restore-only, and `_execute_operation()` routing.
2. `acm_switchover.py` keeps thin compatibility wrappers for
   `run_switchover()`, `_run_switchover_impl()`, `run_restore_only()`,
   `_run_restore_only_impl()`, and `_execute_operation()`.
3. `_run_restore_only_argocd_pause()` remains in `acm_switchover.py` and is
   passed into the extracted restore-only runner instead of being moved in this
   slice.
4. Existing `acm_switchover.*` patch/import patterns remain usable in
   `tests/test_main.py` and `tests/test_main_phase_flow.py`.
5. The extracted runner module has direct unit coverage, and the existing
   orchestration regression surface still passes.

## Next Step After Review

Once this spec is reviewed and approved, the next step is the implementation
plan for `PR 29`, scoped to the new `lib/operation_runners.py` module, the thin
compatibility wrappers in `acm_switchover.py`, and the direct/compatibility test
updates needed to keep the runner seam behavior-preserving.
