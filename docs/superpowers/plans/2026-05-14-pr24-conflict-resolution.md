# PR 24 Conflict Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the GitHub conflict on PR #24 by merging `origin/main` into `ansible` and preserving both branches' intended behavior.

**Architecture:** This is a local Git conflict-resolution task, not a feature refactor. The merge should adopt `main`'s centralized `RESTORE_ALREADY_AVAILABLE_MARKER` constant while keeping the PR branch's Ansible/e2e removals intact.

**Tech Stack:** Git, Python, pytest, existing ACM switchover Python CLI and Ansible collection tests.

---

## File Structure

- Modify: `lib/constants.py` to include `RESTORE_ALREADY_AVAILABLE_MARKER`.
- Modify: `modules/activation.py` to import both `PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME` and `RESTORE_ALREADY_AVAILABLE_MARKER`, and to use the marker constant for restore status checks.
- Modify: `modules/preflight/backup_validators.py` to import and use `RESTORE_ALREADY_AVAILABLE_MARKER`.
- Inspect: `tests/e2e/orchestrator.py` to confirm stale `argocd_resume_after_switchover` wiring remains removed.
- Do not modify: `docs/ACM_SWITCHOVER_RUNBOOK.md` or `.claude/skills/**/*.skill.md`.

### Task 1: Start Merge

**Files:**
- Modify through Git merge: `lib/constants.py`
- Modify through Git merge: `modules/activation.py`
- Modify through Git merge: `modules/preflight/backup_validators.py`
- Modify through Git merge: `tests/e2e/orchestrator.py`

- [ ] **Step 1: Confirm clean worktree except committed plan/spec history**

Run: `git status --short --branch`

Expected: branch is `ansible`; no unstaged or staged source changes.

- [ ] **Step 2: Merge current main**

Run: `git merge origin/main`

Expected: merge stops with conflicts in the four known files.

### Task 2: Resolve Restore Marker Conflicts

**Files:**
- Modify: `lib/constants.py`
- Modify: `modules/activation.py`
- Modify: `modules/preflight/backup_validators.py`

- [ ] **Step 1: Resolve `lib/constants.py`**

Ensure this constant exists near the restore constants:

```python
# Restore status message marker for clusters that are already available
# (expected when running consecutive switchovers)
RESTORE_ALREADY_AVAILABLE_MARKER = "already available"
```

- [ ] **Step 2: Resolve `modules/activation.py` imports**

Ensure the constants import includes both names:

```python
    PRE_ACTIVATION_VELERO_MANAGED_CLUSTERS_RESTORE_NAME,
    RESTORE_ALREADY_AVAILABLE_MARKER,
```

- [ ] **Step 3: Resolve `modules/activation.py` restore checks**

Ensure both `FinishedWithErrors` checks use the constant:

```python
if messages and all(RESTORE_ALREADY_AVAILABLE_MARKER in m for m in messages):
```

- [ ] **Step 4: Resolve `modules/preflight/backup_validators.py` imports**

Ensure the constants import includes:

```python
    RESTORE_ALREADY_AVAILABLE_MARKER,
```

- [ ] **Step 5: Resolve `modules/preflight/backup_validators.py` restore check**

Ensure the passive sync `FinishedWithErrors` check uses:

```python
if messages and all(RESTORE_ALREADY_AVAILABLE_MARKER in m for m in messages):
```

### Task 3: Resolve E2E Orchestrator Conflict

**Files:**
- Inspect: `tests/e2e/orchestrator.py`

- [ ] **Step 1: Preserve stale field removal**

Ensure `RunConfig` does not include:

```python
    argocd_resume_after_switchover: bool = False
```

- [ ] **Step 2: Preserve stale pass-through removal**

Ensure the `E2EPhaseHandlers` call does not include:

```python
                argocd_resume_after_switchover=self.config.argocd_resume_after_switchover,
```

### Task 4: Verify and Commit

**Files:**
- Test: `tests/test_activation.py`
- Test: `tests/test_preflight_validators_unit.py`
- Test: `tests/e2e/orchestrator.py`

- [ ] **Step 1: Check for unresolved conflict markers**

Run: `rg -n '<<<<<<<|=======|>>>>>>>' lib modules tests/e2e`

Expected: no output.

- [ ] **Step 2: Run targeted tests**

Run: `pytest tests/test_activation.py tests/test_preflight_validators_unit.py -q`

Expected: passing tests.

- [ ] **Step 3: Run e2e import smoke check**

Run: `python -m py_compile tests/e2e/orchestrator.py`

Expected: no output and exit code 0.

- [ ] **Step 4: Inspect merge diff**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 5: Commit merge resolution**

Run:

```bash
git add lib/constants.py modules/activation.py modules/preflight/backup_validators.py tests/e2e/orchestrator.py
git commit
```

Expected: Git creates the merge commit using its generated merge message.
