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

**Default posture (audit C4):** `checkpoint.enabled` remains `false` by
default. Without checkpointing the collection has no resume and no
hub-identity binding for resumed runs; enabling it is the operator's opt-in
for resumable executions. The safety obligations that must survive
interruption (Argo CD pause register, auto-import reset) live on the cluster
and do not depend on this setting.

**resume_summary:** both runtimes now use replace semantics — each resumed
process records the phase it started at (`resume_start_phase`), last resume
wins. Shared key names are pinned by `tests/test_checkpoint_state_parity.py`.

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
