# Mutation Testing: Kill Validation Survivors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Kill the high-value surviving mutants found during the Phase 0 spike on `lib/validation.py` and reduce equivalent-mutant noise in future runs.

**Architecture:** Two changes: (1) add `do_not_mutate_patterns` to `setup.cfg` so that equivalent string-literal mutations on `raise` lines are suppressed before generating 500+ noisy mutants on every run; (2) add 6 integration tests to `tests/test_validation.py` that call `validate_all_cli_args` with invalid values for fields whose `hasattr` guards were bypassed in the spike. No production code changes are needed. No parity action needed (collection validation uses dict-based access, not `hasattr` guards).

**Tech Stack:** Python 3.10+, pytest, mutmut 3.6.x (dev-only), lib/validation.py

---

## Background

The Phase 0 mutation spike (`lib/validation.py`, commit `7010dc2`) found:

| Status | Count |
|--------|-------|
| Total mutants | 519 |
| Killed | 343 (66%) |
| **Survived** | **176 (34%)** |
| Timeout | 0 |

**Class A (~140 survivors):** Equivalent string-literal mutations — `raise ValidationError("msg")` → `raise ValidationError("XXmsgXX")`. Tests catch that an exception is raised but don't assert on the message text (correct design). These generate noise. Suppressed via `do_not_mutate_patterns`.

**Class B (~35 survivors):** Missing-scenario `hasattr` guard bypasses in `validate_all_cli_args`. Mutmut changes `hasattr(args, "field")` to `hasattr(None, "field")`, which always returns `False`, silently skipping field validation. The existing tests only pass valid values for these fields, so the bypass is invisible. Need new tests with invalid values.

---

## Task 1: Suppress Equivalent String-Literal Mutants

**Files:**
- Modify: `setup.cfg` — `[mutmut]` section (appended in previous spike session, near end of file)

### Step 1: Add `do_not_mutate_patterns`

Open `setup.cfg` and append to the `[mutmut]` section so it reads:

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
```

`raise .*Error\(` suppresses mutations on all `raise XxxError(...)` lines (string → None, string → "XXstringXX").
`_validate_choice\(` suppresses `field_name` argument mutations on `_validate_choice(...)` call lines.

### Step 2: Verify the patterns parse correctly

```bash
source .venv/bin/activate
python -c "
import sys; sys.argv = ['mutmut']
from mutmut.configuration import Config
Config.ensure_loaded()
c = Config.get()
print('do_not_mutate_patterns:', c.do_not_mutate_patterns)
"
```

Expected output:
```
do_not_mutate_patterns: ['raise .*Error\\(', '_validate_choice\\(']
```

### Step 3: Run mutation to verify noise reduction

First confirm the focused tests are still green:

```bash
python -m pytest tests/test_validation.py tests/test_validation_parity.py -q
```

Expected: `82 passed`

Then re-run mutation (deletes old cache automatically):

```bash
mutmut run
```

Expected: total survivors drops from 176 to roughly 30–45. Confirm with:

```bash
mutmut results 2>&1 | wc -l
```

### Step 4: Commit

```bash
git add setup.cfg
git commit -m "chore: suppress equivalent string-literal mutants in mutmut config"
```

---

## Task 2: Kill `hasattr` Guard Bypass Survivors

Six guards in `validate_all_cli_args` (`lib/validation.py` lines 306–328) survived because the existing integration tests only pass valid values. Adding tests with invalid values kills the bypass mutants.

**Files:**
- Modify: `tests/test_validation.py` — add 6 tests to the `TestCLIArgumentValidation` class (append after the last test in that class, before `TestKubernetesValidation`)

**Where to insert:** After the test at line ~518 (the last test in `TestCLIArgumentValidation`). Find the next class definition line (`class TestKubernetesValidation`) and insert before it.

### Step 1: Write the 6 failing tests

Append the following 6 test methods to the `TestCLIArgumentValidation` class in `tests/test_validation.py`. Add them just before the line that reads `class TestKubernetesValidation:`.

```python
    def test_validate_all_cli_args_rejects_invalid_secondary_context(self):
        """validate_all_cli_args must apply context validation to secondary_context."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="invalid context!",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            decommission=False,
        )
        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_validate_all_cli_args_rejects_invalid_method_via_integration(self):
        """validate_all_cli_args must call validate_cli_method for invalid method values."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="invalid-method",
            old_hub_action="secondary",
            log_format="text",
            decommission=False,
        )
        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_validate_all_cli_args_rejects_invalid_activation_method_via_integration(self):
        """validate_all_cli_args must call validate_cli_activation_method for invalid values."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            activation_method="bogus-activation",
            old_hub_action="secondary",
            log_format="text",
            decommission=False,
        )
        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_validate_all_cli_args_rejects_invalid_old_hub_action_via_integration(self):
        """validate_all_cli_args must call validate_cli_old_hub_action for invalid values."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="bogus-action",
            log_format="text",
            decommission=False,
        )
        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_validate_all_cli_args_rejects_invalid_log_format_via_integration(self):
        """validate_all_cli_args must call validate_cli_log_format for invalid values."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="bogus-format",
            decommission=False,
        )
        with pytest.raises(ValidationError):
            InputValidator.validate_all_cli_args(args)

    def test_validate_all_cli_args_rejects_unsafe_state_file_via_integration(self):
        """validate_all_cli_args must call validate_safe_filesystem_path for state_file."""
        args = MockArgs(
            primary_context="primary-hub",
            secondary_context="secondary-hub",
            method="passive",
            old_hub_action="secondary",
            log_format="text",
            state_file="../../../etc/passwd",
            decommission=False,
        )
        with pytest.raises(SecurityValidationError):
            InputValidator.validate_all_cli_args(args)
```

### Step 2: Run the new tests to confirm they fail before any code change

There is no production code to change — the goal is to show these tests would have caught the mutant. Run them to confirm they pass against the unmodified source:

```bash
python -m pytest tests/test_validation.py -k "invalid_secondary_context or invalid_method_via or invalid_activation_method_via or invalid_old_hub_action_via or invalid_log_format_via or unsafe_state_file_via" -v
```

Expected: **6 passed** (they pass against the real code; they only fail when a `hasattr` guard is mutated).

### Step 3: Run the full focused test suite

```bash
python -m pytest tests/test_validation.py tests/test_validation_parity.py -q
```

Expected: **88 passed** (82 original + 6 new)

### Step 4: Verify formatting

```bash
black --check --line-length 120 tests/test_validation.py
isort --check-only --profile black --line-length 120 tests/test_validation.py
```

Fix if needed:

```bash
black --line-length 120 tests/test_validation.py
isort --profile black --line-length 120 tests/test_validation.py
```

### Step 5: Commit

```bash
git add tests/test_validation.py
git commit -m "test: add validate_all_cli_args integration tests for hasattr guard coverage"
```

---

## Task 3: Verify Survivors Are Killed

Re-run mutation against the updated tests to confirm the Class B survivors are now killed.

### Step 1: Re-run mutation

```bash
source .venv/bin/activate
mutmut run
```

### Step 2: Check results

```bash
mutmut results 2>&1 | wc -l
```

Expected: Total survivors is now ≤ 10. The guard-bypass survivors for `secondary_context`, `method`, `activation_method`, `old_hub_action`, `log_format`, and `state_file` should no longer appear.

Confirm specifically with:

```bash
mutmut results 2>&1 | grep -E "hasattr|secondary_context|activation_method|old_hub_action|log_format|state_file"
```

Expected: no matches.

### Step 3: Document residual survivors

If any survivors remain, inspect them with `mutmut show <id>` and classify:
- If equivalent (default parameter string, etc.) → note for future `do_not_mutate_patterns` entries
- If missing scenario → add to a follow-up tracking note in `docs/development/mutation-testing-plan.md`

### Step 4: Commit

```bash
git add .  # in case mutmut touched any config state
git commit -m "chore: verify mutation survivor kill — validate_all_cli_args guards covered"
```

---

## Final Verification

Run the full test suite to confirm nothing regressed:

```bash
./run_tests.sh
```

Expected: all tests pass, all quality gates pass.

---

## Parity Note

No collection changes are needed. The collection's validation module
(`ansible_collections/tomazb/acm_switchover/plugins/module_utils/validation.py`)
uses dict-based `operation.get("field", default)` access, not `hasattr` guards.
The same `hasattr`-bypass pattern does not exist in collection validation.
The `validate_all_cli_args` function is Python CLI–only
(`docs/ansible-collection/parity-matrix.md`: CLI-only cross-argument enforcement).
