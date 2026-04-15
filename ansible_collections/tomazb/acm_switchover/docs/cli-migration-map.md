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
| `--argocd-manage` | `acm_switchover_features.argocd.manage` |

## Phase 5 Capability Status

| Python / CLI Capability | Collection Phase 5 Status |
|-------------------------|---------------------------|
| ArgoCD auto-sync management | dual-supported |
| ArgoCD resume after switchover | **removed** — use `argocd_resume.yml` after Git retarget |

## Phase 6 Non-Core Capability Status

| Python / CLI Capability | Collection Phase 6 Status | Playbook | Notes |
|-------------------------|---------------------------|----------|-------|
| Hub discovery | dual-supported | `playbooks/discovery.yml` | `scripts/discover-hub.sh` remains supported bridge for context enumeration |
| Decommission old hub | dual-supported | `playbooks/decommission.yml` | Requires `acm_switchover_decommission.confirmed: true` or `mode: dry_run` |
| RBAC bootstrap | dual-supported | `playbooks/rbac_bootstrap.yml` | Replaces `scripts/setup-rbac.sh` |

## Phase 2 Capability Status

| Python / CLI Capability | Collection Phase 2 Status | Notes |
|-------------------------|---------------------------|-------|
| Input validation | dual-supported | `acm_input_validate` |
| RBAC validation | dual-supported | `acm_rbac_validate` |
| Version validation | dual-supported | `roles/preflight/tasks/validate_versions.yml` |
| Backup / BSL validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
| Passive restore validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
