# Ansible Collection Rewrite Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the core switchover execution flow in the collection so `primary_prep`, `activation`, `post_activation`, and `finalization` can run end-to-end with explicit dry-run behavior and parity-oriented reporting.

**Architecture:** Phase 3 keeps sequencing in collection roles and uses thin custom modules where the current Python code has ACM-specific version gates, restore interpretation, or cluster-verification logic that would become brittle in YAML alone. The playbooks stay controller-driven on `localhost`, while modules encapsulate BackupSchedule semantics, restore activation state, managed-cluster verification, and finalization-oriented status normalization.

**Tech Stack:** Ansible Collection roles and playbooks, Python 3.10+, `ansible-core >= 2.15`, `kubernetes.core >= 3.0.0`, pytest, `ansible-playbook`, `ansible-test sanity`

This plan assumes Phase 0, Phase 1, and Phase 2 work is complete first, including the collection skeleton, shared `module_utils`, and preflight/report contract.

---

## File Structure

```text
ansible_collections/tomazb/acm_switchover/
  plugins/
    modules/
      acm_backup_schedule.py                  - Version-aware BackupSchedule pause/enable planning and result contract
      acm_restore_info.py                     - Restore discovery, passive-sync selection, and activation-state normalization
      acm_managedcluster_status.py            - ManagedCluster condition normalization
      acm_cluster_verify.py                   - Cluster threshold, joined/available aggregation, and klusterlet verification helpers
  roles/
    primary_prep/
      tasks/
        main.yml
        pause_backups.yml
        manage_auto_import.yml
        scale_observability.yml
    activation/
      tasks/
        main.yml
        verify_passive_sync.yml
        activate_restore.yml
        wait_for_restore.yml
        apply_immediate_import.yml
    post_activation/
      tasks/
        main.yml
        verify_managed_clusters.yml
        verify_klusterlet.yml
        verify_observability.yml
    finalization/
      tasks/
        main.yml
        enable_backups.yml
        verify_backups.yml
        verify_mch.yml
        handle_old_hub.yml
  tests/
    unit/
      plugins/
        modules/
          test_acm_backup_schedule.py
          test_acm_restore_info.py
          test_acm_managedcluster_status.py
          test_acm_cluster_verify.py
    integration/
      fixtures/
        switchover/
          passive_activation_success.yml
          post_activation_cluster_failure.yml
          finalization_backup_recovery.yml
      test_switchover_roles.py
    scenario/
      test_core_switchover.py
  docs/
    artifact-schema.md                        - Add execution-phase result records
    variable-reference.md                     - Add role result facts for primary_prep/activation/post_activation/finalization
docs/ansible-collection/
  parity-matrix.md                            - Promote core switchover rows to dual-supported
  scenario-catalog.md                         - Mark supported core switchover scenarios as collection-covered
```

## Environment Setup

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules -v
```

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -v
```

```bash
cd /home/tomaz/sources/rh-acm-switchover/ansible_collections/tomazb/acm_switchover
ansible-test sanity --python 3.11 plugins/
```

---

## Phase 3: Switchover Execution Migration

### Task 1: BackupSchedule and Restore Modules (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py`
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_backup_schedule import (
    build_backup_schedule_operation,
    backup_schedule_pause_mode,
)


def test_pause_mode_uses_delete_for_acm_211():
    assert backup_schedule_pause_mode("2.11.6") == "delete"


def test_pause_mode_uses_spec_paused_for_acm_212_plus():
    assert backup_schedule_pause_mode("2.12.0") == "pause"


def test_build_pause_operation_for_spec_paused_mode():
    operation = build_backup_schedule_operation(
        acm_version="2.13.2",
        intent="pause",
        schedules=[{"metadata": {"name": "acm-hub-backup"}, "spec": {"paused": False}}],
    )
    assert operation["action"] == "patch"
    assert operation["patch"]["spec"]["paused"] is True
```

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_restore_info import (
    build_activation_patch,
    select_passive_sync_restore,
)


def test_select_passive_sync_restore_prefers_sync_enabled_resource():
    restore = select_passive_sync_restore(
        [
            {"metadata": {"name": "restore-old", "creationTimestamp": "2026-04-10T10:00:00Z"}, "spec": {}},
            {
                "metadata": {"name": "restore-passive", "creationTimestamp": "2026-04-10T11:00:00Z"},
                "spec": {"syncRestoreWithNewBackups": True},
            },
        ]
    )
    assert restore["metadata"]["name"] == "restore-passive"


def test_build_activation_patch_targets_latest_backup():
    patch = build_activation_patch("latest")
    assert patch == {"spec": {"veleroManagedClustersBackupName": "latest"}}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementations**

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py`:

```python
from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def backup_schedule_pause_mode(acm_version: str) -> str:
    major, minor, *_rest = [int(part) for part in acm_version.split(".")]
    return "delete" if (major, minor) <= (2, 11) else "pause"


def build_backup_schedule_operation(acm_version: str, intent: str, schedules: list[dict]) -> dict:
    mode = backup_schedule_pause_mode(acm_version)
    if intent == "pause" and mode == "delete":
        return {"action": "delete", "mode": mode}
    if intent == "pause":
        return {"action": "patch", "mode": mode, "patch": {"spec": {"paused": True}}}
    return {"action": "patch", "mode": mode, "patch": {"spec": {"paused": False}}}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "acm_version": {"type": "str", "required": True},
            "intent": {"type": "str", "required": True},
            "schedules": {"type": "list", "elements": "dict", "default": []},
        },
        supports_check_mode=True,
    )
    operation = build_backup_schedule_operation(
        module.params["acm_version"],
        module.params["intent"],
        module.params["schedules"],
    )
    module.exit_json(changed=operation["action"] != "none", operation=operation)


if __name__ == "__main__":
    main()
```

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py`:

```python
from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


def select_passive_sync_restore(restores: list[dict]) -> dict | None:
    candidates = [item for item in restores if item.get("spec", {}).get("syncRestoreWithNewBackups") is True]
    candidates.sort(key=lambda item: item.get("metadata", {}).get("creationTimestamp", ""), reverse=True)
    return candidates[0] if candidates else None


def build_activation_patch(backup_name: str) -> dict:
    return {"spec": {"veleroManagedClustersBackupName": backup_name}}


def main() -> None:
    module = AnsibleModule(
        argument_spec={"restores": {"type": "list", "elements": "dict", "default": []}},
        supports_check_mode=True,
    )
    selected = select_passive_sync_restore(module.params["restores"])
    module.exit_json(changed=False, restore=selected)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py
git commit -m "feat: add backup schedule and restore info modules"
```

---

### Task 2: ManagedCluster Status and Cluster Verification Modules (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_managedcluster_status.py`
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_cluster_verify.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_cluster_verify.py`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_managedcluster_status import summarize_cluster


def test_summarize_cluster_detects_joined_and_available():
    summary = summarize_cluster(
        {
            "metadata": {"name": "cluster-a"},
            "status": {
                "conditions": [
                    {"type": "ManagedClusterConditionAvailable", "status": "True"},
                    {"type": "ManagedClusterJoined", "status": "True"},
                ]
            },
        }
    )
    assert summary["joined"] is True
    assert summary["available"] is True
```

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_cluster_verify.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_cluster_verify import summarize_cluster_group


def test_cluster_group_fails_when_threshold_not_met():
    summary = summarize_cluster_group(
        [
            {"name": "cluster-a", "joined": True, "available": True},
            {"name": "cluster-b", "joined": False, "available": False},
        ],
        min_managed_clusters=2,
    )
    assert summary["passed"] is False
    assert "cluster-b" in summary["pending"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_cluster_verify.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementations**

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_managedcluster_status.py`:

```python
from __future__ import annotations


def summarize_cluster(cluster: dict) -> dict:
    conditions = cluster.get("status", {}).get("conditions", [])
    return {
        "name": cluster.get("metadata", {}).get("name", "unknown"),
        "joined": any(item.get("type") == "ManagedClusterJoined" and item.get("status") == "True" for item in conditions),
        "available": any(
            item.get("type") == "ManagedClusterConditionAvailable" and item.get("status") == "True"
            for item in conditions
        ),
    }
```

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_cluster_verify.py`:

```python
from __future__ import annotations


def summarize_cluster_group(clusters: list[dict], min_managed_clusters: int) -> dict:
    pending = [item["name"] for item in clusters if not (item["joined"] and item["available"])]
    return {
        "passed": len(clusters) >= min_managed_clusters and not pending,
        "total": len(clusters),
        "pending": pending,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_cluster_verify.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_managedcluster_status.py
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_cluster_verify.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_cluster_verify.py
git commit -m "feat: add managed cluster verification modules"
```

---

### Task 3: Primary Prep and Activation Roles

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/pause_backups.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/manage_auto_import.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/scale_observability.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/verify_passive_sync.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/activate_restore.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/wait_for_restore.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/apply_immediate_import.yml`

- [ ] **Step 1: Write the failing integration fixture and test**

Create `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover/passive_activation_success.yml`:

```yaml
---
acm_switchover_hubs:
  primary:
    context: primary-hub
    kubeconfig: ./kubeconfigs/primary
  secondary:
    context: secondary-hub
    kubeconfig: ./kubeconfigs/secondary

acm_switchover_operation:
  method: passive
  activation_method: patch
  old_hub_action: secondary

acm_switchover_execution:
  mode: execute
  report_dir: ./artifacts

acm_primary_backup_schedules_info:
  resources:
    - metadata:
        name: acm-hub-backup
      spec:
        paused: false

acm_secondary_restores_info:
  resources:
    - metadata:
        name: restore-acm-passive-sync
      spec:
        syncRestoreWithNewBackups: true
      status:
        phase: Enabled
```

Create `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`:

```python
def test_primary_prep_and_activation_fixture_pass(run_switchover_fixture):
    completed, report = run_switchover_fixture("passive_activation_success.yml")
    assert completed.returncode == 0
    assert report["phases"]["primary_prep"]["status"] == "pass"
    assert report["phases"]["activation"]["status"] == "pass"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -v
```

Expected: FAIL because the roles still emit only Phase 1 foundation facts.

- [ ] **Step 3: Implement primary prep role tasks**

Update `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`:

```yaml
---
- name: Pause BackupSchedule on primary hub
  ansible.builtin.import_tasks: pause_backups.yml

- name: Manage import-controller strategy and disable-auto-import markers
  ansible.builtin.import_tasks: manage_auto_import.yml

- name: Scale observability components when enabled
  ansible.builtin.import_tasks: scale_observability.yml
  when: not (acm_switchover_features.skip_observability_checks | default(false))

- name: Publish primary_prep result contract
  ansible.builtin.set_fact:
    acm_switchover_primary_prep_result:
      phase: primary_prep
      status: pass
      changed: true
```

Create `pause_backups.yml` with a call to `tomazb.acm_switchover.acm_backup_schedule` and register the operation result.

Create `manage_auto_import.yml` with `ansible.builtin.set_fact` to record:

```yaml
acm_switchover_auto_import_result:
  changed: "{{ acm_switchover_features.manage_auto_import_strategy | default(false) }}"
  target_strategy: "{{ 'ImportAndSync' if acm_switchover_features.manage_auto_import_strategy | default(false) else 'default' }}"
```

Create `scale_observability.yml` with `kubernetes.core.k8s_scale` for `thanos-compactor` when the compactor statefulset exists.

- [ ] **Step 4: Implement activation role tasks**

Update `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml`:

```yaml
---
- name: Verify passive sync when method is passive
  ansible.builtin.import_tasks: verify_passive_sync.yml
  when: acm_switchover_operation.method == 'passive'

- name: Activate restore
  ansible.builtin.import_tasks: activate_restore.yml

- name: Wait for restore completion
  ansible.builtin.import_tasks: wait_for_restore.yml

- name: Apply immediate import annotations when required
  ansible.builtin.import_tasks: apply_immediate_import.yml

- name: Publish activation result contract
  ansible.builtin.set_fact:
    acm_switchover_activation_result:
      phase: activation
      status: pass
      changed: true
```

Use `tomazb.acm_switchover.acm_restore_info` in `verify_passive_sync.yml` and `activate_restore.yml` to normalize restore selection and activation patch content.

- [ ] **Step 5: Run the integration test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -v
```

Expected: PASS for the `passive_activation_success.yml` fixture.

- [ ] **Step 6: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/
git add ansible_collections/tomazb/acm_switchover/roles/activation/tasks/
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover/passive_activation_success.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py
git commit -m "feat: implement primary prep and activation roles"
```

---

### Task 4: Post-Activation and Finalization Roles

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/verify_managed_clusters.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/verify_klusterlet.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/verify_observability.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/enable_backups.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/verify_backups.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/verify_mch.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/handle_old_hub.yml`

- [ ] **Step 1: Extend the integration fixtures**

Append to `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover/post_activation_cluster_failure.yml`:

```yaml
---
acm_switchover_operation:
  min_managed_clusters: 2

acm_secondary_managed_clusters_info:
  resources:
    - metadata:
        name: cluster-a
      status:
        conditions:
          - type: ManagedClusterJoined
            status: "True"
          - type: ManagedClusterConditionAvailable
            status: "True"
    - metadata:
        name: cluster-b
      status:
        conditions:
          - type: ManagedClusterJoined
            status: "False"
          - type: ManagedClusterConditionAvailable
            status: "False"
```

Add to `test_switchover_roles.py`:

```python
def test_post_activation_failure_reports_pending_clusters(run_switchover_fixture):
    completed, report = run_switchover_fixture("post_activation_cluster_failure.yml")
    assert completed.returncode != 0
    assert report["phases"]["post_activation"]["status"] == "fail"
    assert "cluster-b" in report["phases"]["post_activation"]["summary"]["pending"]
```

- [ ] **Step 2: Run the integration test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -v
```

Expected: FAIL because post-activation and finalization logic is still a stub.

- [ ] **Step 3: Implement post-activation tasks**

Use `tomazb.acm_switchover.acm_managedcluster_status` plus `tomazb.acm_switchover.acm_cluster_verify` to produce a stable summary in `verify_managed_clusters.yml`.

Use `verify_klusterlet.yml` to emit a controller-side warning-only result contract for clusters requiring manual klusterlet remediation.

Use `verify_observability.yml` to check `observatorium-api` deployment readiness when observability is enabled.

Publish this result in `roles/post_activation/tasks/main.yml`:

```yaml
acm_switchover_post_activation_result:
  phase: post_activation
  status: "{{ 'pass' if acm_cluster_verify_result.passed else 'fail' }}"
  changed: false
  summary: "{{ acm_cluster_verify_result }}"
```

- [ ] **Step 4: Implement finalization tasks**

Use `tomazb.acm_switchover.acm_backup_schedule` in `enable_backups.yml`.

Use `verify_backups.yml` to assert that the new hub sees fresh ACM-owned backups.

Use `verify_mch.yml` to assert `MultiClusterHub` health on the new hub.

Use `handle_old_hub.yml` to emit a typed result for:
- `secondary`
- `decommission`
- `none`

Publish this result in `roles/finalization/tasks/main.yml`:

```yaml
acm_switchover_finalization_result:
  phase: finalization
  status: pass
  changed: true
  old_hub_action: "{{ acm_switchover_operation.old_hub_action | default('secondary') }}"
```

- [ ] **Step 5: Re-run the integration tests**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py -v
```

Expected: PASS for success fixtures and FAIL with structured post-activation summary for the cluster-failure fixture.

- [ ] **Step 6: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/
git add ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/switchover/post_activation_cluster_failure.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py
git commit -m "feat: implement post activation and finalization roles"
```

---

### Task 5: Scenario Coverage, Docs, and Full Verification

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/scenario/test_core_switchover.py`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md`
- Modify: `docs/ansible-collection/parity-matrix.md`
- Modify: `docs/ansible-collection/scenario-catalog.md`

- [ ] **Step 1: Add the scenario test**

Create `ansible_collections/tomazb/acm_switchover/tests/scenario/test_core_switchover.py`:

```python
def test_core_switchover_fixture_emits_all_phase_reports(run_switchover_fixture):
    completed, report = run_switchover_fixture("finalization_backup_recovery.yml")
    assert completed.returncode == 0
    assert set(report["phases"]) >= {"primary_prep", "activation", "post_activation", "finalization"}
```

- [ ] **Step 2: Update the docs**

Update `artifact-schema.md` to add a top-level `phases` object:

````markdown
## Core Switchover Report Contract

```json
{
  "phases": {
    "primary_prep": {"status": "pass|fail", "changed": true},
    "activation": {"status": "pass|fail", "changed": true},
    "post_activation": {"status": "pass|fail", "summary": {}},
    "finalization": {"status": "pass|fail", "old_hub_action": "secondary|decommission|none"}
  }
}
```
````

Update `variable-reference.md` to add the role result facts for each execution phase.

Update `parity-matrix.md` and `scenario-catalog.md` to mark the supported core switchover scenarios as `dual-supported`.

- [ ] **Step 3: Run the full Phase 3 verification suite**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py \
  ansible_collections/tomazb/acm_switchover/tests/scenario/test_core_switchover.py -v
```

Expected: PASS.

- [ ] **Step 4: Run collection sanity**

```bash
cd /home/tomaz/sources/rh-acm-switchover/ansible_collections/tomazb/acm_switchover
ansible-test sanity --python 3.11 plugins/
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/scenario/test_core_switchover.py
git add ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md
git add ansible_collections/tomazb/acm_switchover/docs/variable-reference.md
git add docs/ansible-collection/parity-matrix.md
git add docs/ansible-collection/scenario-catalog.md
git commit -m "docs: mark core switchover phase parity"
```
