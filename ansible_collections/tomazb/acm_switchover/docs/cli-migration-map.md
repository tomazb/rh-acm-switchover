# CLI to Collection Migration Map

| Current CLI Flag | Collection Variable |
| --- | --- |
| `--primary-context` | `acm_switchover_hubs.primary.context` |
| `--secondary-context` | `acm_switchover_hubs.secondary.context` |
| `--method` | `acm_switchover_operation.method` |
| `--old-hub-action` | `acm_switchover_operation.old_hub_action` |
| `--activation-method` | `acm_switchover_operation.activation_method` |
| `--min-managed-clusters` | `acm_switchover_operation.min_managed_clusters` |
| `--manage-auto-import-strategy` | `acm_switchover_features.manage_auto_import_strategy` |
| `--validate-only` | `acm_switchover_execution.mode=validate` |
| `--dry-run` | `acm_switchover_execution.mode=dry_run` |
| `--verbose` / `-v` | `acm_switchover_execution.verbose` |
| `--force` | `acm_switchover_execution.force` |
| `--log-format` | No direct collection variable; Ansible output formatting is controlled by the selected callback/output plugin |
| `--state-file` | `acm_switchover_execution.checkpoint.path` |
| `--reset-state` | `acm_switchover_execution.checkpoint.reset` |
| `--report-dir` | `acm_switchover_execution.report_dir` |
| `--restore-only` | Run `playbooks/restore_only.yml` for the operator workflow; set `acm_switchover_operation.restore_only=true` when invoking a role or alternate playbook directly |
| `--decommission` | `playbooks/decommission.yml` |
| `--setup` | `playbooks/rbac_bootstrap.yml` |
| `--argocd-manage` | `acm_switchover_features.argocd.manage` |
| `--argocd-resume-only` | `playbooks/argocd_resume.yml` |
| `--argocd-resume-on-failure` | `acm_switchover_features.argocd.resume_on_failure` |
| `--skip-gitops-check` | `acm_switchover_features.skip_gitops_check` |
| `--skip-rbac-validation` | `acm_switchover_features.skip_rbac_validation` |
| `--skip-observability-checks` | `acm_switchover_features.skip_observability_checks` |
| `--disable-observability-on-secondary` | Deprecated compatibility flag; use `acm_switchover_features.disable_observability_on_secondary` only when preserving legacy Python invocation shape |
| `--non-interactive` | `acm_switchover_decommission.confirmed=true` for decommission automation |
| `--admin-kubeconfig` | `acm_switchover_hubs.primary.kubeconfig` for the admin bootstrap target |
| `--role {operator,validator,both}` | `acm_switchover_rbac_bootstrap.role` |
| `--include-decommission` | `acm_switchover_rbac_bootstrap.include_decommission` |
| `--skip-kubeconfig-generation` | `acm_switchover_rbac_bootstrap.generate_kubeconfigs=false` |
| `--token-duration` | `acm_switchover_rbac_bootstrap.token_duration` |
| `--output-dir` | `acm_switchover_rbac_bootstrap.output_dir` |

`--min-managed-clusters` semantics match the Python CLI: leave
`acm_switchover_operation.min_managed_clusters` unset or null to derive the
expected non-local ManagedCluster names/count from preflight. In restore-only,
the omitted value requires at least one restored non-local ManagedCluster unless
`acm_switchover_operation.allow_zero_managed_clusters=true` is set. Set
`min_managed_clusters` to `0` to opt into an empty non-local ManagedCluster
target, or set a positive value to enforce that explicit minimum count.

`playbooks/argocd_resume.yml` can recover the Argo CD pause `run_id` from the
configured checkpoint file. When it does, the playbook reads live `kube-system`
namespace UIDs for the hubs it will resume and validates them against the
checkpoint `operation_identity` before including the mutating `argocd_manage`
role. Explicit `run_id` input remains supported and does not require checkpoint
loading.

## Phase 5 Capability Status

| Python / CLI Capability | Collection Phase 5 Status |
|-------------------------|---------------------------|
| ArgoCD auto-sync management | dual-supported |
| ArgoCD resume after switchover | **removed** — use `argocd_resume.yml` after Git retarget |

## Phase 6 Non-Core Capability Status

| Python / CLI Capability | Collection Phase 6 Status | Playbook | Notes |
|-------------------------|---------------------------|----------|-------|
| Hub discovery | dual-supported | `playbooks/discovery.yml` | `scripts/discover-hub.sh` remains supported bridge for context enumeration |
| Decommission old hub | dual-supported | `playbooks/decommission.yml` | Requires explicit non-empty primary kubeconfig/context plus `acm_switchover_decommission.confirmed: true` or `mode: dry_run`; warns and continues if non-operator ACM pods remain after the bounded MultiClusterHub deletion wait |
| RBAC bootstrap | dual-supported | `playbooks/rbac_bootstrap.yml` | Replaces `scripts/setup-rbac.sh` |

## Phase 2 Capability Status

| Python / CLI Capability | Collection Phase 2 Status | Notes |
|-------------------------|---------------------------|-------|
| Input validation | dual-supported | `acm_input_validate` |
| RBAC validation | dual-supported | `acm_rbac_validate` |
| Version validation | dual-supported | `roles/preflight/tasks/validate_versions.yml` |
| Backup / BSL validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
| Passive restore validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
