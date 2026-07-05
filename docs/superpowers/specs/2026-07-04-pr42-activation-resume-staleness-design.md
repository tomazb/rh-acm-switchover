# PR 42 Design: Resume-Path Re-Validation of Passive-Restore Freshness (R2-M2)

**Date:** 2026-07-04
**Finding:** `R2-M2` from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 42 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `fix/thermos-42-activation-resume-staleness`

## Problem

Verified at `ansible` @ `87a9c070`, `modules/activation.py`:

The passive-method flow runs two checkpointed steps:
`verify_passive_sync` (freshness/phase validation of the passive-sync
Restore via `_verify_passive_sync`, lines 179-228) and then
`activate_managed_clusters` (patch- or restore-based activation). The
freshness guard is **step-scoped**: once `verify_passive_sync` is marked
completed in the state file, a hard crash before
`activate_managed_clusters` completes, followed by a resume (possibly
hours later), re-enters activation **without re-running the staleness
check**. The passive restore may meanwhile have degraded
(`FinishedWithErrors` with real errors, `Error`, stuck) — activation
would patch or delete-and-replace it anyway. This is the
crash-mid-verification case specifically; the idempotent-rerun case
(activation already applied) is already hardened
(`_activation_already_applied`, PR #105's Option-B resume fix).

## Approaches considered

1. **Call-scoped re-validation (chosen)** — extract the phase-readiness
   rules from `_verify_passive_sync` into
   `_assert_passive_restore_ready(restore, restore_name)` and assert
   inside both activation paths on the Restore object they already hold:
   - `_activate_via_passive_sync`: after the `_activation_already_applied`
     early return (a patched restore legitimately transitions phases, so
     re-validating there would be wrong), before persisting the
     pre-activation signal and patching. Zero extra API calls
     (`restore_before` is already fetched).
   - `_activate_via_restore_resource`: on the discovered passive-sync
     restore, before deleting it — establishing the invariant "never
     destroy the passive restore without confirming it was ready". When
     no passive restore exists (already deleted by a prior attempt), the
     existing tolerate-missing resume path is unchanged.
   First runs re-validate too (harmless double-check seconds apart);
   resumes get the protection automatically. No checkpoint-semantics
   changes.
2. **Re-run the `verify_passive_sync` step until activation completes**
   (widen the step-skip condition) — changes checkpoint semantics, and a
   resume after a successful patch would re-verify a restore that has
   legitimately moved on, producing false failures. Rejected.
3. **Persist a verification timestamp and re-verify only when older than
   a threshold** — introduces a tunable with no principled value and
   still misses fast degradations. Rejected.

## Design

1. New method (extracted verbatim from `_verify_passive_sync`'s phase
   logic):

```python
    def _assert_passive_restore_ready(self, restore: Dict, restore_name: str) -> None:
        """Raise FatalError unless the passive-sync restore is in an activation-ready phase.

        Shared by the verify_passive_sync step and the activation paths'
        resume re-validation (Thermos R2-M2): a crash between the verify
        step completing and activation completing must not let a resumed
        run activate against a degraded restore.
        """
        status = restore.get("status", {})
        phase = status.get("phase", "unknown")
        message = status.get("lastMessage", "")
        if phase in ("Enabled", "Finished", "Completed", "Running"):
            logger.info("Passive sync verified (%s): %s", phase, message)
            return
        if phase == "FinishedWithErrors":
            messages = status.get("messages", [])
            if restore_messages_are_benign_already_available(messages):
                logger.warning(
                    "Passive sync restore %s in %s state but all errors are"
                    " 'already available' clusters (expected for consecutive"
                    " switchovers). Proceeding.",
                    restore_name,
                    phase,
                )
                return
        raise FatalError(f"Passive sync restore not ready: {phase} - {message}")
```

2. `_verify_passive_sync` keeps its fetch + GitOps-marker recording and
   delegates the phase logic to the new method (behavior identical,
   including log/error strings).

3. `_activate_via_passive_sync`: after the already-applied early return,
   insert `self._assert_passive_restore_ready(restore_before, restore_name)`.

4. `_activate_via_restore_resource`: inside the `if restore_name:` branch
   (passive restore discovered), before building the snapshot/deleting,
   insert `self._assert_passive_restore_ready(restore, restore_name)`.

Dry-run: both activation paths run these lines before any mutation; the
assertion is read-only and uses already-fetched data, so dry-run behavior
gains the same fail-closed check (a dry run against a degraded restore now
reports the failure instead of pretending it would activate — consistent
with dry-run's discovery-and-blocker philosophy).

## Behavior change summary

- Resume after crash-mid-verification now fails closed with
  `Passive sync restore not ready: ...` instead of activating against a
  degraded restore.
- Option-B activation never deletes a passive restore that is not in a
  ready phase (previously it would delete first and only fail later, with
  rollback-from-snapshot as the safety net).
- First-run behavior unchanged except a logically-redundant re-check of
  data already in hand.

## Testing (red-first)

`tests/test_activation.py`:

1. `_activate_via_passive_sync` with a not-ready `restore_before`
   (`phase=Error`) and activation not already applied → raises
   `FatalError`, `patch_custom_resource` not called, pre-activation
   signal not persisted. (Red: currently patches.)
2. `_activate_via_passive_sync` with activation already applied and a
   degraded phase → no error (early return preserved; re-validation must
   not fire post-patch).
3. `_activate_via_restore_resource` with a discovered passive restore in
   `phase=Error` → raises `FatalError`, `delete_custom_resource` not
   called. (Red: currently deletes then creates.)
4. `_activate_via_restore_resource` with no passive restore → unchanged
   resume-tolerant path (existing tests).
5. Extraction equivalence: `_verify_passive_sync` benign
   `FinishedWithErrors` and hard-failure cases keep passing (existing
   tests cover these).

## Acceptance criteria

1. Both activation paths re-validate passive-restore readiness on entry
   (resume included), with the already-applied and missing-restore
   exemptions above.
2. New tests pass; full activation suite passes; full `./run_tests.sh`
   passes.
3. Touched-file `black`/`isort`, `git diff --check` clean.

## Out of scope

- The full-restore method (`_create_full_restore`) — no passive-sync
  staleness concept; the finding names the pre-activation Velero-restore
  staleness guard on the passive path.
- Ansible-collection parity: the collection's activation role builds its
  plan from a fresh `acm_restore_info` run on every invocation (including
  resume), so it re-reads restore state each time; no equivalent gap.
  Recorded here as the parity rationale.

## Assumptions (autonomous session)

Operator pre-approved via the tracker queue; the tracker flagged this as
the highest-complexity slice, so the spec deliberately keeps the fix
read-only-before-mutation and step-semantics-free.
