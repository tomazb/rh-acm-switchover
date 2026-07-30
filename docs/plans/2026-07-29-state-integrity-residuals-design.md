# State Execution Integrity Residuals — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`,
independently revalidated (Codex). Excludes issues already tracked: `SSA-01`
(primary≠secondary UID distinctness), `R3-A6` (collection `reset_from` identity bypass),
`R3-P7`/`R3-10c` (dry-run report artifact), `R3-X1`/`R3-10g` (run-lock handle close on
normal completion — the handle is intentionally held for process lifetime and released
through an `atexit` hook, which is not itself a leak; `R3-X1` tracks the absence of an
explicit close on normal completion and for long-lived embedding/reuse, which is what
the suite's `ResourceWarning` surfaces).
This design deliberately builds on the branch's accepted snapshot-rollback approach —
memory-mode state (as designed for `main`) is explicitly rejected here.

## Problem

1. **Validate-only leaks state.** The runtime checkpoint captures only `current_phase`,
   `errors`, `last_updated` (`lib/utils.py:482-505`); validate-only preflight writes
   versions, observability flags, expected names, and other `config` keys
   (`acm_switchover.py:639-692`) that survive the restore. The state file is not
   byte-identical after a validate-only run.
2. **Crash mid-simulation poisons state.** Dry-run snapshot/restore happens only in
   `finally` (`acm_switchover.py:428-443`); every intermediate phase/step/config write is
   durable (`lib/utils.py:468-471,516-529,570-577`). SIGKILL mid-dry-run leaves PREFLIGHT→
   FINALIZATION residue a later real run will trust. (A fresh `COMPLETED` cannot be minted
   by dry-run — `lib/operation_runners.py:182-185` returns before completion — but
   everything short of it can.)
3. **No parent-directory fsync.** `_write_state` fsyncs the temp file and `os.replace`s it
   (`lib/utils.py:367-384`) but never fsyncs the directory; the rename itself can be lost
   on power failure.
4. **Locks are state-file-scoped.** `_run_lock_path = realpath(state_file) + ".run.lock"`
   (`lib/utils.py:156`); two operators running against the same physical hubs with
   different `--state-file` paths do not contend.
5. **Reset races and `--force` overreach.** `--reset-state` calls
   `os.remove(resolved_state_file)` before the StateManager (and its run lock) exists
   (`acm_switchover.py:1073-1083`) — it can delete an active run's state. Separately,
   `--force` resets progressed/completed state on the stale-COMPLETED and
   FAILED-undeterminable paths.
6. **No run contract.** Resume reads `method`, `old_hub_action`,
   `manage_auto_import_strategy`, ArgoCD flags, etc. from the current invocation
   (`acm_switchover.py:813-823,889-901`) with no comparison against what the recorded
   progress was executed under.

## Goals

1. Simulation modes (validate-only, dry-run) leave the state file byte-identical, and a
   crashed simulation is detected — never silently trusted.
2. State writes survive power failure (rename durability).
3. One physical hub pair, one running switchover — regardless of state-file paths.
4. State reset happens under the lock; `--force` cannot destroy progressed history.
5. Safety-critical options are bound to the state; silent behavior changes on resume are
   impossible.

## Non-goals

- Memory-mode state (`main`-spec design) — rejected; the snapshot approach stays.
- Hub distinctness (`SSA-01`), collection `reset_from` (`R3-A6`).
- Evidence/transaction schema beyond what Areas B and D define (the auto-import
  transaction and migration-evidence slices, `2026-07-29-auto-import-transaction-design.md`
  and `2026-07-29-migration-evidence-design.md`).
- Cross-process lock service or lock files on shared filesystems (local flock only).

## Design

### 1. Full-fidelity simulation snapshot + crash marker

- `capture_state_snapshot()` / the validate-only checkpoint are unified into one
  full-fidelity snapshot: capture the original state-file **bytes** (or an explicit
  absent-file sentinel), not a field-level deep copy. Restore atomically writes the
  captured bytes back — deleting the file when the original was absent — so both
  `--validate-only` and `--dry-run` end byte-identical by construction.
- Durably persist `simulation_in_progress: {"mode": "dry_run"|"validate_only",
  "started_at": ...}` **before the first simulation write** (it is the first write), keep
  it present on every intermediate write, and clear it as part of the byte restore.
- On load, a present `simulation_in_progress` marker means a simulation died without
  restoring: fail closed with a message naming the mode, start time, and remedies
  (`--reset-state`, or manual inspection). No auto-repair — the snapshot lived in the
  dead process.
- Preserves the existing property that dry-run cannot mint `COMPLETED`.

### 2. Parent-directory fsync

After `os.replace` in `_write_state`: open the containing directory read-only, fsync, close.
Only explicitly-unsupported errors (`ENOTSUP`/`EINVAL`-class, e.g. filesystems that reject
directory fsync) are suppressed and logged at debug; any other I/O failure propagates —
a state write must not report success when its durability step failed.

### 3. Per-hub UID locks

- After `ensure_hub_identities` resolves both cluster UIDs, acquire two flock files —
  sorted UID order to avoid AB/BA deadlock — at
  `/tmp/acm-switchover-<euid>/locks/<sha256(uid)>`, opened with `O_NOFOLLOW|O_CREAT`,
  non-blocking. The parent chain is created and revalidated securely: each component
  (`acm-switchover-<euid>`, `locks`) is created `0700`, then verified to be a real
  directory (not a symlink), owned by the current euid, and mode-restricted before use —
  a pre-created or symlinked parent under world-writable `/tmp` aborts with a security
  error rather than following it. Holding **both** locks is an explicit barrier: identity discovery
  (read-only) completes first, then both locks are acquired before any cluster mutation,
  simulation-marker write, legacy contract adoption, or state mutation beyond identity
  recording. If the second lock cannot be acquired, the first is released before failing.
  Both handles are held until process exit.
- Contention → fatal: "another switchover is running against this hub" with the lock path.
- Existing state-file run-lock and write-lock stay unchanged. Single-context operations
  (e.g. standalone decommission) lock the one UID they have.

### 4. Reset under lock; `--force` scope

- `--reset-state` moves into StateManager as a construction mode that never parses or
  validates the existing payload: resolve the state path → acquire the run lock → then
  reinitialize/remove the file under the lock. A corrupt or schema-invalid state file must
  not make reset fail on load — reset exists precisely for that case. Normal (non-reset)
  construction keeps fail-closed payload validation. The pre-construction `os.remove` at
  `acm_switchover.py:1073-1083` is deleted. A concurrent run holding the lock makes reset
  fail fast instead of deleting live state.
- `--force` no longer triggers `state.reset()` on the stale-COMPLETED or
  FAILED-undeterminable paths; those paths now instruct the operator to use
  `--reset-state` explicitly. `--force` retains its other meanings (staleness override,
  legacy identity backfill).

### 5. Run contract

- On first run (state creation), persist `run_contract`. Canonical field table (exhaustive
  — a field not listed here is not contract-bound):

  | field | mismatch class | condition (phase boundary or journal state) and override eligibility |
  | --- | --- | --- |
  | `method` (passive/full) | A — immutable/destructive past PRIMARY_PREP | freely re-recorded through PRIMARY_PREP; past it no override is accepted |
  | `activation_method` (patch/restore) | A — immutable/destructive past PRIMARY_PREP | same boundary as `method` (the collection already binds it in checkpoint identity; Python must match) |
  | `old_hub_action` | A — immutable/destructive past PRIMARY_PREP | same boundary as `method` |
  | `manage_auto_import_strategy` | A — immutable/destructive past PRIMARY_PREP | same boundary as `method` |
  | `--argocd-manage` | A — immutable while the pause journal holds a gate obligation (any entry other than `resumed`, including `skipped_disabled`) | class B (overridable, any phase) only when the journal is absent or holds `resumed` entries exclusively; clearing it otherwise withdraws the FINALIZATION gate that must still re-read those entries |
  | `--argocd-resume-after-switchover` | A — immutable while the pause journal holds a restoration obligation (any `paused`, `verify_pending`, or `classification_unknown` entry) | class B (overridable, any phase) once no such entry remains — `resumed` and `skipped_disabled` entries impose no restoration obligation; changing it with outstanding entries can silently drop that obligation |
  | `--min-managed-clusters` (expectation floor) | B — overridable | `--accept-changed-options` accepted at any phase |
  | expectation waiver (the Area D expected-ManagedCluster-name waiver, `--skip-managed-cluster-expectations`, provisional name, in `2026-07-29-migration-evidence-design.md` §2) | B — overridable | `--accept-changed-options` accepted at any phase |
  | `--skip-observability-checks` | B — overridable | `--accept-changed-options` accepted at any phase |
  | `tool_version` | C — informational | mismatch warns at every phase; never blocks, never requires the flag |

- Every resume compares the live invocation against the contract **before any mutation**
  and **rejects every non-permitted contract difference**, listing each as
  `field: recorded → requested` together with its class and, for a rejection, the reason.
  All mismatches present are reported together, not just the first. A run carrying both a
  class A and a class B mismatch remains fatal even with the override flag.
- New flag `--accept-changed-options`: re-records the contract and journals the old→new
  diff into state (audit trail) for class B fields whose condition permits it. `--force`
  does not substitute. The override is field-and-condition scoped per the canonical table
  above.
- Legacy state without a contract: progressed legacy state (beyond PREFLIGHT) requires
  `--accept-changed-options` once to adopt the current invocation as the contract —
  adoption is an explicit, journaled act, not a silent recording. Un-progressed legacy
  state records the contract silently (nothing to protect yet), logged at info.

#### Mismatch decision model

Every contract-bound field belongs to exactly one of three classes, and the class
together with the field's current condition — its phase boundary, or for the two ArgoCD
flags its journal state — determines the outcome of a difference. There is no "any
difference is fatal" rule.

**Class A — immutable/destructive.** While the field is *within* its permitted window, a
difference is not a mismatch at all: the field is re-recorded normally (see the audit
rule below). Once the window has closed — the phase boundary passed, or the journal
obligation incurred — any difference is **always fatal before any mutation**, and neither
`--accept-changed-options` nor `--force` can override it. The error lists the field,
recorded value, requested value, and the reason and phase that make it immutable.
Recovery is `--reset-state`; additionally, for `--argocd-resume-after-switchover`,
settling every outstanding restoration obligation through an explicit resume/rerun
reopens the window. That second route does not exist for `--argocd-manage`, whose
obligation a resume cannot clear (below).

"Outstanding obligation" is **not the same obligation for the two ArgoCD flags**, because
the ArgoCD design gives `resumed` and `skipped_disabled` different roles: `resumed` is
its only terminal state and is skipped by gates, while `skipped_disabled` is
informational, never terminal, and is re-read on *every* gate pass so an app that was
disabled at pause time cannot be silently re-enabled mid-switchover.

- `--argocd-resume-after-switchover` binds a **restoration** obligation: one exists while
  any `paused`, `verify_pending`, or `classification_unknown` entry remains. Resume never
  patches `skipped_disabled` entries and `resumed` entries are complete, so a journal
  holding only those two kinds carries no restoration obligation and the flag is class B.
- `--argocd-manage` binds a **gate** obligation, which is broader: clearing it withdraws
  the FINALIZATION gate, which is the destructive-phase gate that must still re-read
  `skipped_disabled` entries before any finalization mutation. (The ACTIVATION gate runs
  on every pass and is unaffected, so the exposure is the finalization boundary.) An
  obligation therefore exists while any entry other than `resumed` remains — including
  `skipped_disabled`, which no resume can convert. Only an absent journal, or one
  consisting solely of `resumed` entries, makes this flag class B.

**Class B — overridable.** Fatal before any mutation **without**
`--accept-changed-options`. Accepted only when the canonical table permits the override
under the field's current condition — for the two ArgoCD flags, the journal-state
condition above; an override attempted while that condition forbids it is a class A
outcome. Acceptance durably records an audit entry containing the field name, the old
value, the new value, the phase, a timestamp, and the operator invocation/audit context,
and the contract is re-recorded **only after** that audit record is durably written — a
failed audit write leaves the contract unchanged and fails the run. `--force` is never a
substitute for the flag.

The same audit entry is written whenever a class A field is re-recorded inside its
permitted window (for example `method` changed during PRIMARY_PREP). That is a permitted
contract update rather than a mismatch rejection — it needs no flag — but it is journaled
with the same field/old/new/phase/timestamp/invocation record, so no contract-bound field
ever changes without an audit trail.

**Class C — informational.** `tool_version` is the current and only example. It warns,
never blocks, never requires `--accept-changed-options`, and never silently alters any
safety-critical behavior; it is recorded for diagnostics only.

### 6. Collection parity

- `checkpoint_phase`'s existing `validate_operation_identity` gains a contract comparison
  recorded at checkpoint creation. The compared set is the complete canonical field set
  above, enumerated explicitly rather than abbreviated — no summary phrase such as
  "activation-affecting flags" stands in for it:

  | contract field | collection representation | class and phase rule |
  | --- | --- | --- |
  | `method` | operation method variable | A — immutable past PRIMARY_PREP |
  | `activation_method` | activation-method variable (already bound in checkpoint identity) | A — immutable past PRIMARY_PREP |
  | `old_hub_action` | old-hub action variable | A — immutable past PRIMARY_PREP |
  | `manage_auto_import_strategy` | auto-import management variable | A — immutable past PRIMARY_PREP |
  | `--argocd-manage` | ArgoCD management variable | A while any entry other than `resumed` remains (gate obligation, `skipped_disabled` included); otherwise B, any phase |
  | `--argocd-resume-after-switchover` | ArgoCD resume-behavior variable | A while any `paused`, `verify_pending`, or `classification_unknown` entry remains (restoration obligation); otherwise B, any phase |
  | `--min-managed-clusters` | expectation-floor variable | B — overridable at any phase |
  | expectation waiver (Area D expected-name waiver, per `2026-07-29-migration-evidence-design.md` §2) | waiver variable | B — overridable at any phase |
  | `--skip-observability-checks` | observability-skip variable | B — overridable at any phase |
  | `tool_version` | recorded collection/tool version | C — warns, never blocks |

  An override variable mirrors `--accept-changed-options` with the same field-and-phase
  scoping and the same durable audit-record requirement (field, old value, new value,
  phase, timestamp, invocation context, written before the contract is re-recorded).
  Parity means the two implementations reach the identical class, phase decision,
  override outcome, and audit content for the same inputs — no field can change silently
  in one implementation but not the other — while remaining independent implementations
  that share no code.
- The simulation marker is N/A for the collection (per-task `dry_run` guards mean no
  durable writes in check mode today); noted in role docs.

## Testing

- Byte-equivalence: hash state file before/after `--validate-only` and `--dry-run`
  (marker lifecycle included) — equal.
- Simulated crash: capture marker written, process killed → next load fails closed naming
  the mode; `--reset-state` then recovers.
- `_write_state` calls dir-fsync (mock `os.fsync` counts); `EIO`-class dir-fsync failure
  propagates (write not reported successful); `ENOTSUP`/`EINVAL` suppressed at debug.
- Two processes, same hub UIDs, different `--state-file` → second fails fast; different
  UID pairs → no contention; lock order stable under reversed context order.
- Reset: concurrent lock holder → reset fails fast, live state intact; reset under lock
  reinitializes.
- `--force` on progressed state no longer resets; message points to `--reset-state`.
- Contract, by mismatch class over the complete field set:
  - every **immutable-at-the-current-phase** field mismatch is fatal before mutation both
    without and with `--accept-changed-options`, naming field, recorded value, requested
    value, and the reason/phase;
  - every **overridable** field mismatch is fatal without the override flag;
  - every overridable field mismatch succeeds **only** in a phase where the canonical
    table permits the override, and only after the durable audit record is written;
  - an override attempted while its phase or journal-state condition forbids it fails
    closed, with the two ArgoCD flags asserted separately: a journal holding only
    `resumed` and `skipped_disabled` entries leaves `--argocd-resume-after-switchover`
    overridable while `--argocd-manage` stays class A (its gate must keep re-reading the
    `skipped_disabled` entries), and any `paused`, `verify_pending`, or
    `classification_unknown` entry keeps both flags class A;
  - a class A field changed **at or before** its phase boundary (for example `method`
    during PRIMARY_PREP) is re-recorded normally rather than rejected, and that
    re-record is audited like any other contract update;
  - a durable audit-write failure during an accepted class B override leaves the
    contract unchanged and fails the run — the contract is never re-recorded on an
    unwritten audit entry;
  - `tool_version` mismatch warns, does not block, and never requires the flag;
  - multiple simultaneous mismatches are all reported in one message, not just the
    first;
  - a mix of immutable and overridable mismatches remains fatal even with the flag;
  - `--force` alone authorizes no contract change in any class;
  - `--min-managed-clusters` and the expectation-waiver flag are explicitly covered,
    including their any-phase override eligibility;
  - progressed legacy state requires explicit adoption; un-progressed legacy state
    adopts silently.
- Collection: the same vectors over the same complete field set produce the same
  class, phase decision, override outcome, and audit-record content as Python.

## Release/process follow-up

Version management is not a test case. Per the repository's Version Management policy in
`AGENTS.md`, the implementation PR for this slice is ordinary development work:

- it records its changelog-worthy change under `CHANGELOG.md` `## [Unreleased]`;
- it does **not** change released version identifiers and does not create a release tag;
- the synchronized Python/Bash/container/Helm/README version updates happen only in a
  separately scoped release/version-bump PR that selects the next version from the
  accumulated `[Unreleased]` entries.

This design document performs no changelog or version update itself.

## Tracker updates (same PR)

| id | severity | summary |
| --- | --- | --- |
| new-E1 | High | Crashed dry-run/validate-only leaves durable intermediate state trusted by later runs |
| new-E2 | Medium | Validate-only checkpoint restores phase/errors only; config/steps leak |
| new-E3 | Medium | No run contract: resume silently accepts changed safety-critical options |
| new-E4 | Medium | Locks are state-file-scoped; same physical hubs don't contend across state files |
| new-E5 | Medium | `--reset-state` deletes state before lock; `--force` resets progressed state |
| new-E6 | Low | `_write_state` lacks parent-directory fsync |

Plus one planned slice row referencing this design. `SSA-01`, `R3-A6`, `R3-P7`, `R3-X1`
cross-referenced as excluded-tracked.

## Acceptance criteria

1. A validate-only or dry-run leaves the state file byte-identical; a killed simulation is
   detected and blocks the next run until reset.
2. Two invocations against the same physical hubs cannot both run, regardless of state
   file paths.
3. State reset cannot affect a running process's state file.
4. `--force` alone can never destroy progressed history.
5. Every resume rejects every non-permitted contract difference before any cluster
   mutation, per the three-class model: a class A immutable/destructive mismatch is fatal
   even with `--accept-changed-options`; a class B overridable mismatch is fatal without
   the flag and is accepted only in a phase the canonical table permits, leaving a
   durable audit record (field, old value, new value, phase, timestamp, invocation
   context) written before the contract is re-recorded; a class C informational mismatch
   (`tool_version`) warns without blocking. `--force` authorizes no contract change.
   Python and the collection apply the identical field-and-phase decisions over the same
   complete field set.
