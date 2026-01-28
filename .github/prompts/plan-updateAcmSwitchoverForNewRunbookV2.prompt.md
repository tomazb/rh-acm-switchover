## Update ACM Switchover for New Runbook (v2)

### 1. Establish Phase-to-Runbook-Step Mapping (NEW — HIGH PRIORITY)

- **Create mapping table**: Add explicit documentation in [docs/development/architecture.md](docs/development/architecture.md) mapping Python phases to runbook steps:

  | Python Phase | Runbook Steps | Module | Key Actions |
  |--------------|---------------|--------|-------------|
  | `PREFLIGHT` | Step 0 | `preflight_coordinator.py` | All prerequisites |
  | `PRIMARY_PREP` | Steps 1-3 (Method 1) / F1-F3 (Method 2) | `primary_prep.py` | Pause backups, disable-auto-import, Thanos |
  | `ACTIVATION` | Steps 4-5 (Method 1) / F4-F5 (Method 2) | `activation.py` | Verify passive sync or create full restore, activate clusters |
  | `POST_ACTIVATION` | Steps 6-10 / F6 | `post_activation.py` | Verify clusters, restart Observatorium, metrics |
  | `FINALIZATION` | Steps 11-12 | `finalization.py` | Enable backups, verify integrity |
  | (manual) | Step 13 | — | Inform stakeholders (out-of-band) |
  | (separate) | Step 14 | `decommission.py` | Decommission old hub |
  | (separate) | Rollback 1-5 | (manual/partial) | Rollback procedures |

  **Method 2 (Full Restore) Support**: Use `--method full` CLI flag (examples: `--method=full` or `--method full`). The Python tool runs `PRIMARY_PREP` → `ACTIVATION` → `POST_ACTIVATION` → `FINALIZATION` for both methods. Method 2 creates `restore-acm-full` via `_create_full_restore()` instead of patching passive sync.

  **Caveat**: Primary hub must be reachable; the tool does not currently support "primary unreachable" full-restore-only execution.

- **Add inline comments**: In each module (`primary_prep.py`, `activation.py`, etc.), add header comments referencing the corresponding runbook steps.
- **Update AGENTS.md**: Extend the "Phase Flow" section with this mapping.

### 2. Add Missing Constants to lib/constants.py (NEW)

- **`IMMEDIATE_IMPORT_ANNOTATION`**: `"import.open-cluster-management.io/immediate-import"`
- **`MANAGED_CLUSTER_RESTORE_NAME`**: `"restore-acm-activate"` (for Option B)
- **`IMPORT_CONTROLLER_CONFIG_CM`**: `"import-controller-config"` (for auto-import strategy ConfigMap)

### 3. Verify and Align primary_prep.py with Steps 2-3 / F2-F3 (NEW)

- **Step 2 / F2 — Disable auto-import**: Verify `_disable_auto_import()` adds the `cluster.open-cluster-management.io/disable-auto-import: ""` annotation to all ManagedClusters (except `local-cluster`).
- **Step 3 / F3 — Thanos Compactor**: Verify `_scale_down_thanos_compactor()` scales down **StatefulSet `observability-thanos-compact`** (not a deployment) to 0 replicas in the observability namespace. Only runs if observability is detected.
- **Observatorium API pause**: Confirm optional pause of Observatorium API gateway is handled (or document as manual step).

### 4. Align Core Python Workflow with Updated Runbook

- **Implement ACM 2.14 `immediate-import` behavior**: In [modules/activation.py](modules/activation.py), add logic to annotate non-local `ManagedCluster` objects with `import.open-cluster-management.io/immediate-import=''` when `autoImportStrategy` is `ImportOnly`. This applies when:
  - The `import-controller-config` ConfigMap is missing (defaults to `ImportOnly`), OR
  - The ConfigMap explicitly sets `autoImportStrategy: ImportOnly`
  - Applies to **both** Method 1 (passive activation) and Method 2 (full restore) flows
- **Support both activation options for Method 1 (Step 5)**:
  - **Option A (default)**: Patch existing restore with `veleroManagedClustersBackupName: latest`
  - **Option B**: Delete passive sync restore and create `restore-acm-activate` resource
  - Add CLI flag `--activation-method=patch|restore` to select (examples: `--activation-method=restore` or `--activation-method restore`)
- **Clarify auto-import strategy handling (Step 4b / F4 & Step 7)**:
  - Step 4b / F4: Optional `import-controller-config` ConfigMap creation (before activation). **Guards**: Only apply `ImportAndSync` when (a) hub has existing non-local ManagedClusters restored, AND (b) intended for future switchback. Otherwise skip.
  - Step 7: Delete ConfigMap after clusters attached (in `post_activation.py` or `finalization.py`). **Guard**: Only delete if ConfigMap was explicitly set during this switchover (record a marker in state, e.g., a state file flag or a ConfigMap annotation, and check it before deletion).
- **Enhance backup integrity verification (Step 12)**: Expand verification in [modules/finalization.py](modules/finalization.py) to check latest backup status, Velero logs, and recent timestamp.

### 5. Add Optional MCO Deletion for Non-Decommission Flows (NEW)

- **Between Steps 10-11**: Add optional step in `post_activation.py` or `finalization.py` to disable Observability on old secondary hub by deleting `MultiClusterObservability` resource.
- **CLI flag**: `--disable-observability-on-secondary` or similar.
- **Safety**: Only applies when not doing full decommission.
- **GitOps coordination**: If MCO is managed by GitOps (ArgoCD/Flux), surface warning to coordinate deletion to avoid drift/recreation.
- **Bug detection**: If observability pods remain after MCO deletion, surface warning about potential product bug (per runbook note).

### 6. Align Bash Scripts With Runbook (Preflight/Postflight/Decommission)

- **Preflight script vs Step 0**: Review [scripts/preflight-check.sh](scripts/preflight-check.sh) against updated prerequisites:
  - BSL availability, DPA status, ACM version matching
  - `preserveOnDelete=true` on ClusterDeployments
  - Passive sync restore state
  - (ACM 2.14+) `autoImportStrategy` visibility
- **Postflight script vs Steps 6-12**: Align [scripts/postflight-check.sh](scripts/postflight-check.sh):
  - Managed cluster availability/join states (Step 6)
  - Observatorium API restart confirmation (Step 8)
  - Pod health (Step 9), metrics presence (Step 10)
  - Backup resumption / latest backup age (Steps 11-12)
- **Add safety warnings**: Document warning about never re-enabling Thanos/Observatorium on old hub after switchover.

### 7. Non-Runbook Improvements (Findings Report)

> **Note**: These items are not runbook-driven but improve code quality and reliability. Consider splitting into a separate change set.

- **Polling interval optimization (Issue #16)**: Introduce 5-10 second polling for fast operations (Velero restores) in [lib/waiter.py](lib/waiter.py).
- **Timeout behavior (Issue #19)**: Adjust `wait_for_condition` to not silently succeed after timeout, or make configurable.

### 8. Update Tests to Match New Behavior

- **Unit tests**: Cover `immediate-import` annotation, both activation options, backup integrity verification, polling changes.
- **Integration tests**: Cover `primary_prep.py` steps, auto-import strategy lifecycle.
- **Script tests**: Validate updated preflight/postflight checks.

### 9. Sync Documentation and Operational Guides

- **Architecture docs**: Add phase-to-step mapping table (from Section 1).
- **Usage docs**: Document new CLI options (`--activation-method`, `--disable-observability-on-secondary`).
- **Claude SKILLS**: Update operations SKILLS to match runbook procedures.
- **Safety documentation**: Add warnings about Thanos/Observatorium on old hub.

### 10. Versioning and Changelog

- Bump versions in all locations per AGENTS.md checklist.
- Update CHANGELOG with runbook-alignment changes.

## Todos Summary

| ID | Description | Priority | Notes |
|----|-------------|----------|-------|
| `add-phase-runbook-mapping` | Create phase-to-runbook-step mapping table (incl. Step 13, Method 2) | HIGH | Foundational for maintainability |
| `add-constants` | Add `IMMEDIATE_IMPORT_ANNOTATION`, `MANAGED_CLUSTER_RESTORE_NAME` constants | HIGH | — |
| `align-core-python` | Implement immediate-import (both methods), Option B, auto-import lifecycle with guards | HIGH | — |
| `verify-primary-prep` | Verify Steps 2-3 / F2-F3 implementation (fix StatefulSet name) | MEDIUM | — |
| `add-mco-deletion` | Optional MCO deletion with GitOps/bug guardrails | MEDIUM | — |
| `align-scripts-runbook` | Update preflight/postflight scripts | MEDIUM | — |
| `fix-open-findings` | Polling interval and timeout behavior fixes | MEDIUM | Non-runbook; consider separate PR |
| `update-tests` | Test coverage for new behaviors | MEDIUM | — |
| `sync-docs-and-skills` | Documentation and SKILLS sync | MEDIUM | — |
