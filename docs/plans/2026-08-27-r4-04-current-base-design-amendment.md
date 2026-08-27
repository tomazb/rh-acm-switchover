# R4-04 ManagedCluster Migration Evidence — Current-Base Amendment

**Date:** 2026-08-27
**Base:** `ansible@acb002eb561055deb9cdb9a44c4ea74fea10fd41`
**Status:** normative amendment awaiting written-spec review; no implementation plan or implementation authority
**Amends:** `docs/plans/2026-07-29-migration-evidence-design.md`

## Authority and scope

This document is the current-base amendment to the accepted 2026-07-29 R4-04 design. The
July design remains the baseline for the migration-evidence transaction. Where this
amendment conflicts with that document, **this amendment wins**. Where it is silent, the
July design remains normative.

The amendment exists because the repository architecture and adjacent safety work changed
after the July design, and because the 2026-08-02 design-hardening ledger deliberately
left four R4-04 choices to the implementing slice. Current-source revalidation on the base
above confirms the underlying R4-04 defects are still open in both supported form factors:
Restore sources still use the moving `latest` alias, explicit ManagedCluster floors can
suppress expected-name enforcement, the Python generic list helper still collapses list
404 into an empty inventory, and integrated finalization still has no migration-evidence
barrier before the journaled Restore is cleaned up and the old hub can be handled.

This is a **design-only** amendment. It changes no runtime, test, RBAC, manifest, Helm,
release-validation, lab-controller, protected-file, or support behavior. R4-04 remains
`planned`. The implementation plan is written only after this exact written amendment is
reviewed and approved.

## 1. Current persistence ownership

The July design predates the repository's named cross-phase state facades. R4-04 must use
them; it must not reopen raw state access.

### Python CLI

- `lib/run_record.py::RunRecord` owns the Python `migration_backups` vocabulary and its
  typed/named read-write operations.
- `lib/utils.py::StateManager` owns storage mechanics only: locking, atomic file
  replacement, dirty tracking, and durability primitives. Runtime R4-04 code does not call
  `_set_config` / `_get_config` and does not read a named migration key directly from a
  state snapshot.
- `migration_backups` remains one top-level config value. A journal transition builds and
  validates a complete candidate record, then asks `RunRecord` to persist that value as
  one critical state operation. Splitting one logical transition across unrelated config
  keys is forbidden.
- `tests/test_run_record_guardrails.py` remains the structural guardrail; R4-04 adds
  interface tests that prove production migration code reaches the journal only through
  `RunRecord`.

### Ansible collection

- `plugins/module_utils/checkpoint.py` owns the collection checkpoint key vocabulary.
  R4-04 adds the named migration-journal key and the strict typed/validated facade there.
- `checkpoint_phase` remains the authoritative checkpoint persistence boundary. Mid-phase
  R4-04 transitions use its explicit `operational_data` update path; no second state file,
  sidecar journal, or cross-form-factor store is introduced.
- Role and playbook YAML consume named flattened facts or a dedicated validated journal
  result published by the checkpoint facade. They do not walk raw `operational_data`.
  `test_checkpoint_vocabulary_guardrail.py` remains the structural guardrail.

The two codebases remain independent and never import from one another. Parity means the
same schema, validation, transition decisions, recovery states, and externally observable
outcomes, not shared persistence.

## 2. Strict journal validation, not tolerant summary parsing

Existing run-summary/checkpoint-fact readers intentionally degrade malformed ordinary
state to defaults. That tolerance model is **not** allowed for R4-04 evidence.

Each form factor owns an independent strict migration-journal validator with the same test
vectors and these exact outcomes:

- **absent** — `migration_backups` has never been created. This is distinguishable from a
  present empty/malformed value and is allowed only before the freeze boundary or after an
  explicitly successful full state/checkpoint reset;
- **valid** — the complete schema and every state-dependent invariant validate;
- **invalid** — present but malformed, wrong-versioned, partial, internally inconsistent,
  or carrying an impossible state transition. Invalid evidence is blocking before any
  R4-04 mutation, Restore cleanup, BackupSchedule enablement, finalization completion, or
  integrated teardown.

No wrong-typed or missing R4-04 field silently becomes `None`, `[]`, `{}`, `0`, or
`False`. Unknown future schema versions fail closed rather than being treated as an empty
legacy record. Validation is performed before consuming a persisted transition and again
before writing a candidate transition.

## 3. Backup evidence is UID-bound and status-complete

The July schema's `{name, completed_at}` backup entries are superseded. Every consumed
backup category stores the complete validated provenance projection:

```yaml
backups:
  managed_clusters:
    namespace: open-cluster-management-backup
    name: "<concrete Velero Backup metadata.name>"
    uid: "<Velero Backup metadata.uid>"
    phase: Completed
    completed_at: "<status.completionTimestamp>"
    errors: 0
    warnings: 0
  credentials:     # full only; same shape
    namespace: open-cluster-management-backup
    name: "<concrete name>"
    uid: "<uid>"
    phase: Completed
    completed_at: "<completionTimestamp>"
    errors: 0
    warnings: 0
  resources:       # full only; same shape
    namespace: open-cluster-management-backup
    name: "<concrete name>"
    uid: "<uid>"
    phase: Completed
    completed_at: "<completionTimestamp>"
    errors: 0
    warnings: 0
```

The `warnings` value is the exact non-negative integer observed; `0` above is illustrative.
All six fields are required for every consumed category. The existing eligibility rules
remain: phase exactly `Completed`, a well-formed completion timestamp, `errors == 0`, and
well-formed non-negative `warnings`. `name` and `uid` are non-empty strings and
`namespace` is the exact backup namespace used by the read.

The selected Backup is strictly GET/revalidated by namespace/name immediately before the
Restore mutation boundary. Its UID and the complete persisted status projection must still
match. A same-name Backup with a different UID is a replacement and blocks; the run never
rebinds the journal to it. A status change away from the persisted successful projection
also blocks. This proves provenance at each required read instant; it does not claim an
admission lock that prevents a Backup replacement after the read returns.

This resolves the 2026-08-02 R4-04 provenance choice in the stronger direction: same-name
Backup replacement is **inside** the guarantee and is detected whenever it is observable at
a required strict read.

## 4. ACM Restore to Velero Restore association is explicit

`restore.completed_at` requires both the ACM Restore's terminal success and terminal
success of every Velero Restore that ACM explicitly publishes as part of the consumed
categories. Association is never inferred from a naming convention or from "latest".

The association source is the ACM Restore status API exposed across the repository's ACM
2.12-through-2.17 test target range. The 2.12-era and 2.17-era
cluster-backup-operator `RestoreStatus` both expose:

- `veleroManagedClustersRestoreName`;
- `veleroCredentialsRestoreName`;
- `veleroResourcesRestoreName`;
- `veleroGenericResourcesRestoreName`.

The cluster-backup-operator controller sets those fields from the Velero Restores it
creates and sets the ACM Restore as their controller owner. Its resources backup can fan
out into resources and generic-resources Velero Restores, so the R4-04 association is not
forced into a false one-to-one category model.

For each required status association, R4-04:

1. Reads the name from the same strictly validated, UID-bound ACM Restore whose terminal
   status is being accepted.
2. Strictly GETs that exact `velero.io/v1 Restore` in the same namespace.
3. Requires a non-empty Velero Restore `metadata.uid` and journals
   `namespace`/`name`/`uid`.
4. Requires a controller `ownerReference` whose `apiVersion` is
   `cluster.open-cluster-management.io/v1beta1`, `kind` is `Restore`, `name` equals the
   journaled ACM Restore name, `uid` equals the journaled ACM Restore UID, and
   `controller` is true. A same-name owner with a different UID is not sufficient.
5. Requires Velero `spec.backupName` to equal the exact concrete, UID-bound journaled
   Backup name for that category.
6. Requires Velero `status.phase == "Completed"`. Every other/missing/malformed phase is
   non-success under the July terminal-phase rule.

Method-specific requirements are exact:

- **passive** — `veleroManagedClustersRestoreName` must be non-empty and its one associated
  Velero Restore must pass all six checks;
- **full / managed clusters** — same requirement;
- **full / credentials** — `veleroCredentialsRestoreName` must be non-empty and pass all
  checks against `backups.credentials`;
- **full / resources** — at least one of `veleroResourcesRestoreName` and
  `veleroGenericResourcesRestoreName` must be non-empty, and **every non-empty one** must
  pass all checks against `backups.resources`. Duplicate status names normalize to one
  unique locator before validation; conflicting evidence for that locator is malformed.

The journal stores the resulting associations under the Restore evidence, with lists used
so resources fan-out is represented without ambiguity:

```yaml
restore:
  velero_restores:
    managed_clusters:
      - {namespace: "<ns>", name: "<name>", uid: "<uid>"}
    credentials: []
    resources: []
```

Only categories consumed by the selected method may be non-empty. For full activation,
all three category requirements above must be satisfied. A missing status name, unrelated
Velero Restore, owner UID mismatch, replacement UID, `spec.backupName` mismatch,
unreadable object, or non-`Completed` phase blocks and writes no
`restore.completed_at`.

This association is deliberately stronger than merely checking that some Velero Restore
with the expected backup name exists. It binds operator-published status, ACM Restore UID,
Velero Restore UID, and the concrete Backup name into one evidence chain.

### Upstream basis for the association

The implementation plan must pin the exact upstream references it uses. The design
revalidation inspected these immutable snapshots:

- ACM 2.12-era API snapshot:
  `stolostron/cluster-backup-operator@74b54988a5bd6712ea3fe3e9ceb770e06db91e8b`,
  `api/v1beta1/restore_types.go`;
- ACM 2.17-era API/controller snapshot:
  `stolostron/cluster-backup-operator@c8578f94df09deab561e1aa5a7e9fc9b57f7d113`,
  `api/v1beta1/restore_types.go` and `controllers/restore_controller.go`/
  `controllers/restore.go`.

Both inspected API endpoints expose the same four status-name fields. Current controller
source sets those fields when creating the corresponding Velero Restores and applies a
controller owner reference to the Velero Restore before creation.

## 5. `cleanupBeforeRestore` is part of the exact-spec claim

The 2026-08-02 design-hardening choice is resolved by **including**
`cleanupBeforeRestore`, not by weakening the exact-spec claim.

The journal adds the immutable field:

```yaml
restore:
  cleanup_before_restore: CleanupRestored
cleanup:
  cleanup_before_restore: null | CleanupRestored
```

`CleanupRestored` is the current Python `CLEANUP_BEFORE_RESTORE_VALUE`; the collection
must use the parity-equivalent exact value. Activation records the exact normalized value
before mutation and every Restore create/post-read/evidence/revalidation boundary requires
the live `spec.cleanupBeforeRestore` to match it. Cleanup intent copies it structurally
unchanged from `restore` and all cleanup/resume/final-delete consistency checks include it.

The canonical fingerprint projection from the July design is therefore superseded by
exactly:

```json
{"activation_method":"<passive|full>","backup_fields":{},"cleanup_before_restore":"CleanupRestored"}
```

where `backup_fields` is the exact method-scoped map from the July design. Serialization
and SHA-256 rules are otherwise unchanged: sorted JSON keys, no insignificant whitespace,
ASCII escaping identical in both form factors, lowercase SHA-256 hex. No live-spec field
outside this explicit projection is claimed by the fingerprint.

The final pre-delete projection, teardown revalidation, cleanup intent copy, parity
fixtures, and digest checks all include `cleanup_before_restore`. Any missing, extra,
wrong-typed, or changed value blocks before mutation/deletion.

## 6. ManagedCluster expectation changes use the existing seams

R4-04 does not add another expectation resolver.

- Python evolves the existing `RunRecord`/`ManagedClusterExpectation`-backed resolution
  path so a configured/derived expected-name set and an explicit minimum are additive.
- The collection evolves `roles/common/tasks/resolve_managed_cluster_expectation.yml`
  with the same rule.
- Explicit minimum `0` therefore never clears an available expected-name set.
- The only name-predicate bypass remains the July design's explicit audited waiver, stored
  inside the strict migration journal. It does not rewrite or erase the expectation.

All current restore-only defaults and the July waiver scope/outcome rules remain unchanged.

## 7. Strict Kubernetes reads: reuse the current architecture

R4-03 still owns the complete shared strict-inventory contract and remains a prerequisite
for R4-04 inventory consumers. In particular, Python's current
`KubeClient.list_custom_resources()` is not acceptable because list 404 can still become
`[]`, and the R4-03 contract additionally owns complete pagination and malformed-response
semantics.

The collection has gained `acm_k8s_read_outcome` since the July design. It already gives a
useful sanitized one-read `ok` / named-`not_found` / `error` contract. R4-04/R4-03 must
**reuse or extend that seam** where applicable rather than creating a second lossless-read
abstraction. Its present contract does not by itself satisfy the R4-03 inventory contract:
it does not replace the required complete-pagination outcome algebra.

Implementation order remains fixed:

1. R4-03's shared strict outcome/pagination contract is implemented and parity vectors are
   green in both form factors, reusing the collection's existing read-outcome plumbing
   where that is the natural boundary.
2. R4-04 switches backup discovery, ManagedCluster inventories, and passive Restore
   discovery to the complete strict contract.
3. R4-04's named-object Backup/ACM-Restore/Velero-Restore evidence reads use the matching
   strict named-object semantics; API/discovery/auth/transport/decode failure is never
   absence.

No R4-04 plan may wire a safety decision to the current advisory-shaped Python list helper
or to a collection result whose completeness has not been positively established.

## 8. Evidence durability is a hard prerequisite

The July design already says an evidence transition is durable only after the form
factor's critical persistence primitive has acknowledged the required file/directory
synchronization. Current-base revalidation makes the dependency explicit:

- Python `StateManager` currently fsyncs the temporary file before `os.replace` but does
  not yet implement R4-05's parent-directory durability contract.
- Collection checkpoint persistence currently performs a parent-directory fsync after
  replace, but its best-effort `OSError` suppression does not satisfy R4-05's stricter
  capability/failure model for a safety-authorizing evidence write.

Therefore **R4-04 may not treat either current implementation as sufficient durability
proof as-is**. Before any R4-04 mutation depends on the journal, one of these must be true:

1. R4-05's applicable durability contract has already landed in that form factor; or
2. the R4-04 foundation tranche includes the narrow prerequisite persistence change needed
   to satisfy that same contract before the first R4-04 journal write.

The second option is not permission to invent a different durability model. It implements
the R4-05 contract narrowly and is sequenced/reconciled with R4-05 to avoid duplicate
future work. The collection still uses checkpoint persistence; Python still uses
StateManager through RunRecord. No second journal is allowed.

An unwritable/disabled collection checkpoint, failed Python critical write, failed required
parent-directory durability acknowledgement, or indeterminate durability outcome blocks
before the corresponding cluster mutation. Dry-run/check-mode remains non-mutating and
does not persist authoritative R4-04 transitions.

## 9. Resume, retry, reset, and rewind semantics

Once the backup freeze has been durably recorded, the migration journal represents one
immutable migration transaction. Normal retry and resume **reuse and revalidate it**; they
never resolve `latest` again, choose newer backups, mint a replacement `run_id`, or erase
partial evidence merely because the workflow phase is being retried.

### Python

- FAILED-state retry at ACTIVATION or later loads the existing journal through RunRecord,
  validates it strictly, and follows the July reconciliation/state-machine rules.
- `--reset-state` is the explicit fresh-run boundary. After a successful full reset the
  old journal is absent and a later activation may freeze a new set.
- Ordinary phase retry is not a journal reset.

### Collection

Current `checkpoint_phase reset_from` preserves `operational_data`; R4-04 makes the
migration-journal consequence explicit rather than relying on that incidental behavior:

- `reset_from: activation`, `post_activation`, or `finalization` with a valid existing
  migration journal **retains that journal and revalidates/reuses it**. It does not freeze
  a new backup set or discard cleanup/recovery history.
- A rewind to `preflight` or `primary_prep` while `migration_backups` exists is rejected
  for R4-04: those phases precede the freeze boundary and cannot safely coexist with a
  retained transaction from a later activation. The operator must use the repository's
  explicit full checkpoint reset path before starting a genuinely new migration
  transaction.
- Any reset path that leaves a malformed/partial journal cannot pass merely because the
  phase marker was pruned. Strict journal validation still applies.

The broader `reset_from` identity-bypass and unsafe-legacy convergence defects remain
owned by `R3-06`. R4-04 does not redesign checkpoint identity. Its invariant is narrower:
**no reset/retry path may silently discard, overwrite, or re-freeze an existing migration
transaction.**

## 10. Current implementation-plan boundary

After this written amendment is approved, `superpowers:writing-plans` should produce the
implementation plan from the July baseline plus this amendment. The plan must not start by
editing activation call sites. Its dependency order is:

1. **Foundation:** strict journal facade/validator in each form factor; parity schema
   vectors; R4-03 strict-read prerequisite/reuse; applicable R4-05 durability prerequisite;
   exact backup/Restore/Velero evidence types and deterministic fingerprint helper.
2. **Activation and completion evidence:** UID-bound concrete backup freeze; additive
   expectations/waiver; exact Restore create/guarded-patch inputs; ACM+Velero terminal
   association and completion bundle; retry/resume semantics.
3. **Finalization and cleanup:** live teardown revalidation; journal-reserved Restore
   cleanup; guarded UID+resourceVersion DELETE/recovery/repair; BackupSchedule enablement
   and integrated-decommission evidence gates.

These are review boundaries, not automatic permission to create three PRs. If the
implementation plan splits delivery across multiple PRs, `thermos-resolution-plan.md`
must receive one row/branch/worktree per PR and each PR must preserve a safe intermediate
state. No intermediate PR may enable a consumer before its foundation/prerequisite is
merged.

Current collection verification must use the repository's maintained endpoints and gate
inventory rather than July-era assumptions: the compatibility authority currently tests
`ansible-core` 2.16.* / Python 3.11 and `ansible-core` 2.21.* / Python 3.12, with unit,
integration, scenario, syntax, and collection-build surfaces as separately meaningful
checks. Parity-sensitive implementation also runs the root Python/parity gates required by
`AGENTS.md`.

## Amended acceptance criteria

The July acceptance criteria remain mandatory, with these additions and replacements:

13. Python owns the migration vocabulary only through `RunRecord`; collection owns it
    through `module_utils/checkpoint.py` plus `checkpoint_phase`. Production callers cannot
    bypass either facade with raw named state/checkpoint reads or writes.
14. An absent migration journal is distinct from a present invalid one. A present
    malformed, partial, unknown-version, or state-inconsistent journal fails closed before
    any R4-04 mutation, cleanup, BackupSchedule enablement, or integrated teardown.
15. Every consumed Backup persists and revalidates namespace, name, UID, `phase`,
    completion timestamp, errors, and warnings. Same-name UID replacement or status drift
    at a required evidence read blocks; neither form factor silently rebinds it.
16. `restore.completed_at` requires ACM `Restore.status.phase == Finished` and every
    method-required, status-published Velero Restore association to be owner-UID-bound,
    Backup-name-bound, UID-journaled, and `status.phase == Completed`. Full resources
    evidence validates every non-empty resources/generic-resources association and
    requires at least one. Unrelated, missing, replaced, or mismatched Velero Restores are
    blocking.
17. `cleanupBeforeRestore` is immutable R4-04 evidence. It is in the journal, canonical
    fingerprint projection, cleanup-intent copy, resume comparisons, teardown
    revalidation, final pre-delete validation, and Python/collection parity fixtures.
18. R4-04 uses the existing expectation resolvers with additive name+floor semantics and
    uses/extends the current collection lossless-read seam under the R4-03 strict-inventory
    authority; it creates neither a second expectation resolver nor a competing read
    abstraction.
19. No R4-04 safety-authorizing journal transition is considered durable until the
    applicable R4-05 file/directory durability contract is satisfied. Current Python and
    collection persistence behavior is not grandfathered as sufficient merely because it
    already uses atomic replace.
20. Resume/retry never re-resolves frozen backups. Collection `reset_from` at or after
    activation retains and revalidates the journal; rewind before the freeze boundary with
    an existing journal refuses and requires a full reset. Python phase retry follows the
    same no-refreeze invariant; only the explicit full reset creates a new transaction.
21. Tests pin the status/owner-reference association against immutable
    cluster-backup-operator source snapshots for the supported ACM-era boundary and use
    negative fixtures for missing status names, unrelated owner UIDs, replacement Velero
    Restore UIDs, wrong `spec.backupName`, fan-out resources associations, malformed
    status, and non-terminal phases.

## Written-spec review gate

This amendment deliberately stops before implementation planning. Review must verify at
least these points against current source before approval:

- the RunRecord/checkpoint facade ownership matches current guardrails;
- the strict-read dependency does not duplicate R3-02/R4-03 plumbing;
- the Backup UID/status projection is implementable from the existing Velero API surface;
- the ACM-to-Velero status-name/owner-UID/backup-name association matches supported
  cluster-backup-operator behavior;
- `cleanupBeforeRestore` is included consistently in every fingerprint/cleanup gate;
- reset/retry semantics cannot silently mint a new migration transaction;
- durability prerequisites are explicit and do not weaken R4-05;
- no protected-file, RBAC, release-validation, or lab-controller authority is implied.

Only after that exact written review is accepted does the implementation-plan gate open.
