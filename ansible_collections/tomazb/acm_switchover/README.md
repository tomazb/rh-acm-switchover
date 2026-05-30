# tomazb.acm_switchover

Production-ready Ansible Collection for ACM hub switchover automation.

## Current Scope

- collection metadata and layout
- `preflight.yml` and `switchover.yml` playbooks for hub switchover workflows
- core phase roles used by the switchover flow
- collection variable model and compatibility docs
- checkpoint action plugin support and custom modules used by the workflow
- Argo CD management and decommission automation roles included in this collection

## Argo CD Safety Boundary

The `argocd_manage` role fails closed instead of patching unsafe child Applications. It blocks auto-sync Applications managed by an ApplicationSet when they touch ACM resources, blocks auto-sync Applications with empty or stale `status.resources`, and re-reads patched Applications to confirm auto-sync is disabled. For ApplicationSet-managed cases, pause or update the parent ApplicationSet, generator, or template rather than the generated child Application.

## Explicit Non-Scope

- additional functionality beyond the playbooks, roles, plugins, and modules currently shipped in this collection
- guarantees about environments, integrations, or workflows not documented in this README
