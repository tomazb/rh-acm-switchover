# PR28 F44 Remaining Slice Map Design

## Goal

Define the remaining `F44` decomposition path after `PR 27` so the follow-up
maintainability work stays narrow, behavior-preserving, and explicitly
sequenced instead of turning into a broad `acm_switchover.py` refactor.

## Problem

`PR 27` extracted the runtime/bootstrap cluster into
`lib/runtime_bootstrap.py`, but `acm_switchover.py` still concentrates three
separate safety-sensitive seams:

- operation runner and phase-flow wiring:
  `run_switchover()`, `_run_switchover_impl()`, `run_restore_only()`,
  `_run_restore_only_impl()`, `_execute_operation()`, and the small flow helpers
  that exist only to support those runners
- Argo CD resume and failure-recovery logic:
  `_prepare_argocd_resume_clients()`, `_run_argocd_resume_only()`, and
  `_attempt_argocd_resume_on_failure()`
- CLI outcome/report orchestration:
  `_report_target()`, `_phase_report_from_state()`, `_write_python_report()`,
  and the `try`/`except`/`finally` shell in `main()` that binds runtime
  identities, dispatches the operation, records errors, writes the report, and
  emits the GitOps summary

Without an explicit slice map, the next `F44` PR could mix those seams, reopen
already-stabilized runtime logic from `PR 27`, or make the Argo CD safety path
harder to review in isolation.

## Scope

This design covers the `PR 28` docs-only scoping pass:

- `docs/superpowers/specs/2026-06-07-pr28-f44-remaining-slice-map-design.md`
- `thermos-resolution-plan.md`

## Non-Goals

- No product-code changes in `acm_switchover.py`, `lib/`, or tests
- No Python/collection parity changes
- No new behavior for switchover, restore-only, decommission, Argo CD resume,
  or report artifacts
- No commitment yet to exact helper module names for the later implementation
  slices; this spec fixes boundaries and order, not final filenames

## Constraints

1. Preserve the `PR 27` seam: runtime/bootstrap ownership stays in
   `lib/runtime_bootstrap.py`, and follow-up slices must not fold that logic
   back into `acm_switchover.py`.
2. Keep the existing `acm_switchover.*` patch/import surface stable across the
   next implementation slices unless a later slice-specific spec explicitly
   widens scope and justifies the test-surface change.
3. Treat each remaining implementation slice as its own spec-first unit. This
   `PR 28` map is not blanket approval to refactor all remaining seams in one
   branch.
4. Keep Argo CD resume safety isolated from unrelated runner/report cleanup.
   Wrong-hub protections, legacy-state fail-closed behavior, and durable pause
   state handling remain review-critical and deserve their own slice.
5. Keep `F44` as maintainability-only work. Runtime safety, parity, and
   operator-facing behavior remain fixed by the already-merged guardrail PRs.

## Relationship To PR27

`PR 27` narrowed `main()` by moving state-path resolution helpers, state
bootstrap helpers, and client-construction helpers into
`lib/runtime_bootstrap.py`, then delegating entrypoint setup through
`_prepare_runtime()`. That leaves a cleaner, but still large, coordination
surface in `acm_switchover.py`.

The next backlog is no longer "extract whatever is still big." It is the more
specific set of seams that remained after runtime/bootstrap moved out:

- operation dispatch plus phase-flow setup
- Argo CD resume safety orchestration
- CLI outcome/report orchestration

## Current Shape After PR27

After the runtime/bootstrap extraction, the file now clusters more cleanly:

- CLI surface: `parse_args()`, `validate_args()`
- workflow runners and dispatch:
  `run_switchover()`, `_run_switchover_impl()`, `run_restore_only()`,
  `_run_restore_only_impl()`, `_execute_operation()`
- phase handlers: `_run_phase_*()`
- Argo CD resume helpers:
  `_prepare_argocd_resume_clients()`, `_run_argocd_resume_only()`,
  `_attempt_argocd_resume_on_failure()`
- report helpers:
  `_report_target()`, `_phase_report_from_state()`, `_write_python_report()`
- runtime/bootstrap wrappers around `lib/runtime_bootstrap.py`
- the remaining `main()` operation shell

That shape is good enough to stop broad refactoring and choose the next seams
deliberately.

## Approaches Considered

### Approach 1: Minimal Tracker Update Only

Add a `PR 28` row and a short note that more `F44` work is coming.

Pros:

- Fastest possible tracker update
- Lowest writing cost

Cons:

- Leaves the follow-up sequence ambiguous
- Does not create a reviewable contract for the next slice boundaries
- Makes it easier for `PR 29` to become another mixed-scope refactor

### Approach 2: Docs-Only Slice Map With Ordered Follow-Ups

Use `PR 28` to document the remaining seams, record the recommended slice
order, and update the tracker to reflect that sequencing.

Pros:

- Keeps `PR 28` small and reviewable
- Creates an explicit contract for later `F44` work
- Reduces the chance that future slices mix operation dispatch, Argo CD safety,
  and report/exit concerns

Cons:

- Adds one documentation PR before the next implementation slice
- Future slice-specific specs are still required

### Approach 3: Start The Next Extraction Immediately

Skip the docs-only pass and move directly into extracting the next seam.

Pros:

- Produces code movement sooner
- Avoids an extra documentation-only branch

Cons:

- Conflicts with the spec-first Thermos gate
- Makes it too easy to pick an implementation seam without recording why that
  order is safer than the alternatives

## Recommendation

Use Approach 2.

`PR 28` should be the tracker/spec pass that records the remaining `F44`
sequence. It should not move code. The next implementation PRs should then
start one seam at a time from this map, each with its own slice-specific
design/spec and implementation plan.

## Proposed Slice Map

### PR28: Remaining F44 Slice Map And Tracker Alignment

`PR 28` is the docs-only pass:

- add this design spec
- update `thermos-resolution-plan.md`
- record the remaining `F44` backlog as ordered follow-up slices
- leave product code untouched

### PR29: Operation Dispatch And Phase-Flow Runner Extraction

Target seam:

- `run_switchover()`
- `_run_switchover_impl()`
- `run_restore_only()`
- `_run_restore_only_impl()`
- `_execute_operation()`
- only the small runner-local helpers that clearly belong with those flows, if
  needed

Boundary rules:

- keep the phase handlers (`_run_phase_*()`) in place for this slice
- keep runtime/bootstrap in `lib/runtime_bootstrap.py`
- preserve phase semantics, resume behavior, dry-run rollback, and validation
  flow

Why next:

- This is the largest remaining non-Argo-CD seam
- It reduces the coordination bulk around `main()` without touching the
  fail-closed resume path

### PR30: Argo CD Resume Safety Extraction

Target seam:

- `_prepare_argocd_resume_clients()`
- `_run_argocd_resume_only()`
- `_attempt_argocd_resume_on_failure()`

Boundary rules:

- preserve swapped-context handling, legacy-state fail-closed behavior,
  `--force` override semantics, and durable pause-state cleanup rules
- keep this slice separate from generic runner/report refactors

Why separate:

- This path is safety-critical and easier to review when isolated
- It carries the strongest wrong-hub and legacy-state correctness requirements

### PR31: CLI Outcome And Report Orchestration Extraction

Target seam:

- `_report_target()`
- `_phase_report_from_state()`
- `_write_python_report()`
- the top-level success/failure/report-emission shell around operation dispatch
  in `main()`

Boundary rules:

- preserve exit codes, error recording, report artifact shape, and GitOps report
  emission order
- keep `run_setup()` out of scope unless a later slice-specific spec proves it
  belongs in the same seam

Why last:

- It depends on the already-stabilized runtime/bootstrap seam from `PR 27`
- It benefits from separating operation dispatch first, so the final `main()`
  shell is smaller before the report/outcome move

## Sequencing Notes

- The exact helper module names for `PR 29` through `PR 31` stay open until
  those slices write their own specs and plans.
- The boundary contract above is the important part: one seam per PR, no mixed
  runner + Argo CD + reporting refactor.
- If later review shows one proposed slice is still too broad, split that slice
  again rather than merging concerns back together.

## Acceptance Criteria

1. `PR 28` records a docs-only scope pass rather than starting another code
   refactor.
2. The remaining `F44` backlog is mapped into at least three narrow follow-up
   seams: operation runners, Argo CD resume safety, and CLI outcome/report
   orchestration.
3. The tracker records `PR 28` plus tentative `PR 29` through `PR 31` sequence
   entries consistent with this design.
4. Future implementation work remains gated on slice-specific design/spec and
   implementation-plan artifacts.
5. No product behavior changes are introduced by this pass.

## Next Step After Review

Once this spec and the matching tracker update are reviewed, the next step is a
separate design/spec for whichever implementation slice starts first from this
map, expected to be the operation/phase-flow runner seam currently assigned to
`PR 29`.
