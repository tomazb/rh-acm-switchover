# ArgoCD Pause/Resume Correctness Residuals — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`, independently revalidated by a second reviewer (Codex). Only issues confirmed open on `ansible` and untracked in `thermos-resolution-plan.md` are in scope.

## Problem

Four residual defects survive the earlier ArgoCD fixes on this branch (`automated: null`
pause patch, post-pause re-read, resume resourceVersion precondition, marker-mismatch skip):

1. **Bash pause is a silent no-op.** `scripts/argocd-manage.sh:341` builds the pause patch
   with `jq 'del(.automated)'` and sends it with `oc patch --type=merge` (`:345`). Under
   RFC 7396 merge-patch semantics, omitting a key leaves it unchanged — auto-sync stays
   enabled. The script then prints `Paused ...` (`:353-356`) and journals the Application as
   paused (`:364-366`). Python (`lib/argocd.py:678-684`, `automated = None`) and the
   collection (`roles/argocd_manage/tasks/pause.yml:49-53`, `combine({'automated': none})`)
   are correct; the Bash path is divergent and lies about safety-critical state.
2. **`automated.enabled: false` is classified as active** everywhere: Python
   `lib/argocd.py:423-426` (any non-null `automated` object), Bash
   `scripts/argocd-manage.sh:326-330`, collection `pause.yml:58-61`. Argo CD ≥2.13 keeps the
   `automated` object with `enabled: false` when auto-sync is toggled off, so an already
   -disabled Application gets patched, journaled, and "restored" for no reason.
3. **Resume overwrites with stale values and never verifies.** All three implementations
   send the entire stored `syncPolicy` snapshot back (`lib/argocd.py:809-897`,
   `resume.yml:55-136`, `argocd-manage.sh:430-449`). A merge patch does not delete
   concurrently added sibling keys, but it does overwrite any pre-existing key an operator
   changed mid-switchover with the stale stored value. No implementation re-reads the
   Application after resume to verify `automated` is restored and the marker is gone. Bash
   additionally lacks the resourceVersion precondition Python and the collection have.
   (Collection OCC parity is separately tracked as `TR2D-02`; that row does not cover
   post-resume verification or the stale-overwrite shape.)
4. **No destructive-phase gates.** The pause step is checkpointed
   (`modules/primary_prep.py:71-81`, `state.step(STEP_PAUSE_ARGOCD_APPS, ...)`), so a run
   resumed at ACTIVATION never re-examines ArgoCD state (`acm_switchover.py:802-825`,
   `lib/operation_runners.py:139-165`). Integrated decommission
   (`modules/finalization.py:1125-1149`) performs no ArgoCD revalidation. Between crash and
   resume, an operator or controller can re-enable auto-sync and the tool proceeds into
   activation/teardown with GitOps actively fighting it.

## Goals

1. The Bash pause patch actually disables auto-sync, and the script fails loudly when it
   does not.
2. One shared auto-sync classification rule across Python, collection, and Bash, including
   `enabled: false`.
3. Resume restores exactly what pause removed — nothing else — and proves it.
4. Destructive phases revalidate journaled pause state before proceeding.

## Non-goals

- Bash script lifecycle (deprecation/deletion, state identity, permissions, locks, JSON
  Patch, RV preconditions in Bash): owned by planned slice `SSA-05`. This spec makes the
  minimal correctness fix only, so the script tells the truth for as long as it exists.
- Global rediscovery gates or UNKNOWN classification of arbitrary Applications: planned
  `R3-10a` is narrowing exactly that blast radius; this spec stays journal-scoped.
- Collection resume OCC outcome parity: tracked by `TR2D-02`.
- Foreign-marker override policy changes (current mismatch → skip behavior is kept).

## Design

### 1. Bash pause fix (minimal)

- `scripts/argocd-manage.sh` pause: build the patch with `jq '.automated = null'` (explicit
  null) instead of `del(.automated)`; keep `--type=merge`.
- After patching, re-read the Application (`oc get ... -o json`) and verify the exact
  post-patch value: `spec.syncPolicy.automated` must be `null`. Merely "inactive" per the
  §2 classification is not enough — another actor flipping the object to
  `{"enabled": false}` between patch and re-read must not be journaled as our successful
  pause. On any other value: print a failure for that Application, do NOT journal it as
  paused, and exit non-zero after processing remaining Applications (consistent with the
  existing per-app error accumulation). The same exact-null post-pause check applies to
  the Python and collection verifies.
- Patch-succeeded-but-verify-unreadable is not a discard: when the patch call returned
  success but the post-patch read fails, the Application may already be paused. All three
  implementations journal the entry as `verify_pending` (state written immediately, even
  though the overall run fails) so resume and the destructive-phase gates know a
  restoration obligation may exist; gates treat `verify_pending` as blocking until an
  operator or rerun re-reads and settles it.
- The only other Bash changes are the shared classification expression (§2) and the resume
  shape + verification (§3) — all correctness fixes to existing code paths. Everything else
  about the script is `SSA-05` scope.

### 2. Shared auto-sync classification

Tri-state rule, identical in `lib/argocd.py` (`is_autosync_enabled` → new
`classify_autosync`), `plugins/module_utils/argocd.py`, `pause.yml` gate expression, and
the Bash `jq` expression:

| `spec.syncPolicy.automated` shape | classification | pause action | journal |
| --- | --- | --- | --- |
| key absent | inactive | skip | not journaled (unchanged behavior) |
| `null` | inactive | skip | not journaled (unchanged behavior) |
| object with `enabled: false` | inactive | skip | journaled as `skipped_disabled` with the observed object |
| any other object (incl. `{}`) | active | pause | journaled as paused with original value |

`skipped_disabled` entries are informational: resume must never patch them, and the
summary reports them distinctly. Rationale: auto-sync is already off; patching adds churn
and a restore obligation with no safety benefit. The stored observed object lets a future
operator audit what was seen.

### 3. Resume shape and verification

- Patch body becomes `{"spec": {"syncPolicy": {"automated": <stored original>}}}` plus the
  marker-annotation removal — only the key pause removed. Sibling `syncPolicy` keys are
  never sent. Applies to Python (`lib/argocd.py`), collection (`resume.yml`), and Bash.
- Keep the existing resourceVersion precondition in Python and the collection (Bash stays
  without one — SSA-05 scope).
- After a successful patch, re-read the Application and verify: (a) `automated` is
  deep-equal to the stored original — including when the original was `{}`; a different
  active object (e.g. `{"enabled": true}` or a prune-only object) is a verification
  failure, with no normalization — (b) the pause marker annotation is absent. On mismatch: per-app failure in the summary, overall
  non-zero result (`SwitchoverError` / `ansible.builtin.fail` / exit ≠ 0). One re-read, no
  polling loop — controller drift after a verified restore is the operator's normal GitOps
  state, not a switchover concern.

### 4. Journal-scoped destructive-phase gates

New helper `revalidate_argocd_pause_journal(...)` in `lib/argocd_coordinator.py` (Python)
and an equivalent pre-task include in the collection:

- Input: the persisted pause journal (state key `argocd_paused_apps` / collection
  checkpoint equivalent). If the journal is empty or absent, the gate passes trivially.
- Journal entry states and gate treatment: `paused` and `verify_pending` are non-terminal;
  `resumed` is the only terminal state and is skipped by gates. `skipped_disabled` is
  informational, never terminal, and is always re-read (below).
- `verify_pending` entries (pause patch succeeded but the post-patch read failed, §1)
  block every gate until settled: the gate re-reads the Application; exact
  `automated == null` with our marker → promote to `paused` (durably journaled) and apply
  the normal `paused` checks; any other observed state → fail closed; read error → fail
  closed, entry stays `verify_pending`.
- For each journaled *paused* entry: GET the Application. Failure modes, each fail-closed:
  - read error (incl. 404) → `SwitchoverError` naming the app and error;
  - marker annotation missing or not this run's identity → fail (journal/cluster
    disagreement — someone else touched it);
  - classification says auto-sync active again → fail (re-enabled mid-switchover).
- `skipped_disabled` entries are always re-read and re-classified, on every gate pass and
  regardless of any terminal filtering; if now active → fail with a message that auto-sync
  was enabled mid-switchover on a previously disabled app.
- Failure message includes per-app `oc get application.argoproj.io -n <ns> <name> -o
  jsonpath=...` inspection commands and the choice: re-pause (re-run primary-prep) or
  investigate/override.
- Call sites (both un-checkpointed, executed on every pass):
  1. ACTIVATION entry, on every pass — including fresh runs that paused in-process. A
     controller or operator can re-enable auto-sync between the pause and activation within
     the same run; the gate is cheap (one GET per journaled Application) and closes that
     window too;
  2. FINALIZATION phase entry, before the phase's first mutation, when `--argocd-manage`
     was active for the run — not merely before `_decommission_old_hub()`; earlier
     finalization steps already mutate (backup enablement, old-hub handling), and the gate
     must precede all of them.
- Gate/resume ordering: gates skip only `resumed`-terminal entries (per the state table
  above — `skipped_disabled` is still re-read). When `--argocd-resume-after-switchover`
  resumes an Application, its journal entry is
  marked terminal (`resumed`) **only after** the resume patch, the deep-equality
  restoration check, and the marker-absence check have all passed and the journal update
  is durably written; a failed or unverified restore keeps the entry non-terminal, so
  subsequent gates still block on it. With that, the marker is legitimately gone for
  terminal entries, and gate passes skip them instead of reading the missing marker as
  tampering. The
  finalization gate therefore runs before any resume step in the same phase.
- Dry-run: gates execute read-only and log what would block (consistent with the `F40`
  resolution that dry-run performs real discovery).

## Error handling summary

Every new failure path is fail-closed and names the exact Applications: Bash exit ≠ 0 with
per-app lines; Python `SwitchoverError` aggregating failed apps; collection
`ansible.builtin.fail` with the same list. No new warning-only paths.

## Testing

- **Python** (`tests/test_argocd.py`, coordinator tests): patch-body assertions
  (`automated` key present and null on pause; resume body contains only `automated` +
  marker removal); classification table test over the four shapes; resume verification
  success/mismatch; gate tests — marker stolen, auto-sync re-enabled, read error, 404,
  empty journal, `skipped_disabled` re-enabled, clean pass; `verify_pending` promotion to
  `paused` on confirmed null, blocking on any other state or read error; ACTIVATION
  call-site tests:
  restored-from-state resume AND same-run re-enable after an in-process pause both blocked.
- **Collection**: parity tests extended with the same classification table and resume
  shape; new-assertion oracles must load the real `pause.yml`/`resume.yml` values (do not
  extend the hand-written oracle pattern flagged by `R3-T3`).
- **Bash** (`tests/test_argocd_manage_script.py`): mock `oc` upgraded to capture and parse
  the `-p` payload; assert `"automated":null` present on pause; assert re-read failure path
  exits non-zero and does not journal. This closes the accept-any-patch blind spot that let
  defect 1 ship.
- Version bump per repo policy (PATCH for Python+Bash+collection, synced).

## Tracker updates (same PR as this spec)

New finding rows in `thermos-resolution-plan.md` (Round-4 / spec-sourced section):

| id | severity | summary |
| --- | --- | --- |
| new-A1 | High | Bash pause merge patch omits `automated` → silent no-op journaled as paused |
| new-A2 | Medium | `automated.enabled: false` classified active in all three implementations |
| new-A3 | Medium | Resume overwrites stale `syncPolicy` keys; no post-resume verification |
| new-A4 | Medium | No journal revalidation before ACTIVATION-on-resume or integrated decommission |

Plus one planned slice row referencing this design doc. Cross-references recorded as
adjacent-not-superseded: `SSA-05` (script lifecycle), `TR2D-02` (collection resume OCC),
`R3-10a` (discovery blast radius), `R3-T3` (parity-test oracle).

## Acceptance criteria

1. A cluster with an auto-sync Application paused via the Bash script has
   `spec.syncPolicy.automated == null` afterwards, or the script exits non-zero.
2. An Application with `automated.enabled: false` is never patched by pause and never
   patched by resume, in all three implementations.
3. Resume changes only `spec.syncPolicy.automated` and the marker annotation; a sibling
   `syncPolicy.retry` edit made mid-switchover survives resume.
4. A run resumed at ACTIVATION after its paused Application was re-enabled (or its marker
   replaced) fails closed before any activation mutation, naming the Application.
5. A finalization phase (including integrated decommission) with `--argocd-manage` fails
   closed under the same conditions before any finalization mutation.
6. All existing green tests still pass except those that asserted the defective patch
   shapes, which are inverted in the same commit.
