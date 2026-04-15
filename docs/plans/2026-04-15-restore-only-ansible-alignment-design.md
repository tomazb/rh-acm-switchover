# Design: Restore-Only Ansible Collection Alignment

## Problem Statement

The Python CLI has a full `--restore-only` mode for single-hub disaster recovery (restoring managed clusters from S3 backups onto a fresh ACM hub when the original hub is permanently unavailable). The Ansible collection (`tomazb.acm_switchover`) has zero restore-only support — no playbook, no validation, no role branching.

Additionally, the Python CLI's ArgoCD handling for restore-only is suboptimal: it rejects `--argocd-manage` entirely and tells operators to "pause manually." This is a gap because the secondary hub may have ArgoCD Applications with auto-sync that would fight the restore process.

## Approach

**Operation-level flag** (`acm_switchover_operation.restore_only: true`) with a dedicated `restore_only.yml` playbook. Roles branch internally on the flag. Both Python CLI and Ansible collection get aligned ArgoCD semantics.

This approach:
- Matches the `decommission.yml` precedent (purpose-specific playbooks)
- Maps cleanly to the Python CLI's `--restore-only` flag
- Follows the collection's existing pattern where `acm_switchover_operation` controls workflow behavior
- Provides a clean AAP workflow template: one playbook = one job template

## Phase Flow

### Normal switchover
```
PREFLIGHT → PRIMARY_PREP → ACTIVATION → POST_ACTIVATION → FINALIZATION
```

### Restore-only
```
PREFLIGHT → [ArgoCD pause on secondary] → ACTIVATION → POST_ACTIVATION → FINALIZATION
```

`PRIMARY_PREP` is omitted entirely (not conditional — not included in the playbook).

## Variable Design

New field in `acm_switchover_operation`:

```yaml
acm_switchover_operation:
  restore_only: false     # NEW — single-hub restore from S3 backups
  method: passive         # forced to "full" when restore_only
  old_hub_action: secondary  # forced to "none" when restore_only
  activation_method: patch
  min_managed_clusters: 0
```

Hub config for restore-only — primary can be empty/omitted:

```yaml
acm_switchover_hubs:
  primary:
    context: ""           # empty — no primary hub
    kubeconfig: ""
  secondary:
    context: "new-hub"    # required — restore target
    kubeconfig: "~/.kube/config"
```

## Playbook: `restore_only.yml`

New playbook at `playbooks/restore_only.yml`:
- Includes roles: `preflight` → (optional ArgoCD pause) → `activation` → `post_activation` → `finalization`
- Omits `primary_prep` entirely
- Sets `acm_switchover_operation.restore_only: true` as play-level override
- Same `always:` report block as `switchover.yml` (minus primary_prep in phases)
- Checkpoint support uses the same mechanism with reduced phase set

## Input Validation Rules

### Ansible (`acm_input_validate` module)

When `restore_only: true`:

| Rule | Action |
|------|--------|
| `primary.context` is set | **Reject** — no primary hub needed |
| `secondary.context` missing | **Reject** — required |
| `method` != `full` (if explicitly set) | **Reject** — passive sync needs live primary |
| `old_hub_action` explicitly set | **Reject** — no old hub |
| `argocd.manage: true` | **Allow** — pause on secondary before restore |
| `argocd.resume_after_switchover: true` | **Reject** — would restore secondary-role ArgoCD state |

Validation returns normalized values with forced defaults:
- `method` → `"full"`
- `old_hub_action` → `"none"`

### Python CLI (`lib/validation.py`)

Same rules. Change from current behavior:
- `--argocd-manage` with `--restore-only`: **now allowed** (was rejected)
- `--argocd-resume-after-switchover` with `--restore-only`: still rejected

## ArgoCD Design (Both Tools)

### The Problem

In restore-only, the secondary hub was a standby. Its ArgoCD Applications are configured for a **secondary role** — managing secondary-specific resources, pointing at secondary-specific git branches/paths.

After restore, the secondary becomes the **primary**. Two risks:

1. **During restore**: ArgoCD auto-sync fights the restore, reverting ACM resources to their git-declared (secondary-role) state
2. **After restore**: Auto-resuming ArgoCD would re-apply secondary-role git state onto what is now a primary hub

### The Solution

| Phase | ArgoCD action | Target hub |
|-------|--------------|------------|
| Before activation | Pause auto-sync (when `argocd.manage: true`) | Secondary |
| During activation | No interference — auto-sync disabled | — |
| Finalization | **Do NOT auto-resume**; leave paused with annotations | Secondary |
| Post-switchover | Operator retargets git, then runs `argocd_resume.yml` manually | Secondary |

### Implementation

**Ansible**: In `restore_only.yml`, include `argocd_manage` role with:
```yaml
- name: Pause Argo CD auto-sync on secondary (when enabled)
  ansible.builtin.include_role:
    name: tomazb.acm_switchover.argocd_manage
  vars:
    acm_switchover_argocd_mode_override: pause
    _argocd_discover_hub: secondary
  when: acm_switchover_features.argocd.manage | default(false)
```

Finalization: skip ArgoCD resume for restore-only (even if `resume_after_switchover` somehow gets through validation).

**Python CLI**: In `run_restore_only()`, insert ArgoCD pause step before ACTIVATION targeting secondary-only. In `_run_phase_finalization`, skip ArgoCD resume when `restore_only=True`.

### Advisory Warning

When ArgoCD ACM-touching Applications are detected on secondary but `argocd.manage` is not set:

```
⚠ ArgoCD advisory: N ACM-touching Application(s) with auto-sync detected on secondary hub.
  Consider enabling argocd.manage to pause auto-sync before restore.
  Without pausing, ArgoCD may revert restored ACM resources.
```

When ArgoCD is paused during restore-only:

```
ℹ ArgoCD: N Application(s) paused on secondary. Left paused intentionally.
  Retarget git repos/paths to primary-role configuration before resuming.
  Resume with: ansible-playbook playbooks/argocd_resume.yml
```

## Role Branching Details

### Preflight Role

Skip primary-only validators when `acm_switchover_operation.restore_only`:

| Validator | Restore-only | Notes |
|-----------|-------------|-------|
| Input validation | ✅ | With restore-only rules |
| GitOps detection | ✅ | Informational |
| Primary hub discovery | ❌ Skip | No primary hub |
| Secondary hub discovery | ✅ | BSL, namespaces, MCH |
| Kubeconfig validation | ✅ (secondary only) | |
| Version check | ✅ (secondary only) | |
| Namespace check | ✅ (secondary only) | |
| Hub components | ✅ (secondary only) | |
| BSL on secondary | ✅ **critical** | Required for restore |
| Backup/schedule/cluster validators | ❌ Skip | Primary-only |
| Passive sync check | ❌ Skip | Full restore only |
| Observability detection | ✅ (secondary only) | |
| RBAC validation | ✅ (secondary only) | Secondary write permissions |

### Primary_Prep Role

Not included in `restore_only.yml`. No changes to the role itself.

### Activation Role

No changes needed. Already works secondary-only; `method=full` creates full restore.

### Post-Activation Role

No changes needed. Already secondary-only validation.

### Finalization Role

- Force `old_hub_action: none` when restore-only (skip `handle_old_hub.yml` entirely)
- Skip ArgoCD resume for restore-only
- BackupSchedule enablement, verification, and MCH health check proceed normally (secondary-only)

## Testing Plan

### Ansible Unit Tests

1. **`acm_input_validate` module**:
   - Restore-only accepts: `method=full`, `argocd.manage=true`, secondary-only
   - Restore-only rejects: primary context, method=passive, old_hub_action, argocd.resume_after
   - Restore-only forces: method→full, old_hub_action→none

2. **Integration vars**: New e2e-vars file for restore-only scenario with appropriate variable structure

### Python CLI Tests

1. **Validation** (`tests/test_validation.py`):
   - `--argocd-manage` + `--restore-only` now passes (was rejected)
   - `--argocd-resume-after-switchover` + `--restore-only` still rejected

2. **ArgoCD pause** (`tests/test_main.py` or equivalent):
   - Verify ArgoCD pause step runs on secondary when `restore_only=True` and `argocd_manage=True`
   - Verify ArgoCD resume skipped in finalization for restore-only

## Files Changed

### Ansible Collection

| File | Change |
|------|--------|
| `playbooks/restore_only.yml` | **New** — restore-only playbook |
| `roles/preflight/defaults/main.yml` | Add `restore_only: false` to operation |
| `roles/preflight/tasks/discover_resources.yml` | Skip primary discovery when restore_only |
| `roles/preflight/tasks/main.yml` | Skip primary-only validation tasks |
| `roles/finalization/tasks/main.yml` | Skip handle_old_hub and ArgoCD resume for restore-only |
| `roles/finalization/defaults/main.yml` | Add `restore_only: false` to operation |
| `plugins/modules/acm_input_validate.py` | Restore-only validation rules |
| `plugins/module_utils/validation.py` | `validate_operation_inputs` with restore-only |
| `tests/unit/plugins/modules/test_acm_input_validate.py` | Restore-only test cases |
| Other role `defaults/main.yml` | Add `restore_only: false` where operation is defaulted |

### Python CLI

| File | Change |
|------|--------|
| `lib/validation.py` | Allow `--argocd-manage` with `--restore-only` |
| `acm_switchover.py` | Add ArgoCD pause step in `run_restore_only()` |
| `modules/finalization.py` | Skip ArgoCD resume when restore-only |
| `tests/test_validation.py` | Update ArgoCD + restore-only test expectations |
| `tests/test_main.py` | ArgoCD pause/skip-resume tests for restore-only |

### Documentation

| File | Change |
|------|--------|
| `AGENTS.md` | Document restore-only Ansible support |
| `CHANGELOG.md` | Add entries under Unreleased |
| `docs/operations/usage.md` | Updated ArgoCD + restore-only guidance |
