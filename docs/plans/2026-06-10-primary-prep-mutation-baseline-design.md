# Primary-Prep Mutation Baseline Design

**Date:** 2026-06-10

## Goal

Run the next whole-file mutation-testing baseline against `modules/primary_prep.py`,
using the Python primary-prep tests plus focused collection unit and integration
lanes to classify parity-sensitive survivors before any fix work starts.

## Why This Target

`modules/primary_prep.py` is the next highest-value mutation target after the
completed validation, RBAC, Argo CD, utils, activation, decommission, and
post-activation slices. It sits on the core switchover path and owns the
pre-activation prep steps that can make the rest of the workflow unsafe if
assertions are weak.

The parity docs mark **primary prep** as `dual-supported`, and the behavior map
points Python `modules/primary_prep.py` directly at collection
`roles/primary_prep/`. That makes this a strong next target because meaningful
survivors here are likely to expose operator-facing preparation gaps rather than
isolated local helper noise.

## Scope

### Source target

- `modules/primary_prep.py`

### Python baseline lane

- `python -m pytest tests/test_primary_prep.py -q`

### Collection review lanes

- one focused **unit/contracts lane** covering collection primary-prep behavior
- one **integration/scenario lane** exercising primary-prep behavior in the
  collection flow

The collection lanes are for parity-aware baseline review and survivor
classification. They are not the first direct mutation target.

## Approach Options Considered

### 1. Whole-file baseline with collection unit + integration context

Run a full-file mutation baseline over `modules/primary_prep.py` and evaluate
survivors against both Python and collection evidence.

- **Pros:** strongest safety signal; covers backup pause/delete semantics,
  disable-auto-import behavior, observability/Thanos prep, and Argo CD prep
  coordination in one pass
- **Cons:** broader survivor set and more classification work

### 2. Whole-file baseline with unit-only collection context

Run the same full-file spike but review survivors only against the collection
unit/contracts lane.

- **Pros:** faster than adding an integration lane
- **Cons:** weaker workflow-level parity context

### 3. Narrow slice first

Start with backup pause/delete plus auto-import only.

- **Pros:** faster and easier to triage
- **Cons:** under-samples observability prep and other fail-closed prep behavior

### Recommendation

Use **Option 1**. The primary-prep phase is safety-sensitive enough that the
first useful baseline should cover the whole file and include both collection
unit and integration context.

## Expected Survivor Buckets

### High-value buckets

- BackupSchedule pause/delete and wrong-resource targeting survivors
- disable-auto-import targeting and local-cluster exclusion survivors
- observability / Thanos scale-down and termination-wait survivors
- Argo CD prep coordination survivors

### Lower-value buckets

- logger text mutations
- inert default-parameter noise
- helper-string mutations with no operator-facing effect

## Parity Handling

If a survivor touches operator-facing behavior that also exists in
`roles/primary_prep/`, treat it as **parity-sensitive by default**.

Likely parity-sensitive areas include:

- BackupSchedule pause/delete semantics
- disable-auto-import mutation semantics
- observability / Thanos prep behavior
- Argo CD prep pause coordination when enabled

Do not close meaningful survivors with Python-only reasoning if the collection
surface exposes the same operator-facing behavior.

## Execution Design

1. Verify repo state and mutation-tool availability.
2. Run the focused Python baseline.
3. Run the selected collection unit/contracts lane.
4. Run one collection integration/scenario lane.
5. Temporarily repoint `[mutmut]` in `setup.cfg` to `modules/primary_prep.py`.
6. Run the whole-file mutation spike.
7. Classify top survivors against Python and collection evidence.
8. Record the baseline in `docs/development/mutation-testing-plan.md`.
9. Restore `setup.cfg` afterward.

## Decision Gates

- If the Python baseline fails, stop and report the baseline failure.
- If the collection lane is too noisy or fails, narrow it before trusting
  mutation output.
- If the spike mostly yields equivalent/noisy survivors, stop at baseline
  recording.
- If survivors cluster in shared behavior, the next step should be a
  parity-aware follow-up design and implementation plan.

## Success Criteria

- A whole-file `modules/primary_prep.py` mutation baseline is recorded.
- The baseline includes Python plus collection review context.
- Survivors are classified into meaningful buckets rather than left as a raw score.
- The outcome clearly answers whether the next step is:
  - no-op
  - targeted survivor triage
  - or a parity-aware follow-up slice
