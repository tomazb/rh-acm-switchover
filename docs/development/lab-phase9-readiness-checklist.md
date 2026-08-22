# Phase 9 Lab Readiness Checklist

Operator checklist for preparing a disposable two-hub OpenShift lab for Phases 9B–9F.

This document is intentionally generic. Do not commit live context names, kubeconfig paths, API
endpoints, cluster UIDs, trust-anchor material, credentials, or generated live profiles into the
repository. Keep those values in an owner-only runtime directory outside the checkout.

## Authority

- The operator authorizes every live contact and every preparation mutation.
- The Python lab controller owns Phase 9 discovery, identity, authorization, and GO/NO-GO decisions.
- Agent/assistant workflows may orchestrate and explain, but must not issue ad hoc live mutations or
  override a controller decision.
- External preparation outputs are always `LAB_PREPARATION_ONLY`. They never count as Phase 9B or
  Phase 9C exit evidence and never count as live ACM certification evidence.

## Inventory labels versus runtime locators

Controller inventory labels remain stable aliases such as `hub-a`, `hub-b`, and expected managed-cluster
names. Runtime context names and kubeconfig references are locators only. Bind them in a private
runtime file, for example:

| Inventory label | Private runtime locator field | Notes |
| --- | --- | --- |
| `hub-a` | runtime hub context / cluster A | Stable physical inventory label |
| `hub-b` | runtime hub context / cluster B | Must be a distinct physical cluster |
| managed set | exact expected managed-cluster names | One to three names; exclude `local-cluster` |

Logical primary/secondary is live state. Prove it from ACM evidence later; never treat a context name
as a permanent role.

## Tier A — Phase 9B physical-identity readiness

Required for Phase 9B live-exit evidence (Issue #188 is closed). Re-prove after any later
preparation mutation:

- [ ] Two distinct, reachable OpenShift hubs with healthy API access
- [ ] Readable `kube-system` Namespace UID on both hubs
- [ ] Readable OpenShift `Infrastructure/cluster` UID and `status.infrastructureName` on both hubs
- [ ] Readable OpenShift `ClusterVersion/version` UID (version is corroborating only)
- [ ] Exact PEM API trust-anchor bundle available for each hub connection
- [ ] Distinct enrolled identity fingerprints for both hubs, stored outside Git
- [ ] Explicit operator opt-in and approval reference for the read-only live run
- [ ] Clean source revision matching the Phase 9B branch under test
- [ ] Owner-only runtime directory with separate `config/`, `credentials/`, `preparation-artifacts/`,
      and later `certification-artifacts/` roots

Not required for the Phase 9B exit gate:

- ACM/MCE installation
- Managed-cluster import
- BackupSchedule / Restore known state
- OADP / object storage
- OpenShift GitOps
- Switchover RBAC bootstrap

## Tier B — Phase 9C / 9E / 9F known-state readiness

Required before Phase 9C authorization and before each live mutating segment (Phase 9E and
Phase 9F):

- [ ] ACM/MCE and a ready `MultiClusterHub` on both hubs at an officially supported combination
- [ ] Exactly one active primary and one passive secondary from agreeing ACM evidence
- [ ] Exact expected managed-cluster set owned only by the active primary
- [ ] All expected managed clusters accepted, joined, available, and connected
- [ ] Secondary does not actively own the same managed-cluster set
- [ ] OADP / `DataProtectionApplication` healthy on both hubs
- [ ] Shared operator-approved object storage configured without committing credentials
- [ ] Healthy `BackupStorageLocation` on both hubs
- [ ] Enabled, non-paused `BackupSchedule` plus recent successful backups on the active primary
- [ ] Coherent non-terminally-failed passive/sync `Restore` on the secondary for the same lineage
- [ ] Both hubs can read the shared backup objects so reverse passive switchover remains possible
- [ ] `acm-switchover-validator` and `acm-switchover-operator` service accounts bootstrapped on both hubs
- [ ] SubjectAccessReview allow/deny evidence for the bootstrapped accounts
- [ ] Decommission RBAC extension left disabled for Phases 9B–9F
- [ ] OpenShift GitOps / Argo CD CRDs readable if GitOps is in scope
- [ ] Initial GitOps posture is no ACM ownership, or observe-only only
- [ ] Current official-source compatibility evidence recorded and regenerated within policy expiry
      before each authorization window

Tier B is not a one-time gate. Because the Phase 9E segment flips the primary/secondary roles,
Phase 9F authorization requires re-proving Tier B from fresh controller rediscovery of the post-9E
state, under a new profile/approval reference and unexpired compatibility evidence, before any 9F
mutation or handoff.

## Executable assets already in this repository

Use these for tool RBAC and switchover validation after the lab exists. They do not provision
OpenShift, ACM, OADP, managed-cluster import, or object storage:

- Collection `rbac_bootstrap` playbook and `deploy/rbac` / Helm / Kustomize RBAC assets
- Live RBAC SAR certification path (`rbac-bootstrap-live`, opt-in)
- Existing e2e / preflight / postflight / discover-hub operator tooling
- Static `tests/release/kustomize/` fixtures as shape references only; do not apply hostile GitOps
  fixtures to the live lab

## Preparation evidence contract

Every external preparation artifact must record:

- `purpose=lab_preparation`
- `evidence_class=LAB_PREPARATION_ONLY`
- `certification_eligible=false`
- `live_certification_evidence=false`
- `mutation_attempted` accurately reflecting whether the lab was mutated
- redacted/fingerprinted identity evidence only
- source revision and compatibility-evidence hash when applicable

After any preparation mutation, re-run fresh controller discovery. Stale preparation evidence cannot
authorize Phase 9B exit, Phase 9C authorization, or Phase 9E/9F mutation.

## Phase sequencing reminder

1. Tier A readiness + Issue #188 live read-only exit (closed; rediscover after later prep)
2. Phase 9C known-state / non-executable authorization
3. Phase 9D bootstrap automation only after 9C, or continue with operator-proven Tier B state
4. Phase 9E one Python passive-switchover segment
5. Phase 9F one Ansible reverse passive-switchover segment from freshly proven 9E final state
