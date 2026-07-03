# PR 38 Design: Truthful `changed` Under Native Check Mode for Pause/Activation (R2-M1 part 2)

**Date:** 2026-07-03
**Finding:** `R2-M1` (part 2) from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 38 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `fix/thermos-38-checkmode-changed-surfacing`

## Recorded design decision (required by tracker row 38)

The tracker demands an explicit choice between (a) wiring native-check-mode
`changed` through the role-level aggregation, or (b) documenting the
limitation. **Chosen: (a) wiring, plus a one-sentence doc clarification.**
Rationale: the collection already treats its own `mode: dry_run` as a
would-change signal in both aggregations; native `--check` deserves the
same treatment (symmetry), the change is small and testable, and
doc-only would leave the published role contracts
(`acm_switchover_pause_backups_result.changed`,
`acm_switchover_restore_activation_result.changed`) knowingly misleading
against the documented "native check mode is non-mutating even when
`mode: execute`" contract (`ansible_collections/.../docs/variable-reference.md:70`).

## Problem

Verified against source at `ansible` @ `5c2b24e0`:

Under `ansible-playbook --check` with `mode: execute`:

- `plugins/modules/acm_backup_schedule.py:181` returns
  `changed=(operation["action"] != "none") and not module.check_mode` —
  the plan module zeroes its own accurate would-change verdict.
- `plugins/modules/acm_restore_info.py:430-431` computes
  `plan["changed"] = operation["action"] != "none"` then explicitly zeroes
  it under check mode — the same discard-the-right-answer pattern PR 37
  removed from `acm_preflight_report`.
- `roles/primary_prep/tasks/pause_backups.yml` publishes
  `acm_switchover_pause_backups_result.changed` from the k8s task results
  plus a fallback that fires only when
  `acm_switchover_execution.mode == 'dry_run'` (the collection's own
  variable). Native `--check` does not set that variable, so the fallback
  is dead under check mode.
- `roles/activation/tasks/activate_restore.yml` publishes
  `acm_switchover_restore_activation_result.changed` the same way, and its
  fallback additionally consumes `acm_restore_activation_plan.changed` —
  which the plugin zeroed. Doubly dead under `--check`.

The k8s tasks themselves run under `--check` (their `when:` only excludes
`mode == 'dry_run'`) and `kubernetes.core.k8s` has check-mode support, but
the published aggregate should not depend on that module's per-version
check-mode fidelity when the collection already computes its own plan.

## Approaches considered

1. **Wire check mode into the aggregation (chosen)** — make the two plan
   modules' `changed` mode-independent (they mutate nothing; their
   `changed` describes the plan) and extend both role fallbacks to fire on
   `ansible_check_mode` as well as `mode == 'dry_run'`. Truthful published
   `changed` in all three modes; smallest coherent wiring.
2. **Doc-only** — one sentence in `variable-reference.md` admitting
   `changed` is unreliable under `--check`. Leaves the stable role
   contracts misleading; rejected as primary, kept as a clarifying
   sentence for the chosen behavior.
3. **Rely on `kubernetes.core.k8s` check-mode `changed`** — no code
   change; assumes full check-mode fidelity of a third-party module across
   versions and leaves the `acm_restore_info`-driven fallback dead.
   Rejected.

## Design

1. `acm_backup_schedule.py`: `changed=(operation["action"] != "none")` —
   drop `and not module.check_mode`.
2. `acm_restore_info.py`: delete the `if module.check_mode:
   plan["changed"] = False` override.
3. `pause_backups.yml` published-changed fallback becomes:
   `((acm_switchover_execution.mode | default('dry_run') == 'dry_run') or
   ansible_check_mode) and acm_backup_schedule_operation.operation.action != 'none'`.
4. `activate_restore.yml` fallback becomes:
   `((acm_switchover_execution.mode | default('dry_run') == 'dry_run') or
   ansible_check_mode) and (acm_restore_activation_plan.changed | default(false))`.
5. `docs/variable-reference.md` `mode` row: extend the native-check-mode
   sentence to state that published role results report the would-change
   `changed` verdict under `--check`.

### Tests (red-first)

- Flip `test_run_module_check_mode_returns_planned_pause_without_change`
  (`test_acm_backup_schedule.py`) and
  `test_run_module_check_mode_returns_planned_operation_without_change`
  (`test_acm_restore_info.py`) to assert `changed is True` for a
  would-change plan under check mode, renaming to `..._reports_would_change`.
- Add static role-contract assertions (matching the repo's YAML-parsing
  contract-test style) that both published-changed expressions reference
  `ansible_check_mode`.

## Out of scope

- The verify/wait polling tasks' retry behavior under `--check` (they poll
  live state that check mode never mutates); pre-existing, orthogonal to
  the `changed` contract, and worth its own finding if it matters.
- Ansible-side `changed` for other roles (no finding).

## Acceptance criteria

1. Both plan modules report mode-independent `changed`; both role
   aggregations treat `ansible_check_mode` like `mode: dry_run` in their
   fallbacks.
2. Flipped/extended plugin tests pass; role-contract assertions pass; full
   collection unit suite passes.
3. Touched-file `black`/`isort`, `git diff --check` clean; full
   `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved the slice via the tracker queue; the tracker-required
design decision is recorded above. Python-CLI parity is unaffected: the CLI
has no native-check-mode analogue (its dry-run path already reports
would-change outcomes).
