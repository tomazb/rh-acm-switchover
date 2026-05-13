# Parity Matrix

Date: 2026-05-12 (1.7.10 production resilience hardening)
Allowed statuses: `Python only`, `dual-supported`, `collection only`, `deprecated`

Intentional parity changes require explicit operator approval before implementation. When a capability's support status changes, or approved work intentionally leaves a `dual-supported` capability divergent, record that decision here and update the related mapping/coexistence docs in the same change.

## Current Migration Baseline

| Capability | Status | Target Milestone | Notes |
| --- | --- | --- | --- |
| preflight validation | dual-supported | dual-supported | core parity requirement |
| primary prep | dual-supported | dual-supported | core parity requirement |
| activation | dual-supported | dual-supported | core parity requirement |
| post-activation verification | dual-supported | dual-supported | core parity requirement |
| finalization | dual-supported | dual-supported | core parity requirement |
| RBAC self-validation | dual-supported | dual-supported | core parity requirement |
| machine-readable reports | dual-supported | dual-supported | Python and collection both write schema version `1.0` report artifacts |
| optional checkpoints | dual-supported | dual-supported | Phase 4 collection implementation complete |
| Argo CD management | dual-supported | dual-supported | Phase 5 collection implementation complete |
| discovery | dual-supported | dual-supported | Collection provides classification/reporting and bridge guidance; `scripts/discover-hub.sh` remains the full context-enumeration bridge |
| decommission | dual-supported | dual-supported | Collection defaults observability detection to `auto` and waits for ACM/observability workload pods during teardown |
| RBAC bootstrap | dual-supported | dual-supported | Phase 6 collection implementation complete; scripts/setup-rbac.sh deprecated |

## Milestone Gates

1. Collection preview
2. Dual-supported
3. Collection-primary
4. Python read-only
5. Python retirement

The matrix is the migration control document. Do not invent alternate status vocabularies in follow-on plans or leave approved status changes undocumented here.

## Parity Enforcement

Cross-implementation contracts are enforced by automated parity tests in `tests/`:

| Test File | Contract Enforced |
| --- | --- |
| `tests/test_constants_parity.py` | ~18 shared constants match between `lib/constants.py` and `plugins/module_utils/constants.py` |
| `tests/test_rbac_collection_parity.py` | Python `RBACValidator` permission matrix matches collection `acm_rbac_validate` expansion |
| `tests/test_argocd_constants_parity.py` | `ACM_KINDS`, `ACM_NAMESPACES`, and `build_pause_patch` match between Python and collection |
| `tests/test_validation_parity.py` and collection `test_validation_parity_fixture.py` | Shared validation fixture keeps Argo CD option rules and safe-path policy aligned |
| `tests/release/scenarios/test_runtime_parity.py` | Release runtime parity required fields cover switchover, restore-only, decommission, RBAC/bootstrap, checkpoint, and report artifact contracts |
| `tests/release/adapters/test_ansible.py` and `tests/release/scenarios/test_catalog.py` | Release scenario wiring discovers decommission artifacts and keeps optional resilience scenarios selectable |

These tests run in CI and must remain green. Add a new test or assertion whenever a new shared constant or behavioral contract is added.

## Phase 2 Preflight Check Coverage

| Capability | Python Status | Collection Status | Phase | Notes |
| --- | --- | --- | --- | --- |
| Kubeconfig validation | implemented | dual-supported | 2 | Connectivity and safe-path coverage landed in Phase 2 |
| ACM version validation | implemented | dual-supported | 2 | Collection preflight enforces exact ACM version equality like Python |
| Namespace validation | implemented | dual-supported | 2 | Backup namespaces validated on both hubs |
| Observability detection | implemented | dual-supported | 2 | Collection preflight records observability presence or skip state |
| Backup validation | implemented | dual-supported | 2 | Backup, BackupSchedule, BSL, latest Completed backup, and in-progress wait checks are aligned |
| ManagedCluster backup validation | implemented | dual-supported | 2 | Collection preflight requires a completed managed-clusters backup when joined clusters exist |
| ClusterDeployment validation | implemented | dual-supported | 2 | Collection preflight requires Hive ClusterDeployment resources |
| Passive sync validation | implemented | dual-supported | 2 | Secondary passive restore required for passive method |
| RBAC self-validation (SelfSubjectAccessReview) | implemented | dual-supported | 2 | Collection module mirrors Python RBAC gate |
| Structured validation results | implemented | dual-supported | 2 | Report artifact written before role failure |
