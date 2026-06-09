# Argo CD Mutation Baseline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run a whole-file mutation-testing baseline for `lib/argocd.py`, using Python tests plus one collection unit/contracts lane and one collection integration lane to classify parity-sensitive survivors before any fix work starts.

**Architecture:** This is a baseline/triage plan, not a survivor-kill plan. The workflow first proves the unmutated Python and collection Argo CD lanes are green, then temporarily repoints `[mutmut]` in `setup.cfg` to `lib/argocd.py`, runs the spike, classifies the top survivors against both form factors, records the baseline, and restores the default `setup.cfg` target afterward.

**Tech Stack:** Python 3.14, pytest, mutmut 3.6.x, GitHub CLI, Python CLI Argo CD helpers, Ansible collection Argo CD role/unit tests

---

## Task 1: Capture the baseline context

**Files:**
- Modify: `setup.cfg` (temporary later in Task 5; do not edit yet)
- Reference: `docs/plans/2026-06-09-argocd-mutation-baseline-design.md`
- Reference: `docs/development/mutation-testing-plan.md`

### Step 1: Verify the working tree is safe for a spike

Run:

```bash
git status --short
```

Expected: no unexpected tracked changes in files needed for the spike. If unrelated local work exists, stop and decide whether to isolate the spike in a fresh worktree first.

### Step 2: Verify mutmut is installed and available

Run:

```bash
source .venv/bin/activate
python -m pip show mutmut
mutmut --help >/dev/null
```

Expected: `mutmut` is installed from `requirements-dev.txt` and the help command exits successfully.

### Step 3: Record the current commit for the baseline

Run:

```bash
git rev-parse --short HEAD
git branch --show-current
```

Expected: capture the exact commit SHA and branch name for the baseline notes.

## Task 2: Prove the Python Argo CD baseline is green

**Files:**
- Reference: `lib/argocd.py`
- Test: `tests/test_argocd.py`
- Test: `tests/test_argocd_constants_parity.py`

### Step 1: Run the focused Python Argo CD tests

Run:

```bash
source .venv/bin/activate
python -m pytest tests/test_argocd.py tests/test_argocd_constants_parity.py -q
```

Expected: PASS. If this baseline is red, stop and report the failing unmutated suite instead of running mutation testing.

### Step 2: Save the exact command and result for the baseline notes

Record:

- command run
- pass/fail
- total tests collected/passed

Do not continue until this baseline is confirmed green.

## Task 3: Prove the collection Argo CD unit/contracts lane is green

**Files:**
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_discovery_safety.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py`
- Test: `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_filter.py`

### Step 1: Run the collection unit/contracts lane

Run:

```bash
source .venv/bin/activate
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_manage_role_contracts.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_discovery_safety.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_resume_on_failure.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_argocd_hub_parameterization.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_autosync.py \
  ansible_collections/tomazb/acm_switchover/tests/unit/plugins/modules/test_acm_argocd_filter.py \
  -q
```

Expected: PASS. This is the collection review lane used to classify parity-sensitive survivors.

### Step 2: Record the exact command and result

Record:

- command run
- pass/fail
- total tests collected/passed

If this lane fails, stop and report the unmutated failure before running mutmut.

## Task 4: Prove the collection Argo CD integration lane is green

**Files:**
- Test: `ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py`

### Step 1: Run the integration role baseline

Run:

```bash
source .venv/bin/activate
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/integration/test_argocd_manage_role.py \
  -q
```

Expected: PASS.

### Step 2: Record the exact command and result

Record:

- command run
- pass/fail
- total tests collected/passed

If this lane is noisy or red, stop and decide whether to narrow the integration scope before interpreting any mutation output.

## Task 5: Temporarily point mutmut at `lib/argocd.py`

**Files:**
- Modify: `setup.cfg`

### Step 1: Update the `[mutmut]` block for the Argo CD spike

Change `setup.cfg` so the block reads:

```ini
[mutmut]
source_paths = lib/argocd.py
pytest_add_cli_args_test_selection =
    tests/test_argocd.py
    tests/test_argocd_constants_parity.py
also_copy =
    lib/
    ansible_collections/tomazb/acm_switchover/plugins/module_utils/
do_not_mutate_patterns =
    raise .*Error\(
    logger\.(info|debug|warning|error|exception)\(
```

The extra `also_copy` path is required because `tests/test_argocd_constants_parity.py`
imports collection Argo CD helpers from
`ansible_collections/tomazb/acm_switchover/plugins/module_utils/`. Do **not** add
broader exclusions in the first spike. If new equivalent patterns appear later,
review them from actual survivors first.

### Step 2: Verify the config parses

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
print('do_not_mutate_patterns:', c.do_not_mutate_patterns)
"
```

Expected: `source_paths` points at `lib/argocd.py`, the Python Argo CD tests are
selected, `also_copy` includes both `lib/` and the collection
`plugins/module_utils/` path, and only the narrow logger/raise patterns are
configured.

### Step 3: Commit the temporary spike config

```bash
git add setup.cfg
git commit -m "chore: point mutmut at lib/argocd.py baseline spike"
```

## Task 6: Run the whole-file Argo CD mutation spike

**Files:**
- Source: `lib/argocd.py`
- Baseline output: `mutants/lib/argocd.py.meta`

### Step 1: Re-run the Python focused tests after the config change

Run:

```bash
source .venv/bin/activate
python -m pytest tests/test_argocd.py tests/test_argocd_constants_parity.py -q
```

Expected: still PASS.

### Step 2: Run mutmut

Run:

```bash
source .venv/bin/activate
rm -rf mutants/
mutmut run
```

Expected: the run completes and writes `mutants/lib/argocd.py.meta`.

Note: previous spikes in this repo sometimes printed `failed to collect stats. runner returned ...` even when the `.meta` file was written successfully. Treat the `.meta` file as the source of truth for counts.

### Step 3: Extract counts from the meta file

Run:

```bash
source .venv/bin/activate
python3 -c "
import json
from collections import Counter
meta = json.load(open('mutants/lib/argocd.py.meta'))
exit_codes = meta.get('exit_code_by_key', {})
survived = sum(1 for v in exit_codes.values() if v == 0)
killed = sum(1 for v in exit_codes.values() if v != 0)
print(f'Total: {len(exit_codes)} | Killed: {killed} | Survived: {survived}')
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

Expected: a clear total/killed/survived summary plus the top survivor-heavy functions.

## Task 7: Classify the top survivors against Python and collection evidence

**Files:**
- Source: `lib/argocd.py`
- Test: `tests/test_argocd.py`
- Test: `tests/test_argocd_constants_parity.py`
- Reference: collection Argo CD unit/integration test files from Tasks 3-4
- Modify: `docs/development/mutation-testing-plan.md`

### Step 1: Inspect the top survivors

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

- ACM-touching app detection
- pause/resume patch payload construction
- resume-on-failure and paused-by marker behavior
- ApplicationSet or child-Application safety

### Step 2: Classify each meaningful survivor

For each high-value survivor, assign exactly one class:

- missing assertion
- missing scenario
- parity gap
- equivalent
- tool/runtime issue

Use the collection lanes to decide whether a survivor is Python-only noise or a
shared-behavior concern.

### Step 3: Record the baseline in `docs/development/mutation-testing-plan.md`

Add a new Phase 2 baseline subsection for `lib/argocd.py` with:

- source target
- Python baseline command/result
- collection unit lane command/result
- collection integration lane command/result
- mutmut command
- total/killed/survived counts
- top high-value survivors
- next action recommendation

### Step 4: Commit the baseline notes

```bash
git add docs/development/mutation-testing-plan.md
git commit -m "docs: record lib/argocd.py mutation baseline"
```

## Task 8: Restore the default mutmut config

**Files:**
- Modify: `setup.cfg`

### Step 1: Restore the default validation target

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

### Step 2: Verify the restored config

Run:

```bash
source .venv/bin/activate
python -m pytest tests/test_validation.py tests/test_validation_parity.py -q
```

Expected: PASS.

### Step 3: Commit the restored config

```bash
git add setup.cfg
git commit -m "chore: restore mutmut config after argocd baseline spike"
```

## Task 9: Decide whether follow-up implementation is warranted

**Files:**
- Reference: `docs/plans/2026-06-09-argocd-mutation-baseline-design.md`
- Reference: updated `docs/development/mutation-testing-plan.md`

### Step 1: Review the classified survivor set

Use the recorded baseline to answer:

- Are the top survivors meaningful operator-safety gaps?
- Are they mostly equivalent/noisy?
- Do any require collection-side parity confirmation before fixing?

### Step 2: Choose the next path

- If survivors are trivial/noisy: stop here and keep the baseline only.
- If survivors are meaningful and bounded: write a dedicated survivor-resolution design and implementation plan.
- If survivors imply parity drift: stop and escalate the parity question before making code changes.

### Step 3: Summarize the recommendation

Produce a concise summary with:

- the next recommended action
- the top 3 survivor groups
- whether follow-up should be Python-only, parity-aware, or no-op
