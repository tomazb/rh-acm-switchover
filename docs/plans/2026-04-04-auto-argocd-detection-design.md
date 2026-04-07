# Auto ArgoCD Detection in Preflight

**Date:** 2026-04-04
**Version target:** 1.6.0 (MINOR — removes `--argocd-check` flag)

## Problem

Passive GitOps marker detection (the default behavior) only catches ~20-30% of real-world ArgoCD management scenarios. It checks labels/annotations on ~7 ACM resource types but misses resources managed by ArgoCD Applications that don't carry explicit GitOps labels. The thorough deep dive (`--argocd-check`) catches far more by scanning ArgoCD Application `status.resources`, but requires an explicit opt-in flag that most operators don't use.

## Approach

Auto-enable the ArgoCD deep dive during preflight when ArgoCD is detected, and emit an advisory warning when ACM-touching Applications with auto-sync are found without `--argocd-manage`.

### Detection Flow

```
Preflight starts → run validators → auto-detect ArgoCD CRD on both hubs
  ├─ CRD not found on either hub → skip (no extra API calls)
  └─ CRD found on at least one hub → validate read-only RBAC → run deep dive
      ├─ ACM-touching apps found + --argocd-manage NOT set → advisory warning
      ├─ ACM-touching apps found + --argocd-manage set → info only
      └─ No ACM-touching apps → clean pass
```

### CLI Flag Changes

- **Remove** `--argocd-check` — deep dive now runs automatically when ArgoCD CRD is detected.
- **Keep** `--argocd-manage` — opt-in to pause/resume auto-sync.
- **Keep** `--argocd-resume-after-switchover` — opt-in to resume during finalization.
- **Keep** `--argocd-resume-only` — standalone resume mode.
- **Keep** `--skip-gitops-check` — disables all GitOps detection (passive + deep dive).

### RBAC Validation

RBAC mode determination changes from flag-gated to CRD-gated:

| ArgoCD CRD? | `--argocd-manage`? | RBAC validated | Perms required |
|:-:|:-:|:-:|:-:|
| Not found | No | None | — |
| Not found | Yes | None | — |
| Found | No | Yes (critical) | `get/list` applications, `get` CRDs |
| Found | Yes | Yes (critical) | `get/list/patch` applications, `get/list` argocds, `get` CRDs |

`_get_argocd_rbac_mode()` becomes:
- `--skip-gitops-check` → `"none"`
- `--argocd-manage` → `"manage"`
- default → `"check"` (auto-enabled)

`_get_effective_argocd_rbac_mode()` gates on CRD existence (unchanged concept):
- CRD found → validate RBAC for requested mode
- CRD not found → return `"none"` (skip)
- RBAC blocks CRD probe (401/403) → fall through with `"unknown"` install type

Escape hatches: `--skip-rbac-validation` or `--skip-gitops-check`.

### Advisory Warning

When ACM-touching ArgoCD Applications with `automated` sync policy are detected and `--argocd-manage` is NOT set:

**Bash:**
```
⚠ Argo CD Applications managing ACM resources detected on 1 hub(s):
  Primary hub: 3 Application(s) with auto-sync targeting ACM namespaces

  Consider using --argocd-manage to pause auto-sync during switchover.
  Without pausing, Argo CD may revert switchover changes.

  To suppress this warning: --skip-gitops-check
```

**Python:** Same content via `logger.warning`. Non-blocking (preflight passes).

Only shown when apps have `automated` sync policy. Skipped when `--argocd-manage` is set.

## Scope of Changes

### Python

| File | Change |
|------|--------|
| `acm_switchover.py` | Remove `--argocd-check` arg, move detection into preflight flow, add advisory warning |
| `modules/preflight_coordinator.py` | Remove `argocd_check` param, update `_get_argocd_rbac_mode()`, pass `skip_gitops_check` |
| `lib/validation.py` | Remove `--argocd-check` cross-argument rules |

### Bash

| File | Change |
|------|--------|
| `scripts/preflight-check.sh` | Remove `--argocd-check` flag, add auto-detection block |
| `scripts/postflight-check.sh` | Remove `--argocd-check` flag, add auto-detection block |
| `scripts/lib-common.sh` | Extract `probe_argocd_crd()`, add `print_argocd_advisory_warning()` |

### No Changes

- `lib/argocd.py` — detection/pause/resume logic unchanged
- `modules/primary_prep.py` — pause logic unchanged
- `modules/finalization.py` — resume logic unchanged
- `lib/kube_client.py` — API methods unchanged
- `deploy/rbac/` — RBAC manifests unchanged
- `check_rbac.py` — standalone script unchanged
- `argocd-manage.sh` — standalone mutation tool unchanged

### Documentation

| File | Change |
|------|--------|
| `docs/operations/usage.md` | Remove `--argocd-check`, document auto-detection |
| `docs/operations/quickref.md` | Update ArgoCD section |
| `docs/reference/validation-rules.md` | Remove `--argocd-check` rules |
| `scripts/README.md` | Remove `--argocd-check` from flag docs, document auto-detection |
| `AGENTS.md` | Update CLI flags, ArgoCD detection description |
| `CHANGELOG.md` | Add entries under `[Unreleased]` |

### Version Bump (1.5.16 → 1.6.0)

MINOR bump for breaking change (removing `--argocd-check` flag).

## Testing

| Test | What it verifies |
|------|-----------------|
| `test_preflight_auto_detects_argocd_crd` | CRD found → deep dive runs |
| `test_preflight_skips_when_no_argocd_crd` | CRD not found → no deep dive, no RBAC check |
| `test_preflight_rbac_validates_readonly_when_crd_found` | Auto-detect triggers read-only RBAC validation |
| `test_preflight_skip_gitops_disables_auto_detect` | `--skip-gitops-check` disables everything |
| `test_advisory_warning_shown_without_argocd_manage` | Advisory warning logged |
| `test_advisory_warning_hidden_with_argocd_manage` | No warning when managing |
| Update existing `--argocd-check` tests | Adapt or remove |
