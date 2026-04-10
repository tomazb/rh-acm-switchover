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
