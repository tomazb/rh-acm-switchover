# Restore-Only Ansible Alignment — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `restore_only` mode to the Ansible collection with full feature parity to the Python CLI, and fix ArgoCD handling in both tools so restore-only can pause auto-sync on the secondary hub.

**Architecture:** Operation-level flag (`acm_switchover_operation.restore_only`) with a dedicated `restore_only.yml` playbook. Roles branch on the flag to skip primary-only validators and force restore-only defaults. Both Python CLI and Ansible collection get aligned ArgoCD semantics — `--argocd-manage` is now allowed with `--restore-only` (pauses on secondary), while `--argocd-resume-after-switchover` remains rejected (operator must retarget git first).

**Tech Stack:** Ansible (YAML roles/playbooks, Python modules), Python (CLI validation, orchestrator), pytest

**Design document:** `docs/plans/2026-04-15-restore-only-ansible-alignment-design.md`

---

## Task 1: Ansible validation — `validate_operation_inputs` restore-only rules

Add restore-only validation to the shared validation module used by `acm_input_validate`.

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py:78-108`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py`

**Step 1: Write failing tests**

Add tests to `test_acm_input_validate.py` for restore-only validation:
- `test_restore_only_valid_secondary_only` — restore_only=true, no primary context, secondary present, method=full → passes
- `test_restore_only_rejects_primary_context` — restore_only=true with primary context → fails
- `test_restore_only_rejects_passive_method` — restore_only=true, method=passive → fails
- `test_restore_only_forces_method_full` — restore_only=true, no explicit method → normalized to full
- `test_restore_only_allows_argocd_manage` — restore_only=true, argocd.manage=true → passes
- `test_restore_only_rejects_argocd_resume_after` — restore_only=true, argocd.resume_after=true → fails
- `test_restore_only_rejects_old_hub_action` — restore_only=true, old_hub_action != none → fails

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py -v`
Expected: New tests FAIL (validation logic not yet implemented)

**Step 3: Implement `validate_operation_inputs` restore-only logic**

In `plugins/module_utils/validation.py`, extend `validate_operation_inputs()`:
- Accept `operation.get("restore_only", False)`
- When restore_only:
  - Force `method = "full"` (error if explicitly "passive")
  - Force `old_hub_action = "none"` (error if explicitly set to something else)
  - Reject `argocd_resume_after` (secondary-role git state danger)
  - Allow `argocd_manage` (pause on secondary)
- Return normalized dict including `restore_only` flag

**Step 4: Update `acm_input_validate` module restore-only handling**

In `plugins/modules/acm_input_validate.py`, in `build_input_validation_results()`:
- When `restore_only: true`:
  - Skip primary context validation (don't fail on empty primary context)
  - Still require secondary context
  - Pass `restore_only` through to `validate_operation_inputs()`

**Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_input_validate.py -v`
Expected: All tests PASS

**Step 6: Commit**

```
feat(ansible): add restore-only validation to acm_input_validate module

Validates restore-only operation rules: requires secondary context,
forces method=full and old_hub_action=none, allows argocd.manage
(pause on secondary), rejects argocd.resume_after_switchover.
```

---

## Task 2: Role defaults — add `restore_only` to `acm_switchover_operation`

Add `restore_only: false` to every role's `defaults/main.yml` that declares `acm_switchover_operation`.

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/defaults/main.yml:10-14`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/activation/defaults/main.yml:21-25`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/defaults/main.yml:21-25`

**Step 1: Add `restore_only: false` to each file**

In each `defaults/main.yml` that has `acm_switchover_operation:`, add `restore_only: false` as the first key in the dict (before `method:`). This ensures the variable is always defined even if the caller doesn't set it.

Only these three roles declare `acm_switchover_operation` in their defaults. Other roles (`post_activation`, `primary_prep`, `argocd_manage`, `discovery`, `decommission`, `rbac_bootstrap`) do not define `acm_switchover_operation` in their defaults — they receive it from the playbook.

**Step 2: Commit**

```
feat(ansible): add restore_only default to role operation variables
```

---

## Task 3: Preflight role — skip primary-only validators for restore-only

Branch preflight discovery and validation tasks to skip primary hub when `restore_only` is true.

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/discover_resources.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_kubeconfigs.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_versions.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_namespaces.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_backups.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/validate_rbac.yml`

All guards use the same pattern:
```yaml
when:
  - not (acm_switchover_operation.restore_only | default(false))
  - <existing_guard_if_any>
```

**Step 1: Guard primary discovery tasks**

In `discover_resources.yml`, add `when: not (acm_switchover_operation.restore_only | default(false))` (combined with existing `when: <var> is not defined`) to these primary-only tasks:
- "Read primary hub namespaces" (line 2)
- "Read primary MultiClusterHub" (line 20)
- "Read primary hub backups" (line 40)
- "Read primary hub BackupSchedules" (line 50)
- "Read primary hub BackupStorageLocations" (line 60)
- "Read primary hub ClusterDeployments" (line 90)
- "Read primary hub ManagedCluster backups" (line 99)

Keep these secondary tasks **unguarded** (they run in all modes):
- "Read secondary hub namespaces" (line 11)
- "Read secondary MultiClusterHub" (line 30)
- "Read secondary hub BackupStorageLocations" (line 70)
- "Read secondary passive restore resource" (line 80)

After the guarded primary tasks, seed empty defaults so downstream tasks don't fail on undefined variables:
```yaml
- name: Seed empty primary facts for restore-only mode
  ansible.builtin.set_fact:
    acm_primary_namespace_info: { resources: [] }
    acm_primary_mch_info: { resources: [] }
    acm_primary_backups_info: { resources: [] }
    acm_primary_backup_schedules_info: { resources: [] }
    acm_primary_bsl_info: { resources: [] }
    acm_primary_cluster_deployments_info: { resources: [] }
    acm_primary_managed_cluster_backups_info: { resources: [] }
  when:
    - acm_switchover_operation.restore_only | default(false)
    - acm_primary_mch_info is not defined
```

**Step 2: Guard primary kubeconfig validation**

In `validate_kubeconfigs.yml`, guard the "Record primary-hub connectivity result" task:
```yaml
when: not (acm_switchover_operation.restore_only | default(false))
```
The secondary connectivity check runs unconditionally.

**Step 3: Guard primary version validation**

In `validate_versions.yml`, for restore-only:
- Set `acm_primary_version` to empty string (no primary MCH)
- Skip cross-hub version comparison (only validate secondary has a valid version)
- Add a restore-only-specific version check that validates secondary version is available

**Step 4: Guard primary namespace validation**

In `validate_namespaces.yml`, guard all primary namespace checks with:
```yaml
when: not (acm_switchover_operation.restore_only | default(false))
```
Keep secondary namespace checks (backup namespace, observability detection) running.

**Step 5: Guard primary backup/schedule validators**

In `validate_backups.yml`, guard these primary-only tasks:
- "Record latest backup existence" — skip (references `acm_primary_backups_info`)
- "Record BackupSchedule presence" — skip (references `acm_primary_backup_schedules_info`)
- "Record primary hub BackupStorageLocation health" — skip
- "Record ClusterDeployment check" — skip
- "Record ManagedClusterBackup check" — skip

Keep "Record secondary hub BackupStorageLocation health" — runs in all modes (BSL required for restore).
Keep "Record passive restore availability" — already auto-passes for `method=full`.

**Step 6: Guard primary RBAC validation**

In `validate_rbac.yml`, skip primary RBAC SSAR checks when restore-only. Keep secondary RBAC checks (write permissions for Restore, BackupSchedule, ManagedCluster).

**Step 7: Run existing tests to verify no regressions**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`
Expected: All existing tests PASS (restore_only defaults to false)

**Step 8: Commit**

```
feat(ansible): skip primary-only preflight validators in restore-only mode

Preflight discovery and validation tasks now check
acm_switchover_operation.restore_only and skip primary hub queries,
seeding empty defaults so downstream tasks don't fail on undefined vars.
```

---

## Task 4: Finalization role — force `old_hub_action=none` and skip ArgoCD resume

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/main.yml:25-35`
- Modify: `ansible_collections/tomazb/acm_switchover/roles/finalization/tasks/handle_old_hub.yml`

**Step 1: Guard handle_old_hub and ArgoCD resume in finalization**

In `finalization/tasks/main.yml`:
- Guard "Handle old hub disposition" with: `when: not (acm_switchover_operation.restore_only | default(false))`
- Guard "Resume Argo CD auto-sync" with additional condition: `not (acm_switchover_operation.restore_only | default(false))`
- Add advisory debug message after the guarded ArgoCD resume for restore-only:
  ```yaml
  - name: Advisory — ArgoCD left paused in restore-only mode
    ansible.builtin.debug:
      msg: >-
        Argo CD Applications were paused before restore. Retarget git
        repos/paths to primary-role configuration before resuming.
        Resume with: ansible-playbook playbooks/argocd_resume.yml
    when:
      - acm_switchover_operation.restore_only | default(false)
      - acm_switchover_features.argocd.manage | default(false)
  ```

**Step 2: Run existing tests**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`
Expected: All existing tests PASS

**Step 3: Commit**

```
feat(ansible): skip old-hub handling and ArgoCD resume in restore-only finalization
```

---

## Task 5: New playbook — `restore_only.yml`

**Files:**
- Create: `ansible_collections/tomazb/acm_switchover/playbooks/restore_only.yml`

**Step 1: Create the playbook**

Model after `switchover.yml` but:
- Force `acm_switchover_operation.restore_only: true` in `vars:` or `pre_tasks:`
- Include roles: preflight → (ArgoCD pause on secondary when enabled) → activation → post_activation → finalization
- Omit `primary_prep` role entirely
- ArgoCD pause step before activation:
  ```yaml
  - name: Pause Argo CD auto-sync on secondary hub (when enabled)
    ansible.builtin.include_role:
      name: tomazb.acm_switchover.argocd_manage
    vars:
      acm_switchover_argocd_mode_override: pause
      _argocd_discover_hub: secondary
    when: acm_switchover_features.argocd.manage | default(false)
  ```
- Same `always:` report block (minus primary_prep in phases)
- Checkpoint support with the 4-phase set (preflight, activation, post_activation, finalization)

**Step 2: Commit**

```
feat(ansible): add restore_only.yml playbook for single-hub restore
```

---

## Task 6: Python CLI — allow `--argocd-manage` with `--restore-only`

Fix the Python CLI validation and orchestrator to support ArgoCD pause on secondary in restore-only mode.

**Files:**
- Modify: `lib/validation.py:414-419`
- Modify: `acm_switchover.py:484-616` (run_restore_only function)
- Modify: `acm_switchover.py:780-800` (_report_argocd_acm_impact advisory)
- Test: `tests/test_validation.py:683-695`

**Step 1: Update validation test expectations**

In `tests/test_validation.py`:
- Change `test_restore_only_forbids_argocd_manage` → `test_restore_only_allows_argocd_manage`: remove the `pytest.raises` wrapper, assert validation passes
- Add `test_restore_only_with_argocd_manage_and_resume_after_forbidden`: restore_only + argocd_manage + argocd_resume_after → still rejected
- Keep `test_restore_only_forbids_argocd_resume_after_switchover` unchanged
- Keep `test_restore_only_forbids_argocd_resume_only` unchanged

**Step 2: Run tests to verify test changes fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_validation.py -k "restore_only" -v`
Expected: `test_restore_only_allows_argocd_manage` FAILS (validation still rejects)

**Step 3: Remove `--argocd-manage` rejection from validation**

In `lib/validation.py`, in the `is_restore_only` block (lines 414-419), remove the `if has_argocd_manage: raise ValidationError(...)` block. Keep the `has_argocd_resume_after` rejection.

**Step 4: Add ArgoCD pause step to `run_restore_only`**

In `acm_switchover.py`, in `run_restore_only()`, add an ArgoCD pause step before the phase flow execution. The pause should:
- Check `getattr(args, "argocd_manage", False)` and not `args.validate_only`
- Use the existing `PrimaryPreparation._pause_argocd_acm_apps()` pattern but targeting secondary only
- Or more simply: extract a standalone ArgoCD pause helper that takes a list of (client, label) pairs and call it with `[(secondary, "secondary")]`

The simplest approach is to add a dedicated function `_pause_argocd_on_secondary()` that:
1. Detects ArgoCD installation on secondary
2. Finds ACM-touching apps
3. Pauses auto-sync
4. Records state (`argocd_paused_apps`, `argocd_run_id`)

This reuses `lib/argocd` functions directly (same as `PrimaryPreparation._pause_argocd_acm_apps` does).

**Step 5: Update the advisory message**

In `_report_argocd_acm_impact()`, update the `primary is None` advisory (lines 785-792) to suggest `--argocd-manage` instead of "pause manually":
```python
"\n⚠ ArgoCD advisory: %d ACM-touching Application(s) with auto-sync detected on secondary hub.\n"
"  Consider --argocd-manage to pause auto-sync before restore.\n"
"  Without pausing, ArgoCD may revert restored ACM resources.\n"
"  To suppress: --skip-gitops-check"
```

**Step 6: Skip ArgoCD resume in finalization for restore-only**

In `_run_phase_finalization()` (around line 891), when `restore_only=True`:
- Force `argocd_resume_after_switchover=False` for the `Finalization` constructor
- This is already enforced by validation, but defense-in-depth is good

**Step 7: Run tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_validation.py -k "restore_only" -v`
Expected: All PASS

Run: `source .venv/bin/activate && python -m pytest tests/ -x -q`
Expected: Full suite PASS

**Step 8: Commit**

```
feat: allow --argocd-manage with --restore-only for secondary hub pause

Removes the blanket rejection of --argocd-manage in restore-only mode.
ArgoCD auto-sync is now paused on the secondary hub before activation
to prevent drift during restore. --argocd-resume-after-switchover
remains rejected (operator must retarget git before resuming).
```

---

## Task 7: E2E vars and integration test fixtures

**Files:**
- Create: `e2e-vars/restore-only.yml` — vars file for restore-only scenario
- Create: `e2e-vars/restore-only-argocd.yml` — vars file for restore-only with ArgoCD

**Step 1: Create restore-only e2e vars**

```yaml
---
acm_switchover_hubs:
  primary:
    context: ""
    kubeconfig: ""
  secondary:
    context: "restore-target-hub"
    kubeconfig: "~/.kube/config"

acm_switchover_operation:
  restore_only: true
  method: full
  old_hub_action: none
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
  mode: dry_run
  verbose: true
  force: false
  report_dir: ./artifacts
  checkpoint:
    enabled: false
    backend: file
    path: .state/restore-only.json
    reset: false
```

**Step 2: Create restore-only-argocd e2e vars**

Same as above but with `argocd.manage: true`.

**Step 3: Commit**

```
test(e2e-vars): add restore-only and restore-only-argocd scenario vars
```

---

## Task 8: Run full test suite and verify

**Step 1: Run full Ansible collection tests**

Run: `source .venv/bin/activate && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`
Expected: All PASS

**Step 2: Run full Python CLI tests**

Run: `source .venv/bin/activate && python -m pytest tests/ -x -q`
Expected: All PASS (including updated restore-only ArgoCD tests)

**Step 3: Commit any fixups from test failures**

---

## Task 9: Documentation and changelog

**Files:**
- Modify: `CHANGELOG.md` — add entries under `[Unreleased]`
- Modify: `AGENTS.md` — document restore-only Ansible support in the collection section

**Step 1: Update CHANGELOG.md**

Under `## [Unreleased]`, add:

```markdown
### Added

- **Ansible Collection: Restore-Only Mode** — new `restore_only.yml` playbook and operation flag for single-hub disaster recovery via S3 backup restore, with full feature parity to the Python CLI `--restore-only` flag
- **ArgoCD pause on secondary for restore-only** — both Python CLI (`--argocd-manage`) and Ansible collection (`argocd.manage: true`) now support pausing ArgoCD auto-sync on the secondary hub before restore-only activation, preventing drift during restore

### Changed

- Python CLI: `--argocd-manage` is now allowed with `--restore-only` (previously rejected); pauses ArgoCD on secondary hub before activation
- Python CLI: `--argocd-resume-after-switchover` remains rejected with `--restore-only` (operator must retarget git first)

### Fixed

- Python CLI: restore-only ArgoCD advisory now suggests `--argocd-manage` instead of "pause manually"
```

**Step 2: Update AGENTS.md**

In the Ansible Collection section, add restore-only to the playbooks table and note the operation flag.

**Step 3: Commit**

```
docs: document restore-only Ansible support and ArgoCD improvements
```
