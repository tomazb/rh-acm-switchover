# EE Base-Stage Python Pip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install Python 3.12's pip RPM in Ansible Builder's base interpreter package step instead of relying on final-stage bindep processing.

**Architecture:** Keep the interpreter and its pip RPM in the existing `dependencies.python_interpreter.package_system` declaration so Ansible Builder installs both through its generated base-stage `$PYPKG` command. Strengthen the compatibility contract to validate placement, remove the misleading bindep entries, and align the execution-environment documentation.

**Tech Stack:** Ansible Builder v3 execution-environment YAML, pytest, PyYAML, Markdown

## Global Constraints

- Address only the P1 execution-environment build-order issue; leave the already-resolved syntax-check guardrail unchanged.
- Preserve the existing `ansible-core>=2.16,<2.22` and Python 3.12 compatibility policy.
- Do not change parity status or protected operational documents.
- Use test-driven development and observe the new guardrail fail before changing production configuration.

---

### Task 1: Enforce Base-Stage Pip Placement

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py`
- Modify: `ansible_collections/tomazb/acm_switchover/execution-environment.yml`
- Modify: `ansible_collections/tomazb/acm_switchover/bindep.txt`

**Interfaces:**
- Consumes: `dependencies.python_interpreter.package_system` from `execution-environment.yml`
- Produces: A static compatibility contract requiring `python3.12` and `python3.12-pip` in the base-stage interpreter package set and rejecting `python3.12-pip` from final-stage bindep input

- [ ] **Step 1: Replace the package-presence assertion with a placement assertion**

Update `test_execution_environment_selects_an_interpreter_the_core_range_can_run()` so it parses the whitespace-separated `package_system` value and asserts:

```python
interpreter_packages = set(interpreter["package_system"].split())
expected_packages = {f"python{EE_PYTHON_VERSION}", f"python{EE_PYTHON_VERSION}-pip"}
assert expected_packages.issubset(interpreter_packages), (
    "EE base interpreter setup must install "
    f"{', '.join(sorted(expected_packages))}; found {sorted(interpreter_packages)}"
)

bindep = (COLLECTION_ROOT / "bindep.txt").read_text()
bindep_packages = {line.split()[0] for line in bindep.splitlines() if line.strip() and not line.startswith("#")}
assert f"python{EE_PYTHON_VERSION}-pip" not in bindep_packages, (
    f"python{EE_PYTHON_VERSION}-pip must be installed with the base interpreter; "
    "bindep packages are installed too late"
)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py::test_execution_environment_selects_an_interpreter_the_core_range_can_run -q
```

Expected: FAIL because `package_system` contains only `python3.12` and `python3.12-pip` is still declared in `bindep.txt`.

- [ ] **Step 3: Move the pip RPM into the interpreter package declaration**

Set:

```yaml
python_interpreter:
  package_system: "python3.12 python3.12-pip"
  python_path: /usr/bin/python3.12
```

Remove both Python 3.12-specific RPM lines and their obsolete ordering explanation from `bindep.txt`, leaving the existing default Python bindep requirements unchanged:

```text
python3 [platform:rpm]
python3-pip [platform:rpm]
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py::test_execution_environment_selects_an_interpreter_the_core_range_can_run -q
```

Expected: PASS.

- [ ] **Step 5: Commit the placement fix**

```bash
git add ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py ansible_collections/tomazb/acm_switchover/execution-environment.yml ansible_collections/tomazb/acm_switchover/bindep.txt
git commit -m "fix(ansible): install EE pip in base stage"
```

### Task 2: Align Documentation and Verify

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/docs/compatibility.md`
- Verify: `tests/test_ci_guardrails.py`

**Interfaces:**
- Consumes: The base-stage package placement established in Task 1
- Produces: Operator-facing compatibility documentation that accurately describes Ansible Builder's build order

- [ ] **Step 1: Correct the execution-environment build-order documentation**

In the execution-environment section, state that `package_system` declares both `python3.12` and `python3.12-pip`, which Ansible Builder installs together in its base-stage interpreter command before the pip bootstrap and builder stage. Preserve the existing support-tier and UBI 9 rationale.

- [ ] **Step 2: Run targeted tests**

Run:

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py tests/test_ci_guardrails.py -q
```

Expected: all tests PASS.

- [ ] **Step 3: Run formatting and whitespace checks**

Run:

```bash
black --check --line-length 120 --diff ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py
git diff --check
```

Expected: both commands exit successfully with no formatting or whitespace errors.

- [ ] **Step 4: Inspect the final diff for scope and correctness**

Run:

```bash
git diff --stat HEAD~1
git diff HEAD~1 -- ansible_collections/tomazb/acm_switchover/execution-environment.yml ansible_collections/tomazb/acm_switchover/bindep.txt ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py ansible_collections/tomazb/acm_switchover/docs/compatibility.md
```

Expected: only the approved P1 configuration, guardrail, and documentation surfaces have changed.

- [ ] **Step 5: Commit documentation**

```bash
git add ansible_collections/tomazb/acm_switchover/docs/compatibility.md docs/superpowers/plans/2026-08-11-ee-base-stage-python-pip.md
git commit -m "docs(ansible): clarify EE pip build order"
```
