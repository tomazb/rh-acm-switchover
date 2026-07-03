# PR 37 Design: Report Accurate Check-Mode `changed` in acm_preflight_report (R2-M1 part 1)

**Date:** 2026-07-03
**Finding:** `R2-M1` (part 1) from
[`docs/plans/2026-07-02-thermos-ansible-review-2-findings.md`](../../plans/2026-07-02-thermos-ansible-review-2-findings.md)
**Tracker row:** PR 37 in [`thermos-resolution-plan.md`](../../../thermos-resolution-plan.md)
**Branch:** `fix/thermos-37-preflight-report-checkmode`

## Problem

Verified against source at `ansible` @ `de943b0c`:

`plugins/module_utils/artifacts.py:write_json_artifact` computes a
diff-based `changed` (content + file mode) **before** its check-mode gate
and returns it without writing — accurate "would change" semantics.
`acm_preflight_report.py:main()` receives that accurate value through
`write_report(...)`, then explicitly discards it:

```python
if module.check_mode:
    changed = False   # lines 156-157
```

So `ansible-playbook --check` always reports `changed=false` for the
preflight report even when the artifact would be created or updated.
Sibling `acm_report_artifact.py` has no such override and reports the
accurate value. The preflight role's stable contract
(`roles/preflight/tasks/write_report.yml`) publishes the plugin's `changed`
verbatim into `acm_switchover_preflight_result.changed`, so the wrong value
propagates to role consumers.

`tests/unit/plugins/modules/test_acm_preflight_report.py::test_run_module_check_mode_does_not_write_report_and_returns_unchanged`
currently encodes the buggy behavior: destination absent (a real run would
create it), yet it asserts `changed is False`.

## Approaches considered

1. **Delete the override (chosen)** — two-line removal; the module then
   reports `write_json_artifact`'s accurate value, matching its sibling and
   Ansible's check-mode convention ("report what a real run would change",
   e.g. `file`/`template` modules). The no-write guarantee is unaffected —
   it lives in `write_json_artifact`'s check-mode gate.
2. **Make the override conditional** (`changed = False` only when the
   artifact matches) — re-derives inside `main()` what
   `write_json_artifact` already computed; duplicate logic, rejected.
3. **Change `write_report`** — nothing to change; the wrapper already
   passes the accurate tuple through. Rejected as no-op.

## Design

- `acm_preflight_report.py`: delete the `if module.check_mode: changed = False`
  block (2 lines). No other production change.
- Tests (red-first):
  - Flip `test_run_module_check_mode_does_not_write_report_and_returns_unchanged`
    to assert `changed is True` and rename to
    `test_run_module_check_mode_does_not_write_report_but_reports_would_change`;
    keep the `os.open` not-called and `destination.exists() is False`
    assertions (the no-write contract).
  - `test_run_module_check_mode_reports_unchanged_when_artifact_matches`
    stays as-is: `changed is False` is now produced by the accurate diff,
    not the override — it keeps passing before and after only via the fix
    (before the fix it passes for the wrong reason; it guards regressions
    after).
  - Add a sibling-parity test asserting execute-mode create reports
    `changed is True` and check-mode create reports the same `True` —
    documenting the mode-independence of the `changed` verdict.

## Behavior change note

Operators running `--check` will now see `changed` for the preflight-report
task when the artifact would be created/updated — a truthful diff signal,
consistent with `acm_report_artifact` and core Ansible modules. No
execute-mode behavior changes.

## Acceptance criteria

1. Check-mode run with absent/stale artifact reports `changed=true` and
   writes nothing; matching artifact reports `changed=false`.
2. `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py -q` passes;
   full collection unit suite passes.
3. Touched-file `black`/`isort` (line-length 120), `git diff --check`
   clean; full `./run_tests.sh` passes.

## Assumptions (autonomous session)

Operator pre-approved this slice via the tracker queue; design gate
satisfied by this spec. The role-level aggregation gap for
`pause_backups.yml`/`activate_restore.yml` (R2-M1 part 2) is explicitly
out of scope — that is PR 38 with its own design decision.
