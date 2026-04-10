# Ansible Collection Rewrite Phase 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the remaining non-core operational capabilities a clear home in the collection by adding discovery, decommission, and RBAC bootstrap entrypoints, while reconciling packaging and deploy assets for long-term distribution.

**Architecture:** Phase 6 intentionally separates the concerns that were deferred from core parity: discovery stays read-only and bridge-friendly, decommission is isolated behind its own playbook and confirmations, RBAC bootstrap becomes collection-native instead of a standalone shell-only path, and packaging reconciles execution-environment metadata with the repo’s existing Helm and RBAC assets. Where a shell helper remains temporarily useful, the plan documents that bridge explicitly instead of leaving ownership ambiguous.

**Tech Stack:** Ansible Collection roles and playbooks, Python 3.10+, `ansible-core >= 2.15`, `kubernetes.core >= 3.0.0`, pytest, `ansible-playbook`, ansible-builder

This plan assumes the core switchover and optional checkpoint support are already in place.

---

## File Structure

```text
ansible_collections/tomazb/acm_switchover/
  playbooks/
    discovery.yml                            - Collection-native discovery entrypoint
    decommission.yml                         - Safe old-hub teardown entrypoint
    rbac_bootstrap.yml                       - RBAC deployment and kubeconfig generation entrypoint
  roles/
    discovery/
      defaults/main.yml
      tasks/main.yml
    decommission/
      defaults/main.yml
      tasks/main.yml
      tasks/delete_observability.yml
      tasks/delete_managed_clusters.yml
      tasks/delete_multiclusterhub.yml
    rbac_bootstrap/
      defaults/main.yml
      tasks/main.yml
      tasks/deploy_manifests.yml
      tasks/generate_kubeconfigs.yml
      tasks/validate_permissions.yml
  plugins/
    modules/
      acm_discovery.py                       - Context and hub-state discovery helper
      acm_rbac_bootstrap.py                  - Manifest-selection and kubeconfig-output planning helper
  tests/
    unit/
      plugins/
        modules/
          test_acm_discovery.py
          test_acm_rbac_bootstrap.py
    integration/
      fixtures/
        noncore/
          discovery_bridge.yml
          decommission_dry_run.yml
          rbac_bootstrap_dry_run.yml
      test_noncore_roles.py
  docs/
    distribution.md
    cli-migration-map.md
    variable-reference.md
deploy/
  helm/acm-switchover-rbac/                  - Reconciled with collection RBAC bootstrap
  rbac/                                      - Reconciled with collection role inputs
scripts/
  README.md                                  - Updated to mark remaining bridge scripts and retirement status
docs/ansible-collection/
  parity-matrix.md
```

## Environment Setup

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_discovery.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_bootstrap.py -v
```

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_noncore_roles.py -v
```

---

## Phase 6: Non-Core Helpers and Operational Extras

### Task 1: Discovery Module and Entry Point (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_discovery.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_discovery.py`
- Create: `ansible_collections/tomazb/acm_switchover/playbooks/discovery.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/discovery/defaults/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/discovery/tasks/main.yml`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_discovery.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_discovery import classify_hub_state


def test_classify_hub_state_marks_passive_sync_secondary():
    state = classify_hub_state({"restore_state": "passive-sync", "managed_clusters": 0})
    assert state == "secondary"


def test_classify_hub_state_marks_active_primary():
    state = classify_hub_state({"restore_state": "none", "managed_clusters": 3})
    assert state == "primary"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_discovery.py -v
```

Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Create `acm_discovery.py`:

```python
from __future__ import annotations


def classify_hub_state(facts: dict) -> str:
    if facts.get("restore_state") == "passive-sync":
        return "secondary"
    if facts.get("managed_clusters", 0) > 0:
        return "primary"
    return "standby"
```

Create `playbooks/discovery.yml`:

```yaml
---
- name: Discover ACM hubs
  hosts: localhost
  gather_facts: false
  roles:
    - role: tomazb.acm_switchover.discovery
```

Create `roles/discovery/tasks/main.yml` that emits a read-only discovery summary and explicitly records `scripts/discover-hub.sh` as the temporary bridge when context enumeration is not yet fully collection-native.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_discovery.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_discovery.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_discovery.py
git add ansible_collections/tomazb/acm_switchover/playbooks/discovery.yml
git add ansible_collections/tomazb/acm_switchover/roles/discovery/
git commit -m "feat: add collection discovery entrypoint"
```

---

### Task 2: Decommission Role and Playbook

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/playbooks/decommission.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/decommission/defaults/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/delete_observability.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/delete_managed_clusters.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/decommission/tasks/delete_multiclusterhub.yml`

- [ ] **Step 1: Write the integration fixture**

Create `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/noncore/decommission_dry_run.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: ./kubeconfigs/primary

acm_switchover_execution:
  mode: dry_run

acm_switchover_decommission:
  interactive: false
  has_observability: true
```

Add to `test_noncore_roles.py`:

```python
def test_decommission_dry_run_fixture(run_noncore_fixture):
    completed, summary = run_noncore_fixture("decommission_dry_run.yml", "decommission")
    assert completed.returncode == 0
    assert summary["phase"] == "decommission"
    assert summary["mode"] == "dry_run"
```

- [ ] **Step 2: Implement the role**

Create `playbooks/decommission.yml`:

```yaml
---
- name: Decommission old ACM hub
  hosts: localhost
  gather_facts: false
  roles:
    - role: tomazb.acm_switchover.decommission
```

Implement the role with the same step order as `modules/decommission.py`:
- delete observability first
- delete non-local `ManagedCluster` resources
- delete `MultiClusterHub` last

Keep the collection non-interactive by default and require an explicit variable gate:

```yaml
acm_switchover_decommission:
  confirmed: false
```

Fail fast unless `confirmed` is true or `mode == dry_run`.

- [ ] **Step 3: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/playbooks/decommission.yml
git add ansible_collections/tomazb/acm_switchover/roles/decommission/
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/noncore/decommission_dry_run.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_noncore_roles.py
git commit -m "feat: add decommission role and playbook"
```

---

### Task 3: RBAC Bootstrap Role and Planning Module (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_bootstrap.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_bootstrap.py`
- Create: `ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/defaults/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/deploy_manifests.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/generate_kubeconfigs.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/tasks/validate_permissions.yml`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_bootstrap.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_rbac_bootstrap import select_rbac_assets


def test_select_rbac_assets_for_operator_and_decommission():
    assets = select_rbac_assets(role="operator", include_decommission=True)
    assert "deploy/rbac/clusterrole.yaml" in assets
    assert any("decommission" in path for path in assets)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_bootstrap.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement planning helper and role**

Create `acm_rbac_bootstrap.py` with:

```python
from __future__ import annotations


def select_rbac_assets(role: str, include_decommission: bool) -> list[str]:
    assets = [
        "deploy/rbac/namespace.yaml",
        "deploy/rbac/serviceaccount.yaml",
        "deploy/rbac/role.yaml",
        "deploy/rbac/rolebinding.yaml",
        "deploy/rbac/clusterrole.yaml",
        "deploy/rbac/clusterrolebinding.yaml",
    ]
    if include_decommission:
        assets.append("deploy/rbac/extensions/decommission/clusterrole.yaml")
    return assets
```

Create `playbooks/rbac_bootstrap.yml` and the `rbac_bootstrap` role to:
- apply RBAC manifests
- optionally generate kubeconfigs
- optionally validate with the collection RBAC validation module

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_bootstrap.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_bootstrap.py
git add ansible_collections/tomazb/acm_switchover/playbooks/rbac_bootstrap.yml
git add ansible_collections/tomazb/acm_switchover/roles/rbac_bootstrap/
git commit -m "feat: add rbac bootstrap role and planning helper"
```

---

### Task 4: Packaging and Deploy-Asset Reconciliation

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/docs/distribution.md`
- Modify: `ansible_collections/tomazb/acm_switchover/execution-environment.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/requirements.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/requirements.txt`
- Modify: `ansible_collections/tomazb/acm_switchover/bindep.txt`
- Modify: `deploy/helm/acm-switchover-rbac/README.md`
- Modify: `deploy/rbac/namespace.yaml`
- Modify: `deploy/rbac/serviceaccount.yaml`
- Modify: `deploy/rbac/role.yaml`
- Modify: `deploy/rbac/rolebinding.yaml`
- Modify: `deploy/rbac/clusterrole.yaml`
- Modify: `deploy/rbac/clusterrolebinding.yaml`
- Modify: `scripts/README.md`

- [ ] **Step 1: Update distribution docs**

Add a `Collection Primary Distribution` section to `distribution.md` that defines:
- Galaxy / Automation Hub package as the canonical operator artifact
- execution-environment build as the canonical AAP runtime
- Helm chart and raw RBAC YAML as implementation assets for the `rbac_bootstrap` role, not parallel operator UX

- [ ] **Step 2: Align dependency manifests**

Ensure `execution-environment.yml`, `requirements.yml`, `requirements.txt`, and `bindep.txt` reflect the collections and Python packages required by Phases 3-6.

- [ ] **Step 3: Update bridge-script status**

Update `scripts/README.md` to classify:
- `discover-hub.sh` as supported bridge until collection discovery covers all context enumeration needs
- `setup-rbac.sh` as deprecated in favor of `playbooks/rbac_bootstrap.yml`
- `argocd-manage.sh` as deprecated in favor of Phase 5 collection content

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/docs/distribution.md
git add ansible_collections/tomazb/acm_switchover/execution-environment.yml
git add ansible_collections/tomazb/acm_switchover/requirements.yml
git add ansible_collections/tomazb/acm_switchover/requirements.txt
git add ansible_collections/tomazb/acm_switchover/bindep.txt
git add deploy/helm/acm-switchover-rbac/README.md
git add deploy/rbac/
git add scripts/README.md
git commit -m "docs: reconcile packaging and deploy assets with collection roles"
```

---

### Task 5: Non-Core Role Integration Tests, Parity Updates, and Verification

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/noncore/discovery_bridge.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/noncore/rbac_bootstrap_dry_run.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/test_noncore_roles.py`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`
- Modify: `docs/ansible-collection/parity-matrix.md`

- [ ] **Step 1: Add the integration tests**

Create `test_noncore_roles.py` with three role smoke tests:

```python
def test_discovery_bridge_fixture(run_noncore_fixture):
    completed, summary = run_noncore_fixture("discovery_bridge.yml", "discovery")
    assert completed.returncode == 0
    assert summary["playbook"] == "discovery"


def test_rbac_bootstrap_dry_run_fixture(run_noncore_fixture):
    completed, summary = run_noncore_fixture("rbac_bootstrap_dry_run.yml", "rbac_bootstrap")
    assert completed.returncode == 0
    assert summary["mode"] == "dry_run"
```

- [ ] **Step 2: Update docs and parity matrix**

Update `cli-migration-map.md` and `variable-reference.md` for the new playbooks and variables.

Update `parity-matrix.md` to move discovery, decommission, and RBAC bootstrap to `dual-supported` only after these integration tests pass.

- [ ] **Step 3: Run the full Phase 6 verification suite**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_discovery.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_bootstrap.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_noncore_roles.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/noncore/
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_noncore_roles.py
git add ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md
git add ansible_collections/tomazb/acm_switchover/docs/variable-reference.md
git add docs/ansible-collection/parity-matrix.md
git commit -m "docs: mark non core collection phase parity"
```
