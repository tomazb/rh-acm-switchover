# Comprehensive Test Audit Design

**Date**: 2026-05-22
**Scope**: Full coverage + parity audit across Python CLI and Ansible collection
**PR**: `feat/graphify-test-coverage` → `ansible` (PR #67)

## Goal

Find and fix all meaningful test gaps across both form factors:
- Code that has no tests
- Tests that don't cover important behaviors (error paths, edge cases, boundary conditions)
- Behaviors tested on one side (Python or Ansible) but not the other

"Meaningful" means a test would catch a real bug, not just execute a line.

## Approach: Parallel Multi-Agent Audit

Five independent agents analyze separate domains simultaneously, then one consolidation pass writes all tests.

### Agents

| Agent | Domain | Tools |
|-------|--------|-------|
| A | Python module logic (`lib/`, `modules/`) | code reading, coverage |
| B | Collection module logic (`plugins/modules/`, `plugins/module_utils/`) | code reading, coverage |
| C | Structural YAML contracts (role `tasks/*.yml`) | YAML reading, existing test comparison |
| D | Python ↔ Ansible parity (dual-supported capabilities) | parity matrix, cross-reference |
| E | Coverage report (uncovered lines) | pytest-cov both sides |

### Test Scope

- **Module logic**: negative tests, error paths, boundary cases, missing `check_mode`/argument_spec edge cases
- **Structural contracts**: task ordering, guard conditions, dry-run defaults, hub context isolation, missing task files without any corresponding structural test
- **Parity**: every dual-supported behavior tested on both sides with equivalent coverage

### Out of Scope

- `tests/release/` and `tests/e2e/` (separate CI concern)
- Cosmetic/formatting-only tests
- Tests that only assert on implementation details without catching real bugs

## Consolidation

After all agents complete:

1. Deduplicate across agent findings
2. Rank by safety impact:
   - Wrong-context mutations
   - Destructive operations missing confirmation
   - Error paths that silently succeed
   - Missing check_mode / dry-run safety
   - Coverage gaps
3. Write tests in batches per module/domain, running suite after each batch
4. Commit each logical group to the branch
5. Final `./run_tests.sh` in strict mode before push

## Success Criteria

- All new tests catch a real potential bug (not just line coverage)
- Every dual-supported capability has behavioral coverage on both sides
- Full suite passes in strict mode (`black`, `isort`, `mypy`, `bandit`)
- No superficial assertions ("assert True" or "assert module_exists" style)
