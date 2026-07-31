# Argo CD Mutation Baseline Design

**Date:** 2026-06-09

## Goal

Run the next mutation-testing spike against `lib/argocd.py` as a whole-file baseline,
using both the Python Argo CD tests and one collection unit lane plus one collection
integration lane to classify parity-sensitive survivors.

## Why This Target

`lib/argocd.py` is the highest-value remaining Phase 2 target because Argo CD
management is `dual-supported`, operator-facing, and safety-sensitive. A meaningful
survivor here could imply:

- pausing or resuming the wrong `Application`
- missing ACM-touching applications during discovery
- drifting patch payload behavior between Python and collection implementations
- regressions around resume-on-failure or ApplicationSet-managed resources

Unlike the already-completed `lib/utils.py`, `modules/activation.py`, and
`modules/decommission.py` spikes, this target has not yet been baselined and
has an explicit dual-supported parity surface in the repo docs.

## Scope

### Source target

- `lib/argocd.py`

### Python baseline

- `tests/test_argocd.py`
- `tests/test_argocd_constants_parity.py`

### Collection review lanes

- one Argo CD unit/contracts lane under
  `ansible_collections/tomazb/acm_switchover/tests/unit/`
- one Argo CD integration role lane:
  `ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py`

The collection lanes are part of baseline review and survivor classification, not
the direct mutation runner target.

## Rejected Alternatives

### 1. Helper-only spike

Limit the spike to one helper family such as ACM-touch discovery or pause-patch
helpers.

- **Pros:** faster, smaller output
- **Cons:** likely misses the highest-value survivors in whole-file orchestration

### 2. Collection-first spike

Start by mutating the collection Argo CD surface instead of Python.

- **Pros:** strong parity signal
- **Cons:** higher complexity before a Python baseline exists

### Recommendation

Start with the full Python file baseline and use the collection lanes as parity
classification guardrails.

## Execution Design

1. Verify the repo is in a safe local state for an on-demand spike.
2. Run the Python unmutated baseline:
   - `python -m pytest tests/test_argocd.py tests/test_argocd_constants_parity.py -q`
3. Run the collection review lanes:
   - targeted Argo CD unit/contracts tests
   - `python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py -q`
4. If those baselines are green, switch `[mutmut]` in `setup.cfg` to:
   - `source_paths = lib/argocd.py`
   - Python Argo CD test selection
   - narrow equivalent-mutant exclusions only if clearly justified
5. Run the whole-file mutation spike and record the baseline.
6. Classify survivors against both Python and collection evidence before proposing fixes.
7. Restore `setup.cfg` after the spike.

## Triage Policy

### Highest-priority survivors

- wrong-application selection
- ACM-touch detection misses
- pause/resume patch payload drift
- resume-on-failure behavior
- ApplicationSet or generated-child safety

### Medium-priority survivors

- missing assertions on discovery summaries
- missing assertions on patch contents
- dry-run behavior gaps

### Lower-priority likely equivalents

- logger text changes
- inert default-parameter string mutations
- formatting-only report strings

## Parity Handling

If a survivor touches dual-supported Argo CD behavior:

1. check parity status in `docs/ansible-collection/parity-matrix.md`
2. map the behavior through `docs/ansible-collection/behavior-map.md`
3. check the matching collection test layer in
   `docs/ansible-collection/test-migration-catalog.md`
4. confirm or add coverage on the collection side before proposing closure

Do not close a meaningful survivor with Python-only reasoning if the collection
surface carries the same operator-facing behavior.

## Decision Gates

- If the Python baseline fails, stop and report the baseline failure.
- If the collection lane is too noisy to classify, stop and narrow the lane before
  interpreting mutation results.
- If survivors are mostly logger or report-string noise, classify them as
  equivalent/noisy and do not broaden the scope.
- If the spike produces a manageable, meaningful survivor set, the next step is a
  dedicated implementation plan for `lib/argocd.py` survivor resolution.

## Success Criteria

- A whole-file `lib/argocd.py` mutation baseline exists.
- The baseline includes Python results plus collection review context.
- Survivors are classified into:
  - missing assertion
  - missing scenario
  - parity gap
  - equivalent
  - tool/runtime issue
- The spike ends with a clear next action:
  - no-op
  - targeted test strengthening
  - or a small parity-sensitive follow-up plan
