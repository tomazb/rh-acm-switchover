# Post-Activation Mutation Baseline Design

**Date:** 2026-06-10

## Goal

Run the next whole-file mutation-testing baseline against `modules/post_activation.py`,
using the Python post-activation tests plus focused collection post-activation lanes
to classify parity-sensitive survivors before any fix work starts.

## Why This Target

`modules/post_activation.py` is the next highest-value mutation target after the
completed validation, RBAC, Argo CD, utils, activation, and decommission slices.
It sits on the core switchover path and owns post-activation safety checks that can
turn a real failure into an apparent success if assertions are weak.

The parity docs mark **post-activation verification** as `dual-supported`, and the
behavior map points Python `modules/post_activation.py` directly at the collection
`roles/post_activation/` surface. That makes this a stronger next target than
lower-level helper modules because meaningful survivors here are more likely to
expose operator-facing gaps rather than isolated implementation noise.

## Scope

### Source target

- `modules/post_activation.py`

### Python baseline lane

- `python -m pytest tests/test_post_activation.py -q`

### Collection review lanes

- one focused **unit/contracts lane** covering collection post-activation,
  klusterlet, and managed-cluster helper behavior
- one **integration/scenario lane** exercising post-activation behavior in the
  collection flow

The collection lanes are for parity-aware baseline review and survivor
classification. They are not the first direct mutation target.

## Approach Options Considered

### 1. Whole-file post-activation baseline

Run a full-file mutation baseline over `modules/post_activation.py`.

- **Pros:** broadest safety signal; captures cluster reconnection, klusterlet
  remediation, observability readiness, and metrics verification in one pass
- **Cons:** likely larger survivor set and more classification work

### 2. Connectivity + klusterlet slice first

Limit the first spike to cluster reconnection and klusterlet remediation paths.

- **Pros:** faster; isolates wrong-hub and reconnection-risk logic
- **Cons:** under-samples observability and metrics verification semantics

### 3. Observability-only slice first

Focus the first spike on observability restart/readiness logic.

- **Pros:** targeted and narrow
- **Cons:** defers the more important post-activation success/failure contract

### Recommendation

Use **Option 1**. The first useful baseline for this target should cover the whole
post-activation contract because the risk is not one helper in isolation; it is the
combined chance of reporting success while clusters or observability are still not
healthy.

## Expected Survivor Buckets

### High-value buckets

- managed-cluster reconnection / false-success survivors
- klusterlet remediation and wrong-hub verification survivors
- observability restart/readiness survivors
- wait/polling and timeout-boundary survivors

### Lower-value buckets

- logger text mutations
- inert default-parameter noise
- permissive mock call-signature survivors with no operator-facing effect

## Parity Handling

If a survivor touches operator-facing behavior that also exists in
`roles/post_activation/`, treat it as **parity-sensitive by default**.

Likely parity-sensitive areas include:

- connected-state verification for managed clusters
- klusterlet remediation outcomes
- observability readiness and restart behavior
- metrics verification semantics

Do not close meaningful survivors with Python-only reasoning if the collection
surface exposes the same operator-facing behavior.

## Execution Design

1. Verify repo state and mutation-tool availability.
2. Run the focused Python baseline.
3. Run the selected collection unit/contracts lane.
4. Run one collection integration/scenario lane.
5. Temporarily repoint `[mutmut]` in `setup.cfg` to `modules/post_activation.py`.
6. Run the whole-file mutation spike.
7. Classify top survivors against Python and collection evidence.
8. Record the baseline in `docs/development/mutation-testing-plan.md`.
9. Restore `setup.cfg` afterward.

## Decision Gates

- If the Python baseline fails, stop and report the baseline failure.
- If the collection lane is too noisy or fails, narrow it before trusting mutation
  output.
- If the spike mostly yields equivalent/noisy survivors, stop at baseline recording.
- If survivors cluster in shared behavior, the next step should be a
  parity-aware follow-up design and implementation plan.

## Success Criteria

- A whole-file `modules/post_activation.py` mutation baseline is recorded.
- The baseline includes Python plus collection review context.
- Survivors are classified into meaningful buckets rather than left as a raw score.
- The outcome clearly answers whether the next step is:
  - no-op
  - targeted survivor triage
  - or a parity-aware follow-up slice
