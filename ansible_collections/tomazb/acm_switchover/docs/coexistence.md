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

## GitOps Integration Boundary

Generic GitOps marker detection in the collection is **read-only and warning-oriented**.
The `roles/preflight/tasks/validate_gitops.yml` task records an informational result
(`preflight-gitops-warning`) when `acm_switchover_features.skip_gitops_check` is not
set, but does not fail the preflight or block the switchover.

Argo CD auto-sync pause/resume is the **only supported mutating GitOps integration**
in the collection. It is managed by the `argocd_manage` role:

- Pause is triggered in `primary_prep` when `acm_switchover_features.argocd.manage: true`
- Automatic resume during finalization has been removed (unsafe — operator must retarget Git first)
- A standalone resume entrypoint is available at `playbooks/argocd_resume.yml`

The `app.kubernetes.io/instance` label is treated as `UNRELIABLE` by the marker detector
and must not be used as a definitive GitOps signal. Use `argocd.argoproj.io/instance`
or `app.kubernetes.io/managed-by: argocd` instead.
