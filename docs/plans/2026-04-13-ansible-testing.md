# Test Plan: Ansible Collection Real-Cluster E2E Testing

**Date**: 2026-04-13
**Scope**: Comprehensive test plan for validating the `tomazb.acm_switchover` Ansible Collection against real ACM clusters
**Status**: PLAN

---

## 1. Context and Goals

### 1.1 Current State

The Ansible Collection (`tomazb.acm_switchover` v0.1.0) has been developed through 6 phases with:
- 7 playbooks, 9 roles, 11 custom modules, 1 action plugin
- 95 unit tests, 15 integration tests, 2 scenario tests (112 total) — all fixture-driven (mocked Kubernetes)
- **Zero real-cluster testing** — all tests use pre-seeded YAML variables to simulate K8s API responses

The Python CLI tool has a mature E2E framework (`tests/e2e/`) with 12 successful real switchovers documented, soak testing, failure injection, and monitoring. The Ansible Collection has no equivalent.

### 1.2 Goals

1. Validate that the Ansible Collection can execute against real ACM clusters without errors
2. Confirm parity with the Python tool's behavior on the same cluster topology
3. Identify any bugs, missing error handling, or incorrect Kubernetes API interactions
4. Build confidence for production readiness

### 1.3 Non-Goals (for this plan)

- Building a full automated E2E framework (that's a separate effort)
- AAP/Automation Controller integration testing (requires AAP infrastructure)
- Soak testing or failure injection (future phases)
- Replacing the Python E2E framework

---

## 2. Prerequisites

### 2.1 Cluster Environment

**Required topology** (matches existing Python E2E environment):

| Cluster | Role | Requirements |
|---------|------|-------------|
| mgmt1 | Primary ACM hub | ACM 2.14.x, OADP/Velero configured, BackupSchedule active, passive-sync restore running |
| mgmt2 | Secondary ACM hub | ACM 2.14.x, OADP/Velero configured, passive-sync restore receiving from mgmt1 |
| prod1–prod3 | Managed clusters | Registered as ManagedClusters on the active hub |

**State verification before testing**:
```bash
# Confirm both hubs are reachable
kubectl --context mgmt1 get multiclusterhubs -A
kubectl --context mgmt2 get multiclusterhubs -A

# Confirm managed clusters are connected to primary
kubectl --context mgmt1 get managedclusters

# Confirm backup/restore state
kubectl --context mgmt1 get backupschedule -n open-cluster-management-backup
kubectl --context mgmt2 get restore -n open-cluster-management-backup

# Confirm OADP BSL is available on both
kubectl --context mgmt1 get backupstoragelocations -n open-cluster-management-backup
kubectl --context mgmt2 get backupstoragelocations -n open-cluster-management-backup
```

### 2.2 Workstation Setup

```bash
# 1. Install ansible-core (collection requires >= 2.15)
pip install ansible-core==2.15.*

# 2. Install Python dependencies
pip install PyYAML>=6.0 kubernetes>=24.2.0

# 3. Install collection dependencies
ansible-galaxy collection install kubernetes.core

# 4. Set ANSIBLE_COLLECTIONS_PATH to include the repo
export ANSIBLE_COLLECTIONS_PATH="/home/tomaz/sources/rh-acm-switchover:${ANSIBLE_COLLECTIONS_PATH:-~/.ansible/collections}"

# 5. Verify collection is loadable
ansible-doc tomazb.acm_switchover.acm_input_validate

# 6. Verify kubeconfig access
kubectl --context mgmt1 cluster-info
kubectl --context mgmt2 cluster-info
```

### 2.3 RBAC Verification

Before any real-cluster run, verify RBAC permissions are sufficient:

```bash
# Using the Python tool's check (works independently)
python check_rbac.py --primary-context mgmt1 --secondary-context mgmt2 --role operator --verbose

# Or via the collection's RBAC validate module directly (future step)
```

### 2.4 Kubeconfig Preparation

The collection expects kubeconfig paths in its variable model. Prepare a vars file pointing to real kubeconfigs:

```yaml
# File: e2e-vars/real-cluster-base.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config  # or a dedicated kubeconfig file
  secondary:
    context: mgmt2
    kubeconfig: ~/.kube/config

acm_switchover_features:
  skip_rbac_validation: false
  skip_observability_checks: false
  argocd:
    manage: false
    resume_after_switchover: false
```

---

## 3. Test Phases

Testing is structured in 7 phases, ordered from safest (read-only) to most destructive (real switchovers). **Stop and investigate at the first unexpected failure before proceeding to the next phase.**

### Phase 1: Syntax and Collection Integrity (Safe, No Cluster Access)

**Purpose**: Confirm the collection loads and playbooks parse correctly before touching any cluster.

| ID | Test | Command | Expected |
|----|------|---------|----------|
| S1 | Collection syntax | `ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml --syntax-check` | Exit 0 |
| S2 | Switchover syntax | `ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml --syntax-check` | Exit 0 |
| S3 | Decommission syntax | `ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/decommission.yml --syntax-check` | Exit 0 |
| S4 | Discovery syntax | `ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/discovery.yml --syntax-check` | Exit 0 |
| S5 | RBAC bootstrap syntax | `ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml --syntax-check` | Exit 0 |
| S6 | ArgoCD resume syntax | `ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml --syntax-check` | Exit 0 |
| S7 | Unit tests pass | `pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q` | All pass |
| S8 | Integration tests pass | `pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q` | All pass |
| S9 | Scenario tests pass | `pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q` | All pass |

### Phase 2: Input Validation (Safe, No Cluster Access)

**Purpose**: Verify that the `acm_input_validate` module catches bad inputs before any K8s API call.

| ID | Test | Command | Expected |
|----|------|---------|----------|
| V1 | Missing secondary context | `ansible-playbook playbooks/preflight.yml -e @e2e-vars/missing-secondary.yml -v` | Fail with clear message about missing secondary context |
| V2 | Invalid kubeconfig path | `ansible-playbook playbooks/preflight.yml -e @e2e-vars/bad-kubeconfig.yml -v` | Fail during kubeconfig reachability check |
| V3 | Invalid method | `ansible-playbook playbooks/preflight.yml -e @e2e-vars/bad-method.yml -v` | Fail with input validation error about method |

**Vars files needed**:

```yaml
# e2e-vars/missing-secondary.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
  # secondary intentionally omitted
acm_switchover_operation:
  method: passive
acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
```

```yaml
# e2e-vars/bad-kubeconfig.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: /nonexistent/path/kubeconfig
  secondary:
    context: mgmt2
    kubeconfig: /nonexistent/path/kubeconfig
acm_switchover_operation:
  method: passive
acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
```

```yaml
# e2e-vars/bad-method.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt2
    kubeconfig: ~/.kube/config
acm_switchover_operation:
  method: invalid_method
acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
```

### Phase 3: Preflight Validation Against Real Clusters (Read-Only)

**Purpose**: Run the preflight playbook in `validate` mode against real clusters. This is the first real-cluster interaction — all operations are read-only (GET/LIST calls and SelfSubjectAccessReview).

| ID | Test | Command | Expected |
|----|------|---------|----------|
| P1 | Preflight passive validate | See command below | Exit 0, preflight-report.json with `status: pass` |
| P2 | Preflight full validate | Same with `method: full` | Exit 0 (or known warnings) |
| P3 | Preflight with RBAC validation | Set `skip_rbac_validation: false` | RBAC checks pass (SelfSubjectAccessReview) |
| P4 | Preflight report artifact | Check `./artifacts/preflight-report.json` after P1 | Valid JSON, contains all expected check IDs |
| P5 | Preflight swapped contexts | Swap primary/secondary | Fails or warns about secondary having active backups |
| P6 | Parity check vs Python | Run Python `--validate-only` on same clusters, compare findings | Same checks pass/fail, same issues detected |
| P7 | GitOps check behavior | Run with `skip_gitops_check: false` then `true` | `preflight-gitops-warning` check ID present when false, absent when true |

**P1 command**:
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml \
  -e @e2e-vars/real-cluster-passive-validate.yml \
  -v
```

**Vars file**: `e2e-vars/real-cluster-passive-validate.yml`
```yaml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt2
    kubeconfig: ~/.kube/config

acm_switchover_operation:
  method: passive
  activation_method: patch

acm_switchover_features:
  skip_rbac_validation: false
  skip_observability_checks: false
  skip_gitops_check: false
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: validate
  report_dir: ./artifacts
```

**P5 command** (swapped contexts):
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml \
  -e @e2e-vars/real-cluster-swapped-validate.yml \
  -v
```

**P6 parity check**:
```bash
# Python equivalent
python acm_switchover.py \
  --primary-context mgmt1 --secondary-context mgmt2 \
  --validate-only --method passive --old-hub-action secondary

# Compare: both should produce the same pass/fail/warning findings
# Document any differences
```

**Verification checklist for P1**:
- [ ] Playbook exits 0
- [ ] `./artifacts/preflight-report.json` exists and is valid JSON
- [ ] Report contains `"status": "pass"`
- [ ] Expected check IDs present: `preflight-input-*`, `preflight-version-*`, `preflight-namespace-*`, `preflight-backup-*`
- [ ] ACM versions correctly detected from both hubs
- [ ] Managed cluster count is accurate (should show 3 for prod1-prod3)
- [ ] No unexpected warnings about backup/restore state

**P7 — GitOps check behavior**:
```bash
# P7a: With skip_gitops_check: false (default) — warning should appear
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml \
  -e @e2e-vars/real-cluster-passive-validate.yml \
  -v

# Check report for the gitops warning
python -c "import json; r=json.load(open('./artifacts/preflight-report.json')); print([c for c in r.get('checks',[]) if c['id']=='preflight-gitops-warning'])"

# P7b: With skip_gitops_check: true — warning should be absent
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml \
  -e @e2e-vars/real-cluster-passive-validate.yml \
  -e '{"acm_switchover_features": {"skip_gitops_check": true}}' \
  -v

# Verify absence
python -c "import json; r=json.load(open('./artifacts/preflight-report.json')); assert not [c for c in r.get('checks',[]) if c['id']=='preflight-gitops-warning'], 'GitOps warning should be absent'"
```

**P7 verification**:
- [ ] P7a: `preflight-gitops-warning` check ID present in report with `status: pass` and severity `warning`
- [ ] P7b: `preflight-gitops-warning` check ID absent from report when `skip_gitops_check: true`

### Phase 4: Dry-Run Switchover Against Real Clusters (Read-Only)

**Purpose**: Run the full switchover playbook in `dry_run` mode. This exercises the full phase sequence (preflight → primary_prep → activation → post_activation → finalization) but skips all mutating operations. It verifies the logic flow, resource discovery, and decision-making without changing cluster state.

| ID | Test | Command | Expected |
|----|------|---------|----------|
| D1 | Dry-run passive switchover | See command below | Exit 0, report shows all phases pass with dry-run annotations |
| D2 | Dry-run full switchover | `method: full` | Exit 0 |
| D3 | Dry-run with activation-method restore | `activation_method: restore` | Exit 0, shows delete+create plan |
| D4 | Dry-run report artifact | Check `./artifacts/switchover-report.json` | Valid JSON with all 4 phase results |
| D5 | Dry-run parity check | Compare with Python `--dry-run` output | Same resource discovery, same decisions |
| D6 | Ansible `--check` mode | D1 command with `--check` appended | Exit 0, no mutations, flags tasks that lack check-mode support |
| D7 | High-verbosity dry-run | D1 command with `-vvv` instead of `-v` | Exit 0, no secrets/tokens in output, manageable output size |

**D1 command**:
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
  -e @e2e-vars/real-cluster-passive-dryrun.yml \
  -v
```

**Vars file**: `e2e-vars/real-cluster-passive-dryrun.yml`
```yaml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt2
    kubeconfig: ~/.kube/config

acm_switchover_operation:
  method: passive
  activation_method: patch
  old_hub_action: secondary

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_rbac_validation: true
  skip_observability_checks: false
  skip_gitops_check: false
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: dry_run
  report_dir: ./artifacts
```

**Verification checklist for D1**:
- [ ] Playbook exits 0
- [ ] `./artifacts/switchover-report.json` exists
- [ ] Report `schema_version` is `"1.0"`
- [ ] All 4 phases present: `primary_prep`, `activation`, `post_activation`, `finalization`
- [ ] All phase statuses are `"pass"`
- [ ] No actual resources were modified (verify BackupSchedule still active, restore still running)
- [ ] `ansible-playbook` output shows dry-run skip messages where expected

**D6 command** (Ansible native `--check` mode — tests a different code path than `mode: dry_run`):
```bash
# Ansible's --check validates task idempotency at the Ansible layer,
# while mode: dry_run is an application-level skip.
# This catches tasks that don't properly support check mode.
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
  -e @e2e-vars/real-cluster-passive-dryrun.yml \
  -v --check
```

**D6 verification checklist**:
- [ ] Playbook exits 0
- [ ] No actual resources were modified
- [ ] Note any tasks that report `skipping` or `unsupported` in check mode — these are candidates for future `check_mode` support

**D7 command** (high verbosity — catches debug-level logging issues):
```bash
# Tests with -vvv to catch: oversized outputs, secrets/tokens leaked
# in debug output, and formatting problems not visible at -v
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
  -e @e2e-vars/real-cluster-passive-dryrun.yml \
  -vvv 2>&1 | tee ./artifacts/d7-verbose-output.log
```

**D7 verification checklist**:
- [ ] Playbook exits 0
- [ ] Output does not contain kubeconfig tokens, bearer tokens, or passwords
- [ ] Output size is manageable (< 5000 lines for a dry-run — check with `wc -l ./artifacts/d7-verbose-output.log`)
- [ ] No Python tracebacks or encoding errors in verbose output

**Post-dry-run cluster state verification**:
```bash
# Confirm nothing changed
kubectl --context mgmt1 get backupschedule -n open-cluster-management-backup -o yaml | grep paused
kubectl --context mgmt2 get restore -n open-cluster-management-backup
kubectl --context mgmt1 get managedclusters
```

### Phase 5: Non-Core Playbooks Against Real Clusters (Mostly Read-Only)

**Purpose**: Test the discovery, RBAC bootstrap (dry-run), and decommission (dry-run) playbooks against real clusters.

| ID | Test | Command | Expected |
|----|------|---------|----------|
| N1 | Discovery playbook | See below | Hub role correctly classified |
| N1b | Discovery with wrong facts | See below | Returns incorrect classification (proves classifier trusts inputs) |
| N2 | RBAC bootstrap dry-run | See below | Exit 0, shows planned manifests |
| N3 | Decommission dry-run | See below | Exit 0, shows planned deletions |
| N4 | RBAC bootstrap real apply | See below | RBAC manifests applied, permissions validated |

> **Discovery Architecture Note**: The discovery role is **intentionally a pure classifier** — it makes zero Kubernetes API calls by design. It requires pre-populated facts (`acm_switchover_discovery_restore_state` and `acm_switchover_discovery_managed_clusters`) gathered by the caller. The intended fact-gathering bridge is `scripts/discover-hub.sh --auto --run`. This is a deliberate architectural decision to keep the role side-effect-free and testable, not a gap.

**N1 — Discovery**:
```bash
# Note: Discovery role expects pre-populated facts. For real clusters,
# first gather the facts manually, then feed them:
RESTORE_STATE=$(kubectl --context mgmt2 get restore -n open-cluster-management-backup -o jsonpath='{.items[0].spec.syncRestoreWithNewBackups}' 2>/dev/null && echo "passive-sync" || echo "none")
MC_COUNT=$(kubectl --context mgmt1 get managedclusters --no-headers 2>/dev/null | wc -l)

ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/discovery.yml \
  -e "acm_switchover_discovery_restore_state=${RESTORE_STATE}" \
  -e "acm_switchover_discovery_managed_clusters=${MC_COUNT}" \
  -e "summary_path=./artifacts/discovery-summary.json" \
  -v
```

**N1 verification**:
- [ ] Playbook exits 0
- [ ] `./artifacts/discovery-summary.json` contains a `role` field matching the expected hub classification
- [ ] Classification matches what you would expect given the real cluster state

**N1b — Discovery with deliberately wrong facts** (negative test):
```bash
# Feed incorrect facts: claim 0 managed clusters when we know there are 3.
# The classifier should return an incorrect classification, proving it trusts
# its inputs and does not silently compensate or make API calls.
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/discovery.yml \
  -e "acm_switchover_discovery_restore_state=none" \
  -e "acm_switchover_discovery_managed_clusters=0" \
  -e "summary_path=./artifacts/discovery-n1b-summary.json" \
  -v
```

**N1b verification**:
- [ ] Playbook exits 0
- [ ] Classification is `standby` (or equivalent non-primary/non-secondary) — NOT the actual hub role
- [ ] This confirms the role is a pure classifier that does not reach out to the cluster

**N2 — RBAC bootstrap dry-run**:
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml \
  -e @e2e-vars/rbac-bootstrap-dryrun.yml \
  -e "summary_path=./artifacts/rbac-bootstrap-summary.json" \
  -v
```

```yaml
# e2e-vars/rbac-bootstrap-dryrun.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
acm_switchover_rbac_bootstrap:
  role: operator
  include_decommission: false
  generate_kubeconfigs: false
  validate_permissions: false
acm_switchover_execution:
  mode: dry_run
```

**N3 — Decommission dry-run**:
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/decommission.yml \
  -e @e2e-vars/decommission-dryrun.yml \
  -e "summary_path=./artifacts/decommission-summary.json" \
  -v
```

```yaml
# e2e-vars/decommission-dryrun.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
acm_switchover_decommission:
  confirmed: false
  interactive: false
  has_observability: true
acm_switchover_execution:
  mode: dry_run
```

**N4 — RBAC bootstrap real** (only after dry-run success):
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml \
  -e @e2e-vars/rbac-bootstrap-real.yml \
  -e "summary_path=./artifacts/rbac-bootstrap-summary.json" \
  -v
```

```yaml
# e2e-vars/rbac-bootstrap-real.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
acm_switchover_rbac_bootstrap:
  role: operator
  include_decommission: false
  generate_kubeconfigs: false
  validate_permissions: true
acm_switchover_execution:
  mode: execute
```

### Phase 6: ArgoCD Management Against Real Clusters (Mutating, Reversible)

**Purpose**: Test ArgoCD pause/resume operations. These are mutating but easily reversible (resume restores auto-sync).

**Prerequisite**: ArgoCD installed on the primary hub with Applications that touch ACM namespaces.

**Skip Conditions**: If ArgoCD is not installed on the test cluster, skip Phase 6 entirely AND skip R3/R3r in Phase 7. Detect with:
```bash
kubectl --context mgmt1 get crd applications.argoproj.io 2>/dev/null
# If this returns "not found" or fails → skip Phase 6 and R3/R3r
# Record skipped tests as "SKIP (ArgoCD not installed)" in results, not FAIL
```

> **Important**: The `argocd_manage` role discovers which hub to query based on the `_argocd_discover_hub` parameter (`primary` for pause, `secondary` for resume). This means `secondary` must always be defined in the vars file even for A1 (standalone pause+resume on the same hub). The `acm_switchover_argocd.namespace` key is also required — the role does not fall back to a default namespace.
>
> **Variable precedence pitfall (Bug 18)**: Ansible `include_role vars:` is at precedence level 8, which is always overridden by user-supplied `-e` extra_vars at level 16. The role therefore uses `acm_switchover_argocd_mode_override` (a distinct variable name the user does not set) to pass `pause`/`resume` mode internally. Do not attempt to pass the mode via `acm_switchover_argocd.mode` in a `-e` flag — it will be silently dropped if the user also passes any `acm_switchover_argocd: {...}` dict.

| ID | Test | Command | Expected |
|----|------|---------|----------|
| A1 | ArgoCD test playbook | See below | Apps paused and resumed |
| A2 | ArgoCD resume-only | See below | Resume works standalone |

**A1 — ArgoCD test playbook** (pause + resume):
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/argocd_manage_test.yml \
  -e @e2e-vars/argocd-test.yml \
  -e "summary_path=./artifacts/argocd-summary.json" \
  -v
```

```yaml
# e2e-vars/argocd-test.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt1        # same hub for standalone test — pause and resume on the same cluster
    kubeconfig: ~/.kube/config
acm_switchover_features:
  argocd:
    manage: true
    resume_after_switchover: true
acm_switchover_argocd:
  namespace: openshift-gitops   # required: set to your ArgoCD namespace
acm_switchover_execution:
  mode: execute
  run_id: "e2e-argocd-test-001"
```

**A2 — ArgoCD resume-only**:
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml \
  -e @e2e-vars/argocd-resume.yml \
  -v
```

```yaml
# e2e-vars/argocd-resume.yml
# secondary = the hub where apps were paused.
# After R3 (mgmt1→mgmt2), the old primary mgmt1 is now secondary and holds the paused apps.
# Adjust primary/secondary to match the current topology after whichever R3 run you reversed.
acm_switchover_hubs:
  primary:
    context: mgmt2
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt1        # hub where ArgoCD apps have paused-by annotations
    kubeconfig: ~/.kube/config
acm_switchover_argocd:
  namespace: openshift-gitops   # required: set to your ArgoCD namespace
acm_switchover_execution:
  mode: execute
  run_id: "e2e-argocd-test-001"
```

> **Note**: `argocd_resume.yml` always targets the **secondary** hub (where apps were paused during primary_prep). After a switchover from mgmt1→mgmt2, the secondary is mgmt1. Swapping `primary`/`secondary` here would resume from the wrong hub and find zero apps to restore.

**Verification**:
```bash
# After A1, check that all ArgoCD apps have auto-sync restored
kubectl --context mgmt1 get applications -A -o custom-columns=NAME:.metadata.name,SYNC_POLICY:.spec.syncPolicy.automated
```

### Phase 7: Real Switchovers (Destructive, Careful)

**Purpose**: Execute actual switchovers. This is the critical test — real state changes on real clusters. Follow the same pattern as the Python E2E test plan (R-series tests).

**CRITICAL SAFETY RULES**:
1. Always verify cluster state before and after each switchover
2. Always do a forward + reverse pair (switchover then switch back)
3. Have the Python tool available as a known-good fallback for rollback
4. Start with the simplest scenario (passive, no ArgoCD, no observability deletion)
5. Wait for all managed clusters to reconnect before proceeding to the next test

#### Pre-Switchover State Capture

```bash
# Capture baseline state for comparison
mkdir -p ./artifacts/baseline

# Primary hub state
kubectl --context mgmt1 get managedclusters -o json > ./artifacts/baseline/mgmt1-mc.json
kubectl --context mgmt1 get backupschedule -n open-cluster-management-backup -o json > ./artifacts/baseline/mgmt1-bs.json
kubectl --context mgmt1 get multiclusterhub -A -o json > ./artifacts/baseline/mgmt1-mch.json

# Secondary hub state
kubectl --context mgmt2 get restore -n open-cluster-management-backup -o json > ./artifacts/baseline/mgmt2-restore.json
kubectl --context mgmt2 get backupschedule -n open-cluster-management-backup -o json > ./artifacts/baseline/mgmt2-bs.json
```

#### Test Matrix

| ID | Test | Direction | Method | ArgoCD | Observability | Expected Duration |
|----|------|-----------|--------|--------|--------------|-------------------|
| R1 | Basic passive | mgmt1→mgmt2 | passive/patch | off | skip | ~1–5 min |
| R1r | Reverse R1 | mgmt2→mgmt1 | passive/patch | off | skip | ~1–5 min |
| R2 | Passive + observability | mgmt1→mgmt2 | passive/patch | off | enabled | ~5–11 min |
| R2r | Reverse R2 | mgmt2→mgmt1 | passive/patch | off | enabled | ~5–11 min |
| R2b | Passive + disable obs on secondary | mgmt1→mgmt2 | passive/patch | off | enabled + `disable_observability_on_secondary: true` | ~5–11 min |
| R3 | Passive + ArgoCD | mgmt1→mgmt2 | passive/patch | on | skip | ~5–13 min |
| R3r | Reverse R3 | mgmt2→mgmt1 | passive/patch | off | skip | ~5 min |
| R4 | Passive restore activation | mgmt1→mgmt2 | passive/restore | off | skip | ~5–11 min |
| R4r | Reverse R4 | mgmt2→mgmt1 | passive/patch | off | skip | ~5 min |
| R5 | Full restore | mgmt1→mgmt2 | full | off | skip | ~10–15 min |
| R5r | Reverse R5 | mgmt2→mgmt1 | passive/patch | off | skip | ~5 min |
| R6 | Controlled RBAC failure | mgmt1→mgmt2 | passive/patch | off | skip | ~1–2 min |
| R6c | Verify no state change | — | — | — | — | ~1 min |

**R1 command** (simplest case first):
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
  -e @e2e-vars/real-switchover-r1.yml \
  -v 2>&1 | tee ./artifacts/r1-output.log
```

```yaml
# e2e-vars/real-switchover-r1.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt2
    kubeconfig: ~/.kube/config

acm_switchover_operation:
  method: passive
  activation_method: patch
  old_hub_action: secondary
  min_managed_clusters: 3

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_rbac_validation: true
  skip_observability_checks: true
  skip_gitops_check: false
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: execute
  report_dir: ./artifacts
  verbosity: 1
```

**R1r command** (reverse — swap contexts):
```yaml
# e2e-vars/real-switchover-r1r.yml
acm_switchover_hubs:
  primary:
    context: mgmt2
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt1
    kubeconfig: ~/.kube/config

acm_switchover_operation:
  method: passive
  activation_method: patch
  old_hub_action: secondary
  min_managed_clusters: 3

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_rbac_validation: true
  skip_observability_checks: true
  skip_gitops_check: false
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: execute
  report_dir: ./artifacts
  verbosity: 1
```

**R3 command** (with ArgoCD):
```yaml
# e2e-vars/real-switchover-r3.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt2
    kubeconfig: ~/.kube/config

acm_switchover_operation:
  method: passive
  activation_method: patch
  old_hub_action: secondary
  min_managed_clusters: 3

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_rbac_validation: true
  skip_observability_checks: true
  skip_gitops_check: false
  disable_observability_on_secondary: false
  argocd:
    manage: true
    resume_after_switchover: true

acm_switchover_execution:
  mode: execute
  report_dir: ./artifacts
  run_id: "e2e-r3-switchover"
  verbosity: 1
```

#### R6: Controlled Failure Test (Deliberate RBAC Restriction)

**Purpose**: Deliberately trigger a real API permission error mid-switchover to validate error handling, error messages, and state safety. Uses validator-role RBAC (insufficient for mutations) with `skip_rbac_validation: true` so preflight passes but the first mutating operation (BackupSchedule pause in `primary_prep`) fails.

**Setup**: Ensure the kubeconfig/service account used for R6 has only **validator** (read-only) permissions. If your normal kubeconfig has full admin access, create a temporary restricted kubeconfig:
```bash
# Option A: Use the RBAC bootstrap to create a validator-only SA
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml \
  -e @e2e-vars/rbac-bootstrap-validator.yml -v

# Option B: Use the Python tool's setup
python check_rbac.py --primary-context mgmt1 --secondary-context mgmt2 --role validator --verbose
```

**R6 command**:
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
  -e @e2e-vars/real-switchover-r6.yml \
  -v 2>&1 | tee ./artifacts/r6-output.log
```

```yaml
# e2e-vars/real-switchover-r6.yml
acm_switchover_hubs:
  primary:
    context: mgmt1
    kubeconfig: ~/.kube/r6-validator-kubeconfig  # validator-only permissions
  secondary:
    context: mgmt2
    kubeconfig: ~/.kube/r6-validator-kubeconfig

acm_switchover_operation:
  method: passive
  activation_method: patch
  old_hub_action: secondary
  min_managed_clusters: 3

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_rbac_validation: true  # bypass preflight RBAC check so we reach the mutation
  skip_observability_checks: true
  skip_gitops_check: false
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: execute
  report_dir: ./artifacts
  verbosity: 1
```

**R6 verification checklist**:
- [ ] Playbook exits non-zero (expected failure)
- [ ] Error message is actionable — mentions permission denied / forbidden, not a generic traceback
- [ ] Failure occurs during `primary_prep` phase (first mutating phase)
- [ ] `./artifacts/switchover-report.json` exists and shows `primary_prep` as failed
- [ ] No partial state corruption — proceed to R6c

**R6c — Verify no state change after controlled failure**:
```bash
# All of these should match the pre-test baseline:
kubectl --context mgmt1 get backupschedule -n open-cluster-management-backup -o yaml | grep paused
# Expected: paused should be false/absent (unchanged)

kubectl --context mgmt2 get restore -n open-cluster-management-backup
# Expected: passive-sync restore still running (unchanged)

kubectl --context mgmt1 get managedclusters
# Expected: all 3 clusters still connected (unchanged)
```

- [ ] BackupSchedule not paused (unchanged from baseline)
- [ ] Restore still running on secondary (unchanged)
- [ ] All managed clusters still connected to primary (unchanged)

#### Post-Switchover Verification Checklist

Run after **each** switchover (both forward and reverse):

```bash
# 1. Check switchover report
cat ./artifacts/switchover-report.json | python -m json.tool

# 2. Verify managed clusters on new primary
kubectl --context <NEW_PRIMARY> get managedclusters
# Expected: All 3 (prod1, prod2, prod3) show Joined=True, Available=True

# 3. Verify backup schedule on new primary
kubectl --context <NEW_PRIMARY> get backupschedule -n open-cluster-management-backup
# Expected: BackupSchedule exists and is active (not paused)

# 4. Verify passive-sync restore on new secondary
kubectl --context <NEW_SECONDARY> get restore -n open-cluster-management-backup
# Expected: Passive-sync restore exists and is Enabled

# 5. Verify MultiClusterHub health on both
kubectl --context <NEW_PRIMARY> get multiclusterhub -A
kubectl --context <NEW_SECONDARY> get multiclusterhub -A

# 6. Verify klusterlet agents (spot-check one managed cluster)
kubectl --context <NEW_PRIMARY> get managedcluster prod1 -o jsonpath='{.status.conditions}' | python -m json.tool
```

### Phase 8: Checkpoint Resume Testing (Controlled Interruption)

**Purpose**: Test the checkpoint/resume functionality to verify that already-completed phases are skipped on re-run and that `reset: true` forces a fresh start.

| ID | Test | Steps | Expected |
|----|------|-------|----------|
| C1 | Skip completed phases on resume | 1. Create partial checkpoint (preflight done)<br>2. Run with `reset: false` | preflight skipped, primary_prep runs |
| C2 | Fresh run with reset | Same checkpoint file, run with `reset: true` | Checkpoint cleared, preflight re-runs from scratch |

> **Recommended approach — dry_run simulation (safe, no cluster state change)**:
> Creating a partial checkpoint file directly and running in `dry_run` mode is safer and fully repeatable compared to Ctrl+C-ing a live switchover. The checkpoint mechanism is purely file-based and works identically in both modes.

**Step 1 — Create the partial checkpoint** (simulates a run that completed preflight but was interrupted before primary_prep):
```bash
python3 -c "
import json
from datetime import timezone, datetime
checkpoint = {
    'schema_version': '1.0',
    'phase': 'primary_prep',
    'completed_phases': ['preflight'],
    'operational_data': {},
    'errors': [],
    'report_refs': [],
    'created_at': datetime.now(timezone.utc).isoformat(),
    'updated_at': datetime.now(timezone.utc).isoformat()
}
with open('./artifacts/checkpoint.json', 'w') as f:
    json.dump(checkpoint, f, indent=2)
print('Partial checkpoint created.')
"
```

**Step 2 — C1: Run with `reset: false`** (preflight should be skipped):
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
  -e @e2e-vars/real-switchover-checkpoint.yml \
  -v 2>&1 | grep -E "(Enter checkpointed|skipping:|skipped_phase)" | head -20
```

**C1 verification**:
- `preflight : Enter checkpointed phase` → `"skipped_phase": true`
- All preflight sub-tasks show `skipping: [localhost]`
- `primary_prep : Enter checkpointed phase` → `"skipped_phase": false`
- primary_prep tasks show `ok:` or dry-run skip messages (not skipping due to checkpoint)

**Step 3 — C2: Run with `reset: true`** (checkpoint wiped, preflight re-runs):
```bash
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
  -e @e2e-vars/real-switchover-checkpoint.yml \
  -e '{"acm_switchover_execution": {"mode": "dry_run", "checkpoint": {"enabled": true, "path": "./artifacts/checkpoint.json", "reset": true}}}' \
  -v 2>&1 | grep -E "(Enter checkpointed|skipping:|skipped_phase)" | head -10
```

**C2 verification**:
- `preflight : Enter checkpointed phase` → `"skipped_phase": false` and `"completed_phases": []`
- preflight sub-tasks show `ok:` (not skipping — they run fresh)

**Vars file** (`e2e-vars/real-switchover-checkpoint.yml`):
```yaml
# e2e-vars/real-switchover-checkpoint.yml
acm_switchover_hubs:
  primary:
    context: mgmt2
    kubeconfig: ~/.kube/config
  secondary:
    context: mgmt1
    kubeconfig: ~/.kube/config

acm_switchover_operation:
  method: passive
  activation_method: patch
  old_hub_action: secondary
  min_managed_clusters: 3

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_rbac_validation: true
  skip_observability_checks: true
  skip_gitops_check: true          # avoids ArgoCD CRD queries in dry_run
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: dry_run                    # no cluster state changes
  report_dir: ./artifacts
  checkpoint:
    enabled: true
    path: ./artifacts/checkpoint.json
    reset: false
```

> **Alternative — live interruption test** (validates real Ctrl+C durability):
> If you want to test checkpoint integrity under actual interruption, run a real switchover with checkpoints and Ctrl+C during activation. Then inspect the checkpoint file and re-run. This requires a fresh pair of hubs in the correct pre-switchover state and must be followed by a reverse switchover.

---

## 4. Parity Comparison Strategy

For each real-cluster test in Phases 3, 4, and 7, run the equivalent Python command and compare:

| Aspect | How to Compare |
|--------|---------------|
| Preflight findings | Compare preflight-report.json fields with Python `--validate-only` output |
| Dry-run decisions | Compare switchover-report.json with Python `--dry-run` output |
| Switchover outcome | Compare final cluster state (managed clusters, backups, restores) after both tools |
| Error handling | Feed same invalid inputs to both; compare error messages |
| Timing | Document wall-clock time for each phase in both tools |

**Python equivalent commands** (for reference):
```bash
# Validate-only (matches Phase 3)
python acm_switchover.py --primary-context mgmt1 --secondary-context mgmt2 \
  --validate-only --method passive --old-hub-action secondary

# Dry-run (matches Phase 4)
python acm_switchover.py --primary-context mgmt1 --secondary-context mgmt2 \
  --dry-run --method passive --old-hub-action secondary --reset-state

# Real switchover (matches Phase 7 R1)
python acm_switchover.py --primary-context mgmt1 --secondary-context mgmt2 \
  --method passive --old-hub-action secondary --min-managed-clusters 3 \
  --skip-observability-checks --reset-state
```

---

## 5. Known Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Collection modules may make incorrect API calls not caught by fixture tests | Phase 3 (validate) and Phase 4 (dry-run) catch these before any mutations |
| BackupSchedule pause may not work with real ACM versions | Phase 4 dry-run will reveal the issue; Phase 7 R1 is the first mutation |
| Managed clusters may not reconnect after switchover | Always do forward+reverse pairs; Python tool available as fallback |
| Checkpoint file corruption on interrupt | Test C1 specifically validates this; checkpoint is advisory, not blocking |
| ArgoCD apps left in paused state | Phase 6 A1 tests pause+resume as a pair; A2 provides standalone resume |
| Ansible `include_role vars:` precedence (Bug 18) | `include_role vars:` is level 8 — user `-e` extra_vars (level 16) always wins. The role uses `acm_switchover_argocd_mode_override` internally so users can't accidentally override it. Do NOT pass `acm_switchover_argocd.mode` via `-e`; pass the whole dict and the internal mode key gets wiped. |
| RBAC permissions insufficient for collection modules | Phase 3 P3 tests RBAC validation against real cluster |
| Collection modules may not handle real K8s error responses | Phases 3-4 exercise error paths with real API responses |
| Real cluster state doesn't match fixture assumptions | This is exactly what we're testing — differences reveal fixture gaps |

### 5.1 Per-Phase Timeout and Bail-Out Guidelines

These are **wall-clock guidelines** for the operator. If a phase exceeds its max wait, the bail-out action describes how to safely stop and assess. Note that `kubernetes.core` module-level timeouts govern individual K8s API operations (typically 60s), not overall phase duration.

| Phase | Max Wait | Bail-Out Action |
|-------|----------|-----------------|
| Preflight (P*) | 5 min | Ctrl+C safe — all operations are read-only |
| Dry-run (D*) | 10 min | Ctrl+C safe — all operations are read-only |
| Primary Prep | 5 min | Ctrl+C, then unpause BackupSchedule manually (see Section 6) |
| Activation | 10 min | Ctrl+C, then check restore CR status (`kubectl get restore -n open-cluster-management-backup -o yaml`) |
| Post Activation (cluster reconnect) | 15 min | Clusters may genuinely need more time. If >15 min, investigate klusterlet on one managed cluster before bailing out |
| Finalization | 5 min | Ctrl+C, then verify backup schedule state and passive-sync restore manually |
| ArgoCD (A*) | 5 min | Ctrl+C, then resume manually via A2 command (see Phase 6) |
| RBAC bootstrap (N4) | 3 min | Ctrl+C safe — partial RBAC apply is idempotent on re-run |
| Checkpoint resume (C*) | Same as underlying phase | Follow the bail-out for the phase that's currently executing |

**If `ansible-playbook` appears hung** (no output for >2 minutes):
1. Check if a `kubernetes.core` module is waiting on a K8s API timeout
2. Try pressing Enter — some terminal configurations buffer output
3. Ctrl+C and re-run with `-vvv` to see where it stalled

---

## 6. Rollback Procedures

### If a switchover fails mid-flight

1. **Check the switchover report**: `cat ./artifacts/switchover-report.json`
2. **Identify which phase failed**: Look at the `phases` object
3. **If failed during primary_prep**: No critical state change. Unpause BackupSchedule manually:
   ```bash
   kubectl --context mgmt1 patch backupschedule <name> -n open-cluster-management-backup \
     --type merge -p '{"spec":{"paused":false}}'
   ```
4. **If failed during activation**: Restore may be in an intermediate state. Check restore CR:
   ```bash
   kubectl --context mgmt2 get restore -n open-cluster-management-backup -o yaml
   ```
5. **If failed during post_activation**: Switchover likely completed but verification failed. Check managed clusters manually.
6. **If all else fails**: Use the Python tool's known-good rollback:
   ```bash
   python acm_switchover.py --primary-context mgmt2 --secondary-context mgmt1 \
     --method passive --old-hub-action secondary --reset-state
   ```

### If ArgoCD apps are left paused

```bash
# Standalone resume
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml \
  -e '{"acm_switchover_hubs": {"primary": {"context": "mgmt1", "kubeconfig": "~/.kube/config"}}, "acm_switchover_execution": {"run_id": "<ORIGINAL_RUN_ID>"}}'

# Or manually
kubectl --context mgmt1 get applications -A -o json | \
  jq -r '.items[] | select(.metadata.annotations["acm-switchover/pre-pause-sync"] != null) | .metadata.name'
```

---

## 7. Results Recording Template

Use this template to document each test result:

```markdown
### Test [ID]: [Name]
- **Date**: YYYY-MM-DD HH:MM
- **Command**: (exact command run)
- **Exit code**: 0/non-zero
- **Duration**: Xm Ys
- **Result**: PASS / FAIL / SKIP (reason)
- **Artifacts**: (list of files produced)
- **Notes**: (observations, warnings, differences from expected)
- **Parity vs Python**: (match / difference — describe)
- **Cluster state verified**: YES / NO

> Use `SKIP (ArgoCD not installed)` for Phase 6 and R3/R3r if ArgoCD CRD is absent.
> Use `SKIP (prerequisite failed)` if a dependency phase failed and later phases were not attempted.
```

---

## 8. Execution Order Summary

| Order | Phase | Tests | Risk Level | Cluster Impact |
|-------|-------|-------|-----------|----------------|
| 1 | Phase 1: Syntax & integrity | S1–S9 | None | None |
| 2 | Phase 2: Input validation | V1–V3 | None | None |
| 3 | Phase 3: Preflight validate | P1–P7 | None | Read-only |
| 4 | Phase 4: Dry-run switchover | D1–D7 | None | Read-only |
| 5 | Phase 5: Non-core playbooks | N1–N1b, N2–N4 | Low | N4 creates RBAC resources |
| 6 | Phase 6: ArgoCD management | A1–A2 | Low | Temporarily pauses auto-sync |
| 7 | Phase 7: Real switchovers | R1–R5r, R2b, R6, R6c | **High** | Full switchover, reversible in pairs |
| 8 | Phase 8: Checkpoint resume | C1–C2 | **High** | Interrupted switchover + resume |

**Total test count**: 9 + 3 + 7 + 7 + 5 + 2 + 13 + 2 = **48 tests**

**Estimated time** (excluding cluster wait times): ~5–7 hours for a complete run

### 8.1 Multi-Session Execution Guidance

The full test plan does not need to be completed in a single session. Here are the rules for splitting across sessions:

| Phases | Session Rules |
|--------|---------------|
| Phases 1–4 (S*, V*, P*, D*) | Safe to run anytime. Read-only, no state dependency between sessions. |
| Phases 5–6 (N*, A*) | Independent of each other. Can be run in any session. |
| **Phase 7 (R*)** | **Forward+reverse pairs MUST complete within the same session.** Do not leave a cluster in switched-over state overnight. |
| Phase 8 (C*) | Can follow any Phase 7 forward run within the same session. |

**Between sessions** (if Phase 7 was previously run):
1. Re-run the "Pre-Switchover State Capture" commands to verify cluster baseline
2. Confirm managed clusters are connected to the expected primary hub
3. Confirm BackupSchedule is active and restore is running on the expected secondary

**If cluster state is unknown** (e.g., after an interrupted session):
```bash
# Assess cluster state with the Python tool before continuing
python acm_switchover.py --primary-context mgmt1 --secondary-context mgmt2 \
  --validate-only --method passive --old-hub-action secondary

# Or use the discovery playbook (need to gather facts first)
RESTORE_STATE=$(kubectl --context mgmt2 get restore -n open-cluster-management-backup \
  -o jsonpath='{.items[0].spec.syncRestoreWithNewBackups}' 2>/dev/null && echo "passive-sync" || echo "none")
MC_COUNT=$(kubectl --context mgmt1 get managedclusters --no-headers 2>/dev/null | wc -l)
echo "Restore state: ${RESTORE_STATE}, Managed clusters on mgmt1: ${MC_COUNT}"
```

---

## 9. Success Criteria

| Criterion | Threshold |
|-----------|-----------|
| All syntax and offline tests pass | 100% (S1–S9, V1–V3) |
| Preflight validation matches Python findings | All critical checks produce same result |
| Dry-run exercises all phases without error | 100% (D1–D7) |
| At least one forward+reverse switchover succeeds | R1 + R1r both pass |
| All managed clusters reconnect after switchover | 100% of prod1–prod3 |
| Checkpoint resume works after interruption | C1 passes |
| Switchover report artifact is valid and complete | All 4 core phases present |
| No data loss or orphaned state | Verified after each switchover pair |
| Controlled failure produces clean error, no state corruption | R6 + R6c both pass |

---

## 10. Follow-Up Work (Out of Scope for This Plan)

After the manual test plan is executed:

1. **Automated E2E framework** — Build a pytest-based E2E runner for the collection (analogous to `tests/e2e/` for Python)
2. **AAP smoke testing** — Test execution through Ansible Automation Platform job templates
3. **Soak testing** — Multi-cycle back-and-forth switchovers
4. **Failure injection** — Pause-backup, delay-restore, kill-pod scenarios during collection execution
5. **Performance benchmarking** — Compare collection vs Python tool timings
6. **CI integration** — Add real-cluster E2E as a gated workflow (on-demand)
