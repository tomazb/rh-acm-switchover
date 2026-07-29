# ManagedCluster Migration Evidence — Design

**Date:** 2026-07-29
**Branch:** `ansible` (spec branch `docs/thermos-safety-specs`)
**Status:** approved design, awaiting implementation plan
**Origin:** cross-validation of main-branch safety specs against `ansible` @ `0bf55db9`,
independently revalidated (Codex). Untracked in `thermos-resolution-plan.md` (`F19` is a
deduplication refactor; `R2-M2` is crash-revalidation of passive Restore readiness; the
Phase 9 `TR2D-03` freshness language does not define these runtime requirements).

## Problem

The branch already enforces preflight-derived ManagedCluster *name* parity
(`expected_managed_cluster_names`, enforced in both restore paths and post-activation).
Four gaps remain:

1. **Restores bind to the moving `latest` alias.** `modules/activation.py:339-350` (restore
   create), `:453` (passive patch), `:793-804` (full restore) all set
   `veleroManagedClustersBackupName: latest` (and credentials/resources). Which backup was
   actually consumed is never recorded; a backup created between preflight validation and
   activation silently changes the restore source; resume re-resolves the alias.
2. **Explicit count weakens enforcement.** `acm_switchover.py:869-875`: an explicit
   `--min-managed-clusters` value replaces name enforcement with a count-only check, and an
   explicit `0` disables enforcement entirely (`modules/activation.py:1082-1089` then
   labels the count check informational).
3. **404 → `[]` empty baselines.** `lib/kube_client.py:724-748` maps API 404 to an empty
   list for every inventory read; a missing/broken discovery during activation or
   post-activation reads as "zero clusters" instead of an error.
4. **Teardown ignores evidence.** Integrated decommission
   (`modules/finalization.py:1140-1143`) receives neither the expected names nor any
   restore evidence; a partially-failed migration can be followed by source-hub teardown.

## Goals

1. Every restore is bound to concrete, journaled backup names — provenance is knowable
   and stable across resume.
2. Name enforcement cannot be silently weakened by count arguments.
3. Inventory reads on the migration path distinguish empty from missing-API.
4. Integrated teardown requires migration evidence; an explicit journaled waiver can
   substitute only for the expected-name predicate, never for restore provenance,
   restore completion, or post-activation completion.

## Non-goals

- Backup content inspection, UID-level cluster inventory, or BSL configuration comparison
  (main-branch spec scope; not carried here — name-set + frozen backup names is the
  ansible-branch contract).
- Creating dedicated run-owned Backup resources (existing backup schedule output is used).
- Standalone decommission target identity: planned `SSA-02`.

## Design

### 1. Freeze backup names at activation entry

At activation start — after PRIMARY_PREP has paused the BackupSchedule, so the newest
backup is final:

1. List Velero backups (strict read, §3) and resolve the alias once — **only for the
   fields the method actually sets**. The per-field Restore contract is preserved
   exactly: the passive patch path changes only `veleroManagedClustersBackupName`
   (credentials/resources fields untouched), and restore-create paths keep
   `VELERO_BACKUP_SKIP` on any field the current code skips — this design substitutes
   concrete names for `latest`, it never widens restore scope. Where multiple categories
   are consumed (full restore), all resolved names must come from the same backup
   generation (same BackupSchedule run, matched by the schedule's name-timestamp); mixed
   generations → fatal at activation entry.
2. Journal to state **before** any Restore mutation (intent-first, consistent with the
   auto-import design), using one canonical versioned schema shared verbatim by the Python
   state key and the collection checkpoint `operational_data` entry:

   ```yaml
   migration_backups:
     schema_version: 1
     resolved_at: "<iso8601>"
     backups:            # only categories the method consumes; skipped fields absent
       managed_clusters: {name: "<backup>", completed_at: "<iso8601>"}
       credentials:      {name: "<backup>", completed_at: "<iso8601>"}
       resources:        {name: "<backup>", completed_at: "<iso8601>"}
     restore:            # written by activation as evidence accrues (§4 is a pure read of this record's restore/post_activation/waiver fields)
       name: "<restore-cr-name>"
       backup_names_verified_at: null | "<iso8601>"  # set when the completed live Restore's spec matched the journaled backup names (§1a)
       completed_at: null | "<iso8601>"       # set when Restore reached its terminal success phase, last field written, after backup_names_verified_at
       names_verified_at: null | "<iso8601>"  # set when expected-name check passed in activation
     post_activation:
       names_verified_at: null | "<iso8601>"  # set when post-activation name check passed
       completed_at: null | "<iso8601>"       # set last, only when every required post-activation operation succeeded
     waiver: null | {flag: "<flag/var name>", journaled_at: "<iso8601>"}
   ```

   Resume (§1.4) and the teardown gate (§4) consume exactly this record in both
   implementations; field names never diverge between Python and the collection.
3. Create/patch the Restore with the concrete names — `latest` never reaches a Restore
   spec. Both the full-restore and passive-activation patch paths use the same journal.
4. Resume: reuse journaled names verbatim. If a live Restore exists whose spec disagrees
   with the journal → fail closed (someone changed the restore source mid-run).

Failure to resolve (no completed backup in a category the method requires) → fatal at
activation entry, before any mutation.

#### 1a. Restore backup-provenance evidence

`restore.completed_at` alone does not prove the completed live Restore consumed the
journaled backup names; `restore.backup_names_verified_at` records that proof, produced
in exactly this order:

1. Observe the live Restore reaching its required terminal success phase.
2. Re-read the live Restore.
3. Compare every Restore backup field consumed by the selected activation method against
   the concrete journaled value.
4. Per-method scope is preserved exactly: passive activation compares only the
   ManagedCluster backup field; skipped credentials/resources fields remain skipped; full
   restore compares every category it consumed; no comparison is required for a category
   absent from the journal.
5. Any mismatch, unreadable Restore, missing expected field, unexpected `latest` alias,
   or malformed response → fail closed; no evidence is written.
6. Only after the comparison succeeds, durably record `backup_names_verified_at` and
   `completed_at` — preferably in one durable state update; otherwise `completed_at` is
   the last field written, after `backup_names_verified_at`.
7. Resume repeats the live-spec comparison (§1.4's fail-closed journal/live disagreement
   rule) before accepting existing completion evidence.

The teardown gate (§4) requires both `restore.backup_names_verified_at` and
`restore.completed_at`; the §2 waiver bypasses neither.

### 2. Additive expectations

- The preflight-derived name set is enforced whenever available (both restore methods and
  post-activation — unchanged call sites).
- Explicit `--min-managed-clusters N` becomes an **additional floor** checked alongside
  names, no longer a replacement. Explicit `0` keeps name enforcement.
- Disabling name enforcement requires a new explicit flag
  (`--skip-managed-cluster-expectations`; final name at implementation), which is:
  - journaled in state as a waiver (who-asked-for-it evidence),
  - rejected by validation when no expectation exists to waive,
  - never used to rewrite the recorded expectation — the discovery fact stays intact
    (waiver ≠ evidence).
- Restore-only mode keeps its existing default floor of one.

### 3. Strict inventory reads

The migration path adopts the strict list variant introduced by the decommission
completion design (`list_custom_resources_strict` in `lib/kube_client.py`):
ManagedCluster inventory reads in activation and post-activation, and the Velero backup
list in §1. API-group/resource 404 → typed fatal error; genuine empty list → normal
result (then judged against expectations).

### 4. Evidence gate before integrated teardown

`post_activation.completed_at` is the final post-activation completion marker: it is
written last, only after every required post-activation operation — including
expected-name verification — has succeeded; it is never written when any later
post-activation operation fails; and it must be durably written before finalization or
integrated teardown may rely on it. `post_activation.names_verified_at` alone proves
only the name check, not that later post-activation work completed.

`_decommission_old_hub` entry requires, from the run's own state:

- **unconditionally** (the waiver cannot bypass these): `restore.backup_names_verified_at`
  is set (§1a — the completed Restore provably consumed the exact journaled backup
  names), `restore.completed_at` is set and the journaled `restore.name` matches, and
  `post_activation.completed_at` is set (all post-activation work completed);
- expected-name verification passed in both activation and post-activation
  (`restore.names_verified_at` and `post_activation.names_verified_at` set) — this
  predicate, and only this one, is satisfiable by the §2 waiver instead. The waiver never
  substitutes for restore provenance, restore completion, or post-activation completion.

These fields are exactly the §1 schema's `restore`/`post_activation` block, written
durably as each piece of evidence accrues — the gate stays a pure state read.

Anything missing → fail closed listing exactly which clusters or checks lack evidence.
Pure state read — no new cluster calls. Standalone decommission is unaffected (its
wrong-target protection is `SSA-02`).

### 5. Collection parity

- Activation role resolves and journals concrete backup names into checkpoint
  `operational_data` before creating/patching the Restore; resume tasks reuse them.
- Expectation additivity mirrored in role defaults/asserts; count variables become an
  additional floor.
- Where the Python side enforces the name set, role `k8s_info`-based checks compare the
  same name set, not just counts.
- The waiver is a documented role variable with the same journaling semantics.

## Testing

- Freeze: journal written before Restore mutation; Restore spec contains concrete names;
  `latest` absent from every created/patched Restore body.
- Resume: live `latest` moved → journaled names still used; live Restore spec mismatch →
  fatal.
- No completed backup in a required category → fatal before mutation.
- Additivity: names pass + floor fails → fatal; explicit `0` + missing expected name →
  fatal; waiver flag → passes with journaled waiver; waiver without expectations →
  validation error.
- Strict reads: discovery 404 during activation inventory → fatal, not zero-cluster
  success.
- Backup-provenance evidence (§1a): exact live-spec/journal match → both fields written;
  one consumed field mismatch → fail closed, no evidence; missing expected field → fail
  closed; `latest` alias present in the live spec → fail closed; skipped
  credentials/resources fields remain skipped and uncompared; passive activation compares
  only the ManagedCluster field; full restore compares every consumed category;
  unreadable or malformed live Restore → fail closed; resume with journal/live-spec
  disagreement → fatal; teardown blocked when `backup_names_verified_at` is absent even
  with `completed_at` set.
- Post-activation completion marker: all post-activation work succeeds → `completed_at`
  written last; name verification succeeds but a later post-activation operation fails →
  `completed_at` absent and teardown blocked; `completed_at` written only after all work;
  waiver present but `completed_at` missing → still blocks; resume preserves or
  revalidates the marker per the state/resume contract.
- Teardown gate: blocked without evidence; passes with full evidence; waiver satisfies
  only the name predicate (waiver + missing restore-provenance, restore-completion, or
  post-activation-completion evidence still blocks); message names missing pieces.
- Collection parity for journal shape, additivity, and gate.
- Version bump per repo policy (Python + collection, synced).

## Tracker updates (same PR)

| id | severity | summary |
| --- | --- | --- |
| new-D1 | High | Restores bound to moving `latest` alias; consumed backup never journaled; resume re-resolves |
| new-D2 | Medium | Explicit `--min-managed-clusters` drops name enforcement; explicit 0 disables it |
| new-D3 | Medium | 404→[] yields empty baselines on activation/post-activation inventory reads |
| new-D4 | Medium | Integrated teardown consumes no migration evidence |

Plus one planned slice row referencing this design. `F19`, `R2-M2`, `SSA-02`
cross-referenced as adjacent.

## Acceptance criteria

1. Every Restore created by the tool names concrete backups, and state records which.
2. A backup appearing mid-run cannot silently change the restore source.
3. No combination of count arguments weakens name enforcement without the explicit waiver
   flag, and the waiver is journaled.
4. A discovery failure can never be read as an empty cluster inventory on the migration
   path.
5. Integrated teardown without restore-provenance (`restore.backup_names_verified_at`),
   restore-completion (`restore.completed_at`), or post-activation-completion
   (`post_activation.completed_at`) evidence fails closed regardless of the waiver; the
   waiver substitutes only for expected-name verification.
