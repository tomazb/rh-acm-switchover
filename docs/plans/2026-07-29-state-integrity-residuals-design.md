# State Execution Integrity Residuals — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`,
independently revalidated (Codex). Excludes issues already tracked: `SSA-01`
(primary≠secondary UID distinctness), `R3-A6` (collection `reset_from` identity bypass),
`R3-P7`/`R3-10c` (dry-run report artifact), `R3-X1`/`R3-10g` (run-lock handle leak).
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
- Evidence/transaction schema beyond what Areas B and D define.
- Cross-process lock service or lock files on shared filesystems (local flock only).

## Design

### 1. Full-fidelity simulation snapshot + crash marker

- `capture_state_snapshot()` / the validate-only checkpoint are unified into one
  full-fidelity snapshot: deep-copy of `current_phase`, `completed_steps`, `config`,
  `errors`, `last_updated` (i.e., everything mutable). Restore returns the file to
  byte-equivalent content for both `--validate-only` and `--dry-run`.
- At capture, write `simulation_in_progress: {"mode": "dry_run"|"validate_only",
  "started_at": ...}` to the state file; clear it during restore.
- On load, a present `simulation_in_progress` marker means a simulation died without
  restoring: fail closed with a message naming the mode, start time, and remedies
  (`--reset-state`, or manual inspection). No auto-repair — the snapshot lived in the
  dead process.
- Preserves the existing property that dry-run cannot mint `COMPLETED`.

### 2. Parent-directory fsync

After `os.replace` in `_write_state`: open the containing directory read-only, fsync, close.
Failure to dir-fsync is logged at debug and non-fatal on platforms/filesystems that reject
it (best-effort hardening, matching the temp-file fsync's spirit).

### 3. Per-hub UID locks

- After `ensure_hub_identities` resolves both cluster UIDs, acquire two flock files —
  sorted UID order to avoid AB/BA deadlock — at
  `/tmp/acm-switchover-<euid>/locks/<sha256(uid)>`, opened with `O_NOFOLLOW|O_CREAT`,
  non-blocking, held until process exit.
- Contention → fatal: "another switchover is running against this hub" with the lock path.
- The window before identities resolve is read-only (preflight identity discovery), so the
  late acquisition is safe; document it.
- Existing state-file run-lock and write-lock stay unchanged. Single-context operations
  (e.g. standalone decommission) lock the one UID they have.

### 4. Reset under lock; `--force` scope

- `--reset-state` moves into StateManager: construct manager → acquire run lock → then
  reset (reinitialize state) under the lock. The pre-construction `os.remove` at
  `acm_switchover.py:1073-1083` is deleted. A concurrent run holding the lock makes reset
  fail fast instead of deleting live state.
- `--force` no longer triggers `state.reset()` on the stale-COMPLETED or
  FAILED-undeterminable paths; those paths now instruct the operator to use
  `--reset-state` explicitly. `--force` retains its other meanings (staleness override,
  legacy identity backfill).

### 5. Run contract

- On first run (state creation), persist `run_contract`: `method`,
  `activation_method`-affecting flags, `old_hub_action`, `manage_auto_import_strategy`,
  `--argocd-manage`, `--argocd-resume-after-switchover`, the Area D expectation waiver,
  `--skip-observability-checks`, and `tool_version`.
- Every resume compares the live invocation against the contract **before any mutation**;
  any difference → fatal listing each `field: recorded → requested`.
- New flag `--accept-changed-options`: re-records the contract and journals the old→new
  diff into state (audit trail). `--force` does not substitute.
- Legacy state without a contract: record one from the current invocation on first
  post-upgrade resume and continue (no retroactive failure), logged at info.

### 6. Collection parity

- `checkpoint_phase`'s existing `validate_operation_identity` gains a contract comparison
  of the role-critical variables (method, old-hub action, auto-import management, ArgoCD
  management, waiver) recorded at checkpoint creation, with an override variable mirroring
  `--accept-changed-options`.
- The simulation marker is N/A for the collection (per-task `dry_run` guards mean no
  durable writes in check mode today); noted in role docs.

## Testing

- Byte-equivalence: hash state file before/after `--validate-only` and `--dry-run`
  (marker lifecycle included) — equal.
- Simulated crash: capture marker written, process killed → next load fails closed naming
  the mode; `--reset-state` then recovers.
- `_write_state` calls dir-fsync (mock `os.fsync` counts); dir-fsync failure non-fatal.
- Two processes, same hub UIDs, different `--state-file` → second fails fast; different
  UID pairs → no contention; lock order stable under reversed context order.
- Reset: concurrent lock holder → reset fails fast, live state intact; reset under lock
  reinitializes.
- `--force` on progressed state no longer resets; message points to `--reset-state`.
- Contract: matrix over each contract field mismatch → fatal naming the field; override
  flag re-records + journals; legacy state adopts silently.
- Collection: contract comparison + override variable parity.
- Version bump per repo policy (Python + collection, synced).

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
5. Changing any contract field on resume without `--accept-changed-options` fails before
   any cluster mutation, and the override leaves an audit record.
