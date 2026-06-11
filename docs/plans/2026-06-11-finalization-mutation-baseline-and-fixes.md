# Finalization Mutation Baseline And Fix Pass

Date: 2026-06-11

Branch/worktree: `mutation/finalization-baseline` at
`<repo-root>/.worktrees/mutation-finalization-baseline`

Source target: `modules/finalization.py`

Focused Python tests:

- `tests/test_finalization.py`
- `tests/test_backup_schedule.py`

## Baseline

- Python lane before mutation: `.venv/bin/python -m pytest tests/test_finalization.py tests/test_backup_schedule.py -q` - PASS (`102 passed`)
- Collection lanes before mutation:
  - finalization unit/contracts: PASS (`78 passed`)
  - switchover finalization integration: PASS (`2 passed, 8 deselected`)
  - restore-only integration: PASS (`1 passed`)
- Temporary mutmut config:
  - `source_paths = modules/finalization.py`
  - `pytest_add_cli_args_test_selection = tests/test_finalization.py tests/test_backup_schedule.py`
  - `also_copy = lib/` and `modules/`
  - exclusions limited to `raise .*Error\(` and `logger\.`
- Baseline mutation counts: total `1170`, killed `440`, survived `688`, timed out `42`

## Fix Pass

Implemented test-only assertions for exactly three selected survivor groups:

1. `_disable_observability_on_old_hub`: old-hub MCO list/delete target, dry-run safety, pod namespace, and wait arguments.
2. `_cleanup_restore_resources`: Restore list/delete API target, namespace, resource name, and delete timeout.
3. `_wait_for_backup_schedule_deletion`: BackupSchedule polling target, callback behavior, cache invalidation, and UID-change fail-closed behavior.

No production behavior, CLI surface, report schema, parity status, or operator workflow changed.

## Final Results

- Python lane after fixes: PASS (`104 passed`)
- Collection lanes after fixes:
  - finalization unit/contracts: PASS (`78 passed`)
  - switchover finalization integration: PASS (`2 passed, 8 deselected`)
  - restore-only integration: PASS (`1 passed`)
- Final mutation counts: total `1170`, killed `551`, survived `577`, timed out `42`
- Selected survivor groups:
  - `_disable_observability_on_old_hub`: `68` -> `31` survived
  - `_cleanup_restore_resources`: `45` -> `13` survived
  - `_wait_for_backup_schedule_deletion`: `60` -> `18` survived

`setup.cfg` was restored to the default `lib/validation.py` mutation target after the finalization run.
