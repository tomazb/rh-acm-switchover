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
| `tests/test_validation.py` | later unit/integration | variable-validation semantics |
| `tests/test_validation_parity.py` | parity | shared same-context, same-UID, unreadable-UID, and distinct-UID decision messages |
| `ansible_collections/tomazb/acm_switchover/tests/unit/test_validation_parity_fixture.py` | parity | collection consumption of the shared validation fixture |
| `ansible_collections/tomazb/acm_switchover/tests/integration/test_distinct_hub_identity_barrier.py` | integration | Cases A-F: spoofed equal UID, unavailable UID, checkpoint drift, pre-barrier recovery exclusion, post-barrier recovery, and execute-plus-check fresh reads |
| `ansible_collections/tomazb/acm_switchover/tests/scenario/test_checkpoint_resume.py` | scenario | checkpoint/reset recovery and native-check no-write compatibility |
| `tests/test_rbac_validator.py` | later unit/integration | RBAC self-validation stays in core parity |
| `tests/test_argocd.py` | parity, collection unit | Argo CD pause/resume and resume-on-failure are dual-supported |
| `tests/test_gitops_detector.py` | parity, collection unit | GitOps classification remains shared behavior; full context discovery remains bridge-backed |
| `tests/test_decommission.py` | parity, collection unit | Decommission is dual-supported, including observability autodetection and pod waits |
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
- The distinct-physical-hub guard has Python unit/runtime coverage in
  `tests/test_validation.py`, `tests/test_main.py`, and
  `tests/test_validation_parity.py`; collection parity, integration, and
  scenario coverage use the paths above. The endpoint lanes are
  `ansible-core` 2.16 on Python 3.11 and `ansible-core` 2.21 on Python 3.12.
