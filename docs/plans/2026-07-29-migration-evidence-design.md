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
4. Integrated teardown requires migration evidence bound to a stable Restore identity
   (namespace + UID) and the exact validated spec (generation + canonical fingerprint),
   live-revalidated before anything deletes the Restore; an explicit journaled waiver
   can substitute only for the expected-name predicate, never for restore identity,
   provenance, completion, post-activation completion, or teardown revalidation.

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
     restore:            # written by activation as evidence accrues (consumed by §1a, §4, §4a)
       namespace: "<restore-namespace>"       # journaled with uid at mutation time (§1 step 3)
       name: "<restore-cr-name>"              # locator only — never an identity (§1a)
       uid: null | "<metadata.uid>"           # stable object identity, journaled at mutation time (§1 step 3), verified equal at §1a
       generation: null | <metadata.generation>  # spec-version guard, journaled post-mutation (§1 step 3), verified equal at §1a
       spec_fingerprint: null | "<sha256-hex>"   # canonical fingerprint of the validated spec projection (§1a)
       backup_names_verified_at: null | "<iso8601>"  # set when the completed live Restore's spec matched the journaled backup names (§1a)
       completed_at: null | "<iso8601>"       # terminal success evidence; same durable update as the §1a bundle, or strictly last
       names_verified_at: null | "<iso8601>"  # set when expected-name check passed in activation
       teardown_revalidated_at: null | "<iso8601>"  # set only by the §4a live revalidation barrier
     post_activation:
       names_verified_at: null | "<iso8601>"  # set when post-activation name check passed
       completed_at: null | "<iso8601>"       # set last, only when every required post-activation operation succeeded
     waiver: null | {flag: "<flag/var name>", journaled_at: "<iso8601>"}
   ```

   Resume (§1 step 4) and the teardown gate (§4) consume exactly this record in both
   implementations; field names never diverge between Python and the collection.
3. Create/patch the Restore with the concrete names — `latest` never reaches a Restore
   spec. Both the full-restore and passive-activation patch paths use the same journal.
   **Identity is journaled at mutation time**: the create response (create paths) or the
   pre-patch read (passive path, `_get_restore_or_raise`) carries `metadata.uid` — the
   run durably records `restore.namespace`, `restore.name`, `restore.uid`, and the
   post-mutation `metadata.generation` (from the create/patch response; if the patch
   response omits it, from an immediate re-read whose UID must match) before treating
   the mutation as applied. A create/patch response without a readable UID fails closed.
4. Resume: reuse journaled names verbatim. If a live Restore exists whose spec disagrees
   with the journal, or whose `metadata.uid` differs from the journaled `restore.uid` →
   fail closed (someone changed or replaced the restore mid-run).

Failure to resolve (no completed backup in a category the method requires) → fatal at
activation entry, before any mutation.

#### 1a. Restore identity, spec version, and backup-provenance evidence

`restore.completed_at` alone does not prove the completed live Restore consumed the
journaled backup names, and `restore.name` alone does not prove the evidence describes
the same object later phases see. Identity semantics, first:

- `restore.name` is a **locator**, not an identity. `namespace` plus `metadata.uid` is
  the stable object identity: the API server never reuses a UID, so a
  deleted-and-recreated Restore with the same name is a **different object**.
- Evidence captured for one UID never authorizes work for another UID.
- A missing, malformed, or unreadable UID on any read fails closed.
- Resume never adopts a newly observed same-name Restore as the original transaction
  target: a live Restore whose UID differs from the journaled `restore.uid` is a
  fail-closed journal/cluster disagreement, exactly like a spec mismatch (§1 step 4).

Spec-version guard: `metadata.generation` is the Kubernetes spec-version signal — it
increments on spec changes and is untouched by status-subresource updates. This holds
only for CRDs with the status subresource enabled; the ACM cluster-backup Restore CRD
enables it, and the implementation asserts that at first use (subresource absent →
fail closed rather than silently degrading the guard to fingerprint-only).
`resourceVersion` changes on *every* write, including status updates, so it is never
used as the long-lived gate; only as an optional short-lived precondition inside a
single read-modify-write.

Canonical spec fingerprint — identical algorithm in Python and the collection:

- **Projection**: exactly `{"activation_method": "<passive|full>", "backup_fields":
  {"<restore-spec-field>": "<concrete-backup-name>", ...}}` where `backup_fields`
  contains precisely the Restore spec backup fields the selected method owns or
  consumes (the §1 per-field contract). The journal-category → spec-field mapping is
  pinned identically in both implementations: `managed_clusters` →
  `veleroManagedClustersBackupName`, `credentials` → `veleroCredentialsBackupName`,
  `resources` → `veleroResourcesBackupName`. Skipped (`VELERO_BACKUP_SKIP`) categories
  and categories absent from the journal are excluded from the projection — identically
  in both implementations. No status fields, no metadata, no timestamps, no transient
  client fields.
- **Serialization**: JSON, lexicographically sorted keys, no insignificant whitespace
  (`,`/`:` separators only), non-ASCII escaped as `\uXXXX` (Python `json.dumps`
  default `ensure_ascii=True`; the collection matches this escaping exactly).
- **Digest**: SHA-256, lowercase hex, stored as `restore.spec_fingerprint`.

The evidence bundle is produced in exactly this order:

1. Strictly GET (§3) the live Restore after it reaches its required terminal success
   phase.
2. Require the read's `namespace`, `name`, and `metadata.uid` to equal the values
   journaled at mutation time (§1 step 3), and `metadata.generation` to equal the
   journaled post-mutation generation — the object that reached terminal success is
   provably the object this run created or patched, with an unchanged spec. A UID
   mismatch means a same-name replacement: fail closed, never adopt.
3. Compare every Restore backup field consumed by the selected activation method against
   the concrete journaled value. Per-method scope is preserved exactly: passive
   activation compares only the ManagedCluster backup field it owns; skipped
   credentials/resources fields remain skipped; full restore compares every category it
   consumed; no comparison is invented for a category absent from the journal.
4. Compute the canonical spec fingerprint from the same read.
5. Persist `spec_fingerprint` and `backup_names_verified_at` — together with the
   already-journaled `namespace`, `name`, `uid`, and `generation`, whose equality step 2
   just re-asserted — as **one durable state/checkpoint update** where the form factor
   supports it.
6. Write `completed_at` in that same durable update, or strictly last after it.

Any mismatch, unreadable Restore, missing UID or expected field, unexpected `latest`
alias, generation or fingerprint inconsistency, or malformed response → fail closed; no
evidence is written. A partial record missing any identity, version, fingerprint,
provenance, or completion field is **non-terminal**: it blocks resume continuation,
finalization, Restore cleanup, and integrated teardown until re-established.

Resume repeats the live comparison — identity (UID), generation, fingerprint, and
consumed backup fields — before accepting existing completion evidence (§1 step 4's
fail-closed journal/live disagreement rule).

The teardown gate (§4) requires the complete bundle; the §2 waiver bypasses none of it.

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

The migration path adopts the strict list variant to be introduced by the decommission
completion design (working name `list_custom_resources_strict` in
`lib/kube_client.py`; it may land as a `strict=True` flag on the existing helper):
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

- **unconditionally** (the waiver cannot bypass these): the complete §1a evidence bundle
  — `restore.uid`, `restore.generation`, `restore.spec_fingerprint`,
  `restore.backup_names_verified_at`, and `restore.completed_at`, with the journaled
  `restore.namespace`/`restore.name` matching — plus `post_activation.completed_at`
  (all post-activation work completed) and `restore.teardown_revalidated_at` (the §4a
  live barrier succeeded for exactly that UID/generation/fingerprint);
- expected-name verification passed in both activation and post-activation
  (`restore.names_verified_at` and `post_activation.names_verified_at` set) — this
  predicate, and only this one, is satisfiable by the §2 waiver instead. The waiver never
  substitutes for restore identity, generation, fingerprint, provenance, completion,
  post-activation completion, or teardown revalidation.

These fields are exactly the §1 schema's `restore`/`post_activation` block, written
durably as each piece of evidence accrues. The `_decommission_old_hub` check itself
reads only this state — a state read alone cannot prove the live Restore still matched
the validated identity and spec, which is exactly why `teardown_revalidated_at` exists:
it is written only by the §4a live revalidation barrier, which runs earlier in the same
finalization pass, before anything can delete the Restore.

Anything missing → fail closed listing exactly which clusters or checks lack evidence.
Standalone decommission is unaffected (its wrong-target protection is `SSA-02`).

### 4a. Live revalidation barrier before Restore cleanup and teardown

Current ordering in both form factors deletes the Restore long before integrated
teardown: Python `finalize()` runs `enable_backup_schedule` →
`_cleanup_restore_resources()` (`modules/finalization.py:266-276`, deleting every
switchover Restore) as its first restore-touching step, while `_handle_old_hub` →
`_decommission_old_hub` comes much later; the collection mirrors this
(`roles/finalization/tasks/main.yml`: `cleanup_restores.yml` before
`handle_old_hub.yml`). A live check at teardown time is therefore impossible — the
barrier moves **ahead of that cleanup**.

Placement: at FINALIZATION, immediately before the first task that may delete or
irreversibly mutate a switchover Restore — in Python, before
`_cleanup_restore_resources()` inside the `enable_backup_schedule` step; in the
collection, before `cleanup_restores.yml`. Precondition: the §1a bundle and
`post_activation.completed_at` already exist (otherwise the barrier fails closed
immediately — incomplete evidence is non-terminal, §1a).

The barrier:

1. Uses the destination-hub client with the run's explicit kubeconfig and context (the
   same secondary-hub client identity activation used).
2. Strictly GETs (§3) the journaled `restore.namespace`/`restore.name`.
3. Requires live `metadata.uid` == journaled `restore.uid`.
4. Requires live `metadata.generation` == journaled `restore.generation`.
5. Recomputes the §1a canonical fingerprint from the live spec and requires equality
   with `restore.spec_fingerprint`.
6. Rechecks every journaled concrete backup field against the live spec (§1a scope).
7. Fails closed on absence, 404, discovery failure, malformed response, missing or
   malformed UID, replacement UID, generation drift, fingerprint drift, or any
   backup-field mismatch.
8. On success, durably records `restore.teardown_revalidated_at`, bound to the same
   UID/generation/fingerprint it just verified (the write re-asserts equality with the
   journaled triple; it never updates the triple from the new observation).

Rules:

- Restore cleanup may proceed only after successful revalidation; integrated teardown
  may proceed only after successful revalidation (enforced again by the §4 gate's
  `teardown_revalidated_at` requirement).
- The cleanup deletion of the run's own Restore is bound to the verified identity: the
  Python delete uses a server-side UID precondition
  (`V1DeleteOptions.preconditions.uid` = journaled `restore.uid`), and the collection
  deletes it through the `acm_uid_guarded_delete` module introduced by the decommission
  completion design — no name-only delete of the run's Restore remains. A precondition
  mismatch (409/412: the object was replaced between barrier and delete) fails closed:
  the replacement is left intact, cleanup and teardown stop. This closes the
  barrier→cleanup window and matches the same-PR decommission `expected_uid` contract.
  A 404 at delete time (object absent at the journaled locator with no journaled prior
  delete) is likewise fail-closed — an unexplained disappearance after the barrier, not
  a success. "The run's own Restore" is defined by the journaled `namespace`/`name`
  locator: whatever object sits there gets the UID-preconditioned delete, so a same-name
  replacement 409s instead of being routed around the guard. (Cleanup of *other*
  switchover-owned Restores found by discovery at different names keeps its existing
  archive-then-delete behavior — those objects carry no journaled evidence to protect.)
- A failed later step never fabricates or upgrades the marker; no unrelated later
  observation may replace the journaled identity.
- Resume re-runs the barrier whenever the prior marker is absent, incomplete, or
  invalidated by workflow state. Operationally: the marker stands only when the state
  shows the cleanup step verifiably completed after it (the UID-preconditioned delete
  succeeded and was journaled); in every other resume — cleanup not yet run, cleanup
  incomplete, or the run re-entered any phase before FINALIZATION — the barrier runs
  again. The object's absence after a verified, journaled cleanup is expected, not a
  failure; a crash between marker write and cleanup re-runs the barrier, and a
  replacement appearing in that window is caught by the re-run or by the delete's UID
  precondition.
- No cross-cluster or cross-store atomicity is claimed: the barrier is one strict read
  plus one durable local state write in the run's own form factor.

### 5. Collection parity

- Activation role resolves and journals concrete backup names into checkpoint
  `operational_data` before creating/patching the Restore; resume tasks reuse them.
- Expectation additivity mirrored in role defaults/asserts; count variables become an
  additional floor.
- Where the Python side enforces the name set, role `k8s_info`-based checks compare the
  same name set, not just counts.
- The waiver is a documented role variable with the same journaling semantics.
- The §1a fingerprint algorithm (projection, serialization, digest) and the §4a barrier
  are implemented identically; a fingerprint computed by one implementation over the
  same validated spec projection equals the other's.
- Persistence stays single-authority per form factor: Python writes one authoritative
  record to the Python run state; the collection writes one authoritative record to its
  checkpoint `operational_data`. A run reads only its own form factor's record. The
  implementations are independent, neither imports from the other, and no
  cross-form-factor handoff, dual-write, or commit protocol exists or is introduced —
  "shared schema" means parity-aligned field names and semantics, nothing more.

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
- Backup-provenance evidence (§1a): exact live-spec/journal match → evidence bundle
  written; one consumed field mismatch → fail closed, no evidence; missing expected
  field → fail closed; `latest` alias present in the live spec → fail closed; skipped
  credentials/resources fields remain skipped and uncompared; passive activation compares
  only the ManagedCluster field; full restore compares every consumed category;
  unreadable or malformed live Restore → fail closed; resume with journal/live-spec
  disagreement → fatal; teardown blocked when `backup_names_verified_at` is absent even
  with `completed_at` set.
- Identity (§1/§1a): UID journaled at mutation time from the create response and from
  the pre-patch read; create/patch response without a readable UID → fail closed; exact
  namespace/name/UID match → evidence written; same name with a different UID → fail
  closed, no adoption; Restore deleted and recreated between mutation and the terminal
  GET (i.e. before evidence capture) → §1a step-2 UID mismatch, fail closed; Restore
  deleted and recreated before teardown revalidation → barrier fails closed; missing
  UID → fail closed; unreadable or malformed Restore → fail closed; replacement UID is
  never automatically adopted, by capture, resume, barrier, or cleanup.
- Version/spec (§1a): exact generation and fingerprint match → passes; generation change
  after evidence capture → barrier fails closed; consumed backup field change → fail
  closed; passive single-field projection and full-restore multi-field projection each
  produce the documented fingerprint; skipped fields stay out of the projection;
  deterministic fingerprint parity — Python and collection produce identical digests for
  identical projections; a status-only update (generation unchanged) does not invalidate
  the fingerprint or the barrier; a resourceVersion-only change is not treated as a spec
  mutation.
- Ordering (§1a/§4a): `completed_at` cannot exist without the full
  identity/version/provenance bundle; `teardown_revalidated_at` cannot exist without an
  exact live match; Restore cleanup refuses to run before successful revalidation; the
  cleanup delete of the run's own Restore carries the UID precondition and a 409/412
  mismatch fails closed with the replacement intact; integrated teardown refuses to run
  without `teardown_revalidated_at`; resume
  revalidates stale or incomplete evidence; failure after name verification but before
  completion stays blocked; the waiver bypasses none of UID, generation, fingerprint,
  provenance, completion, or teardown revalidation.
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
   restore-completion (`restore.completed_at`), post-activation-completion
   (`post_activation.completed_at`), or teardown-revalidation
   (`restore.teardown_revalidated_at`) evidence fails closed regardless of the waiver;
   the waiver substitutes only for expected-name verification.
6. Completion and provenance evidence is bound to the live Restore's namespace + UID and
   to its exact validated spec (generation + canonical fingerprint); a same-name
   replacement Restore can never inherit another object's evidence — at capture, on
   resume, at the §4a barrier, and at the UID-preconditioned cleanup delete. A spec
   change after validation is caught up to and including the barrier and the
   preconditioned delete; Restore objects created *after* the run's own Restore is
   verifiably cleaned up are outside this contract (explicit non-goal — they carry none
   of this run's evidence).
