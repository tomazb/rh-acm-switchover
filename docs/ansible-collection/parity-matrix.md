# Parity Matrix

Date: 2026-04-10
Allowed statuses: `Python only`, `dual-supported`, `collection only`, `deprecated`

## Current Migration Baseline

| Capability | Status | Target Milestone | Notes |
| --- | --- | --- | --- |
| preflight validation | Python only | dual-supported | core parity requirement |
| primary prep | Python only | dual-supported | core parity requirement |
| activation | Python only | dual-supported | core parity requirement |
| post-activation verification | Python only | dual-supported | core parity requirement |
| finalization | Python only | dual-supported | core parity requirement |
| RBAC self-validation | Python only | dual-supported | core parity requirement |
| machine-readable reports | Python only | dual-supported | schema defined in Phase 1 |
| optional checkpoints | Python only | dual-supported | runtime work deferred to Phase 4 |
| Argo CD management | Python only | dual-supported | deferred to Phase 5 |
| discovery | Python only | dual-supported | supported bridge during coexistence |
| decommission | Python only | dual-supported | deferred to Phase 6 |
| RBAC bootstrap | Python only | dual-supported | deferred to Phase 6 |

## Milestone Gates

1. Collection preview
2. Dual-supported
3. Collection-primary
4. Python read-only
5. Python retirement

The matrix is the migration control document. Do not invent alternate status vocabularies in follow-on plans.
