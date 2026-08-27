# R4-04 ManagedCluster Migration Evidence — Current-Base Amendment

**Date:** 2026-08-27
**Base:** `ansible@acb002eb561055deb9cdb9a44c4ea74fea10fd41`
**Status:** second-round revised normative amendment awaiting written-spec review; no implementation plan or implementation authority
**Amends:** `docs/plans/2026-07-29-migration-evidence-design.md`

## Authority and scope

This document is the current-base amendment to the accepted 2026-07-29 R4-04 design.
The July design remains the baseline for the migration-evidence transaction. Where this
amendment conflicts with that document, **this amendment wins**. Where it is silent, the
July design remains normative.

The amendment exists because the repository architecture and adjacent safety work changed
after the July design, and because the root-level `thermos-resolution-plan.md` section
**"Convergence-rule triage — CodeRabbit round of 2026-08-02 (18 findings)"** left four
R4-04 implementation obligations, two of them explicit design choices, to the implementing
slice.

Two independent review rounds materially hardened the current-base design before the
implementation-plan gate:

1. the first review showed that generic-resource provenance cannot be bound to the ordinary
   resources Backup and that the passive in-place sync Restore cannot simply replace every
   `latest` sentinel with a concrete Backup name;
2. the second review showed that accepting passive `Enabled` after validating only the
   ManagedClusters child has no aggregate-success guarantee, and that passive activation
   can consume additional credential/resource Backup inputs that must be provenance-bound
   as part of the same transaction.

Current-source revalidation on the base above confirms the underlying R4-04 defects remain
open in both supported form factors: Restore sources can still move through `latest`,
explicit ManagedCluster floors can suppress expected-name enforcement, the Python generic
list helper can collapse list 404 into an empty inventory, and integrated finalization has
no migration-evidence barrier before Restore cleanup and old-hub handling.

This is a **design-only** amendment. It changes no runtime, test, RBAC, manifest, Helm,
release-validation, lab-controller, protected-file, or support behavior. R4-04 remains
`planned`. The implementation plan is written only after this exact revised amendment is
reviewed and approved.

### Superseded July assumptions

Three July assumptions are narrowed by this amendment:

1. **"Every Restore names concrete backups" is not universally implementable.**
   Passive in-place activation uses an existing `syncRestoreWithNewBackups: true` Restore.
   Supported cluster-backup-operator snapshots require that Restore's backup fields to stay
   within the normalized `skip`/`latest` sync contract. R4-04 therefore permits `latest`
   only at that upstream-required passive-patch trigger boundary and binds every concrete
   Backup actually accepted as transaction provenance before completion evidence is
   written. One-shot passive Restore creation and full Restore creation still use concrete
   names.
2. **The resources category is not a one-Backup evidence domain.**
   Full restore can consume both `acm-resources-schedule-*` and the distinct
   `acm-resources-generic-schedule-*` Backup. The generic Backup has no ACM Restore spec
   field of its own; it is operator-derived and must be journaled and verified separately.
3. **Passive activation evidence is not only the ManagedClusters child.**
   Depending on the pinned ACM/controller lane, activation also consumes credential and
   generic-resource Backup inputs and can create additional Velero Restores that are not all
   published directly in ACM Restore status. R4-04 must bind those inputs and prove the
   complete lane-specific completion cohort before `restore.completed_at` can be written.

No other July safety guarantee is weakened.

## 1. Current persistence ownership

The July design predates the repository's named cross-phase state facades. R4-04 must use
those facades; it must not reopen raw state access.

### Python CLI

- `lib/run_record.py::RunRecord` will own the Python `migration_backups` vocabulary and its
  typed/named read-write operations once R4-04 is implemented.
- `lib/utils.py::StateManager` owns storage mechanics only: locking, atomic file
  replacement, dirty tracking, and durability primitives. Runtime R4-04 code does not call
  `_set_config` / `_get_config` and does not read a named migration key directly from a
  state snapshot.
- `migration_backups` remains one top-level config value. A journal transition builds and
  validates a complete candidate record, then asks `RunRecord` to persist that value as one
  critical state operation. Splitting one logical transition across unrelated config keys
  is forbidden.
- `tests/test_run_record_guardrails.py` remains the structural guardrail; R4-04 adds
  interface tests proving production migration code reaches the journal only through
  `RunRecord`.

### Ansible collection

- `plugins/module_utils/checkpoint.py` owns the collection checkpoint vocabulary. R4-04
  adds the named migration-journal key, strict parser/validator, and reset-boundary helper
  there.
- `checkpoint_phase` remains the authoritative checkpoint persistence boundary. No second
  state file, sidecar journal, or cross-form-factor store is introduced.
- R4-04 adds `status: update` to `CHECKPOINT_VALID_STATUSES` as an
  **operational-data-only** transition. It exists so a safety-critical mid-phase journal
  write does not masquerade as `enter`, `pass`, or `fail`.
- `status: update` validates the loaded checkpoint, operation identity, current phase, and
  candidate operational data. The requested phase must equal the persisted checkpoint
  phase; a missing checkpoint or phase mismatch is fatal. `error` and `report_ref` inputs
  are invalid for this status.
- `build_phase_transition()` is an explicit integration point. The implementation must
  either special-case `status == "update"` there or bypass the phase-transition helper for
  that status. In either case, the returned/applied values for `completed_phases` and
  `phase_status` are the **existing persisted values**, not `"update"`. An update may
  change only `operational_data` and `updated_at`; it must not alter `phase`,
  `completed_phases`, `phase_status`, `errors`, or `report_refs`.
- R4-04 passes the complete non-empty journal as one top-level mapping:
  `operational_data={"migration_backups": <complete-candidate>}`. The existing top-level
  filtering of `None` / `""` therefore does not strip schema-valid nulls nested inside the
  journal, and key-wise merge cannot leave stale nested fields because the entire
  `migration_backups` mapping is replaced as one value.
- Role and playbook YAML consume named flattened facts or a dedicated validated journal
  result published by the checkpoint facade. They do not walk raw `operational_data`.
  `test_checkpoint_vocabulary_guardrail.py` remains the structural guardrail.

The two codebases remain independent and never import from one another. Parity means the
same schema, validation, transition decisions, recovery states, and externally observable
outcomes, not shared persistence.

## 2. Store-read and journal-validation algebra

Existing run-summary/checkpoint-fact readers intentionally degrade malformed ordinary
state to defaults. That tolerance model is **not** allowed for R4-04 evidence.

Validation has two levels.

### Store-read outcome

The authoritative state/checkpoint store yields exactly one of:

- **readable** — the file was positively opened, decoded as JSON, and produced the
  form-factor's ordinary top-level state/checkpoint structure;
- **corrupt** — JSON or top-level checkpoint/state structure is malformed;
- **unreadable** — permission, I/O, path, or other storage failure prevents a trustworthy
  read.

`corrupt` and `unreadable` are blocking outcomes. Neither may be mapped to journal absence.

For parity, corruption preservation is fail-closed across invocations. Python already
copies the corrupt state to a forensic path while leaving the original in place. The
collection must adopt the same semantic invariant for corrupt checkpoints used by an
R4-04-aware run: preserve a forensic copy, leave the original blocking checkpoint in
place, and fail. A later invocation therefore cannot interpret the prior corruption as
"no checkpoint". A failed forensic copy also fails closed without deleting/moving the
original.

### Journal outcome inside a readable store

Only a **readable** store can produce a migration-journal outcome:

- **absent** — the `migration_backups` key is absent. This is allowed only before the
  freeze boundary or after an explicitly successful full state/checkpoint reset;
- **valid** — the complete schema and every state-dependent invariant validate;
- **invalid** — the key is present but malformed, wrong-versioned, partial, internally
  inconsistent, or carries an impossible state transition.

`invalid` is blocking before any R4-04 mutation, Restore cleanup, BackupSchedule
enablement, finalization completion, or integrated teardown.

No wrong-typed or missing R4-04 field silently becomes `None`, `[]`, `{}`, `0`, or
`False`. Unknown future journal schema versions fail closed rather than being treated as
empty legacy state. Validation runs before consuming a persisted transition and again
before writing a candidate transition.

## 3. Backup evidence and upstream selection algebra

The July schema's `{name, completed_at}` backup entries are superseded. Every Backup that
R4-04 accepts as migration provenance stores this seven-field projection:

```yaml
namespace: open-cluster-management-backup
name: "<concrete Velero Backup metadata.name>"
uid: "<Velero Backup metadata.uid>"
phase: Completed
completed_at: "<status.completionTimestamp>"
errors: 0
warnings: 0
```

`warnings` is the exact non-negative integer observed; `0` above is illustrative. All
**seven** fields are required. R4-04 eligibility is strict:

- `status.phase == "Completed"`;
- a present, well-formed `status.completionTimestamp`;
- `status.errors` is a well-formed non-negative integer equal to zero;
- `status.warnings` is a well-formed non-negative integer;
- `metadata.name` and `metadata.uid` are non-empty strings;
- `metadata.namespace` equals the exact backup namespace used by the strict read.

Every required Backup is strictly GET/revalidated by namespace/name immediately before the
Restore mutation boundary. UID and the complete persisted status projection must still
match. A same-name Backup with a different UID is a replacement and blocks; the run never
rebinds the journal to it. Status drift away from the persisted successful projection also
blocks. This proves provenance at each required read instant; it does not claim an
admission lock after that read returns.

### Journal categories

The journal permits these method-scoped Backup categories:

```yaml
backups:
  managed_clusters: {<seven-field projection>}

  # full_restore only
  credentials: {<seven-field projection>}
  resources: {<seven-field projection>}
  resources_generic: {<seven-field projection>}

  # passive_patch only: operator-selected auxiliary inputs
  activation_credentials: {<seven-field projection>}
  activation_resources: {<seven-field projection>}
  activation_resources_generic: {<seven-field projection>}
```

`resources_generic` and the three `activation_*` categories are evidence categories, not
additional ACM Restore spec fields. Supported controller snapshots expose only three
backup-name fields on the ACM Restore: managed clusters, resources, and credentials.

The passive auxiliary set is deliberately a superset of the minimum activation-only
children. It freezes every concrete Backup that the pinned controller can select from the
live passive Restore's `latest` credentials/resources inputs during the accepted patch
transaction. A category may remain unconsumed by the resulting child cohort, but if a
child consumes it its `spec.backupName` must match the frozen category exactly. This avoids
silently accepting a controller branch that consumed an ordinary resources Backup merely
because the expected activation-only path usually does not.

### Upstream `latest` selection must be predicted before R4 eligibility is applied

Where R4-04 must predict what a supported controller will select from `latest`, it mirrors
the controller's **selection order first** and applies the stricter R4-04 eligibility test
only to the selected object. It never filters failed/partially-failed candidates out first
and then chooses a different Backup.

For the pinned controller snapshots, direct `latest` selection for managed clusters,
credentials, ordinary resources, and generic resources:

1. strictly and completely lists Velero Backups;
2. applies the controller's resource-type schedule-prefix filter;
3. applies the controller's raw phase filter (`Completed` or `PartiallyFailed`) where that
   controller path applies it;
4. sorts the raw candidates by `status.startTimestamp` descending exactly as the pinned
   source does and chooses the first candidate;
5. only then requires that selected Backup to satisfy the seven-field R4-04 projection.

If the controller-selected candidate is `PartiallyFailed`, malformed, or otherwise fails
R4-04 eligibility, activation blocks. R4-04 does not substitute an older successful
Backup because the controller would not make that substitution.

### Correlated generic-resource fallback

When a controller derives a generic-resource Backup from a concrete ordinary resources
Backup, R4-04 mirrors the exact-name/fallback algorithm:

1. Start from the already frozen concrete ordinary resources Backup name.
2. Parse the timestamp **from the Backup name suffix**, not from the ordinary Backup's
   `status.startTimestamp`. The pinned helper uses the 14-digit format
   `20060102150405` after the last `-`.
3. Construct the exact generic candidate with the generic schedule prefix and that same
   suffix. If an exact object exists, it is the controller candidate; R4-04 then requires
   the seven-field success projection.
4. If the exact object is absent, build the controller's raw ±30-second candidate set over
   the strictly complete generic Backup inventory. The target is the timestamp parsed from
   the ordinary Backup **name**. A raw candidate is included when its name matches the
   generic schedule family, its `status.startTimestamp` is present, and that timestamp is
   within ±30 seconds of the parsed target. There is **no R4-04 phase/success pre-filter**
   when computing this raw set.
5. R4-04 accepts the fallback only when the raw set has exactly one object. Zero or more
   than one raw candidates is fatal before mutation because the controller's list-order
   choice is not a provenance proof.
6. The one raw candidate must then satisfy the seven-field R4-04 projection before it is
   frozen.

For full restore this result is stored as `backups.resources_generic`. For passive patch,
the same selection algebra is used where the pinned controller's auxiliary generic path
requires `activation_resources_generic`.

The current repository's `ACM_BACKUP_SCHEDULE_TYPES` and `ACM_BACKUP_NAME_RE` do not know
about generic/activation evidence categories. R4-04 implementation must extend only the
R4-04 evidence-selection surface needed for these categories; it must not silently broaden
unrelated backup classification without tests and explicit scope.

## 4. Restore trigger and spec-binding semantics

The July rule that `latest` never appears in a journaled Restore spec is superseded by the
method-specific contract below.

The journal adds immutable method/controller evidence:

```yaml
restore:
  activation_method: passive | full
  mutation_kind: passive_patch | passive_restore | full_restore
  acm_minor: "2.12" | "2.13" | "2.14" | "2.15" | "2.16" | "2.17"
  controller_contract: legacy_2_12_2_16 | active_2_17
```

`acm_minor` is derived from the already validated secondary ACM version. The controller
contract is selected from the pinned matrix in §5. Unknown/unmapped ACM minors are
blocking for `passive_patch`; R4-04 never guesses a controller contract. These values are
immutable across resume. `mutation_kind` is copied into cleanup intent and included in all
strict journal/resume/fingerprint consistency checks; `acm_minor` and
`controller_contract` remain journal evidence but are not live Restore spec fields and are
therefore not part of the spec fingerprint.

### `passive_patch`

The existing passive sync Restore is a `syncRestoreWithNewBackups: true` object. Every
pinned ACM 2.12-through-2.17 controller snapshot normalizes backup option strings with
`strings.TrimSpace` + `strings.ToLower` and requires:

- ManagedClusters backup option in `{skip, latest}`;
- credentials backup option equal to `latest`;
- resources backup option equal to `latest`.

A concrete ManagedCluster Backup name would invalidate sync processing. Therefore R4-04
does **not** patch a concrete name into this existing sync Restore.

Fresh `passive_patch` intent is exact:

1. Strictly read the passive ACM Restore and preserve the exact raw values of its owned
   pre-patch spec fields for guarded comparison/audit.
2. Normalize the three backup option strings exactly as upstream does. Require normalized
   ManagedClusters `skip`, normalized credentials `latest`, normalized resources `latest`,
   and `syncRestoreWithNewBackups == true`. Case/outer whitespace accepted by upstream is
   therefore not rejected merely for presentation. The guarded patch tests the exact raw
   pre-patch ManagedClusters value before replacing it.
3. Freeze `backups.managed_clusters` by mirroring upstream `latest` selection and then
   applying R4-04 eligibility.
4. Freeze the lane's operator-selectable passive auxiliary inputs before mutation:
   `activation_credentials`, `activation_resources`, and
   `activation_resources_generic`, using §3's exact upstream-first selection rules. If the
   pinned lane cannot deterministically select a required auxiliary input, the patch is
   not attempted.
5. Persist the ACM Restore namespace/name/UID, pre-patch generation and resourceVersion,
   raw and normalized owned pre-patch projection, all frozen Backup evidence,
   `mutation_kind`, `acm_minor`, and `controller_contract` before mutation.
6. Record the pre-patch values of all four ACM status Restore-name fields, not only the
   ManagedClusters field. These values distinguish newly published/changed status
   associations from existing passive-sync children during post-patch reconciliation.
7. The guarded JSON Patch tests the exact UID, resourceVersion, and raw current
   `/spec/veleroManagedClustersBackupName`, then replaces that field with canonical
   lowercase `latest`. It does not rewrite credentials/resources merely to normalize their
   spelling.
8. `restore.backup_fields` for this mutation kind contains exactly
   `{"veleroManagedClustersBackupName": "latest"}`. This is the only R4-04 `latest`
   sentinel permitted in an owned mutation projection. Concrete provenance lives in the
   `backups.*` evidence and validated child Restores.
9. A different non-empty post-patch `veleroManagedClustersRestoreName` is mandatory. Its
   Velero Restore must bind to `backups.managed_clusters` under §5.
10. Every other lane-required child Restore and every status association changed by this
    transaction must pass §5 completion and provenance binding. A child that consumes a
    Backup different from the corresponding frozen `activation_*` evidence blocks
    completion/finalization evidence.

On resume after this journal has already recorded an accepted guarded patch, normalized
live ManagedClusters `latest` is expected and the run reconciles the existing transaction
instead of requiring the fresh-intent `skip` precondition again. A live normalized
`latest` without this journal's accepted patch evidence is stale/unowned and blocking.
Resume never recomputes any frozen `activation_*` Backup from the later meaning of
`latest`.

The remaining concurrency limit is explicit. The controller resolves `latest` after the
PATCH reaches the cluster. R4-04 cannot prevent an unrelated actor from creating a newer
controller-eligible Backup between the final pre-patch Backup reads and controller alias
resolution. R4-04 detects a mismatched result from the child `spec.backupName` evidence and
blocks all completion/finalization evidence, but the mismatched child Restore may already
have run. Eliminating that alias-resolution window requires retiring/coercing
`passive_patch` to a one-shot concrete Restore mechanism or another upstream coordination
mechanism; that behavior change is outside this compatibility-preserving revision and
requires separate operator approval.

### `passive_restore`

The one-shot activation Restore created after deleting/replacing the passive sync Restore
is not a sync Restore. Its ManagedCluster field uses the concrete frozen
`backups.managed_clusters.name`; credentials/resources remain the documented skip values.
`restore.backup_fields` contains only the concrete ManagedCluster field, as in the July
method-scoped projection. Passive auxiliary categories are absent for this mutation kind.

### `full_restore`

All three ACM Restore backup-name fields use their concrete journaled names:

- managed clusters → `backups.managed_clusters.name`;
- credentials → `backups.credentials.name`;
- resources → `backups.resources.name`.

`backups.resources_generic` is evidence-only because the ACM Restore API has no fourth
generic backup-name field. The generic Velero Restore is verified after the controller
selects it. Passive `activation_*` categories are absent for this mutation kind.

## 5. ACM Restore to Velero Restore association and lane-specific terminal evidence

Association is never inferred from an ACM Restore naming convention alone, and one
controller-generation algorithm is not extrapolated across all six ACM minors.

### Immutable upstream contract matrix

The design revalidation pins these cluster-backup-operator snapshots:

| ACM lane | immutable cluster-backup-operator snapshot | passive completion/child model |
| --- | --- | --- |
| 2.12 | `74b54988a5bd6712ea3fe3e9ceb770e06db91e8b` | `legacy_2_12_2_16` |
| 2.13 | `7a7b240b3df71105da3f15620e4116498f9e2a23` | `legacy_2_12_2_16` |
| 2.14 | `8b489db488739e7d9adca50cb3be0eae79293f22` | `legacy_2_12_2_16` |
| 2.15 | `25b28b762355a14b4fb7f145efe173f73659e740` | `legacy_2_12_2_16` |
| 2.16 | `9efe77eaec2139f106c957051e2297dafc84b482` | `legacy_2_12_2_16` |
| 2.17 | `c8578f94df09deab561e1aa5a7e9fc9b57f7d113` | `active_2_17` |

All six inspected Restore APIs expose the three ACM backup-name fields and four status
Restore-name fields used here, and all six inspected controller generations use the
normalized sync-option contract described in §4. The 2.12-through-2.16 snapshots share
the legacy `restoreOnlyManagedClusters` resource set of `ManagedClusters`, `Credentials`,
and `ResourcesGeneric`; they have neither `CredentialsActive` /
`ResourcesGenericActive` nor the newer `getLatestVeleroRestores` current-name filter.
The pinned 2.17 snapshot introduces `CredentialsActive` and
`ResourcesGenericActive`, and its status aggregation filters to current status-published
Restore names plus their related `-active` variants so historical owned failures do not
poison the current sync cycle.

The implementation plan must keep this matrix as immutable-source fixtures. A future ACM
minor, a different controller snapshot for one of these lanes, or a source change that
alters the selection/completion model is not silently assigned to an existing contract.

### Strict owner validation

For every required Velero Restore R4-04:

1. strictly GETs the exact object in the ACM Restore namespace when a status locator gives
   its name;
2. requires non-empty Velero Restore `metadata.uid` and journals namespace/name/UID;
3. requires exactly one controller ownerReference matching:
   - API group `cluster.open-cluster-management.io` (served version is not pinned),
   - `kind: Restore`,
   - the exact journaled ACM Restore name,
   - the exact journaled ACM Restore UID,
   - `controller: true`;
4. requires the child's `spec.backupName` to equal the concrete journaled Backup required
   for that child;
5. requires `status.phase == "Completed"`. Missing, malformed, failed,
   partially-failed, validation-failed, in-progress, or unrecognized phases are
   non-success.

The cluster-backup-operator's `.metadata.controller` `MatchingFields` lookup is a
controller-runtime cache index, not a portable server-side Kubernetes field selector.
R4-04 therefore never relies on that selector from the CLI/collection. Any child sweep
strictly and completely LISTs `velero.io/v1 Restore` objects in the namespace under the
R4-03 pagination/outcome contract and filters client-side by the exact controller
ownerReference identity above. Truncation, malformed list data, auth/discovery/transport
failure, or incomplete pagination is blocking.

### Journaled child evidence

The journal keeps status associations and any additional lane-required children explicitly:

```yaml
restore:
  velero_restores:
    managed_clusters: []
    credentials: []
    resources: []
    resources_generic: []
    activation_credentials: []
    activation_resources: []
    activation_resources_generic: []
```

Each list entry is at least `{namespace, name, uid}` and is immutable once accepted.
Duplicate locators normalize only when every observed identity agrees; conflicting evidence
is malformed.

### Full and one-shot passive requirements

- **`passive_restore`** — `veleroManagedClustersRestoreName` is mandatory and its child
  binds to `backups.managed_clusters`.
- **`full_restore`** — ManagedClusters, credentials, ordinary resources, and generic
  resources associations are mandatory and bind respectively to
  `backups.managed_clusters`, `backups.credentials`, `backups.resources`, and
  `backups.resources_generic`.

For these mutation kinds ACM `Restore.status.phase == "Finished"` is required before
`restore.completed_at`. `FinishedWithErrors` is blocking; the migration-evidence
transaction has no benign error-message allow-list. Every method-required child must also
be `Completed` and provenance-bound as above.

### `passive_patch`: legacy 2.12–2.16 contract

For `controller_contract: legacy_2_12_2_16`:

1. A strict complete namespace LIST is filtered client-side to children controller-owned by
   the exact ACM Restore UID. The legacy controller itself evaluates its owner-controlled
   Restore list without the 2.17 current-name filter; R4-04 therefore requires every child
   in that legacy completion cohort to be terminal-success `Completed`. This can be more
   conservative than the 2.17 path on a cluster carrying historical failed children; the
   design chooses fail-closed compatibility rather than inventing a current-cycle filter
   absent from the pinned legacy source.
2. Post-patch `veleroManagedClustersRestoreName` must be non-empty and different from the
   pre-patch value, and its child binds to `backups.managed_clusters`.
3. Post-patch `veleroCredentialsRestoreName` and
   `veleroGenericResourcesRestoreName` are required for the legacy
   `restoreOnlyManagedClusters` path and bind to `backups.activation_credentials` and
   `backups.activation_resources_generic` respectively. If the controller branch changes
   `veleroResourcesRestoreName` during the transaction, that changed association is also
   required and binds to `backups.activation_resources`.
4. A status association that resolves to an already-existing child is still acceptable
   only when that child's exact `spec.backupName` equals the frozen auxiliary Backup. R4-04
   does not infer successful activation provenance merely from an unchanged name.

If lab/source fixtures show a claimed legacy lane does not satisfy this exact contract, the
lane fails closed and the design must be amended rather than weakening evidence at runtime.

### `passive_patch`: 2.17 active-variant contract

For `controller_contract: active_2_17`:

1. Strictly complete-list and owner-filter all Velero Restores as above.
2. Reproduce the pinned `getLatestVeleroRestores` completion cohort from the strictly
   complete owner-filtered inventory: start with all non-empty current ACM status-published
   Restore names, strip a trailing `-active` to form each base name, and include every
   owner child whose exact name is current or whose name with a trailing `-active` removed
   matches one of those base names.
3. Every member of that current cohort must be `Completed`. A missing expected child,
   failed/in-progress `-active` variant, malformed object, or child outside the exact owner
   UID is blocking even if the ACM Restore still reads `Enabled`.
4. The newly published ManagedClusters association remains mandatory and binds to
   `backups.managed_clusters`.
5. The current-cohort children created for `CredentialsActive` and
   `ResourcesGenericActive` are not directly published into distinct ACM status fields;
   their related `-active` children are nevertheless mandatory completion/provenance
   evidence. Credential-active children must use
   `backups.activation_credentials`; generic-resource-active children must use
   `backups.activation_resources_generic`.
6. If the same controller cycle publishes/changes ordinary credentials/resources/generic
   status associations, each changed association is also required, `Completed`, and binds
   respectively to `activation_credentials`, `activation_resources`, or
   `activation_resources_generic`.

The implementation plan must pin negative fixtures proving that an `Enabled` ACM Restore
with a failed or still-running current `-active` child is **not** accepted.

### Method-specific ACM phase rule

`restore.completed_at` is not derived from one phase literal for every activation
mechanism.

- `passive_restore` and `full_restore` require ACM `Finished` plus every method-required
  child described above.
- `passive_patch` may accept ACM `Finished` or `Enabled`, but **both** phase values require
  the complete lane-specific child/cohort proof above. `Enabled` is never a shortcut around
  child aggregation. In addition:
  1. the guarded patch/reconciliation evidence must be complete for the journaled ACM
     Restore UID/generation/spec projection;
  2. the post-patch ManagedClusters status name must be new relative to the pre-patch
     value;
  3. every required current/legacy cohort child must be owner-UID-bound, provenance-bound,
     and `Completed`;
  4. the expected ManagedCluster-name predicate for activation must have passed or be
     covered by the explicit audited waiver.

`FinishedWithErrors`, `Error`, `EnabledError`, `Unknown`, missing/malformed phase, and every
unrecognized value are blocking. The stricter ACM `Finished` outcome remains preferred,
but it does not eliminate the R4-04 child/provenance checks.

## 6. `cleanupBeforeRestore` is part of the exact-spec claim

The 2026-08-02 obligation is resolved by **including** `cleanupBeforeRestore`, not by
weakening the exact-spec claim.

The journal adds:

```yaml
restore:
  cleanup_before_restore: CleanupRestored
cleanup:
  cleanup_before_restore: null | CleanupRestored
```

`CleanupRestored` is the shared cross-form-factor value. Activation records the exact
normalized value before mutation and every governed destination Restore
create/post-read/evidence/revalidation boundary requires live
`spec.cleanupBeforeRestore` to match it. Cleanup intent copies it structurally unchanged.

The canonical fingerprint projection is superseded by exactly these keys:

```json
{
  "activation_method": "<passive|full>",
  "mutation_kind": "<passive_patch|passive_restore|full_restore>",
  "backup_fields": {},
  "cleanup_before_restore": "CleanupRestored"
}
```

`backup_fields` is the exact owned live-spec projection from §4. For `passive_patch`, its
single value is canonical `latest`; concrete provenance lives in the frozen Backup
categories and child Restore evidence. For one-shot passive/full Restores, consumed owned
fields are concrete. `acm_minor`, `controller_contract`, and auxiliary Backup evidence are
journal invariants but are not live-spec fields and therefore do not enter the spec
fingerprint.

Serialization and SHA-256 rules are otherwise unchanged: sorted JSON keys, no
insignificant whitespace, identical ASCII escaping in both form factors, lowercase
SHA-256 hex. No live-spec field outside this explicit projection is claimed by the
fingerprint.

The final pre-delete projection, teardown revalidation, cleanup-intent copy, parity
fixtures, and digest checks all include `mutation_kind` and `cleanup_before_restore`.

The separate old-hub passive Restore created by
`roles/finalization/tasks/handle_old_hub.yml` is outside this destination migration
evidence transaction. Its existing literal `CleanupRestored` is not expanded into R4-04
cleanup merely because it shares the value.

## 7. ManagedCluster expectation changes use the existing seams

R4-04 does not add another expectation resolver.

The effective contract is explicit:

- An available expected-name set is retained independently of any explicit minimum.
- A non-empty expected-name set means the name predicate is enforced unless the strict
  migration journal carries the July design's valid audited waiver covering that
  predicate.
- An explicit minimum is an **additional count floor**, never a switch that disables
  names.
- Explicit `min_managed_clusters == 0` sets only the count floor to zero. It does **not**
  waive or clear a non-empty expected-name set.
- In Python, `enforce_expected_managed_cluster_names` is true whenever effective expected
  names are non-empty and no valid waiver covers the current predicate.
- In the collection, `acm_switchover_resolved_allow_zero_managed_clusters` may be true only
  when the effective expected-name set is empty. If names exist, the resolved value is
  false so published facts do not simultaneously say "zero permitted" and "these names
  are mandatory".
- Restore-only default-floor behavior is preserved when no discovered/configured
  expectation exists.

The waiver never rewrites or erases the recorded expectation; it records that one predicate
was explicitly waived.

## 8. Strict Kubernetes reads: reuse the current architecture

R4-03 still owns the complete shared strict-inventory contract and is a prerequisite for
R4-04 inventory consumers. Python's current `KubeClient.list_custom_resources()` is not
acceptable because list 404 can become `[]`, malformed `items` can be accepted, and
bounded truncation can return a partial inventory.

The collection has gained `acm_k8s_read_outcome` since the July design. It already gives a
useful sanitized one-read `ok` / named-`not_found` / `error` contract. R4-04/R4-03 must
**reuse or extend that seam** where applicable rather than creating a second lossless-read
abstraction. Its current contract does not itself satisfy complete-pagination inventory
semantics.

Implementation order remains fixed:

1. R4-03's shared strict outcome/pagination contract is implemented with parity vectors in
   both form factors, reusing collection read-outcome plumbing where natural.
2. R4-04 switches Backup discovery, ManagedCluster inventories, passive ACM Restore
   discovery, and the passive child-Restore namespace sweep to that complete contract.
3. The passive child sweep strictly LISTs all Velero Restores in the namespace and filters
   client-side by exact controller ownerReference. It does not use the
   cluster-backup-operator controller-runtime `.metadata.controller` cache index as an API
   field selector.
4. R4-04's named-object Backup/ACM-Restore/Velero-Restore evidence reads use matching
   strict named-object semantics; API/discovery/auth/transport/decode failure is never
   absence.

No R4-04 plan may wire a safety decision to the current advisory-shaped Python list helper,
a truncated child inventory, or a collection result whose completeness has not been
positively established.

## 9. Evidence durability is a hard prerequisite

The July design already requires safety-authorizing evidence to be durable. Current-base
revalidation makes the dependency explicit:

- Python `StateManager` fsyncs the temporary file before `os.replace` but does not yet
  implement R4-05's parent-directory durability contract.
- Collection checkpoint persistence performs a parent-directory fsync after replace, but
  its best-effort `OSError` suppression does not satisfy R4-05's stricter
  capability/failure model.

R4-04 may not treat either current implementation as sufficient durability proof as-is.
Before a cluster mutation depends on the R4-04 journal, one of these must be true:

1. R4-05's applicable durability contract has already landed in that form factor; or
2. the R4-04 foundation tranche includes the narrow prerequisite persistence change
   needed to satisfy that same contract before the first R4-04 journal write.

The second option implements the R4-05 contract narrowly; it does not invent a different
durability model. The collection still uses checkpoint persistence and Python still uses
StateManager through RunRecord.

An unreadable/unwritable store, disabled execute-mode collection checkpoint, failed Python
critical write, failed required parent-directory durability acknowledgement, corrupt
store, or indeterminate durability outcome blocks before the corresponding cluster
mutation. Dry-run/check mode persists no authoritative R4-04 transition.

## 10. Resume, retry, reset, and rewind semantics

Once the first Backup freeze is durably recorded, the migration journal represents one
immutable migration transaction. Retry/resume reuses and revalidates it; it never chooses
a newer Backup merely because the phase is being retried.

For `passive_patch`, this no-refreeze rule covers **all** frozen concrete evidence:
`managed_clusters` and every `activation_*` category. A resumed run never updates the
journal to whichever Backups the live `latest` aliases mean later. The recorded
`acm_minor` and `controller_contract` are also immutable; a resumed run whose live detected
ACM minor no longer matches the journal blocks rather than silently switching child/cohort
semantics.

### Python

- FAILED-state retry at ACTIVATION or later loads the existing journal through RunRecord,
  validates it strictly, and follows the July reconciliation/state-machine rules.
- `--reset-state` is the explicit fresh-run boundary. After a successful full reset the old
  journal is absent and a later activation may create a new transaction.
- Ordinary phase retry is not a journal reset.

### Collection

Current `checkpoint_phase reset_from` preserves `operational_data`. R4-04 makes the
migration consequence explicit and enforces it in the checkpoint ownership layer:

- `plugins/module_utils/checkpoint.py` owns a strict helper that classifies whether a
  requested reset/rewind is compatible with the migration journal.
- `checkpoint_phase` calls that helper **before** applying `reset_from` pruning or
  persistence.
- `reset_from: activation`, `post_activation`, or `finalization` with a valid journal
  retains that journal and revalidates/reuses it. It does not freeze new backups or discard
  cleanup/recovery history.
- A rewind to `preflight` or `primary_prep` while `migration_backups` exists is rejected
  before checkpoint mutation. Those phases precede the freeze boundary; starting a
  genuinely new transaction requires the explicit full checkpoint reset path.
- A corrupt/unreadable store or invalid migration journal blocks reset/rewind; pruning a
  phase marker cannot convert bad evidence into absence.
- Full reset rebuilds the checkpoint with empty `operational_data` through the existing
  reset path; that explicit successful reset is the only collection path that may make an
  existing migration journal absent.

The broader `reset_from` identity-bypass and unsafe-legacy convergence defects remain owned
by `R3-06`. R4-04 does not redesign checkpoint identity. Its narrower invariant is:
**no reset/retry path may silently discard, overwrite, or refreeze an existing migration
transaction.**

## 11. Current implementation-plan boundary

After this exact revised amendment is approved, `superpowers:writing-plans` should produce
the implementation plan from the July baseline plus this amendment. The plan must not
start by editing activation call sites.

Dependency order:

1. **Foundation:** strict journal facade/validator in both form factors; store-read parity;
   collection `status: update` with the `build_phase_transition()` no-op semantics;
   R4-03 strict-read prerequisite/reuse; applicable R4-05 durability prerequisite; exact
   Backup/Restore/Velero evidence types; deterministic fingerprint helper; upstream
   selection helpers; all-six-lane controller-contract fixtures.
2. **Activation and completion evidence:** method-specific trigger semantics; concrete
   Backup freeze including passive auxiliary evidence; normalized/raw passive trigger
   validation; additive expectations/waiver; exact Restore create/guarded-patch inputs;
   lane-specific ACM+Velero completion cohorts; retry/resume.
3. **Finalization and cleanup:** live teardown revalidation; journal-reserved Restore
   cleanup; guarded UID+resourceVersion DELETE/recovery/repair; BackupSchedule enablement
   and integrated-decommission evidence gates.

These are review boundaries, not automatic permission to create three PRs. If the
implementation plan splits delivery across multiple PRs, root `thermos-resolution-plan.md`
must receive one row/branch/worktree per PR and every PR must preserve a safe intermediate
state. No intermediate PR may enable a consumer before its foundation/prerequisite is
merged.

Current collection verification must use the maintained compatibility endpoints:
`ansible-core` 2.16.* / Python 3.11 and `ansible-core` 2.21.* / Python 3.12, with unit,
integration, scenario, syntax, and collection-build surfaces treated separately.
Parity-sensitive implementation also runs the root Python/parity gates required by
`AGENTS.md`.

## Amended acceptance criteria

The July acceptance criteria remain mandatory except where this amendment explicitly
supersedes them.

13. Python accesses the migration vocabulary only through `RunRecord`; collection owns it
    through `module_utils/checkpoint.py` plus `checkpoint_phase`. Production callers do
    not bypass either facade with raw named state/checkpoint reads or writes.
14. Store-read `corrupt`/`unreadable` outcomes are distinct from a readable-store journal
    `absent`/`valid`/`invalid` outcome. Corruption remains persistently blocking across
    invocations in both form factors; collection quarantine cannot turn corruption into
    absence.
15. Every Backup accepted as migration provenance persists and revalidates all seven
    fields: namespace, name, UID, phase, completion timestamp, errors, and warnings.
    Same-name UID replacement or status drift at a required evidence read blocks.
16. Upstream Backup prediction mirrors controller selection **before** applying R4-04's
    stricter success predicate. A controller-selected `PartiallyFailed`/malformed Backup
    blocks; R4-04 never skips it to choose an older successful object the controller would
    not have selected.
17. Correlated generic selection parses the 14-digit timestamp from the ordinary Backup
    **name**, prefers the exact generic name, and when falling back requires exactly one
    raw prefix/non-null-startTimestamp/±30-second candidate before applying R4-04
    eligibility. Full restore journals that result as `backups.resources_generic`.
18. `passive_patch` is the sole permitted R4-04 `latest` trigger. Fresh intent validates
    upstream-normalized `skip/latest/latest` semantics while preserving raw values for the
    guarded mutation. The canonical patch writes lowercase `latest`; an unowned existing
    normalized ManagedClusters `latest` is blocking.
19. Before `passive_patch`, R4-04 freezes ManagedClusters plus the controller-selectable
    auxiliary credential/resources/generic Backup evidence required by the pinned lane.
    Resume never refreezes any of those categories to later alias targets.
20. Passive completion uses an immutable ACM-minor/controller-contract matrix. The
    2.12–2.16 snapshots use the legacy owner-list/ManagedClusters+Credentials+ResourcesGeneric
    model; the pinned 2.17 snapshot additionally requires current-cohort related
    `CredentialsActive`/`ResourcesGenericActive` evidence. Unknown/unmapped controller
    behavior is blocking rather than inferred.
21. `restore.completed_at` for `passive_patch` requires the complete lane-specific child
    cohort to be owner-UID-bound, provenance-bound where consumed by this transaction, and
    `Completed`, regardless of whether ACM phase is `Finished` or conditionally accepted
    `Enabled`. `Enabled` alone never proves activation.
22. One-shot passive Restore creation and full Restore creation use concrete journaled
    Backup names in every ACM Restore field they consume; their required ACM terminal phase
    is `Finished`, and all required Velero child evidence must also be `Completed`.
23. Velero owner validation binds `controller=true`, API group, kind, name, and exact ACM
    Restore UID without pinning the served API version literal. Namespace child sweeps use
    strict complete LIST + client-side owner filtering, never the controller-runtime cache
    index as an API field selector.
24. `cleanupBeforeRestore` and `mutation_kind` are immutable evidence in the journal,
    fingerprint, cleanup-intent copy, resume comparisons, teardown revalidation, final
    pre-delete validation, and parity fixtures. Controller-contract metadata and auxiliary
    Backup evidence remain journal invariants outside the live-spec fingerprint.
25. ManagedCluster names and minimum are additive. A non-empty expected-name set enables
    enforcement unless covered by the audited waiver. `min=0` lowers only the count floor;
    it does not clear names or permit zero when names are expected.
26. Collection mid-phase migration writes use `checkpoint_phase status: update`;
    `build_phase_transition()`/its caller preserve the prior `phase_status` and
    `completed_phases`, and the transition changes only operational data/updated-at while
    replacing the complete non-empty `migration_backups` mapping as one top-level value.
27. R4-04 uses the R4-03 strict inventory authority and extends/reuses the current
    collection lossless-read seam; it creates neither a competing inventory algebra nor a
    second expectation resolver.
28. No R4-04 safety-authorizing transition is considered durable until the applicable
    R4-05 file/directory durability contract is satisfied.
29. Retry/resume never changes frozen concrete Backup evidence or controller-contract
    identity. Collection rewinds at or after activation retain/revalidate the journal;
    rewinds before the freeze boundary refuse while a journal exists and require explicit
    full reset for a new transaction.
30. Tests pin the upstream selection, sync-trigger, owner, status-association, and
    completion-cohort contracts against all six immutable controller snapshots above.
    Negative fixtures include raw generic-fallback ambiguity, a failed candidate that would
    sort ahead of an eligible one, wrong owner UID, replacement child UID, wrong
    `spec.backupName`, stale/unowned passive `latest`, unchanged ManagedClusters status
    name, missing/failed 2.17 `-active` child, malformed/incomplete child LIST, malformed
    store/journal, non-terminal ACM/Velero phases, and min-zero-with-names.

## Written-spec review gate

This amendment deliberately stops before implementation planning. Review must verify at
least these points against current source and the pinned upstream snapshots:

- the RunRecord/checkpoint facade ownership matches current guardrails;
- `status: update` explicitly preserves `phase_status` and `completed_phases` at the
  current `build_phase_transition()` integration point;
- corruption cannot become journal absence on a subsequent invocation;
- the strict-read dependency does not duplicate R3-02/R4-03 plumbing;
- the seven-field Backup projection is available from the supported Velero API surface;
- `latest` selection is predicted in the controller's raw order before R4 eligibility is
  applied;
- generic-resource exact/fallback selection uses the Backup-name timestamp target and
  computes ambiguity over the unfiltered upstream fallback candidate set;
- the pinned 2.12, 2.13, 2.14, 2.15, 2.16, and 2.17 snapshots support the controller
  contract matrix stated in §5;
- passive auxiliary credential/resources/generic Backup inputs are frozen before mutation
  and every transaction-consumed child binds to the corresponding evidence;
- the 2.17 `Enabled` path cannot accept while a required current `-active` child is failed,
  missing, malformed, or still running;
- legacy-lane owner-list semantics and their deliberate fail-closed behavior are acceptable
  for the supported workflow;
- the passive-patch alias-resolution race is stated as detection-after-mutation rather than
  prevention, and retaining that limitation is acceptable for the supported workflow;
- method-specific ACM phase rules cannot accept the pre-existing passive `Enabled` state as
  this run's completion;
- ownerReference matching does not depend on one served API version or a non-portable
  server-side field selector;
- `cleanupBeforeRestore` and `mutation_kind` are included consistently in every
  fingerprint/cleanup gate;
- expected names still enforce when the explicit minimum is zero;
- reset/retry semantics cannot silently mint a new migration transaction or switch its
  controller contract;
- durability prerequisites are explicit and do not weaken R4-05;
- no protected-file, RBAC, release-validation, lab-controller, or unrelated old-hub
  cleanup authority is implied.

Only after that exact written review is accepted does the implementation-plan gate open.
