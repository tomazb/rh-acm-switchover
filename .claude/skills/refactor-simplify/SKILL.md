---
name: refactor-simplify
description: Safely simplify a Python module by extracting long methods, consolidating boilerplate, and applying existing patterns. Usage - /refactor-simplify modules/finalization.py  or  /refactor-simplify --review modules/finalization.py
---

# Safe Code Simplification

Simplify a Python module without changing its behavior. This skill applies existing
project patterns consistently and breaks long methods into smaller, testable pieces.

## Philosophy

### 1. Functionality Is Sacred

Never change behavior. Inputs, outputs, side effects, error handling, log messages,
state transitions, and edge cases must remain identical. If you cannot prove
preservation, do not make the change.

### 2. Readability Over Brevity

Fewer lines is not the goal. Clearer lines are. A longer `if` block that reads like
prose is better than a dense one-liner that needs mental parsing. Method extraction
should make the orchestrator read like a table of contents.

### 3. Respect The Codebase

Before touching code, read `CLAUDE.md` (or `AGENTS.md`), `setup.cfg`, and the
surrounding code in the same module. The local conventions override your preferences
and the patterns in this skill. If the project uses `logger.info("Starting %s...", name)`,
keep that style — do not switch to f-strings.

### 4. Scope Discipline

Touch only the file the user asked about and code needed to support the
simplification. Do not broaden the diff with unrelated cleanup. If you spot an
issue in another file, mention it in the summary — do not fix it.

## Arguments

The user provides a file path relative to the repository root (e.g., `modules/finalization.py`).

Optional flag: `--review` for Review-Only mode (analysis without edits).

If no argument is provided, ask the user which file to simplify. Suggest the
highest-impact candidates:

1. `modules/finalization.py` (1,624 lines — `_fix_backup_schedule_collision` is 178 lines)
2. `modules/post_activation.py` (1,259 lines — `_load_kubeconfig_data` is 120 lines)
3. `modules/activation.py` (956 lines — `_verify_patch_applied` is 102 lines)
4. `acm_switchover.py` (1,082 lines — `parse_args` is 231 lines)
5. `modules/preflight/backup_validators.py` (695 lines — 3 `run()` methods >100 lines)

## Operating Modes

### Review-Only Mode

Use when the user passes `--review` or asks for critique, assessment, or audit
without requesting direct edits.

Produce a structured report of simplification opportunities with:
- priority ranking (high/medium/low)
- estimated line reduction per opportunity
- behavior-preservation risk assessment (safe / needs-care / risky)
- concrete rewrite sketches for the top 3 opportunities
- suggestions intentionally left unapplied and why

Do NOT modify any files in this mode.

### Apply-Changes Mode (default)

Use when the user wants the code actually simplified. Make direct edits,
verify incrementally, and summarize what changed.

---

## Safety Rules

These rules are **non-negotiable**. Violating any one of them means the refactoring is rejected.

1. **No behavior changes** — every code path, error, log message, and return value must remain identical
2. **No new files** — do not create new utility modules, helpers, or abstractions
3. **No new dependencies** — do not add imports from outside the existing project
4. **No signature changes on public methods** — only add new private methods (prefixed with `_`)
5. **Preserve all logging** — every `logger.info/warning/error` call must produce the same output
6. **Preserve all state tracking** — every `is_step_completed` / `mark_step_completed` / `state.step()` call stays
7. **Preserve exception types** — if a method raises `SwitchoverError`, the refactored version raises the same
8. **Tests must pass** — run `./run_tests.sh` before and after; zero regressions allowed

## When Not To Simplify

Hold back when:

- The code sits on a **retry or timeout hot path** and the current shape may be
  intentional for error-recovery semantics (e.g., nested try/except with different
  fallback strategies per exception type)
- A check looks redundant but appears to **protect against a known edge case**,
  Kubernetes API quirk, or ACM version difference — treat it as intentional until
  you can prove otherwise
- The user asked for a specific file and the cleanup would **expand into other files**
- The only available change is **cosmetic and subjective** (e.g., renaming a variable
  you personally dislike)
- A documented workaround or compatibility shim exists (look for comments containing
  `workaround`, `compat`, `legacy`, `TODO`, or version-gated `if` blocks)
- You **cannot verify** the change with existing tests — if there are no tests
  covering the code path, flag it as a suggestion instead of applying it

---

## Procedure

### Phase 0: Read Project Context

Before analyzing the target file, read these files to understand project conventions:

1. `AGENTS.md` (or `CLAUDE.md`) — project-level AI instructions, patterns, constants usage
2. `setup.cfg` — formatter, linter, and test configuration
3. `lib/constants.py` — verify any magic strings used in the target file
4. `lib/waiter.py` — understand `wait_for_condition()` signature for Transform 3

This ensures your simplifications align with the codebase, not just generic best practices.

### Phase 1: Analyze the Target File

Read the target file completely. Identify and categorize every simplification
opportunity into one of these **seven transforms** (described below). List them
as a checklist for the user, ranked by impact.

Present findings like this:

```
Found N simplification opportunities in <file>:

EXTRACT METHOD (high impact):
  [ ] _fix_backup_schedule_collision (178 lines) → split into 3 methods  [safe]
  [ ] _verify_new_backups (107 lines) → extract polling into helper      [safe]

CONSOLIDATE ERROR HANDLING (high impact):
  [ ] Lines 45-55, 120-130, 200-210 → identical try/except (3 instances) [safe]

USE EXISTING WAITER (medium impact):
  [ ] Lines 447-479 → replace manual while-loop with wait_for_condition() [needs-care]

CONVERT TO DRY-RUN DECORATOR (medium impact):
  [ ] Lines 88-92 → replace if self.dry_run with @dry_run_skip           [safe]

PYTHON IDIOM QUICK-WINS (low impact):
  [ ] Line 312 → len(lst) == 0 → not lst                                 [safe]

Estimated reduction: ~X lines
Risk tags: [safe] = provably identical, [needs-care] = verify with tests, [risky] = suggest only
```

**In Review-Only mode**: stop here, present the analysis with concrete rewrite
sketches for the top 3 opportunities, and list any suggestions you intentionally
left unapplied. Do NOT modify files.

**In Apply-Changes mode**: wait for the user to confirm, then proceed to Phase 2.

---

### Phase 2: Apply Transforms (One at a Time)

Apply each transform individually. After each transform, verify the file is syntactically valid by running:

```bash
python3 -c "import ast; ast.parse(open('<file>').read()); print('OK')"
```

#### Transform 1: Extract Long Methods

For any method longer than **80 lines**, extract logical blocks into private helper methods.

**Rules:**
- New method names must start with `_` and clearly describe the extracted block
- The new method must be on the same class (not a standalone function)
- Pass only the minimum required arguments — do not pass `self` attributes as arguments if the method is on the same class
- Preserve the exact same logging, error handling, and return values
- Keep the original method as a thin orchestrator that calls the extracted helpers

**Pattern:**

Before:
```python
def _fix_backup_schedule_collision(self):
    # Block A: detect collision (40 lines)
    ...
    # Block B: resolve naming (60 lines)
    ...
    # Block C: apply fix (78 lines)
    ...
```

After:
```python
def _fix_backup_schedule_collision(self):
    collision = self._detect_schedule_collision()
    if collision:
        resolved_name = self._resolve_schedule_naming(collision)
        self._apply_schedule_fix(resolved_name)

def _detect_schedule_collision(self):
    # Block A (40 lines, unchanged)
    ...

def _resolve_schedule_naming(self, collision):
    # Block B (60 lines, unchanged)
    ...

def _apply_schedule_fix(self, resolved_name):
    # Block C (78 lines, unchanged)
    ...
```

#### Transform 2: Consolidate Identical Error Handling

When multiple methods share the exact same try/except pattern, extract it.

**Pattern — Phase-level error handling:**

Before (repeated 4+ times):
```python
def some_phase(self) -> bool:
    logger.info("Starting X...")
    try:
        # ... phase logic ...
        logger.info("X completed successfully")
        return True
    except SwitchoverError as e:
        logger.error("X failed: %s", e)
        self.state.add_error(str(e), "phase_x")
        return False
    except Exception as e:
        logger.error("Unexpected error in X: %s", e)
        self.state.add_error(f"Unexpected: {str(e)}", "phase_x")
        return False
```

After:
```python
def some_phase(self) -> bool:
    return self._run_phase("X", "phase_x", self._do_some_phase)

def _do_some_phase(self):
    # ... phase logic (no try/except needed) ...

def _run_phase(self, display_name, error_key, func) -> bool:
    """Standard phase execution with error handling."""
    logger.info("Starting %s...", display_name)
    try:
        func()
        logger.info("%s completed successfully", display_name)
        return True
    except SwitchoverError as e:
        logger.error("%s failed: %s", display_name, e)
        self.state.add_error(str(e), error_key)
        return False
    except Exception as e:
        logger.error("Unexpected error in %s: %s", display_name, e)
        self.state.add_error(f"Unexpected: {str(e)}", error_key)
        return False
```

**Only apply this transform when the try/except structure is truly identical.** If there are differences in exception types caught, logging format, or control flow, leave them as-is.

#### Transform 3: Use Existing `wait_for_condition()`

The project has `lib/waiter.py` with a `wait_for_condition()` utility. Replace manual polling loops that match this shape:

Before:
```python
deadline = time.time() + timeout
while time.time() < deadline:
    result = self._check_something()
    if result:
        return result
    time.sleep(interval)
raise SwitchoverError("Timed out waiting for X")
```

After:
```python
from lib.waiter import wait_for_condition

return wait_for_condition(
    condition_fn=self._check_something,
    timeout=timeout,
    interval=interval,
    description="waiting for X",
)
```

**Only apply when the manual loop is a straightforward poll-until-true pattern.** If the loop has complex retry logic, partial results, or side effects between iterations, leave it as-is.

#### Transform 4: Convert Manual Dry-Run Checks to `@dry_run_skip`

The project has a `@dry_run_skip` decorator in `lib/utils.py`. Replace manual checks:

Before:
```python
def scale_deployment(self, name, namespace, replicas):
    if self.dry_run:
        logger.info("[DRY-RUN] Would scale deployment %s/%s to %d replicas", namespace, name, replicas)
        return {}
    # ... actual implementation ...
```

After:
```python
@dry_run_skip(message="Would scale deployment {name} in {namespace} to {replicas} replicas", return_value={})
def scale_deployment(self, name, namespace, replicas):
    # ... actual implementation ...
```

**Only apply when:**
- The dry-run block is at the top of the method (guard clause pattern)
- The return value is a simple literal (`{}`, `None`, `True`, `[]`)
- The log message can be expressed as a format string using the method's parameter names

**Do NOT apply when:**
- The dry-run check is in the middle of the method with conditional logic
- The dry-run path computes a mock return value
- The method already has `@dry_run_skip`

#### Transform 5: Remove Dead Code

Remove any code that is provably unreachable:

- Methods that are never called (verify with grep across the entire codebase)
- Variables that are assigned but never read
- Import statements for unused symbols
- Commented-out code blocks (more than 3 consecutive commented lines)

**Rules:**
- Grep the entire project before removing a method: `grep -r "method_name" --include="*.py"`
- Do NOT remove methods that are part of a public API or could be called dynamically
- Do NOT remove `# TODO` or `# FIXME` comments — those are intentional markers

#### Transform 6: Simplify Conditional Logic

Reduce nesting depth where straightforward:

**Early returns:**
```python
# Before
def check(self):
    if condition_a:
        if condition_b:
            return do_thing()
    return None

# After
def check(self):
    if not condition_a:
        return None
    if not condition_b:
        return None
    return do_thing()
```

**Guard clauses at method entry:**
```python
# Before
def process(self, items):
    if items:
        for item in items:
            self._handle(item)

# After
def process(self, items):
    if not items:
        return
    for item in items:
        self._handle(item)
```

**Only apply when it clearly improves readability.** Do not refactor
conditional logic that is intentionally structured for domain clarity.

#### Transform 7: Python Idiom Quick-Wins

Apply standard Python simplifications when they make the code clearer without
changing behavior. These are low-risk, high-readability changes.

**Boolean checks:**
```python
# Before                          # After
if len(items) == 0:               if not items:
if len(items) > 0:                if items:
if x == True:                     if x:
if x == False:                    if not x:
if x is not None and len(x) > 0: if x:  # only when falsy=empty is correct
```

**Aggregation with `any()` / `all()`:**
```python
# Before
has_error = False
for item in items:
    if item.status == 'error':
        has_error = True
        break

# After
has_error = any(item.status == 'error' for item in items)
```

**Comprehensions for simple transforms:**
```python
# Before
names = []
for cluster in clusters:
    if cluster.get("status") == "ready":
        names.append(cluster["name"])

# After
names = [c["name"] for c in clusters if c.get("status") == "ready"]
```

**`contextlib.suppress` for intentional ignoring:**
```python
# Before
try:
    os.remove(tmp_file)
except FileNotFoundError:
    pass

# After
from contextlib import suppress
with suppress(FileNotFoundError):
    os.remove(tmp_file)
```

**When NOT to apply idiom changes:**
- Comprehensions with side effects, nested loops, or >80 chars — keep the loop
- `any()`/`all()` where the loop has early-exit side effects beyond the boolean
- Boolean simplification where `None` vs empty-list distinction matters
- When the verbose form matches surrounding code style in the same method

---

### Phase 3: Verify

After all transforms are applied, run verification in order of increasing scope:

**Step 1 — Syntax check** (already done per-transform):
```bash
python3 -c "import ast; ast.parse(open('<file>').read()); print('OK')"
```

**Step 2 — Targeted test** (run first, fast feedback):
```bash
source .venv/bin/activate
# Find and run the matching test file
pytest tests/test_<module_name>.py -v --tb=short
```

**Step 3 — Full suite** (only after targeted test passes):
```bash
./run_tests.sh
```

If any test fails:
1. Revert the most recent transform
2. Investigate — is the failure from the transform or a pre-existing flake?
3. Re-apply the transform with the fix
4. Re-run the targeted test first, then the full suite

If you cannot run verification (missing venv, broken deps), say so explicitly
and list what the user should run manually.

---

### Phase 4: Summarize

Present a summary tailored to the mode:

**Apply-Changes mode:**
```
Simplification complete for <file>:

  Before: N lines
  After:  M lines (K lines removed, -X%)

  Transforms applied:
    ✓ Extracted 3 methods from _fix_backup_schedule_collision    [safe]
    ✓ Consolidated 2 identical error handlers                    [safe]
    ✓ Replaced 1 manual poll loop with wait_for_condition()      [needs-care]
    ✗ Skipped: no manual dry-run checks found
    ⚠ Deferred: _verify_backup_integrity has complex retry logic [risky — suggest only]

  Tests: All passing (N tests, targeted + full suite)
  Verification gaps: None (or: "no tests cover _poll_backup_completion")
```

**Review-Only mode:**
```
Simplification assessment for <file>:

  Current: N lines, M methods, K methods >80 lines

  Top opportunities (ranked by impact):
    1. [HIGH]   Extract _fix_backup_schedule_collision → 3 methods (-80 lines) [safe]
    2. [HIGH]   Consolidate 3 identical error handlers (-40 lines) [safe]
    3. [MEDIUM] Replace 2 poll loops with wait_for_condition() (-30 lines) [needs-care]
    4. [LOW]    4 Python idiom quick-wins (-8 lines) [safe]

  Intentionally deferred:
    - _verify_backup_integrity: complex retry with partial results, leave as-is
    - Lines 445-450: redundant-looking check may guard against ACM 2.11 quirk

  Estimated total reduction: ~158 lines (-10%)
```

Include any simplification opportunities spotted in **other files** as a brief
note at the end — do not fix them, just mention them for a future pass.

---

## After All Edits

Run `git diff --stat` to show the change summary.

Do NOT commit. Tell the user they can review with `git diff` and commit when ready.

---

## What Good Simplification Looks Like

After applying this skill, the target file should:

- Read top-to-bottom without forcing the reader to hold >3 things in working memory
- Have methods that fit on one screen (~40-60 lines) with clear orchestrator methods
- Use one consistent pattern per concept (waiter, dry-run, error handling)
- Contain no dead paths, unused variables, or redundant indirection
- Be easier to debug during an incident and easier to extend for the next feature
