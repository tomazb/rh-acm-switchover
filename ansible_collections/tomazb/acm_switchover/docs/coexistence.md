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
are exactly the Applications currently paused by this tool. Resume removes an
entry immediately when its Application is restored (or found already resumed);
failed entries stay for retry. Dry-run records nothing in the register, and the
register is never cleared just because the Applications CRD stops being visible
— only an empty register is cleaned up on CRD-visibility loss.

The collection's checkpoint/cluster-as-truth model is the equivalent register
on the Ansible side: the cluster-side `paused-by` markers plus the checkpoint
scope play the same role, and both form factors share the marker-ownership and
resourceVersion-conditional patch rules documented above.
