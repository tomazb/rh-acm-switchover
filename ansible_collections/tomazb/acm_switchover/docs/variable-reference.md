# Variable Reference

## Namespaces

- `acm_switchover_hubs`
- `acm_switchover_operation`
- `acm_switchover_features`
- `acm_switchover_execution`
- `acm_switchover_rbac`

## Notes

- The collection public API is grouped variables, not a flat CLI flag layer.
- `decommission`, `argocd_resume`, and `setup` remain deferred.
- checkpoint keys are contract-only in Phase 1.

## Preflight Result Facts

| Variable | Type | Description |
|----------|------|-------------|
| `acm_switchover_validation_results` | list[dict] | Accumulated preflight findings |
| `acm_switchover_preflight_summary.passed` | bool | False when any critical finding fails |
| `acm_switchover_preflight_result.report` | dict | Structured preflight report payload |
| `acm_switchover_preflight_result.path` | string | Path to the written JSON report |

## Execution Phase Result Facts

Each role publishes a typed result fact. All facts persist in play scope and are aggregated into `switchover-report.json`.

| Variable | Phase | Key Fields |
|----------|-------|------------|
| `acm_switchover_primary_prep_result` | primary_prep | `status`, `changed`, `pause_backups`, `auto_import`, `observability` |
| `acm_switchover_activation_result` | activation | `status`, `changed`, `method`, `restore`, `patch` |
| `acm_switchover_post_activation_result` | post_activation | `status`, `changed`, `summary.passed`, `summary.total`, `summary.pending` |
| `acm_switchover_finalization_result` | finalization | `status`, `changed`, `old_hub_action` |

### post_activation summary fields

| Field | Type | Description |
|-------|------|-------------|
| `summary.passed` | bool | True when all clusters are joined and available |
| `summary.total` | int | Total number of ManagedClusters evaluated |
| `summary.pending` | list[str] | Names of clusters not yet joined or available |
