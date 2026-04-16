# Code Review Fixes — Design Document

> **Date:** 2026-04-16
> **Source:** External code review + deep codebase review
> **Scope:** 2 confirmed bugs (P2), 2 feature parity gaps, 1 defensive improvement, 1 code cleanup

## Problem Statement

An external code review and internal audit identified bugs, feature parity gaps between the Python CLI and Ansible Collection, and a defensive code quality issue. This design addresses the findings that were verified as real and actionable.

## Findings Triage (Post-Verification)

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| T2 | **P2 Bug** | Ansible: `argocd_resume.yml` primary resume guard always passes | **Confirmed — fix** |
| T3 | **P2 Bug** | Python: decommission RBAC validator blocks idempotent reruns | **Confirmed — fix** |
| T4 | **Parity Gap** | Ansible: missing klusterlet auto-remediation | **Confirmed — implement** |
| T5 | **Parity Gap** | Ansible: missing auto-import strategy management | **Confirmed — implement** |
| T7 | **Defensive** | Python: `dry_run_skip` decorator null-safety on broken attribute paths | **Theoretical but valid — fix** |
| T1 | **Cleanup** | Python: `_resolve_state_file` unused `restore_only` param | **Not a bug — cleanup only** |
| T6 | — | Ansible: `handle_old_hub.yml` apply semantics | **Deferred — needs cluster validation** |

### T1 Re-assessment (Downgraded from P1 → Cleanup)

The original claim was that `--restore-only --argocd-resume-only` can't find the state file. This is **incorrect**: when `--restore-only` is active, `--primary-context` is forbidden, so `primary_ctx=None`, and `_build_default_state_file(None, sec)` produces `switchover-restore-only__<secondary>.json` — the exact file the pause step wrote. The `restore_only` parameter is accepted but unused. Fix: remove the dead parameter or add a clarifying comment.

### T7 Re-assessment (Downgraded from Medium → Low/Defensive)

All production callers of `@dry_run_skip` use the single-level default `dry_run_attr="dry_run"`. Multi-level paths (`"client.dry_run"`) only appear in test code. The null-safety issue is real in theory but cannot trigger in current production code. Still worth fixing as defense-in-depth.

---

## Task 2: Fix Ansible Primary Resume Guard

**Bug:** `when: acm_switchover_hubs.primary is defined` always evaluates true because `roles/preflight/defaults/main.yml` defines `primary` with empty strings. In restore-only or standalone resume, this invokes `argocd_manage` against a non-existent primary hub.

**Files:**
- `ansible_collections/.../playbooks/argocd_resume.yml` (line 19)
- `ansible_collections/.../playbooks/switchover.yml` (line 55, same pattern in rescue block)

**Fix:** Replace `is defined` with a substantive check:
```yaml
when: >-
  acm_switchover_hubs.primary is defined
  and (acm_switchover_hubs.primary.kubeconfig | default('')) | length > 0
  and (acm_switchover_hubs.primary.context | default('')) | length > 0
```

**Test:** Verify existing unit tests in `test_argocd_resume_on_failure.py` are updated to assert the stricter guard. Add a test that confirms the primary resume block is skipped when kubeconfig is empty.

---

## Task 3: Allow Decommission Reruns After ACM Namespace Removal

**Bug:** `validate_decommission_rbac()` in `lib/rbac_validator.py` (line ~664) sets `all_valid = False` when `open-cluster-management` namespace is missing. This blocks idempotent reruns of decommission after ACM has already been removed. Observability namespace already has this grace handling; ACM namespace does not.

**Files:**
- `lib/rbac_validator.py` — `validate_decommission_rbac()` method

**Fix:** Before the generic namespace loop, add a pre-check for `ACM_NAMESPACE` that mirrors the existing observability pre-check pattern:
```python
if namespace == ACM_NAMESPACE and not self.client.namespace_exists(namespace):
    logger.info(
        "Namespace %s does not exist - ACM already removed, "
        "skipping decommission permission checks for this namespace",
        namespace,
    )
    continue  # Don't set all_valid = False
```

**Test:** Add to `tests/test_rbac_validator.py`:
- `validate_decommission_rbac()` returns `all_valid=True` when `ACM_NAMESPACE` is gone
- Still fails when other required namespaces are unexpectedly missing

---

## Task 4: Ansible Klusterlet Auto-Remediation

**Gap:** Python's `_force_klusterlet_reconnect()` automatically fixes klusterlets pointing to the old hub. Ansible only records a warning note in `verify_klusterlet.yml`.

**Approach:** Require managed cluster kubeconfigs via a new variable. Direct translation of the Python logic using `kubernetes.core` modules.

**New variable:**
```yaml
# Optional — only needed when klusterlet remediation is desired
acm_switchover_managed_clusters:
  cluster1:
    kubeconfig: "/path/to/cluster1-kubeconfig"
    context: "cluster1-context"  # optional, uses default if omitted
```

**Files:**
- **Create:** `roles/post_activation/tasks/fix_klusterlet.yml`
- **Modify:** `roles/post_activation/tasks/main.yml` — include after `verify_klusterlet.yml`
- **Modify:** `roles/post_activation/defaults/main.yml` — add `acm_switchover_managed_clusters: {}`

**Implementation steps in `fix_klusterlet.yml`:**
1. Loop over clusters identified as `wrong_hub` or pending import
2. For each cluster:
   a. Get import secret from the new hub (`kubernetes.core.k8s_info` on secondary)
   b. Delete `bootstrap-hub-kubeconfig` secret on the managed cluster using its kubeconfig
   c. Apply decoded import manifest on the managed cluster
   d. Patch klusterlet deployment with rollout annotation to trigger restart
3. Guard entire task with `when: acm_switchover_managed_clusters | default({}) | length > 0`
4. Skip individual clusters gracefully when their kubeconfig is not provided

**Test:** Add integration test that mocks the managed cluster k8s calls and verifies the fix flow runs for wrong_hub clusters.

---

## Task 5: Ansible Auto-Import Strategy Management

**Gap:** Python's `_maybe_set_auto_import_strategy()` and `_apply_immediate_import_annotations()` manage the `import-controller-config` ConfigMap in MCE namespace for ACM 2.14+. Ansible's `apply_immediate_import.yml` is a stub.

**Approach:** Full parity via YAML tasks.

**Files:**
- **Create:** `roles/activation/tasks/manage_auto_import.yml`
- **Replace:** `roles/activation/tasks/apply_immediate_import.yml` (currently a stub)
- **Modify:** `roles/activation/tasks/main.yml` — include `manage_auto_import.yml` before activation

**Implementation in `manage_auto_import.yml`:**
1. Read `import-controller-config` ConfigMap in `multicluster-engine` namespace
2. Check if `autoImportStrategy` is `ImportOnly`
3. If so, patch to `ImportAndSync` (guard: `acm_switchover_features.manage_auto_import_strategy`)
4. Set fact `_auto_import_strategy_changed: true` for later reset

**Implementation in `apply_immediate_import.yml` (replace stub):**
1. Get list of ManagedClusters on secondary hub
2. For each cluster, check if it has the `import.open-cluster-management.io/trigger` annotation
3. If not, patch the annotation to `immediate`
4. Guard with ACM version ≥ 2.14 check

**Post-activation reset (in `main.yml` or new `reset_auto_import.yml`):**
1. If `_auto_import_strategy_changed`, patch ConfigMap back to `ImportOnly`

**Test:** Add integration test verifying strategy is set before activation and reset after.

---

## Task 7: Fix `dry_run_skip` Decorator Null-Safety

**Issue:** When a dot-separated attribute path has an intermediate `None` (e.g., `self.client` is `None` for path `"client.dry_run"`), the loop breaks and `obj` becomes `None`. The `if obj is True` check fails, causing the decorated function to execute instead of skipping. While theoretical for current production callers, it's a safety gap.

**Files:**
- `lib/utils.py` — `dry_run_skip()` decorator

**Fix:** When an intermediate attribute resolves to `None`, default to **skipping** (safe) rather than executing (risky):
```python
for attr_name in dry_run_attr.split("."):
    obj = getattr(obj, attr_name, None)
    if obj is None:
        logger = logging.getLogger("acm_switchover")
        logger.warning(
            "[DRY-RUN] Cannot resolve attribute path '%s'; "
            "skipping for safety",
            dry_run_attr,
        )
        if callable(return_value):
            return return_value(*args, **kwargs)
        return return_value
```

**Test:** Add to `tests/test_utils.py`:
- Broken intermediate path → function skips (returns `return_value`)
- Valid path with `dry_run=True` → function skips (unchanged behavior)
- Valid path with `dry_run=False` → function executes (unchanged behavior)

---

## Task 1: Clean Up `_resolve_state_file` Unused Parameter

**Issue:** The `restore_only` parameter was added to `_resolve_state_file()` but is never used in the function body. The function works correctly without it because `None` primary_ctx naturally produces the right filename.

**Files:**
- `acm_switchover.py` — `_resolve_state_file()` signature and call site

**Fix:** Remove the unused `restore_only` parameter from both the function signature and the call site. Add a comment explaining why no special handling is needed:
```python
# Note: restore-only mode needs no special handling here because
# --restore-only forbids --primary-context, so primary_ctx is None,
# and _build_default_state_file(None, secondary_ctx) naturally
# produces the correct "switchover-restore-only__<sec>.json" filename.
```

**Test:** Existing tests should continue to pass. No new tests needed.

---

## Execution Order

1. **T3** — Decommission RBAC fix (isolated, no dependencies)
2. **T2** — Ansible primary guard fix (isolated, no dependencies)
3. **T7** — dry_run_skip null-safety (isolated)
4. **T1** — State file cleanup (isolated)
5. **T5** — Auto-import strategy (builds on activation role)
6. **T4** — Klusterlet remediation (builds on post_activation role, most complex)

## Deferred

- **T6**: `handle_old_hub.yml` apply vs. delete+recreate — needs real-cluster validation to determine if `state: present, apply: true` correctly resets an activated restore. The Ansible code has an explicit comment arguing the patch approach is correct.
- **Observability verification parity** (gap 1.2) — needs separate design
- **Backup integrity verification parity** (gap 1.5) — needs separate design
- **Constants drift CI check** (4.1) — needs separate design
