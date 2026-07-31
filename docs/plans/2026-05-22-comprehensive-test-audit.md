# Comprehensive Test Audit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Find and fix all meaningful test gaps across both the Python CLI and Ansible collection, ensuring every dual-supported capability has equivalent behavioral coverage on both sides.

**Architecture:** Five parallel analysis agents each own one domain (Python logic, collection logic, structural YAML, parity, coverage). Findings are synthesized and ranked, then tests are written module-by-module in the worktree. All work lands in PR #67 (`feat/graphify-test-coverage` → `ansible`).

**Tech Stack:** Python 3.14, pytest, pytest-cov, ansible-core==2.15.*, black, isort, mypy

**Worktree:** `.worktrees/graphify-test-coverage` (branch `feat/graphify-test-coverage`)
**Baseline:** 1876 tests, all green, as of HEAD `90a481f`

---

## Phase 0: Setup

### Task 0: Verify clean baseline

**Files:** None modified.

**Step 1: Confirm worktree and baseline**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/graphify-test-coverage
source ../../.venv/bin/activate
python -m pytest tests/ ../../ansible_collections/tomazb/acm_switchover/tests/unit/ \
  -q --ignore=tests/e2e --ignore=tests/release 2>&1 | tail -5
```

Expected: `1876 passed` (or close — some skips are normal)

---

## Phase 1: Parallel Analysis (run all 5 agents simultaneously)

All 5 agents are independent. Dispatch them in one step and wait for all to complete.

### Task 1-A: Python Module Logic Gaps

Dispatch an `explore` agent with this prompt:

> You are auditing the Python CLI test coverage for rh-acm-switchover (repo root: `/home/tomaz/sources/rh-acm-switchover`).
>
> For EVERY file in `lib/` and `modules/` (excluding `__init__.py`, `preflight_validators.py` which is a shim):
> 1. Read the source file
> 2. List every public function/method and its purpose
> 3. Find the corresponding test file(s) in `tests/`
> 4. For each function, check what is NOT tested:
>    - Missing error path tests (what happens when an API call raises ApiException 404, 500?)
>    - Missing boundary conditions (empty list, None input, max values)
>    - Missing negative tests (what should FAIL but isn't tested to fail)
>    - Functions with NO tests at all
>    - Critical behavior covered by only 1 test (fragile)
>
> Output a structured list:
> ```
> FILE: lib/xxx.py
>   FUNCTION: function_name()
>   GAP: [description of what's missing]
>   SEVERITY: HIGH/MEDIUM/LOW
>   REASON: [why this could catch a real bug]
> ```
>
> Only report gaps where a missing test could catch a REAL bug. Skip trivial getters, constants, and __init__ methods.
> HIGH = safety-critical (wrong-cluster mutation, data loss, silent failure)
> MEDIUM = incorrect behavior under error conditions
> LOW = edge case that rarely matters in practice

**Expected output:** Structured findings report from Agent A

---

### Task 1-B: Collection Module Logic Gaps

Dispatch an `explore` agent with this prompt:

> You are auditing the Ansible collection test coverage for rh-acm-switchover (repo root: `/home/tomaz/sources/rh-acm-switchover`).
>
> For EVERY Python file in:
> - `ansible_collections/tomazb/acm_switchover/plugins/modules/`
> - `ansible_collections/tomazb/acm_switchover/plugins/module_utils/`
>
> 1. Read the source file
> 2. Identify the module's key behaviors (what it checks, what it returns, what it modifies)
> 3. Find the corresponding test file(s) in `ansible_collections/tomazb/acm_switchover/tests/unit/plugins/`
> 4. For each key behavior, check what is NOT tested:
>    - Missing `check_mode` test (does the module handle check_mode=True correctly?)
>    - Missing `changed=True` vs `changed=False` differentiation tests
>    - Missing argument_spec validation tests (required args missing, invalid values)
>    - Missing failure/error path tests (what if the k8s resource isn't found?)
>    - Module outcomes that are returned but never asserted on in tests
>    - `failed_when` conditions that are never triggered in tests
>
> Output format:
> ```
> FILE: plugins/modules/acm_xxx.py
>   BEHAVIOR: [what it does]
>   GAP: [what's not tested]
>   SEVERITY: HIGH/MEDIUM/LOW
>   REASON: [why this catches a real bug]
> ```
>
> HIGH = check_mode not tested (could accidentally mutate in check mode), changed incorrectly reported
> MEDIUM = error paths not covered, argument validation missing
> LOW = minor edge cases

**Expected output:** Structured findings report from Agent B

---

### Task 1-C: Structural YAML Contract Gaps

Dispatch an `explore` agent with this prompt:

> You are auditing structural test coverage for Ansible role task files in rh-acm-switchover (repo root: `/home/tomaz/sources/rh-acm-switchover`).
>
> Roles are in: `ansible_collections/tomazb/acm_switchover/roles/`
> Structural tests are in: `ansible_collections/tomazb/acm_switchover/tests/unit/`
>
> For EACH role, list its task YAML files under `tasks/`. Then check the structural test files for contracts that:
> 1. Verify task ordering (e.g., "verify before mutate", "discover before act")
> 2. Verify dry-run guards (tasks must check `acm_switchover_execution.mode | default('dry_run') != 'dry_run'`)
> 3. Verify hub context isolation (tasks use `acm_switchover_hubs.primary.kubeconfig` or `.secondary.kubeconfig`)
> 4. Verify guard conditions (`when:` conditions prevent mutations without prerequisite)
>
> Find task files that have ZERO structural test coverage and task files where key contracts are not asserted.
>
> Output format:
> ```
> ROLE: role_name
>   TASK_FILE: tasks/xxx.yml
>   CONTRACT_MISSING: [what safety contract is not tested]
>   SEVERITY: HIGH/MEDIUM/LOW
>   REASON: [why this matters]
> ```
>
> HIGH = mutation task with no dry-run guard test, ordering not tested
> MEDIUM = guard condition exists but no test verifies it
> LOW = minor structural omission

**Expected output:** Structural gap findings by role

---

### Task 1-D: Parity Gaps

Dispatch an `explore` agent with this prompt:

> You are auditing test parity between Python CLI and Ansible collection for rh-acm-switchover (repo root: `/home/tomaz/sources/rh-acm-switchover`).
>
> All capabilities in `docs/ansible-collection/parity-matrix.md` with status `dual-supported` must have equivalent behavioral test coverage on BOTH sides.
>
> For each dual-supported capability, find the relevant tests:
> - Python tests in `tests/` (e.g., `test_activation.py`, `test_primary_prep.py`)
> - Collection tests in `ansible_collections/tomazb/acm_switchover/tests/unit/`
>
> For each capability, list behaviors tested on the Python side but NOT equivalently tested on the collection side, and vice versa.
>
> Focus on BEHAVIORAL gaps, not structural ones. A "behavior" is: "when X happens, Y should result". If Python has 10 tests for activation and collection has 3, list the 7 missing behaviors.
>
> Specifically check these capabilities:
> 1. preflight validation (Python: `test_preflight*.py`, Collection: `test_preflight*.py`)
> 2. primary prep (Python: `test_primary_prep.py`, Collection: `test_primary_prep_auto_import.py`)
> 3. activation (Python: `test_activation.py`, Collection: `test_activation_auto_import.py`, `test_ansible_resilience_contracts.py`)
> 4. post-activation (Python: `test_post_activation.py`, Collection: `test_post_activation_*.py`)
> 5. finalization (Python: `test_finalization.py`, Collection: `test_finalization_*.py`)
> 6. RBAC validation (Python: `test_rbac_validator.py`, Collection: `plugins/modules/test_acm_rbac_validate.py`)
> 7. ArgoCD management (Python: `test_argocd*.py`, Collection: `test_argocd_*.py`)
> 8. decommission (Python: `test_decommission.py`, Collection: check `test_ansible_resilience_contracts.py`)
> 9. checkpoint/restore (Python: `test_utils.py` StateManager section, Collection: `plugins/action/test_checkpoint_phase_runtime.py`)
> 10. machine-readable reports (Python: `test_report_artifacts.py`, Collection: `plugins/modules/test_acm_report_artifact.py`)
>
> Output format:
> ```
> CAPABILITY: xxx
>   PYTHON_BEHAVIOR: [behavior tested in Python]
>   COLLECTION_GAP: [same behavior NOT in collection tests]
>   SEVERITY: HIGH/MEDIUM/LOW
> ```
> and reverse:
> ```
> CAPABILITY: xxx
>   COLLECTION_BEHAVIOR: [behavior tested in collection]
>   PYTHON_GAP: [same behavior NOT in Python tests]
>   SEVERITY: HIGH/MEDIUM/LOW
> ```

**Expected output:** Parity gap table per capability

---

### Task 1-E: Coverage Report

**Step 1: Run Python coverage**

```bash
cd /home/tomaz/sources/rh-acm-switchover
source .venv/bin/activate
python -m pytest tests/ -q \
  --ignore=tests/e2e --ignore=tests/release \
  --cov=lib --cov=modules \
  --cov-report=term-missing \
  --cov-report=json:coverage-python.json \
  2>&1 | grep -E "TOTAL|lib/|modules/" | head -60
```

**Step 2: Run collection coverage**

```bash
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/ -q \
  --cov=ansible_collections/tomazb/acm_switchover/plugins \
  --cov-report=term-missing \
  --cov-report=json:coverage-collection.json \
  2>&1 | grep -E "TOTAL|plugins/" | head -60
```

**Step 3: Identify files below 80% coverage and record uncovered line ranges**

Parse the output and list every file with coverage < 80% along with the specific uncovered line numbers.

---

## Phase 2: Synthesize Findings

### Task 2: Build the master findings list

After all 5 agents complete, create a prioritized work list in the session SQL DB:

```sql
CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY,
  domain TEXT,  -- 'python-logic', 'collection-logic', 'structural', 'parity', 'coverage'
  file_path TEXT,
  gap_description TEXT,
  severity TEXT,  -- HIGH, MEDIUM, LOW
  test_file TEXT,
  status TEXT DEFAULT 'pending'
);
```

Insert all findings, then sort by severity (HIGH first), then deduplicate overlapping gaps.

---

## Phase 3: Write Tests

### Task 3: Write tests for each finding

For each finding in the DB (HIGH priority first):

**Pattern for each test batch:**

1. Read the source file to understand the exact behavior
2. Write the test — it must assert on REAL behavior, not just "function was called"
3. Verify the test FAILS before any implementation change (tests should already pass if testing existing code, so verify they pass)
4. Run the relevant test file: `pytest tests/test_xxx.py -v -k test_new_function`
5. Fix any formatting: `black <file> && isort <file>`
6. Commit after each module: `git commit -m "test(xxx): [what was added and why]"`

**Required test patterns for collection modules:**

```python
# Pattern: check_mode must not mutate
def test_module_does_nothing_in_check_mode(module_mock):
    module_mock.check_mode = True
    result = run_module_logic(...)
    assert result['changed'] is False
    # Verify no k8s patch was called

# Pattern: changed=True only when mutation occurred  
def test_module_reports_changed_false_when_already_in_desired_state(module_mock):
    # Pre-condition: desired state already exists
    result = run_module_logic(...)
    assert result['changed'] is False

# Pattern: argument_spec rejects invalid inputs
def test_module_fails_on_missing_required_arg(capfd):
    with pytest.raises(SystemExit):
        run_module({'required_arg': None}, ...)
```

**Required test patterns for Python modules:**

```python
# Pattern: API error path
def test_function_raises_switchover_error_on_api_404(mock_client):
    mock_client.get_resource.side_effect = ApiException(status=404)
    with pytest.raises(FatalError, match="not found"):
        function_under_test(mock_client, ...)

# Pattern: empty/None input boundary
def test_function_handles_empty_list_gracefully(mock_client):
    result = function_under_test(mock_client, resources=[])
    assert result == expected_empty_result
```

---

## Phase 4: Validation

### Task 4: Full strict run

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/graphify-test-coverage
source ../../.venv/bin/activate
STRICT_QUALITY=1 ../../run_tests.sh 2>&1 | tail -20
```

Expected: all tests pass, no formatting errors, no mypy/bandit errors.

If anything fails, fix it before proceeding.

---

## Phase 5: Push and Update PR

### Task 5: Update PR #67

**Step 1: Push branch**

```bash
cd /home/tomaz/sources/rh-acm-switchover/.worktrees/graphify-test-coverage
git push origin feat/graphify-test-coverage
```

**Step 2: Update PR description**

```bash
gh pr edit 67 --body "$(cat <<'EOF'
## Comprehensive test audit — Python CLI + Ansible collection

### What this PR does
- Fixes all meaningful test gaps found via parallel multi-agent audit
- Ensures every dual-supported capability has equivalent behavioral coverage on both sides
- Adds missing error path, boundary, check_mode, and parity tests

### Methodology
Five parallel analysis agents covered:
- Python module logic (lib/ + modules/)
- Collection module logic (plugins/modules/ + module_utils/)
- Structural YAML contracts (role task files)
- Python ↔ Ansible parity (dual-supported capabilities)
- Coverage report (pytest-cov both sides)

### Verification
- Full suite passes: `./run_tests.sh` strict mode
- No superficial assertions — every test catches a real potential bug
EOF
)"
```

---

## Appendix: Key Reference Paths

| Purpose | Path |
|---------|------|
| Python source | `lib/`, `modules/` |
| Collection modules | `ansible_collections/tomazb/acm_switchover/plugins/modules/` |
| Collection utils | `ansible_collections/tomazb/acm_switchover/plugins/module_utils/` |
| Collection roles | `ansible_collections/tomazb/acm_switchover/roles/` |
| Python tests | `tests/` |
| Collection tests | `ansible_collections/tomazb/acm_switchover/tests/unit/` |
| Parity matrix | `docs/ansible-collection/parity-matrix.md` |
| Behavior map | `docs/ansible-collection/behavior-map.md` |
| Worktree | `.worktrees/graphify-test-coverage` |
| Branch | `feat/graphify-test-coverage` |
| PR | #67 |
