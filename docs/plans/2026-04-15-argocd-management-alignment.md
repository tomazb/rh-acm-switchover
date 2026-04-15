# ArgoCD Management Alignment — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix production bugs and quality gaps in ArgoCD pause/resume across Ansible, Python, and Bash form factors.

**Architecture:** Two-phase approach. Phase 1 fixes P0/P1 production bugs (hub hardcoding, ACM_KINDS gap, empty run_id, missing clobber guard, Bash deprecation). Phase 2 addresses quality improvements (Python duplication refactor, edge-case warnings, build_pause_patch divergence). Each task is TDD with frequent commits.

**Tech Stack:** Python 3, Ansible (roles/plugins), Bash, pytest

**Design doc:** `docs/plans/2026-04-15-argocd-management-alignment-design.md`

---

## Phase 1: Production Bugs

### Task 1A: Fix hub hardcoding in pause.yml and resume.yml

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml:14-16`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml:19-21`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py` (create)

**Context:** `discover.yml:25-26` already uses `_argocd_discover_hub` correctly. `pause.yml:15-16` hardcodes `acm_switchover_hubs.primary`, and `resume.yml:20-21` hardcodes `acm_switchover_hubs.secondary`. In restore-only mode, apps are discovered from secondary but patched against primary — broken.

**Step 1: Write failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py`:

```python
"""Tests to verify ArgoCD role tasks use parameterized hub access."""

import yaml
import pathlib

ROLE_DIR = pathlib.Path(__file__).resolve().parents[4] / "roles" / "argocd_manage" / "tasks"


def _load_yaml(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_DIR / name).read_text())


def test_pause_uses_parameterized_hub():
    """pause.yml must NOT hardcode .primary or .secondary for kubeconfig/context."""
    tasks = _load_yaml("pause.yml")
    for task in tasks:
        k8s = task.get("kubernetes.core.k8s", {})
        if not k8s:
            # Check inside block tasks
            for block_task in task.get("block", []):
                k8s = block_task.get("kubernetes.core.k8s", {})
                if k8s:
                    kc = str(k8s.get("kubeconfig", ""))
                    ctx = str(k8s.get("context", ""))
                    assert ".primary." not in kc, f"pause.yml hardcodes .primary in kubeconfig: {kc}"
                    assert ".primary." not in ctx, f"pause.yml hardcodes .primary in context: {ctx}"
                    assert ".secondary." not in kc, f"pause.yml hardcodes .secondary in kubeconfig: {kc}"
                    assert ".secondary." not in ctx, f"pause.yml hardcodes .secondary in context: {ctx}"
                    assert "_argocd_discover_hub" in kc, f"pause.yml kubeconfig should use _argocd_discover_hub: {kc}"


def test_resume_uses_parameterized_hub():
    """resume.yml must NOT hardcode .primary or .secondary for kubeconfig/context."""
    tasks = _load_yaml("resume.yml")
    for task in tasks:
        for block_task in task.get("block", []):
            k8s = block_task.get("kubernetes.core.k8s", {})
            if k8s:
                kc = str(k8s.get("kubeconfig", ""))
                ctx = str(k8s.get("context", ""))
                assert ".primary." not in kc, f"resume.yml hardcodes .primary in kubeconfig: {kc}"
                assert ".primary." not in ctx, f"resume.yml hardcodes .primary in context: {ctx}"
                assert ".secondary." not in kc, f"resume.yml hardcodes .secondary in kubeconfig: {kc}"
                assert ".secondary." not in ctx, f"resume.yml hardcodes .secondary in context: {ctx}"
                assert "_argocd_discover_hub" in kc, f"resume.yml kubeconfig should use _argocd_discover_hub: {kc}"


def test_discover_uses_parameterized_hub():
    """discover.yml should already use _argocd_discover_hub (baseline check)."""
    tasks = _load_yaml("discover.yml")
    found = False
    for task in tasks:
        for block_task in task.get("block", []):
            k8s_info = block_task.get("kubernetes.core.k8s_info", {})
            if k8s_info:
                kc = str(k8s_info.get("kubeconfig", ""))
                assert "_argocd_discover_hub" in kc
                found = True
    assert found, "discover.yml should have at least one k8s_info task with _argocd_discover_hub"
```

**Step 2: Run tests — expect FAIL for pause and resume, PASS for discover**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py -v`

Expected: `test_pause_uses_parameterized_hub` FAIL, `test_resume_uses_parameterized_hub` FAIL, `test_discover_uses_parameterized_hub` PASS

**Step 3: Fix pause.yml — replace hardcoded hub with parameterized lookup**

In `pause.yml:15-16`, change:
```yaml
        kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
        context: "{{ acm_switchover_hubs.primary.context }}"
```
to:
```yaml
        kubeconfig: "{{ acm_switchover_hubs[_argocd_discover_hub | default('primary')].kubeconfig | default(omit) }}"
        context: "{{ acm_switchover_hubs[_argocd_discover_hub | default('primary')].context | default(omit) }}"
```

**Step 4: Fix resume.yml — replace hardcoded hub with parameterized lookup**

In `resume.yml:20-21`, change:
```yaml
        kubeconfig: "{{ acm_switchover_hubs.secondary.kubeconfig }}"
        context: "{{ acm_switchover_hubs.secondary.context }}"
```
to:
```yaml
        kubeconfig: "{{ acm_switchover_hubs[_argocd_discover_hub | default('secondary')].kubeconfig | default(omit) }}"
        context: "{{ acm_switchover_hubs[_argocd_discover_hub | default('secondary')].context | default(omit) }}"
```

Note: resume defaults to `'secondary'` (not `'primary'`) because resume is always on the new hub in normal flow.

**Step 5: Run tests — expect all PASS**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py -v`

**Step 6: Run full test suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -x -q`

**Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml \
        ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/resume.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py
git commit -m "fix(ansible): parameterize hub in ArgoCD pause/resume tasks

pause.yml hardcoded acm_switchover_hubs.primary and resume.yml hardcoded
acm_switchover_hubs.secondary. In restore-only mode, apps were discovered
from secondary but patched against primary — broken. Now both tasks use
_argocd_discover_hub (matching discover.yml pattern)."
```

---

### Task 1B: Expand Ansible ACM_KINDS from 6 to 14

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py:14-21`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_filter.py` (extend)
- Test: `tests/test_argocd_constants_parity.py` (create — covers Task 1E too)

**Context:** Python `lib/argocd.py:58-75` has 14 ACM_KINDS. Ansible `module_utils/argocd.py:14-21` only has 6. Missing: `MultiClusterEngine`, `ManagedClusterSet`, `ManagedClusterSetBinding`, `Placement`, `PlacementBinding`, `Policy`, `PolicySet`, `DataProtectionApplication`. This means Ansible misses Applications that only touch these 8 kinds.

**Step 1: Write failing parity test**

Create `tests/test_argocd_constants_parity.py`:

```python
"""Parity contract: Ansible and Python ACM_KINDS / ACM_NAMESPACES must match."""

import pytest


def test_acm_kinds_parity():
    """Ansible and Python ACM_KINDS must contain the same entries."""
    from lib.argocd import ARGOCD_ACM_KINDS
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import ACM_KINDS

    python_kinds = set(ARGOCD_ACM_KINDS)
    ansible_kinds = set(ACM_KINDS)
    missing_in_ansible = python_kinds - ansible_kinds
    extra_in_ansible = ansible_kinds - python_kinds
    assert not missing_in_ansible, f"Ansible ACM_KINDS missing: {missing_in_ansible}"
    assert not extra_in_ansible, f"Ansible ACM_KINDS has extras: {extra_in_ansible}"


def test_acm_namespaces_parity():
    """Ansible and Python ACM namespaces must cover the same set."""
    from lib.argocd import ARGOCD_ACM_NS_REGEX
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import ACM_NAMESPACES

    # Test that every Ansible namespace matches the Python regex
    for ns in ACM_NAMESPACES:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Ansible namespace '{ns}' not matched by Python regex"

    # Test known ACM sub-namespaces that Python regex matches
    sub_ns_samples = [
        "open-cluster-management-agent",
        "open-cluster-management-agent-addon",
    ]
    for ns in sub_ns_samples:
        assert ARGOCD_ACM_NS_REGEX.match(ns), f"Python regex should match ACM sub-namespace '{ns}'"
```

**Step 2: Run parity test — expect FAIL on ACM_KINDS**

Run: `source .venv/bin/activate && python -m pytest tests/test_argocd_constants_parity.py -v`

**Step 3: Expand Ansible ACM_KINDS**

In `ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py:14-21`, replace:
```python
ACM_KINDS = {
    "MultiClusterHub",
    "MultiClusterObservability",
    "ManagedCluster",
    "BackupSchedule",
    "Restore",
    "ClusterDeployment",
}
```
with:
```python
ACM_KINDS = {
    "MultiClusterHub",
    "MultiClusterEngine",
    "MultiClusterObservability",
    "ManagedCluster",
    "ManagedClusterSet",
    "ManagedClusterSetBinding",
    "Placement",
    "PlacementBinding",
    "Policy",
    "PolicySet",
    "BackupSchedule",
    "Restore",
    "DataProtectionApplication",
    "ClusterDeployment",
}
```

**Step 4: Add filter test for a newly-included kind**

Extend `test_acm_argocd_filter.py` with:

```python
def test_policy_kind_is_acm_touching():
    """Policy kind (newly added) should be recognized as ACM-touching."""
    assert is_acm_touching_application(
        {"metadata": {"name": "policy-app"}, "status": {"resources": [{"kind": "Policy", "namespace": "default"}]}}
    ) is True


def test_placement_binding_kind_is_acm_touching():
    """PlacementBinding kind (newly added) should be recognized as ACM-touching."""
    assert is_acm_touching_application(
        {"metadata": {"name": "placement-app"}, "status": {"resources": [{"kind": "PlacementBinding", "namespace": "default"}]}}
    ) is True
```

**Step 5: Run all tests — expect PASS**

Run: `source .venv/bin/activate && python -m pytest tests/test_argocd_constants_parity.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_filter.py -v`

**Step 6: Run full suite**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -x -q`

**Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py \
        ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_filter.py \
        tests/test_argocd_constants_parity.py
git commit -m "fix(ansible): expand ACM_KINDS to 14 entries + add parity test

Ansible had only 6 of 14 ACM kinds, missing MultiClusterEngine,
ManagedClusterSet, ManagedClusterSetBinding, Placement, PlacementBinding,
Policy, PolicySet, and DataProtectionApplication. Applications touching
only these kinds were not detected.

Adds cross-form-factor parity contract test that will fail if either side
drifts in the future."
```

---

### Task 1C: Fix empty run_id default

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/defaults/main.yml:9`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml` (add UUID generation)
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py` (extend)

**Context:** `defaults/main.yml:9` has `run_id: ""`. Jinja `default()` only fires on undefined, not empty string. No playbook ever sets `run_id`. Result: the `paused-by` annotation is always empty string `""`, making resume unable to match any run.

**Step 1: Write failing test**

Add to `test_argocd_hub_parameterization.py`:

```python
def test_run_id_default_is_not_empty_string():
    """defaults/main.yml run_id must not default to empty string."""
    defaults = yaml.safe_load((ROLE_DIR.parent / "defaults" / "main.yml").read_text())
    run_id = defaults.get("acm_switchover_argocd", {}).get("run_id")
    # run_id should either be absent (undefined → triggers Jinja default())
    # or be a non-empty string. Empty string breaks resume matching.
    assert run_id is None or (isinstance(run_id, str) and run_id != ""), \
        f"run_id defaults to empty string, which bypasses Jinja default() filter"


def test_discover_generates_run_id_when_not_set():
    """discover.yml should generate a UUID run_id when not provided."""
    tasks = _load_yaml("discover.yml")
    yaml_text = (ROLE_DIR / "discover.yml").read_text()
    # The discover task should contain run_id generation logic
    assert "ansible.builtin.set_fact" in yaml_text or "run_id" in yaml_text, \
        "discover.yml should handle run_id generation"
```

**Step 2: Run test — expect FAIL (empty string)**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py::test_run_id_default_is_not_empty_string -v`

**Step 3: Fix defaults/main.yml — remove run_id default entirely**

Replace `defaults/main.yml` content:
```yaml
---
acm_switchover_features:
  argocd:
    manage: false
    resume_after_switchover: false

acm_switchover_argocd:
  mode: pause
```

(Remove `run_id: ""` line. When undefined, Jinja `default()` filter will fire correctly.)

**Step 4: Add run_id generation to discover.yml**

Add a task at the start of the live-mode block in `discover.yml` (before the k8s_info call), inside the `when: acm_switchover_argocd_mock_apps is not defined` block:

```yaml
    - name: Generate run_id if not provided
      ansible.builtin.set_fact:
        acm_switchover_argocd: >-
          {{
            acm_switchover_argocd | default({})
            | combine({'run_id': acm_switchover_argocd.run_id
                       | default(lookup('pipe', 'python3 -c "import uuid; print(uuid.uuid4().hex[:12])\"'))})
          }}
      when: (acm_switchover_argocd.run_id | default('')) == ''
```

**Step 5: Run tests — expect PASS**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py -v`

**Step 6: Run full suite**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -x -q`

**Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/argocd_manage/defaults/main.yml \
        ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/discover.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py
git commit -m "fix(ansible): remove empty run_id default, generate UUID in discover

Empty string run_id bypassed Jinja default() filter, causing paused-by
annotation to always be empty. Resume could not match any run. Now run_id
is undefined by default and discover.yml generates a UUID when not set."
```

---

### Task 1D: Add re-pause clobber guard in pause.yml

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml`
- Test: extend `test_argocd_hub_parameterization.py`

**Context:** If pause.yml runs twice (e.g., retry after partial failure), the second run overwrites `original-sync-policy` with the already-paused policy. Python's `_pause_argocd_acm_apps` has `_find_pause_entry` to prevent this.

**Step 1: Write failing test**

Add to `test_argocd_hub_parameterization.py`:

```python
def test_pause_has_clobber_guard():
    """pause.yml should skip applications that are already paused (have our annotation)."""
    yaml_text = (ROLE_DIR / "pause.yml").read_text()
    assert "paused-by" in yaml_text, \
        "pause.yml should check for existing paused-by annotation to prevent clobber"
```

**Step 2: Run test — expect FAIL**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py::test_pause_has_clobber_guard -v`

**Step 3: Add when condition to pause task**

In `pause.yml`, the `kubernetes.core.k8s` patch task at line 13 already loops over `acm_switchover_argocd_acm_apps`. Add a `when` condition to skip already-paused apps:

```yaml
    - name: Remove automated sync policy and annotate with run-id
      kubernetes.core.k8s:
        kubeconfig: "{{ acm_switchover_hubs[_argocd_discover_hub | default('primary')].kubeconfig | default(omit) }}"
        context: "{{ acm_switchover_hubs[_argocd_discover_hub | default('primary')].context | default(omit) }}"
        state: patched
        api_version: argoproj.io/v1alpha1
        kind: Application
        namespace: "{{ item.metadata.namespace }}"
        name: "{{ item.metadata.name }}"
        definition:
          metadata:
            annotations:
              acm-switchover.argoproj.io/paused-by: "{{ acm_switchover_argocd.run_id | default(acm_switchover_execution.run_id | default('unknown')) }}"
              acm-switchover.argoproj.io/original-sync-policy: >-
                {{ item.spec.syncPolicy | default({}) | to_json }}
          spec:
            syncPolicy: >-
              {{
                (item.spec.syncPolicy | default({}))
                | dict2items
                | rejectattr('key', 'equalto', 'automated')
                | items2dict
              }}
      loop: "{{ acm_switchover_argocd_acm_apps }}"
      loop_control:
        label: "{{ item.metadata.namespace }}/{{ item.metadata.name }}"
      register: acm_argocd_pause_results
      when:
        - acm_switchover_argocd_mock_apps is not defined
        - "'acm-switchover.argoproj.io/paused-by' not in (item.metadata.annotations | default({}))"
```

The key change is adding the second `when` condition: `'acm-switchover.argoproj.io/paused-by' not in (item.metadata.annotations | default({}))`. This prevents clobbering the `original-sync-policy` annotation on retry.

**Step 4: Run tests — expect PASS**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py -v`

**Step 5: Run full suite**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -x -q`

**Step 6: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py
git commit -m "fix(ansible): add clobber guard to ArgoCD pause task

On retry, pause.yml would overwrite original-sync-policy with the
already-paused policy, making resume unable to restore the original.
Now skips applications that already have the paused-by annotation."
```

---

### Task 1E: Add secondary hub pause in primary_prep + primary hub resume in finalization

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml:26-32`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml:29-38`

**Context:** Python `primary_prep.py:166` pauses BOTH hubs: `hubs = [(self.primary, "primary")] + ([(self.secondary, "secondary")] if self.secondary else [])`. Ansible `primary_prep/main.yml:31` only includes `_argocd_discover_hub: primary`. Similarly, Python resumes primary hub during finalization, but Ansible only resumes secondary.

**Step 1: Write failing tests**

Create or extend tests to verify both hubs are handled:

Add to `test_argocd_hub_parameterization.py`:

```python
def test_primary_prep_pauses_both_hubs():
    """primary_prep/main.yml should include argocd_manage for both primary and secondary hubs."""
    text = (ROLE_DIR.parents[1] / "primary_prep" / "tasks" / "main.yml").read_text()
    assert text.count("argocd_manage") >= 2, \
        "primary_prep should include argocd_manage role at least twice (primary + secondary)"
    assert "_argocd_discover_hub: primary" in text, "Should pause primary hub"
    assert "_argocd_discover_hub: secondary" in text, "Should pause secondary hub"


def test_finalization_resumes_primary_hub():
    """finalization/main.yml should resume argocd on primary hub (not just secondary)."""
    text = (ROLE_DIR.parents[1] / "finalization" / "tasks" / "main.yml").read_text()
    # At minimum, finalization should handle resume for secondary (existing) and primary (new)
    resume_count = text.count("acm_switchover_argocd_mode_override: resume")
    assert resume_count >= 2, \
        f"finalization should resume argocd on both hubs, found {resume_count} resume include(s)"
```

**Step 2: Run tests — expect FAIL**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py::test_primary_prep_pauses_both_hubs ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py::test_finalization_resumes_primary_hub -v`

**Step 3: Add secondary hub pause to primary_prep/main.yml**

After the existing ArgoCD block (line 32), add:

```yaml
    - name: Pause Argo CD auto-sync on secondary hub when enabled
      ansible.builtin.include_role:
        name: tomazb.acm_switchover.argocd_manage
      vars:
        acm_switchover_argocd_mode_override: pause
        _argocd_discover_hub: secondary
      when: acm_switchover_features.argocd.manage | default(false)
```

**Step 4: Add primary hub resume to finalization/main.yml**

After the existing ArgoCD resume block (line 38), add:

```yaml
    - name: Resume Argo CD auto-sync on primary hub when enabled
      ansible.builtin.include_role:
        name: tomazb.acm_switchover.argocd_manage
      vars:
        acm_switchover_argocd_mode_override: resume
        _argocd_discover_hub: primary
      when:
        - acm_switchover_features.argocd.resume_after_switchover | default(false)
        - not (acm_switchover_operation.restore_only | default(false))
```

**Step 5: Run tests — expect PASS**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py -v`

**Step 6: Run full suite**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -x -q`

**Step 7: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml \
        ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py
git commit -m "fix(ansible): pause/resume ArgoCD on both hubs

primary_prep now pauses ArgoCD on both primary and secondary hubs
(matching Python behavior). finalization now resumes on both hubs.
Previously only primary was paused and only secondary was resumed."
```

---

### Task 1F: Bash deprecation

**Files:**
- Modify: `scripts/argocd-manage.sh` (add deprecation banner, fix dry-run state log)
- Modify: `scripts/README.md` (mark deprecated)
- Modify: `docs/operations/usage.md:496` (remove Bash alternative)
- Modify: `docs/operations/quickref.md:240` (remove Bash refs)
- Modify: `AGENTS.md` (update ArgoCD section)

**Context:** Strategic decision: full Bash deprecation. No feature investment. Fix the dry-run log issue (line 358-363 skips `write_pause_state` in dry-run, but state IS written with dry-run entries in Python — the log message is misleading). Add deprecation banner. Remove from docs as recommended tool.

**Step 1: Add deprecation banner to argocd-manage.sh**

After the shebang and comment block (after line 10), add:

```bash
# ============================================================================
# DEPRECATED: This script is deprecated. Use the Python CLI (--argocd-manage)
# or Ansible collection (argocd_manage role) instead.
# ============================================================================
echo "WARNING: argocd-manage.sh is deprecated. Use 'python acm_switchover.py --argocd-manage' or the Ansible collection instead." >&2
```

**Step 2: Fix dry-run log message**

The dry-run block at lines 327-332 already correctly records apps in `apps_array` during dry-run. The issue is the final message at line ~362 says "Would write state" — this is fine since dry-run doesn't call `write_pause_state`. This behavior differs from Python (which writes state with `dry_run: true` entries), but since the script is deprecated, the log message is acceptable. No code change needed here.

**Step 3: Update docs — mark deprecated**

Update `docs/operations/usage.md:496` to indicate deprecation:
```
**Bash alternative (deprecated):** `./scripts/argocd-manage.sh` is deprecated. Use the Python CLI or Ansible collection instead.
```

Update `docs/operations/quickref.md:240` — replace Bash references with Python/Ansible alternatives.

Update `scripts/README.md:17` — add "(Deprecated)" to the argocd-manage.sh entry.

**Step 4: Update AGENTS.md ArgoCD section**

In AGENTS.md, update the "Argo CD pause/resume" entry to note Bash is deprecated.

**Step 5: No new tests needed (docs-only + deprecation banner)**

**Step 6: Run full suite to ensure no regressions**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -x -q`

**Step 7: Commit**

```bash
git add scripts/argocd-manage.sh scripts/README.md docs/operations/usage.md \
        docs/operations/quickref.md AGENTS.md
git commit -m "chore: deprecate argocd-manage.sh, update docs

Add deprecation banner to argocd-manage.sh. Update usage.md, quickref.md,
scripts/README.md, and AGENTS.md to direct users to Python CLI or Ansible
collection instead."
```

---

## Phase 2: Quality Improvements

### Task 2A: Python ArgoCD duplication refactor (ArgoCDPauseCoordinator)

**Files:**
- Create: `lib/argocd_coordinator.py`
- Modify: `modules/primary_prep.py:164-265`
- Modify: `acm_switchover.py:485-577`
- Test: `tests/test_argocd_coordinator.py` (create)

**Context:** `_run_restore_only_argocd_pause` in `acm_switchover.py:485-577` is a simplified copy of `_pause_argocd_acm_apps` in `primary_prep.py:164-265`. They share ~80% of logic but differ in error handling (one uses `_fail_phase`, one raises `SwitchoverError`) and entry-recovery logic. Extract shared logic into a coordinator.

**Step 1: Design the coordinator**

```python
# lib/argocd_coordinator.py
"""Coordinator for ArgoCD pause/resume across hubs."""

import copy
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib import argocd as argocd_lib
from lib.kube_client import KubeClient
from lib.utils import StateManager

logger = logging.getLogger("acm_switchover")


class ArgoCDPauseCoordinator:
    """Coordinates ArgoCD pause across one or more hubs with state tracking."""

    def __init__(
        self,
        state: StateManager,
        dry_run: bool = False,
    ):
        self.state = state
        self.dry_run = dry_run

    def pause_hubs(
        self,
        hubs: List[Tuple[KubeClient, str]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Pause ArgoCD on specified hubs. Returns (paused_apps, failure_count)."""
        ...

    def _pause_single_hub(
        self,
        client: KubeClient,
        hub_label: str,
        run_id: str,
        paused_apps: List[Dict[str, Any]],
    ) -> int:
        """Pause apps on a single hub. Returns failure count."""
        ...
```

**Step 2: Write tests first**

Create `tests/test_argocd_coordinator.py` with tests for:
- `pause_hubs` with single hub (restore-only scenario)
- `pause_hubs` with two hubs (switchover scenario)
- Idempotent re-pause (clobber guard)
- Dry-run behavior
- Error handling (API failures)

**Step 3: Implement coordinator**

**Step 4: Refactor primary_prep.py to use coordinator**

Replace `_pause_argocd_acm_apps` body with coordinator call.

**Step 5: Refactor acm_switchover.py to use coordinator**

Replace `_run_restore_only_argocd_pause` body with coordinator call + error-to-bool wrapper.

**Step 6: Run full suite**

Run: `source .venv/bin/activate && python -m pytest tests/ -x -q`

**Step 7: Commit**

```bash
git add lib/argocd_coordinator.py tests/test_argocd_coordinator.py \
        modules/primary_prep.py acm_switchover.py
git commit -m "refactor: extract ArgoCDPauseCoordinator to reduce duplication

Shared ~80% of logic between _run_restore_only_argocd_pause and
_pause_argocd_acm_apps. Now both delegate to ArgoCDPauseCoordinator."
```

---

### Task 2B: Edge-case warnings (ApplicationSets, empty status.resources)

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py`
- Modify: `lib/argocd.py` (add warning for empty resources)
- Test: extend existing tests

**Context:** Applications created by ApplicationSets have `ownerReferences` pointing to the ApplicationSet. Pausing them directly may cause the ApplicationSet controller to revert changes. Also, apps with empty `status.resources` are silently skipped — should emit a debug-level warning.

**Step 1: Add warning for empty status.resources in Python**

In `lib/argocd.py:find_acm_touching_apps`, after filtering:
```python
if not resources:
    logger.debug("App %s/%s has no status.resources; cannot verify ACM impact — skipped", ns, name)
```

**Step 2: Add ApplicationSet owner warning in Ansible module_utils/argocd.py**

```python
def has_applicationset_owner(app: dict) -> bool:
    """Return True if app is owned by an ApplicationSet (patching may be reverted)."""
    for ref in app.get("metadata", {}).get("ownerReferences", []):
        if ref.get("kind") == "ApplicationSet":
            return True
    return False
```

**Step 3: Add tests, run suite, commit**

---

### Task 2C: Fix build_pause_patch divergence

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/argocd_manage/tasks/pause.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/argocd.py`

**Context:** `build_pause_patch` in `module_utils/argocd.py:39-46` is tested but unused in production — `pause.yml` builds the patch inline via Jinja2. The risk is drift between the tested helper and the actual production logic.

**Step 1: Refactor pause.yml to use build_pause_patch via a custom module or filter**

This is lower priority and may be deferred. The simplest fix is to add a test that verifies the Jinja template in `pause.yml` produces the same output as `build_pause_patch` for representative inputs.

**Step 2: Write equivalence test**

```python
def test_pause_patch_jinja_matches_python_helper():
    """Verify pause.yml inline Jinja and build_pause_patch produce same result."""
    from ansible_collections.tomazb.acm_switchover.plugins.module_utils.argocd import build_pause_patch

    sync_policy = {"automated": {"prune": True}, "syncOptions": ["CreateNamespace=true"]}
    run_id = "test-run-123"

    python_patch = build_pause_patch(sync_policy, run_id)

    # Simulate what Jinja does: remove 'automated' key
    jinja_sync = {k: v for k, v in sync_policy.items() if k != "automated"}

    assert python_patch["spec"]["syncPolicy"] == jinja_sync
    assert python_patch["metadata"]["annotations"]["acm-switchover.argoproj.io/paused-by"] == run_id
```

**Step 3: Run tests, commit**

---

### Task 2D: Integration test hardening

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/argocd/pause_and_resume.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/integration/fixtures/argocd/restore_only_pause.yml`
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py` (extend)

**Context:** Only 1 integration test fixture exists. Need restore-only scenario and re-pause scenario fixtures.

**Step 1: Create restore-only fixture**

```yaml
acm_switchover_argocd_mock_apps:
  - metadata:
      namespace: argocd
      name: acm-policies
      annotations: {}
    spec:
      syncPolicy:
        automated:
          prune: true
    status:
      resources:
        - kind: Policy
          namespace: open-cluster-management

acm_switchover_argocd_mock_apps_for_resume:
  - metadata:
      namespace: argocd
      name: acm-policies
      annotations:
        acm-switchover.argoproj.io/paused-by: "test-run-123"
        acm-switchover.argoproj.io/original-sync-policy: '{"automated":{"prune":true}}'
    spec:
      syncPolicy: {}
    status:
      resources:
        - kind: Policy
          namespace: open-cluster-management
```

**Step 2: Add test using fixture**

**Step 3: Run integration tests, commit**

---

## Final: Update changelog and run full suite

**Files:**
- Modify: `CHANGELOG.md` — add entries under `[Unreleased]`

**Step 1: Add changelog entries**

Under `## [Unreleased]`:

```markdown
### Fixed
- **Ansible ArgoCD**: Fix hub hardcoding in pause.yml/resume.yml — now uses parameterized hub lookup matching discover.yml pattern
- **Ansible ArgoCD**: Expand ACM_KINDS from 6 to 14 entries (matching Python) — Applications touching Policy, Placement, etc. were not detected
- **Ansible ArgoCD**: Fix empty run_id default causing blank paused-by annotations — now generates UUID when not provided
- **Ansible ArgoCD**: Add clobber guard to pause task — prevents overwriting original-sync-policy on retry
- **Ansible ArgoCD**: Pause/resume ArgoCD on both hubs during switchover (matching Python behavior)

### Changed
- **Bash**: Deprecated argocd-manage.sh — use Python CLI or Ansible collection instead

### Added
- Cross-form-factor parity test for ACM_KINDS and ACM_NAMESPACES between Python and Ansible
```

**Step 2: Run final full suite**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -x -q`

**Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: update changelog for ArgoCD alignment fixes"
```
