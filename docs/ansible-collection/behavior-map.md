# ACM Switchover Behavior Map

Date: 2026-05-04
Source: `lib/`, `modules/`, `scripts/`

## Mapping Rules

- workflow sequencing belongs in playbooks and roles
- API-heavy normalization, polling, report shaping, and validation helpers belong in thin custom plugins or `module_utils`
- prefer `kubernetes.core.k8s` and `kubernetes.core.k8s_info` wherever they are sufficient
- architectural differences such as Ansible `until` loops replacing Python `wait_for_condition()` are acceptable only when the operator-facing behavior remains aligned

## Current-to-Collection Mapping

| Current Source | Collection Target | Phase |
| --- | --- | --- |
| `acm_switchover.py` | `playbooks/preflight.yml`, `playbooks/switchover.yml` | 1 |
| `modules/preflight_coordinator.py` and `modules/preflight/` | `roles/preflight/` | 2 |
| `modules/primary_prep.py` | `roles/primary_prep/` | 3 |
| `modules/activation.py` | `roles/activation/` | 3 |
| `modules/post_activation.py` | `roles/post_activation/` | 3 |
| `modules/finalization.py` | `roles/finalization/` | 3 |
| `modules/backup_schedule.py` | `roles/finalization/tasks/enable_backups.yml`, `roles/finalization/tasks/repair_backup_schedule_collision.yml` | 3 |
| `modules/decommission.py` | `roles/decommission/`, with the standalone phase lifecycle in `playbooks/decommission.yml`. The MultiClusterObservability substep's shared phase machine, UID-preconditioned guarded delete, durable per-resource phase records, and destination-observability gate are each mirrored independently — see the guarded-delete row below and the narrative after this table | 6 |
| `lib/rbac_validator.py` | `roles/preflight/` validation behavior | 2 |
| `lib/validation.py` | centralized collection validation layer | 2 |
| `lib/kube_client.py` legacy readers | stock `kubernetes.core` usage plus later helper code | 2-3 |
| `lib/strict_read.py` + `lib/kube_client.py` strict producers (`list_custom_resources_strict`, `get_custom_resource_strict`, `get_namespace_strict`, `list_pods_strict`, `get_deployment_strict`, `get_replicaset_strict`) | `plugins/modules/acm_k8s_read_outcome.py` | 3 |
| `lib/kube_client.py` `delete_custom_resource_preconditioned` (UID-preconditioned guarded delete used by the MultiClusterObservability phase machine) | `plugins/modules/acm_uid_guarded_delete.py`, `plugins/module_utils/uid_guarded_delete.py` | 6 |
| `lib/utils.py` checkpoint semantics | `plugins/action/checkpoint_phase.py`, `plugins/module_utils/checkpoint.py` | 4 |

In `validate` mode (`acm_switchover_execution.mode: validate`), the checkpoint
preflight runs the same load + verification path as `execute` mode — including
hub-identity binding and `reset_from` handling — but does **not** persist any
checkpoint transitions or perform mutations. This surfaces misconfigured
checkpoints before an actual execute-mode run.

For the normal two-hub path, Python's runtime binder and the collection's
`checkpoint_phase` identity barrier independently reject equal context names,
equal live `kube-system` Namespace UIDs, and unreadable role evidence before a
mutation-capable phase. The collection keeps UID evidence action-local through
checkpoint validation; Python keeps the order collect, compare, then bind to
state. Restore-only and standalone decommission remain outside the two-hub
comparison. This distinct physical-hub decision preserves operator parity
without sharing production runtime code.

Standalone decommission is one-hub, and it is entered only through an explicit
`standalone_decommission_identity: true` argument on `checkpoint_phase` — never
inferred from a missing secondary, from the calling role or playbook, or from the
shape of an existing checkpoint. `playbooks/decommission.yml` owns that lifecycle
(`enter`, then the role include and `pass` in a block, with `fail` in a rescue);
the shared `decommission` role owns none of it, because integrated finalization
includes that same role from inside an established two-hub `finalization`
checkpoint. Each standalone transition freshly reads the primary `kube-system`
Namespace UID and supplies a primary-only operation identity explicitly, so the
canonical two-hub reader is never consulted and an established two-hub checkpoint
cannot be downgraded. The `checkpoint.reset` and `checkpoint.reset_from`
configuration flags are refused on that path, because they bypass identity
validation.

Observability RBAC permissions are skipped when MCO is verifiably absent: when
preflight detection finds no `MultiClusterObservability` resources on the hub
(a successful lookup returning empty), Observability-scoped RBAC checks
(including the baseline `MultiClusterObservability` delete validation) are
skipped because they are not required for that workflow. Detection failure
(API/auth errors) still fails closed.

The MultiClusterObservability teardown boundary held equal by parity vectors, independently implemented on
each side: a UID-preconditioned guarded delete (Python `KubeClient.delete_custom_resource_preconditioned`,
collection `acm_uid_guarded_delete`), the durable per-resource phase vocabulary `delete_started` →
`cr_absent` → `drain_pending` → `drained` → `completed` (Python `RunRecord` teardown records, collection
`decommission` checkpoint `operational_data`), and a fresh live completion proof required before
`completed` and re-proved — never trusted — on every resume of a `completed` record. On an integrated
switchover only, and only when this invocation's own fresh read found the target present, that delete is
additionally gated on fresh destination-observability proof: Python `destination_observability_gate()`
(`modules/decommission.py`), collection `roles/decommission/tasks/destination_observability_gate.yml`.
Both classify the same five outcomes (`source_observability_unverifiable`, `source_observability_ambiguous`,
`destination_observability_unverifiable`, `destination_observability_absent`,
`acknowledgement_not_applicable`) and both accept the override only for a destination proven to have no
observability: Python `--acknowledge-observability-not-migrated`, collection
`acm_switchover_decommission.acknowledge_observability_not_migrated`.

| `lib/argocd.py` | `roles/argocd_manage/`, preflight read-only advisory discovery, and deferred playbook | 5 |
| `lib/gitops_detector.py` | preflight detection and warnings, including non-blocking Argo CD ACM-touching Application advisory output | 5 |
| `modules/preflight/reporter.py` and Python-only `lib/report_artifacts.py` | `plugins/modules/acm_preflight_report.py`, `plugins/modules/acm_report_artifact.py`, playbook report contracts | 2-6 |
| `modules/preflight/backup_validators.py` restore analysis | `plugins/modules/acm_restore_info.py`, `roles/preflight/tasks/validate_backups.yml`, `roles/activation/tasks/wait_for_restore.yml` | 2-3 |
| `check_rbac.py` / `scripts/setup-rbac.sh` | `playbooks/rbac_bootstrap.yml`, `roles/rbac_bootstrap/`, `plugins/modules/acm_rbac_bootstrap.py` | 6 |
| `lib/waiter.py` (`WaitConditionResult`, `wait_for_condition`) | no direct equivalent — collection roles use Ansible's native `until`/`retries` loop construct; architectural difference, not a gap to close | coexistence |
| `scripts/discover-hub.sh` | supported migration bridge, not rewritten in the collection | coexistence |

## Activation Wait Contract (collection)

Collection activation parity with `modules/activation.py` was tightened in
`[Unreleased]`. Two cases that previously caused false failures or wrong waits
are now explicit:

- **Passive activation, stale pre-activation Velero signal (Step 5).** When the
  ACM controller has not yet published the new restore name, a leftover
  pre-activation Velero managed-clusters restore signal is treated as a
  *retryable pending* state, not a terminal failure. Activation re-reads the
  live Restore and continues polling within the configured budget instead of
  failing before the controller publishes the new restore.
- **Full-restore activation wait (F6).** After the ACM `Restore` resource
  reaches a terminal phase, activation switches to ManagedCluster presence
  checks. It does **not** wait for a Velero `managed-clusters` Restore — that
  intermediate object can arrive out of order or be coalesced by Velero, so
  blocking on it produces false-negative timeouts. The contract is: ACM
  Restore terminal phase ⇒ verify expected ManagedClusters appear within the
  remaining timeout budget.

## ACM Version Gates to Preserve

- ACM 2.11 BackupSchedule delete semantics
- ACM 2.12+ BackupSchedule pause semantics
- ACM 2.14+ `autoImportStrategy`

Roles must not hard-code scattered version comparisons. Current boundaries document normalized facts only:

- `backup_schedule_pause_mode`
- `supports_auto_import_strategy`
- `supports_managed_service_account`

## Discovery Boundary

The collection discovery role classifies hub resources and emits bridge guidance. Full kubeconfig/context enumeration remains owned by `scripts/discover-hub.sh` during coexistence.
