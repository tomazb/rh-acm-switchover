# Post-Activation Mutation Baseline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run a whole-file mutation-testing baseline for `modules/post_activation.py`, using the Python post-activation tests plus focused collection unit and integration lanes to classify parity-sensitive survivors before any fix work starts.

**Architecture:** This is a baseline/triage plan, not a survivor-kill plan. The workflow first proves the unmutated Python and collection post-activation lanes are green, then temporarily repoints `[mutmut]` in `setup.cfg` to `modules/post_activation.py`, runs the spike, classifies the top survivors against both form factors, records the baseline, and restores the default `setup.cfg` target afterward.

**Tech Stack:** Python 3.10+, pytest, mutmut 3.6.x, Python CLI post-activation verification, Ansible collection post-activation role/unit tests

---

## Task 1: Capture the baseline context

**Files:**
- Reference: `docs/plans/2026-06-10-post-activation-mutation-baseline-design.md`
- Reference: `docs/development/mutation-testing-plan.md`
- Reference: `setup.cfg`

**Step 1: Verify the working tree is safe for a spike**

Run:

```bash
git status --short
```

Expected: no unexpected tracked changes in files needed for the spike.

**Step 2: Verify mutmut is installed and available**

Run:

```bash
source .venv/bin/activate
python -m pip show mutmut
mutmut --help >/dev/null
```

Expected: `mutmut` is installed from `requirements-dev.txt` and the help command exits successfully.

**Step 3: Record the exact baseline commit and branch**

Run:

```bash
git rev-parse --short HEAD
git branch --show-current
```

Expected: capture the exact commit SHA and branch name for the recorded baseline.

## Task 2: Prove the Python post-activation baseline is green

**Files:**
- Reference: `modules/post_activation.py`
- Test: `tests/test_post_activation.py`

**Step 1: Run the focused Python baseline**

Run:

```bash
source .venv/bin/activate
python -m pytest tests/test_post_activation.py -q
```

Expected: PASS.

**Step 2: Record the command and result**

Capture:

- command run
- pass/fail
- total tests collected/passed

If this lane fails, stop and report the red baseline instead of running mutation testing.

## Task 3: Prove the collection post-activation unit/contracts lane is green

**Files:**
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_regressions.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_observability.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_shared_ansible_logic_contracts.py`

**Step 1: Run the collection unit/contracts lane**

Run:

```bash
source .venv/bin/activate
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_regressions.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_post_activation_observability.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_klusterlet_remediation.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_managedcluster_status.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_klusterlet_modules.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_shared_ansible_logic_contracts.py \
  -q
```

Expected: PASS.

**Step 2: Record the command and result**

Capture:

- command run
- pass/fail
- total tests collected/passed

If this lane fails, stop and report the unmutated failure before running mutmut.

## Task 4: Prove the collection post-activation integration/scenario lane is green

**Files:**
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py`

**Step 1: Run the focused collection integration lane**

Run:

```bash
source .venv/bin/activate
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_switchover_roles.py \
  -k post_activation \
  -q
```

Expected: PASS.

**Step 2: Record the command and result**

Capture:

- command run
- pass/fail
- total tests collected/passed

If this lane is noisy or red, stop and narrow the integration scope before trusting mutation output.

## Task 5: Temporarily point mutmut at `modules/post_activation.py`

**Files:**
- Modify: `setup.cfg`

**Step 1: Update the `[mutmut]` block for the post-activation spike**

Change `setup.cfg` so the block reads:

```ini
[mutmut]
source_paths = modules/post_activation.py
pytest_add_cli_args_test_selection =
    tests/test_post_activation.py
also_copy = lib/
    modules/
do_not_mutate_patterns =
    raise .*Error\(
    logger\.(info|debug|warning|error|exception)\(
```

Do **not** add broader exclusions in the first spike.

**Step 2: Verify the config parses**

Run:

```bash
source .venv/bin/activate
python -c "
import sys; sys.argv = ['mutmut']
from mutmut.configuration import Config
Config.ensure_loaded()
c = Config.get()
print('source_paths:', c.source_paths)
print('pytest_add_cli_args_test_selection:', c.pytest_add_cli_args_test_selection)
print('also_copy:', c.also_copy)
print('do_not_mutate_patterns:', c.do_not_mutate_patterns)
"
```

Expected: `source_paths` points at `modules/post_activation.py`, the Python post-activation test file is selected, `also_copy` includes both `lib/` and `modules/`, and only the narrow logger/raise patterns are configured.

**Step 3: Commit the temporary spike config**

```bash
git add setup.cfg
git commit -m "chore: point mutmut at modules/post_activation.py baseline spike"
```

## Task 6: Run the whole-file post-activation mutation spike

**Files:**
- Source: `modules/post_activation.py`
- Baseline output: `mutants/modules/post_activation.py.meta`

**Step 1: Re-run the Python focused tests after the config change**

Run:

```bash
source .venv/bin/activate
python -m pytest tests/test_post_activation.py -q
```

Expected: still PASS.

**Step 2: Run mutmut**

Run:

```bash
source .venv/bin/activate
rm -rf mutants/
mutmut run
```

Expected: the run completes and writes `mutants/modules/post_activation.py.meta`.

Note: previous repo spikes sometimes printed a stats warning even when the `.meta`
file was written. Treat the `.meta` file as the source of truth for counts.

**Step 3: Extract counts from the meta file**

Run:

```bash
source .venv/bin/activate
python -c "
import json
from collections import Counter
meta = json.load(open('mutants/modules/post_activation.py.meta'))
exit_codes = meta.get('exit_code_by_key', {})
survived = sum(1 for v in exit_codes.values() if v == 0)
killed = sum(1 for v in exit_codes.values() if v == 1)
not_checked = sum(1 for v in exit_codes.values() if v is None)
print(f'Total: {len(exit_codes)} | Killed: {killed} | Survived: {survived} | Not checked: {not_checked}')
func_survived = Counter()
for key, code in exit_codes.items():
    if code == 0:
        parts = key.split('ǁ')
        func = parts[-1].rsplit('__mutmut_', 1)[0] if len(parts) >= 2 else key
        func_survived[func] += 1
for func, count in func_survived.most_common(10):
    print(f'  {count:3d}  {func}')
"
```

Expected: a clear total/killed/survived/not_checked summary plus the top survivor-heavy functions.

## Task 7: Classify the top survivors against Python and collection evidence

**Files:**
- Source: `modules/post_activation.py`
- Test: `tests/test_post_activation.py`
- Reference: collection post-activation test files from Tasks 3-4
- Modify: `docs/development/mutation-testing-plan.md`

**Step 1: Inspect the top survivors**

Run:

```bash
source .venv/bin/activate
mutmut results | head -40
```

Then inspect the highest-value survivors with:

```bash
mutmut show <id>
```

Start with survivors affecting:

- managed-cluster reconnection success/failure boundaries
- klusterlet remediation and wrong-hub detection
- observability restart/readiness or metrics verification
- wait/polling and timeout semantics

**Step 2: Classify each meaningful survivor**

For each high-value survivor, assign exactly one class:

- missing assertion
- missing scenario
- parity gap
- equivalent
- incidental/noisy
- tool/runtime issue

Use the collection lanes to decide whether a survivor is Python-only noise or a
shared-behavior concern.

**Step 3: Record the baseline in `docs/development/mutation-testing-plan.md`**

Add a new Phase 2 baseline subsection for `modules/post_activation.py` with:

- source target
- exact baseline branch and commit
- Python baseline command/result
- collection unit lane command/result
- collection integration lane command/result
- mutation tool/version and command
- total/killed/survived/not_checked counts
- top high-value survivors
- next action recommendation

**Step 4: Commit the baseline notes**

```bash
git add docs/development/mutation-testing-plan.md
git commit -m "docs: record modules/post_activation.py mutation baseline"
```

## Task 8: Restore the default mutmut config

**Files:**
- Modify: `setup.cfg`

**Step 1: Restore `setup.cfg` back to the default validation target**

Restore `setup.cfg` back to:

```ini
[mutmut]
source_paths = lib/validation.py
pytest_add_cli_args_test_selection =
    tests/test_validation.py
    tests/test_validation_parity.py
also_copy = lib/
do_not_mutate_patterns =
    raise .*Error\(
    _validate_choice\(
    validate_non_empty_string\(
    validate_safe_filesystem_path\(
    resource_type: str =
    getattr\(args, "role", "operator"\)
    getattr\(args, "include_decommission", False\)
    and not is_setup
```

**Step 2: Verify the restored config**

Run:

```bash
source .venv/bin/activate
python -m pytest tests/test_validation.py tests/test_validation_parity.py -q
```

Expected: PASS.

**Step 3: Commit the restored config**

```bash
git add setup.cfg
git commit -m "chore: restore mutmut config after post-activation baseline spike"
```

## Task 9: Decide whether follow-up implementation is warranted

**Files:**
- Reference: `docs/plans/2026-06-10-post-activation-mutation-baseline-design.md`
- Reference: updated `docs/development/mutation-testing-plan.md`

**Step 1: Review the classified survivor set**

Answer:

- Are the top survivors meaningful operator-safety gaps?
- Are they mostly equivalent/noisy?
- Do any require collection-side parity confirmation before fixing?

**Step 2: Choose the next path**

- If survivors are trivial/noisy: stop here and keep the baseline only.
- If survivors are meaningful and bounded: write a dedicated survivor-resolution design and implementation plan.
- If survivors imply parity drift: stop and escalate the parity question before making code changes.

**Step 3: Summarize the recommendation**

Produce a concise recommendation with:

- the next recommended action
- the top 3 survivor groups
- whether follow-up should be Python-only, parity-aware, or no-op
