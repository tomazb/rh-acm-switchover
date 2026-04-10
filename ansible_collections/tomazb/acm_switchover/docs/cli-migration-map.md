# CLI to Collection Migration Map

| Current CLI Flag | Collection Variable |
| --- | --- |
| `--primary-context` | `acm_switchover_hubs.primary.context` |
| `--secondary-context` | `acm_switchover_hubs.secondary.context` |
| `--method` | `acm_switchover_operation.method` |
| `--old-hub-action` | `acm_switchover_operation.old_hub_action` |
| `--activation-method` | `acm_switchover_operation.activation_method` |
| `--min-managed-clusters` | `acm_switchover_operation.min_managed_clusters` |
| `--validate-only` | `acm_switchover_execution.mode=validate` |
| `--dry-run` | `acm_switchover_execution.mode=dry_run` |
| `--state-file` | `acm_switchover_execution.checkpoint.path` |
| `--reset-state` | `acm_switchover_execution.checkpoint.reset` |
