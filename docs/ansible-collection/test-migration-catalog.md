# Test Migration Catalog

Date: 2026-04-10
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
| `tests/test_rbac_validator.py` | later unit/integration | RBAC self-validation stays in core parity |
| `tests/test_argocd.py` | deferred | Phase 5 |
| `tests/test_gitops_detector.py` | deferred | Phase 5 |
| `tests/test_decommission.py` | deferred | Phase 6 |
| `tests/test_scripts_integration.py` | partial drop, partial bridge docs | only bridge behavior retained |

## Phase 1 Test Baseline

Phase 1 tests should verify only:

- collection metadata parses correctly
- playbooks are syntactically valid
- example variable files parse correctly
- CI entrypoints run successfully
