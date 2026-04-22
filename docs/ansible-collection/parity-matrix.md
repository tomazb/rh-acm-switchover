# Parity Matrix

Date: 2026-04-10 (Phase 6 complete)
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
| machine-readable reports | dual-supported | dual-supported | schema defined in Phase 1 |
| optional checkpoints | dual-supported | dual-supported | Phase 4 collection implementation complete |
| Argo CD management | dual-supported | dual-supported | Phase 5 collection implementation complete |
| discovery | dual-supported | dual-supported | Phase 6 collection implementation complete; scripts/discover-hub.sh remains supported bridge |
| decommission | dual-supported | dual-supported | Phase 6 collection implementation complete |
| RBAC bootstrap | dual-supported | dual-supported | Phase 6 collection implementation complete; scripts/setup-rbac.sh deprecated |

## Milestone Gates

1. Collection preview
2. Dual-supported
3. Collection-primary
4. Python read-only
5. Python retirement

The matrix is the migration control document. Do not invent alternate status vocabularies in follow-on plans or leave approved status changes undocumented here.

## Phase 2 Preflight Check Coverage

| Capability | Python Status | Collection Status | Phase | Notes |
| --- | --- | --- | --- | --- |
| Kubeconfig validation | implemented | dual-supported | 2 | Connectivity and safe-path coverage landed in Phase 2 |
| ACM version validation | implemented | dual-supported | 2 | Collection preflight enforces compatible ACM minor versions |
| Namespace validation | implemented | dual-supported | 2 | Backup namespaces validated on both hubs |
| Observability detection | implemented | dual-supported | 2 | Collection preflight records observability presence or skip state |
| Backup validation | implemented | dual-supported | 2 | Backup, BackupSchedule, and BSL checks landed |
| ManagedCluster backup validation | implemented | dual-supported | 2 | Collection preflight requires managed-cluster backup artifacts |
| ClusterDeployment validation | implemented | dual-supported | 2 | Collection preflight requires Hive ClusterDeployment resources |
| Passive sync validation | implemented | dual-supported | 2 | Secondary passive restore required for passive method |
| RBAC self-validation (SelfSubjectAccessReview) | implemented | dual-supported | 2 | Collection module mirrors Python RBAC gate |
| Structured validation results | implemented | dual-supported | 2 | Report artifact written before role failure |
