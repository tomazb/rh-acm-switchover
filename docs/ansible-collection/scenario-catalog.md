# Shared Parity Scenario Catalog

Established: 2026-05-12; evidence descriptions reconciled 2026-10-29.
Purpose: Define scenarios that both implementations must eventually satisfy

The initial scenarios describe intended behavior. The coverage tables below link to concrete
tests and state their exercised scope; a test for one component is not evidence that the
entire scenario runs end to end. All listed evidence is static, in-process, fake-backed, or
fixture-backed. None is live ACM certification evidence. See the
[testing guide](../development/testing.md) for gate commands and evidence tiers, and the
[test-migration catalog](test-migration-catalog.md) for additional targeted tests. These
coverage descriptions do not change capability status in the [parity matrix](parity-matrix.md).

## Scenario Schema

Each scenario records:

- inputs
- initial cluster state assumptions
- expected phase outcomes
- expected validation findings
- expected mutated resources
- expected report and checkpoint artifacts

## Initial Scenarios

### SCENARIO-001 Passive switchover happy path

- method: passive
- old hub action: secondary
- expected phases: all pass
- expected artifacts: report present; in execute mode the collection checkpoint (and the
  Python state file) carries the completed migration journal: frozen Backups, the activated
  Restore's completion evidence, `post_activation.completed_at`, and a `completed` cleanup

### SCENARIO-002 Full restore switchover happy path

- method: full
- expected phases: all pass
- expected mutated resources: `restore-acm-full` created with the journaled concrete Backup names
- expected artifacts: report present

### SCENARIO-003 Preflight version mismatch

- expected preflight: fail
- expected later phases: not run
- expected artifacts: report present

### SCENARIO-004 Validate-only mode

- expected mutations: none
- expected artifact: report present

### SCENARIO-005 Dry-run mode

- expected mutations: none
- expected artifact: report present

### SCENARIO-006 Restore-only activation

- method: full
- restore_only: true
- expected phases: preflight, activation, post_activation, finalization
- expected artifact: `restore-only-report.json`

### SCENARIO-007 Decommission old hub

- decommission confirmed or dry-run
- expected effective observability: auto-detected from namespace unless explicitly set
- expected artifact: `decommission-report.json`
- MultiClusterObservability deletion runs through the UID-preconditioned guarded delete
  (`acm_uid_guarded_delete`) with a durable per-resource phase record and a fresh completion proof
- on an integrated switchover the destination-observability gate is evaluated only where this run has a
  delete to authorize: a run whose own fresh source read finds the fixed target present with its
  teardown record still pending. A completed record and an already-absent target reach no gate outcome
  at all, and neither does an integrated `old_hub_action: decommission` dry run -- `handle_old_hub.yml`
  guards the decommission include on `acm_switchover_execution.mode != 'dry_run'`, so a dry run never
  reaches the teardown path. A native Ansible check-mode run in `execute` mode DOES include it and DOES
  evaluate the gate, because a preview must surface a blocker it would hit for real
- where the gate is evaluated it must pass, block (each of the five reason codes), or accept
  `acknowledge_observability_not_migrated` for a proven-absent destination before the delete; the
  standalone playbook never evaluates the gate and refuses a truthy acknowledgement

### SCENARIO-008 Argo CD pause and failure recovery

- argocd manage: true
- resume_on_failure: true
- expected checkpoint/report: pause `run_id` preserved across retry
- scoped discovery: every normalized namespace returns exactly one positive
  result before any Application is aggregated or patched
- retry expectation: reconciled Applications are re-paused, already-correct
  Applications are unchanged, and unrelated Applications are never patched
- standalone expectation: checkpoint identity is validated before either hub
  role runs; the result reports exact per-hub and total changed-patch counts
- migration-journal expectation: once a journal exists, a failure in activation,
  post-activation, or finalization (switchover and restore-only) records the Argo CD re-pause marker
  before any resume, never rewinds to `primary_prep`, keeps the journal, and the retry
  re-pauses before any further phase work; a journal-free failure keeps the pre-R4-04 behavior

### SCENARIO-009 RBAC bootstrap and validation

- role: operator or validator
- optional decommission extension: operator only
- expected validation: shipped manifest permissions are covered, including Argo CD Application patch for operator manage mode
- expected artifacts: applied manifest list and optional generated kubeconfig path are exposed in the bootstrap result; release guardrails track this separately from switchover reports

### SCENARIO-010 distinct-physical-hub guard

- normal two-hub inputs with matching context names fail before mutation
- different contexts that return an equal live `kube-system` Namespace UID fail
  before mutation
- malformed or unavailable live role evidence fails closed without trusting
  public, registered, cached, or caller-supplied UID data
- checkpoint identity drift still fails after the trusted UID comparison
- a pre-barrier refusal enters neither recovery nor checkpoint reset; a
  post-barrier failure retains the existing recovery path
- execute plus native Ansible check mode performs fresh UID GETs and makes no
  writes; `validate` and `dry_run` may use the explicit non-live test override,
  but otherwise the action reads live UIDs
- restore-only remains secondary-only, and standalone decommission is excluded

Concrete collection coverage is
`ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py`:
Cases A-F are `test_same_live_cluster_rejects_spoofed_distinct_extra_vars`,
`test_unavailable_live_uid_rejects_spoofed_identity`,
`test_checkpoint_drift_rejects_spoofed_stored_identity`,
`test_pre_barrier_failure_ignores_spoofed_recovery_values`,
`test_post_barrier_failure_retains_recovery`, and
`test_execute_check_mode_uses_fresh_uids_without_mutation`. Checkpoint and
resume compatibility remain in
`ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py`.

## Collection Coverage (Phase 2)

| Scenario ID | Python evidence | Collection evidence | Exercised scope |
|-------------|--------|------------|-------|
| `preflight-passive-success` | [Validator units][python-preflight] | [Preflight fixture][collection-preflight] | Validator success cases and fixture report status; no live preflight proof |
| `preflight-input-failure` | [Input units][python-inputs] | [Preflight fixture][collection-preflight] | Input refusal; Collection fixture asserts a failed process and written report |
| `preflight-version-mismatch` | [Version validator units][python-preflight] | [Preflight fixture][collection-preflight] | Version mismatch refusal |
| `preflight-backup-failure` | [Backup validator units][python-preflight] | [Preflight fixture][collection-preflight] | Missing, incomplete, or unhealthy backup inputs; fixture failure reporting |

## Collection Coverage (Phase 3)

| Scenario ID | Python evidence | Collection evidence | Exercised scope |
|-------------|--------|------------|-------|
| `switchover-passive-success` | [Phase-flow stubs][python-flow] | [Switchover fixtures][collection-switchover], [report composition][collection-composition] | Python phase order, Collection primary-prep/activation pass, and four phase-report keys; not an all-phase live execution |
| `switchover-post-activation-cluster-failure` | [Post-activation units][python-post-activation] | [Post-activation fake API][collection-post-activation] | Targeted unavailable/missing-inventory refusal and journal behavior. The previously claimed named whole-playbook failure/report fixture is not present; retain that evidence gap under [#268](https://github.com/tomazb/rh-acm-switchover/issues/268) |
| `switchover-finalization-backup-recovery` | [Finalization units][python-finalization] | [Switchover fixtures][collection-switchover], [report composition][collection-composition] | Backup-enable plan, finalization report, and multi-phase report composition; no live backup-continuity or recovery proof |

## Collection Coverage (Phase 6 and Safety Realignment)

| Scenario ID | Python evidence | Collection evidence | Exercised scope |
|-------------|--------|------------|-------|
| `restore-only-success` | [Restore-only phase-flow stubs][python-flow] | [Recovery contracts][collection-restore-only], [Argo fixture][collection-argocd] | Targeted routing/recovery and restore-only Argo pause/resume; these do not establish a complete restore-only success run |
| `argocd-resume-on-failure` | [Journal rescue units][python-rescue] | [Journal rescue runtime][collection-rescue] | Durable re-pause marker, retry order, journal retention, and secondary-only restore recovery over fake hubs |
| `decommission-observability-auto` | [Decommission units][python-decommission] | [Role contracts][collection-decommission], [dry-run fixture][collection-noncore] | Targeted teardown/wiring and dry-run summary; not live autodetection or decommission certification |
| `rbac-bootstrap-operator` | [RBAC units][python-rbac], [permission parity][rbac-parity] | [Bootstrap module units][collection-rbac], [dry-run fixture][collection-noncore] | Permission expansion and bootstrap prediction; no live apply-and-validate evidence |
| `machine-readable-reports` | [Report units][python-reports] | [Report module units][collection-reports] | Local report serialization/artifact contracts |
| `runtime-parity-safety` | [Activation][activation-parity], [post-activation][post-activation-parity], [cleanup][cleanup-parity] parity | Same shared parity tests call Collection helpers in-process | Normalized transaction decisions over shared fake inputs; broader targeted coverage is in the test-migration catalog, not one full-runtime parity scenario |
| `release-1.7.10-artifact-guardrails` | [Release metadata units][release-metadata] | [Source-schema contracts][collection-schema] | Metadata and required report fields; no installed-artifact or live release certification |
| `distinct-physical-hub` | [Validation parity][identity-parity] | [Identity barrier runtime][collection-identity], [checkpoint scenarios][collection-checkpoint] | Same-context/UID and unavailable-evidence refusals, resume drift, recovery boundaries, check-mode freshness, and one-hub exclusions |

[python-preflight]: ../../tests/test_preflight_validators_unit.py
[python-inputs]: ../../tests/test_validation.py
[python-flow]: ../../tests/test_main_phase_flow.py
[python-post-activation]: ../../tests/test_post_activation.py
[python-finalization]: ../../tests/test_finalization.py
[python-rescue]: ../../tests/test_argocd_rescue_journal.py
[python-decommission]: ../../tests/test_decommission.py
[python-rbac]: ../../tests/test_rbac_validator.py
[python-reports]: ../../tests/test_report_artifacts.py
[rbac-parity]: ../../tests/test_rbac_collection_parity.py
[activation-parity]: ../../tests/test_activation_transaction_parity.py
[post-activation-parity]: ../../tests/test_post_activation_transaction_parity.py
[cleanup-parity]: ../../tests/test_restore_cleanup_transaction_parity.py
[release-metadata]: ../../tests/release/checks/test_metadata.py
[identity-parity]: ../../tests/test_validation_parity.py
[collection-preflight]: ../../ansible_collections/tomazb/acm_switchover/tests/integration/test_preflight_role.py
[collection-switchover]: ../../ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py
[collection-composition]: ../../ansible_collections/tomazb/acm_switchover/tests/scenario/test_core_switchover.py
[collection-post-activation]: ../../ansible_collections/tomazb/acm_switchover/tests/integration/test_post_activation_evidence_runtime.py
[collection-restore-only]: ../../ansible_collections/tomazb/acm_switchover/tests/unit/test_restore_only_recovery_contracts.py
[collection-argocd]: ../../ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py
[collection-rescue]: ../../ansible_collections/tomazb/acm_switchover/tests/integration/test_journal_rescue_runtime.py
[collection-decommission]: ../../ansible_collections/tomazb/acm_switchover/tests/unit/test_decommission_role_contracts.py
[collection-noncore]: ../../ansible_collections/tomazb/acm_switchover/tests/integration/test_noncore_roles.py
[collection-rbac]: ../../ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_rbac_bootstrap.py
[collection-reports]: ../../ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_report_artifact.py
[collection-schema]: ../../ansible_collections/tomazb/acm_switchover/tests/unit/test_release_source_schema.py
[collection-identity]: ../../ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py
[collection-checkpoint]: ../../ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py

## Backup continuity compatibility candidate

The #342 candidate is exercised by the real Python CLI/runner lifecycle tests and
Collection `tests/integration/test_backup_continuity_runtime.py` plus
`tests/scenario/test_backup_continuity_compatibility.py`. Local fake HTTP supplies
Kubernetes responses; real Ansible action/module loading and checkpoint files establish
completed no-write refusal, identity/reset refusal, current-proof no-op, active retry,
raw-boundary preservation and interruption. The scenario wrapper records loaded source
hashes and checkpoint save attempts so byte equality cannot hide write-then-restore.
The existing journal-rescue integration suite covers actual Argo CD Application PATCHes.
`tests/test_backup_continuity_terminal_retry.py` and Collection
`tests/integration/test_backup_terminal_retry_runtime.py` cover production-generated
completed/repaired cleanup, transient pre-boundary inventory failure, process loss after
real enablement before boundary persistence, and a new distinct Backup after the retry's
new boundary. No-schedule remains refused; generic missing history without a canonical
journal and every present-history assessment retain their controls. The Collection
interruption wrapper records durable state immediately after the actual enable write.

Shared creation/health decisions are covered by
`tests/fixtures/backup_continuity_compatibility.json` in both independent proof owners.
Serialized old-source history is retained under `tests/fixtures/backup_continuity_history`.
These tests are non-live evidence. The separately reviewed
[#367 correction](https://github.com/tomazb/rh-acm-switchover/issues/367) is integrated.
`test_shipped_fresh_execute_enables_reads_proves_and_publishes_pass` exercises the healthy
shipped role and both playbooks on each supported lane, proving actual schedule enablement,
Backup reads and finalization pass publication. A module positive, completed no-op
or test that expects the cadence failure cannot satisfy that gate. See the
[operator contract](../operations/usage.md#backup-continuity-and-historical-checkpoints).
