# ArgoCD Management Alignment — Design Document

## Problem

The ArgoCD auto-sync pause/resume feature is implemented across three form factors (Bash `argocd-manage.sh`, Python `lib/argocd.py`, Ansible `roles/argocd_manage/`). An external cross-surface review revealed 16 findings including production bugs, behavioral parity gaps, and quality issues. This document captures the validated findings and the approved remediation design.

## External Review Validation

The external plan's 16 findings were verified against the codebase. Results:

| # | Finding | Claimed | Verified | Notes |
|---|---------|---------|----------|-------|
| 1 | Ansible restore_only pauses wrong hub | P0 | **Confirmed** | `pause.yml:15` hardcodes `hubs.primary` |
| 2 | Ansible doesn't pause secondary hub | P0 | **Confirmed** | `primary_prep` only includes `_argocd_discover_hub: primary` |
| 3 | Bash dry-run skips state write | P0 | **Confirmed** | Lines 358-363 skip `write_pause_state` |
| 4 | State-file schema divergence | P1 | **Confirmed** | Context-keyed (Bash) vs flat array (Python) |
| 5 | run_id format divergence | P1 | **Confirmed** | `YYYYMMDDHHMMSS-$RANDOM` vs `uuid4().hex[:12]` |
| 6 | Duplicated constants | P1 | **Understated** | Ansible has 6 kinds vs 14 in Python/Bash (see below) |
| 7 | Re-pause clobbers original-sync-policy | P2 | **Confirmed** | No guard on annotation overwrite |
| 8 | Restore-only is simplified copy | P2 | **Confirmed** | Missing `_find_pause_entry`/recovery |
| 9 | Bash resume no stale cleanup | P2 | **Confirmed** | |
| 10 | ApplicationSets blind spot | P2 | **Confirmed** | Zero references across all surfaces |
| 11 | status.resources-only discovery | P2 | **Confirmed** | Fresh/unsynced apps missed |
| 12 | @dry_run_skip on module functions | P2 | **Partially** | Works via KubeClient positional hack; has explicit comment |
| 13 | Bash locale-fragile | P2 | **Confirmed** | Hardcoded English substring matching |
| 14 | build_pause_patch is dead code | P2 | **Invalid** | Tested in unit tests; real issue is tested-helper/untested-production divergence |
| 15 | --target accepts only acm | P2 | **Confirmed** | Pretend ontology |
| 16 | No --force flag | P2 | **Premature** | Current stale-marker handling is adequate |

### Additional Findings (not in external plan)

**NEW P1: `paused-by` annotation is empty string in Ansible.**
`argocd_manage/defaults/main.yml` sets `run_id: ""`. Jinja `default()` only triggers on undefined, not empty string. No playbook or plugin sets `run_id`, so the annotation is always `""` — no provenance tracking, no cross-run distinction.

**#6 severity escalation: Ansible ACM_KINDS has only 6 of 14 kinds.**
Missing: MultiClusterEngine, ManagedClusterSet, ManagedClusterSetBinding, Placement, PlacementBinding, Policy, PolicySet, DataProtectionApplication. Namespace matching partly mitigates (apps deploying to ACM namespaces are still caught), but cluster-scoped resources and resources in non-ACM namespaces are silently missed.

**#14 correction:** `build_pause_patch` is not dead code — it's tested. But `pause.yml` builds the patch inline in Jinja, creating a tested-helper / untested-production divergence.

**Integration test weakness:** Mock resume short-circuits k8s patching. Tests don't verify actual pause→resume state propagation.

## Strategic Decision: Bash `argocd-manage.sh`

**Decision: Full deprecation.** The script is already marked deprecated in `scripts/README.md`. Python CLI and Ansible collection both provide full ArgoCD management. The script's maintenance burden (3 duplicated constant sets, incompatible state format, multiple unfixed bugs) outweighs its value as a lightweight emergency tool.

Actions:
- Fix the misleading dry-run log message (1 line)
- Add deprecation banner on execution
- Remove references from runbook, SKILLS, quickref, and usage docs
- Point to Python `--argocd-manage` / Ansible `argocd_manage` role instead
- No further feature work or bug fixes

## Remediation Design

### Phase 1: Fix Production Bugs

#### 1A. Ansible hub hardcoding (P0 #1, #2)

**pause.yml + resume.yml**: Replace hardcoded hub references with `_argocd_discover_hub`-parameterized access:

```yaml
# Before (pause.yml:15-16):
kubeconfig: "{{ acm_switchover_hubs.primary.kubeconfig }}"
context: "{{ acm_switchover_hubs.primary.context }}"

# After:
kubeconfig: "{{ acm_switchover_hubs[_argocd_discover_hub | default('primary')].kubeconfig }}"
context: "{{ acm_switchover_hubs[_argocd_discover_hub | default('primary')].context }}"
```

Same change in `resume.yml` (currently hardcodes secondary).

**primary_prep/main.yml**: Add a second `include_role: argocd_manage` with `_argocd_discover_hub: secondary` when `acm_switchover_hubs.secondary` is defined. This matches Python's both-hubs behavior.

**finalization/main.yml**: Add primary hub resume when:
- Not restore-only (`not acm_switchover_operation.restore_only`)
- Old hub not being decommissioned (`acm_switchover_operation.old_hub_action != 'decommission'`)
- Primary hub is defined (`acm_switchover_hubs.primary is defined`)

#### 1B. Ansible ACM_KINDS alignment (P0-escalated from P1 #6)

Expand `plugins/module_utils/argocd.py` `ACM_KINDS` from 6 to 14 kinds, matching `lib/argocd.py`:

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

#### 1C. Empty run_id fix (NEW finding)

**argocd_manage/defaults/main.yml**: Change `run_id: ""` to remove the default entirely. In `discover.yml` or `pause.yml`, generate a run_id when undefined/empty:

```yaml
- name: Generate ArgoCD run_id if not set
  ansible.builtin.set_fact:
    acm_switchover_argocd: >-
      {{ acm_switchover_argocd | default({}) | combine({
        'run_id': lookup('ansible.builtin.password', '/dev/null chars=hexdigits length=12') | lower
      }) }}
  when: (acm_switchover_argocd.run_id | default('')) == ''
```

#### 1D. Re-pause clobber guard (P2 #7, escalated)

**pause.yml**: Add condition to skip already-paused apps:

```yaml
when:
  - acm_switchover_argocd_mock_apps is not defined
  - "'acm-switchover.argoproj.io/paused-by' not in (item.metadata.annotations | default({}))"
```

#### 1E. Parity contract test (P1 #6 prevention)

New `tests/test_argocd_constants_parity.py`:
- Parse `lib/argocd.py` `ARGOCD_ACM_KINDS` (import directly)
- Parse `plugins/module_utils/argocd.py` `ACM_KINDS` (import or ast.parse)
- Compare sets — fail CI on drift
- Optionally parse Bash `ARGOCD_ACM_KINDS_JSON` via regex (lower priority since Bash is deprecated)

#### 1F. Bash deprecation (P0 #3 + strategy)

- Fix dry-run log message to not claim state would be written
- Add deprecation banner: `echo "WARNING: argocd-manage.sh is deprecated. Use Python --argocd-manage or Ansible argocd_manage role."`
- Update runbook, SKILLS, quickref, usage docs to remove Bash references

### Phase 2: Quality Improvements

#### 2A. Python duplication refactor (P2 #8)

Extract shared helper from `primary_prep.py::_pause_argocd_acm_apps` and `acm_switchover.py::_run_restore_only_argocd_pause`. Single `pause_apps_on_hub(client, hub_label, state, run_id, dry_run)` method with caller-side error handling.

#### 2B. Edge-case warnings (P2 #10, #11)

- ApplicationSet presence warning in preflight
- Empty `status.resources` count returned from `find_acm_touching_apps`

#### 2C. Small quality fixes (P2 #12, #14)

- Explicit `dry_run` parameter on `pause_autosync`/`resume_autosync`
- Resolve `build_pause_patch` divergence (refactor `pause.yml` to use it or delete it)

#### 2D. Integration test hardening

- Pause→resume state round-trip test
- Secondary-hub pause in full switchover
- Restore-only secondary-only pause
- Re-pause idempotency verification

## Out of Scope

- Phase C (Python read-shim for Bash state) — Bash is deprecated
- Phase F (Port stale-marker cleanup to Bash) — Bash is deprecated
- `--force` flag for stale markers — premature; current handling adequate
- ApplicationSet pause/resume — future design discussion
- Bidirectional state interop (Python↔Bash) — Bash is deprecated
- AppProject pause/resume — no stated need
