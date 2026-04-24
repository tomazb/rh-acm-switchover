# tomazb.acm_switchover

Foundation Ansible Collection for ACM hub switchover automation.

## Current Scope

- collection metadata and layout
- `preflight.yml` and `switchover.yml` playbooks for hub switchover workflows
- core phase roles used by the switchover flow
- collection variable model and compatibility docs
- checkpoint action plugin support and custom modules used by the workflow
- Argo CD management and decommission automation roles included in this collection

## Explicit Non-Scope

- additional functionality beyond the playbooks, roles, plugins, and modules currently shipped in this collection
- guarantees about environments, integrations, or workflows not documented in this README
