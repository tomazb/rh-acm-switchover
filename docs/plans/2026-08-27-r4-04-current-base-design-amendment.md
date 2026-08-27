# R4-04 ManagedCluster Migration Evidence — Current-Base Amendment

**Date:** 2026-08-27
**Base:** `ansible@acb002eb561055deb9cdb9a44c4ea74fea10fd41`
**Status:** revised normative amendment awaiting written-spec review; no implementation plan or implementation authority
**Amends:** `docs/plans/2026-07-29-migration-evidence-design.md`

## Authority and scope

This document is the current-base amendment to the accepted 2026-07-29 R4-04 design.
The July design remains the baseline for the migration-evidence transaction. Where this
amendment conflicts with that document, **this amendment wins**. Where it is silent, the
July design remains normative.

The amendment exists because the repository architecture and adjacent safety work changed
after the July design, and because the root-level
`thermos-resolution-plan.md` section
**"Convergence-rule triage — CodeRabbit round of 2026-08-02 (18 findings)"**
left four R4-04 implementation obligations, two of them explicit design choices, to the
implementing slice. A first current-base amendment was independently reviewed and found to
contain two blocking assumptions: generic-resource provenance was incorrectly bound to the
ordinary resources Backup, and the passive-patch path was treated as if its upstream
`latest` trigger could always be replaced with a concrete Backup name. This revision
resolves those assumptions before the implementation-plan gate.

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

Two July statements are narrowed by this amendment:

1. **"Every Restore names concrete backups" is not universally implementable.**
   For passive in-place activation, supported cluster-backup-operator versions require the
   sync Restore's ManagedCluster field to remain within the `skip`/`latest` contract.
   R4-04 therefore permits `latest` only at that explicitly documented upstream-required
   passive-patch trigger boundary and binds it to concrete pre- and post-mutation evidence.
   One-shot passive Restore creation and full Restore creation still use concrete names.
2. **The resources category is not a one-Backup evidence domain.**
   Full restore can consume both `acm-resources-schedule-*` and the distinct
   `acm-resources-generic-schedule-*` Backup. The generic Backup has no ACM Restore spec
   field of its own; it is operator-derived and must be journaled and verified separately.

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
  validates a complete candidate record, then asks `RunRecord` to persist that value as
  one critical state operation. Splitting one logical transition across unrelated config
  keys is forbidden.
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
  phase; a missing checkpoint or phase mismatch is fatal. The transition may update
  `operational_data` and `updated_at` only. It must not alter `phase`,
  `completed_phases`, `phase_status`, `errors`, or `report_refs`. `error` and
  `report_ref` inputs are invalid for this status.
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

`corrupt` and `unreadable` are blocking outcomes. Neither may be mapped to journal
absence.

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

## 3. Backup evidence is UID-bound and status-complete

The July schema's `{name, completed_at}` backup entries are superseded. Every consumed
Backup stores this seven-field provenance projection:

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
**seven** fields are required. Eligibility remains strict:

- `status.phase == "Completed"`;
- a present, well-formed `status.completionTimestamp`;
- `status.errors` is a well-formed non-negative integer equal to zero;
- `status.warnings` is a well-formed non-negative integer;
- `metadata.name` and `metadata.uid` are non-empty strings;
- `metadata.namespace` equals the exact backup namespace used by the strict read.

Every required Backup is strictly GET/revalidated by namespace/name immediately before
the Restore mutation boundary. UID and the complete persisted status projection must
still match. A same-name Backup with a different UID is a replacement and blocks; the run
never rebinds the journal to it. Status drift away from the persisted successful
projection also blocks. This proves provenance at each required read instant; it does not
claim an admission lock after that read returns.

### Journal categories

The journal uses these categories:

```yaml
backups:
  managed_clusters: {<seven-field projection>}
  credentials: {<seven-field projection>}       # full only
  resources: {<seven-field projection>}         # full only
  resources_generic: {<seven-field projection>} # full only, operator-derived
```

`resources_generic` is intentionally **not** a Restore spec-field mapping. Supported
cluster-backup-operator versions expose only three backup-name fields on the ACM Restore:
managed clusters, resources, and credentials.

### Full-restore generic-resource selection

For full activation, R4-04 predicts the generic-resources Backup using the supported
cluster-backup-operator selection semantics before mutation:

1. Start from the already frozen concrete `backups.resources.name`.
2. Derive the exact generic candidate by replacing the ordinary resources schedule prefix
   with `acm-resources-generic-schedule` while preserving the backup timestamp suffix.
3. If that exact Backup exists, require it to satisfy the seven-field success projection
   and freeze it as `backups.resources_generic`.
4. If the exact name does not exist, emulate the operator's ±30-second
   `status.startTimestamp` fallback over strictly and completely listed generic-resource
   Backups. R4-04 accepts the fallback only when **exactly one** eligible candidate falls
   inside that operator window. Zero candidates or more than one candidate is fatal before
   mutation because the tool cannot prove which Backup the operator will choose.
5. Immediately before Restore creation, revalidate both `backups.resources` and
   `backups.resources_generic` by namespace/name/UID/status.

After the operator publishes the generic Velero Restore, its actual
`spec.backupName` must equal this frozen `resources_generic` Backup. The operator, not the
switchover tool, chooses the generic Backup through its own controller logic; R4-04 proves
that the choice matches the precomputed unambiguous evidence.

The current repository's `ACM_BACKUP_SCHEDULE_TYPES` and `ACM_BACKUP_NAME_RE` do not know
about `resources_generic`. R4-04 implementation must extend only the R4-04 evidence
selection surface needed for this category; it must not silently broaden unrelated backup
classification without tests and explicit scope.

## 4. Restore trigger and spec-binding semantics

The July rule that `latest` never appears in a journaled Restore spec is superseded by the
method-specific contract below.

The journal adds an immutable normalized mutation kind:

```yaml
restore:
  activation_method: passive | full
  mutation_kind: passive_patch | passive_restore | full_restore
```

`mutation_kind` is copied into cleanup intent and included in all strict
journal/resume/fingerprint consistency checks.

### `passive_patch`

The existing passive sync Restore is a `syncRestoreWithNewBackups: true` object. In both
the ACM 2.12-era and 2.17-era controller snapshots, valid sync options require:

- `veleroManagedClustersBackupName` to be `skip` or `latest`;
- `veleroCredentialsBackupName` to be `latest`;
- `veleroResourcesBackupName` to be `latest`.

A concrete ManagedCluster Backup name would invalidate sync processing. Therefore R4-04
does **not** patch a concrete name into this existing sync Restore.

For `passive_patch`:

1. Freeze and persist the concrete `backups.managed_clusters` seven-field evidence before
   mutation.
2. On a fresh `passive_patch` transaction, require the live Restore to remain a valid
   passive-sync source: `syncRestoreWithNewBackups == true`,
   `veleroManagedClustersBackupName == "skip"`, and the credentials/resources backup
   fields remain `latest`. A live ManagedCluster field already equal to `latest` without
   this journal's accepted patch evidence is not adopted as a fresh activation.
3. Persist the pre-patch ACM Restore identity, generation, `mutation_kind`, and the owned
   pre-patch spec projection.
4. The guarded JSON Patch still tests exact UID and resourceVersion first, but its sole
   mutation sets `/spec/veleroManagedClustersBackupName` to the literal `latest`.
5. `restore.backup_fields` for this mutation kind therefore contains exactly:
   `{"veleroManagedClustersBackupName": "latest"}`. This is the **only** R4-04
   `latest` sentinel permitted in an owned mutation projection.
6. The run records the pre-patch
   `status.veleroManagedClustersRestoreName` and requires a different non-empty status name
   to appear after the patch.
7. That newly published Velero Restore must pass the association/evidence rules in §5 and
   its `spec.backupName` must equal the frozen concrete
   `backups.managed_clusters.name`.
8. Any alias drift that selects a different concrete Backup is therefore detected before
   completion evidence is written. The trigger can be `latest`; the accepted provenance
   cannot.

On resume after this journal has already recorded an accepted guarded patch, a live
`latest` field is expected and the run reconciles the existing transaction instead of
requiring the fresh-intent `skip` precondition again. A live `latest` field without that
journal evidence is stale/unowned and blocking.

The remaining concurrency limit is explicit. The cluster-backup-operator resolves
`latest` after the PATCH reaches the cluster, so R4-04 cannot prevent an unrelated actor
from creating a newer eligible Backup between the tool's final pre-patch Backup read and
the controller's alias resolution. R4-04 detects that outcome when the new Velero Restore
is bound to a concrete `spec.backupName` and blocks all completion/finalization evidence
if it differs from the frozen Backup, but the mismatched Restore may already have run.
Eliminating that last alias-resolution window requires retiring/coercing
`passive_patch` to the one-shot Restore mechanism or another upstream coordination
mechanism; that behavior change is outside this approved compatibility-preserving
revision and requires separate operator approval.

### `passive_restore`

The one-shot activation Restore created after deleting/replacing the passive sync Restore
is not a sync Restore. Its ManagedCluster field uses the concrete frozen
`backups.managed_clusters.name`; credentials/resources remain the documented skip values.
`restore.backup_fields` contains only the concrete ManagedCluster field, as in the July
method-scoped projection.

### `full_restore`

All three ACM Restore backup-name fields use their concrete journaled names:

- managed clusters → `backups.managed_clusters.name`;
- credentials → `backups.credentials.name`;
- resources → `backups.resources.name`.

`backups.resources_generic` is evidence-only because the ACM Restore API has no fourth
generic backup-name field. The generic Velero Restore is verified after the controller
selects it.

## 5. ACM Restore to Velero Restore association and terminal evidence

Association is never inferred from a Restore naming convention alone.

Across the pinned ACM 2.12-era and 2.17-era cluster-backup-operator API snapshots,
`RestoreStatus` exposes:

- `veleroManagedClustersRestoreName`;
- `veleroCredentialsRestoreName`;
- `veleroResourcesRestoreName`;
- `veleroGenericResourcesRestoreName`.

The controller sets those fields from Velero Restores it creates and applies a controller
owner reference before creating each Velero Restore.

For every method-required status association, R4-04:

1. Reads the status name from the same strictly validated, UID-bound ACM Restore whose
   completion evidence is being considered.
2. Strictly GETs that exact `velero.io/v1 Restore` in the same namespace.
3. Requires non-empty Velero Restore `metadata.uid` and journals namespace/name/UID.
4. Requires exactly one controller ownerReference matching:
   - API **group** `cluster.open-cluster-management.io` (served version is not pinned);
   - `kind: Restore`;
   - the exact journaled ACM Restore name;
   - the exact journaled ACM Restore UID;
   - `controller: true`.
5. Requires Velero `spec.backupName` to equal the concrete journaled Backup for that
   association.
6. Requires Velero `status.phase == "Completed"`. Missing, malformed, failed,
   partially-failed, validation-failed, in-progress, or unrecognized phases are
   non-success.

The journal stores associations as lists so fan-out is explicit:

```yaml
restore:
  velero_restores:
    managed_clusters:
      - {namespace: "<ns>", name: "<name>", uid: "<uid>"}
    credentials: []
    resources: []
    resources_generic: []
```

Method requirements:

- **passive_patch** — a newly published ManagedClusters association is mandatory and must
  bind to `backups.managed_clusters`;
- **passive_restore** — the ManagedClusters association is mandatory and binds to
  `backups.managed_clusters`;
- **full_restore** — ManagedClusters, credentials, ordinary resources, and generic
  resources associations are mandatory and bind respectively to
  `backups.managed_clusters`, `backups.credentials`, `backups.resources`, and
  `backups.resources_generic`.

A duplicate status locator normalizes to one unique object only when every observation is
identical; conflicting evidence is malformed. A missing status name, unrelated owner,
owner UID mismatch, replacement Velero Restore UID, wrong `spec.backupName`, unreadable
object, or non-`Completed` phase blocks and writes no `restore.completed_at`.

### Method-specific ACM Restore phase rule

`restore.completed_at` is not derived from one phase literal for every activation
mechanism.

- **`passive_restore` and `full_restore`** require ACM
  `Restore.status.phase == "Finished"` plus all method-required Velero associations above.
  `FinishedWithErrors` is blocking; the migration-evidence transaction has no benign
  error-message allow-list.
- **`passive_patch`** may observe ACM `Enabled` as the pre-activation sync-ready state and
  can observe `Enabled` transiently while the controller begins activation. `Enabled`
  alone is never completion evidence. R4-04 accepts ACM phase `Finished`, or `Enabled`
  only when **all** of these additional facts are true:
  1. the guarded patch/reconciliation evidence is complete for the journaled ACM Restore
     UID/generation/spec projection;
  2. a new `veleroManagedClustersRestoreName` different from the pre-patch name was
     published after the mutation;
  3. that exact Velero Restore is UID-bound to the ACM Restore owner, uses the frozen
     concrete ManagedCluster Backup, and is `Completed`;
  4. the expected ManagedCluster-name predicate for activation has passed or is covered by
     the explicit audited waiver.
  `FinishedWithErrors`, `Error`, `Unknown`, missing/malformed phase, and every
  unrecognized value remain blocking.

The stricter `Finished` outcome remains preferred when the controller reaches it. The
conditional `Enabled` rule exists only to preserve supported passive-patch semantics
without treating the pre-existing sync-ready state as proof that this run restored
anything.

### Upstream basis

The implementation plan must pin exact upstream references. This design was revalidated
against these immutable snapshots:

- ACM 2.12-era:
  `stolostron/cluster-backup-operator@74b54988a5bd6712ea3fe3e9ceb770e06db91e8b`,
  including `api/v1beta1/restore_types.go`, `controllers/restore_controller.go`, and
  `controllers/restore.go`;
- ACM 2.17-era:
  `stolostron/cluster-backup-operator@c8578f94df09deab561e1aa5a7e9fc9b57f7d113`,
  including the same API/controller files.

Both expose the four status-name fields, the three-field Restore backup API, the
`skip`/`latest` sync-option contract, distinct ordinary/generic resources Backup schedule
prefixes, and controller-owned Velero Restores. The implementation plan must re-check all
repo-supported ACM lanes it claims, not infer intermediate-version behavior merely from
the endpoints.

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
single value is the permitted trigger sentinel `latest`; the concrete provenance lives in
`backups.managed_clusters` and the Velero association. For one-shot passive/full
Restores, the consumed owned fields are concrete.

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
- In Python, the returned `enforce_expected_managed_cluster_names` value is therefore true
  whenever effective expected names are non-empty and no valid waiver covers the current
  predicate.
- In the collection, `acm_switchover_resolved_allow_zero_managed_clusters` may be true only
  when the effective expected-name set is empty. If names exist, the resolved value is
  false so the published facts do not simultaneously say "zero permitted" and "these
  names are mandatory".
- Restore-only default-floor behavior is preserved when no discovered/configured
  expectation exists.

The waiver never rewrites or erases the recorded expectation; it records that one
predicate was explicitly waived.

## 8. Strict Kubernetes reads: reuse the current architecture

R4-03 still owns the complete shared strict-inventory contract and is a prerequisite for
R4-04 inventory consumers. Python's current `KubeClient.list_custom_resources()` is not
acceptable because list 404 can become `[]`, malformed `items` can be accepted, and
bounded truncation can return a partial inventory.

The collection has gained `acm_k8s_read_outcome` since the July design. It already gives
a useful sanitized one-read `ok` / named-`not_found` / `error` contract. R4-04/R4-03 must
**reuse or extend that seam** where applicable rather than creating a second lossless-read
abstraction. Its current contract does not itself satisfy complete-pagination inventory
semantics.

Implementation order remains fixed:

1. R4-03's shared strict outcome/pagination contract is implemented with parity vectors in
   both form factors, reusing collection read-outcome plumbing where natural.
2. R4-04 switches Backup discovery, ManagedCluster inventories, and passive Restore
   discovery to that complete contract.
3. R4-04's named-object Backup/ACM-Restore/Velero-Restore evidence reads use matching
   strict named-object semantics; API/discovery/auth/transport/decode failure is never
   absence.

No R4-04 plan may wire a safety decision to the current advisory-shaped Python list helper
or to a collection result whose completeness has not been positively established.

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

Once the first backup freeze is durably recorded, the migration journal represents one
immutable migration transaction. Retry/resume reuses and revalidates it; it never chooses
a newer Backup merely because the phase is being retried.

For `passive_patch`, this "no refreeze" rule applies to the **concrete**
`backups.managed_clusters` evidence even though the live trigger field is `latest`.
A resumed run never updates the journal to whichever Backup `latest` means later.

### Python

- FAILED-state retry at ACTIVATION or later loads the existing journal through RunRecord,
  validates it strictly, and follows the July reconciliation/state-machine rules.
- `--reset-state` is the explicit fresh-run boundary. After a successful full reset the
  old journal is absent and a later activation may create a new transaction.
- Ordinary phase retry is not a journal reset.

### Collection

Current `checkpoint_phase reset_from` preserves `operational_data`. R4-04 makes the
migration consequence explicit and enforces it in the checkpoint ownership layer:

- `plugins/module_utils/checkpoint.py` owns a strict helper that classifies whether a
  requested reset/rewind is compatible with the migration journal.
- `checkpoint_phase` calls that helper **before** applying `reset_from` pruning or
  persistence.
- `reset_from: activation`, `post_activation`, or `finalization` with a valid journal
  retains that journal and revalidates/reuses it. It does not freeze new backups or
  discard cleanup/recovery history.
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

After this exact revised amendment is approved,
`superpowers:writing-plans` should produce the implementation plan from the July baseline
plus this amendment. The plan must not start by editing activation call sites.

Dependency order:

1. **Foundation:** strict journal facade/validator in both form factors; store-read parity;
   collection `status: update`; R4-03 strict-read prerequisite/reuse; applicable R4-05
   durability prerequisite; exact backup/Restore/Velero evidence types; deterministic
   fingerprint helper; generic-resource selection parity fixtures.
2. **Activation and completion evidence:** method-specific trigger semantics; concrete
   Backup freeze; passive `latest` trigger binding; additive expectations/waiver; exact
   Restore create/guarded-patch inputs; ACM+Velero completion evidence; retry/resume.
3. **Finalization and cleanup:** live teardown revalidation; journal-reserved Restore
   cleanup; guarded UID+resourceVersion DELETE/recovery/repair; BackupSchedule enablement
   and integrated-decommission evidence gates.

These are review boundaries, not automatic permission to create three PRs. If the
implementation plan splits delivery across multiple PRs, root
`thermos-resolution-plan.md` must receive one row/branch/worktree per PR and every PR must
preserve a safe intermediate state. No intermediate PR may enable a consumer before its
foundation/prerequisite is merged.

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
15. Every consumed Backup persists and revalidates all seven provenance fields:
    namespace, name, UID, phase, completion timestamp, errors, and warnings. Same-name UID
    replacement or status drift at a required evidence read blocks.
16. Full restore journals a distinct `backups.resources_generic`. Exact timestamp
    correlation is preferred; the ±30-second operator fallback is accepted only with one
    eligible candidate. The published generic Velero Restore must bind by UID and
    `spec.backupName` to that frozen generic Backup.
17. `passive_patch` is the sole permitted R4-04 `latest` trigger. A fresh transaction
    starts only from the valid sync shape with ManagedClusters `skip`; an unowned existing
    `latest` is blocking. The trigger is accepted only with a pre-frozen concrete
    ManagedCluster Backup and a newly published, owner-UID-bound, `Completed`
    ManagedClusters Velero Restore whose `spec.backupName` equals that frozen Backup.
    Resume never refreezes to a later alias target. A post-patch alias mismatch blocks all
    later evidence and is reported as detected-after-mutation, never as prevention.
18. One-shot passive Restore creation and full Restore creation use concrete journaled
    backup names in every ACM Restore field they consume.
19. `restore.completed_at` is method-specific. `passive_restore`/`full_restore` require ACM
    `Finished` plus all required `Completed` Velero associations. `passive_patch` may
    accept `Enabled` only under the four conjunctive post-mutation evidence requirements
    in §5; `Enabled` alone never proves activation.
20. Velero association owner validation binds controller=true, API group, kind, name, and
    exact ACM Restore UID without pinning the served API version literal.
21. `cleanupBeforeRestore` and `mutation_kind` are immutable evidence in the journal,
    fingerprint, cleanup-intent copy, resume comparisons, teardown revalidation, final
    pre-delete validation, and parity fixtures.
22. ManagedCluster names and minimum are additive. A non-empty expected-name set enables
    enforcement unless covered by the audited waiver. `min=0` lowers only the count floor;
    it does not clear names or permit zero when names are expected.
23. Collection mid-phase migration writes use `checkpoint_phase status: update`; that
    status changes only operational data/updated-at and replaces the complete non-empty
    `migration_backups` mapping as one top-level value.
24. R4-04 uses the R4-03 strict inventory authority and extends/reuses the current
    collection lossless-read seam; it creates neither a competing inventory algebra nor a
    second expectation resolver.
25. No R4-04 safety-authorizing transition is considered durable until the applicable
    R4-05 file/directory durability contract is satisfied.
26. Retry/resume never changes frozen concrete Backup evidence. Collection rewinds at or
    after activation retain/revalidate the journal; rewinds before the freeze boundary
    refuse while a journal exists and require explicit full reset for a new transaction.
27. Tests pin the upstream association and sync-trigger contracts against immutable
    cluster-backup-operator sources for every claimed ACM support lane, including negative
    fixtures for generic-resource ambiguity, wrong owner UID, replacement Velero Restore
    UID, wrong `spec.backupName`, stale passive `latest`, unchanged pre-patch Velero
    Restore name, malformed store/journal, non-terminal phases, and min-zero-with-names.

## Written-spec review gate

This amendment deliberately stops before implementation planning. Review must verify at
least these points against current source and the pinned upstream snapshots:

- the RunRecord/checkpoint facade ownership matches current guardrails;
- `status: update` can be added without changing ordinary phase-transition semantics;
- corruption cannot become journal absence on a subsequent invocation;
- the strict-read dependency does not duplicate R3-02/R4-03 plumbing;
- the seven-field Backup projection is available from the supported Velero API surface;
- generic-resource preselection matches the operator's exact-name/±30-second algorithm
  and fails closed on ambiguity;
- passive-patch `latest` is required by the supported sync contract and is bound to one
  concrete frozen Backup by post-mutation Velero evidence;
- the passive-patch alias-resolution race is stated as detection-after-mutation rather
  than prevention, and retaining that limitation is acceptable for the supported workflow;
- method-specific ACM phase rules cannot accept the pre-existing passive `Enabled` state as
  this run's completion;
- ownerReference matching does not depend on one served API version;
- `cleanupBeforeRestore` and `mutation_kind` are included consistently in every
  fingerprint/cleanup gate;
- expected names still enforce when the explicit minimum is zero;
- reset/retry semantics cannot silently mint a new migration transaction;
- durability prerequisites are explicit and do not weaken R4-05;
- no protected-file, RBAC, release-validation, lab-controller, or unrelated old-hub
  cleanup authority is implied.

Only after that exact written review is accepted does the implementation-plan gate open.
