# CLI to Collection Migration Map

| Current CLI Flag | Collection Variable |
| --- | --- |
| `--primary-context` | `acm_switchover_hubs.primary.context` |
| `--secondary-context` | `acm_switchover_hubs.secondary.context` |
| `--method` | `acm_switchover_operation.method` |
| `--old-hub-action` | `acm_switchover_operation.old_hub_action` |
| `--activation-method` | `acm_switchover_operation.activation_method` |
| `--min-managed-clusters` | `acm_switchover_operation.min_managed_clusters` |
| `--skip-managed-cluster-expectations` | `acm_switchover_operation.skip_managed_cluster_expectations` |
| `--managed-cluster-expectation-waiver-scope {activation,post_activation,both}` | `acm_switchover_operation.managed_cluster_expectation_waiver.scope` |
| `--managed-cluster-expectation-waiver-actor` | `acm_switchover_operation.managed_cluster_expectation_waiver.actor` |
| `--managed-cluster-expectation-waiver-reason` | `acm_switchover_operation.managed_cluster_expectation_waiver.reason` |
| `--managed-cluster-expectation-waiver-request-id` | `acm_switchover_operation.managed_cluster_expectation_waiver.request_id` (optional) |
| `--repair-migration-cleanup` | No separate variable; a `acm_switchover_operation.migration_cleanup_repair` block that names any field is the request |
| `--cleanup-repair-actor` | `acm_switchover_operation.migration_cleanup_repair.actor` |
| `--cleanup-repair-reason` | `acm_switchover_operation.migration_cleanup_repair.reason` |
| `--cleanup-repair-evidence` (repeatable) | `acm_switchover_operation.migration_cleanup_repair.inspected_evidence` (list) |
| `--cleanup-repair-run-id` | `acm_switchover_operation.migration_cleanup_repair.run_id` |
| `--cleanup-repair-operation-id` | `acm_switchover_operation.migration_cleanup_repair.operation_id` |
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
| `--acknowledge-observability-not-migrated` | `acm_switchover_decommission.acknowledge_observability_not_migrated` — converts only a destination hub proven to have no observability into a proceed for the MultiClusterObservability teardown; valid only with `old_hub_action: decommission` on an integrated switchover, rejected in validate mode, with `restore_only`, and for the standalone decommission playbook |
| `--non-interactive` | `acm_switchover_decommission.confirmed=true` for decommission automation |
| `--admin-kubeconfig` | `acm_switchover_hubs.primary.kubeconfig` for the admin bootstrap target |
| `--role {operator,validator,both}` | `acm_switchover_rbac_bootstrap.role` |
| `--include-decommission` | `acm_switchover_rbac_bootstrap.include_decommission` |
| `--skip-kubeconfig-generation` | `acm_switchover_rbac_bootstrap.generate_kubeconfigs=false` |
| `--token-duration` | `acm_switchover_rbac_bootstrap.token_duration` |
| `--output-dir` | `acm_switchover_rbac_bootstrap.output_dir` |

`--min-managed-clusters` semantics match the Python CLI: leave
`acm_switchover_operation.min_managed_clusters` unset or null to derive the
expected non-local ManagedCluster names/count from preflight. Restore-only activation defaults to a minimum of one. To permit an empty destination through both activation and post-activation, explicitly set `min_managed_clusters: 0` and have no expected names. `allow_zero_managed_clusters` affects only post-activation resolution when the minimum is omitted and no names are expected. An explicit
`min_managed_clusters` is an additional count floor beside the expected names,
never a replacement for them: `0` opts into an empty non-local ManagedCluster
target only when no expected name exists, and a positive value enforces that
minimum count while the derived names stay enforced. `allow_zero_managed_clusters`
likewise resolves to false whenever expected names exist.

The expected-name waiver and the cleanup-repair request are validated identically
in both form factors (`tests/fixtures/validation_parity_cases.yml`). A waiver needs
`skip_managed_cluster_expectations: true`, a `scope` and a non-empty `actor` and
`reason`; it is rejected in validate mode, for the standalone decommission playbook,
for `restore_only` activation scopes, and at preflight when a covered scope has no
expected name. A cleanup repair is accepted only as the complete set and is an
acknowledgement request, not evidence of a delete. Neither input changes the
recorded expectation.

An executing collection run records the migration journal in the checkpoint, so it needs
`acm_switchover_execution.checkpoint.enabled: true`; the Python CLI always has its state
file. `--reset-state` and `checkpoint.reset: true` are the only resets that discard a
journal. Where the Python CLI recovers a resume refused for missing post-activation or
restore-completion evidence by setting `current_phase` in its state file, the collection
uses `checkpoint.reset_from: post_activation` (or `activation`), which keeps the journal.
This is a pre-existing difference in rewind tooling, not a behavior divergence.

The `old_hub_action: secondary` finalization adapter
(`roles/finalization/tasks/disable_old_hub_observability.yml`) gates its MultiClusterObservability delete on
fresh destination-observability proof the same way `decommission` does, but with no
`acknowledge_observability_not_migrated` override available on that path — the acknowledgement variable is
valid only with `old_hub_action: decommission`. In execute mode the adapter also requires
`acm_switchover_execution.checkpoint.enabled: true` (its shared teardown persists a durable identity map
before the delete) and refuses `mode: validate` outright; both refusals fire before any read or delete.

`playbooks/argocd_resume.yml` can recover the Argo CD pause `run_id` from the
configured checkpoint file. When it does, the playbook reads live `kube-system`
namespace UIDs for the hubs it will resume and validates them against the
checkpoint `operation_identity` before including the mutating `argocd_manage`
role. Explicit `run_id` input remains supported and does not require checkpoint
loading.

For a normal two-hub switchover, both form factors reject matching context names
and fail when fresh live `kube-system` Namespace UIDs show the same physical
Kubernetes cluster. The collection performs that check in its preflight identity
barrier. It uses the test-only non-live identity override only in `validate` or
`dry_run`; `execute` and native `--check` still read live UIDs. `--restore-only`
maps to a secondary-only flow, and `--decommission` maps to standalone
decommission, so neither flow uses this normal two-hub predicate.

## Phase 5 Capability Status

| Python / CLI Capability | Collection Phase 5 Status |
|-------------------------|---------------------------|
| ArgoCD auto-sync management | dual-supported |
| ArgoCD resume after switchover | **removed** — use `argocd_resume.yml` after Git retarget |

## Phase 6 Non-Core Capability Status

| Python / CLI Capability | Collection Phase 6 Status | Playbook | Notes |
|-------------------------|---------------------------|----------|-------|
| Hub discovery | dual-supported | `playbooks/discovery.yml` | `scripts/discover-hub.sh` remains supported bridge for context enumeration |
| Decommission old hub | dual-supported | `playbooks/decommission.yml` | Requires explicit non-empty primary kubeconfig/context plus `acm_switchover_decommission.confirmed: true` or `mode: dry_run`; MultiClusterHub teardown records the target UID and the captured operator identity before a UID-guarded delete, then classifies every remaining ACM Pod by owner chain up to the recorded operator Deployment, and fails closed on drain timeout, on an unreadable pass, or on a recorded identity that no longer holds — the same contract as the Python CLI (#290), except for two operator-approved, fail-closed #317 strict-read divergences (built-in named reads, and custom-resource named reads from a stale cached route; the former malformed-`groupVersion` divergence was aligned by #321) recorded in [coexistence.md](coexistence.md). Its RBAC surface now also requires the `open-cluster-management` reads `clusterserviceversions` (get, list), `deployments` (get) and `replicasets` (get) plus `multiclusterhubs` get on the decommission extension; see `docs/deployment/rbac-requirements.md`. MultiClusterObservability deletion goes through a UID-preconditioned guarded delete with a durable per-resource phase record and a fresh completion proof; the standalone playbook declares `acm_switchover_standalone_decommission: true`, never evaluates the destination-observability gate, and refuses a truthy `acknowledge_observability_not_migrated` in `pre_tasks` before any read |
| RBAC bootstrap | dual-supported | `playbooks/rbac_bootstrap.yml` | Replaces `scripts/setup-rbac.sh` |

## Phase 2 Capability Status

| Python / CLI Capability | Collection Phase 2 Status | Notes |
|-------------------------|---------------------------|-------|
| Input validation | dual-supported | `acm_input_validate` |
| RBAC validation | dual-supported | `acm_rbac_validate` |
| Version validation | dual-supported | `roles/preflight/tasks/validate_versions.yml` |
| Backup / BSL validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
| Passive restore validation | dual-supported | `roles/preflight/tasks/validate_backups.yml` |
