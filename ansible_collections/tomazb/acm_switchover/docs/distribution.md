# Distribution and Packaging Strategy

## Targets

- Ansible Galaxy-compatible packaging
- Automation Hub-compatible packaging
- execution environment for AAP

## Collection Primary Distribution

The collection is the canonical operator artifact. Distribution hierarchy:

| Artifact | Role |
| --- | --- |
| Galaxy / Automation Hub package | Canonical operator-facing distribution unit |
| Execution environment (ansible-builder) | Canonical AAP runtime; built from `execution-environment.yml` |
| Helm chart (`deploy/helm/acm-switchover-rbac/`) | Implementation asset consumed by the `rbac_bootstrap` role — not a parallel operator UX |
| Raw RBAC YAML (`deploy/rbac/`) | Implementation asset consumed by the `rbac_bootstrap` role — not a parallel operator UX |

The Helm chart and raw RBAC manifests are **not** standalone distribution targets. Operators using the
collection deploy RBAC through `playbooks/rbac_bootstrap.yml`, which internally applies the manifests
from `deploy/rbac/`.

## AAP Contract

- same playbooks as local CLI usage
- same variable model as local CLI usage
- survey and `extra_vars` values treated as untrusted input

## Lock Model

Phase 1 defines the rule only:

- local file-backed checkpoints require advisory locking
- shared or controller-backed checkpoints require a Lease-style or equivalent coordination mechanism
- lock failures must be explicit and operator-visible
