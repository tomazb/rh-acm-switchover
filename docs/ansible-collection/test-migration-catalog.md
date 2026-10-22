# Test Migration Catalog

Date: 2026-05-12
Purpose: Triage the existing Python-oriented test suite into collection-era test layers

## Target Layers

- `unit`: collection-local Python or metadata tests
- `integration`: collection behavior tests against mocked or disposable APIs
- `scenario`: multi-phase flow tests
- `parity`: shared scenario suite run against both implementations during coexistence
- `drop`: tests that only assert current Python internals

## Initial Triage Rules

- preflight, workflow, and scenario behavior stays in scope as behavior catalog
- CLI parsing tests do not migrate directly because the collection public API is variables, not flags
- shell-script implementation tests do not migrate directly unless the script remains part of the supported bridge
- state-engine internals do not migrate directly; only resume behavior and safety outcomes do

## Initial Mapping Examples

| Current Test File | Target Layer | Notes |
| --- | --- | --- |
| `tests/test_preflight_coordinator.py` | parity, later integration | preflight behavior catalog |
| `tests/test_primary_prep.py` | parity, later integration | core switchover phase |
| `tests/test_activation.py` | parity, later integration | core switchover phase |
| `tests/test_post_activation.py` | parity, later integration | core switchover phase |
| `tests/test_finalization.py` | parity, later integration | core switchover phase |
| `tests/test_backup_continuity.py`, `tests/test_finalization_backup_strict.py` | Python unit/transport | Strict initial inventory and polls, saved-name creation proof, terminal UID/health |
| `tests/test_backup_continuity_lifecycle.py` | Python lifecycle/transport | Real bootstrap, both operation runners and outcomes; serialized legacy markers; active retry and completed no-write refusal |
| `tests/test_backup_continuity_parity.py` | parity | Independent owners consume shared creation/health vectors without importing the other runtime |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_backup_continuity.py` | Collection unit/transport | Raw checkpoint facts, strict producer faults, latest-fresh selection and terminal selected-object proof |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_backup_continuity_runtime.py` | Collection integration | Real module HTTP and both shipped playbooks/direct role; active boundary refusal, identity/reset guard and current completed no-op |
| `ansible_collections/tomazb/acm_switchover/tests/scenario/test_backup_continuity_compatibility.py` | Collection scenario | Actual action-loader traces, zero completed checkpoint save attempts, interruption and byte/mtime preservation |
| `tests/test_validation.py` | later unit/integration | variable-validation semantics |
| `tests/test_validation_parity.py` | parity | shared same-context, same-UID, unreadable-UID, and distinct-UID decision messages |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py` | parity | collection consumption of the shared validation fixture |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py` | integration | Cases A-F: spoofed equal UID, unavailable UID, checkpoint drift, pre-barrier recovery exclusion, post-barrier recovery, and execute-plus-check fresh reads |
| `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py` | scenario | checkpoint/reset recovery and native-check no-write compatibility |
| `tests/test_rbac_validator.py` | later unit/integration | RBAC self-validation stays in core parity |
| `tests/test_argocd.py` | parity, collection unit | Argo CD pause/resume and resume-on-failure are dual-supported |
| `tests/test_gitops_detector.py` | parity, collection unit | GitOps classification remains shared behavior; full context discovery remains bridge-backed |
| `tests/test_decommission.py` | parity, collection unit | Decommission is dual-supported, including observability autodetection and pod waits, the shared MultiClusterObservability phase machine, and the destination-observability gate |
| `tests/test_mco_completed_resume.py` | parity, collection unit | Completed-record resume re-proves MultiClusterObservability absence live without rewriting the immutable completion evidence |
| `tests/test_kube_client.py` | later unit/integration | Covers the UID-preconditioned guarded delete primitive `delete_custom_resource_preconditioned` and the strict-read producers |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_mco_teardown_runtime.py` | collection unit | Collection MultiClusterObservability teardown runtime rows, including the destination-observability gate's pass/block/acknowledge outcomes and the finalization adapter |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_uid_guarded_delete.py` | collection unit | `acm_uid_guarded_delete` module unit behavior: namespace-scope validation, fresh discovery, finite budgets, poll-sleep capping, DELETE-404 final proof |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_uid_guarded_delete_runtime.py` | collection integration | `acm_uid_guarded_delete` against a fake Kubernetes API, including the UID-precondition wire body and the `no_log` fixture contract |
| `tests/test_migration_evidence.py` | Python unit | R4-04 pure migration-evidence model edge cases |
| `tests/test_migration_evidence_parity.py` | parity | R4-04 pure model in both form factors over `tests/fixtures/r4_04_migration_evidence_vectors.json` |
| `tests/test_restore_guarded_mutation_parity.py` | parity | Guarded Restore patch, delete and create: request, classification, dry-run/check-mode prediction |
| `tests/test_managed_cluster_expectation_parity.py` | parity | Additive ManagedCluster count/name expectation (Python resolver against the collection resolver task file) |
| `tests/test_activation_evidence.py` | Python unit | Python activation transaction: freeze, guarded mutations, completion evidence, resume, dry-run |
| `tests/test_activation_transaction_parity.py` | parity | Activation transaction outcomes, mutations and journals agree over identical fake hubs |
| `tests/test_post_activation_transaction_parity.py` | parity | Post-activation evidence (readiness, name check or waiver, completion) agrees |
| `tests/test_restore_cleanup_transaction_parity.py` | parity | Restore-cleanup transaction, recovery, repair and gates agree |
| `tests/test_argocd_rescue_journal.py` | Python unit | Argo CD resume-on-failure under a migration journal: re-pause marker, no rewind, re-pause on retry |
| `tests/test_restore_discovery.py` | Python unit | Strict passive sync Restore discovery |
| `tests/test_r4_06_endpoint_parity.py` | parity | R4-06 endpoint canonicalization, hub classification, redaction and ManagedCluster URL rule over `tests/fixtures/r4_06_endpoint_vectors.json` |
| `tests/test_r4_06_kubeconfig_parity.py` | parity | R4-06 single-file kubeconfig contract and duplicate rule over `tests/fixtures/r4_06_kubeconfig_vectors.json` |
| `tests/test_post_activation_endpoint_identity.py` | Python unit | R4-06 Python klusterlet endpoint identity: discovery, refusals with construction/read/mutation counters, remediation barrier |
| `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_endpoint_identity.py` | collection unit | R4-06 collection klusterlet identity proof, refusals, two-phase remediation barrier, stale URL, worker cap |
| `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/module_utils/test_kubeconfig_file.py` | collection unit | R4-06 mapped kubeconfig validation and context selection |
| `tests/test_r4_06_snapshot_parity.py` | parity | R4-06 PR 3 credential capture over `tests/fixtures/r4_06_credential_vectors.json` and real-loader construction (private material, poisoned cache, explicit context, concurrency) |
| `tests/test_post_activation_snapshot.py` | Python unit | R4-06 PR 3 snapshot binding across probe, remediation and re-check; refusals; cleanup; late remediation worker |
| `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_snapshot.py` | collection unit | R4-06 PR 3 snapshot binding inside one remediate invocation; refusals; late mutation; cleanup |
| `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_migration_evidence.py` | collection unit | `acm_migration_evidence` adapter: operations, refusal messages, templating floor |
| `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/action/test_checkpoint_rescue_safeguards.py` | collection unit | `checkpoint_phase` journal safeguards: re-pause marker, refused pre-freeze resets, rewind fence |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_activation_transaction_contracts.py` | collection unit | Activation role wiring of the transaction (guarded module only, freeze before mutation, `completed_at` last) |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_evidence_contracts.py` | collection unit | Post-activation role wiring of the evidence writes and the completed-marker refusal |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_finalization_cleanup_contracts.py` | collection unit | Finalization role wiring of the cleanup transaction and the old-hub evidence gate |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_journal_rescue_contracts.py` | collection unit | Playbook rescue and retry re-pause wiring under a journal |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_migration_evidence_operator_inputs.py` | collection unit | Waiver and cleanup-repair operator inputs |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_validate_mode_preview_contract.py` | collection unit | A direct `validate` entry into a mutating role mutates nothing |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_activation_transaction_runtime.py` | collection integration | Shipped activation transaction tasks against a fake destination hub |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_post_activation_evidence_runtime.py` | collection integration | Shipped post-activation evidence tasks, execute and check mode |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_finalization_cleanup_runtime.py` | collection integration | Shipped Restore-cleanup transaction, recovery and repair tasks |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_journal_rescue_runtime.py` | collection integration | Shipped-playbook Argo CD rescue under a journal |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_role_entry_guard_runtime.py` | collection integration | Direct role entry guards: input validation, checkpoint requirement, recorded expectation |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_checkpoint_rewind_fence_runtime.py` | collection integration | The `reset_from` rewind fence across real task workers |
| `tests/test_scripts_integration.py` | partial drop, partial bridge docs | only bridge behavior retained |
| `tests/test_rbac_validator.py` | parity, collection unit | SSAR shape, dry-run validation, and manifest/policy alignment are shared contracts |
| `tests/release/adapters/test_python_cli.py` | release adapter | Python scenario commands must pass `--report-dir` |
| `tests/release/adapters/test_ansible.py` | release adapter | Collection scenario commands and discovered report artifacts must include decommission coverage |
| `tests/release/scenarios/test_runtime_parity.py` | parity | Runtime parity required fields cover release artifact contracts |

## Current Test Baseline

Current boundary tests should verify:

- collection metadata parses correctly
- playbooks are syntactically valid
- example variable files parse correctly
- CI entrypoints run successfully

## Current Safety-Parity Coverage

- Restore-only, Argo CD, discovery, decommission, RBAC bootstrap, checkpoint, report artifacts, and runtime parity now have collection unit, release adapter, or shared parity tests.
- Bridge-only script behavior remains tested only where the script is still the supported bridge, especially full hub context enumeration through `scripts/discover-hub.sh`.
- New parity-sensitive changes should add tests in both the Python suite and `ansible_collections/tomazb/acm_switchover/tests/unit/` unless the parity matrix records an approved divergence.
- R4-04 migration evidence is covered by the rows above at the static, fake-backed and
  fixture-backed tiers only. None of it is live certification evidence.
- The distinct-physical-hub guard has Python unit/runtime coverage in
  `tests/test_validation.py`, `tests/test_main.py`, and
  `tests/test_validation_parity.py`; collection parity, integration, and
  scenario coverage use the paths above. The endpoint lanes are
  `ansible-core` 2.16 on Python 3.11 and `ansible-core` 2.21 on Python 3.12.

The #342 rows describe candidate regression coverage, not terminal merge or live
certification. The separately reviewed
[#367 cadence correction](https://github.com/tomazb/rh-acm-switchover/issues/367) is integrated.
Healthy shipped role and playbook execute-positive acceptance is covered on both supported
lanes by `test_shipped_fresh_execute_enables_reads_proves_and_publishes_pass`. The [operator contract](../operations/usage.md#backup-continuity-and-historical-checkpoints)
records the remaining history and recovery limits, including the independent #352
residual.
The approved bounded active terminal-journal procedure adds generated failure/retry and
enable-before-boundary interruption coverage in Python
`tests/test_backup_continuity_terminal_retry.py` and Collection
`tests/integration/test_backup_terminal_retry_runtime.py`. Their new Backup events use
distinct names/UIDs and preserve the frozen objects. These remain non-live evidence.
