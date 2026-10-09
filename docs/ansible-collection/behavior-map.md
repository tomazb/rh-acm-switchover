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
| `modules/decommission.py` | `roles/decommission/`, with the standalone phase lifecycle in `playbooks/decommission.yml`. The shared phase machine, UID-preconditioned guarded delete, durable per-resource phase records, and destination-observability gate are each mirrored independently; the MultiClusterHub substep adds operator-identity capture and owner-chain Pod classification, orchestrated by `roles/decommission/tasks/delete_multiclusterhub.yml` — see the guarded-delete and identity rows below, the *MultiClusterObservability teardown boundary* paragraph, and the *MultiClusterHub identity ownership split* paragraph later in [Current-to-Collection Mapping](#current-to-collection-mapping) | 6 |
| `lib/rbac_validator.py` | `roles/preflight/` validation behavior | 2 |
| `lib/validation.py` | centralized collection validation layer | 2 |
| `lib/kube_client.py` legacy readers | stock `kubernetes.core` usage plus later helper code | 2-3 |
| `lib/strict_read.py` + `lib/kube_client.py` strict producers (`list_custom_resources_strict`, `get_custom_resource_strict`, `get_namespace_strict`, `list_pods_strict`, `get_deployment_strict`, `get_replicaset_strict`) | `plugins/module_utils/k8s_read.py`, with `plugins/modules/acm_k8s_read_outcome.py` as the thin module wrapper over those helpers. A named 404 is `not_found` only after live discovery confirms the requested group/version, the requested kind, and the route that was read; a built-in 404 that live discovery does not confirm is `error` or `kind_not_served` where Python reports absence (approved divergence, #317). A custom-resource read whose live discovery has a missing, empty, non-string, or non-matching `groupVersion` is `error` / `ERROR` on both form factors (collection since #317, Python since #321). Separately, a custom-resource named GET that returns 404 on a stale cached route (a non-canonical resolved plural, or live discovery showing a different kind or scope) is `error` in the collection where Python, which reads the caller's fixed route, can read the object or report `OBJECT_ABSENT` or `CRD_ABSENT` (approved after implementation, #317; LIST routing is #320); custom-resource reads against conformant discovery and a cached route that still matches it stay aligned. Before any request, the collection refuses a resolved route that is not the requested group/version, canonical plural and scope, so a stale cached scope never reads a namespaced LIST cluster-wide (#320, collection-internal, fail-closed); and it reads a custom resource (any group/version other than `v1` and `apps/v1`) only after live discovery proves it served, as Python does (#322). See [coexistence](../../ansible_collections/tomazb/acm_switchover/docs/coexistence.md) | 3 |
| `lib/kube_client.py` `delete_custom_resource_preconditioned` (UID-preconditioned guarded delete used by the shared teardown phase machine for MultiClusterObservability, ManagedCluster and MultiClusterHub) | `plugins/modules/acm_uid_guarded_delete.py`, `plugins/module_utils/uid_guarded_delete.py` | 6 |
| `lib/kube_client.py` `json_patch_custom_resource_guarded` / `delete_restore_guarded` and `create_restore_guarded` (R4-04 guarded ACM Restore JSON Patch with `test` operations on UID, resourceVersion and the raw ManagedClusters field, the UID- and resourceVersion-preconditioned Restore delete, and the single never-reconciled create; called by activation and the finalization Restore cleanup) | `plugins/modules/acm_restore_guarded_mutation.py` (`action: patch`, `delete`, `create`); `tests/test_restore_guarded_mutation_parity.py` holds the two form factors to the same patch document, classification and check-mode/dry-run result | 3 |
| `lib/migration_evidence.py`, `lib/migration_child_evidence.py`, `lib/migration_journal.py` (R4-04 pure migration-evidence model: Backup selection, Velero child evidence, journal validation) | `plugins/module_utils/migration_evidence.py`, `migration_child_evidence.py`, `migration_journal.py`; `tests/fixtures/r4_04_migration_evidence_vectors.json` feeds both through `tests/test_migration_evidence_parity.py` | 3 |
| `modules/activation.py` R4-04 activation transaction (freeze, guarded mutation, completion evidence) | `plugins/module_utils/migration_activation.py` behind `plugins/modules/acm_migration_evidence.py`, driven by `roles/activation/tasks/activate_restore.yml` and `wait_for_restore.yml`; the role persists each candidate through `checkpoint_phase` `status: update`; `tests/test_activation_transaction_parity.py` | 3 |
| `lib/migration_post_activation.py` (post-activation evidence) | `plugins/module_utils/migration_post_activation.py` behind `acm_migration_evidence`, driven by `roles/post_activation/tasks/main.yml` and `verify_managed_clusters.yml`; `tests/test_post_activation_transaction_parity.py` | 3 |
| `lib/migration_cleanup.py` and `Finalization._run_restore_cleanup_transaction` / `_require_old_hub_evidence` (Restore-cleanup transaction, locator-absence and old-hub evidence gates) | `plugins/module_utils/migration_cleanup.py` behind `acm_migration_evidence`, driven by `roles/finalization/tasks/cleanup_journaled_restore.yml`, `cleanup_journaled_restore_attempt.yml`, `poll_journaled_restore_absent.yml`, `require_journaled_locator_absent.yml` and `require_old_hub_evidence.yml`; `tests/test_restore_cleanup_transaction_parity.py` | 3 |
| `lib/argocd_resume.py` resume-on-failure under a migration journal and `repause_argocd_after_rescue` | `roles/argocd_manage/tasks/journal_rescue.yml` (play rescue of `switchover.yml` and `restore_only.yml`) and `roles/argocd_manage/tasks/repause_recovery.yml` (retry entry); the re-pause marker is `checkpoint_phase` state outside the journal | 5 |
| `acm_switchover.py` resumed-phase input checks (`_resolve_managed_cluster_expectation`, `_require_waivable_recorded_expectation`) | `roles/common/tasks/guard_role_entry.yml`, included at the entry of `activation`, `post_activation` and `finalization` | 3 |
| `modules/decommission_identity.py` (`capture_operator_identity`, `classify_pods`) | `plugins/modules/acm_pod_owner_classify.py` (`capture_identity` and `classify` operations), `plugins/module_utils/pod_owner_classify.py` | 6 |
| `lib/utils.py` checkpoint semantics | `plugins/action/checkpoint_phase.py`, `plugins/module_utils/checkpoint.py`. A corrupt checkpoint (invalid JSON, including bytes that are not valid UTF-8, or a non-object top level) is preserved in place with a forensic copy and blocks later runs, as `StateManager` does for a corrupt state file (R4-04 PR B) | 4 |

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

**MultiClusterHub identity ownership split.** On both sides the identity/classification helper
(`modules/decommission_identity.py`, `plugins/module_utils/pod_owner_classify.py` behind
`acm_pod_owner_classify`) is read-side only: it captures the operator Deployment identity from the
MultiClusterHub-owning ClusterServiceVersion, or returns an explicit unavailable identity, and it
decides per Pod whether the owner chain reaches the recorded Deployment. It persists nothing,
deletes nothing and waits for nothing. The orchestration owns the rest — durable records, the phase
table, the UID-preconditioned delete, the bounded waits and the completion proof: `modules/decommission.py`
for Python, `roles/decommission/tasks/delete_multiclusterhub.yml` for the collection. The two
implementations are independent and never import each other at runtime; parity is behavioral and is
held by shared identity vectors in `tests/test_mch_identity_parity.py`.

| `lib/argocd.py` | `roles/argocd_manage/`, preflight read-only advisory discovery, and deferred playbook | 5 |
| `lib/gitops_detector.py` | preflight detection and warnings, including non-blocking Argo CD ACM-touching Application advisory output | 5 |
| `modules/preflight/reporter.py` and Python-only `lib/report_artifacts.py` | `plugins/modules/acm_preflight_report.py`, `plugins/modules/acm_report_artifact.py`, playbook report contracts | 2-6 |
| `modules/preflight/backup_validators.py` restore analysis | `plugins/modules/acm_restore_info.py` (preflight passive-restore analysis only; activation plans through `acm_migration_evidence`), `roles/preflight/tasks/validate_backups.yml` | 2 |
| `check_rbac.py` / `scripts/setup-rbac.sh` | `playbooks/rbac_bootstrap.yml`, `roles/rbac_bootstrap/`, `plugins/modules/acm_rbac_bootstrap.py` | 6 |
| `lib/waiter.py` (`WaitConditionResult`, `wait_for_condition`) | no direct equivalent — collection roles use Ansible's native `until`/`retries` loop construct; architectural difference, not a gap to close | coexistence |
| `scripts/discover-hub.sh` | supported migration bridge, not rewritten in the collection | coexistence |

## Activation Wait Contract (collection)

The R4-04 activation transaction (above) owns the activation wait in both form factors and
supersedes the earlier collection-only wait rules (the retryable stale pre-activation Velero
signal and the full-restore wait that skipped Velero children). Strict reads of the journaled
Restore and of the complete Velero Restore list are polled until the method-specific proof holds:
the ACM Restore phase is `Finished` (a patched passive sync Restore may also be `Enabled`, only
with the complete child proof), and every required Velero child Restore is owned by the journaled
Restore, bound to a frozen Backup, and `Completed`. The ManagedCluster name and count check follows;
a fresh proof taken after it is journaled, with `restore.completed_at` written last. A resume over
recorded completion re-runs the proof and never rewrites it.

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
