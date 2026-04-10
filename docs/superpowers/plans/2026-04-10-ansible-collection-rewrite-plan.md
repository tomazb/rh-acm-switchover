# Ansible Collection Rewrite Phase 0 and Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a clean Phase 0 and Phase 1 foundation for the ACM switchover Ansible Collection that matches the approved design without leaking later-phase runtime implementation into the foundation work.

**Architecture:** Phase 0 creates the migration control documents: inventory, behavior map, test migration catalog, parity matrix, and shared scenario catalog. Phase 1 then creates only the collection skeleton, operator-facing playbook and role stubs, documentation skeleton, examples, and CI baseline required to support both `ansible-core` CLI and AAP from the start. Checkpoint runtime code, resume behavior, and custom plugin implementation remain explicitly out of scope for this plan.

**Tech Stack:** Ansible Collection structure, `ansible-core >= 2.15`, `kubernetes.core >= 3.0.0`, GitHub Actions, `pytest`, `ansible-test`, `ansible-builder`

---

## Scope Guardrails

This plan is intentionally limited to Phases 0 and 1 from the approved design in [2026-04-10-ansible-collection-rewrite-design.md](/home/tomaz/sources/rh-acm-switchover/docs/superpowers/specs/2026-04-10-ansible-collection-rewrite-design.md).

Allowed in this plan:

- migration control documents
- collection directory skeleton
- metadata files
- playbook and role stubs
- example inventory and variable files
- architecture, compatibility, and packaging docs
- CI and validation commands for the skeleton

Not allowed in this plan:

- checkpoint backend implementation
- action plugin implementation
- custom module implementation
- `module_utils` runtime code
- resume logic
- Argo CD runtime behavior
- decommission implementation
- discovery rewrite
- RBAC bootstrap implementation

If a task starts creating runtime checkpoint or plugin behavior, it belongs in a later plan and must be removed from this file.

## File Structure

### Phase 0 Deliverables

```text
docs/ansible-collection/
  feature-inventory.md
  behavior-map.md
  test-migration-catalog.md
  parity-matrix.md
  scenario-catalog.md
```

### Phase 1 Deliverables

```text
ansible_collections/tomazb/acm_switchover/
  galaxy.yml
  meta/runtime.yml
  README.md
  requirements.yml
  execution-environment.yml
  requirements.txt
  bindep.txt
  playbooks/
    preflight.yml
    switchover.yml
  roles/
    preflight/
      defaults/main.yml
      meta/main.yml
      tasks/main.yml
    primary_prep/
      defaults/main.yml
      meta/main.yml
      tasks/main.yml
    activation/
      defaults/main.yml
      meta/main.yml
      tasks/main.yml
    post_activation/
      defaults/main.yml
      meta/main.yml
      tasks/main.yml
    finalization/
      defaults/main.yml
      meta/main.yml
      tasks/main.yml
  plugins/
    modules/
    action/
    module_utils/
    filter/
    callback/
  docs/
    variable-reference.md
    cli-migration-map.md
    architecture.md
    artifact-schema.md
    coexistence.md
    distribution.md
  examples/
    inventory.yml
    group_vars/
      all.yml
  tests/
    unit/
      test_collection_metadata.py
.github/
  workflows/
    ansible-collection-foundation.yml
```

Deferred but documented only:

- `playbooks/decommission.yml`
- `playbooks/argocd_resume.yml`
- `roles/decommission/`
- `roles/argocd_manage/`
- `roles/discovery/`
- `roles/rbac_bootstrap/`

## Environment Setup

```bash
cd /home/tomaz/sources/rh-acm-switchover

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install ansible-core==2.15.* ansible-builder pytest
ansible-galaxy collection install -r ansible_collections/tomazb/acm_switchover/requirements.yml -p ~/.ansible/collections
```

If the collection directory does not exist yet, create it before running the `ansible-galaxy` command.

---

## Phase 0: Discovery and Migration Control Documents

### Task 1: Feature Inventory

**Files:**
- Create: `docs/ansible-collection/feature-inventory.md`

- [ ] **Step 1: Create the feature inventory document**

Create `docs/ansible-collection/feature-inventory.md` with the following structure and content:

```markdown
# ACM Switchover Feature Inventory

Date: 2026-04-10
Source: `acm_switchover.py`, `lib/validation.py`, `scripts/`

## Purpose

This document records the current operator-facing behavior that the collection migration must account for.

## Top-Level Variable Namespaces

- `acm_switchover_hubs`
- `acm_switchover_operation`
- `acm_switchover_features`
- `acm_switchover_execution`
- `acm_switchover_rbac`

## Core Switchover and Validation Inputs

| Current CLI Flag | Collection Variable | Phase 1 Foundation | Notes |
| --- | --- | --- | --- |
| `--primary-context` | `acm_switchover_hubs.primary.context` | yes | Required in supported flows |
| `--secondary-context` | `acm_switchover_hubs.secondary.context` | yes | Required in core switchover flows |
| `--method` | `acm_switchover_operation.method` | yes | `passive` or `full` |
| `--old-hub-action` | `acm_switchover_operation.old_hub_action` | yes | `secondary`, `decommission`, or `none` |
| `--activation-method` | `acm_switchover_operation.activation_method` | yes | `restore` only valid with passive |
| `--min-managed-clusters` | `acm_switchover_operation.min_managed_clusters` | yes | Threshold contract only in Phase 1 |
| `--validate-only` | `acm_switchover_execution.mode=validate` | yes | Playbook contract only |
| `--dry-run` | `acm_switchover_execution.mode=dry_run` | yes | Contract only; no runtime implementation here |
| `--verbose` | `acm_switchover_execution.verbose` | yes | Output contract only |
| `--force` | `acm_switchover_execution.force` | yes | Checkpoint semantics deferred |
| `--state-file` | `acm_switchover_execution.checkpoint.path` | yes | Schema and docs only |
| `--reset-state` | `acm_switchover_execution.checkpoint.reset` | yes | Schema and docs only |

## Deferred Inputs

| Current CLI Flag | Collection Variable | Deferred Phase | Notes |
| --- | --- | --- | --- |
| `--decommission` | `acm_switchover_execution.mode=decommission` | 6 | Separate playbook later |
| `--setup` | `acm_switchover_execution.mode=setup` | 6 | Becomes `rbac_bootstrap` content |
| `--argocd-manage` | `acm_switchover_features.argocd.manage` | 5 | Runtime behavior deferred |
| `--argocd-resume-after-switchover` | `acm_switchover_features.argocd.resume_after_switchover` | 5 | Runtime behavior deferred |
| `--argocd-resume-only` | `acm_switchover_execution.mode=argocd_resume` | 5 | Playbook deferred |
| `--admin-kubeconfig` | `acm_switchover_rbac.admin_kubeconfig` | 6 | RBAC bootstrap only |
| `--role` | `acm_switchover_rbac.role` | 6 | RBAC bootstrap only |
| `--token-duration` | `acm_switchover_rbac.token_duration` | 6 | RBAC bootstrap only |
| `--output-dir` | `acm_switchover_rbac.output_dir` | 6 | RBAC bootstrap only |
| `--skip-kubeconfig-generation` | `acm_switchover_rbac.skip_kubeconfig_generation` | 6 | RBAC bootstrap only |
| `--include-decommission` | `acm_switchover_rbac.include_decommission` | 6 | RBAC bootstrap only |

## Validation Rules Preserved Conceptually

- secondary context required for normal switchovers
- `activation_method=restore` requires `method=passive`
- path validation must still block traversal and shell metacharacters
- AAP survey values and `extra_vars` are untrusted inputs
- RBAC self-validation remains part of core parity even though bootstrap is deferred

## Execution Modes

Supported in Phase 1 documentation and stubs:

- `execute`
- `validate`
- `dry_run`

Deferred:

- `decommission`
- `setup`
- `argocd_resume`
```

- [ ] **Step 2: Commit**

```bash
git add docs/ansible-collection/feature-inventory.md
git commit -m "docs: add Ansible Collection feature inventory"
```

---

### Task 2: Behavior Map

**Files:**
- Create: `docs/ansible-collection/behavior-map.md`

- [ ] **Step 1: Create the behavior map document**

Create `docs/ansible-collection/behavior-map.md` with the following structure and content:

```markdown
# ACM Switchover Behavior Map

Date: 2026-04-10
Source: `lib/`, `modules/`, `scripts/`

## Mapping Rules

- workflow sequencing belongs in playbooks and roles
- API-heavy normalization, retry behavior, polling semantics, and version interpretation belong in later custom plugins
- Phase 1 documents those boundaries but does not implement the plugins
- prefer `kubernetes.core.k8s` and `kubernetes.core.k8s_info` wherever they are sufficient

## Current-to-Collection Mapping

| Current Source | Collection Target | Phase |
| --- | --- | --- |
| `acm_switchover.py` | `playbooks/preflight.yml`, `playbooks/switchover.yml` | 1 |
| `modules/preflight_coordinator.py` and `modules/preflight/` | `roles/preflight/` | 2 |
| `modules/primary_prep.py` | `roles/primary_prep/` | 3 |
| `modules/activation.py` | `roles/activation/` | 3 |
| `modules/post_activation.py` | `roles/post_activation/` | 3 |
| `modules/finalization.py` | `roles/finalization/` | 3 |
| `modules/decommission.py` | `roles/decommission/` | 6 |
| `lib/rbac_validator.py` | `roles/preflight/` validation behavior | 2 |
| `lib/validation.py` | centralized collection validation layer | 2 |
| `lib/kube_client.py` | stock `kubernetes.core` usage plus later helper code | 2-3 |
| `lib/utils.py` checkpoint semantics | documented checkpoint contract only | 1, runtime in 4 |
| `lib/argocd.py` | `roles/argocd_manage/` and deferred playbook | 5 |
| `lib/gitops_detector.py` | preflight detection and warnings | 5 |
| `scripts/discover-hub.sh` | supported migration bridge, not rewritten in Phase 1 | coexistence |
| `scripts/setup-rbac.sh` | `rbac_bootstrap` later | 6 |

## ACM Version Gates to Preserve

- ACM 2.11 BackupSchedule delete semantics
- ACM 2.12+ BackupSchedule pause semantics
- ACM 2.14+ `autoImportStrategy`

Roles must not hard-code scattered version comparisons. Phase 1 should document normalized facts only:

- `backup_schedule_pause_mode`
- `supports_auto_import_strategy`
- `supports_managed_service_account`

## Explicit Deferral

This behavior map intentionally does not schedule checkpoint backends, action plugins, or custom module implementation in Phase 1.
```

- [ ] **Step 2: Commit**

```bash
git add docs/ansible-collection/behavior-map.md
git commit -m "docs: add Ansible Collection behavior map"
```

---

### Task 3: Test Migration Catalog

**Files:**
- Create: `docs/ansible-collection/test-migration-catalog.md`

- [ ] **Step 1: Create the test migration catalog**

Create `docs/ansible-collection/test-migration-catalog.md` with the following structure and content:

```markdown
# Test Migration Catalog

Date: 2026-04-10
Purpose: Triage the existing Python-oriented test suite into collection-era test layers

## Target Layers

- `unit`: collection-local Python or metadata tests
- `integration`: collection behavior tests against mocked or disposable APIs
- `scenario`: multi-phase flow tests
- `parity`: shared scenario suite run against both implementations during coexistence
- `drop`: tests that only assert current Python internals

## Initial Triage Rules

- preflight, workflow, and scenario behavior stays in scope as behavior catalog
- CLI parsing tests do not migrate directly because the collection public API is variables, not flags
- shell-script implementation tests do not migrate directly unless the script remains part of the supported bridge
- state-engine internals do not migrate directly; only resume behavior and safety outcomes do

## Initial Mapping Examples

| Current Test File | Target Layer | Notes |
| --- | --- | --- |
| `tests/test_preflight_coordinator.py` | parity, later integration | preflight behavior catalog |
| `tests/test_primary_prep.py` | parity, later integration | core switchover phase |
| `tests/test_activation.py` | parity, later integration | core switchover phase |
| `tests/test_post_activation.py` | parity, later integration | core switchover phase |
| `tests/test_finalization.py` | parity, later integration | core switchover phase |
| `tests/test_validation.py` | later unit/integration | variable-validation semantics |
| `tests/test_rbac_validator.py` | later unit/integration | RBAC self-validation stays in core parity |
| `tests/test_argocd.py` | deferred | Phase 5 |
| `tests/test_gitops_detector.py` | deferred | Phase 5 |
| `tests/test_decommission.py` | deferred | Phase 6 |
| `tests/test_scripts_integration.py` | partial drop, partial bridge docs | only bridge behavior retained |

## Phase 1 Test Baseline

Phase 1 tests should verify only:

- collection metadata parses correctly
- playbooks are syntactically valid
- example variable files parse correctly
- CI entrypoints run successfully
```

- [ ] **Step 2: Commit**

```bash
git add docs/ansible-collection/test-migration-catalog.md
git commit -m "docs: add test migration catalog for collection rewrite"
```

---

### Task 4: Parity Matrix and Shared Scenario Catalog

**Files:**
- Create: `docs/ansible-collection/parity-matrix.md`
- Create: `docs/ansible-collection/scenario-catalog.md`

- [ ] **Step 1: Create the parity matrix**

Create `docs/ansible-collection/parity-matrix.md` with the following structure and content:

```markdown
# Parity Matrix

Date: 2026-04-10
Allowed statuses: `Python only`, `dual-supported`, `collection only`, `deprecated`

## Current Migration Baseline

| Capability | Status | Target Milestone | Notes |
| --- | --- | --- | --- |
| preflight validation | Python only | dual-supported | core parity requirement |
| primary prep | Python only | dual-supported | core parity requirement |
| activation | Python only | dual-supported | core parity requirement |
| post-activation verification | Python only | dual-supported | core parity requirement |
| finalization | Python only | dual-supported | core parity requirement |
| RBAC self-validation | Python only | dual-supported | core parity requirement |
| machine-readable reports | Python only | dual-supported | schema defined in Phase 1 |
| optional checkpoints | Python only | dual-supported | runtime work deferred to Phase 4 |
| Argo CD management | Python only | dual-supported | deferred to Phase 5 |
| discovery | Python only | dual-supported | supported bridge during coexistence |
| decommission | Python only | dual-supported | deferred to Phase 6 |
| RBAC bootstrap | Python only | dual-supported | deferred to Phase 6 |

## Milestone Gates

1. Collection preview
2. Dual-supported
3. Collection-primary
4. Python read-only
5. Python retirement

The matrix is the migration control document. Do not invent alternate status vocabularies in follow-on plans.
```

- [ ] **Step 2: Create the shared scenario catalog**

Create `docs/ansible-collection/scenario-catalog.md` with the following structure and content:

```markdown
# Shared Parity Scenario Catalog

Date: 2026-04-10
Purpose: Define scenarios that both implementations must eventually satisfy

## Scenario Schema

Each scenario records:

- inputs
- initial cluster state assumptions
- expected phase outcomes
- expected validation findings
- expected mutated resources
- expected report and checkpoint artifacts

## Initial Scenarios

### SCENARIO-001 Passive switchover happy path

- method: passive
- old hub action: secondary
- expected phases: all pass
- expected artifacts: report present, checkpoint optional

### SCENARIO-002 Full restore switchover happy path

- method: full
- expected phases: all pass
- expected artifacts: report present

### SCENARIO-003 Preflight version mismatch

- expected preflight: fail
- expected later phases: not run
- expected artifacts: report present

### SCENARIO-004 Validate-only mode

- expected mutations: none
- expected artifact: report present

### SCENARIO-005 Dry-run mode

- expected mutations: none
- expected artifact: report present
```

- [ ] **Step 3: Commit**

```bash
git add docs/ansible-collection/parity-matrix.md
git add docs/ansible-collection/scenario-catalog.md
git commit -m "docs: add parity control documents for collection rewrite"
```

---

## Phase 1: Collection Foundation Only

### Task 5: Collection Metadata and Directory Skeleton

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/galaxy.yml`
- Create: `ansible_collections/tomazb/acm_switchover/meta/runtime.yml`
- Create: `ansible_collections/tomazb/acm_switchover/README.md`
- Create: `ansible_collections/tomazb/acm_switchover/requirements.yml`
- Create: `ansible_collections/tomazb/acm_switchover/execution-environment.yml`
- Create: `ansible_collections/tomazb/acm_switchover/requirements.txt`
- Create: `ansible_collections/tomazb/acm_switchover/bindep.txt`

- [ ] **Step 1: Create the collection directory structure**

```bash
cd /home/tomaz/sources/rh-acm-switchover
mkdir -p ansible_collections/tomazb/acm_switchover/{meta,playbooks,docs,examples/group_vars,plugins/modules,plugins/action,plugins/module_utils,plugins/filter,plugins/callback,tests/unit}
for role in preflight primary_prep activation post_activation finalization; do
  mkdir -p "ansible_collections/tomazb/acm_switchover/roles/${role}/defaults"
  mkdir -p "ansible_collections/tomazb/acm_switchover/roles/${role}/meta"
  mkdir -p "ansible_collections/tomazb/acm_switchover/roles/${role}/tasks"
done
```

- [ ] **Step 2: Create `galaxy.yml`**

Create `ansible_collections/tomazb/acm_switchover/galaxy.yml`:

```yaml
namespace: tomazb
name: acm_switchover
version: 0.1.0
readme: README.md
authors:
  - Tomaz B
description: >-
  Ansible Collection foundation for Red Hat Advanced Cluster Management hub
  switchover automation.
license:
  - Apache-2.0
tags:
  - acm
  - kubernetes
  - openshift
  - switchover
dependencies:
  kubernetes.core: ">=3.0.0"
repository: https://github.com/tomazb/rh-acm-switchover
```

- [ ] **Step 3: Create `meta/runtime.yml`**

Create `ansible_collections/tomazb/acm_switchover/meta/runtime.yml`:

```yaml
---
requires_ansible: ">=2.15.0"
```

- [ ] **Step 4: Create dependency manifests**

Create `ansible_collections/tomazb/acm_switchover/requirements.yml`:

```yaml
---
collections:
  - name: kubernetes.core
    version: ">=3.0.0"
```

Create `ansible_collections/tomazb/acm_switchover/requirements.txt`:

```text
PyYAML>=6.0
```

Create `ansible_collections/tomazb/acm_switchover/bindep.txt`:

```text
python3 [platform:rpm]
```

- [ ] **Step 5: Create the execution environment definition**

Create `ansible_collections/tomazb/acm_switchover/execution-environment.yml`:

```yaml
---
version: 3

images:
  base_image:
    name: quay.io/ansible/ansible-runner:stable-2.15-latest

dependencies:
  galaxy: requirements.yml
  python: requirements.txt
  system: bindep.txt
```

- [ ] **Step 6: Create the collection `README.md`**

Create `ansible_collections/tomazb/acm_switchover/README.md`:

```markdown
# tomazb.acm_switchover

Foundation Ansible Collection for ACM hub switchover automation.

## Current Scope

- collection metadata and layout
- `preflight.yml` and `switchover.yml` stubs
- five core phase-role stubs
- collection variable model and compatibility docs

## Explicit Non-Scope

- runtime checkpoint behavior
- custom module implementation
- action plugin implementation
- Argo CD runtime behavior
- decommission runtime behavior
```

- [ ] **Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/galaxy.yml
git add ansible_collections/tomazb/acm_switchover/meta/runtime.yml
git add ansible_collections/tomazb/acm_switchover/README.md
git add ansible_collections/tomazb/acm_switchover/requirements.yml
git add ansible_collections/tomazb/acm_switchover/execution-environment.yml
git add ansible_collections/tomazb/acm_switchover/requirements.txt
git add ansible_collections/tomazb/acm_switchover/bindep.txt
git commit -m "feat: add collection metadata and directory skeleton"
```

---

### Task 6: Playbook and Role Stubs

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml`
- Create: `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/meta/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`
- Create: matching `defaults/main.yml`, `meta/main.yml`, `tasks/main.yml` for `primary_prep`, `activation`, `post_activation`, `finalization`

- [ ] **Step 1: Create `playbooks/preflight.yml`**

Create `ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml`:

```yaml
---
- name: ACM Hub Switchover - Preflight
  hosts: localhost
  connection: local
  gather_facts: false

  roles:
    - role: tomazb.acm_switchover.preflight
      tags:
        - preflight
        - validate
```

- [ ] **Step 2: Create `playbooks/switchover.yml`**

Create `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml`:

```yaml
---
- name: ACM Hub Switchover
  hosts: localhost
  connection: local
  gather_facts: false

  roles:
    - role: tomazb.acm_switchover.preflight
      tags: [preflight, validate]
    - role: tomazb.acm_switchover.primary_prep
      tags: [primary_prep]
    - role: tomazb.acm_switchover.activation
      tags: [activation]
    - role: tomazb.acm_switchover.post_activation
      tags: [post_activation, verify]
    - role: tomazb.acm_switchover.finalization
      tags: [finalization]
```

- [ ] **Step 3: Create the preflight role stub**

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: ""
    kubeconfig: ""
  secondary:
    context: ""
    kubeconfig: ""

acm_switchover_operation:
  method: passive
  old_hub_action: secondary
  activation_method: patch
  min_managed_clusters: 0

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_observability_checks: false
  skip_gitops_check: false
  skip_rbac_validation: false
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: execute
  verbose: false
  force: false
  report_dir: ./artifacts
  checkpoint:
    enabled: false
    backend: file
    path: .state/switchover.json
    reset: false
```

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/meta/main.yml`:

```yaml
---
galaxy_info:
  role_name: preflight
  author: Tomaz B
  description: Foundation stub for preflight validation behavior.
  license: Apache-2.0
  min_ansible_version: "2.15"
dependencies: []
```

Create `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`:

```yaml
---
- name: Emit preflight foundation contract
  ansible.builtin.set_fact:
    acm_switchover_phase_result:
      phase: preflight
      status: foundation_only
      changed: false
  tags:
    - preflight
    - validate
```

- [ ] **Step 4: Create the remaining four phase-role stubs**

Use the same pattern as preflight: defaults for the variables consumed by the phase, a short role description in `meta/main.yml`, and a `tasks/main.yml` that emits only the phase contract with `status: foundation_only`.

Required phase names:

- `primary_prep`
- `activation`
- `post_activation`
- `finalization`

Each `tasks/main.yml` must contain exactly one `set_fact` task and phase-appropriate tags.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml
git add ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml
git add ansible_collections/tomazb/acm_switchover/roles/
git commit -m "feat: add Phase 1 playbook and role stubs"
```

---

### Task 7: Example Inventory and Variable Files

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/examples/inventory.yml`
- Create: `ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml`

- [ ] **Step 1: Create the example inventory**

Create `ansible_collections/tomazb/acm_switchover/examples/inventory.yml`:

```yaml
---
all:
  hosts:
    localhost:
      ansible_connection: local
```

- [ ] **Step 2: Create the example variable file**

Create `ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: ~/.kube/config
  secondary:
    context: secondary-hub
    kubeconfig: ~/.kube/config

acm_switchover_operation:
  method: passive
  old_hub_action: secondary
  activation_method: patch
  min_managed_clusters: 0

acm_switchover_features:
  manage_auto_import_strategy: false
  skip_observability_checks: false
  skip_gitops_check: false
  skip_rbac_validation: false
  disable_observability_on_secondary: false
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_execution:
  mode: execute
  verbose: false
  force: false
  report_dir: ./artifacts
  checkpoint:
    enabled: false
    backend: file
    path: .state/switchover.json
    reset: false
```

- [ ] **Step 3: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/examples/inventory.yml
git add ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml
git commit -m "feat: add example inventory and variable contract"
```

---

### Task 8: Collection Documentation Skeleton

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`
- Create: `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`
- Create: `ansible_collections/tomazb/acm_switchover/docs/architecture.md`
- Create: `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`
- Create: `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`
- Create: `ansible_collections/tomazb/acm_switchover/docs/distribution.md`

- [ ] **Step 1: Create the variable reference**

Create `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`:

```markdown
# Variable Reference

## Namespaces

- `acm_switchover_hubs`
- `acm_switchover_operation`
- `acm_switchover_features`
- `acm_switchover_execution`
- `acm_switchover_rbac`

## Notes

- The collection public API is grouped variables, not a flat CLI flag layer.
- `decommission`, `argocd_resume`, and `setup` remain deferred.
- checkpoint keys are contract-only in Phase 1.
```

- [ ] **Step 2: Create the CLI migration map**

Create `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`:

```markdown
# CLI to Collection Migration Map

| Current CLI Flag | Collection Variable |
| --- | --- |
| `--primary-context` | `acm_switchover_hubs.primary.context` |
| `--secondary-context` | `acm_switchover_hubs.secondary.context` |
| `--method` | `acm_switchover_operation.method` |
| `--old-hub-action` | `acm_switchover_operation.old_hub_action` |
| `--activation-method` | `acm_switchover_operation.activation_method` |
| `--min-managed-clusters` | `acm_switchover_operation.min_managed_clusters` |
| `--validate-only` | `acm_switchover_execution.mode=validate` |
| `--dry-run` | `acm_switchover_execution.mode=dry_run` |
| `--state-file` | `acm_switchover_execution.checkpoint.path` |
| `--reset-state` | `acm_switchover_execution.checkpoint.reset` |
```

- [ ] **Step 3: Create the architecture note**

Create `ansible_collections/tomazb/acm_switchover/docs/architecture.md`:

```markdown
# Collection Architecture

## Foundations

- collection-first migration
- controller-side execution for both CLI and AAP
- explicit phases as operator-facing boundaries
- stock `kubernetes.core` first
- thin custom plugins later, not in Phase 1

## Phase 1 Boundaries

Phase 1 defines:

- collection layout
- variable contract
- playbook entrypoints
- role boundaries
- artifact schema
- lock model

Phase 1 does not implement:

- checkpoint backend code
- custom modules
- action plugins
```

- [ ] **Step 4: Create the artifact schema doc**

Create `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`:

```markdown
# Artifact Schema

## Report Artifact

Required fields:

- `schema_version`
- `timestamp`
- `phase`
- `status`
- `results`

Each result entry must support:

- `id`
- `severity`
- `status`
- `message`
- `details`
- `recommended_action`

## Checkpoint Contract

Phase 1 defines only the contract:

- current phase
- completed high-risk checkpoints
- operational data needed for resume or reversal
- Argo CD pause metadata
- structured error history
- report artifact references
- lock ownership metadata

Runtime checkpoint implementation is deferred to a later plan.

## Compatibility Rule

If exact compatibility with Python artifacts is not feasible, a documented schema mapping or translation note is required before rollout.
```

- [ ] **Step 5: Create the coexistence doc**

Create `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`:

```markdown
# Coexistence with the Python Tool

## Shared Behavior Contract

Parity is tracked by shared scenarios and the parity matrix, not by internal implementation shape.

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
```

- [ ] **Step 6: Create the distribution doc**

Create `ansible_collections/tomazb/acm_switchover/docs/distribution.md`:

```markdown
# Distribution and Packaging Strategy

## Targets

- Ansible Galaxy compatible packaging
- Automation Hub compatible packaging
- execution environment for AAP

## AAP Contract

- same playbooks as local CLI usage
- same variable model as local CLI usage
- survey and `extra_vars` values treated as untrusted input

## Lock Model

Phase 1 defines the rule only:

- local file-backed checkpoints require advisory locking
- shared or controller-backed checkpoints require a Lease-style or equivalent coordination mechanism
- lock failures must be explicit and operator-visible
```

- [ ] **Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/docs/
git commit -m "feat: add collection documentation skeleton"
```

---

### Task 9: CI Baseline and Foundation Validation

**Files:**
- Create: `.github/workflows/ansible-collection-foundation.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py`

- [ ] **Step 1: Create the metadata smoke test**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py`:

```python
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[5]
COLLECTION_ROOT = REPO_ROOT / "ansible_collections" / "tomazb" / "acm_switchover"


def test_galaxy_yml_parses():
    data = yaml.safe_load((COLLECTION_ROOT / "galaxy.yml").read_text())
    assert data["namespace"] == "tomazb"
    assert data["name"] == "acm_switchover"


def test_runtime_yml_parses():
    data = yaml.safe_load((COLLECTION_ROOT / "meta" / "runtime.yml").read_text())
    assert data["requires_ansible"].startswith(">=")


def test_example_group_vars_parse():
    data = yaml.safe_load((COLLECTION_ROOT / "examples" / "group_vars" / "all.yml").read_text())
    assert "acm_switchover_hubs" in data
    assert "acm_switchover_execution" in data
```

- [ ] **Step 2: Create the GitHub Actions workflow**

Create `.github/workflows/ansible-collection-foundation.yml`:

```yaml
name: ansible-collection-foundation

on:
  pull_request:
  push:
    branches: [main]

jobs:
  foundation:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install foundation dependencies
        run: |
          python -m pip install --upgrade pip
          pip install ansible-core==2.15.* pytest PyYAML ansible-builder
      - name: Validate collection metadata tests
        run: |
          PYTHONPATH=. pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py -q
      - name: Syntax check playbooks
        run: |
          ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml --syntax-check
          ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml --syntax-check
      - name: Build collection archive
        run: |
          cd ansible_collections/tomazb/acm_switchover
          ansible-galaxy collection build --output-path /tmp/dist
```

- [ ] **Step 3: Run the local validation commands**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py -q
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/preflight.yml --syntax-check
ansible-playbook ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml --syntax-check
cd ansible_collections/tomazb/acm_switchover
ansible-galaxy collection build --output-path /tmp/dist
```

Expected:

- `pytest` passes
- both `ansible-playbook --syntax-check` commands pass
- `ansible-galaxy collection build` produces a tarball in `/tmp/dist`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ansible-collection-foundation.yml
git add ansible_collections/tomazb/acm_switchover/tests/unit/test_collection_metadata.py
git commit -m "feat: add Phase 1 collection CI baseline"
```

---

## Self-Review Checklist

Before executing this plan, verify the following against the approved design:

1. Phase 1 contains no checkpoint backend, action plugin, or custom module implementation work.
2. The parity matrix uses only `Python only`, `dual-supported`, `collection only`, and `deprecated`.
3. The coexistence doc includes:
   - dual-bug-fix policy
   - shared-code policy
   - discovery bridge
   - artifact compatibility rule
4. The distribution doc defines the lock model without implementing it.
5. Only `preflight.yml` and `switchover.yml` are created in Phase 1.
6. Deferred content is documented explicitly, not silently omitted.

## Execution Order

Execute tasks in this order:

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8
9. Task 9

## Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-10-ansible-collection-rewrite-plan.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
