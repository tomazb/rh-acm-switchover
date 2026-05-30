# Variable Reference

## Namespaces

- `acm_switchover_hubs`
- `acm_switchover_operation`
- `acm_switchover_features`
- `acm_switchover_execution`
- `acm_switchover_managed_clusters`
- `acm_switchover_decommission`
- `acm_switchover_rbac_bootstrap`
- `acm_switchover_discovery`

## Notes

- The collection public API is grouped variables, not a flat CLI flag layer.
- Checkpoint files are collection-owned JSON state and are not interchangeable with Python CLI state files.

## Core Input Variables

### `acm_switchover_hubs`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `primary.context` | str | required for switchover and decommission | Primary hub kubeconfig context |
| `primary.kubeconfig` | str | required for decommission; caller environment for other flows | Primary hub kubeconfig path |
| `secondary.context` | str | required for switchover and restore-only | Secondary or restore target hub kubeconfig context |
| `secondary.kubeconfig` | str | caller environment | Secondary or restore target hub kubeconfig path |

### `acm_switchover_operation`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `method` | `passive`, `full` | `passive` | Switchover activation strategy |
| `old_hub_action` | `secondary`, `decommission`, `none` | `secondary` | Finalization action for the old hub |
| `activation_method` | `patch`, `restore` | `patch` | Passive activation mechanism; `restore` is valid only with `method=passive` |
| `min_managed_clusters` | int or null | null | Omitted/null derives expected non-local ManagedCluster names/count from preflight; restore-only omitted requires at least one restored non-local ManagedCluster unless `allow_zero_managed_clusters` is explicitly enabled; explicit `0` opts into an empty non-local ManagedCluster target like the Python CLI; positive values enforce that minimum count |
| `restore_only` | bool | `false` | Set by `playbooks/restore_only.yml`; direct role invocations must set it explicitly |

### `acm_switchover_managed_clusters`

Optional mapping of managed-cluster names to direct kubeconfig data used for
klusterlet probing and remediation. When entries include `kubeconfig`,
preflight RBAC validation also checks the managed-cluster
`open-cluster-management-agent` permissions through that kubeconfig before
post-activation reaches klusterlet operations.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `<cluster>.kubeconfig` | str | optional | Managed-cluster kubeconfig path for direct klusterlet probe/remediation and managed-cluster RBAC validation |
| `<cluster>.context` | str | optional | Managed-cluster kubeconfig context. If omitted, the kubeconfig current context is used for RBAC validation and module calls |

### `acm_switchover_features`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `manage_auto_import_strategy` | bool | `false` | Temporarily set ACM 2.14+ auto-import strategy for activation |
| `skip_observability_checks` | bool | `false` | Explicitly bypass Observability preflight, primary prep, post-activation, and old-hub finalization checks. Observability failures are blocking by default when this is `false` |
| `skip_gitops_check` | bool | `false` | Skip read-only GitOps marker checks |
| `skip_rbac_validation` | bool | `false` | Skip RBAC self-validation in preflight |
| `disable_observability_on_secondary` | bool | `false` | Deprecated compatibility setting; old-hub MCO deletion is now automatic when keeping the old hub as secondary |
| `argocd.manage` | bool | `false` | Pause ACM-touching Argo CD Applications during switchover; blocks ApplicationSet-managed child apps, unknown/stale Application impact, and failed post-patch pause verification |
| `argocd.resume_on_failure` | bool | `false` | Best-effort resume of Applications paused by the current run if switchover fails |
| `klusterlet.strict_remediation` | bool | `false` | Module-level immediate failure toggle for klusterlet remediation. The role still fails after the post-remediation re-check when remediation failed or klusterlets remain on the wrong hub |

### `acm_switchover_execution`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | `execute`, `validate`, `dry_run` | `execute` | Runtime mode. `validate` runs the checkpoint preflight (loading and verifying checkpoint state, including hub-identity binding) along the same path used by `execute`, but does not persist checkpoint transitions or perform mutations — so misconfigured checkpoints fail fast before any execute-mode run. `dry_run` also does not persist checkpoint transitions. Native Ansible check mode is non-mutating even when this is `execute`. |
| `verbose` | bool | `false` | Enable verbose collection output where roles expose additional debug detail |
| `force` | bool | `false` | Operator override flag reserved for compatibility with Python CLI state-reset workflows |
| `report_dir` | str | `./artifacts` | Directory for JSON report artifacts; validated by the safe-path policy |
| `checkpoint.enabled` | bool | `false` | Enable file-backed phase checkpointing |
| `checkpoint.backend` | str | `file` | Checkpoint backend; only `file` is currently supported |
| `checkpoint.path` | str | `.state/switchover.json` | Checkpoint JSON path; validated by the safe-path policy before controller-side reads or writes |
| `checkpoint.reset` | bool | `false` | Start a fresh checkpoint from `preflight` and ignore existing checkpoint content |
| `checkpoint.reset_from` | phase name or empty string | `""` | Remove the named phase and every downstream phase from `completed_phases`; used for safe retries such as Argo CD resume-on-failure |
| `concurrency.klusterlet_probe_workers` | int | `10` | Maximum concurrent klusterlet probe workers; set to `1` for sequential probing |
| `concurrency.klusterlet_remediation_workers` | int | `10` | Maximum concurrent klusterlet remediation workers; set to `1` for sequential remediation |
| `timeouts.klusterlet_request_seconds` | int | `30` | Per Kubernetes API request timeout for direct klusterlet probe/remediation calls |
| `timeouts.klusterlet_worker_seconds` | int | `180` | Worker future timeout window for each parallel klusterlet probe/remediation batch |
| `timeouts.klusterlet_recheck_seconds` | int | `300` | Total post-remediation wait for klusterlet hub secret convergence before failing persistent wrong-hub results |
| `timeouts.klusterlet_recheck_interval_seconds` | int | `10` | Poll interval for post-remediation klusterlet hub secret convergence checks |

The full variable name is `acm_switchover_execution.checkpoint.reset_from`.
Checkpoint `reset_from` accepts `preflight`, `primary_prep`, `activation`, `post_activation`, or `finalization`.

### Argo CD management safety

When `acm_switchover_features.argocd.manage` is `true`, the collection refuses to patch unsafe Applications. Auto-sync Applications owned by an ApplicationSet block the run when they touch ACM resources because the parent controller can revert child changes. Remediate by pausing or updating the parent ApplicationSet, generator, or template before retrying.

Auto-sync Applications also block when `status.resources` is empty or stale because the role cannot rule out ACM impact. Refresh or sync the Application until Argo CD reports current resources, or inspect and pause it manually. After patching, the role re-reads each Application and fails if `spec.syncPolicy.automated` remains enabled.

## Preflight Result Facts

| Variable | Type | Description |
|----------|------|-------------|
| `acm_switchover_validation_results` | list[dict] | Accumulated preflight findings |
| `acm_switchover_preflight_summary.passed` | bool | False when any critical finding fails |
| `acm_switchover_preflight_result.report` | dict | Structured preflight report payload |
| `acm_switchover_preflight_result.path` | string | Path to the written JSON report |
| `acm_switchover_expected_managed_cluster_names` | list[str] | Non-local ManagedCluster names observed on the primary during preflight; empty for restore-only because no primary is available |
| `acm_switchover_expected_managed_cluster_count` | int | Count derived from `acm_switchover_expected_managed_cluster_names`; restore-only records `0` here but post-activation still requires a restored non-local ManagedCluster unless `allow_zero_managed_clusters` is explicitly enabled |
| `acm_switchover_primary_has_observability` | bool | Effective primary Observability detection after `skip_observability_checks`; persisted in checkpoints for old-hub finalization behavior |
| `acm_switchover_secondary_has_observability` | bool | Effective secondary Observability detection after `skip_observability_checks`; persisted in checkpoints for post-activation Observability verification |

## Execution Phase Result Facts

Each role publishes a typed result fact. All facts persist in play scope and are aggregated into `switchover-report.json`.

| Variable | Phase | Key Fields |
|----------|-------|------------|
| `acm_switchover_primary_prep_result` | primary_prep | `status`, `changed`, `pause_backups`, `auto_import`, `observability` |
| `acm_switchover_activation_result` | activation | `status`, `changed`, `method`, `restore`, `patch` |
| `acm_switchover_post_activation_result` | post_activation | `status`, `changed`, `summary.passed`, `summary.total`, `summary.pending`, `klusterlet_probe`, `klusterlet_remediation` |
| `acm_switchover_finalization_result` | finalization | `status`, `changed`, `old_hub_action` |

### post_activation summary fields

| Field | Type | Description |
|-------|------|-------------|
| `summary.passed` | bool | True when all clusters are joined and available |
| `summary.total` | int | Total number of ManagedClusters evaluated |
| `summary.pending` | list[str] | Names of clusters not yet joined or available |
| `klusterlet_probe.results` | list[dict] | Per-cluster klusterlet hub comparison results |
| `klusterlet_probe.wrong_hub_clusters` | list[str] | Clusters whose klusterlet kubeconfig still points at a different hub |
| `klusterlet_probe.skipped_clusters` | list[str] | Clusters skipped because direct klusterlet probing could not run, commonly because no managed-cluster kubeconfig was supplied |
| `klusterlet_remediation.results` | list[dict] | Per-cluster remediation steps and status |
| `klusterlet_remediation.failed_clusters` | list[str] | Clusters where remediation failed; non-empty fails post-activation after the klusterlet re-check |

### post_activation input variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `acm_switchover_managed_clusters` | dict | `{}` | Optional managed-cluster kubeconfigs used for preflight managed-cluster RBAC validation plus klusterlet probe and remediation |
| `acm_switchover_execution.concurrency.klusterlet_probe_workers` | int | `10` | Maximum concurrent klusterlet probe workers; set to `1` for sequential probing |
| `acm_switchover_execution.concurrency.klusterlet_remediation_workers` | int | `10` | Maximum concurrent klusterlet remediation workers; set to `1` for sequential remediation |
| `acm_switchover_execution.timeouts.klusterlet_request_seconds` | int | `30` | Per Kubernetes API request timeout for direct klusterlet probe/remediation calls |
| `acm_switchover_execution.timeouts.klusterlet_worker_seconds` | int | `180` | Worker future timeout window for each parallel klusterlet probe/remediation batch |
| `acm_switchover_execution.timeouts.klusterlet_recheck_seconds` | int | `300` | Total post-remediation wait for klusterlet hub secret convergence before failing persistent wrong-hub results |
| `acm_switchover_execution.timeouts.klusterlet_recheck_interval_seconds` | int | `10` | Poll interval for post-remediation klusterlet hub secret convergence checks |
| `acm_switchover_features.klusterlet.strict_remediation` | bool | `false` | Module-level immediate failure toggle. Leave `false` for the role's normal remediation, re-check, then fail behavior |

Post-activation klusterlet probing is stricter than best-effort status logging:
wrong-hub klusterlets are remediated, probed again, and then fail the role if
they still point at the wrong hub or if remediation reported failed clusters.
Clusters without entries in `acm_switchover_managed_clusters` are skipped for
direct klusterlet probing/remediation because the collection has no managed
cluster kubeconfig to reach them. That skip is non-fatal only when
ManagedCluster readiness already passed; if the cluster is still pending, the
ManagedCluster verification result remains the blocking failure.

Klusterlet worker future timeouts are treated as failed cluster results even when
`klusterlet.strict_remediation` is `false`, because a timed-out worker means the
module could not establish the cluster's post-switchover state.

When `acm_switchover_features.skip_observability_checks` is `false` and
Observability is detected, Observability failures block the workflow by default:
Thanos scale-down in `primary_prep`, post-activation scale-up/readiness/restart
verification, and old-hub Observability termination in `finalization` must
succeed. Set `skip_observability_checks: true` only as an explicit bypass when
Observability will be handled separately.

## Phase 6 Non-Core Input Variables

### `acm_switchover_decommission`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `confirmed` | bool | `false` | Must be `true` to proceed outside `dry_run` mode |
| `interactive` | bool | `false` | Reserved for future interactive prompting |
| `has_observability` | `auto`, `true`, `false` | `auto` | Auto-detect `open-cluster-management-observability` by default; `true`/`false` force the observability deletion path on or off. Auto-detection fails closed on API errors; only a successful lookup with no namespace disables Observability deletion/checks. |

Decommission always re-checks matching Hive `ClusterDeployment` resources before
live non-local `ManagedCluster` deletion. Matching ClusterDeployments must have
`spec.preserveOnDelete=true`; unsafe values or unclassified Hive API lookup
errors, including a missing Hive API/CRD, stop the role before ManagedClusters
are deleted. A successful lookup with no matching ClusterDeployments is accepted.

Standalone decommission requires non-empty
`acm_switchover_hubs.primary.kubeconfig` and
`acm_switchover_hubs.primary.context`. The role refuses to rely on Ansible's
implicit or default kube context before any Kubernetes operation runs.

After deleting `MultiClusterHub` resources, decommission waits for non-operator
ACM workload pods to terminate. If pods remain after the bounded wait, the role
warns and continues so the result matches the Python CLI's warning behavior.

### `acm_switchover_rbac_bootstrap`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `role` | str | `operator` | Role profile: `operator` (write) or `validator` (read-only) |
| `include_decommission` | bool | `false` | Append decommission-scoped ClusterRole manifests for `ClusterDeployment` list safety validation plus `ManagedCluster`, `MultiClusterHub`, and `MultiClusterObservability` delete; baseline operator RBAC already grants `MultiClusterObservability` delete for normal old-hub finalization |
| `generate_kubeconfigs` | bool | `false` | Generate kubeconfigs after manifest apply |
| `validate_permissions` | bool | `false` | Run `acm_rbac_validate` after apply |
| `token_duration` | str | `48h` | Token validity duration for generated service account kubeconfigs |
| `output_dir` | str | `./kubeconfigs` | Directory for generated service account kubeconfigs |

### `acm_switchover_discovery` / input facts

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `acm_switchover_discovery.bridge_script` | str | `scripts/discover-hub.sh` | Bridge script used for kubeconfig context enumeration until full native discovery coverage |
| `acm_switchover_discovery_restore_state` | str | `none` | Observed restore state (e.g. `passive-sync`) |
| `acm_switchover_discovery_managed_clusters` | int | `0` | Number of non-local ManagedClusters registered |

## Phase 6 Result Facts

| Variable | Playbook | Key Fields |
|----------|----------|------------|
| `acm_switchover_discovery_result` | discovery | `playbook`, `hub_role`, `status` |
| `acm_switchover_decommission_result` | decommission | `phase`, `mode`, `status` |
| `acm_switchover_rbac_bootstrap_result` | rbac_bootstrap | `phase`, `mode`, `role`, `assets_applied`, `generated_kubeconfig`, `status` |
