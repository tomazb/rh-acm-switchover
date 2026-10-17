# Coexistence with the Python Tool

## Shared Behavior Contract

Parity is tracked by shared scenarios and the parity matrix, not by internal implementation shape.

Intentional divergence from a `dual-supported` capability requires explicit operator approval before implementation and must be documented in the parity matrix plus the relevant mapping/support docs in the same change.

## Dual-Bug-Fix Policy

Safety and correctness defects in dual-supported features must be evaluated for both implementations.

## Shared Code Policy

- share behavior specs, schemas, fixtures, and sample artifacts where useful
- do not share live runtime orchestration code by default
- prefer disciplined duplication over accidental coupling when execution models differ

## Discovery Bridge

`scripts/discover-hub.sh` remains the supported discovery bridge during coexistence.
Its output must be documented in terms of:

- `acm_switchover_hubs.primary.context`
- `acm_switchover_hubs.secondary.context`
- optional kubeconfig path inputs

## Checkpoint State Translation

The Python tool and the collection use separate checkpoint file formats.  They are
**not interchangeable** at runtime.

| Scenario | Guidance |
| --- | --- |
| Start with Python, finish with collection | Not supported. Begin a fresh collection run. |
| Start with collection, inspect with Python | Read the JSON checkpoint file directly; no Python helper supports it. |
| Migrate checkpoint between runs | Use the collection checkpoint JSON as-is. |

When a collection checkpoint exists at `acm_switchover_execution.checkpoint.path`, the
`checkpoint_phase` action plugin skips any phase listed in `completed_phases` on resume.
Checkpoints written under pre-2.19 ansible-core (classic Jinja) may carry
stringified scalars (for example `"2"` or `"True"` where 2.19+ writes native
types); the facts layer coerces digit strings and Ansible's boolean vocabulary
back to native types on read, so those checkpoints resume identically.
A fresh run (or `checkpoint.reset: true`) starts from the beginning regardless of any
pre-existing checkpoint file.

Corrupt state is handled alike on both sides (R4-04 PR B). A Python state file or
collection checkpoint that is not valid JSON, or whose top level is not a JSON
object, is copied for inspection (`<state file>.corrupt.<UTC timestamp>` /
`<checkpoint path>.corrupt-<UTC timestamp>`), left in place, and the run fails.
Every later run stays blocked by the original until the operator repairs or
removes it or resets (`--reset-state` / `checkpoint.reset: true`); a failed copy
also fails without touching the original. Earlier collection releases moved the
corrupt checkpoint aside instead, so the next run silently started fresh.
Non-mutating collection runs report the corruption without making the copy. An
unreadable file blocks on both sides without a copy. Python also treats a state
file with an unknown `current_phase` as corrupt; the collection checks no
checkpoint field beyond the top-level object.

A recorded R4-04 migration journal is dropped only by the explicit full reset on
both sides (`--reset-state` / `checkpoint.reset: true`). Python refuses the implicit
resets that would otherwise discard it — a context mismatch with the stored
contexts, missing contexts on an in-progress state, and the `--force` restart of a
stale completed state or of a failed state with no resumable phase — and points the
operator at `--reset-state`. The collection refuses a pre-freeze `reset_from` that
would move the checkpoint back before the freeze and a `status: reset` of a
pre-freeze phase (an invalid journal refuses every `reset_from` and every
`status: reset`), and a `status: fail` of a pre-freeze phase while the journal key is
present; a fail of `activation` or later is always recorded. For a journal-bearing
schema `1.0` checkpoint it refuses the schema 1.0 rebuild that would discard
operational data; journal-preserving `reset_from` rebuilds remain allowed. Its other
reset paths keep `operational_data`. Without a journal every one of these paths behaves as before.

Dry-run, validate, and native Ansible check-mode collection runs do not write
pass/fail/reset checkpoint transitions, so they cannot make a later live run
skip phases. Check mode remains non-mutating even when
`acm_switchover_execution.mode: execute` is set.

Live execute-mode runs bind resumable state to the current hub identities. The
Python CLI persists each hub context with the Kubernetes `kube-system` namespace
UID in its state file and rejects a resumed run if the same stored context now
targets a different cluster. Legacy in-progress Python state without hub
identity data must be reset or explicitly backfilled with `--force`.

The collection records the same live cluster UIDs in checkpoint operation
identity data. Preflight discovers these UIDs before checkpoint entry, and the
checkpoint action plugin rejects resume when the stored context and current
cluster UID do not match. Execute-mode collection preflight also refreshes
MultiClusterHub discovery even if discovery variables were pre-seeded, so stale
cached MCH status cannot authorize a later live mutation.

The standalone `argocd_resume.yml` playbook applies the same identity boundary
when it loads a checkpoint to recover the Argo CD pause `run_id`: it reads live
`kube-system` namespace UIDs for the hubs it will mutate and fails before
including `argocd_manage` if the checkpoint identity is missing, unreadable, or
does not match the live hubs. If both hubs are supplied, normal and explicitly
swapped primary/secondary mappings are accepted only when both contexts and UIDs
match the checkpoint. After both role invocations, it publishes
`acm_switchover_argocd_resume_result` with `restored_by_hub.primary`,
`restored_by_hub.secondary`, their once-derived `restored` total, and a Boolean
`changed` value.

## R4-04 migration evidence transaction

Activation, post-activation, and finalization share one migration-evidence transaction,
implemented independently on each side with no new parity divergence:

| Concern | Python CLI | Collection |
| --- | --- | --- |
| Pure decisions | `lib/migration_evidence.py`, `migration_child_evidence.py`, `migration_journal.py`, `migration_post_activation.py`, `migration_cleanup.py` | the same-named `plugins/module_utils/` mirrors behind `acm_migration_evidence` |
| Journal store | state file `config.migration_backups` through `RunRecord` | checkpoint `operational_data.migration_backups` through `checkpoint_phase` `status: update` |
| Guarded Restore mutations | `KubeClient.json_patch_custom_resource_guarded`, `delete_restore_guarded`, `create_restore_guarded` | `acm_restore_guarded_mutation` (`patch`, `delete`, `create`) |
| Expected-name waiver | `--skip-managed-cluster-expectations` and `--managed-cluster-expectation-waiver-*` | `skip_managed_cluster_expectations` and `managed_cluster_expectation_waiver` |
| Cleanup repair | `--repair-migration-cleanup` and `--cleanup-repair-*` | `migration_cleanup_repair` |

Shared behavior:

- **Freeze and no refreeze.** Activation journals the controller's predicted Backups before any
  Restore mutation. `passive_patch` writes ManagedClusters `latest` and the one-shot
  `passive_restore` writes ManagedClusters `latest` with credentials and resources `skip`;
  `full_restore` writes the concrete owned Backup names. Every accepted Backup and Velero child
  is concrete and journaled. A resume reuses the journal, refuses a different mutation kind or ACM
  minor, and never adopts a Restore the journal does not record.
- **Restore-only ManagedCluster expectation.** Restore-only records no expected name. Activation
  and post-activation enforce a floor of one non-local ManagedCluster when the minimum is omitted
  (the collection's `allow_zero_managed_clusters` lowers the post-activation floor to zero, like an
  explicit `0`), and exactly the explicit minimum otherwise. The collection's
  `acm_switchover_managed_clusters` klusterlet map never supplies names or a count.
- **Legacy `passive_patch` hive/cluster rule.** On ACM 2.12–2.16 the `CredentialsHive` and
  `CredentialsCluster` requests are predicted before the PATCH (frozen-credentials reuse or a
  proven "no candidate" only), and any owner child bound to a dedicated hive or cluster credential
  Backup blocks completion — including a historical child, which restricts installations whose
  sync Restore history includes the legacy three-Backup format.
- **Guards.** Restore PATCH and DELETE requests use UID/resourceVersion guards. CREATE sends one POST, never adopts or reconciles an existing object, and records identity only after validating the accepted response and a strict post-create read.
- **Completion.** The ACM Restore must be `Finished` (`passive_patch` also `Enabled` with the full
  child proof); required Velero children must be `Completed`. `restore.completed_at` is the final
  activation-journal evidence write; immediate-import annotations follow before activation
  succeeds. `post_activation.completed_at` is written after all required post-activation work
  succeeds.
- **Count and name additivity.** An explicit minimum is an additional floor; recorded names stay
  enforced. The waiver lifts only the name check and is journaled once; a different later
  request is refused.
- **Finalization order.** Journaled Restore cleanup first; then the old-hub observability
  teardown where it applies (behind the old-hub evidence gate); then other-name switchover
  Restores through the generic cleanup, which runs only once the journaled cleanup is terminal;
  then a fresh locator-absence gate before BackupSchedule enablement; then the old-hub
  disposition behind the old-hub evidence gate. BackupSchedule inventory reads are strict in
  execute mode. The gate sits inside each old-hub adapter (Python
  `_disable_observability_on_old_hub` and `_handle_old_hub`; collection
  `disable_old_hub_observability.yml` and `handle_old_hub.yml`), so a direct collection
  `tasks_from` entry is gated too: without the validated journal and Restore-cleanup result of
  the current finalization pass it refuses before any request ("Refusing the old-hub action: no
  validated migration journal and Restore-cleanup result from this finalization pass is
  available").
- **Backup continuity (#342).** Python accepts only a complete strict Backup inventory
  for the initial baseline, polling, and integrity fallback. An unreadable baseline
  blocks before recorded-name reuse. Poll failures may retry within the existing wait
  while retaining the baseline. The Collection independently requires a Backup created
  at or after its persisted enable timestamp, then clean `Completed` status. No parity
  status changes. Legacy stored Python names or completed checkpoints are not migrated.
  Real task-level comparison isolates the pre-existing Collection timeout-template
  failure tracked by [#367](https://github.com/tomazb/rh-acm-switchover/issues/367);
  it does not establish a full unchanged-role pass.
- **Cleanup recovery and repair.** The same states, transitions, reason codes
  (`absent_without_completion`, `replacement_uid`, `replacement_during_poll`), conflict retry
  bound (three retries), and 300-second elapsed poll bound. Each absence-poll read is bounded by
  the remaining deadline, floored to whole seconds and capped at the 30-second request timeout,
  and no read is sent with less than one second left (Python `poll_read_timeout`, collection
  `acm_migration_evidence` `cleanup_poll_read_timeout` feeding `acm_k8s_read_outcome`
  `request_timeout`); the deadline is re-checked immediately before a poll decision is
  persisted. The error that first records `recovery_required` names the run and operation IDs.
  A later retry without repair reports `cleanup_recovery_required`; recover the IDs from the
  original error or the retained journal's `run_id` and `cleanup.operation_id`. The repair
  confirmation names the operation ID; a repair request on a cleanup that is not
  `recovery_required` is refused (`repair_not_permitted`).
- **Argo CD rescue.** With a journal, no form factor rewinds to `primary_prep`; each durably
  records a re-pause marker outside the journal before resuming, and the retry re-pauses before
  further phase work (PR C decisions amendment section 2.2). Both form factors re-pause before
  refusing a resume past post-activation whose journal lacks the completion evidence.
- **Previews.** Preview modes write no migration-journal transition and send no Restore mutation.
  Python skips the finalization-entry marker refusal in dry-run, but finalization previews in both
  form factors can still refuse an existing journal whose required completion evidence is missing.

Independent stores: each form factor reads and writes only its own journal. There is no
dual-write, handoff, or cross-form-factor resume of a migration.

**Rewind tooling (pre-existing form-factor difference, not a divergence).** The collection can
re-run a phase with `checkpoint.reset_from` (`activation`, `post_activation` or `finalization`
keep and revalidate the journal; `preflight` and `primary_prep` are refused while one exists).
Python has no phase-rewind option: a refused resume at finalization whose journal lacks
`restore.completed_at` or `post_activation.completed_at` names the `current_phase` value the
operator sets in the state file to re-run that phase against the kept journal, where the
collection operator uses `reset_from: post_activation` (or `activation`). The refusal itself is
the same on both sides.

**Collection execute mode requires the checkpoint.** The collection journal exists only in the
checkpoint, so an executing `activation` refuses without `checkpoint.enabled: true`. The
`switchover` and `restore_only` playbooks refuse such a run at the identity barrier, before
`primary_prep` or the Argo CD pause mutates anything; a direct `activation` role entry refuses at
its entry. Python state is always on.

**Stale cached route and cleanup liveness (#317).** The collection's named Restore reads in the
cleanup transaction follow the #317 stale-route rule above: a 404 through a stale cached route is
`error`, never absence. In the cleanup that can only block an attempt (an unreadable read changes
no cleanup state); it can never record absence or completion.

## R4-06 klusterlet endpoint identity

Both form factors share one endpoint rule
(`docs/plans/2026-10-15-r4-06-current-base-amendment.md` §2), held equal by
`tests/fixtures/r4_06_endpoint_vectors.json` and `tests/fixtures/r4_06_kubeconfig_vectors.json`:
normalized URL equality; `wrong_hub` only for a different host that neither former
hostname-only comparator called equal, every other mismatch `unverified`; the expected hub
from each cluster's `<cluster>-import` bootstrap server; no name-only context fallback; a
client bound to the proven URL before any read; and any unverified or failed cluster
blocking every remediation in the run. The safety outcome is the same: an ambiguous or
unverifiable input never authorizes a spoke mutation.

Both form factors also build every spoke client the same way (amendment §6, R4-06 PR 3):
from one credential snapshot of the selected context, captured once and never re-read,
materialized in a private directory, with the same refusals, held equal by
`tests/fixtures/r4_06_credential_vectors.json`. The snapshot lives for one Python run or one
collection module invocation.

Python refreshes its complete strict inventory at selection and again before any
remediation worker dispatch (#362). The latter compares the whole selected non-local
batch by name, UID and normalized URL, refusing all writes on drift or an unproven
inventory while retaining the selected snapshots. The collection already reads and
proves each mapped target afresh within every remediation invocation, classifies it
again, and blocks mutations if any prepared peer is unproven. It does not retain UID
continuity across invocations. These checks preserve the shared safety outcome under
the existing input and snapshot lifetimes; `dual-supported` status is unchanged.
Neither form factor makes hub discovery and spoke mutation atomic. Python's final
batch read leaves a race during subsequent worker execution.

The input models differ by design (operator decision 1, 2026-10-15). These are recorded
differences, not parity gaps, and the capability stays `dual-supported`:

| Concern | Python CLI | Collection |
| --- | --- | --- |
| Which clusters are inspected | every non-local ManagedCluster | the clusters mapped in `acm_switchover_managed_clusters`; unmapped clusters are skipped |
| Where a spoke context comes from | discovered by URL from the validated `KUBECONFIG` merge | the mapped file and context, whose server must equal the ManagedCluster URL |
| Duplicate entry names | whole-chain rule: a differing duplicate anywhere fails the step | within-file rule: a differing duplicate fails that cluster only |
| `endpoint_unavailable` scope | every non-local cluster | mapped clusters |
| No matching context | `no_context`, non-fatal skip | not applicable (an unmapped cluster is skipped) |
| Unreadable embedded hub kubeconfig YAML | `unreachable`, non-fatal skip | `failed` (pre-existing, unchanged) |

## Auto-import reset obligation (issue #214, audit C3/C4)

**The cluster is the collection's register** for the auto-import reset
obligation, exactly as it is for the Argo CD pause register: activation's
`ImportAndSync` patch writes the ownership annotation
`acm-switchover.open-cluster-management.io/import-strategy-set-by: acm-switchover`
in the same API call as the mutation. Finalization discharges by observation —
it deletes `multicluster-engine/import-controller-config` when the marker is
present (or the legacy `auto_import_strategy_changed` signal fires) and the
strategy is still `ImportAndSync`. The delete itself runs only in `execute`
mode — `dry_run` and `validate` stay read-only. Preflight reports a
non-blocking warning (`preflight-auto-import-orphan`) when either hub carries
a marked `ImportAndSync` ConfigMap left by an interrupted run (in
`restore_only` mode only the secondary hub is probed, since the primary hub
may not be configured at all in that flow).

Equivalence with the Python CLI: Python's state file is always on, so its
`auto_import_strategy_set` obligation survives interruption by construction.
The collection reaches the same invariant — an obligation is discharged only
when the reset is proven — via the cluster marker, without requiring
checkpointing to be enabled.

### Normal two-hub distinct physical-hub identity

Before a normal two-hub switchover can enter a mutation-capable phase, Python
and the collection independently require different live `kube-system` Namespace
UIDs. Equal context names fail early; different context names that resolve to
the same physical cluster fail closed as well. The cross-role check is additive
to stored-versus-current per-role resume validation. Restore-only remains
secondary-only, and standalone decommission is excluded from this predicate.

Collection execute mode, including native Ansible check mode, reads the live
UIDs instead of trusting public facts, registered values, cache, or extra vars.
Only explicit non-live test fixtures may use
`acm_switchover_test_overrides.non_live_hub_identities` in `validate` or
`dry_run`. The Python CLI has no equivalent production override. These are
independent implementations of one operator decision, not shared runtime code.

**Intentional divergence on read failure:** the collection fails finalization
closed when `import-controller-config` cannot be read, because the cluster is
its only register and it has nothing else to consult; the Python CLI, by
contrast, warns and continues when its state file records no pending
`auto_import_strategy_set` obligation (`modules/finalization.py`
`_ensure_auto_import_default`), since an unreadable ConfigMap without a
recorded obligation cannot represent an undischarged reset.

**Intentional divergence on a built-in named-read 404 that live discovery does not confirm (#317):**
the two form factors have different proof obligations for a named 404. The
collection resolves every kind, built-in or custom, through kubernetes.core's
shared on-disk discovery cache, and the dynamic client routes the GET by the
cached plural and scope. After any named 404 the collection's `strict_read`
therefore publishes `not_found` only when the resolved group/version is the
requested one, the resolved plural is the canonical resource name, a live read
of the group/version's discovery declares that requested group/version and
lists the name with the requested kind and the scope the GET was routed by, and
a namespaced kind was read in a namespace. A live miss is `kind_not_served`; a
group/version, name, kind, or scope mismatch, a namespaced read without a
namespace, or unreadable discovery is `error`. Every collection
discovery read, including the kind-not-served proof for an unresolvable kind,
treats a document whose `groupVersion` is missing, empty, not a string, or not
the requested group/version as unreadable, as Python's custom-resource prover
also does since #321 (next paragraph). The Python CLI has no
cached route: custom-resource reads prove the resource name served before the
GET, while typed built-in reads (`get_namespace_strict`, `get_deployment_strict`,
`get_replicaset_strict`, and the `import-controller-config` ConfigMap read)
use fixed routes and take a 404 as absence without discovery. The difference is
a built-in named 404 that these collection checks do not confirm: `error` for a
group/version, name, kind, or scope mismatch, a namespaced read without a
namespace, or unreadable discovery, and `kind_not_served` for an omitted kind.
Python reports each as absence. For the canonical resource names, kinds, and
namespaces the collection's callers pass, only the unreadable case is reachable
on a conformant API server: built-in kinds are always served there, and their
plural, kind, and scope are fixed, so a stale cache that was written by that
same server cannot mismatch them; a cache file left by another server behind the
same API URL, or a corrupted one, can, and the collection then refuses the route
before any request (see the #320 paragraph below). A
group/version mismatch needs kubernetes.core's fallback for a core `v1` kind
whose core lookup missed, which resolves the kind in any group at `v1`; that
happens only when core discovery could not be read while the kind was
resolved. It is
fail-closed: the collection's
auto-import step fails instead of applying immediate-import annotations, the
destination-observability gate blocks instead of accepting an absent
namespace, observability teardown fails instead of recording namespace-absent
evidence, and Pod classification reports an unreadable namespace or
`deployment_read_failed` instead of `namespace_absent` or
`install_deployment_absent`. Operator-approved; the capabilities stay
`dual-supported`. The parity tests intentionally carry no equality vector for
this case.

**Custom-resource discovery with a malformed or non-matching `groupVersion` — aligned (#321):**
both form factors' discovery provers — the collection's shared prover in
`plugins/module_utils/k8s_read.py` and Python's `_discovery_serves` in
`lib/kube_client.py` (used by `get_custom_resource_strict` and
`list_custom_resources_strict`) — treat a readable `APIResourceList` whose
`groupVersion` is missing, empty, not a string, or not the requested
group/version as unverifiable, before any verdict. Such a document proves
nothing about the requested group/version, so it is `error` / `ERROR` in both,
on every path: the named-404 classifier, the kind-not-served proof, and the
live discovery proof every custom-resource read takes before its object request
(#322). It can establish neither a served kind nor object or kind absence. The
parity tests hold this equal with the `malformed_group_version_*` vectors. Only
a malformed or non-conformant discovery response reaches it, because a
conformant API server always declares the requested group/version. History:
the collection check was added in #317; Python did not read the field, and the
operator approved retaining the fail-closed collection-only behaviour (#317),
then its extension to custom-resource success paths (PR #324), each as
temporary until #321, which aligned Python and retired both approvals.

**Intentional divergence on a custom-resource named-read 404 from a stale cached route (#317, approved after implementation):**
this is distinct from the built-in divergence above. For a custom-resource named GET,
the collection builds the object route from the plural and scope that
kubernetes.core resolved for the requested kind, possibly from its shared
on-disk discovery cache. When that GET returns 404 and the resolved route does
not match the requested resource identity, the collection returns `error`: when
the resolved plural is not the canonical resource name (decided before any
discovery read), or when fresh live discovery for the requested group/version
(conformant and readable, declaring that group/version) lists that name with a
different Kubernetes kind or with a namespaced scope that differs from the
scope the GET was routed by. Python's custom-resource reads never use a
dynamic-client route: after a name-only discovery proof they GET the caller's
fixed group, version, plural, and namespace, so for the same cluster state
Python reads the object (`ITEMS`) or reports `OBJECT_ABSENT`; for a stale
plural whose canonical name live discovery also omits, Python reports
`CRD_ABSENT` without a GET. A stale route cannot prove anything about the
requested object, so `error` is the fail-closed outcome. The collection's
plural, kind, and scope checks are not weakened, and the route is not retried
or rerouted, to regain equality. The decommission callers that read custom
resources reject `error` instead of recording absence evidence. With a fresh
cache and conformant discovery the resolved route matches, so this case is
reachable only when the cached discovery no longer matches the live API
server, for example after a custom resource definition's plural, scope, or kind
changed within one group/version after the cache was written. The omitted-kind
case stays aligned when the resolved plural is canonical (`kind_not_served` in
the collection, `CRD_ABSENT` in Python). Chronology: the plural and scope
checks were implemented in `7d105b30` and the kind check in `3105809b`, before
any approval for custom resources; the earlier built-in approval (recorded in
`53179299`) and the since-retired `groupVersion` approval (#321) do not cover this case. An
independent validation then found the difference (IV-319-F4), and the operator
explicitly approved it afterwards as a third narrow divergence (the second still
open since #321 retired the `groupVersion` one). It is recorded
as an operator-granted exception to the approval-before-implementation rule
above. The approval covers only this stale cached-route mismatch on
custom-resource named reads. It does not cover LIST routing, which #320 owns,
and it does not authorize fallbacks. Python runtime is not changed by #317, and
any restoration of parity for this case needs its own governed follow-up. The
capabilities stay `dual-supported`, and the parity tests intentionally carry no
equality vector for this case.

**Custom-resource reads prove live discovery before the request (#322):** the
collection's `strict_read` now reads a custom resource (any group/version other
than `v1` and `apps/v1`, the group/versions Python reads through fixed typed
routes) only after a live discovery read of the requested group/version is
readable and lists the canonical resource name. Unreadable discovery is `error` and an omitted name is
`kind_not_served`, with no object request, for a named GET and a LIST alike.
This is Python's existing order (`get_custom_resource_strict` and
`list_custom_resources_strict` prove the name served before any request), so
the previous difference — the collection publishing a successful custom-resource
GET or LIST as `ok` without reading discovery — is removed and held equal by
parity vectors. Built-in reads are unchanged and read no discovery on success,
as in Python.

**Fail-closed outcome on a stale or foreign resolved route (#320):** before any
request, the collection's `strict_read` refuses, as `error`, a route that
kubernetes.core resolved to a group/version other than the requested one, to a
plural other than the canonical resource name, with a scope that is not a
boolean, or as cluster-scoped for a request that names a namespace. The dynamic
client uses the namespaced URL only for a route resolved as namespaced, so a
stale or foreign cached `namespaced: false` would otherwise read a namespaced
LIST cluster-wide and publish another namespace's objects (or an empty
cluster-wide answer) as the requested namespace's inventory. Scope needs no
discovery field: with a namespaced route, the server answers a namespaced
request at the namespaced URL, which returns 404 for a cluster-scoped kind.
Python has no resolved route: it reads the caller's fixed route and returns the
object or inventory. The collection difference is reachable only when its
resolved route does not match the live API server — a discovery cache written by
another server behind the same API URL, a corrupted cache, a custom resource
definition recreated with another scope, or kubernetes.core's core-`v1`
fallback into another group after core discovery failed during resolution
(#323) — and in each of those cases the collection previously published the
wrong route's result as `ok`. For a resolved route that matches the live server
every outcome is unchanged and equal on both form factors. No cache is
invalidated or rewritten and no route is retried or rebuilt. A post-read check
of each item's namespace is deliberately not added: it could fire only on a
non-conformant server. Every decommission, observability, primary-prep, and
activation caller already treats `error` as fail-closed. The capabilities stay
`dual-supported`; the parity tests carry no equality vector for this
collection-internal case, and the collection's unit and runtime tests pin it.

**Default posture (audit C4):** `checkpoint.enabled` remains `false` by
default. Without checkpointing the collection has no resume and no
hub-identity binding for resumed runs; enabling it is the operator's opt-in
for resumable executions. The safety obligations that must survive
interruption (Argo CD pause register, auto-import reset) live on the cluster
and do not depend on this setting.

**resume_summary:** both runtimes now use replace semantics — each resumed
process records the phase it started at (`resume_start_phase`), last resume
wins. Shared key names are pinned by `tests/test_checkpoint_state_parity.py`.

## MultiClusterHub teardown

MultiClusterHub teardown is dual-supported, and the contract both runtimes hold is
(issue #290):

- the operator identity is captured once, from the MultiClusterHub-owning
  ClusterServiceVersion down to its install Deployment, and bound to the target
  before the delete;
- an identity that cannot be determined is recorded as an **explicit unavailable
  identity** rather than guessed — it excludes no Pod from the drain;
- Pods are classified by owner chain (Pod → sole controller ReplicaSet → sole
  controller Deployment, UID-matched against the recorded Deployment), never by Pod
  name;
- the drain and the completion proof fail closed: a drain still blocked at the end of
  the budget, a pass that cannot be read, and a recorded identity that no longer holds
  all stop the run;
- a resumed run reuses the recorded target and the recorded identity verbatim — it
  never rediscovers, rebinds, or upgrades either;
- final evidence comes only from the last live pass, and a `completed` record is
  re-proved live on the next run instead of being trusted;
- check mode and `dry_run` read only: they capture nothing durable, delete nothing,
  and report no mutation;
- `changed` means this run's delete was accepted; a preview publishes `would_change`
  instead.

As with every other durable state on this seam, the Python and collection formats that
carry this teardown are **independent and not interoperable** — parity is the behavior
above, not the stored shape.

## GitOps Integration Boundary

Generic GitOps marker detection in the collection is **read-only and warning-oriented**.
The `roles/preflight/tasks/validate_gitops.yml` task records an informational result
(`preflight-gitops-warning`) when `acm_switchover_features.skip_gitops_check` is not
set, but does not fail the preflight or block the switchover.

The same preflight task also performs read-only Argo CD Application discovery for
operator advisory output. It lists ACM-touching Applications by hub and prints the
same kind of pause/scope recommendation as the Python CLI when Argo CD management
is not enabled. Discovery failures remain non-blocking and are reported as
warnings.

Argo CD auto-sync pause/resume is the **only supported mutating GitOps integration**
in the collection. It is managed by the `argocd_manage` role:

- Pause is triggered in `primary_prep` when `acm_switchover_features.argocd.manage: true`
- Automatic resume during finalization has been removed (unsafe — operator must retarget Git first)
- A standalone resume entrypoint is available at `playbooks/argocd_resume.yml`; when
  it loads a checkpoint for the pause `run_id`, live hub identity validation
  completes before any Application resume patch runs

When pause or resume uses an explicit namespace or checkpoint-persisted
namespace scope, the collection requires exactly one positively successful
`k8s_info` result for every normalized requested namespace before it aggregates
Applications. Any failed, skipped, unreachable, malformed,
cardinality-mismatched, or mixed present/absent result invalidates the complete
discovery. No partial result reaches filtering or mutation. An all-absent
result is recognized only when every namespace positively reports the API
absent with no contradictory resources.

Python and collection resume share the same marker-ownership boundary: the
`paused-by` annotation must exactly equal the expected run ID before either
implementation restores sync policy or removes the marker. A missing marker is
an idempotent no-op. A different non-empty run ID is reported and left
untouched, including when auto-sync is already enabled; operators must inspect
and explicitly remove a marker they have confirmed is stale. Both form factors
also make a same-run patch conditional on the Application `resourceVersion`
observed with that marker. A backup or concurrent-run change therefore causes
an actionable conflict instead of overwriting newly changed ownership.

The `app.kubernetes.io/instance` label is treated as `UNRELIABLE` by the marker detector
and must not be used as a definitive GitOps signal. Use `argocd.argoproj.io/instance`
or `app.kubernetes.io/managed-by: argocd` instead.

## Pause register invariant

The Python implementation's `argocd_paused_apps` state list is a *pause
register* (ADR-0001, `docs/adr/0001-pause-register-invariant.md`): its entries
are the *unresolved resume obligations* — Applications this tool may have paused
and has not yet confirmed resumed. An entry is confirmed (`pause_applied=True`),
provisional (`pause_applied=False`), or unknown (`pause_state="unknown"`); the
last two mean the pause may have landed, so they are just as load-bearing as a
confirmed one. Resume removes an entry only when resume is proven complete (the
patch landed, or the Application is observably resumed); failed and unproven
entries stay for retry. Dry-run records nothing in the register, and the register
is never cleared just because the Applications CRD stops being visible — only a
truly empty register is cleaned up on CRD-visibility loss.

### Collection register decision (issue #207)

The collection's register **is** the cluster: the annotation pair
(`acm-switchover.argoproj.io/paused-by`,
`acm-switchover.argoproj.io/original-sync-policy`) written in the same patch
that pauses the Application. The collection deliberately does not duplicate the
Python state-file register or its confirmed/provisional/unknown states:

- Python needs three resolution states because its pause is two steps — persist
  the register entry, then patch. The collection's record rides inside the pause
  patch itself, so record and mutation are one atomic API call and there is no
  provisional window to describe. A failed or ambiguous patch is a failed task;
  the operator retries with the same run_id and the patch is idempotent.
- ADR-0001's load-bearing invariant — an obligation is discharged only when
  resume is proven complete; fail closed on ambiguity — is enforced directly:
  - resume fails when Application discovery reports Argo CD absent (or errors)
    while `acm_switchover_argocd.run_id` is set, so the rejected "clear register
    when CRD absent" shape is unreachable;
  - resume never patches `spec.syncPolicy` without a recoverable
    `original-sync-policy` annotation; a matching marker with a missing/empty
    policy fails the phase and routes Python-paused Applications to
    `acm_switchover.py --argocd-resume-only`;
  - an orphaned `original-sync-policy` annotation (marker absent) is reported
    and left untouched — ownership cannot be established.
- `acm_switchover_argocd.run_id` is the obligation signal: minted only after
  pause-mode discovery confirms Argo CD is installed, and persisted to
  checkpoints without the `acm_switchover_execution.run_id` fallback. A
  non-empty run_id means a pause may have landed, so resume must prove
  discharge or fail. (The `_argocd_expected_run_id` fallback chain used for
  marker *matching* keeps the execution fallback for compatibility with markers
  written by older versions.)

Accepted residual divergences:

- The run_id reaches the checkpoint only at phase end. A crash between the
  first pause patch and checkpoint persistence leaves pauses whose run_id is in
  no checkpoint — but it is durable on the cluster in every `paused-by`
  annotation, and the standalone resume playbook accepts an explicit run_id.
- Simultaneous loss of both annotations (external strip, backup restore) is
  unrecoverable by the collection alone — the same class of loss as deleting
  the Python state file.

Both form factors share the marker-ownership and resourceVersion-conditional
patch rules documented above.

### Strict LIST continuation validation (#345)

Python's `list_custom_resources_strict` and the collection's `strict_read` validate
`metadata.continue` before deciding whether an inventory is complete. Missing, null and
empty-string values end pagination; a nonempty string is an opaque continuation passed
unchanged to the next request. Every other type is a malformed response, including false,
zero, lists and mappings. The whole read fails closed without partial resources or a
resource version, and the token is not included in diagnostics. These rules also apply to
read-only previews and native Ansible check mode. Consumers must not complete a checkpoint
from a failed read. The legacy non-strict readers retain their existing behavior.
