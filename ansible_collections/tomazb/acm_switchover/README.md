# tomazb.acm_switchover

Production-ready Ansible Collection for ACM hub switchover automation.

## Compatibility

Requires `ansible-core` `>=2.16.0,<2.22` and `kubernetes.core` `>=6.0.0,<7.0.0`.
Repository-tested lanes are `ansible-core` 2.16 on Python 3.11 and 2.21 on
Python 3.12. No AAP combination is repository-tested and no certification claim
is made.

[`docs/compatibility.md`](docs/compatibility.md) is the authority: it states the
full matrix, what "supported" means for each combination, the AAP and
execution-environment posture, and the local validation commands.

## Current Scope

- collection metadata and layout
- `preflight.yml` and `switchover.yml` playbooks for hub switchover workflows
- core phase roles used by the switchover flow
- collection variable model and compatibility docs
- checkpoint action plugin support and custom modules used by the workflow
- Argo CD management and decommission automation roles included in this collection

## Argo CD Safety Boundary

The `argocd_manage` role fails closed instead of patching unsafe child Applications. It blocks auto-sync Applications managed by an ApplicationSet when they touch ACM resources, blocks auto-sync Applications with empty or stale `status.resources`, and re-reads patched Applications to confirm auto-sync is disabled. Resume is fail-closed too (ADR-0001): it fails when a pause run_id is recorded but the Application CRD is not visible, and it never patches `spec.syncPolicy` without a recoverable `original-sync-policy` annotation — Applications paused by the Python tool must be resumed with `acm_switchover.py --argocd-resume-only`. For ApplicationSet-managed cases, pause or update the parent ApplicationSet, generator, or template rather than the generated child Application.

## Explicit Non-Scope

- additional functionality beyond the playbooks, roles, plugins, and modules currently shipped in this collection
- guarantees about environments, integrations, or workflows not documented in this README
