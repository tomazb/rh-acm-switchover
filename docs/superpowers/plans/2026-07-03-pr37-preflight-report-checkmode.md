# PR 37: Preflight-Report Check-Mode `changed` Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `acm_preflight_report` reports the accurate diff-based `changed` under Ansible check mode instead of forcing `changed=False`, matching sibling `acm_report_artifact` (R2-M1 part 1).

**Architecture:** Two-line deletion in `main()` per the approved design (`docs/superpowers/specs/2026-07-03-pr37-preflight-report-checkmode-design.md`); the no-write guarantee stays in `write_json_artifact`'s check-mode gate. The existing test that encodes the buggy behavior flips to assert the truthful value (red-first), plus a mode-independence test.

**Tech Stack:** Python 3, pytest with FakeModule monkeypatching (existing pattern in the module's test file), black/isort (line-length 120).

## Global Constraints

- `black --line-length 120` and `isort --profile black --line-length 120` on touched files.
- No execute-mode behavior change; check mode must still never write.
- Base branch: `ansible`; PR branch `fix/thermos-37-preflight-report-checkmode`.

---

### Task 1: Red-first — flip the check-mode create test, add mode-independence test

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py:209-252`

- [ ] **Step 1: Flip the buggy-behavior test**

Rename `test_run_module_check_mode_does_not_write_report_and_returns_unchanged`
to `test_run_module_check_mode_does_not_write_report_but_reports_would_change`
and change its final assertions to:

```python
    assert mock_open.called is False
    assert captured["exit"]["changed"] is True
    assert captured["exit"]["path"] == str(destination)
    assert captured["exit"]["report"]["phase"] == "preflight"
    assert captured["exit"]["report"]["status"] == "fail"
    assert not destination.exists()
```

(Only the `changed` assertion flips from `False` to `True`; the no-write assertions stay.)

- [ ] **Step 2: Add a mode-independence test after it**

```python
def test_run_module_changed_verdict_is_mode_independent_for_create(tmp_path, monkeypatch):
    """check_mode and execute mode must agree on the changed verdict for a would-be create."""
    verdicts = {}
    for check_mode in (True, False):
        captured = {}
        destination = tmp_path / f"artifacts-{check_mode}" / "preflight-report.json"
        destination.parent.mkdir()

        class FakeModule:
            def __init__(self, *args, **kwargs):
                self.params = {
                    "phase": "preflight",
                    "results": [],
                    "hubs": {"secondary": {"context": "secondary-hub"}},
                    "path": str(destination),
                }
                self.check_mode = check_mode

            def exit_json(self, **kwargs):
                captured["exit"] = kwargs

            def fail_json(self, **kwargs):
                raise AssertionError(f"unexpected fail_json: {kwargs}")

        monkeypatch.setattr(
            "ansible_collections.tomazb.acm_switchover.plugins.modules.acm_preflight_report.AnsibleModule",
            FakeModule,
        )
        main()
        verdicts[check_mode] = captured["exit"]["changed"]
        assert destination.exists() is (not check_mode)

    assert verdicts[True] is True
    assert verdicts[False] is True
```

- [ ] **Step 3: Run tests to verify the two fail**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py -q`
Expected: 2 FAIL (`changed` is False under check mode), rest PASS.

- [ ] **Step 4: Commit**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py
git commit -m "test: require accurate check-mode changed from acm_preflight_report (red)"
```

### Task 2: Delete the override

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py:156-157`

- [ ] **Step 1: Remove the two-line override**

Delete:

```python
        if module.check_mode:
            changed = False
```

so the block reads:

```python
    if module.params["path"]:
        output_path, changed, write_error = write_report(report, module.params["path"], check_mode=module.check_mode)
        if write_error:
            module.fail_json(msg=write_error, report=report, path=output_path)
            return

    module.exit_json(changed=changed, report=report, path=output_path)
```

- [ ] **Step 2: Run module + sibling + role-contract suites**

Run: `python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_report_artifact.py -q && python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q`
Expected: all PASS.

- [ ] **Step 3: Format and commit**

```bash
black --line-length 120 ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py
isort --profile black --line-length 120 ansible_collections/tomazb/acm_switchover/plugins/modules/acm_preflight_report.py ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_preflight_report.py
git add -A
git commit -m "fix: report accurate check-mode changed in acm_preflight_report (R2-M1 part 1)"
```

### Task 3: Full verification, tracker update, draft PR

**Files:**
- Modify: `thermos-resolution-plan.md` (row PR 37)

- [ ] **Step 1: Full gate**

Run: `./run_tests.sh`
Expected: PASS (record lane counts).

- [ ] **Step 2: Update tracker row 37, push, PR**

```bash
git add thermos-resolution-plan.md
git commit -m "docs: mark Thermos PR 37 ready for review in tracker"
git push -u origin fix/thermos-37-preflight-report-checkmode
gh pr create --draft --base ansible --title "Thermos PR 37: accurate check-mode changed in acm_preflight_report (R2-M1 part 1)" --body "<summary + verification evidence>"
```
