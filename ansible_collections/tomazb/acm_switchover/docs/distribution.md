# Distribution and Packaging Strategy

Supported `ansible-core`, `kubernetes.core`, Python, and AAP versions are
governed by [`compatibility.md`](compatibility.md), which is the authority for
every support claim below.

## Targets

- Ansible Galaxy-compatible packaging
- Automation Hub-compatible packaging
- execution environment for AAP (build input only — see below)

## Collection Primary Distribution

The collection is the canonical operator artifact. Distribution hierarchy:

| Artifact | Role |
| --- | --- |
| Galaxy / Automation Hub package | Canonical operator-facing distribution unit |
| Execution environment (ansible-builder) | Intended AAP runtime, built from `execution-environment.yml`. **Build input only: not repository-tested, not published, and carrying no certification claim.** No CI lane builds it. |
| Helm chart (`deploy/helm/acm-switchover-rbac/`) | Implementation asset consumed by the `rbac_bootstrap` role — not a parallel operator UX |
| Raw RBAC YAML (`deploy/rbac/`) | Implementation asset consumed by the `rbac_bootstrap` role — not a parallel operator UX |

The Helm chart and raw RBAC manifests are **not** standalone distribution targets. Operators using the
collection deploy RBAC through `playbooks/rbac_bootstrap.yml`, which internally applies the manifests
from `deploy/rbac/`.

The bundled baseline operator RBAC includes `delete` on
`observability.open-cluster-management.io/multiclusterobservabilities` because
normal finalization deletes old-hub MCO when observability is present. The
optional decommission assets are still required for `ClusterDeployment` list
safety validation and `ManagedCluster`/`MultiClusterHub` teardown permissions.

## AAP Contract

- same playbooks as local CLI usage
- same variable model as local CLI usage
- survey and `extra_vars` values treated as untrusted input

No AAP combination is repository-tested, and no formal certification claim is
made. See [Ansible Automation Platform](compatibility.md#ansible-automation-platform)
for which AAP versions are upstream-compatible and on what basis.

## Lock Model

Current boundaries define the rule only:

- local file-backed checkpoints require advisory locking
- shared or controller-backed checkpoints require a Lease-style or equivalent coordination mechanism
- lock failures must be explicit and operator-visible
