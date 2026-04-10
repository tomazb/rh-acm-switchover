# Ansible Collection Rewrite Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional persistent checkpoint backend to the collection so long-running switchovers can resume safely after interruption without allowing concurrent active runs on the same workflow state.

**Architecture:** Phase 4 extends the Phase 1 checkpoint foundation into a complete runtime feature: a concrete `acm_checkpoint` module, richer action-plugin coordination, lock ownership reporting, and scenario tests that prove resume safety across execution phases. The default path remains Ansible-native idempotency; checkpointing is opt-in and must fail closed on stale or conflicting lock state.

**Tech Stack:** Ansible Collection modules and action plugins, Python 3.10+, `ansible-core >= 2.15`, pytest, `ansible-playbook`, `ansible-test sanity`

This plan assumes Phase 3 is complete first so checkpoint hooks can wrap real switchover phases instead of only stub tasks.

---

## File Structure

```text
ansible_collections/tomazb/acm_switchover/
  plugins/
    module_utils/
      checkpoint.py                           - Expand into authoritative schema, lock owner contract, and retention metadata
    modules/
      acm_checkpoint.py                       - Module API for read/write/reset/complete operations
    action/
      checkpoint_phase.py                     - Phase enter/exit coordination with lock checks
      write_artifact.py                       - Artifact writer that stores report refs in checkpoint payloads
  roles/
    preflight/tasks/main.yml                  - Optional checkpoint hooks at phase boundaries
    primary_prep/tasks/main.yml
    activation/tasks/main.yml
    post_activation/tasks/main.yml
    finalization/tasks/main.yml
  tests/
    unit/
      plugins/
        modules/
          test_acm_checkpoint.py
        action/
          test_checkpoint_phase_runtime.py
    scenario/
      fixtures/
        checkpoint/
          interrupted_after_activation.yml
      test_checkpoint_resume.py
  docs/
    artifact-schema.md                        - Add checkpoint and report-ref semantics
    coexistence.md                            - Document compatibility with Python checkpoint handoff
docs/ansible-collection/
  parity-matrix.md                            - Promote checkpoint compatibility rows
```

## Environment Setup

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -v
```

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -v
```

---

## Phase 4: Optional Checkpoint Backend

### Task 1: Checkpoint Module API (TDD)

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`

- [ ] **Step 1: Write the failing tests**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.modules.acm_checkpoint import (
    build_checkpoint_record,
    should_resume_phase,
)


def test_build_checkpoint_record_sets_schema_and_phase():
    record = build_checkpoint_record("activation", {"method": "passive"})
    assert record["schema_version"] == "1.0"
    assert record["phase"] == "activation"


def test_should_resume_phase_skips_completed_phase():
    assert should_resume_phase(
        checkpoint={"completed_phases": ["preflight", "primary_prep"]},
        phase="primary_prep",
    ) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from ansible.module_utils.basic import AnsibleModule


def build_checkpoint_record(phase: str, operational_data: dict) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "1.0",
        "phase": phase,
        "completed_phases": [],
        "operational_data": operational_data,
        "errors": [],
        "report_refs": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def should_resume_phase(checkpoint: dict, phase: str) -> bool:
    return phase not in checkpoint.get("completed_phases", [])


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "phase": {"type": "str", "required": True},
            "operational_data": {"type": "dict", "default": {}},
        },
        supports_check_mode=True,
    )
    module.exit_json(changed=False, checkpoint=build_checkpoint_record(module.params["phase"], module.params["operational_data"]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/modules/acm_checkpoint.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py
git commit -m "feat: add checkpoint module api"
```

---

### Task 2: Runtime Action-Plugin Coordination

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/write_artifact.py`
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`

- [ ] **Step 1: Write the failing test**

Create `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`:

```python
from ansible_collections.tomazb.acm_switchover.plugins.action.checkpoint_phase import (
    build_phase_transition,
)


def test_build_phase_transition_marks_completion():
    transition = build_phase_transition(
        checkpoint={"completed_phases": ["preflight"]},
        phase="activation",
        status="pass",
    )
    assert transition["completed_phases"] == ["preflight", "activation"]
    assert transition["phase_status"] == "pass"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement runtime helpers**

Update `checkpoint_phase.py` to add:

```python
def build_phase_transition(checkpoint: dict, phase: str, status: str) -> dict:
    completed = list(checkpoint.get("completed_phases", []))
    if status == "pass" and phase not in completed:
        completed.append(phase)
    return {
        "completed_phases": completed,
        "phase_status": status,
    }
```

Update `write_artifact.py` to also return a `report_ref` object:

```python
def build_report_ref(path: str, phase: str) -> dict:
    return {"phase": phase, "path": path, "kind": "json-report"}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py
git add ansible_collections/tomazb/acm_switchover/plugins/action/write_artifact.py
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py
git commit -m "feat: expand checkpoint action plugin runtime helpers"
```

---

### Task 3: Wire Optional Checkpointing Into Phase Roles

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml`

- [ ] **Step 1: Add checkpoint enter hooks**

At the top of each phase role `main.yml`, add:

```yaml
- name: Enter checkpointed phase
  tomazb.acm_switchover.checkpoint_phase:
    phase: activation
    checkpoint: "{{ acm_switchover_execution.checkpoint | default({}) }}"
    status: enter
  when: acm_switchover_execution.checkpoint.enabled | default(false)
```

- [ ] **Step 2: Add checkpoint completion hooks**

At the bottom of each phase role `main.yml`, add:

```yaml
- name: Mark checkpoint phase completion
  tomazb.acm_switchover.checkpoint_phase:
    phase: activation
    checkpoint: "{{ acm_switchover_execution.checkpoint | default({}) }}"
    status: pass
    report_ref: "{{ acm_switchover_activation_result.path | default(omit) }}"
  when: acm_switchover_execution.checkpoint.enabled | default(false)
```

- [ ] **Step 3: Add fail-path checkpoint updates**

Wrap each role body in a `block` / `rescue` and call:

```yaml
- name: Mark checkpoint failure
  tomazb.acm_switchover.checkpoint_phase:
    phase: activation
    checkpoint: "{{ acm_switchover_execution.checkpoint | default({}) }}"
    status: fail
    error: "{{ ansible_failed_result.msg | default('unknown failure') }}"
```

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/roles/activation/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/roles/post_activation/tasks/main.yml
git add ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml
git commit -m "feat: wire optional checkpoint hooks into phase roles"
```

---

### Task 4: Resume Scenario Coverage, Docs, and Verification

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/interrupted_after_activation.yml`
- Create: `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/coexistence.md`
- Modify: `docs/ansible-collection/parity-matrix.md`

- [ ] **Step 1: Add the scenario test**

Create `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py`:

```python
def test_resume_skips_completed_phases_and_runs_remaining(run_checkpoint_fixture):
    completed, checkpoint = run_checkpoint_fixture("interrupted_after_activation.yml")
    assert completed.returncode == 0
    assert checkpoint["completed_phases"] == ["preflight", "primary_prep", "activation", "post_activation", "finalization"]
```

- [ ] **Step 2: Update docs**

Update `artifact-schema.md` with:

```markdown
## Checkpoint Contract

- `completed_phases`
- `operational_data`
- `errors`
- `report_refs`
- `locked_by`
- `updated_at`
```

Update `coexistence.md` to document when collection checkpoint payloads can be translated from the Python state file versus when a fresh collection checkpoint is required.

Update `parity-matrix.md` to mark checkpoint compatibility as `dual-supported` only after the scenario passes.

- [ ] **Step 3: Run the full Phase 4 verification suite**

```bash
cd /home/tomaz/sources/rh-acm-switchover
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/interrupted_after_activation.yml
git add ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py
git add ansible_collections/tomazb/acm_switchover/docs/artifact-schema.md
git add ansible_collections/tomazb/acm_switchover/docs/coexistence.md
git add docs/ansible-collection/parity-matrix.md
git commit -m "feat: add checkpoint resume scenarios and docs"
```
