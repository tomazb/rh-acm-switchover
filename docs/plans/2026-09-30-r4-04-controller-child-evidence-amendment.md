# R4-04 Migration Evidence — Controller Child-Evidence Amendment

**Date:** 2026-09-30
**Status:** operator-directed amendment. The operator decided on 2026-09-30, in the
coordinating session: amend the design before R4-04 PR B; mirror the upstream exact-first
generic order; keep the `latest` trigger for one-shot `passive_restore` and bind its
provenance through frozen predictions. It is subject to the governed documentation workflow
before any R4-04 implementation relies on it.
**Amends:** `docs/plans/2026-08-27-r4-04-current-base-design-amendment.md` (the "August
amendment") and, through it, `docs/plans/2026-07-29-migration-evidence-design.md` (the
"July design").

## Authority and scope

Where this document conflicts with the August amendment or the July design, **this document
wins**; where it is silent, the August amendment and then the July design remain normative.
It changes no runtime, test, RBAC, manifest, Helm, release-validation, lab-controller,
protected-file, or support behavior, and it does not authorize implementation by itself. The
R4-04 implementation plan remains the execution authority; its normative specification is
now the July design, the August amendment, and this document.

The R4-04 PR B design review compared the August amendment with the six pinned
cluster-backup-operator snapshots it cites (August §5) and found that:

1. the August generic-Backup correlation parses the source name's timestamp before trying the
   exact generic name, while every pinned controller tries the exact name first;
2. the one-shot `passive_restore` and `full_restore` child contracts omit Velero Restores the
   pinned controllers create, so part of their Backup provenance would be unbound;
3. on ACM 2.12–2.16 the July/August switch of one-shot `passive_restore` to a **concrete**
   ManagedClusters Backup name changes what the controller restores: with credentials and
   resources `skip`, the controller substitutes the ManagedClusters option for the
   `Credentials` and `ResourcesGeneric` types, so the Credentials request resolves to the
   ManagedClusters Backup itself. It receives the same generated child name as the
   ManagedClusters request, and because Credentials is created first, the ManagedClusters
   request meets `AlreadyExists` and `veleroManagedClustersRestoreName` is never published.
   The current `latest` trigger does not have this problem.

Some further points were ambiguous; §5 records the fail-closed readings both form factors
implement.

Source references use `U<lane>/<file>:<line>` for the pinned snapshots of August §5
(repository `stolostron/cluster-backup-operator`; 2.12 `74b54988…`, 2.13 `7a7b240b…`, 2.14
`8b489db4…`, 2.15 `25b28b76…`, 2.16 `9efe77ea…`, 2.17 `c8578f94…`). A range such as
`U2.14–2.16` means the same lines in each of those snapshots. These are static source
references, not controller executions or live certification.

## 1. Schedule tokens and direct `latest` prediction

The resource-type tokens are prefixes without a trailing hyphen, exactly as pinned
(`U2.12–2.13/restore.go:58-65`, `U2.14–2.17/restore.go:65-72`):
`acm-managed-clusters-schedule`, `acm-credentials-schedule`, `acm-resources-schedule`,
`acm-resources-generic-schedule`, and, on the legacy lanes only,
`acm-credentials-hive-schedule` and `acm-credentials-cluster-schedule`.

Direct `latest` selection is August §3 steps 1–5, unchanged, with one added fail-closed rule.
The pinned comparator orders only by `status.startTimestamp` and the controllers sort with the
non-stable `sort.Sort` (`U2.12–2.13/restore_controller.go:385-386`,
`U2.14–2.16/restore_controller.go:488-489`, `U2.17/restore_controller.go:611-612`), with no
secondary key, and the raw filter checks only the name prefix and the raw phase, so it does
not exclude a candidate without a start time. After the prefix and raw-phase filters, R4-04:

- blocks when any filtered candidate has a missing or malformed `status.startTimestamp`;
- otherwise blocks when more than one filtered candidate shares the maximum start time
  (equal start times strictly below the maximum do not make the selection ambiguous);
- otherwise selects the unique candidate with the maximum start time and applies the
  seven-field projection (August §3), blocking if it fails.

An empty filtered set is "no candidate": the controller fails the lookup, and whether that is
blocking depends on the type (§§3–4). This is an added R4-04 refusal rule, not a claim about
upstream nil-comparison behavior.

## 2. Correlated generic selection mirrors the controller order

This replaces steps 2–3 of August §3 "Correlated generic-resource fallback" and the matching
clause of August acceptance criterion 17. Given the concrete source Backup name `S`:

1. **Exact name first.** Take the suffix of `S` beginning at its last `-` (the `-` included).
   The exact candidate name is the schedule token of the requested type followed by that
   suffix; if `S` has no `-` there is no exact candidate. If an object with exactly that name
   exists in the strictly complete Backup inventory, it is the controller's candidate — no
   phase or start-time pre-filter — and R4-04 applies the seven-field projection to it,
   blocking if it fails. R4-04 never falls back past an existing exact candidate.
2. **Fallback only when there is no exact candidate.** Compute the target time as the pinned
   `getBackupTimestamp` does: the text after the last `-`, hyphens trimmed, parsed with layout
   `20060102150405` (`U2.12–2.13/utils.go:132-139`, `U2.14/utils.go:141-148`,
   `U2.15–2.17/utils.go:140-147`). A parse error or a zero time — which the helper returns
   without an error when `S` has no `-` — means **no candidate**
   (`U2.12–2.13/restore.go:553-560`, `U2.14–2.16/restore.go:560-567`,
   `U2.17/restore.go:647-654`).
3. The raw fallback set is every Backup in the strictly complete inventory whose name
   *contains* the type's token, whose `status.startTimestamp` is present, and whose start time
   is within ±30 seconds of the target, both bounds inclusive, with no phase filter
   (`U2.12–2.13/restore.go:537-587`, `U2.14–2.16/restore.go:544-594`,
   `U2.17/restore.go:630-674`). The controller takes the first match in inventory order.
4. R4-04 accepts exactly one raw fallback candidate, which must satisfy the seven-field
   projection. More than one raw candidate is blocking before mutation. Zero candidates means
   **no candidate**.

The result is three-way: a selected candidate, no candidate, or blocking. A strict-read
failure, malformed inventory, ambiguity, or a selected candidate failing the projection is
blocking; it never becomes "no candidate".

## 3. One-shot `passive_restore` keeps the `latest` trigger

This replaces the August amendment's `passive_restore` paragraph (August §4) and the
`passive_restore` bullet of August §5 "Full and one-shot passive requirements", and narrows
August acceptance criteria 18 and 22 as stated in §6.

### 3.1 Trigger and owned projection

The one-shot passive Restore keeps the current create spec:
`veleroManagedClustersBackupName: latest`, `veleroCredentialsBackupName: skip`,
`veleroResourcesBackupName: skip`. `restore.backup_fields` for `mutation_kind:
passive_restore` is exactly `{"veleroManagedClustersBackupName": "latest"}`; the two `skip`
fields are checked as skipped and never enter the map (July §1). `passive_patch` and
`passive_restore` are the only mutation kinds whose owned projection is `latest`; concrete
provenance lives in the frozen `backups.*` evidence and the validated child Restores. There is
no `passive_patch_precondition` for this kind.

This method-specific projection also supersedes the older concrete-name-only and no-`latest`
wording in the July design and the August amendment wherever it concerns one-shot passive
creation: July §1 (the `backup_fields` value rules and "`latest` never reaches a Restore
spec"), July §1a fingerprinting, cleanup-intent copying, resume, teardown revalidation, the
final pre-delete check, the July tests, July acceptance criteria 1 and 12, and August §4 and
§6. Both passive mutation kinds use canonical `latest` in their single owned ManagedClusters
field; `full_restore` uses concrete owned fields; every Backup and child provenance record
stays concrete, and cleanup copies the owned map structurally, never replacing `latest` with a
predicted Backup name. August acceptance criterion 20's legacy-versus-2.17 cohort distinction
concerns `passive_patch`; one-shot completion follows §4 of this document.

Before creating the Restore (after PRIMARY_PREP has paused the BackupSchedule, July §1),
R4-04 freezes the controller's predicted Backups, per lane, with §1. The same
detection-after-mutation limit as August §4 `passive_patch` applies: the controller resolves
`latest` after the create reaches the cluster, so a newer controller-eligible Backup created
between the final pre-create reads and alias resolution is detected from the child
`spec.backupName` evidence and blocks all completion and finalization evidence, but the
mismatched child Restore may already have run.

### 3.2 Legacy lanes (`legacy_2_12_2_16`)

A one-shot Restore is not a sync Restore, so the controller considers every non-validation
resource type (`U2.12–2.13/restore.go:609-621`, `U2.14–2.16/restore.go:616-628`) and skips
the types whose effective option remains `skip`. With `skip` credentials and resources and a
set ManagedClusters option, it substitutes that option — here `latest` — for `Credentials`
and `ResourcesGeneric` (`U2.12–2.13/restore.go:667-670`, `U2.14–2.16/restore.go:674-677`).
`CredentialsHive`, `CredentialsCluster`, and `Resources` keep `skip`. The resulting logical
requests, each resolved with direct `latest` selection:

| Request | Selected Backup | Published in ACM status | Frozen as | Child list |
| --- | --- | --- | --- | --- |
| ManagedClusters | latest `acm-managed-clusters-schedule` | `veleroManagedClustersRestoreName` | `backups.managed_clusters` | `managed_clusters` |
| Credentials | latest `acm-credentials-schedule` | `veleroCredentialsRestoreName` | `backups.activation_credentials` | `activation_credentials` |
| ResourcesGeneric | latest `acm-resources-generic-schedule` | `veleroGenericResourcesRestoreName` | `backups.activation_resources_generic` | `activation_resources_generic` |

(`U2.12–2.13/restore.go:490-535`, `U2.14–2.16/restore.go:497-542`: for these three types the
requested and searched types agree, so the selected Backup is returned directly.) The three
direct-selection prefixes are disjoint, so the selected Backup names differ. With the current
one-shot Restore name, their generated child names also differ, which removes the
concrete-name collision described above. Generated-name collisions, including those produced
by the 252-character truncation of the pinned naming helper (`U2.12–2.13/utils.go:118-125`,
`U2.14/utils.go:127-134`, `U2.15–2.17/utils.go:126-133`), remain blocking under §4.2.

- ManagedClusters and Credentials predictions that are "no candidate" are blocking before
  mutation (the controller fails the Restore for those types).
- A ResourcesGeneric "no candidate" freezes **no** `activation_resources_generic` category;
  the controller ignores a missing generic Backup (`U2.12–2.13/restore.go:700-719`,
  `U2.14–2.16/restore.go:707-726`). Its absence is the recorded no-candidate decision (§4.2).

### 3.3 2.17 (`active_2_17`)

The pinned 2.17 controller does not substitute the ManagedClusters option: `Credentials`,
`CredentialsActive`, `ResourcesGeneric`, `ResourcesGenericActive`, and `Resources` read the
credentials/resources options (`U2.17/restore.go` `processRetrieveRestoreDetails`, the
`case Credentials, CredentialsActive` and `case ResourcesGeneric, ResourcesGenericActive,
Resources` branches) and are skipped as `skip`. The one-shot passive Restore therefore
creates only the ManagedClusters request, resolved with direct `latest` selection and frozen
as `backups.managed_clusters`; "no candidate" is blocking. No auxiliary category is frozen.

### 3.4 Completion

`restore.completed_at` requires ACM `Finished` (August §5 "Method-specific ACM phase rule"),
every required role of §4.1 satisfied, and the owner-membership rule of §4.2.

## 4. One-shot child roles, membership, and immutable absence

### 4.1 Required roles

A **role** is a logical request that must be satisfied by exactly one owner child. Backup
equality alone does not establish a role; each role names how its child is located:

| Kind / lane | Role | Located by | Binds to | Child list |
| --- | --- | --- | --- | --- |
| `passive_restore` legacy | ManagedClusters | `veleroManagedClustersRestoreName` | `managed_clusters` | `managed_clusters` |
| `passive_restore` legacy | Credentials | `veleroCredentialsRestoreName` | `activation_credentials` | `activation_credentials` |
| `passive_restore` legacy | ResourcesGeneric (only when frozen) | `veleroGenericResourcesRestoreName` | `activation_resources_generic` | `activation_resources_generic` |
| `passive_restore` 2.17 | ManagedClusters | `veleroManagedClustersRestoreName` | `managed_clusters` | `managed_clusters` |
| `full_restore` all lanes | ManagedClusters, Credentials, Resources, ResourcesGeneric | the four status fields | `managed_clusters`, `credentials`, `resources`, `resources_generic` | same-named lists |
| `full_restore` 2.17 | CredentialsActive | the one owner child bound to `backups.credentials` other than the status-published Credentials child | `credentials` | `credentials` |
| `full_restore` 2.17 | ResourcesGenericActive | the one owner child bound to `backups.resources_generic` other than the status-published generic child | `resources_generic` | `resources_generic` |

For 2.17 `full_restore`, the controller adds `CredentialsActive` and `ResourcesGenericActive`
because ManagedClusters is not `skip` (`U2.17/restore.go:692-723`), renames them with an
`-active` suffix because the credentials and resources options are concrete
(`U2.17/restore_controller.go:803-823`), and publishes neither in ACM status
(`U2.17/restore_controller.go:759-767`). It resolves both through the correlated algorithm of
§2 (`U2.17/restore.go:630-674` covers `ResourcesGeneric`, `ResourcesGenericActive`, and
`CredentialsActive`): with the credentials token from the frozen credentials Backup `C`, and
with the generic token from the frozen resources Backup `R`. Before mutation R4-04 predicts
both and requires them to equal `backups.credentials.name` and
`backups.resources_generic.name`, blocking otherwise (for a schedule-named `C` the exact
candidate is `C` itself).

For legacy `full_restore`, the controller also constructs `CredentialsHive` and
`CredentialsCluster` requests correlated from `C` (the concrete option skips the `latest`
shortcut, `U2.12–2.13/restore.go:503-539`, `U2.14–2.16/restore.go:510-546`), ignores a
missing Backup for them (`U2.12–2.13/restore.go:700-719`, `U2.14–2.16/restore.go:707-726`),
and publishes neither in status. R4-04 adds no hive/cluster evidence category: before
mutation it predicts both with §2 and their tokens, and **blocks if either prediction selects
a candidate or is blocking** (the legacy three-credential backup format is outside the R4-04
evidence model). This removes no supported configuration: the Backup schedules of the
supported controllers define only the credentials, resources, generic-resources,
managed-clusters, and validation types (`U2.12/backup.go:138-144`, `U2.16/backup.go:138-144`),
so a hive or cluster credential Backup correlated — by the exact name suffix or within ±30
seconds — to a credentials Backup produced by a supported schedule can only come from an
unsupported producer.

For `full_restore` on every lane, a ResourcesGeneric "no candidate" is blocking before
mutation, as August §5 requires a generic child for full restore — deliberately stricter than
the controller, which ignores a missing generic Backup.

Every role must be satisfied: a missing status name, a missing or extra owner child for an
unpublished role, or a role child that is not `Completed` or whose `spec.backupName` differs
from its bound Backup, blocks. Required roles are checked independently of §4.2, so an empty
owner inventory never satisfies them.

### 4.2 Owner membership and immutable absence

A one-shot Restore is created by this transaction, so every child it owns belongs to the
transaction. Separately from the roles, a strictly complete owner-filtered child LIST (August
§5 "Strict owner validation") must find **every** owner child `Completed` with a
`spec.backupName` equal to a Backup frozen for that mutation kind and lane. An owner child
bound to any other Backup, a non-terminal or unrecognized phase, or an incomplete or
malformed LIST is blocking.

A "no candidate" decision is made for the complete inventory observed at the freeze boundary
and is immutable for the transaction. If the controller later creates a child whose Backup
was not frozen, completion blocks; the journal never gains that Backup on resume. For legacy
`passive_restore`, an absent `backups.activation_resources_generic` is that persisted decision:
its child list stays empty, `veleroGenericResourcesRestoreName` must stay empty, and a later
generic owner child blocks completion rather than adding the category.

Generated child names are not an identity. Accepted child entries are keyed by name within a
list and carry the five fields of August §5; a generated-name collision between two roles, or
a name that resolves to an object bound to a different role's Backup, is blocking.

The generated names are predictable before mutation: the pinned helper derives each child name
only from the ACM Restore name and the child's Backup name, truncated to 252 characters
(`U2.12–2.13/utils.go:118-125`, `U2.14/utils.go:127-134`, `U2.15–2.17/utils.go:126-133`), plus
the 2.17 `-active` suffix of §4.1. Before creating or patching the ACM Restore, R4-04 computes
the generated names of every required role from the Restore name and the frozen Backups and
**blocks with zero ACM Restore mutation** when two roles would share a name. The post-mutation
check above remains the backstop for children the controller creates from Backups that were
not frozen.

## 5. Recorded resolutions of existing ambiguities

1. **Accepted guarded patch.** For `passive_patch`, `restore.uid` and the precondition are
   persisted before the PATCH, and `restore.generation` is written only from an accepted PATCH
   response, or from an immediate re-read whose UID matches after an accepted PATCH whose
   response omitted it (July §1 step 3). A timeout followed by a matching live object is not an
   accepted PATCH. A live normalized ManagedClusters `latest` with a null `restore.generation`
   is stale/unowned and blocking (August §4).
2. **Historical children of the legacy sync Restore.** The legacy `passive_patch` completion
   cohort (August §5) requires every owner child `Completed`, but only children consumed by
   this transaction are journaled in `restore.velero_restores`; unconsumed historical owner
   children are checked live and never persisted.
3. **Journal shape.** `migration_backups.schema_version` stays `2`: the July design fixes it,
   neither amendment assigns another number, and no journal has been persisted. A version-2
   record without the amended fields is invalid, never an older subset.
   `restore.velero_restores` always carries all seven list keys, empty when unused; each list
   is sorted by child `name`, and a duplicate name collapses only when all five fields agree.
   `cleanup` gains `mutation_kind` and `cleanup_before_restore`, both `null` in `not_started`
   and copied structurally from `restore` at intent, like the July identity fields.
   `restore.acm_minor` is one of the six pinned minors for every mutation kind.
4. **Method-scoped categories.** The category rules of August "Journal categories" are
   unchanged except: `passive_restore` requires `managed_clusters`; on the legacy lanes it also
   requires `activation_credentials` and permits `activation_resources_generic` (§3.2); it
   forbids every other category. A category allowed only on the legacy lanes is invalid for a
   2.17 record.

## 6. Acceptance-criteria changes

- **Criterion 17** reads: correlated generic selection tries the exact generic name before
  computing the source name's timestamp; the fallback uses the parsed 14-digit name timestamp
  (a parse error or zero time is no candidate), the raw `strings.Contains` token test, a
  present start time, and inclusive ±30 seconds, and requires exactly one raw candidate before
  applying R4-04 eligibility. Full restore journals the result as `backups.resources_generic`.
- **Criterion 18** reads: `passive_patch` and one-shot `passive_restore` are the only permitted
  R4-04 `latest` triggers. Fresh `passive_patch` intent is unchanged; `passive_restore` keeps
  the current `latest`/`skip`/`skip` create spec and freezes the controller-predicted Backups of
  §3 before the create.
- **Criterion 22** reads: `full_restore` uses concrete journaled Backup names in every ACM
  Restore field; `passive_restore` uses the §3 trigger; both require ACM `Finished`, and every
  required role and owner child of §4 must be `Completed` and bound.

New criteria:

31. An existing exact generic candidate is never bypassed: it is selected, and blocks if it
    fails the seven-field projection.
32. Legacy `passive_restore` freezes `managed_clusters` and `activation_credentials` from
    direct `latest` prediction and `activation_resources_generic` when one is predicted; 2.17
    `passive_restore` freezes only `managed_clusters`.
33. Legacy `full_restore` blocks before mutation when a hive or cluster credential Backup is
    correlated from the frozen credentials Backup.
34. 2.17 `full_restore` requires the unpublished `CredentialsActive` and
    `ResourcesGenericActive` children, bound to `backups.credentials` and
    `backups.resources_generic`, `Completed`.
35. One-shot completion requires every required role satisfied and, separately, every owner
    child `Completed` and bound to a frozen Backup of that kind and lane.
36. Direct `latest` prediction blocks on a missing or malformed filtered start time or on
    more than one candidate at the maximum start time.
37. A "no candidate" decision is immutable; a later owner child bound to an unfrozen Backup
    blocks completion and never adds a category on resume.
38. Tests attribute every case to each applicable pinned lane with source references and keep
    upstream-derived outcomes distinct from R4-04's stricter decisions. They include: an exact
    generic candidate with an unparseable source suffix (selected); an exact candidate failing
    the projection (blocks, no fallback); a source name without a hyphen (no candidate); correlated
    fallback with multiple raw matches (blocks); legacy `passive_restore` with zero generic
    candidates, one candidate, two candidates with a unique maximum start time (selects the
    newest), two candidates tied at the maximum (blocks), and the credentials prediction
    missing (blocks); a truncation-induced generated-name collision predicted before mutation
    (blocks with zero ACM Restore mutation); a legacy full restore with a hive candidate (blocks); a 2.17 full
    restore with a missing, failed, or duplicated `-active` child (blocks); a one-shot owner
    child bound to an unfrozen Backup (blocks); and direct `latest` with a maximum-time tie
    (blocks), an older tie (selects), and a missing start time (blocks).
