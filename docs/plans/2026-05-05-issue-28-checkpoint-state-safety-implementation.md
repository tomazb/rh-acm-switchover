# Issue 28 Checkpoint State Safety Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Ansible checkpoint resume safe by binding checkpoints to the current operation identity, preventing mutation in `validate`/`dry_run`, making writes atomic, quarantining corrupt JSON, and adding downstream-aware `reset_from`.

**Architecture:** Keep checkpoint policy centralized in `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py` and keep file IO plus runtime enforcement in `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`. Limit YAML churn to the checkpoint config surface and the switchover rescue reset path, then prove behavior with targeted unit and scenario tests.

**Tech Stack:** Python, pytest, Ansible action plugins, collection YAML playbooks/role defaults

---

### Task 1: Lock the checkpoint helper contract

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py:1-29`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py:1-50`

**Step 1: Write the failing test** (`@superpowers:test-driven-development`)

Add helper-level tests that describe the new contract before changing the implementation:

```python
from ansible_collections.tomazb.acm_switchover.plugins.module_utils.checkpoint import (
    CheckpointIdentityMismatch,
    build_checkpoint_record,
    build_operation_identity,
    reset_completed_phases_from,
    validate_operation_identity,
)


def test_build_checkpoint_record_uses_schema_2_and_operation_identity():
    identity = build_operation_identity(
        hubs={
            "primary": {"context": "hub-a", "kubeconfig": "/tmp/primary"},
            "secondary": {"context": "hub-b", "kubeconfig": "/tmp/secondary"},
        },
        operation={"method": "passive", "activation_method": "patch"},
        collection_version="1.7.8",
    )

    record = build_checkpoint_record("preflight", {}, operation_identity=identity)

    assert record["schema_version"] == "2.0"
    assert record["operation_identity"] == identity


def test_reset_completed_phases_from_prunes_downstream_phases():
    assert reset_completed_phases_from(
        ["preflight", "primary_prep", "activation", "post_activation", "finalization"],
        "primary_prep",
    ) == ["preflight"]


def test_validate_operation_identity_raises_on_mismatch():
    checkpoint = {"operation_identity": {"primary_context": "hub-a"}}
    expected = {"primary_context": "hub-b"}

    with pytest.raises(CheckpointIdentityMismatch):
        validate_operation_identity(checkpoint, expected)
```

**Step 2: Run test to verify it fails**

Run:

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py -q
```

Expected: FAIL because schema `2.0`, `operation_identity`, identity validation, and downstream reset helpers do not exist yet.

**Step 3: Write minimal implementation**

Extend the helper module with the smallest complete API that satisfies the tests and the approved design:

```python
SCHEMA_VERSION = "2.0"
KNOWN_PHASES = ("preflight", "primary_prep", "activation", "post_activation", "finalization")


class CheckpointIdentityMismatch(Exception):
    """Raised when a checkpoint belongs to a different switchover invocation."""


def build_operation_identity(hubs: dict, operation: dict, collection_version: str | None = None) -> dict:
    primary = hubs.get("primary", {})
    secondary = hubs.get("secondary", {})
    return {
        "primary_context": primary.get("context", ""),
        "secondary_context": secondary.get("context", ""),
        "primary_kubeconfig": primary.get("kubeconfig", ""),
        "secondary_kubeconfig": secondary.get("kubeconfig", ""),
        "method": operation.get("method", "passive"),
        "activation_method": operation.get("activation_method", "patch"),
        "restore_only": bool(operation.get("restore_only", False)),
        "old_hub_action": operation.get("old_hub_action", "secondary"),
        "collection_version": collection_version or "",
    }


def reset_completed_phases_from(completed_phases: list[str], phase: str) -> list[str]:
    phase_index = KNOWN_PHASES.index(phase)
    return [item for item in completed_phases if KNOWN_PHASES.index(item) < phase_index]
```

Also add any helper needed to distinguish unsafe legacy `1.0` checkpoints from safe `2.0` checkpoints; keep that logic pure so the action plugin can call it without duplicating decisions.

**Step 4: Run test to verify it passes**

Run:

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py -q
```

Expected: PASS for all helper tests, including the new schema/identity/reset coverage.

**Step 5: Commit**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
git add ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py \
        ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py
git commit -m "feat: add checkpoint identity helpers"
```

### Task 2: Harden the checkpoint action plugin runtime behavior

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py:1-191`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py:1-774`

**Step 1: Write the failing test** (`@superpowers:test-driven-development`)

Add runtime tests for the acceptance criteria that only the action plugin can enforce:

```python
def test_action_module_validate_mode_does_not_mutate_checkpoint_file(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(json.dumps({"schema_version": "2.0", "completed_phases": ["preflight"]}))

    result = _make_checkpoint_action(
        {
            "phase": "activation",
            "checkpoint": {"enabled": True, "backend": "file", "path": str(checkpoint_file)},
            "status": "pass",
        }
    ).run(task_vars=_task_vars_for_mode("validate"))

    assert result["changed"] is False
    assert json.loads(checkpoint_file.read_text())["completed_phases"] == ["preflight"]


def test_action_module_rejects_identity_mismatch_without_explicit_reset(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "operation_identity": {"primary_context": "old-primary"},
                "completed_phases": ["preflight"],
            }
        )
    )

    result = _make_checkpoint_action(...).run(
        task_vars={
            "acm_switchover_execution": {"mode": "execute"},
            "acm_switchover_hubs": {"primary": {"context": "new-primary"}},
            "acm_switchover_operation": {},
        }
    )

    assert result["failed"] is True
    assert "operation identity does not match" in result["msg"]
```

Also add tests for:

- corrupt JSON quarantine
- atomic temp-file write + `os.replace`
- explicit reset of schema `1.0`
- `reset_from: primary_prep` pruning downstream phases

**Step 2: Run test to verify it fails**

Run:

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q
```

Expected: FAIL because the current plugin only treats `dry_run` as non-mutating, writes directly to the final path, and does not validate operation identity or quarantine corrupt JSON.

**Step 3: Write minimal implementation**

Implement the runtime behavior in one place instead of scattering it across YAML:

```python
execution_mode = execution.get("mode", "dry_run")
is_non_mutating = execution_mode in {"dry_run", "validate"}
expected_identity = build_operation_identity(
    task_vars.get("acm_switchover_hubs") or {},
    task_vars.get("acm_switchover_operation") or {},
    collection_version=task_vars.get("acm_switchover_collection_version"),
)

checkpoint_data = self._load_checkpoint(path)
if checkpoint_data.get("schema_version") == "1.0":
    if checkpoint_requires_explicit_reset(checkpoint_data) and not (reset or reset_from):
        return {"failed": True, "msg": "...explicit reset..."}
elif not (reset or reset_from):
    validate_operation_identity(checkpoint_data, expected_identity)

if reset_from:
    checkpoint_data["completed_phases"] = reset_completed_phases_from(
        checkpoint_data.get("completed_phases", []),
        reset_from,
    )

if is_non_mutating:
    return {"changed": False, "checkpoint": checkpoint_data}
```

For file IO, load with corruption handling and save atomically:

```python
tmp_path = f"{path}.tmp"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
os.replace(tmp_path, path)
```

On corrupt JSON, rename the original file aside to a timestamped `.corrupt-*` name before returning a failure result.

**Step 4: Run test to verify it passes**

Run:

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py -q
```

Expected: PASS for the new non-mutating-mode, identity, reset, corruption, and atomic-write tests.

**Step 5: Commit**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
git add ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py \
        ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py
git commit -m "feat: harden checkpoint phase runtime"
```

### Task 3: Wire the operator surface and resume scenarios

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml:60-71`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml:27-36`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/defaults/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/defaults/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/post_activation/defaults/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/defaults/main.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml:25-34`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py:1-35`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/interrupted_after_activation.yml:1-135`

**Step 1: Write the failing test** (`@superpowers:test-driven-development`)

Extend the scenario layer so the YAML surface proves the feature instead of relying only on unit tests:

```python
def test_resume_from_primary_prep_prunes_downstream_completion(run_checkpoint_fixture):
    completed, checkpoint = run_checkpoint_fixture(
        "interrupted_after_activation.yml",
        pre_completed_phases=["preflight", "primary_prep", "activation", "post_activation", "finalization"],
        checkpoint_overrides={"reset_from": "primary_prep"},
    )

    assert completed.returncode == 0, completed.stderr
    assert checkpoint["completed_phases"] == ["preflight", "primary_prep", "activation", "post_activation", "finalization"]
    assert "primary_prep : Enter checkpointed phase" in completed.stdout
```

Also add one scenario or fixture assertion that `validate` mode does not create or mutate the checkpoint path when checkpointing is enabled.

**Step 2: Run test to verify it fails**

Run:

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q
```

Expected: FAIL because the config surface does not yet expose `reset_from`, and the switchover rescue path still resets only `primary_prep`.

**Step 3: Write minimal implementation**

Update the operator-facing YAML without changing the overall playbook flow:

```yaml
acm_switchover_execution:
  checkpoint:
    enabled: false
    backend: file
    path: .state/switchover.json
    reset: false
    reset_from: ""
```

In `playbooks/switchover.yml`, replace the rescue reset with downstream-aware reset semantics:

```yaml
- name: Reset downstream checkpoint state after Argo CD resume on failure
  tomazb.acm_switchover.checkpoint_phase:
    phase: primary_prep
    checkpoint: "{{ acm_switchover_execution.checkpoint | combine({'reset_from': 'primary_prep'}) }}"
    status: reset
```

If the current scenario fixture/helper needs it, add the smallest fixture override support required to inject `reset_from` during tests.

**Step 4: Run test to verify it passes**

Run:

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q
```

Expected: PASS for resume/reset scenarios, with `reset_from` exposed in defaults/examples and the rescue path using downstream pruning.

**Step 5: Commit**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
git add ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
        ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml \
        ansible_collections/tomazb/acm_switchover/roles/primary_prep/defaults/main.yml \
        ansible_collections/tomazb/acm_switchover/roles/activation/defaults/main.yml \
        ansible_collections/tomazb/acm_switchover/roles/post_activation/defaults/main.yml \
        ansible_collections/tomazb/acm_switchover/roles/finalization/defaults/main.yml \
        ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml \
        ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py \
        ansible_collections/tomazb/acm_switchover/tests/scenario/fixtures/checkpoint/interrupted_after_activation.yml
git commit -m "feat: expose checkpoint reset-from semantics"
```

### Task 4: Run focused verification and close the branch cleanly

**Files:**
- Verify only: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/checkpoint.py`
- Verify only: `ansible_collections/tomazb/acm_switchover/plugins/action/checkpoint_phase.py`
- Verify only: `ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml`
- Verify only: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py`
- Verify only: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py`
- Verify only: `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py`

**Step 1: Run the focused collection verification**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_checkpoint.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_phase_runtime.py \
  ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py -q
```

Expected: PASS with the issue-specific helper, runtime, and scenario coverage green together.

**Step 2: Run the broader collection baseline**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
```

Expected: PASS for the collection unit suite with no regressions outside checkpoint handling.

**Step 3: Run formatting only on touched Python trees if needed**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
source /home/tomaz/sources/rh-acm-switchover/.venv/bin/activate
python -m black ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests
python -m isort ansible_collections/tomazb/acm_switchover/plugins ansible_collections/tomazb/acm_switchover/tests
```

Expected: no formatting diffs after the first pass.

**Step 4: Commit the final polish if verification changed files**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety
git add ansible_collections/tomazb/acm_switchover/plugins \
        ansible_collections/tomazb/acm_switchover/tests \
        ansible_collections/tomazb/acm_switchover/playbooks/switchover.yml \
        ansible_collections/tomazb/acm_switchover/examples/group_vars/all.yml
git commit -m "test: finalize checkpoint safety verification"
```

If this step produces no new diff, skip the commit and keep the branch ready for review.

## Notes for the execution session

- The isolated worktree for this plan is `/home/tomaz/sources/rh-acm-switchover/.worktrees/issue-28-checkpoint-state-safety`.
- The shared repo virtualenv needed one extra dependency for collection tests: `ansible-core==2.15.*`. CI already does this in `.github/workflows/ansible-collection-foundation.yml`, so do not treat that as a product change.
- Do not broaden this work into PR-2/PR-3 parity items; keep the branch scoped to issue #28 acceptance criteria.
