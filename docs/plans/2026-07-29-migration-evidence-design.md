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
   (namespace + UID) and the exact validated spec (generation + canonical fingerprint).
   Restore cleanup is a durable, recoverable transaction: intent precedes deletion,
   passive patching is atomically conditional on UID and resourceVersion, the final
   cleanup DELETE is conditional on the UID and resourceVersion from its immediately
   preceding validated GET, and unexplained absence never becomes success automatically.
   An explicit journaled waiver can substitute only for the expected-name predicate,
   never for restore identity, provenance, completion, post-activation completion,
   cleanup recovery, or teardown revalidation.

## Non-goals

- Backup content inspection, UID-level cluster inventory, or BSL configuration comparison
  (main-branch spec scope; not carried here — name-set + frozen backup names is the
  ansible-branch contract).
- Creating dedicated run-owned Backup resources (existing backup schedule output is used).
- Standalone decommission target identity: planned `SSA-02`.
- Cross-form-factor transactions or journal handoff. A Python run writes only Python
  state and a collection run writes only collection checkpoint `operational_data`; the
  schemas and transitions are parity-identical, but the stores remain independent.
- Treating a same-name Restore created after this run's verified cleanup as this run's
  object. Such an object is unowned by this transaction, is never deleted or adopted by
  its evidence, and blocks any remaining finalization gate that observes it live (§4
  states the exact instant-of-read scope of the final locator check).

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
     schema_version: 2
     run_id: "<uuid>"      # non-empty immutable migration-journal identity
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
       activation_method: "passive" | "full"  # immutable evidence-scope method, persisted before mutation
       backup_fields:                         # passive example; full has all three keys defined below
         veleroManagedClustersBackupName: "<concrete-backup-name>"
       spec_fingerprint: null | "<sha256-hex>"   # canonical fingerprint of the validated spec projection (§1a)
       backup_names_verified_at: null | "<iso8601>"  # set when the completed live Restore's spec matched the journaled backup names (§1a)
       completed_at: null | "<iso8601>"       # terminal success evidence; same durable update as the §1a bundle, or strictly last
       names_verified_at: null | "<iso8601>"  # set when expected-name check passed in activation
       teardown_revalidated_at: null | "<iso8601>"  # set only by the §4a live revalidation barrier
     cleanup:
       operation_id: null | "<uuid>"
       state: "not_started" | "intent_persisted" | "delete_accepted" | "recovery_required" | "completed" | "repaired"
       namespace: null | "<restore-namespace>"
       name: null | "<restore-cr-name>"
       uid: null | "<metadata.uid>"
       generation: null | <metadata.generation>
       activation_method: null | "passive" | "full"
       spec_fingerprint: null | "<sha256-hex>"
       backup_fields: {}  # exact structural copy of restore.backup_fields
       intent_at: null | "<iso8601>"
       final_get_resource_version: null | "<metadata.resourceVersion>"
       delete_accepted_at: null | "<iso8601>"
       absence_verified_at: null | "<iso8601>"
       completed_at: null | "<iso8601>"
       recovery: null | {required_at: "<iso8601>", reason_code: "<stable-code>", observed_uid: null | "<uid>", observed_resource_version: null | "<resourceVersion>"}
       repair: null | {actor: "<non-empty>", acknowledged_at: "<iso8601>", reason: "<non-empty>", run_id: "<uuid>", operation_id: "<uuid>", inspected_evidence: ["<non-empty reference>", "..."]}
     post_activation:
       names_verified_at: null | "<iso8601>"  # set when post-activation name check passed
       completed_at: null | "<iso8601>"       # set last, only when every required post-activation operation succeeded
     waiver: null | {flag: "<flag/var name>", journaled_at: "<iso8601>"}
   ```

   `run_id`, `cleanup.operation_id`, and all timestamps are strings;
   `restore.generation`/`cleanup.generation` are integers;
   `restore.backup_fields`/`cleanup.backup_fields` are string-to-string mappings;
   `cleanup.recovery` and `cleanup.repair` are either null or
   complete objects of the shown shape. Null is valid only where explicitly shown.
   Empty strings, missing keys in a non-null recovery/repair object, unknown states,
   timestamps that do not parse as ISO 8601, non-string backup field keys/values, or
   cleanup identity fields that disagree with `restore` make the record malformed and
   blocking.

   `backup_fields` is defined exactly once, in `restore.backup_fields`. The allowed
   journal category keys and their one-to-one Restore spec-field projection are:

   | `backups` category | `restore.backup_fields` key | consumed by |
   | --- | --- | --- |
   | `managed_clusters` | `veleroManagedClustersBackupName` | passive and full |
   | `credentials` | `veleroCredentialsBackupName` | full only |
   | `resources` | `veleroResourcesBackupName` | full only |

   No other category or spec-field key is allowed. Each value is the exact non-empty
   Kubernetes/Velero Backup `metadata.name` string captured in the corresponding
   `backups.<category>.name`; it must be a concrete valid resource name and must not be
   `latest`, `skip`, or any other alias/sentinel. The map is logically unordered; its
   canonical byte representation is the sorted-key JSON serialization defined in §1a.

   `restore.activation_method` is the immutable evidence-scope enum `passive` or `full`,
   derived once from the validated operation `method`. It is distinct from the lower-level
   CLI/collection `activation_method` option (`patch` or `restore`): both passive mutation
   mechanisms consume the same single-category backup projection, while their different
   mutation/identity mechanics remain covered by §1b and the Restore identity fields.
   The projections are exact:

   - passive: `restore.backup_fields` contains only
     `veleroManagedClustersBackupName`, copied from
     `backups.managed_clusters.name`;
   - full: it contains all three mapped fields, copied from all three journal categories;
     no full-owned category may use `VELERO_BACKUP_SKIP`; any required category whose
     frozen value is absent, skipped, or non-concrete is fatal before mutation;
   - a category absent from `backups` is excluded when the selected method does not
     consume it, but absence of a category required by the selected method is fatal before
     mutation;
   - a live Restore field deliberately set to `VELERO_BACKUP_SKIP` is checked as skipped
     but is never put in `backups` or either `backup_fields` map. Passive restore-create
     therefore retains the credentials/resources skip values in the live spec while its
     evidence map remains the one ManagedCluster field.

   Activation derives this map once from the already frozen categories and durably writes
   `restore.activation_method` plus `restore.backup_fields` in the same pre-mutation
   evidence update. All create/patch bodies and later validation consume that stored map;
   no resume re-derives it from live state. `cleanup.backup_fields` is not independently
   derived: cleanup-intent creation deep-copies `restore.backup_fields` structurally
   identically, and copies `restore.activation_method` into
   `cleanup.activation_method`. The canonical serialized bytes and key/value sets must be
   identical. Cleanup creation and every resume reject any key, value, method-scope, or
   canonical-byte/digest disagreement. Neither map ever contains `latest` or a skipped
   field.

   `cleanup.recovery.reason_code` is one of `absent_without_completion`,
   `replacement_uid`, or `replacement_during_poll`. `observed_uid` and
   `observed_resource_version` are populated only when a complete safe response supplied
   them; otherwise they remain null. Unknown reason codes are malformed.

   State invariants are exact:

   - `not_started`: operation/target/timestamp fields and `cleanup.activation_method` are
     null, `cleanup.backup_fields` is empty, and recovery/repair are null.
   - `intent_persisted`: operation/target/spec/backup fields and `intent_at` are complete;
     accepted/absence/completion fields and recovery/repair are null.
   - `delete_accepted`: all intent fields plus `final_get_resource_version` and
     `delete_accepted_at` are complete; absence/completion and recovery/repair are null.
   - `recovery_required`: all intent fields and the complete `recovery` object exist;
     accepted fields exist if and only if that transition followed an accepted DELETE;
     `absence_verified_at`, `completed_at`, and repair are null.
   - `completed`: all intent, accepted-delete, final resourceVersion, absence, and
     completion fields are complete; recovery/repair are null.
   - `repaired`: all intent and recovery fields plus the complete matching `repair`
     object exist; `absence_verified_at` remains null, and it does not synthesize
     accepted-delete or completion timestamps.

   `cleanup.operation_id` is null only in `not_started`. The transition to
   `intent_persisted` creates it once and copies `namespace`, `name`, `uid`,
   `generation`, `activation_method`, `spec_fingerprint`, and the complete
   `restore.backup_fields` map from the already complete §1a evidence bundle.
   `cleanup.backup_fields` must be structurally identical to
   `restore.backup_fields`; this is model A (explicit evidence plus copy), not a second
   derivation. Those values and `run_id` are immutable for the lifetime of the journal.
   No resume path mints a new operation ID, re-derives a field map, or refreshes them from
   a later object.

   Resume (§4a) and the teardown gate (§4) consume exactly this record in both
   implementations; field names, types, invariants, and allowed transitions never
   diverge between Python and the collection.
3. Create/patch the Restore with the exact pre-persisted
   `restore.backup_fields` concrete names — `latest` never reaches a Restore
   spec. Both the full-restore and passive-activation patch paths use the same journal;
   passive activation uses the atomic conditional boundary in §1b, never a name-only
   patch plus a post-patch UID check.
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
used as the long-lived evidence gate. It is mandatory as the short-lived concurrency
precondition in the §1b patch and the final §4a GET→DELETE attempt.

Canonical spec fingerprint — identical algorithm in Python and the collection:

- **Projection**: exactly
  `{"activation_method": "<restore.activation_method>",
  "backup_fields": <restore.backup_fields>}`. It consumes the explicit immutable map
  already persisted before mutation; it does not reconstruct one from the live Restore
  or cleanup state. The allowed categories/fields, passive/full key sets, skipped/absent
  treatment, and concrete-name rules are exactly the §1 table above. No status fields,
  metadata, timestamps, transient client fields, skipped sentinel, or absent category
  enter the projection.
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
3. Require the exact `restore.activation_method` and `restore.backup_fields` key set for
   the journaled categories, then compare every map entry against the same live Restore
   spec field. Per-method scope is preserved exactly: passive
   activation compares only the ManagedCluster backup field it owns; skipped
   credentials/resources fields remain skipped; full restore compares every category it
   consumed; no comparison is invented for a category absent from the journal.
4. Compute the canonical spec fingerprint from the persisted activation method and map
   after the exact live comparison.
5. Persist `spec_fingerprint` and `backup_names_verified_at` — together with the
   already-journaled `namespace`, `name`, `uid`, and `generation`, whose equality step 2
   just re-asserted — durably, using exactly one of the two allowed protocols:
   **one atomic durable state/checkpoint update** carrying the complete bundle, or an
   **explicit ordered sequence of durable writes** whose terminal field
   (`completed_at`, step 6) is written strictly last. No conditional or best-effort
   variant exists; these are mandatory durable writes in the authoritative
   per-form-factor store.
6. Write `completed_at` inside that same atomic update, or as the strictly last
   durable write of the ordered sequence. Under either protocol, a bundle missing any
   field is the non-terminal partial record defined below and blocks.

Any map with a missing/extra key, missing/empty/non-string value, wrong method scope,
skipped or absent category drift, unreadable Restore, missing UID or expected field,
unexpected `latest`/`skip` value, generation or fingerprint inconsistency, or malformed
response → fail closed; no completion evidence is written. A partial record missing any
identity, version, activation method, explicit backup map, fingerprint, provenance, or
completion field is **non-terminal**: it blocks resume continuation, finalization,
Restore cleanup, and integrated teardown until re-established.

Resume first validates the exact method-specific key set and canonical digest
relationship inside the journal, then repeats the live comparison — identity (UID),
generation, `restore.activation_method`, exact `restore.backup_fields`, fingerprint, and
consumed fields — before accepting existing completion evidence (§1 step 4's fail-closed
journal/live disagreement rule).

The teardown gate (§4) requires the complete bundle; the §2 waiver bypasses none of it.

#### 1b. Atomic passive-Restore patch boundary

The current passive path performs a name-only merge patch and checks
`resourceVersion` afterward. That post-check can detect a bad outcome, but it cannot
prevent the patch from mutating a same-name replacement. R4-04 replaces it in both form
factors with this server-enforced boundary:

1. Strictly GET the selected passive Restore through the explicitly selected destination
   kubeconfig and context. Distinguish a true object 404 from API discovery, transport,
   authentication, authorization, or malformed-response failure; all are fatal before
   mutation, but are reported with distinct stable public reason codes.
2. Require non-empty string `metadata.uid` and `metadata.resourceVersion`, validate the
   passive Restore's readiness and owned pre-patch fields, and durably bind
   `restore.namespace`, `restore.name`, and `restore.uid` before mutation.
3. Submit one RFC 6902 JSON Patch with `Content-Type:
   application/json-patch+json`. Its first two operations are `test` operations against
   `/metadata/uid` and `/metadata/resourceVersion` using the exact values from step 2;
   only after both tests does an `add` operation set
   `/spec/veleroManagedClustersBackupName` to the concrete journaled backup. `add` is
   intentional because RFC 6902 defines it to replace an existing object member or add a
   missing one. The patch contains no credentials or resources mutation.
4. A failed test or conditional conflict is a no-mutation result for the whole atomic
   PATCH. Treat structured precondition/test failures reported as 409, 412, 422, or the
   server-equivalent conflict outcome as a conflict, not as success. Other API failures
   retain their actual classification. Never fall back to merge patch or a surrounding
   GET plus name-only PATCH.
5. Conflict handling is bounded by one shared implementation constant. Every retry
   starts again at step 1. A different UID fails immediately and preserves the
   replacement. For the same UID, a changed generation or changed owned pre-patch field
   fails closed; a resourceVersion-only change (for example, status activity) may be
   retried only after the complete step-2 validation. Exhaustion fails closed. No retry
   silently loops or reclassifies a conflict as an applied patch.
6. After an accepted PATCH, strictly re-read the Restore through the same kubeconfig and
   context. Require the journaled UID; for an actual field change, require an integer
   generation greater than the validated pre-patch generation and equal to any generation
   returned by the PATCH response; require the concrete ManagedCluster backup field,
   unchanged skipped credentials/resources fields, and the canonical fingerprint. Store
   that verified post-patch generation as `restore.generation`; only then durably write
   the applied §1a evidence. A missing or malformed response, post-patch UID mismatch,
   generation/fingerprint mismatch, or unexpected owned-field value fails closed and
   writes no applied evidence.

Kubernetes documents JSON Patch as a conditional PATCH mechanism, and RFC 6902 makes a
failed `test` fail the atomic HTTP PATCH. The repository's dependency floor is
`kubernetes>=28.0.0`; the generated v28.1 `CustomObjectsApi` custom-resource PATCH
method hard-codes `application/merge-patch+json` and rejects an unknown content-type
keyword. The Python implementation therefore adds a narrowly owned KubeClient helper
that uses the same per-instance configured `ApiClient` and exact custom-resource path to
submit JSON Patch with the required content type and request timeout. It does not change
the general merge-patch helper. The collection adds a collection-owned guarded-patch
module and module_utils helper with the same wire contract; `kubernetes.core.k8s` is not
used as a substitute for this conditional boundary.

Dry-run/check-mode and reporting contract:

- Python dry-run and native Ansible check mode perform the strict read and validation,
  compute whether the owned field would change, and submit no PATCH. The low-level
  helper/module reports `changed: false` and `would_change: true|false`; the surrounding
  preview may aggregate `would_change` into its existing planned-change summary but must
  not claim a mutation occurred or persist authoritative journal transitions.
- Execute mode returns `changed: false` when the exact desired value is already present
  and the complete post-state verification succeeds; `changed: true` only after this
  invocation's PATCH was accepted and verified. Conflict/failure never reports a
  successful change.
- Inputs and results name only sanitized namespace/name, stable reason code, status, and
  attempt count. Kubeconfig paths, contexts where policy treats them as sensitive,
  bearer tokens, authorization headers, raw patch bodies, raw API response bodies, and
  rendered client exceptions are never logged or returned. The collection task invoking
  the module uses `no_log: true` defensively while publishing only the sanitized result.

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
expected-name verification, or, for that name check only, the journaled §2 waiver (the
sole waivable operation; a waived check is recorded as waived, never as succeeded) —
has succeeded; it is never written when any later
post-activation operation fails; and it must be durably written before finalization or
integrated teardown may rely on it. `post_activation.names_verified_at` alone proves
only the name check, not that later post-activation work completed.

`_decommission_old_hub` entry requires, from the run's own state:

- **unconditionally** (the waiver cannot bypass these): the complete §1a evidence bundle
  — `restore.uid`, `restore.generation`, `restore.activation_method`, the exact
  method-scoped `restore.backup_fields`, `restore.spec_fingerprint`,
  `restore.backup_names_verified_at`, and `restore.completed_at`, with the journaled
  `restore.namespace`/`restore.name` matching — plus `post_activation.completed_at`
  (all post-activation work completed) and `restore.teardown_revalidated_at` (the §4a
  live barrier succeeded for exactly that UID/generation/fingerprint), plus an internally
  consistent `cleanup.state` of `completed` or `repaired` for the same
  `run_id`/`operation_id`/identity/spec/backup-field tuple and a final strict read proving
  that no object currently occupies the journaled locator;
- expected-name verification passed in both activation and post-activation
  (`restore.names_verified_at` and `post_activation.names_verified_at` set) — this
  predicate, and only this one, is satisfiable by the §2 waiver instead. The waiver never
  substitutes for restore identity, generation, fingerprint, provenance, completion,
  post-activation completion, cleanup completion/recovery, final locator absence, or
  teardown revalidation.

These fields are exactly the §1 schema's `restore`/`post_activation` block, written
durably as each piece of evidence accrues. The `_decommission_old_hub` check itself
reads only this state, with exactly one exception: the final strict locator-absence
check in the unconditional list above is one live strict GET performed at gate time
(its result is consumed directly and never back-fills the journal); every other
predicate is a pure state read. A state read alone cannot prove the live Restore still matched
the validated identity and spec, which is exactly why `teardown_revalidated_at` exists:
it is written only by the §4a live revalidation barrier, which runs earlier in the same
finalization pass, before anything can delete the Restore.

Anything missing or internally inconsistent → fail closed listing exactly which clusters
or checks lack evidence. A `recovery_required` cleanup state is always blocking. A
completed/repaired record followed by a live same-name replacement does not transfer the
old cleanup evidence: the replacement is preserved and, when it is observable at or
before the gate's read, the final locator-absence gate blocks finalization and
integrated teardown.

The scope of that guarantee is stated precisely. The final locator-absence check is one
live strict GET, so it proves absence **at the instant of that read only**. No
destination-side reservation, lock, admission control, or serialization of the journaled
locator against integrated teardown is claimed or designed here: a same-name Restore
created after the read returns and before teardown proceeds is outside this gate's
guarantee. What the gate does guarantee is that stale completed/repaired evidence never
substitutes for a fresh live check, and that any replacement present at the check
blocks. The UID-bound cleanup transaction (§4a) closes the deletion-path identity and
version windows; it does not extend to this post-check interval. Narrowing that
remaining window would require a destination-side reservation mechanism, which is
outside this design's scope.
Standalone decommission is unaffected (its wrong-target protection is `SSA-02`).

### 4a. Live revalidation and durable Restore-cleanup transaction

Current ordering in both form factors deletes the Restore long before integrated
teardown: Python `finalize()` runs `enable_backup_schedule` →
`_cleanup_restore_resources()` (`modules/finalization.py:266-276`, deleting every
switchover Restore) as its first restore-touching step, while `_handle_old_hub` →
`_decommission_old_hub` comes much later; the collection mirrors this
(`roles/finalization/tasks/main.yml`: `cleanup_restores.yml` before
`handle_old_hub.yml`). The evidence barrier and transaction therefore move **ahead of
that cleanup**.

Placement: at FINALIZATION, immediately before the first task that may delete or
irreversibly mutate a switchover Restore — in Python, before
`_cleanup_restore_resources()` inside the `enable_backup_schedule` step; in the
collection, before `cleanup_restores.yml`. Precondition: the complete §1a bundle and
`post_activation.completed_at` already exist. Missing or partial evidence fails closed
before cleanup intent.

#### Evidence barrier

Before creating cleanup intent, the barrier:

1. Uses the destination-hub client with the run's explicit kubeconfig and context (the
   same secondary-hub client identity activation used).
2. Strictly GETs (§3) the journaled `restore.namespace`/`restore.name`.
3. Requires the live namespace/name locator and `metadata.uid` to equal the journal.
4. Requires live `metadata.generation` to equal the journaled generation.
5. Validates the exact method-specific key set, values, canonical serialization, and
   digest relationship of the persisted `restore.activation_method` and
   `restore.backup_fields`, then rechecks every concrete field in that map against the
   live Restore and rejects any
   unexpected `latest` alias, and requires skipped fields to retain their documented
   `VELERO_BACKUP_SKIP`/untouched semantics. Only after those live comparisons succeed
   does it compute the §1a fingerprint from the persisted method/map pair and require
   equality with `restore.spec_fingerprint`; it never derives the fingerprint input from
   live spec data. Because cleanup is still `not_started`, it
   also requires `cleanup.activation_method` to be null and `cleanup.backup_fields` to be
   empty. The immediately following intent builder deep-copies the restore method/map,
   verifies their exact structural and canonical-byte equality in the candidate record,
   and only then writes `intent_persisted`. Resumes with an existing intent use the
   restore/cleanup equality checks in the resume and final-boundary rules below.
6. Fails closed on absence/404, discovery/read failure, incomplete or malformed response,
   missing identity/version fields, replacement UID, generation/fingerprint drift, or
   backup-field mismatch.
7. On success, durably records `restore.teardown_revalidated_at`, bound to the same
   UID/generation/fingerprint it just verified. It never updates the journaled target
   from the new observation.

This is an evidence milestone, not the final concurrency boundary. It authorizes the
creation of cleanup intent but does not claim that a UID-only DELETE protects against a
same-UID spec change after the barrier.

#### Cleanup state machine and durability boundaries

The authoritative `migration_backups.cleanup` record has these allowed transitions:

| From | To | Durable write and gate meaning |
| --- | --- | --- |
| `not_started` | `intent_persisted` | One critical durable update creates immutable `operation_id`, copies the complete target identity/spec/backup fields, and writes `intent_at` **before** DELETE. Until that write succeeds, no DELETE is sent. |
| `intent_persisted` | `delete_accepted` | Only after this process receives an accepted response to the UID+resourceVersion DELETE; write the request's `final_get_resource_version` and `delete_accepted_at`. This record is audit evidence, not cleanup completion. |
| `delete_accepted` | `delete_accepted` | Re-entry for a resumed guarded retry (resume rule 2) whose new DELETE attempt receives a new accepted response: durably rewrite `final_get_resource_version` and `delete_accepted_at` under the same immutable `operation_id`, identity, spec, and backup fields. These two fields are the only permitted change; this re-entry is the sole exception to byte-for-byte idempotence. |
| `delete_accepted` | `completed` | Only in the uninterrupted attempt that received the most recent accepted response, after bounded absence polling and the final strict absence/replacement check; write `absence_verified_at` and `completed_at` together. |
| `intent_persisted` or `delete_accepted` | `recovery_required` | A resume sees absence without completion or a different UID, or the uninterrupted delete attempt sees a replacement during polling/final verification. Write the complete `recovery` object when the local journal is writable; never mutate the cluster while recording it. |
| `recovery_required` | `repaired` | Only an explicit operator acknowledgement satisfying the repair contract below; write the complete `repair` object atomically. |

All other transitions are invalid. `completed` and `repaired` are terminal; no
automatic transition leaves either state and neither may be rewritten for another
object. Repeating the same valid durable write is idempotent only when every field is
byte-for-byte equal. A partial record is not forward-compatible evidence: it blocks
cleanup, finalization, BackupSchedule enablement, and integrated teardown.

"Durable" means the form factor's critical persistence primitive returned success after
the atomic replace and required file/directory synchronization. Python writes the
transition only to its `StateManager` state and forces the critical flush; the collection
writes it only to checkpoint `operational_data` through an explicit mid-phase journal
update, not a phase `pass`/`fail` shortcut. Execute mode must fail before mutation when
that authoritative store is disabled, unwritable, or cannot acknowledge the write.
Dry-run/check mode writes no authoritative transition. If the parent-directory durability
work specified by R4-05 has not landed first, R4-04 must include that narrow durability
primitive for these writes or be sequenced after it; a buffered in-memory fact is not
cleanup intent.

#### Final pre-delete concurrency boundary

After `intent_persisted`, every deletion attempt performs this sequence:

1. Immediately before DELETE, strictly GET the journaled namespace/name through the
   explicit destination kubeconfig/context.
2. From that one complete response, require the exact locator, journaled UID, expected
   generation, exact activation-method scope, structural equality between restore and
   cleanup backup maps, canonical owned-field fingerprint, every journaled concrete
   backup field, skipped-field semantics, and no unexpected `latest` alias.
   Missing/malformed/partial maps, a key/value difference, method drift, 404, or
   read/discovery failure are not delete success.
3. Capture the non-empty `metadata.resourceVersion` from that same validated response.
4. Submit DELETE with server-side preconditions for **both** journaled UID and that
   resourceVersion. Python uses
   `V1DeleteOptions(preconditions=V1Preconditions(uid=..., resource_version=...))`.
   The collection's guarded-delete boundary supplies the same two values; for the R4-04
   Restore call, `expected_resource_version` is mandatory even if the shared module also
   serves an older UID-only call contract.
5. Treat a 409/412 or server-equivalent precondition conflict as no deletion. Restart at
   step 1, bounded by one parity-shared retry limit. A same UID with a status-only
   resourceVersion change may safely retry only after the entire validation repeats. A
   generation/fingerprint/backup-field change, replacement UID, malformed response, or
   retry exhaustion fails closed. Never drop the resourceVersion precondition, downgrade
   a conflict to success, or loop without a bound.
6. After an accepted DELETE, retain the response in the current attempt and durably
   record `delete_accepted`; poll within a fixed timeout for the journaled UID to
   disappear. A live different UID is a replacement, not absence. When polling first
   observes absence, perform one additional strict final GET: only a verified 404/absence
   with no replacement permits the current uninterrupted attempt to write `completed`.
   A replacement during polling/final verification writes `recovery_required` and never
   completion. Timeout or malformed/unreadable read fails the current attempt with no
   completion while leaving the intent state resumable; a later resume must start with
   the complete strict read rules below.

The Kubernetes API `Preconditions` type explicitly supports both `uid` and
`resourceVersion`. The repository's dependency floor (`kubernetes>=28.0.0`) exposes a
`body: V1DeleteOptions` on namespaced custom-object DELETE, and its generated
`V1Preconditions` contains both fields. The final GET plus UID+resourceVersion
precondition therefore closes the **final-GET-to-DELETE** identity and same-UID version
window for this API path. The earlier evidence barrier remains useful, but it is not
credited with closing that later window.

The journaled locator is reserved for this transaction before generic Restore cleanup
classification. If another UID occupies it, that replacement is never routed through
the generic archive/delete path. Other switchover-owned Restores at different names keep
their existing cleanup contract and carry none of this transaction's evidence.

#### Resume and operator repair

Resume validates the complete journal first, then follows exactly one rule:

1. **No cleanup intent (`not_started`):** cleanup has not started. Re-run the complete
   evidence barrier, then create intent normally. If the Restore is already absent, the
   barrier's strict GET fails closed.
2. **Intent exists and the same UID is live (`intent_persisted` or
   `delete_accepted`):** first require exact restore/cleanup activation-method and
   backup-map equality, then re-run the complete
   locator/UID/generation/fingerprint/backup-field/no-`latest` validation and safely
   retry the guarded deletion using the same
   `run_id`, `operation_id`, and prior evidence. Never mint or adopt replacements.
3. **Intent exists and a different UID is live:** preserve the replacement, write
   `recovery_required` with stable reason `replacement_uid` when possible, and block
   every later gate. Do not delete, patch, archive, or adopt it.
4. **Intent exists and strict GET reports 404/absence, but completion is not durable:**
   do **not** infer success, even if `delete_accepted` was durably recorded. The process
   that received the accepted DELETE did not durably prove the bounded poll and final
   absence check. Write `recovery_required` with reason
   `absent_without_completion` and block cleanup completion, finalization,
   BackupSchedule enablement, and integrated teardown.
5. **Recovery read is unreadable or malformed:** make no mutation or state upgrade,
   preserve the current intent/delete-accepted record, emit a sanitized failure, and
   block the current attempt. A later resume starts the strict classification again;
   unreadability is never treated as absence.
6. **Completion exists:** accept `completed` only when every required field is present,
   internally consistent, and bound to the same run/operation/Restore/spec/backup tuple.
   A current same-name replacement remains a new unowned object: preserve it and block
   the final strict locator-absence gate; it never inherits the completed evidence.
7. **Recovery/repair exists:** `recovery_required` always blocks. `repaired` is accepted
   only under the complete audit and live-absence rules below.

There is no automatic ambiguous-to-success conversion. The operator-repair surface
(final CLI flag/subcommand and collection variable/module interface decided during the
implementation plan) is an explicit acknowledgement, not a synthetic proof that this
run performed the delete. It is accepted only when:

- the current state is `recovery_required`;
- an explicit operator action supplies non-empty `actor`, `reason`, and one or more
  `inspected_evidence` references;
- the action names the exact journal `run_id` and immutable cleanup `operation_id`;
- a fresh strict read through the same destination kubeconfig/context proves the
  journaled locator is absent; a live replacement rejects the repair and is untouched;
- one atomic durable update records `actor`, server/controller timestamp, reason,
  run/operation identities, and inspected-evidence references before changing the state
  to `repaired`.

Missing/partial audit fields, mismatched identities, unreadable live state, or a present
object reject the repair. A complete `repaired` record is accepted by cleanup completion,
BackupSchedule enablement, finalization, and integrated-teardown gates **only** as an
explicitly audited resolution of the original cleanup ambiguity and only while the final
strict locator check remains absent. It does not authorize deleting or adopting a
replacement. The expected-ManagedCluster waiver in §2 cannot create, bypass, or satisfy
cleanup repair evidence.

No cross-cluster or cross-store atomicity is claimed. Each form factor persists only its
own journal; parity is schema and behavioral equivalence, not dual writing or
cross-form-factor resume.

### 5. Form-factor boundaries and parity

#### Python CLI

- `StateManager` owns the sole authoritative Python `migration_backups` record. Backup
  freeze, Restore identity, cleanup intent, accepted-delete audit, completion,
  recovery-required, and repair are critical forced-durability writes.
- KubeClient gains narrowly scoped strict-read, JSON-Patch UID/resourceVersion guarded
  patch, and UID+resourceVersion guarded-delete helpers with request timeouts, bounded
  conflicts, per-instance kubeconfig/context routing, and sanitized errors. Existing
  generic name-only helpers are not used for the journaled Restore.
- Activation owns the passive guarded patch and post-patch evidence; Finalization owns
  the §4a transaction and final locator gate. Integrated `Decommission` receives only
  the already-validated gate outcome and cannot bypass it.
- Python dry-run performs reads/prediction but neither calls the mutating helpers nor
  persists authoritative transaction transitions.

#### Ansible collection

- Activation resolves and journals concrete backup names into checkpoint
  `operational_data` before creating/patching the Restore; resume reuses them.
- A collection-owned guarded-patch module/module_utils boundary submits the exact JSON
  Patch contract from §1b with explicit kubeconfig/context, bounded conflicts,
  check-mode prediction, exact `changed` semantics, post-patch verification, `no_log`
  task wiring, and sanitized result/error fields.
- The collection guarded-delete boundary supplies both expected UID and expected
  resourceVersion for the journaled Restore. Finalization reserves the journaled locator
  before generic cleanup, performs the transaction, and persists every transition
  through an explicit checkpoint operational-data update.
- Execute-mode R4-04 mutations require the checkpoint journal to be enabled and writable.
  Native check mode performs no mutation or authoritative checkpoint transition and
  publishes predicted change separately from actual `changed`.

#### Behavioral parity contract

- Expectation additivity, strict inventory semantics, and waiver behavior are identical.
- The §1a fingerprint projection/serialization/digest, schema-v2 field names and types,
  cleanup states and transitions, stable recovery reason codes, conflict retry limit,
  final polling bound, repair audit requirements, and fail-closed outcomes are identical.
- A fingerprint computed by one implementation over the same validated projection
  equals the other's, but evidence is never transferred: Python writes only Python
  state; the collection writes only checkpoint `operational_data`. A run reads only its
  own form factor's record. Neither imports from the other; no handoff, dual-write,
  two-store commit, or cross-form-factor resume exists.
- Parity means the same inputs produce the same mutation/no-mutation decision,
  `changed`/`would_change` result, recovery state, and teardown gate. It does not mean
  the two independent stores contain one shared physical record.

## Testing

Every case below is required for both Python and the collection unless explicitly
described as a cross-form-factor parity fixture.

### Existing migration evidence

- Freeze: authoritative journal write precedes Restore mutation; live `latest` movement
  does not change journaled names; every created/patched Restore body contains concrete
  names and no unexpected `latest`; no completed required backup is fatal before mutation.
- Additivity: names pass + floor fails; explicit `0` + missing expected name fails; an
  explicit waiver passes only the name predicate and is journaled; a waiver without
  expectations is rejected.
- Strict reads: inventory/discovery 404 is fatal rather than an empty result; a genuine
  empty list remains distinguishable.
- §1a evidence: exact identity/generation/fingerprint/consumed-field match writes the
  complete bundle; mismatch, missing field, malformed/unreadable response, or unexpected
  `latest` writes none. The passive map has exactly the one ManagedCluster key; the full
  map has exactly all three keys; skipped and absent categories stay excluded
  consistently.
- Explicit-map contract: passive single-field map, full multi-field map, skipped
  categories excluded, absent unconsumed journal category excluded, absent required
  category fatal, any skipped full-owned category fatal, `latest`/`skip` alias rejected,
  and partial/malformed maps fail closed.
- Cleanup copy/resume: intent copies the exact restore map and activation method; a
  cleanup map differing by key or value, activation-method drift, or canonical
  serialization/digest disagreement blocks before DELETE.
- Post-activation and teardown gates: the completion marker is written last; later
  post-activation failure leaves it absent; every missing/partial evidence field blocks;
  the waiver bypasses none of identity, provenance, post-activation completion, cleanup
  state, recovery/repair, or final locator absence.

### Cleanup durability and resume

- Crash after durable `intent_persisted` but before DELETE: same UID live causes complete
  revalidation and guarded retry with the same operation ID.
- Crash after DELETE is accepted, including after `delete_accepted` is durable but before
  completion persistence: absent locator becomes `recovery_required`, never completion.
- Intent plus live same UID: revalidate locator, generation, fingerprint, concrete backup
  fields, skipped fields, and no-`latest`, then retry safely.
- Intent plus live replacement UID: replacement remains unmutated and the transaction
  becomes blocking `recovery_required`.
- Intent plus strict 404/absence without completion: record
  `absent_without_completion`; finalization/teardown remain blocked.
- Malformed or unreadable recovery read: no mutation or state upgrade, intent remains
  resumable, and the current attempt fails with a sanitized error.
- Explicit operator repair with complete actor/timestamp/reason/run ID/operation
  ID/inspected-evidence fields and current strict absence: one durable transition to
  `repaired`; the named later gates accept it subject to final absence.
- Missing/partial repair record, wrong run/operation identity, empty evidence, or a live
  object: reject repair and remain blocked.
- Completed cleanup followed by a same-name replacement: original evidence remains bound
  to its UID, replacement remains unmutated, final locator-absence gate blocks.
- No automatic `recovery_required`/ambiguous-to-`completed` or `repaired` transition;
  the ManagedCluster-name waiver cannot affect this result.
- Invalid/unknown state transitions, partial terminal records, rewritten operation ID,
  disabled/unwritable collection checkpoint, and failed Python critical flush all block
  before mutation.

### Passive guarded patch

- Replacement UID appears between pre-read and PATCH: JSON Patch UID test fails and the
  replacement is unchanged.
- Same UID resourceVersion changes before PATCH: resourceVersion test fails; bounded
  complete re-read/revalidation is required before any retry.
- Failed JSON Patch test/precondition produces no field mutation and no applied evidence;
  merge-patch fallback is forbidden.
- Accepted PATCH followed by post-read UID mismatch, or generation/fingerprint/owned-field
  mismatch: fail closed and write no applied evidence.
- Passive activation changes only `veleroManagedClustersBackupName`; credentials and
  resources stay untouched/skipped and any `VELERO_BACKUP_SKIP` values remain.
- Check mode/dry-run reports `changed: false` plus exact `would_change: true|false` and
  performs no PATCH or journal transition.
- Execute exact reporting: desired state already verified → `changed: false`; this
  invocation patched and verified → `changed: true`; conflict/failure never claims an
  applied change.
- Explicit destination kubeconfig/context routing is passed through the guarded helper
  and collection module; a wrong/default client is detected by the test.
- Object 404 is distinct from discovery/read/auth/transport failure and both precede
  mutation.
- Kubeconfig paths, tokens, authorization headers, raw patch/response bodies, credentials,
  and rendered client exceptions are absent from logs/results in success, conflict,
  check-mode, and failure cases.

### Final cleanup boundary

- Same UID but owned spec changes after the earlier evidence barrier: the final GET
  detects generation/fingerprint/backup-field drift and no DELETE is issued.
- ResourceVersion changes between final GET and DELETE: the server rejects the
  UID+resourceVersion precondition and no deletion occurs.
- UID replacement before DELETE: final GET or delete precondition preserves the
  replacement and blocks.
- UID+resourceVersion precondition conflict is never treated as success or retried without
  the complete validation sequence.
- Status-only resourceVersion change causes a safe bounded retry; retry exhaustion blocks.
- Final GET missing/malformed UID, generation, resourceVersion, spec, or required field,
  or unreadable/discovery failure: no DELETE.
- A consumed backup field changes to `latest`: no DELETE.
- DELETE accepted, then a replacement appears during polling/final verification:
  replacement remains and `recovery_required` is written, never completion.
- Bounded poll timeout writes no completion, leaves the accepted/intended operation
  resumable, fails the current attempt, and does not silently loop.
- Completion evidence is written only by the uninterrupted accepted-delete attempt after
  bounded polling and a final verified absence. Crashing after that final read but before
  the durable write still resumes as ambiguous.

### Parity

- Identical explicit activation-method/map inputs produce deterministic sorted-key JSON
  and the same SHA-256 digest in Python and the collection.
- Schema-v2 field names/types, cleanup states/transitions, recovery reason codes, repair
  audit validation, conflict bound, polling bound, `changed`/`would_change`, and
  fail-closed outcomes are identical.
- Shared fixtures prove no cross-form-factor journal read, resume, handoff, or dual-write
  assumption; each form factor mutates only its own authoritative store.
- Version bump per repository policy is synchronized across Python and collection.

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
   (`restore.teardown_revalidated_at`) evidence, an internally consistent
   `cleanup.state` of `completed`/`repaired`, and final strict locator absence fails
   closed regardless of the waiver; the waiver substitutes only for expected-name
   verification.
6. Completion and provenance evidence is bound to the live Restore's namespace + UID and
   to its exact validated spec (generation + canonical fingerprint); a same-name
   replacement Restore can never inherit another object's evidence — at capture, on
   resume, at the §4a barrier, at the conditional patch/delete boundaries, and at the
   final locator gate.
7. Passive activation is one atomic JSON Patch whose UID and resourceVersion tests
   precede its sole ManagedCluster-backup-field mutation. A replacement or concurrent
   change cannot be mutated by a name-only fallback, and applied evidence follows a
   complete strict post-read.
8. Cleanup intent is durably persisted before DELETE. Completion is durably persisted
   only in the uninterrupted accepted-delete attempt after bounded absence polling and a
   final strict absence check. Absent-with-intent but without completion is
   `recovery_required`, never inferred success.
9. The final deletion attempt validates the complete identity/generation/fingerprint/
   backup-field projection in one fresh GET and supplies both that UID and
   resourceVersion as server-side DELETE preconditions. Same-UID spec drift after the
   earlier barrier or a version change after the final GET prevents deletion or causes a
   bounded full revalidation retry.
10. Operator repair is explicit and auditable, requires the complete actor/timestamp/
    reason/run/operation/evidence record plus current strict absence, never deletes or
    adopts a replacement, and is accepted only by the named cleanup/finalization/
    BackupSchedule/teardown gates. The expected-name waiver cannot satisfy it.
11. Python state and collection checkpoint stores implement identical schema,
    transitions, retry/timeout behavior, redaction, and outcomes without any dual write,
    handoff, or cross-form-factor resume.
12. `restore.backup_fields` is the sole canonical immutable map; its passive/full key
    sets, concrete values, skipped/absent treatment, activation-method scope, sorted JSON,
    and SHA-256 relationship are exact. Cleanup copies that map and method structurally
    unchanged and rejects every partial, extra-key, missing-key, changed-value,
    method-drift, alias, or digest mismatch at intent creation, resume, and final
    pre-delete revalidation. Full activation requires all three owned categories to carry
    concrete names and rejects `VELERO_BACKUP_SKIP` before mutation; only passive's
    unowned credentials/resources fields retain their skip/untouched semantics.

## Verified protocol and client basis

- [Kubernetes API concepts: updates to existing resources](https://kubernetes.io/docs/reference/using-api/api-concepts/#updates-to-existing-resources)
  documents `application/json-patch+json`, JSON Patch consistency conditions, and
  conditional resourceVersion handling.
- [RFC 6902 §4.6 and §5](https://datatracker.ietf.org/doc/html/rfc6902#section-4.6)
  define `test` and failed-operation behavior; [RFC 5789 §2.2](https://datatracker.ietf.org/doc/html/rfc5789#section-2.2)
  requires an HTTP PATCH to apply atomically.
- [Kubernetes `Preconditions`](https://kubernetes.io/docs/reference/kubernetes-api/definitions/preconditions-v1-meta/)
  defines both `uid` and `resourceVersion`.
- The repository's minimum supported generated client,
  [`kubernetes-client/python` v28.1 custom-object API](https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/api/custom_objects_api.py),
  accepts `V1DeleteOptions` for namespaced custom-object DELETE but hard-codes
  merge-patch content type for its generated PATCH wrapper.
  [`V1Preconditions` in the same release](https://github.com/kubernetes-client/python/blob/v28.1.0/kubernetes/client/models/v1_preconditions.py)
  exposes `uid` and `resource_version`. These inspected sources are why this design uses
  the generated DELETE body and a dedicated low-level JSON-Patch helper.
