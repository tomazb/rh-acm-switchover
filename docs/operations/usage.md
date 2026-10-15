# ACM Switchover - Detailed Usage Examples

## Complete Switchover Workflow

### Step 1: Pre-Validation

Before attempting any switchover, always run validation first:

```bash
python acm_switchover.py \
  --validate-only \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive
```

Argo CD detection runs automatically during preflight when the Applications CRD is found on either hub. Use `--skip-gitops-check` to disable all GitOps detection including Argo CD deep dive.

Note: `--secondary-context` is required for switchover operations unless you are using `--decommission` or `--setup`.
For `--setup`, `--include-decommission` is only valid with `--role operator` or `--role both`, and should be used only when the generated operator kubeconfig must support teardown workflows.

**What this checks:**
- Primary and secondary context names differ, and their fresh live
  `kube-system` Namespace UIDs prove they are different physical clusters
- ✓ Required namespaces exist on both hubs
- ✓ ACM versions match between hubs
- ✓ OADP operator installed on both hubs
- ✓ DataProtectionApplication configured correctly
- ✓ Latest backup completed successfully
- ✓ **CRITICAL**: All ClusterDeployments have `preserveOnDelete=true`
- ✓ Passive sync restore is current (Method 1 only)
- ✓ ACM Observability detected (if present)
- ✓ GitOps marker detection warnings for ArgoCD/Flux (unless `--skip-gitops-check`)
- ✓ Argo CD discovery and ACM-impact summary on both hubs (automatic when CRD detected)

Note: GitOps marker detection is heuristic. The generic label `app.kubernetes.io/instance` is flagged as `UNRELIABLE` when present and should not be treated as a definitive GitOps signal.

**Expected output:**
```
Pre-flight Validation Summary: 15/15 checks passed
All critical validations passed!
```

If any critical validations fail, **DO NOT PROCEED** until issues are resolved.

For a normal two-hub run, matching context names fail immediately. Different
context names or kubeconfig files can still address the same physical
Kubernetes cluster. The CLI also fails closed when the fresh live UID values
match or either UID cannot be read. This is not a checkpoint reset control.
Resume continues to validate each stored role identity against its current UID,
and the cross-role guard adds the same-run distinctness check before mutation.
Restore-only remains secondary-only and standalone decommission does not use
this two-hub predicate.

### Step 2: Dry-Run

Preview exactly what the script will do:

```bash
python acm_switchover.py \
  --dry-run \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --old-hub-action secondary \
  --method passive
```

**What you'll see:**
- All planned actions (patch, create, delete operations)
- No actual changes will be made
- The state file is restored to its pre-run content, so dry-run cannot advance resume state
- Gives confidence in what will happen

**Sample dry-run output:**
```
[DRY-RUN] Would patch backupschedules/schedule-rhacm with: {'spec': {'paused': True}}
[DRY-RUN] Would patch managedclusters/cluster-1 with annotations
[DRY-RUN] Would scale statefulset observability-thanos-compact to 0 replicas
```

### Step 3: Execute Switchover

When ready, execute the actual switchover:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --verbose
```

**The `--old-hub-action` parameter is required** and controls what happens to the old primary hub:
- `secondary`: Set up passive sync restore for failback capability (recommended)
- `decommission`: Remove ACM components from old hub automatically
- `none`: Leave unchanged for manual handling

**Destination-observability gate.** Before observability is deleted on the old hub, the tool
re-reads both hubs live and refuses the deletion when the destination hub has no observability
(metrics continuity would end) or when the destination cannot be read at all. The two refusals are
never conflated. `--acknowledge-observability-not-migrated` converts only the first one — a
destination positively proven to have no observability — and is valid only with
`--old-hub-action decommission`; it is rejected with `--decommission`, `--validate-only`,
`--restore-only`, `--setup`, `--argocd-resume-only`, and when the gate would pass anyway. It never overrides an unverifiable or
partially present destination. The gate result is never persisted: every run and every resume
re-proves it live. A dry run evaluates the gate on the `--old-hub-action secondary` observability
teardown; the `--old-hub-action decommission` preview is skipped, so it does not evaluate the gate.
`--old-hub-action secondary`'s observability teardown is gated the same way as decommission's, with no
`--acknowledge-observability-not-migrated` override available on that path — the flag is valid only with
`--old-hub-action decommission`.

**Timeline (typical execution):**
- Pre-flight validation: 2-3 minutes
- Primary preparation: 1-2 minutes
- Activation: 5-15 minutes (waiting for restore)
- Post-activation: 10-15 minutes (cluster connections)
- Finalization: depends on BackupSchedule cadence (typical 5-10 minutes; longer for hourly/daily schedules)
- **Total: ~30-45 minutes**

Kubernetes API requests made by the Python workflow are bounded with explicit
per-call timeouts. The default API request timeout is 30 seconds; transient
5xx/429 and network timeout errors still use the existing retry policy.
Polling waits cap each sleep to the remaining timeout budget, so a final poll
interval cannot overshoot the configured phase deadline.

Release 1.7.10 also adds collection-side klusterlet request and worker future
timeouts, plus release parity guardrails for switchover, restore-only,
decommission, RBAC/bootstrap, checkpoint, and report artifacts.

> **Safety warning:** Do **NOT** re-enable Thanos Compactor or Observatorium API on the old hub after switchover.
> Both hubs share the same object storage backend; re-enabling on the old hub can cause data corruption and split-brain.
> Only re-enable on the old hub if you are switching back and have shut down these components on the current primary first.

**Argo CD / GitOps:** If you use Argo CD to manage ACM resources, enable `--argocd-manage` so the tool pauses auto-sync on ACM-touching Applications during primary prep (on both hubs). Applications are left paused after switchover; resume only after updating Git/desired state for the new hub, using `--argocd-resume-only` as a standalone step. Managed pause fails closed for ApplicationSet-managed child Applications, auto-sync Applications whose current `status.resources` cannot prove ACM impact, and Applications that still have auto-sync after a post-patch re-read.

Resume treats already-resumed apps (pause marker missing) as idempotent no-op, and fails if an app is still paused by a different run or cannot be restored for other actionable reasons.

For the Ansible collection, `playbooks/argocd_resume.yml` can reload the pause
`run_id` from the configured checkpoint. When it does, the playbook validates
the checkpoint's stored hub contexts and live cluster UIDs against freshly read
`kube-system` namespace UIDs before running any resume patch. If both hubs are
provided, normal and explicitly swapped primary/secondary contexts are accepted
only when the stored context and UID pairs match. Its final
`acm_switchover_argocd_resume_result` reports `restored_by_hub` for primary and
secondary plus a once-derived `restored` total; only patch results whose
`changed` value is exactly Boolean `true` are counted.

Collection resume is fail-closed on unresolved obligations (ADR-0001): it
fails when a pause `run_id` is recorded but the Application CRD is not visible
on the hub, and it refuses to patch any Application whose matching pause
marker lacks a recoverable `original-sync-policy` annotation — that is the
signature of a Python-paused Application, which must be resumed with
`acm_switchover.py --argocd-resume-only`.

**State file tracking:**
The script creates `.state/switchover-<primary>__<secondary>.json` tracking progress:
```json
{
  "version": "1.0",
  "current_phase": "post_activation_verification",
  "completed_steps": [
    {"name": "pause_backup_schedule", "timestamp": "2025-11-18T10:15:00Z"},
    {"name": "disable_auto_import", "timestamp": "2025-11-18T10:16:30Z"},
    ...
  ]
}
```

### Step 4: Resume from Interruption

If the script is interrupted (Ctrl+C, network issue, etc.), simply re-run the same command:

```bash
# Same command as Step 3 - it will resume automatically
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --verbose
```

The script will:
- Load state from `.state/switchover-<primary>__<secondary>.json`
- Skip already-completed steps
- Continue from the last successful step

**Example resume output:**
```
Step already completed: pause_backup_schedule
Step already completed: disable_auto_import
Step already completed: scale_down_thanos
Continuing with: verify_passive_sync
```

#### Hub identity binding on resume

State files record each hub's **live cluster UID** (the UID of the
`kube-system` namespace) in addition to the context name. On every resume the
tool re-reads the live UIDs and compares them to the stored values. Resume
fails closed before any mutation if:

- A stored UID no longer matches the cluster behind the same context name
  (kubeconfig was edited, context was repointed at a different cluster, or
  cluster was rebuilt).
- The state file is missing hub identity data for an in-progress switchover
  (legacy state predating UID binding).
- The live cluster cannot expose a UID (e.g. `kube-system` not readable).

Recovery options:

- **Different cluster on purpose**: re-run with `--reset-state` after manual
  verification to start fresh against the new cluster.
- **Legacy state file**: re-run with `--force` to bind the existing state to
  the current hubs **only after** manually confirming the clusters match the
  intent of the original run.

This protects against silently resuming a partially completed switchover on
the wrong cluster when contexts are reused.

#### Migration journal on resume

Activation records a **migration journal** in the state file (`config.migration_backups`)
before it mutates any Restore. From then on the journal is one fixed migration: every
retry and resume reuses it, re-reads the frozen Backups and the journaled Restore
strictly, and never freezes newer Backups (see [Migration evidence](#migration-evidence)).
A resume refuses when the live ACM minor version or the activation method no longer
matches the journal.

- `--force` never discards a journal. The implicit resets — a stale completed state, a
  failed state with no resumable phase, a context mismatch, or missing contexts on an
  in-progress state — refuse with a message pointing at `--reset-state`.
- `--reset-state` remains the explicit fresh-run boundary. It deletes the journal along
  with the rest of the state; a later run freezes a new migration.
- A resume at finalization whose journal lacks `restore.completed_at` or
  `post_activation.completed_at` refuses with
  `Migration evidence refused (restore_not_completed: …)` or
  `(post_activation_not_completed: …)`. The migration journal is unchanged, and a plain
  re-run refuses again. A recorded Argo CD re-pause marker is honored first: the re-pause
  runs and clears the marker before the refusal, so Argo CD stays paused while you recover.
  The message gives this recovery:

  > The migration journal is unchanged. For missing post-activation completion, set
  > `current_phase` to `post_activation_verification`. For missing Restore completion, set
  > it to `activation` and remove only the `completed_steps` entry named
  > `wait_restore_completion`, if present. Preserve the migration journal, mutation markers,
  > and Argo CD re-pause marker. Re-run with the same arguments; the tool must establish the
  > missing evidence from fresh reads. Do not synthesize completion timestamps or replace
  > frozen Backup identities. Missing accepted-mutation evidence requires operator
  > reconciliation. `--reset-state` deliberately discards the transaction.

  If the message says the journal is invalid, repair the journal in the state file, without
  synthesizing completion timestamps or replacing frozen Backup identities.

⚠️ **`--reset-state` also destroys durable MultiClusterObservability teardown
records.** `--reset-state` removes the whole state file, including the durable
per-resource teardown records (`delete_started` → `cr_absent` → `drain_pending`
→ `drained` → `completed`) that back the guarded MultiClusterObservability
delete. Resetting state between a failed drain and its rerun forfeits the
remembered drain obligation: a post-reset run that finds the resource absent
cannot distinguish "already deleted" from "never attempted" and takes the
clean-skip path. Retry — which resumes the retained record — is the normal
recovery path; reach for `--reset-state` only when deliberately abandoning
those obligations — the same discard the collection's own full
`checkpoint.reset` warning describes later in this document. `--force` does
not discard teardown records.

## Method Comparison

### Method 1: Continuous Passive Restore (Recommended)

**Use when:**
- You have passive sync already configured and running
- You want minimal downtime
- Secondary hub has been continuously syncing backup data

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary
```

**Activation options (Method 1):**
- **Default (Option A):** Patch the passive sync restore in place.
- **Option B:** Delete passive sync and create `restore-acm-activate`:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --activation-method restore \
  --old-hub-action secondary
```

> **Note:** The restore controller may briefly treat a deleted passive restore as still active.
> The tool deletes the passive sync Restore under its UID and resourceVersion, waits for the
> deletion to propagate, and only then creates `restore-acm-activate`. If that create is not
> accepted, nothing is rolled back or recreated automatically: the error says whether the
> passive sync Restore was already deleted, and a re-run re-proves the destination hub from
> the migration journal (a `restore-acm-activate` the failed attempt left behind is refused as
> unowned, otherwise the create is repeated). If you run this manually, wait for deletion to
> complete before creating the activation restore.

> **Passive Restore requirements (hard fail-closed):** Preflight and activation
> require a sync-enabled passive Restore — one whose
> `spec.syncRestoreWithNewBackups` is `true`. If no such Restore exists on the
> secondary, preflight fails and activation refuses to proceed; there is no
> name-based fallback (a Restore named `restore-acm-passive-sync` without the
> sync flag is **not** sufficient). Create or repair the sync-enabled Restore
> on the secondary before re-running.
>
> Before activation, the passive sync Restore counts as ready in phase `Enabled`,
> `Finished`, `Completed` or `Running`, or in `FinishedWithErrors` **only** when every
> reported error message matches the exact pattern `ManagedCluster <name> already available`.
> That allowance covers only this readiness check. The activation completion proof never
> accepts `FinishedWithErrors` or `Completed`: the activated ACM Restore must reach `Finished`
> (a patched passive sync Restore may also be `Enabled`, and only with the complete Velero
> child proof), and every required Velero child Restore must be `Completed`.
>
> **Note:** `--activation-method` applies only to `--method passive`.
> When `--min-managed-clusters` is omitted, the tool derives the expected non-local
> ManagedCluster names/count from primary preflight and enforces that set after
> activation. Preflight derives that set only from a positively complete, strictly read
> primary ManagedCluster inventory: an unreadable or unserved ManagedCluster API, an incomplete
> read, a ManagedCluster without a name, or a repeated name fails preflight
> (`ManagedCluster inventory`) instead of deriving zero expected clusters. In `--restore-only` mode, omitted `--min-managed-clusters` defaults
> to at least one restored non-local ManagedCluster because no primary inventory is
> available (the collection likewise never derives restore-only names or a count from its
> `acm_switchover_managed_clusters` klusterlet map). An explicit `--min-managed-clusters N` (`N` non-negative) is an
> **additional** count floor: it replaces the derived count, never the derived names,
> which stay enforced. Explicit `0` therefore permits an empty destination only when
> no expected name exists (for example in `--restore-only`). To lift the name check
> itself, use the audited waiver described under [Migration evidence](#migration-evidence).

**Post-activation klusterlet remediation:**
After activation, the tool verifies that managed clusters are joined and
available, then checks whether each reachable klusterlet still points at the old
hub. Wrong-hub klusterlets are remediated by re-applying the new hub bootstrap
kubeconfig and restarting the klusterlet deployment. The remediation is then
re-checked; a failed remediation or a klusterlet that still points at the wrong
hub fails post-activation instead of reporting success.

Clusters without a managed-cluster kubeconfig are explicitly skipped for direct
klusterlet probing/remediation. That skip is non-fatal only when the
ManagedCluster readiness checks already passed; if the same cluster remains not
joined or unavailable, the post-activation ManagedCluster gate still fails.
Python post-activation also treats a missing klusterlet hub secret, a hub
kubeconfig without a `server`, and unreadable embedded kubeconfig YAML as
non-fatal skips (the collection reports unreadable embedded YAML as `failed`).
A `server` that is present but empty, not a string, or not a valid URL is
`endpoint_malformed` (below), not a skip. Managed-cluster client construction
failures, non-404 secret API errors, transport failures, and unexpected probe
exceptions fail post-activation.

**Endpoint identity.** Before a managed cluster is inspected, both form factors
prove which cluster they are about to reach and which hub that cluster should
use:

- The cluster's API URL is the first non-empty
  `spec.managedClusterClientConfigs[].url` of its ManagedCluster, read fresh.
  Without a usable URL the cluster fails with `endpoint_unavailable` (or
  `endpoint_malformed`). No context is ever chosen from the cluster's name, and
  nothing is inspected or changed.
- The expected hub is the API server in the cluster's `<cluster>-import`
  bootstrap kubeconfig on the new hub, which is the value remediation writes.
  The Python CLI used to expect the server of `--secondary-context` instead.
  Two consequences: a cluster with no usable import secret now fails with
  `expected_endpoint_unavailable` (or `expected_endpoint_malformed`) instead of
  passing; and a klusterlet that points at a genuinely different host from the
  import secret is remediated, as the collection already did.
- Endpoints are compared as complete normalized URLs: scheme, host
  (case-insensitive, IPv6 in canonical form), port (a default port equals an
  omitted one) and path (a trailing `/` is not significant). A URL with
  userinfo, a query or fragment, an invalid port or host, or a bad percent
  escape is rejected, and rejected values are never echoed in logs or results.
- A klusterlet whose hub differs from the expected hub only in scheme, port or
  path, or that the previous hostname-only comparison treated as the same hub
  (for example two IPv6 literals in the same `2001:` block), is **unverified**,
  with reason `endpoint_mismatch_unverified`. It is never reported verified and
  never remediated automatically: it needs operator review, and there is no
  bypass flag. Only a different host is `wrong_hub` and remediated.
- Every spoke client must be bound to the proven URL before anything is read
  through it; otherwise the cluster is unverified with `client_endpoint_mismatch`.
- Any unverified, failed or timed-out cluster fails the step **before any
  remediation in the run**, so a genuine wrong-hub cluster in the same run is not
  remediated until every cluster is proven. The step is not recorded as
  complete, and a resume re-evaluates every cluster.

Python selects a spoke context from the validated `KUBECONFIG` merge: the
context whose cluster `server` equals the cluster's API URL. Several contexts on
one cluster entry (for example per-role users such as `managed1:operator` and
`managed1:validator`) select the first in `KUBECONFIG` order. Two different
cluster entries with the same server give `context_ambiguous`. A context with
the same host but a different scheme, port or path gives
`endpoint_mismatch_unverified`. No matching context at all remains the
documented non-fatal skip.

The collection never discovers contexts: `acm_switchover_managed_clusters` maps
each cluster to one kubeconfig file and, optionally, a context. Every mapped
file is validated like the Python merge. The selected context's server must equal
the ManagedCluster's API URL, or the cluster fails with
`target_identity_mismatch` (a different host) or `endpoint_mismatch_unverified`.
Other per-cluster failures are `context_missing`, `context_ambiguous` (no context
mapped and no `current-context`), `kubeconfig_duplicate_conflict`,
`kubeconfig_load_failed (<reason>)` and `kubeconfig_path_unsupported` (a mapped
path containing `:`). A mapping alone is never treated as proof of which cluster
a context reaches.

The collection's remediation reads the ManagedCluster URL and re-checks the hub
in the same invocation, and changes only a cluster it has just found
`wrong_hub`. A cluster that cluster verification reports as pending is no longer
remediated for being pending. If any cluster in the batch is unproven, no
cluster is changed: genuine wrong-hub peers report
`remediation_blocked_by_unproven_cluster`.

**Spoke client credentials.** Every client for a managed cluster is built from a
snapshot of the selected kubeconfig context, captured once per run (per module invocation
in the collection) and never re-read, so a credential file replaced or deleted during the
run cannot change who the tool authenticates as:

- every file-backed credential (`certificate-authority`, `client-certificate`,
  `client-key`, `tokenFile`) is read once, when the cluster is selected, and must be a
  regular, non-empty file within the kubeconfig size limit. A symbolic link, directory,
  empty, oversized or unreadable file is refused (`credential_symlink`,
  `credential_not_regular`, `credential_empty`, `credential_oversized`,
  `credential_unreadable`). Relative paths resolve against the file that defined the entry;
- an inline `token` wins over `tokenFile`. A token file is read as strict UTF-8 with
  trailing whitespace removed. An empty token, an empty `*-data` field, or `*-data` that is
  not valid base64 is refused;
- a user with an `exec` credential plugin or an `auth-provider` is refused
  (`exec_credential_unsupported`, `auth_provider_unsupported`): their credentials are
  produced when the client is built, outside anything that was checked. A context that
  names a user that does not exist is refused (`credential_unsupported`), never turned into
  an anonymous client;
- TLS verification must be provable: `insecure-skip-tls-verify` must be absent or the
  boolean `false` (the Kubernetes client treats the string `"false"` as true), and an `http`
  server, `tls-server-name` or `proxy-url` is refused (`tls_verification_unproven`).

A refused credential makes that cluster unverified before any client exists. The captured
certificates and key are written to `0600` files in a private `0700` temporary directory
for the duration of the step (the collection: of the module invocation) and the directory
is removed afterwards, including on failure, once every worker has finished. The token is
passed in memory. Captured credential content never appears in logs, results or the state
file.

Recovery:

- `endpoint_unavailable`: the ManagedCluster has not registered its API URL yet.
  Check `oc get managedcluster <name> -o jsonpath='{.spec.managedClusterClientConfigs[*].url}'`,
  wait for the klusterlet to report it, and re-run. In the collection you may
  instead remove the cluster's `acm_switchover_managed_clusters` entry: the
  cluster is then skipped, and the ManagedCluster readiness gate still applies.
- `endpoint_mismatch_unverified`: compare the `server` in the klusterlet's
  `hub-kubeconfig-secret` with the one in the `<cluster>-import` bootstrap
  kubeconfig. If they name the same hub, correct whichever is wrong outside this
  tool, then re-run.
- `expected_endpoint_unavailable`: the new hub has no usable import secret for
  the cluster yet. Wait for the import controller to create it, then re-run.
- A credential reason (`exec_credential_unsupported`, `tls_verification_unproven`, and
  so on): give the tool a kubeconfig context for that cluster with a static credential
  (a token, or a client certificate and key) and verifiable TLS, then re-run.

The local kubeconfig that Python uses to find managed-cluster contexts is loaded
fail-closed. Every file named in `KUBECONFIG` (or `~/.kube/config` when
`KUBECONFIG` is unset) must exist, be readable, be at most the size limit
(10 MiB; `ACM_KUBECONFIG_MAX_SIZE` can raise or lower it but not disable it),
parse as YAML without duplicate keys, `<<` merge keys, sets or recursive
aliases, and have a valid kubeconfig structure (an absent or `null` section and
an empty `user`/`cluster` in a context are accepted as kubectl writes them).
Otherwise the klusterlet step fails with `kubeconfig_load_failed (<reason>)`
(reason one of `missing`, `unreadable`, `oversized`, `yaml_invalid`,
`duplicate_yaml_key`, `structure_invalid`). A cluster, context or user name
defined more than once across the whole `KUBECONFIG` chain is used once when
every definition is identical, and fails the step with
`kubeconfig_duplicate_conflict` when they differ; the message locates every
definition by file and position, never by content. The step is not recorded as
complete, so a resume runs it again after you fix the kubeconfig. Remove stale
entries from `KUBECONFIG` rather than relying on them being skipped.

In the Ansible collection, direct klusterlet probes and remediations default to
10 worker threads, 30-second Kubernetes request timeouts, and 180-second
worker future timeout windows for each probe/remediation batch. After remediation,
the post-remediation klusterlet probe waits up to 300 seconds, polling every
10 seconds, for the klusterlet-updated hub kubeconfig secret to converge.
Override these with
`acm_switchover_execution.concurrency.klusterlet_probe_workers`,
`acm_switchover_execution.concurrency.klusterlet_remediation_workers`,
`acm_switchover_execution.timeouts.klusterlet_request_seconds`, and
`acm_switchover_execution.timeouts.klusterlet_worker_seconds`,
`acm_switchover_execution.timeouts.klusterlet_recheck_seconds`, and
`acm_switchover_execution.timeouts.klusterlet_recheck_interval_seconds`.
The two worker counts are capped at 32; a larger value fails the module with an
actionable message.

**Advantages:**
- Faster activation (data already restored)
- Lower risk (passive sync proven working)
- Minimal data loss window

### Method 2: One-Time Full Restore

**Use when:**
- Setting up a new secondary hub
- Passive sync was not previously configured
- You need a complete fresh restore

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method full \
  --old-hub-action decommission
```

**Considerations:**
- Longer restore time (all resources restored)
- Needs selectable credentials, resources, and managed-clusters Backups of one generation;
  activation freezes the controller's `latest` choice for each and creates `restore-acm-full`
  with those concrete Backup names, never the `latest` alias
- CleanupBeforeRestore applied

## Migration Evidence

Activation, post-activation, and finalization share one durable **migration journal**
(`migration_backups`, schema version 2). The Python CLI keeps it in its state file; the
Ansible collection keeps it in checkpoint `operational_data`. The two stores are
independent: each form factor reads and writes only its own journal, and a migration
started by one cannot be resumed by the other. The schema, transitions, reason codes, and
refusals are the same on both sides and are held equal by the root parity tests.

### What activation freezes

Before any Restore mutation — including the guarded delete of the passive sync Restore for
`--activation-method restore` — activation strictly reads the Velero Backup inventory,
predicts the restore controller's own Backup selection, and durably journals the selected
Backups with their identity and status. What the Restore spec carries depends on the method:

| Method | Restore | Backup fields written |
| --- | --- | --- |
| `--method passive` (patch) | the passive sync Restore, patched under its UID and resourceVersion | ManagedClusters `latest` |
| `--method passive --activation-method restore` | one-shot `restore-acm-activate` | ManagedClusters `latest`; credentials and resources `skip` |
| `--method full` and `--restore-only` | `restore-acm-full` | concrete credentials, resources, and managed-clusters Backup names |

`latest` stays in the spec only where the controller's own selection must run. Every Backup
and Velero child Restore that activation accepts as evidence is concrete and journaled, and
completion re-reads each frozen Backup and fails if it was replaced or its status drifted.
A resume never refreezes: it reuses the journaled Backups even if newer ones exist.

On ACM 2.12–2.16 a patched passive sync Restore may also request `CredentialsHive` and
`CredentialsCluster` Velero Restores. Activation predicts both before the patch: reuse of the
frozen credentials Backup (the selector shortcut of the supported Backup schedules) or a
proven "no candidate" is permitted; a selected distinct hive or cluster Backup, or a
prediction that cannot be proven, refuses the patch with no Restore mutation. At completion,
any child Restore of this Restore bound to a dedicated `acm-credentials-hive-schedule` or
`acm-credentials-cluster-schedule` Backup blocks, **including a historical child from before
the patch**. This restricts destination hubs whose passive sync history includes the legacy
three-Backup credentials format: a patch activation cannot complete while such a child of the
passive sync Restore exists.

A resume that finds the Restore mutation recorded but the journal lacking it, or a Restore at
the target name that this run never recorded creating, refuses: the object is never adopted.
Review and delete it, then retry — or start over with `--reset-state` where the message says so.

### Expected ManagedCluster names and the audited waiver

Activation and post-activation each check the expected names derived by preflight and the
count floor (see `--min-managed-clusters` above). `--skip-managed-cluster-expectations`
requests an audited waiver of the **name check only**:

```bash
python acm_switchover.py ... \
  --skip-managed-cluster-expectations \
  --managed-cluster-expectation-waiver-scope both \
  --managed-cluster-expectation-waiver-actor "jane@example.com" \
  --managed-cluster-expectation-waiver-reason "cluster-x retired after preflight" \
  --managed-cluster-expectation-waiver-request-id CHG-1234   # optional
```

- The scope is `activation`, `post_activation`, or `both`; actor and reason must be non-empty.
- A waiver never admits an **empty** destination. With recorded names and
  `--min-managed-clusters 0`, activation and post-activation both refuse a destination that
  has no ManagedCluster at all (`inventory_empty`); a destination with only `local-cluster`
  passes. If the primary genuinely has no clusters left to migrate, re-run preflight so the
  expectation is re-derived from the current primary.
- It is rejected with `--validate-only`, `--decommission`, `--setup`, and
  `--argocd-resume-only`; it is rejected with `--restore-only` in every scope, because restore-only
  records no expected name; and it is rejected when its scope has no expected name to waive.
- It never lifts the count floor, Restore identity, Backup provenance, completion,
  post-activation completion, cleanup recovery or repair, final locator absence, or teardown
  revalidation.
- The waiver is journaled once, when its phase decides the name check (activation journals an
  `activation` or `both` waiver, post-activation a `post_activation` one). A later run must
  repeat the same request or none; a different request is refused.

The collection equivalents are listed in the
[CLI migration map](../../ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md).

### Finalization order and the Restore-cleanup transaction

Finalization runs in this order on every pass:

1. **Journaled Restore cleanup.** The journal, not a step marker, decides the step. A fresh
   pass revalidates the journaled Restore live (identity, generation, owned spec, cleanup
   policy, child evidence, frozen Backups), records `restore.teardown_revalidated_at`, durably
   records cleanup intent with a new operation ID, then deletes the Restore under its UID and
   the resourceVersion of an immediately preceding strict read. A conflict re-reads and
   retries up to three times. After an accepted delete, absence is polled for at most 300
   seconds of elapsed time, and only a final strict 404 records `completed`.
2. **Old-hub observability teardown** — `--old-hub-action secondary` only, unless observability
   is skipped (`--skip-observability-checks`) or absent; the old-hub evidence gate (below) runs
   first.
3. **BackupSchedule enablement.** Other switchover Restores (other names) keep the existing
   name-based cleanup, but only once the journaled cleanup is terminal; an object at the
   journaled name blocks instead of being deleted. A fresh strict read must prove the
   journaled name absent immediately before the BackupSchedule is enabled. BackupSchedule
   collision repair is the exception to "immediately before the write": it proves the name
   absent immediately before it deletes the BackupSchedule, then waits for the deletion
   (about 30 seconds at most) and recreates the schedule without a second read, so a failed
   read can never leave the hub without a BackupSchedule. Each proof establishes absence only
   at the instant of the read; it reserves nothing. BackupSchedule inventory reads are strict:
   an unreadable inventory refuses to change BackupSchedules.
4. Backup and MultiClusterHub verification, auto-import reset.
5. **Old-hub disposition** (`--old-hub-action`), behind the old-hub evidence gate.

The **old-hub evidence gate** runs immediately before every integrated old-hub action. It
requires `post_activation.completed_at`, a `completed` or `repaired` cleanup, the activation
name check or a waiver covering it, and a fresh strict read proving that nothing occupies the
journaled Restore name. A missing post-activation completion refuses first, with
`post_activation_not_completed`; otherwise missing evidence refuses with the first missing reason
code (`cleanup_not_terminal`, `activation_names_unverified`, or `locator_occupied`) and lists all
of them. A dry-run previews and is not gated. Standalone `--decommission` is outside this
transaction.

### Cleanup recovery and operator repair

The cleanup never treats an unexplained absence as success. A resumed cleanup that finds the
Restore absent without recorded completion (`absent_without_completion`), a different UID at
the name (`replacement_uid`), or a replacement appearing while polling
(`replacement_during_poll`) durably records `recovery_required` and blocks cleanup,
BackupSchedule enablement, and every old-hub action. Nothing at the name is deleted or
adopted. An unreadable or malformed read changes nothing and blocks only the current attempt.
A poll timeout leaves the cleanup resumable.

The error that first records `recovery_required` names the run and operation IDs. A later
retry without repair reports `cleanup_recovery_required`; recover the IDs from the original
error or the retained journal's `run_id` and `cleanup.operation_id`. After inspecting the
destination hub, acknowledge the recovery explicitly:

```bash
python acm_switchover.py ... \
  --repair-migration-cleanup \
  --cleanup-repair-actor "jane@example.com" \
  --cleanup-repair-reason "Restore deleted manually during the incident" \
  --cleanup-repair-evidence "INC-42 audit log" \
  --cleanup-repair-run-id <run ID from the error> \
  --cleanup-repair-operation-id <operation ID from the error>
```

A repair is accepted only in `recovery_required`, only for the exact run and operation IDs,
and only when a fresh strict read proves the journaled name absent; it is recorded as an
audited acknowledgement, never as a delete or absence this run performed. Remove the repair
options on later runs: once the cleanup is `repaired` (or in any state other than
`recovery_required`) a repair request is refused (`repair_not_permitted`). The repair options
are rejected with `--validate-only`, `--decommission`, `--setup`, and `--argocd-resume-only`.

### Argo CD after a failure under a journal

With `--argocd-manage --argocd-resume-on-failure`, a phase failure **before** the journal
exists keeps the previous behavior: Applications are resumed. Before a journal exists,
switchover recovery rewinds to primary preparation. Python restore-only records a retry at
preflight; collection restore-only resumes the secondary Applications without rewinding
checkpoint phases. Once a journal exists (a failure in activation,
post-activation, or finalization), the rescue never rewinds: it first durably records an
Argo CD re-pause marker in the state (outside the journal), then resumes the paused
Applications, and the retry stays at the failed phase. The retry re-pauses the
Applications (both hubs; the secondary hub for `--restore-only`) before any further phase
work, never replays the rest of primary preparation, and clears the marker only after the
re-pause succeeds. If the marker cannot be recorded, no Application is resumed. A failed
resume is reported with the original failure and names `--argocd-resume-only` for manual
recovery; a failed re-pause fails the retry and keeps the marker, so the next retry re-pauses
again.

### Ansible collection specifics

- **Execute mode needs the checkpoint.** The journal lives only in the checkpoint, so an
  executing `activation` refuses before any Restore mutation unless
  `acm_switchover_execution.checkpoint.enabled: true`, and finalization has no journal-free
  path.
- **Direct role entry.** A direct role invocation validates operator inputs before ordinary phase mutations. Any recorded Argo CD re-pause obligation runs first. `activation`, `post_activation`, and `finalization` validate their
  operator inputs (including the waiver) at their own entry, restore the ManagedCluster
  expectation the checkpoint recorded, and refuse a waiver scope with no expected name. In
  execute mode, `activation` and `post_activation` refuse when no expectation is recorded and
  `min_managed_clusters` is not set explicitly; `finalization` restores a recorded expectation
  but does not refuse without one. A `validate` entry stays non-mutating.
- **Rewinds.** `checkpoint.reset_from: activation`, `post_activation`, or `finalization` keeps
  and revalidates the journal; `reset_from: preflight` or `primary_prep` is refused while a
  journal exists. The full `checkpoint.reset: true` is the only reset that discards it. A
  completed `post_activation` marker without the evidence it claims is refused at the
  `post_activation` role, and `reset_from: post_activation` re-runs post-activation against
  the kept journal. The Python CLI has no phase-rewind option; its equivalent is the
  `current_phase` edit described under [Migration journal on resume](#migration-journal-on-resume).
  This is a tooling difference between the form factors, not a behavior divergence.
- **Argo CD rescue.** The `switchover.yml` and `restore_only.yml` rescues follow the rule
  above: with a journal they record the re-pause marker in the failed phase's `fail`
  transition, resume only once that write is acknowledged, and never issue the
  `primary_prep` reset; failures are reported with the original failure, not ignored.

### Dry-run and check mode

`--dry-run` performs the strict reads and predictions and reports what it would freeze,
mutate, or record. The collection's `dry_run` and `validate` modes and native `--check` are
previews too. Preview modes write no migration-journal transition and send no Restore
mutation. Python skips the finalization-entry marker refusal in dry-run, but finalization
previews in both form factors can still refuse an existing journal whose required completion
evidence is missing.

### Evidence boundary

The migration-evidence behavior is covered by fake-backed unit, integration, scenario, and
parity tests in both form factors. None of that is live certification evidence; see the
[testing guide](../development/testing.md).

## Advanced Scenarios

### Scenario 1: Switchover Without Observability

If ACM Observability is not deployed, the script automatically detects this and skips related steps:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary
```

**Automatic detection:**
```
✓ ACM Observability: not detected (optional component)
Skipping Thanos compactor scaling (Observability not detected)
Skipping Observability verification (not detected)
```

### Scenario 2: Force Skip Observability Checks

When Observability is detected, related failures are blocking by default:
Thanos scale-down on the old hub, post-activation Observability scale-up,
observatorium-api restart/readiness, pod health, and old-hub Observability
termination must succeed. To explicitly bypass those checks and mutations, set
`--skip-observability-checks`:

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --skip-observability-checks
```

**Use case:** Use only when the operator has decided Observability issues should
not block cluster migration and will be handled separately.

> **Thanos and Python client hardening:** Thanos compactor scale-down is now
> verified with bounded polling against the remaining timeout budget instead
> of a single one-shot check — the operation fails after the configured wait
> rather than returning prematurely. In post-activation, programming errors
> from internal helpers are re-raised instead of being downgraded to retryable
> failures so bugs surface immediately. The Python `KubeClient` no longer
> falls back to the global in-cluster/default kubeconfig if its explicit
> kubeconfig/context cannot be loaded; that scenario now fails fast to prevent
> operating against the wrong cluster.

### Scenario 3: Old Hub Kept as Secondary (Observability Disabled Automatically)

When `--old-hub-action secondary` is used, finalization now deletes
`MultiClusterObservability` on the old hub automatically so the long-lived
secondary does not keep running ACM Observability.

```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary
```

**Notes:**
- `--disable-observability-on-secondary` is still accepted for compatibility,
  but it is now redundant and deprecated.
- Old-hub Observability pod termination is blocking by default. Use
  `--skip-observability-checks` only when intentionally bypassing Observability
  handling.
- If the MCO is managed by GitOps (ArgoCD/Flux), coordinate deletion to avoid drift.

### Scenario 4: Different ACM Versions (2.11 vs 2.12+)

The script auto-detects ACM version and adjusts BackupSchedule handling:

**ACM 2.12+:**
```
Using spec.paused for ACM 2.12.0
BackupSchedule schedule-rhacm paused successfully
```

**ACM 2.11:**
```
ACM 2.11.3 requires deleting BackupSchedule
BackupSchedule schedule-rhacm deleted (saved to state)
```

No manual intervention needed - version-aware handling is automatic.

## Returning to Original Hub (Reverse Switchover)

To return to the original hub after a switchover, perform a **reverse switchover** by swapping the primary and secondary contexts. This is the recommended approach as it uses the same proven switchover workflow.

> **Prerequisite:** Your original switchover must have used `--old-hub-action secondary` to set up passive sync on the old hub. This is why `secondary` is the recommended value for this option.

### Step 1: Verify Old Hub Has Passive Sync

On the original primary hub (now acting as secondary):

```bash
oc get restore restore-acm-passive-sync -n open-cluster-management-backup
# Should show Phase="Enabled"
```

### Step 2: Run Reverse Switchover

Simply swap the `--primary-context` and `--secondary-context` values:

```bash
# Original switchover was:
# python acm_switchover.py --primary-context hub-A --secondary-context hub-B ...

# Reverse switchover (swap contexts):
python acm_switchover.py \
  --primary-context hub-B \
  --secondary-context hub-A \
  --method passive \
  --old-hub-action secondary
```

### Step 3: Verify Clusters Reconnect

Wait 5-10 minutes and verify clusters are `Available=True` on the original hub:

```bash
oc get managedclusters -o custom-columns=NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type=="ManagedClusterConditionAvailable")].status
```

> **Tip:** The same validate → dry-run → execute workflow applies to reverse switchovers.

## Restore-Only Mode (Single Hub)

When the original hub is gone (destroyed, decommissioned, or unreachable) and you need to restore managed clusters from existing S3 backups onto a new hub:

```bash
# Validate the new hub is ready for restore
python acm_switchover.py \
  --restore-only \
  --validate-only \
  --secondary-context new-hub

# Dry-run to preview planned actions
python acm_switchover.py \
  --restore-only \
  --dry-run \
  --secondary-context new-hub

# Execute the restore
python acm_switchover.py \
  --restore-only \
  --secondary-context new-hub
```

**What happens:**
1. **Preflight** — validates the target hub only (ACM version, BSL credentials, namespaces)
2. **Activation** — journals the latest credentials, resources, and managed-clusters Backups
   by name, then creates a one-time full Restore with those concrete names (see
   [Migration evidence](#migration-evidence))
3. **Post-Activation** — waits for ManagedClusters to connect and verifies klusterlet agents
4. **Finalization** — deletes the journaled Restore through the Restore-cleanup transaction, then attempts to enable BackupSchedule on the new hub; if none is found (ACM excludes BackupSchedule from Velero backups to avoid circular backup-of-backup), a warning is emitted and the operator must create one manually after restore

**Key differences from normal switchover:**
- No `--primary-context` needed (there is no primary hub)
- `--method full` is implied and enforced
- `--old-hub-action` is not accepted (no old hub to manage)
- PRIMARY_PREP phase is entirely skipped
- Cannot be combined with `--decommission` or `--method passive`

**Prerequisites:**
- The new hub must have ACM installed and a BackupStorageLocation (BSL) pointing to the same S3 bucket as the old hub's backups
- A valid backup must exist in the S3 bucket

## Decommission Old Hub

After confirming successful switchover, decommission the old primary hub:

```bash
python acm_switchover.py \
  --decommission \
  --primary-context old-primary-hub
```

**Interactive prompts:**
```
DECOMMISSION MODE - This will remove ACM from the old hub!

Are you sure you want to proceed with decommissioning the old hub? [y/N]: y

Delete MultiClusterObservability resource? [y/N]: y

Delete ManagedCluster resources (excluding local-cluster)? [y/N]: y

Delete MultiClusterHub resource? (This will remove all ACM components) [y/N]: y
```

**Timeline:**
- Delete Observability: 3-5 minutes
- Delete ManagedClusters: 1-2 minutes
- Delete MultiClusterHub: 15-20 minutes typically; the resource-absence wait and the Pod drain that
  follows it are each bounded at 1200 seconds (polled every 30 seconds), so a slow hub can take longer

**Decommission substep outcomes.** Python and the collection use the same five
outcomes for one teardown substep. A preview (`--dry-run`, collection
`mode: dry_run`, or native check mode) does not record them.

- `not_requested` — the substep was not requested or not enabled. That is not a failure. An outstanding observability teardown record keeps the substep requested on both form factors, even when observability is disabled.
- `precondition_noop` — teardown was requested, the target was positively absent, and no teardown record was outstanding, so nothing had to be done. This is a successful no-op. It is not `completed`, and it is not proof that a teardown ran. For MultiClusterObservability, the custom resource and its namespace must both be positively absent; a missing custom resource whose namespace is still present is not this outcome.
- `completed` — the requested teardown reached its required completion proof, including a fresh live proof when a resumed record is re-checked. `completed` does not require `changed` on this invocation: a resumed obligation can finish with no new mutation.
- `refused` — the operator declined a required destructive confirmation. The decommission is unsuccessful, and later requested substeps are not attempted. The collection role does not prompt, so it does not emit `refused`.
- `failed` — the substep hit an expected operational failure. The decommission is unsuccessful.

`refused` and `failed` make the decommission result unsuccessful. The Python CLI
exits non-zero for either. A collection substep that fails records `failed` and
fails the play; the role does not emit `refused`. Declining the Python CLI's
opening decommission confirmation cancels the run before any substep and is also
unsuccessful; that cancellation is not one of the five outcomes. The collection
has no such prompt: execute mode without
`acm_switchover_decommission.confirmed: true` fails before any substep outcome
is recorded.

`changed` is an actual mutation accepted during this invocation. `would_change`
is only a read-only preview. A preview can report `would_change` while `changed`
stays false and no substep outcome is recorded.

**MultiClusterHub teardown contract:**
The MultiClusterHub target is resolved dynamically, never from a hard-coded name: decommission lists
MultiClusterHubs in `open-cluster-management` and finds zero (nothing to tear down), exactly one (the
target), or more than one — in which case it refuses to choose. A dry run reads the current
MultiClusterHub and the ACM operator identity, but writes nothing durable and deletes nothing. An
execute run persists the target name and UID together with the captured operator identity *before* the
UID-guarded delete. If that identity cannot be determined, the run records an explicit "unavailable"
identity and proceeds — but then no Pod is excluded from the drain, so every ACM Pod must terminate on
its own. Remaining Pods are judged by owner chain up to the recorded operator Deployment, which replaces
the older Pod-name prefix heuristic. A drain that times out, a read that cannot be verified, or a
recorded identity that no longer matches the live Deployment fails the run instead of warning; the
recorded teardown stays an outstanding obligation that a retry resumes, and an ambiguous namespace
read or a recorded identity that no longer holds is additionally recorded as `recovery_required`. A resumed run reuses the recorded target and identity — it never
recaptures or rebinds them — and a teardown already recorded as completed is re-proved against the live
hub rather than trusted. `changed` in the run result means this run's delete was accepted, which is
distinct from a dry run's would-change preview.

Decommission therefore also needs the `open-cluster-management` reads that identity capture and Pod
classification issue — ClusterServiceVersion get/list, Deployment get, ReplicaSet get — plus
`multiclusterhubs` get on the decommission extension; the shipped manifests include them, and the full
matrix is in [RBAC requirements](../deployment/rbac-requirements.md).

**ManagedCluster deletion safety gate:**
Before deleting any non-local `ManagedCluster`, decommission re-reads Hive
`ClusterDeployment` resources on the old hub and blocks deletion when a matching
ClusterDeployment does not have `spec.preserveOnDelete=true`. If the Hive API
lookup fails with permissions, a timeout, a missing API/CRD, or another API
error, decommission fails closed before deleting ManagedClusters. A successful
lookup with no matching ClusterDeployments remains acceptable.

Matching considers multiple explicit cluster identifiers (ManagedCluster name,
namespace, and any cross-referenced Hive cluster IDs) rather than relying on
name alone. If a plausible MC ↔ CD relationship is found but cannot be verified
unambiguously, decommission fails closed instead of proceeding with a guess.

In the Ansible collection, decommission additionally requires an **explicit**
primary hub input (`acm_switchover_hubs.primary`) — it will not infer the
old-hub target from defaults or secondary-only configuration.

### Standalone collection decommission: durable state and physical identity

Running `playbooks/decommission.yml` on its own — outside a full switchover — is a
one-hub workflow with its own requirements.

**Execute mode requires durable checkpointing.** The identity map must be persisted
before the first delete, so execute mode **fails closed before any teardown** when
checkpointing is unavailable:

```yaml
acm_switchover_execution:
  mode: execute
  checkpoint:
    enabled: true
    backend: file
    path: .state/switchover.json
```

The shipped generic example `examples/group_vars/all.yml` sets
`checkpoint.enabled: false`. It is shared switchover configuration, so it is **not**
a complete standalone-decommission configuration — set `enabled: true` in your own
inventory or group vars rather than editing the shared example, which would alter
unrelated workflows.

**Physical identity is established before teardown and revalidated on resume.** A
fresh standalone run reads the primary hub's `kube-system` Namespace UID and records
it before the first delete. Every later transition re-reads it and compares.
Consequences worth planning for:

- a kube context **repointed at a different physical cluster is refused**, even
  though its name is unchanged — the recorded cluster UID no longer matches;
- an ordinary **retry resumes** the retained checkpoint and re-proves identity; it
  needs no special flags;
- standalone decommission does **not** require a secondary hub, and reads none.

**What that identity does not prove.** These are separate identity properties:

| Identity property | R4-03 status | Owner |
| --- | --- | --- |
| Recorded resource / UID identity | **provided by R4-03** | R4-03 |
| Collection standalone resume continuity to the recorded physical cluster | **provided by R4-03** | R4-03 |
| Initial wrong-hub / expected-target selection | **not provided by R4-03** | SSA-02 |

- **Resource identity.** Both form factors record the UID of a resource they are
  tearing down and delete only that UID. A replacement object created later
  under the same name is not deleted as if it were the recorded one.
- **Resume continuity.** The stored primary `kube-system` Namespace UID, on
  collection standalone decommission, stops a later transition when the
  configured context now points at a different physical cluster than the one
  this run recorded. Python `--decommission` does not bind the switchover
  hub-identity map; on that path the resource UID above is the identity check.

A collection standalone run records whichever cluster the configured context
points at on first execution. Selecting the intended old hub remains the
operator's responsibility, including standalone and non-interactive runs.

**Modes.** `validate` is **refused** for decommission — it is not a preview: the
role's delete guards fire in any mode other than `dry_run`, so a validate run would
really delete while recording no durable identity. Preview with `mode: dry_run`, or
use native check mode (`--check`). Neither establishes authoritative teardown
identity, neither writes checkpoint state, and neither publishes destructive
completion authority. Collection-wide `validate` behaviour outside this role is
tracked separately in issue #284.

**Reset flags are refused on the standalone path.** `checkpoint.reset: true` and a
non-empty `checkpoint.reset_from` are rejected on every standalone decommission
transition. Those flags bypass operation-identity validation, so honouring them
would let a standalone run overwrite an identity established by a full two-hub
switchover. This is a fail-closed guard, not a limitation to work around.

Note that `acm_switchover_execution.checkpoint` exposes five keys — `enabled`,
`backend`, `path`, `reset`, `reset_from` — and only the last two are refused here.
Generic checkpoint semantics elsewhere are unchanged: a full `checkpoint.reset`
still rebuilds state and discards teardown data, `reset_from` still retains and
revalidates it, and integrated decommission (as part of a switchover) continues
under normal two-hub `finalization` checkpoint semantics.

**Starting completely fresh.** To abandon a standalone decommission and begin from
empty state, remove, rename, or repoint the operator-owned checkpoint file at
`acm_switchover_execution.checkpoint.path`. A run against an absent file builds a
fresh record. ⚠️ This **discards the teardown obligations the record carried**: a
rerun that then finds a resource absent cannot distinguish "already deleted" from
"never attempted". Retry — which resumes retained state — is the normal path;
reach for the wipe only when deliberately abandoning those obligations.

**Decommission RBAC bootstrap filtering:**
When bootstrapping decommission RBAC, the tool applies a **label-positive**
filter — manifests are included only when they explicitly carry the
decommission-extension label. Resources without the label are ignored even if
their names match. This prevents accidental bootstrapping of unrelated
manifests that happen to live alongside the decommission extension.

**Non-interactive mode (automation):**
```bash
python acm_switchover.py \
  --decommission \
  --primary-context old-primary-hub \
  --non-interactive
```

⚠️ **WARNING:** Non-interactive mode proceeds without confirmation and can only be used together with `--decommission`. Use only in fully automated environments.

## Troubleshooting

### Issue: ClusterDeployments Missing preserveOnDelete

**Validation fails with:**
```
✗ ClusterDeployment preserveOnDelete: ClusterDeployments missing preserveOnDelete=true: 
  cluster1/cluster1-cd, cluster2/cluster2-cd
```

**Fix before proceeding:**
```bash
# For each ClusterDeployment listed:
oc patch clusterdeployment <name> -n <namespace> \
  --type='merge' \
  -p '{"spec":{"preserveOnDelete":true}}'
```

Then re-run validation.

### Issue: Backup in Progress

**Validation fails with:**
```
✗ Backup status: backup(s) in progress: acm-resources-backup-20251118103045
```

**Resolution:**
Wait for backup to complete, then re-run. Check backup status:
```bash
oc get backup -n open-cluster-management-backup
```

### Issue: Clusters Stuck in "Pending Import"

**After activation, some clusters not connecting:**

**Check:**
1. Wait 10-15 minutes (auto-import can be slow)
2. Verify import secrets exist:
   ```bash
   oc get secret -n <cluster-namespace> | grep import
   ```
3. Check ManagedCluster events:
   ```bash
   oc describe managedcluster <cluster-name>
   ```

### Issue: No Metrics in Grafana After Switchover

**Metrics not appearing after activation:**

**Solution:**
Verify observatorium-api was restarted:
```bash
oc get pods -n open-cluster-management-observability | grep observatorium-api
```

If not restarted, manually restart:
```bash
oc rollout restart deployment/observability-observatorium-api \
  -n open-cluster-management-observability
```

Wait 10 minutes for metrics collection to resume.

### Argo CD Detection and Management

When Argo CD manages ACM resources (BackupSchedule, Restore, ManagedCluster, etc.), auto-sync can fight switchover steps. The tool can detect and optionally manage Argo CD:

**Detection only (no changes):**
```bash
python acm_switchover.py \
  --validate-only \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub
```

**Pause auto-sync during switchover (recommended if GitOps manages ACM):**
```bash
python acm_switchover.py \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub \
  --method passive \
  --old-hub-action secondary \
  --argocd-manage
```

Applications that touch ACM namespaces/kinds are paused (auto-sync removed) and left paused by default. State is stored in the switchover state file.

For collection retries and standalone resume, checkpoint-persisted namespace
scope is fail-closed: every normalized namespace must return exactly one
positively successful discovery result before any Application is aggregated,
filtered, or patched. A failed, skipped, unreachable, malformed, incomplete, or
mixed present/absent namespace result invalidates the entire discovery; partial
Applications are never mutated.

**Managed pause safety checks:**
- ApplicationSet-managed child Applications are not patched. The ApplicationSet controller can revert child edits, so pause or update the parent ApplicationSet, generator, or template, then retry.
- Auto-sync Applications with empty or stale `status.resources` block the workflow because ACM impact cannot be ruled out. Refresh/sync the Application until Argo CD reports current resources, or inspect and pause it manually.
- After each pause patch, the tool re-reads the Application and fails if `spec.syncPolicy.automated` remains enabled.

**Resume auto-sync after updating Git for the new hub:**
- Standalone: `--argocd-resume-only` with `--primary-context` and `--secondary-context` to restore from state.
- Resume-only auto-discovers the original state file when the swapped-context match is unambiguous. If both context orderings have state files, pass the original file explicitly with `--state-file`.
- Resume-only now requires stored hub identity bindings when paused Applications are present. Legacy state files created before hub UID binding must be resumed with `--force` only after the operator manually verifies both hubs still point at the intended live clusters.

Resume patches an Application only when its
`acm-switchover.argoproj.io/paused-by` annotation exactly matches the persisted
Argo CD run ID. A missing marker is an idempotent no-op. A different non-empty
run ID is left untouched and reported as a mismatch, even when auto-sync is
already enabled; strict resume never restores the recorded sync policy or
deletes a marker owned by another run. If a backup restored an older marker,
inspect the live Application and explicitly remove the marker only after
confirming that it is stale. Matching-marker patches are conditional on the
Application `resourceVersion` observed during the ownership check. If a backup
restore or concurrent run changes the Application before the patch, Kubernetes
rejects it (normally with `409 Conflict`); the result is actionable, not a
no-op, and the operator must inspect the new live owner before retrying.

Note: `--argocd-manage` is allowed with `--validate-only`, but it has no effect and the CLI emits a warning. `--argocd-resume-only` is not compatible with `--validate-only`, `--decommission`, or `--setup`. If `--argocd-manage` was run with `--dry-run`, resume is blocked because the pause was not actually applied—re-run without `--dry-run` to generate resumable state.

With `--argocd-manage --dry-run`, the Python CLI still discovers ACM-touching Applications and reports ApplicationSet or stale-status blockers during both switchover and restore-only flows before continuing. Dry-run suppresses the patch itself, but it no longer skips the discovery/reporting path.

⚠️ Only resume after Git/desired state reflects the **new** hub; otherwise Argo CD can revert switchover changes.

**Resume on failure:** Add `--argocd-resume-on-failure` alongside `--argocd-manage` to automatically attempt ArgoCD resume if the switchover fails. This is safe because Git repos have not been updated yet, so ArgoCD syncing back to the original desired state helps restore pre-switchover state. Resume errors are logged but do not compound the original failure. The Python CLI now reuses the same hub identity validation as standalone resume, so legacy state without hub UID bindings requires `--force` before any best-effort resume patch is attempted. For Ansible, set `acm_switchover_features.argocd.resume_on_failure: true` in your vars file.

**Bash alternative:** `./scripts/argocd-manage.sh` was removed (one pause register per form factor; ADR-0001, #207). Use the Python CLI (`--argocd-manage`) or the Ansible collection (`argocd_manage` role) instead.

### Issue: Script Hangs During Restore

**Restore phase stuck in "Running":**

**Check Velero logs:**
```bash
oc logs -n open-cluster-management-backup deployment/velero --tail=100
```

**Check restore details:**
```bash
oc describe restore restore-acm-passive-sync -n open-cluster-management-backup
```

Look for specific errors and resolve before re-running.

## State Management

### View Current State

```bash
cat .state/switchover-<primary>__<secondary>.json | python -m json.tool
```

### Reset State (Start Fresh)

⚠️ **Caution:** Only use if you need to completely restart:

```bash
python acm_switchover.py \
  --reset-state \
  --validate-only \
  --primary-context primary-acm-hub \
  --secondary-context secondary-acm-hub
```

This deletes all progress tracking and starts from scratch, including any recorded
migration journal (see [Migration journal on resume](#migration-journal-on-resume)).

### Use Custom State File

Track multiple switchovers separately:

```bash
python acm_switchover.py \
  --primary-context hub-a \
  --secondary-context hub-b \
  --state-file .state/hub-a-to-hub-b.json
```

**Path Validation Rules**:
- Relative paths are allowed and recommended (e.g., `.state/myfile.json`)
- Absolute paths permitted under:
  - `/tmp/` directory
  - `/var/` directory  
  - Current workspace directory
  - User home directory
- Path traversal (`../`) and command injection characters are blocked
- See `docs/reference/validation-rules.md` for complete details

**Examples**:
```bash
# Relative path (recommended)
--state-file .state/custom-state.json

# Absolute path under workspace
--state-file /home/user/acm-project/.state/state.json

# Temporary file
--state-file /tmp/switchover-state.json
```

## Best Practices

### 1. Always Validate First
```bash
# Never skip this step
--validate-only
```

### 2. Test with Dry-Run
```bash
# Preview before executing
--dry-run
```

### 3. Enable Verbose Logging
```bash
# Better debugging and audit trail
--verbose
```

### 4. Structured Logging
```bash
# Use JSON format for log aggregation systems
--log-format json
```

### 5. Maintain State Files
- Keep `.state/` directory in version control (optional)
- Provides audit trail of switchovers
- Enables resume from failure

### 6. Plan Maintenance Window
- Estimated time: 30-60 minutes
- Brief managed cluster disconnect (5-10 minutes)
- Inform stakeholders beforehand

### 7. Verify Post-Switchover
```bash
# On new secondary hub, check:
oc get managedclusters
oc get backup -n open-cluster-management-backup

# Verify all clusters show AVAILABLE=True
```

### 8. Test Reverse Switchover
In a test environment, practice reverse switchover (swapping contexts) before production use.

## Complete Example: Production Switchover

```bash
#!/bin/bash
# Production ACM Switchover Script

set -e  # Exit on error

PRIMARY="prod-acm-primary"
SECONDARY="prod-acm-secondary"
OLD_HUB_ACTION="secondary"  # Options: secondary, decommission, none
STATE_FILE=".state/prod-switchover-$(date +%Y%m%d-%H%M%S).json"

echo "=== ACM Production Switchover ==="
echo "Started at: $(date)"
echo "Old hub action: $OLD_HUB_ACTION"
echo ""

# Step 1: Validation
echo "Step 1: Running validation..."
python acm_switchover.py \
  --validate-only \
  --primary-context "$PRIMARY" \
  --secondary-context "$SECONDARY" \
  --verbose

read -p "Validation passed. Continue with dry-run? (y/N): " confirm
[[ "$confirm" != "y" ]] && exit 0

# Step 2: Dry-run
echo "Step 2: Running dry-run..."
python acm_switchover.py \
  --dry-run \
  --primary-context "$PRIMARY" \
  --secondary-context "$SECONDARY" \
  --old-hub-action "$OLD_HUB_ACTION" \
  --state-file "$STATE_FILE" \
  --verbose

read -p "Dry-run complete. Execute switchover? (y/N): " confirm
[[ "$confirm" != "y" ]] && exit 0

# Step 3: Execute
echo "Step 3: Executing switchover..."
python acm_switchover.py \
  --primary-context "$PRIMARY" \
  --secondary-context "$SECONDARY" \
  --old-hub-action "$OLD_HUB_ACTION" \
  --state-file "$STATE_FILE" \
  --verbose

echo ""
echo "=== Switchover Completed ==="
echo "Completed at: $(date)"
echo "State file: $STATE_FILE"
echo "Old hub action: $OLD_HUB_ACTION"
echo ""
echo "Next steps:"
echo "  1. Verify clusters on new hub"
echo "  2. Test application functionality"
echo "  3. Inform stakeholders"
if [[ "$OLD_HUB_ACTION" == "none" ]]; then
  echo "  4. Manually handle old hub (action was 'none')"
fi
```

Save as `run-production-switchover.sh` and execute when ready.
