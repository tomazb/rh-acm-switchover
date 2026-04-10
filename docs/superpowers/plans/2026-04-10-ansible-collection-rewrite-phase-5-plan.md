# Ansible Collection Rewrite Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Argo CD pause/resume workflow and separate GitOps drift warnings so GitOps-managed switchover environments remain safe, reversible, and explicit in the collection.

**Architecture:** Phase 5 keeps generic GitOps marker detection read-only and warning-oriented, while Argo CD auto-sync mutation lives behind an explicit module and role boundary. The collection reuses the current run-id and annotation model so pause state can be restored safely at finalization time or through a dedicated resume entrypoint.

**Tech Stack:** Ansible Collection modules and roles, Python 3.10+, `ansible-core >= 2.15`, `kubernetes.core >= 3.0.0`, pytest, `ansible-playbook`

This plan assumes Phase 3 exists first because primary-prep and finalization phases need real places to call pause and resume operations.

---

## File Structure

```text
ansible_collections/tomazb/acm_switchover/
  plugins/
    modules/
      acm_argocd_autosync.py                  - Detect installation, find ACM-touching apps, pause/resume autosync
    module_utils/
      gitops.py                               - Shared marker detection helpers
  roles/
    argocd_manage/
      defaults/main.yml
      tasks/
        main.yml
        discover.yml
        pause.yml
        resume.yml
    preflight/tasks/validate_gitops.yml       - Warning-only marker detection
    primary_prep/tasks/main.yml               - Optional pause hook
    finalization/tasks/main.yml               - Optional resume hook
  playbooks/
    argocd_resume.yml                         - Resume-only entrypoint
  tests/
    unit/
      plugins/
        modules/
          test_acm_argocd_autosync.py
        module_utils/
          test_gitops.py
    integration/
      fixtures/
        argocd/
          pause_and_resume.yml
      test_argocd_manage_role.py
  docs/
    cli-migration-map.md
    coexistence.md
docs/ansible-collection/
  parity-matrix.md
```

## Environment Setup

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_gitops.py -v
```

---

## Phase 5: Argo CD and GitOps Behavior

### Task 1: Argo CD Autosync Module (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_argocd_autosync.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_argocd_autosync import (
    build_pause_patch,
    is_acm_touching_application,
)


def test_acm_touching_app_matches_backup_schedule_kind():
    assert is_acm_touching_application(
        {
            "metadata": {"namespace": "argocd", "name": "acm-app"},
            "status": {"resources": [{"kind": "BackupSchedule", "namespace": "open-cluster-management-backup"}]},
        }
    ) is True


def test_build_pause_patch_removes_automated_and_sets_run_id():
    patch = build_pause_patch({"automated": {"prune": True}}, "run-123")
    assert patch["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == "run-123"
    assert "automated" not in patch["spec"]["syncPolicy"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py -v
```

Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_argocd_autosync.py`:

```python
from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule


ACM_NAMESPACES = {
    "open-cluster-management",
    "open-cluster-management-backup",
    "open-cluster-management-observability",
    "multicluster-engine",
    "open-cluster-management-global-set",
    "local-cluster",
}

ACM_KINDS = {
    "MultiClusterHub",
    "MultiClusterObservability",
    "ManagedCluster",
    "BackupSchedule",
    "Restore",
    "ClusterDeployment",
}


def is_acm_touching_application(app: dict) -> bool:
    for resource in app.get("status", {}).get("resources", []):
        if resource.get("namespace") in ACM_NAMESPACES:
            return True
        if resource.get("kind") in ACM_KINDS:
            return True
    return False


def build_pause_patch(sync_policy: dict, run_id: str) -> dict:
    sync_policy = dict(sync_policy)
    sync_policy.pop("automated", None)
    return {
        "metadata": {"annotations": {"acm-switchover.argoproj.io/paused-by": run_id}},
        "spec": {"syncPolicy": sync_policy},
    }


def main() -> None:
    module = AnsibleModule(argument_spec={}, supports_check_mode=True)
    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_argocd_autosync.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py
git commit -m "feat: add argocd autosync module"
```

---

### Task 2: GitOps Marker Helper and Warning-Only Preflight Integration

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/gitops.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_gitops.py`
- Create: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_gitops.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_gitops.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.gitops import detect_gitops_markers


def test_detect_gitops_markers_flags_argocd_instance():
    markers = detect_gitops_markers({"labels": {"argocd.argoproj.io/instance": "acm"}})
    assert "label:argocd.argoproj.io/instance" in markers


def test_detect_gitops_markers_marks_generic_instance_unreliable():
    markers = detect_gitops_markers({"labels": {"app.kubernetes.io/instance": "something"}})
    assert "label:app.kubernetes.io/instance (UNRELIABLE)" in markers
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_gitops.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement helper and preflight task**

Create `gitops.py` with a direct collection-native port of the marker rules from `lib/gitops_detector.py`.

Create `validate_gitops.yml`:

```yaml
---
- name: Record generic GitOps drift warning
  ansible.builtin.set_fact:
    acm_switchover_validation_results: >-
      {{
        acm_switchover_validation_results
        + [
          {
            "id": "preflight-gitops-warning",
            "severity": "warning",
            "status": "pass",
            "message": "GitOps marker detection is informational only in the collection",
            "details": {"skip_gitops_check": acm_switchover_features.skip_gitops_check | default(false)},
            "recommended_action": "Coordinate with GitOps before mutation if ACM resources are managed declaratively"
          }
        ]
      }}
  when: not (acm_switchover_features.skip_gitops_check | default(false))
```

Import it from `roles/preflight/tasks/main.yml` after input validation and before resource discovery.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_gitops.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/module_utils/gitops.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_gitops.py
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_gitops.yml
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml
git commit -m "feat: add gitops marker helper and preflight warning integration"
```

---

### Task 3: Argocd Manage Role and Resume Playbook

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/defaults/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/main.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml`
- Create: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml`
- Create: `ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml`

- [ ] **Step 1: Add the role defaults**

Create `defaults/main.yml`:

```yaml
---
acm_switchover_features:
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_argocd:
  mode: pause
  run_id: ""
```

- [ ] **Step 2: Implement the role tasks**

Create `tasks/main.yml`:

```yaml
---
- name: Discover Argo CD Applications
  ansible.builtin.import_tasks: discover.yml

- name: Pause autosync
  ansible.builtin.import_tasks: pause.yml
  when: acm_switchover_argocd.mode == 'pause'

- name: Resume autosync
  ansible.builtin.import_tasks: resume.yml
  when: acm_switchover_argocd.mode == 'resume'
```

Create `playbooks/argocd_resume.yml`:

```yaml
---
- name: Resume Argo CD auto-sync after switchover
  hosts: localhost
  gather_facts: false
  roles:
    - role: tomazb.acm_switchover.argocd_manage
      vars:
        acm_switchover_argocd:
          mode: resume
```

- [ ] **Step 3: Wire the role into phase flows**

Import `tomazb.acm_switchover.argocd_manage` from `primary_prep/tasks/main.yml` when `argocd.manage` is true.

Import it from `finalization/tasks/main.yml` with `mode: resume` when `argocd.resume_after_switchover` is true.

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/argocd_manage/
git add ansible_collections/tomazb/acm_switchover/playbooks/argocd_resume.yml
git add ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml
git commit -m "feat: add argocd manage role and resume playbook"
```

---

### Task 4: Integration Tests, Docs, and Verification

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/argocd/pause_and_resume.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`
- Modify: `docs/ansible-collection/parity-matrix.md`

- [ ] **Step 1: Add the integration test**

Create `test_argocd_manage_role.py`:

```python
def test_argocd_pause_and_resume_fixture(run_argocd_fixture):
    completed, summary = run_argocd_fixture("pause_and_resume.yml")
    assert completed.returncode == 0
    assert summary["paused"] >= 1
    assert summary["restored"] >= 1
```

- [ ] **Step 2: Update docs**

Mark `--argocd-manage` and `--argocd-resume-after-switchover` as `dual-supported` in `cli-migration-map.md`.

Update `coexistence.md` to document that generic GitOps warnings remain read-only while Argo CD auto-sync pause/resume is the only supported mutating GitOps integration in the collection.

Update `parity-matrix.md` to mark Argo CD support `dual-supported` only after the integration test passes.

- [ ] **Step 3: Run the full Phase 5 verification suite**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_gitops.py \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/argocd/pause_and_resume.yml
git add ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py
git add ansible_collections/tomazb/acm_switchover/docs/cli-migration-map.md
git add ansible_collections/tomazb/acm_switchover/docs/coexistence.md
git add docs/ansible-collection/parity-matrix.md
git commit -m "docs: mark argocd phase parity"
```
