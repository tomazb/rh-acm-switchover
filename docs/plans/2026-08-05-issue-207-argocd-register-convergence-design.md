# Design: converge collection Argo CD pause register onto ADR-0001 (issue #207)

Status: approved for implementation (autonomous session, 2026-08-05)
Related: issue #207, issue #184 (folded in), PR #206 (ADR-0001), parity audit
2026-08-03 findings C1/C2/M1/M3.

## Problem

ADR-0001 redefined the Python pause register: entries are unresolved resume
obligations that leave only when resume is proven complete. Two other form
factors did not move:

- `scripts/argocd-manage.sh` keeps a second register in
  `.state/argocd-pause-state.json` (deprecated, still runnable).
- The `argocd_manage` collection role has no register at all: its only pause
  record is the annotation pair written with the pause patch, and its
  resume path implements two fail-open shapes the ADR explicitly rejects:
  - CRD invisible at resume → silent no-op success
    (`discover.yml` rescue + `resume.yml:3`);
  - matching `paused-by` marker with missing `original-sync-policy`
    annotation → `spec.syncPolicy` patched to `{}` and counted restored
    (audit C1, issue #184). This destroys the policy of every
    Python-paused Application, because Python stores the original policy in
    its state file, not in an annotation.

## Decision

**The cluster is the collection's register.** The annotation pair
(`acm-switchover.argoproj.io/paused-by`,
`acm-switchover.argoproj.io/original-sync-policy`) written atomically with
the pause mutation is the collection's set of unresolved resume
obligations. The collection does not duplicate Python's state-file register.

Equivalence argument (to be recorded in `coexistence.md`):

- Python needs confirmed/provisional/unknown because its pause is two steps
  (persist register entry, then patch). The collection's record rides inside
  the pause patch itself — record and mutation are one atomic API call, so
  there is no provisional window to describe. A failed/ambiguous patch is an
  Ansible task failure; the operator retries with the same run_id and the
  patch is idempotent.
- ADR-0001's load-bearing invariant is not the three states; it is "an
  obligation is discharged only when resume is proven complete — fail closed
  on ambiguity". The collection satisfies it by closing the fail-open gaps
  below, not by copying the mechanism.

Residual divergences, accepted and documented:

- `run_id` reaches the checkpoint only at phase end; a crash between the
  first pause patch and checkpoint persist leaves pauses whose run_id is not
  in any checkpoint. Recovery: the run_id is durable on the cluster in every
  `paused-by` annotation; the standalone resume playbook accepts an explicit
  run_id. Moving checkpoint writes is C4 territory (out of scope).
- Loss of both annotations simultaneously (external strip, backup restore)
  is unrecoverable by the collection alone — same class as losing the Python
  state file. Single-annotation loss becomes detectable (change 3).

## Changes

1. **Fail resume when CRD is absent but a run_id is known.** In resume mode,
   if an expected run_id resolves (explicit var, execution run_id, or
   checkpoint) and Application CRD discovery reports absent, fail with
   retry guidance instead of skipping. No run_id at all → legitimate no-op
   (Argo CD never installed, nothing was ever paused).
2. **Fail closed on missing/empty `original-sync-policy` (C1, #184).**
   Never patch `spec.syncPolicy` without a recoverable policy. Apps with a
   matching `paused-by` and a missing/empty/unparseable policy annotation
   are excluded from the patch loop, reported, and fail the phase with a
   message routing Python-paused Applications to
   `acm_switchover.py --argocd-resume-only`. Present policy restores
   exactly as today.
3. **Detect inconsistent annotation pairs.** `original-sync-policy` present
   without `paused-by`: warn and report (do not mutate — ownership cannot be
   established). Mirrors the Python "marker lost" defect shape from PR #206.
4. **Remove `scripts/argocd-manage.sh`** and `tests/test_argocd_manage_script.py`;
   update `scripts/README.md`, `docs/development/code-walkthrough.md`,
   `docs/development/e2e-test-plan.md`, `AGENTS.md`; add CHANGELOG removal
   entry. One register per form factor.
5. **Remove dead `module_utils/argocd.py build_pause_patch()`** and the
   tests that certify its non-shipped patch shape
   (`test_acm_argocd_autosync.py`, the Jinja re-implementation test in
   `test_argocd_constants_parity.py:54-88`). Replace with a guardrail that
   parses the shipped `pause.yml`/`resume.yml` and asserts the annotation
   keys and patch shape against the Python constants (fixes the M3
   guardrail illusion for Argo CD).
6. **Record the decision** in
   `ansible_collections/tomazb/acm_switchover/docs/coexistence.md` under the
   existing "Pause register invariant" section: the equivalence argument,
   the fail-closed rules, and the residual divergences above.
7. **Extend parity tests**: `ARGOCD_PAUSED_BY_ANNOTATION` (and the
   original-sync-policy key) equal across `lib/argocd.py`,
   `lib/constants.py`, `module_utils/constants.py` (M1); fail-closed resume
   contract asserted on both sides (Python: no `{}` default — already
   fail-closed; collection: task-level contract tests in the style of
   `test_argocd_manage_role_contracts.py`).

## Testing

- Collection task-contract unit tests (YAML-parsing style, no cluster):
  CRD-absent + run_id → fail; missing policy annotation → no patch + phase
  fail; empty `{}` annotation → fail closed; marker mismatch unchanged;
  successful restore unchanged; inconsistent pair → warn path.
- Python-side parity tests for constants and shipped-YAML patch shape.
- Removal tests: no references to `argocd-manage.sh` outside CHANGELOG.
- Full `./run_tests.sh` + collection unit suite green; `git diff --check`.

## Non-goals

C3/C4 checkpoint rework, H11 report artifacts, ApplicationSet ownership,
discovery redesign, live-cluster certification.
