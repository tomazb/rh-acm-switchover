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

## MultiClusterHub teardown safety

The `decommission` role tears down the MultiClusterHub through checkpoint-backed durable phase state:
the target UID and the captured ACM operator identity are recorded before anything is deleted, the
delete itself is UID-guarded, and the final absence and drain proof is fail-closed. Pod ownership is
decided by the read-only `acm_pod_owner_classify` module, which captures that identity and classifies
Pods by owner chain without mutating anything. `mode: dry_run` and native check mode stay read-only on
this path. [`docs/coexistence.md`](docs/coexistence.md) states the contract shared with the Python
tool; the operator-facing walkthrough is in the repository's `docs/operations/usage.md`.

## Distinct physical-hub guard

For a normal two-hub switchover, preflight rejects identical context names and
requires distinct non-empty live `kube-system` Namespace UIDs before it can
enter a mutation-capable phase. Different contexts or kubeconfigs can still
resolve to the same physical Kubernetes cluster. Execute mode, including
native `ansible-playbook --check`, reads both UIDs freshly. Restore-only reads
only the secondary hub. Standalone decommission is outside this two-hub
distinctness rule. Resource-UID binding and resume continuity of a recorded
cluster are a different property from proving the initial target was the
intended old hub; the operator contract is in the repository
`docs/operations/usage.md` Decommission Old Hub section.

## Explicit Non-Scope

- additional functionality beyond the playbooks, roles, plugins, and modules currently shipped in this collection
- guarantees about environments, integrations, or workflows not documented in this README
