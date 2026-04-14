# Design: `--restore-only` Single-Hub Restore Mode

## Problem

The current ACM switchover tool requires both a primary (source) and secondary (destination) hub to be live and reachable. There is no support for the scenario where the old hub is gone and the operator needs to restore managed clusters onto a new hub from existing S3 backups.

## Proposed Approach

**Approach A: Conditional Phase Skipping** — Add a `--restore-only` flag that skips primary-hub phases and runs a reduced validator set. Reuse existing activation and post-activation modules unchanged.

## Phase Flow

Normal mode:
```
INIT → PREFLIGHT → PRIMARY_PREP → ACTIVATION → POST_ACTIVATION → FINALIZATION → COMPLETED
```

Restore-only mode:
```
INIT → PREFLIGHT(secondary-only) → ACTIVATION → POST_ACTIVATION → FINALIZATION(backups-only) → COMPLETED
```

| Phase | Normal Mode | `--restore-only` Mode |
|-------|------------|----------------------|
| PREFLIGHT | Validates both hubs | Secondary only: BSL (required), namespaces, hub components, observability, RBAC |
| PRIMARY_PREP | Pause backups, disable auto-import, scale Thanos | **Skipped** |
| ACTIVATION | Full or passive restore | Full restore only (`_create_full_restore()`) |
| POST_ACTIVATION | Verify clusters, fix klusterlet, observability | **Identical** (already secondary-only) |
| FINALIZATION | Enable backups + old-hub actions | Enable BackupSchedule only |

**Constraint:** `--restore-only` implies `--method full`. Passive sync requires a live primary and is rejected.

## CLI & Validation

### New argument

```
--restore-only    Restore managed clusters from existing S3 backups onto a single hub.
                  No primary hub required. Implies --method full.
```

### Validation rules when `--restore-only` is set

| Rule | Behavior |
|------|----------|
| `--secondary-context` | **Required** (the restore target) |
| `--primary-context` | **Forbidden** |
| `--method` | Defaults to `full`; rejects `passive` |
| `--old-hub-action` | **Forbidden** |
| `--decommission` | **Forbidden** |
| `--setup` | **Forbidden** |
| `--argocd-resume-only` | **Forbidden** |
| `--argocd-manage` | Allowed |
| `--argocd-resume-after-switchover` | Allowed (with `--argocd-manage`, not with `--validate-only`) |
| `--validate-only` | Allowed |
| `--dry-run` | Allowed |

### State file

`contexts.primary` = `null`; `contexts.secondary` stores the hub context. `ensure_contexts()` already handles null.

## Preflight Validators

| Validator | Runs in restore-only? | Notes |
|-----------|-----------------------|-------|
| KubeconfigValidator | ✅ secondary only | Verify connectivity |
| ToolingValidator | ✅ | System tools |
| NamespaceValidator | ✅ secondary only | Verify backup namespace exists |
| VersionValidator | ✅ secondary only | ACM version (no cross-hub compare) |
| HubComponentValidator | ✅ secondary only | ACM operators running |
| BackupStorageLocationValidator | ✅ **required pass** | BSL exists and Available |
| ObservabilityDetector | ✅ secondary only | Detect observability |
| AutoImportStrategyValidator | ✅ secondary only | ACM 2.14+ check |
| BackupValidator | ❌ | No primary |
| BackupScheduleValidator | ❌ | No primary |
| ClusterDeploymentValidator | ❌ | No primary |
| ManagedClusterBackupValidator | ❌ | No primary |
| PassiveSyncValidator | ❌ | Full restore only |
| RBAC validation | ✅ secondary only | Operator permissions |

## Modules Affected

| Module | Changes |
|--------|---------|
| `acm_switchover.py` | Add `--restore-only` arg; conditional phase skipping; skip primary client creation |
| `lib/validation.py` | New validation rules for `--restore-only` |
| `modules/preflight_coordinator.py` | Accept restore-only mode; select validator subset |
| `modules/finalization.py` | Skip old-hub tasks when no primary context |
| `modules/activation.py` | **No changes** |
| `modules/post_activation.py` | **No changes** |
| `lib/utils.py` | **No changes** (already handles null contexts) |

## Testing

1. **CLI validation tests** — `--restore-only` rules (forbidden combos, required args)
2. **Preflight coordinator test** — correct validator subset in restore-only mode
3. **Orchestrator integration test** — phase flow skips PRIMARY_PREP, correct finalization
4. **State management test** — state works with `primary=None`
5. **End-to-end flow test** — mock full restore-only flow
6. **Regression** — all existing tests pass unchanged
