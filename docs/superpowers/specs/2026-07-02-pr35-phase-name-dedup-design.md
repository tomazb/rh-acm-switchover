# PR 35 Design: Single Canonical Phase-Name Mapping (R2-M4)

**Date:** 2026-07-02
**Finding:** `R2-M4` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 35 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `refactor/thermos-35-phase-name-dedup`

## Problem

Before this change, `lib/utils.py` (`REPORT_PHASE_NAMES`) and
`lib/workflow.py` (`_CANONICAL_RESUME_START_PHASES`) had byte-identical
`Phase → str` dicts, each carrying its own copy of the same rule (including the
legacy `SECONDARY_VERIFY → "activation"` fold, cross-referenced only by
comments). A change to one could silently desync report-artifact phase labels
from resume-start phase labels.

Verified consumers:
- `lib/utils.py:521` — `StateManager.mark_step_completed` phase tagging.
- `lib/workflow.py:246` — resume-start summary phase.
- `lib/cli_outcomes.py:21,55,102` — imports `REPORT_PHASE_NAMES` for report
  phase values and failed-phase lookup.
- No references in `tests/` or the Ansible collection.

## Approaches considered

1. **Rename to `CANONICAL_PHASE_NAMES` in `lib/utils.py` (chosen)** — one
   definition next to the `Phase` enum, docstring stating both uses; delete
   `workflow.py`'s copy; update the three consumer sites. `workflow.py`
   already imports from `lib.utils`, so no new import edges or cycles.
2. **Keep the `REPORT_PHASE_NAMES` name and import it in `workflow.py`** —
   smallest diff, but the "report" name is misleading at the resume-start
   call site; the mapping is canonical, not report-specific.
3. **New `lib/phase_names.py` module** — rejected, YAGNI for a single dict.

## Design

In `lib/utils.py`, rename `REPORT_PHASE_NAMES` to `CANONICAL_PHASE_NAMES`
with a docstring/comment noting it is the single source for (a)
report-artifact phase labels and (b) resume-start phase labels, and that
legacy `SECONDARY_VERIFY` folds into `activation`. No value changes.

Consumers:
- `lib/utils.py` internal use → `CANONICAL_PHASE_NAMES`.
- `lib/workflow.py` — delete `_CANONICAL_RESUME_START_PHASES`, import
  `CANONICAL_PHASE_NAMES` from `lib.utils`, use at the resume-summary site.
- `lib/cli_outcomes.py` — import and use `CANONICAL_PHASE_NAMES`.

No compatibility alias: neither name is used by tests, the collection, or
any other module, so a dangling alias would be dead code.

### Test (red-first)

Extend guardrail-style coverage in a new
`tests/test_phase_name_canonical.py`:
- `CANONICAL_PHASE_NAMES` importable from `lib.utils`, covers every
  non-INIT `Phase`, and folds `SECONDARY_VERIFY` to `"activation"`.
- `lib.workflow` and `lib.cli_outcomes` reference the same object
  (`is` identity), proving there is exactly one mapping.
- Old names are gone from both modules.

## Acceptance criteria

1. One `Phase → str` mapping defined in source code; `grep -rn
   "REPORT_PHASE_NAMES\|_CANONICAL_RESUME_START_PHASES" lib/ modules/`
   returns nothing. Tests may reference the legacy names only as negative
   guardrails.
2. New test passes; suites for the three consumers pass:
   `tests/test_utils.py`, `tests/test_main_phase_flow.py`,
   `tests/test_cli_outcomes.py`, `tests/test_report_artifacts.py`,
   `tests/test_main.py`.
3. Touched-file `black`/`isort` (line-length 120) and `git diff --check`
   clean; full `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved this slice via the tracker queue; design gate
satisfied by this spec. Rename-without-alias chosen because the audit
found zero external references; if review wants an alias for downstream
consumers, it is a one-line addition.
