# RBACValidator Mutation Resolution Implementation Plan

**Goal:** Kill the highest-value surviving mutants in `lib/rbac_validator.py` and establish a documented baseline.

**Architecture:** Three changes: (1) add `do_not_mutate_patterns` to suppress ~200 equivalent logger-call survivors; (2) add a test that catches the `_get_hub_namespace_permissions` role-inversion mutant (validator gets operator permissions — an RBAC safety gap); (3) add `call_args_list` assertions to the cluster-permissions and namespace-permissions success tests so that mutations to permission names/verbs/namespaces are caught.

**Tech Stack:** Python 3.10+, pytest, mutmut 3.6.x (dev-only), lib/rbac_validator.py

---

## Background

Spike result: `lib/rbac_validator.py` (commit `aa56bcd`, 57 focused tests)

| Status | Count | % |
|--------|-------|---|
| Total | 897 | — |
| Killed | 428 | 47% |
| **Survived** | **460** | **51%** |

Survivor breakdown:
- **~200 equivalent noise**: `logger.info/debug/warning/error(...)` call mutations — message→None etc. No behavioral impact.
- **3 RBAC safety survivors**: `_get_hub_namespace_permissions` has `==` → `!=` mutant. Swaps validator and operator namespace permission sets — validator gets write permissions it shouldn't have.
- **~100 missing-assertion survivors**: `validate_cluster_permissions`, `validate_namespace_permissions`, `validate_managed_cluster_permissions`, and `validate_decommission_permissions` mock `check_permission` but never assert on `call_args_list`. Mutations that change the resource name, verb, or namespace passed to `check_permission` survive silently.
- **~50 default-param survivors**: Bool/string parameter defaults mutated — mostly equivalent since tests always pass explicit values.
- **~10 check_permission core**: cache_key bypass, subresource maxsplit.

---

## Task 1: Suppress Logger Noise

**Files:**
- Modify: `setup.cfg` — `[mutmut]` section

### Step 1: Add logger pattern

Open `setup.cfg`, find the `[mutmut]` section, and change it to:

```ini
[mutmut]
source_paths = lib/rbac_validator.py
pytest_add_cli_args_test_selection =
    tests/test_rbac_validator.py
also_copy = lib/
do_not_mutate_patterns =
    raise .*Error\(
    logger\.(info|debug|warning|error|exception)\(
```

### Step 2: Verify config parses

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

Expected: `do_not_mutate_patterns: ['raise .*Error\\(', 'logger\\.(info|debug|warning|error|exception)\\(']`

### Step 3: Confirm tests still green

```bash
python -m pytest tests/test_rbac_validator.py -q
```

Expected: `57 passed`

### Step 4: Re-run mutation

```bash
rm -rf mutants/ && mutmut run
```

### Step 5: Check new count

```bash
python3 -c "
import json
meta = json.load(open('mutants/lib/rbac_validator.py.meta'))
exit_codes = meta.get('exit_code_by_key', {})
survived = sum(1 for v in exit_codes.values() if v == 0)
killed = sum(1 for v in exit_codes.values() if v == 1)
print(f'Total: {len(exit_codes)} | Killed: {killed} ({killed*100//len(exit_codes)}%) | Survived: {survived}')
"
```

Expected: survived drops from 460 to roughly 250–300.

### Step 6: Commit

```bash
git add setup.cfg
git commit -m "chore: update mutmut config for lib/rbac_validator.py spike"
```

---

## Task 2: Fix `_get_hub_namespace_permissions` Role Inversion

**Files:**
- Modify: `tests/test_rbac_validator.py` — add to `TestRBACValidator` class

### Background

`_get_hub_namespace_permissions` has a `==` → `!=` mutant:
```python
# Original
if self.role == "validator":
    return self.VALIDATOR_HUB_NAMESPACE_PERMISSIONS
return self.OPERATOR_HUB_NAMESPACE_PERMISSIONS

# Mutant (survives!)
if self.role != "validator":   # ← SWAPPED — validator gets operator write perms
    return self.VALIDATOR_HUB_NAMESPACE_PERMISSIONS
return self.OPERATOR_HUB_NAMESPACE_PERMISSIONS
```

No test verifies that the validator role gets the read-only `VALIDATOR_HUB_NAMESPACE_PERMISSIONS`. This is a **RBAC safety gap**.

### Step 1: Write the failing test

Add this method to the `TestRBACValidator` class in `tests/test_rbac_validator.py`.
Insert after `test_validate_namespace_permissions_reuses_cached_namespace_exists_results` (around line 402):

```python
def test_validator_role_gets_read_only_namespace_permissions(self, mock_client):
    """Validator role must receive read-only namespace permissions, not operator write permissions."""
    mock_client.context = "test-context"
    validator = RBACValidator(mock_client, role="validator")

    perms = validator._get_hub_namespace_permissions()

    # Must return the validator (read-only) set, not the operator set
    assert perms is RBACValidator.VALIDATOR_HUB_NAMESPACE_PERMISSIONS
    assert perms is not RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS

    # Spot-check: validator must NOT have create/patch/delete on backupschedules
    backup_ns_perms = perms.get("open-cluster-management-backup", [])
    backup_schedule_rule = next(
        (r for r in backup_ns_perms
         if r[0] == "cluster.open-cluster-management.io" and r[1] == "backupschedules"),
        None,
    )
    assert backup_schedule_rule is not None, "backupschedules rule missing from validator permissions"
    assert "create" not in backup_schedule_rule[2], "validator must not have create on backupschedules"
    assert "patch" not in backup_schedule_rule[2], "validator must not have patch on backupschedules"
    assert "delete" not in backup_schedule_rule[2], "validator must not have delete on backupschedules"
```

### Step 2: Run the new test — expect PASS

```bash
python -m pytest tests/test_rbac_validator.py::TestRBACValidator::test_validator_role_gets_read_only_namespace_permissions -v
```

Expected: **PASS** (real code is correct; mutation would fail it)

### Step 3: Run full focused suite

```bash
python -m pytest tests/test_rbac_validator.py -q
```

Expected: `58 passed`

### Step 4: Commit

```bash
git add tests/test_rbac_validator.py
git commit -m "test: assert validator role receives read-only namespace permissions

Kills the _get_hub_namespace_permissions == vs != role inversion mutant.
Without this test a mutation swapping == to != would give validators
operator-level write permissions (create/patch/delete backupschedules etc)
without any test catching it."
```

---

## Task 3: Assert Correct Cluster Permissions via `call_args_list`

**Files:**
- Modify: `tests/test_rbac_validator.py` — strengthen `test_validate_cluster_permissions_success`

### Background

`test_validate_cluster_permissions_success` mocks `check_permission` and asserts `all_valid=True` and no errors. It never asserts **which** permissions were checked. Mutations that change a permission name, verb, or API group survive silently.

The existing Argo CD tests (already in the file around line 188) already use the `call_args_list` pattern — follow that precedent.

### Step 1: Strengthen the success test

Replace `test_validate_cluster_permissions_success` (starting around line 139) with this version:

```python
def test_validate_cluster_permissions_success(self, validator):
    """validate_cluster_permissions must check the exact OPERATOR_CLUSTER_PERMISSIONS set."""
    validator.check_permission = MagicMock(return_value=(True, ""))

    all_valid, errors = validator.validate_cluster_permissions()

    assert all_valid is True
    assert errors == []

    # Assert that every expected cluster permission was checked — no more, no less.
    expected = frozenset(
        (ag, r, v)
        for ag, r, verbs in RBACValidator.OPERATOR_CLUSTER_PERMISSIONS
        for v in verbs
    )
    actual = frozenset(
        (c.args[0], c.args[1], c.args[2])
        for c in validator.check_permission.call_args_list
    )
    assert actual == expected, (
        f"Permission set mismatch.\n"
        f"  Missing: {expected - actual}\n"
        f"  Unexpected: {actual - expected}"
    )
```

### Step 2: Run the test — expect PASS

```bash
python -m pytest tests/test_rbac_validator.py::TestRBACValidator::test_validate_cluster_permissions_success -v
```

Expected: **PASS**

### Step 3: Run full focused suite

```bash
python -m pytest tests/test_rbac_validator.py -q
```

Expected: all pass

### Step 4: Commit

```bash
git add tests/test_rbac_validator.py
git commit -m "test: assert exact permission set in validate_cluster_permissions

Adds call_args_list assertion so mutations to permission names, verbs,
or API groups in the cluster permission checks are caught. Follows the
existing pattern already used for Argo CD permission assertions."
```

---

## Task 4: Assert Correct Namespace Permissions via `call_args_list`

**Files:**
- Modify: `tests/test_rbac_validator.py` — strengthen `test_validate_namespace_permissions_success`

### Step 1: Strengthen the namespace success test

Replace `test_validate_namespace_permissions_success` (around line 355) with:

```python
def test_validate_namespace_permissions_success(self, validator):
    """validate_namespace_permissions must check every permission in OPERATOR_HUB_NAMESPACE_PERMISSIONS."""
    validator.client.namespace_exists.return_value = True
    validator.check_permission = MagicMock(return_value=(True, ""))

    all_valid, errors = validator.validate_namespace_permissions()

    assert all_valid is True
    assert errors == []

    # Assert that every expected namespaced permission was checked.
    expected = frozenset(
        (ag, r, v, ns)
        for ns, rules in RBACValidator.OPERATOR_HUB_NAMESPACE_PERMISSIONS.items()
        for ag, r, verbs in rules
        for v in verbs
    )
    # check_permission is called as check_permission(api_group, resource, verb, namespace)
    actual = frozenset(
        (c.args[0], c.args[1], c.args[2], c.args[3])
        for c in validator.check_permission.call_args_list
        if len(c.args) >= 4
    )
    assert actual == expected, (
        f"Namespace permission set mismatch.\n"
        f"  Missing: {expected - actual}\n"
        f"  Unexpected: {actual - expected}"
    )
```

### Step 2: Run the test — expect PASS

```bash
python -m pytest tests/test_rbac_validator.py::TestRBACValidator::test_validate_namespace_permissions_success -v
```

Expected: **PASS**

### Step 3: Run full suite

```bash
python -m pytest tests/test_rbac_validator.py -q
```

Expected: all pass

### Step 4: Commit

```bash
git add tests/test_rbac_validator.py
git commit -m "test: assert exact permission set in validate_namespace_permissions"
```

---

## Task 5: Verify and Record Final Baseline

### Step 1: Re-run mutation with all fixes in place

```bash
source .venv/bin/activate
rm -rf mutants/ && mutmut run
```

### Step 2: Get counts

```bash
python3 -c "
import json
from collections import Counter
meta = json.load(open('mutants/lib/rbac_validator.py.meta'))
exit_codes = meta.get('exit_code_by_key', {})
survived = sum(1 for v in exit_codes.values() if v == 0)
killed = sum(1 for v in exit_codes.values() if v == 1)
print(f'Total: {len(exit_codes)} | Killed: {killed} ({killed*100//len(exit_codes)}%) | Survived: {survived}')

func_survived = Counter()
for key, code in exit_codes.items():
    if code == 0:
        parts = key.split('ǁ')
        func = parts[-1].rsplit('__mutmut_', 1)[0] if len(parts) >= 2 else key
        func_survived[func] += 1
for func, count in func_survived.most_common(8):
    print(f'  {count:3d}  {func}')
"
```

### Step 3: Classify residual survivors

Inspect top survivors with `mutmut show <id>`. Document residual survivors in `docs/development/mutation-testing-plan.md` under a new "Phase 1 Baselines" section:

Likely residuals (defer, document, do not fix in this PR):
- Default bool parameter mutations (`False` → `True`) across `validate_cluster_permissions`, `validate_decommission_permissions`, etc. — equivalent when tests use explicit args
- `cache_key = None` in `check_permission` — functional but tests don't verify caching behavior
- `resource.split("/", )` (no maxsplit) — edge case for multi-slash subresource paths

### Step 4: Run full test suite

```bash
./run_tests.sh
```

Expected: all tests pass, all quality gates pass.

### Step 5: Commit config back to validation.py scope

After the rbac_validator work, restore `setup.cfg` to validation.py scope so the last recorded config is usable:

```bash
# In setup.cfg, restore:
# source_paths = lib/validation.py
# pytest_add_cli_args_test_selection = tests/test_validation.py / tests/test_validation_parity.py
# also_copy = lib/
# do_not_mutate_patterns = (validation.py patterns)

git add setup.cfg
git commit -m "chore: restore mutmut config to lib/validation.py after rbac_validator spike"
```

---

## Parity Note

`validate_all_cli_args` is Python CLI–only (no collection equivalent). `RBACValidator` and its permission checks ARE parity-sensitive — the collection RBAC module at `ansible_collections/tomazb/acm_switchover/plugins/modules/acm_rbac_validate.py` must maintain equivalent permission sets. The `test_rbac_collection_parity.py` suite already enforces this; no additional parity action is needed for this resolution.
