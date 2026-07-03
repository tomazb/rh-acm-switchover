# PR 38: Native Check-Mode `changed` Surfacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Published `acm_switchover_pause_backups_result.changed` and `acm_switchover_restore_activation_result.changed` report the truthful would-change verdict under native `ansible-playbook --check` with `mode: execute` (R2-M1 part 2, decision (a) wiring).

**Architecture:** Per the approved design (`docs/superpowers/specs/2026-07-03-pr38-checkmode-changed-surfacing-design.md`): make the two plan modules' `changed` mode-independent (they mutate nothing), extend both role fallbacks to fire on `ansible_check_mode` like they already do on `mode: dry_run`, and clarify the contract sentence in `variable-reference.md`. Red-first via the two existing tests that pin the suppressed behavior, plus repo-style text-assert contract tests on the role YAML.

**Tech Stack:** Python 3, pytest, YAML/Jinja role tasks, black/isort (line-length 120).

## Global Constraints

- `black --line-length 120` / `isort --profile black --line-length 120` on touched Python files.
- No execute-mode or dry-run behavior change; only check-mode reporting.
- Base branch: `ansible` @ `5c2b24e0`; PR branch `fix/thermos-38-checkmode-changed-surfacing`.

---

### Task 1: Red-first — flip plugin tests, add contract asserts

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py:175-183`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py:257-283`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py` (append)
- Create: `ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_activation_check_mode_contract.py`

- [ ] **Step 1: Flip the backup_schedule test**

Rename `test_run_module_check_mode_returns_planned_pause_without_change` to
`test_run_module_check_mode_reports_would_change_for_planned_pause` and flip:

```python
    assert result["exit"]["changed"] is True
```

(other assertions unchanged — plan content and input immutability stay).

- [ ] **Step 2: Flip the restore_info test**

Rename `test_run_module_check_mode_returns_planned_operation_without_change` to
`test_run_module_check_mode_reports_would_change_for_planned_operation` and flip:

```python
    assert result["exit"]["changed"] is True
```

- [ ] **Step 3: Append role-contract asserts**

To `test_backup_schedule_persistence.py`:

```python
def test_pause_backups_published_changed_covers_native_check_mode():
    """--check with mode: execute must surface a would-change verdict (Thermos R2-M1 part 2)."""
    text = (PRIMARY_PREP_TASKS / "pause_backups.yml").read_text()
    assert "ansible_check_mode" in text, "pause_backups.yml published changed must treat native check mode like dry_run"
```

New `test_restore_activation_check_mode_contract.py`:

```python
"""Contract: activate_restore publishes a truthful changed under native check mode (Thermos R2-M1 part 2)."""

from pathlib import Path

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
ACTIVATE_RESTORE = ROLES_DIR / "activation" / "tasks" / "activate_restore.yml"


def test_activate_restore_published_changed_covers_native_check_mode():
    text = ACTIVATE_RESTORE.read_text()
    assert (
        "ansible_check_mode" in text
    ), "activate_restore.yml published changed must treat native check mode like dry_run"
```

(Adjust `parents[2]` if the existing contract tests resolve `ROLES_DIR` differently — copy the exact resolution from `test_backup_schedule_persistence.py`.)

- [ ] **Step 4: Run to verify 4 fail**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_activation_check_mode_contract.py -q`
Expected: 4 FAIL (2 flipped plugin tests, 2 contract asserts), rest PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: require truthful check-mode changed for pause/activation (red, R2-M1 part 2)"
```

### Task 2: Implement — plugins, roles, doc

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py:181`
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py:429-431`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/primary_prep/tasks/pause_backups.yml` (published-changed fallback)
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/tasks/activate_restore.yml:129-138`
- Modify: `ansible_collections/tomazb/acm_switchover/docs/variable-reference.md:70`

- [ ] **Step 1: acm_backup_schedule.py**

```python
        changed=(operation["action"] != "none"),
```

- [ ] **Step 2: acm_restore_info.py**

Delete:

```python
    if module.check_mode:
        plan["changed"] = False
```

- [ ] **Step 3: pause_backups.yml fallback**

```yaml
      changed: >-
        {{
          (_backup_patch_results.changed | default(false))
          or (_backup_delete_results.changed | default(false))
          or (((acm_switchover_execution.mode | default('dry_run') == 'dry_run') or ansible_check_mode)
              and acm_backup_schedule_operation.operation.action != 'none')
        }}
```

- [ ] **Step 4: activate_restore.yml fallback**

```yaml
      changed: >-
        {{
          (_restore_patch_result.changed | default(false))
          or (_restore_delete_result.changed | default(false))
          or (_restore_create_result.changed | default(false))
          or (
            ((acm_switchover_execution.mode | default('dry_run') == 'dry_run') or ansible_check_mode)
            and (acm_restore_activation_plan.changed | default(false))
          )
        }}
```

- [ ] **Step 5: variable-reference.md mode row**

Extend the sentence "Native Ansible check mode is non-mutating even when this is `execute`." to:

"Native Ansible check mode is non-mutating even when this is `execute`; published role results (`acm_switchover_pause_backups_result.changed`, `acm_switchover_restore_activation_result.changed`) report the would-change verdict under `--check`."

- [ ] **Step 6: Run to verify all pass**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`
Expected: all PASS.

- [ ] **Step 7: Format and commit**

```bash
black --line-length 120 ansible_collections/tomazb/acm_switchover/plugins/modules/acm_backup_schedule.py ansible_collections/tomazb/acm_switchover/plugins/modules/acm_restore_info.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_backup_schedule.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_restore_info.py ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_schedule_persistence.py ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_activation_check_mode_contract.py
isort --profile black --line-length 120 <same files>
git add -A
git commit -m "fix: surface truthful changed under native check mode for pause/activation (R2-M1 part 2)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 38)

- [ ] **Step 1: Full gate** — `./run_tests.sh`, expect PASS, record lane counts.

- [ ] **Step 2: Tracker + push + PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 38 ready for review in tracker"
git push -u origin fix/thermos-38-checkmode-changed-surfacing
gh pr create --draft --base ansible --title "Thermos PR 38: truthful changed under native check mode (R2-M1 part 2)" --body "<summary + verification evidence>"
```
