# Issue #246 Documentation Realignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `CONTRIBUTING.md`, `docs/development/testing.md`, and `docs/development/architecture.md` into exact agreement with current source, CI, and the `AGENTS.md` policy refreshed by #245, locked by guardrail tests.

**Architecture:** Documentation-only changes plus one Python test file. Each task pairs its own guardrail assertions with the document fix that satisfies them, so every task ends green. Cross-document assertions land last as regression locks, because they cannot pass until every document is corrected.

**Tech Stack:** Python 3.10-3.12, pytest, Markdown. No production code changes.

**Spec:** `docs/superpowers/specs/2026-08-11-issue-246-docs-realignment-design.md`

## Global Constraints

- Branch base is `origin/ansible`, not `origin/main`. `ansible` is the primary development branch.
- Formatting policy is 120 characters everywhere (`setup.cfg:12,38`; `black --line-length 120`).
- Never document a formatter command with a bare `.` target — `AGENTS.md:375-376` forbids repo-wide formatting that can walk `.venv/`.
- Never restate compatibility versions. Link `ansible_collections/tomazb/acm_switchover/docs/compatibility.md` (#244's authority).
- Never restate Phase 9 status. `AGENTS.md:525` assigns it to the GitHub issue tracker.
- Every CLI example must use the flag-only form. There is no `switchover` subcommand. `--method` (values `passive` or `full`) **and** `--old-hub-action` (values `secondary`, `decommission`, or `none`) are both required unless `--setup`, `--restore-only`, or `--argocd-resume-only` is used — see `acm_switchover.py:85-89`. An example missing either one fails argument validation and is a defect, not a style issue. Verify examples by running them, not by checking flags against `--help`.
- No real credentials, kubeconfigs, cluster identifiers, or private paths in any example. `_assert_no_real_live_config_literals` (`tests/test_documentation_guardrails.py:52`) already forbids `https://`, `/home/`, `/tmp/`, and `cluster-id` in the lab-controller docs; do not introduce them elsewhere.
- Do **not** modify `AGENTS.md` or `docs/development/ci.md`. Both are explicitly out of scope.
- Do **not** modify any file under `ansible_collections/` in this branch. The two collection attribution fixes ship in a separate pull request (Task 7) so the full collection gate set is paid once, in isolation.
- Commit messages follow conventional commits. No `Co-Authored-By` trailers and no AI attribution.

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `tests/test_documentation_guardrails.py` | Durable assertions for documentation contracts | Modify — add module constants and 10 tests, retarget 1 |
| `CONTRIBUTING.md` | Contributor entry point: branch, gates, ownership routing | Modify — sections replaced |
| `docs/development/testing.md` | The gate inventory: nine verification surfaces | Modify — restructured |
| `docs/development/architecture.md` | Design reference for the Python CLI and collection | Modify — five targeted edits |
| `docs/development/lab-role-controller-spec.md` | Lab controller design | Modify — one dual-citation fix |
| `CHANGELOG.md` | Release notes | Modify — one documentation-governance entry |

---

### Task 1: Contributor guide guardrails and refresh

**Files:**
- Modify: `tests/test_documentation_guardrails.py` (add constants after line 30; add tests after line 96)
- Modify: `CONTRIBUTING.md:27`, `:70-110`, `:166-188`, `:207-245`, `:321-333`
- Test: `tests/test_documentation_guardrails.py`

**Interfaces:**
- Consumes: `_read(path: str) -> str` (`tests/test_documentation_guardrails.py:33`)
- Produces: module constants `CONTRIBUTING_DOC`, `TESTING_DOC`, `ARCHITECTURE_DOC`, `CONTRIBUTOR_DOCS` used by Tasks 2, 3, and 5

- [ ] **Step 1: Add the shared document constants**

Insert after line 30 of `tests/test_documentation_guardrails.py` (after the `LAB_CONTROLLER_SAFETY_DOCS` tuple, before `def _read`):

```python
CONTRIBUTING_DOC = "CONTRIBUTING.md"
TESTING_DOC = "docs/development/testing.md"
ARCHITECTURE_DOC = "docs/development/architecture.md"
LAB_CONTROLLER_SPEC_DOC = "docs/development/lab-role-controller-spec.md"
CONTRIBUTOR_DOCS = (CONTRIBUTING_DOC, TESTING_DOC, ARCHITECTURE_DOC)
```

- [ ] **Step 2: Write the failing contributor guardrails**

Append to `tests/test_documentation_guardrails.py` immediately after `test_contributing_matches_current_dev_workflow` (which ends at line 96):

```python
def test_contributing_line_length_matches_ci():
    """Contributor line-length guidance must match the 120-character CI policy."""
    content = _read(CONTRIBUTING_DOC)

    assert re.search(
        r"[Mm]aximum line length:\s*120", content
    ), "CONTRIBUTING.md must state a 120-character maximum line length"
    assert "100 characters" not in content, "CONTRIBUTING.md still states the obsolete 100-character limit"


def test_contributing_routes_validation_to_modular_owners():
    """Contributor guide must route changes to current owners, not the retired validator class."""
    content = _read(CONTRIBUTING_DOC)

    for token in (
        "lib/validation.py",
        "modules/preflight/",
        "preflight_coordinator",
        "lib/workflow.py",
        "lib/operation_runners.py",
        "tests/release/checks/",
        "tests/release/lab_controller/",
    ):
        assert token in content, f"CONTRIBUTING.md must route work to {token}"

    assert not re.search(
        r"[Aa]dd (?:a )?method to\s+`?PreflightValidator", content
    ), "CONTRIBUTING.md still teaches the obsolete 'add a method to PreflightValidator' recipe"


def test_contributing_names_primary_branch_and_start_gate():
    """Contributor guide must name the development branch and the mandatory reading gate."""
    content = _read(CONTRIBUTING_DOC)

    assert "AGENTS.md" in content, "CONTRIBUTING.md must direct contributors to AGENTS.md"
    assert re.search(
        r"`ansible`[^\n]*(primary|development) branch", content
    ), "CONTRIBUTING.md must identify `ansible` as the primary development branch"
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "contributing_line_length or routes_validation or primary_branch" -v`

Expected: 3 FAILED. `test_contributing_line_length_matches_ci` fails on the missing 120 pattern; `test_contributing_routes_validation_to_modular_owners` fails on missing `lib/validation.py`; `test_contributing_names_primary_branch_and_start_gate` fails on the missing branch sentence.

- [ ] **Step 4: Replace the Getting Started section**

In `CONTRIBUTING.md`, replace lines 5-17 (the `## Getting Started` block through the `.venv` sentence) with:

````markdown
## Getting Started

Before writing anything, read [`AGENTS.md`](AGENTS.md) and the governing issue or spec for
the work. `AGENTS.md` owns the mandatory start gate, the authority hierarchy, the protected-file
policy, and the verification matrix; this guide only covers contributor mechanics.

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/rh-acm-switchover.git`
3. Branch from `ansible`, which is the primary development branch — not `main`:
   ```bash
   git fetch origin ansible
   git checkout -b feature/your-feature-name origin/ansible
   ```
4. Use an isolated branch or git worktree for implementation, so independent validation runs
   against a stable tree.
5. Set up the development environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```

The repository defaults to `.venv`, and `./run_tests.sh` will reuse an active virtualenv when possible.
````

- [ ] **Step 5: Correct the line-length policy**

In `CONTRIBUTING.md`, replace line 27:

````markdown
- Maximum line length: 100 characters
```

with:

```markdown
- Maximum line length: 120 characters — this matches CI. See `setup.cfg` and the `black`
  invocation in `.github/workflows/ci-cd.yml`.
````

- [ ] **Step 6: Replace the validation recipe with an ownership routing table**

In `CONTRIBUTING.md`, replace the whole `### Adding New Validation Checks` section (lines 70-110, from the heading through the closing fence of the `_check_custom_resource` example) with:

```markdown
### Routing a Change to Its Owner

Find the owner before writing code. Editing the wrong layer is the most common source of
review churn.

| Change | Owner |
| --- | --- |
| CLI, input, and path validation | `lib/validation.py` |
| Python preflight checks | `modules/preflight/` plus `modules/preflight_coordinator.py` and `modules/preflight/reporter.py` |
| Python phase behaviour | The owning phase module under `modules/` |
| Python flow, dispatch, and completed/failed-state behaviour | `lib/workflow.py` and `lib/operation_runners.py` |
| Cross-phase run facts | `lib/run_record.py` (the `RunRecord` facade) — never raw state config keys |
| Ansible behaviour | The owning role, module, `module_utils`, or action plugin |
| Release checks | `tests/release/checks/` and the framework contracts |
| Lab-controller safety | `tests/release/lab_controller/` |
| Parity behaviour | Parity fixtures, parity tests, and the parity authority documents |

Preflight checks live in the modular `modules/preflight/` package — `backup_validators.py`,
`cluster_validators.py`, `namespace_validators.py`, and `version_validators.py`, each building on
`base_validator.py`. Add a check to the module matching its subject, and let
`modules/preflight_coordinator.py` orchestrate it and `modules/preflight/reporter.py` render it.
```

- [ ] **Step 7: Run the three tests to verify they pass**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "contributing_line_length or routes_validation or primary_branch" -v`

Expected: 3 PASSED.

- [ ] **Step 8: Replace the dry-run section with the public contract**

In `CONTRIBUTING.md`, replace the whole `### Dry-Run Support` section (lines 166-188) with:

````markdown
### Dry-Run and Check-Mode Behaviour

Dry-run is a property of the client layer, not something each call site re-implements.
`lib/kube_client.py` honours dry-run centrally, dry-run orchestration captures and restores a
full `StateManager` snapshot after the run, and paths that cannot prove safety fail closed.

Route mutations through `KubeClient` so this behaviour applies:

```python
# Good - dry-run, retry, and state-snapshot behaviour all apply
self.client.patch_custom_resource(...)

# Bad - bypasses the client contract entirely
self.custom_api.patch_namespaced_custom_object(...)
```

Do not add local `if self.dry_run: return {}` guards to new call sites. A hand-rolled guard
returns a fabricated result that later phases may treat as a real observation, which is exactly
the failure the central contract prevents. If a genuinely new operation needs dry-run support,
add it to `KubeClient` alongside the existing operations so every caller inherits it.

A dry-run or check-mode pass proves that the planned actions parse and that validation accepts
the inputs. It is not evidence of live behaviour and never substitutes for certification
evidence.
````

- [ ] **Step 9: Correct the testing section's CLI examples**

In `CONTRIBUTING.md`, replace steps 3 and 4 of the `### Testing` section (lines 221-233) with:

````markdown
3. **Test dry-run mode.** Both `--method` and `--old-hub-action` are required unless using
   `--setup`, `--restore-only`, or `--argocd-resume-only` (`acm_switchover.py:85-89`):
   ```bash
   python acm_switchover.py --dry-run \
     --primary-context test-primary \
     --secondary-context test-secondary \
     --method passive \
     --old-hub-action secondary
   ```

4. **Test validate-only:**
   ```bash
   python acm_switchover.py --validate-only \
     --primary-context test-primary \
     --secondary-context test-secondary \
     --method passive \
     --old-hub-action secondary
   ```
````

Then replace step 5 (lines 235-238) with:

````markdown
5. **Run collection tests when touching the collection.** `PYTHONPATH=.` is part of the
   command — without it the collection imports fail before any test runs:
   ```bash
   PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
   ```

   Collection unit tests are one surface of several. See
   [the testing guide](docs/development/testing.md) for the full gate inventory and for which
   surfaces your change requires.
````

- [ ] **Step 10: Remove the feature-ideas list**

In `CONTRIBUTING.md`, replace the whole `## Feature Ideas` section (lines 321-333) with:

```markdown
## Finding Work

Work starts from a governing issue or spec, so browse the
[issue tracker](https://github.com/tomazb/rh-acm-switchover/issues) rather than an inline
wishlist. Open an issue first if what you want to build does not have one.
```

- [ ] **Step 11: Verify the CLI examples against real argument validation**

Checking that each flag *appears* in `--help` is not enough: it cannot detect a missing
required argument, which is exactly how the first draft of this plan shipped two broken
examples. `--help` also exits before argparse validates anything. The only check that works is
to actually run each documented example and confirm it gets **past** argument parsing.

Extract every `acm_switchover.py` example from the document and run it:

```bash
python - <<'PY'
import pathlib, re, shlex, subprocess, sys

DOC = "CONTRIBUTING.md"
EXPECTED = 2  # --dry-run and --validate-only; raise this when you add an example

doc = pathlib.Path(DOC).read_text(encoding="utf-8")
# ^[ \t]* is load-bearing: examples are indented inside numbered lists and fenced blocks.
# Anchoring to column 0 silently matches nothing and reports a false pass.
examples = re.findall(
    r"^[ \t]*(python acm_switchover\.py(?:[^\n`]*\\\n)*[^\n`]*)$", doc, re.MULTILINE
)

if len(examples) != EXPECTED:
    sys.exit(f"FAIL: expected {EXPECTED} example(s) in {DOC}, matched {len(examples)}. "
             "Fix the pattern or update EXPECTED — a check that matches nothing proves nothing.")

failures = []
for ex in examples:
    proc = subprocess.run(shlex.split(ex.replace("\\\n", " ")), capture_output=True, text=True)
    combined = proc.stdout + proc.stderr
    # argparse signals a usage error with exit code 2 and an "error:" banner.
    if proc.returncode == 2 and "error:" in combined:
        failures.append((" ".join(ex.split()), combined.strip().splitlines()[-1]))

print(f"checked {len(examples)} example(s)")
for ex, err in failures:
    print("BROKEN:", ex, "->", err)
if failures:
    sys.exit("FAIL: examples above do not pass argument validation")
print("result: all examples pass argument validation")
PY
```

Expected: `checked 2 example(s)` followed by `all examples pass argument validation`. Examples
are never run against a real cluster — an example that reaches a kubeconfig or connection error
has already proven that argparse accepted it, which is all this check asserts.

The `EXPECTED` guard exists because the first two drafts of this check both matched zero
examples and reported success. A verifier whose empty case is indistinguishable from its
success case is decoration, not verification.

If an example is reported BROKEN, add the missing required argument rather than deleting the
example. `--method` and `--old-hub-action` are both required unless `--setup`,
`--restore-only`, or `--argocd-resume-only` is used; the CLI's own epilog
(`acm_switchover.py:100-110`) shows the canonical forms.

- [ ] **Step 12: Run the full guardrail suite**

Run: `python -m pytest tests/test_documentation_guardrails.py -q`

Expected: all tests PASS, including the pre-existing `test_contributing_matches_current_dev_workflow`, which requires `.venv`, `requirements-dev.txt`, `./run_tests.sh`, and `CHANGELOG.md` to remain present in `CONTRIBUTING.md`. If it fails, one of those four tokens was removed by the edits above — restore it rather than weakening the test.

- [ ] **Step 13: Commit**

```bash
git add tests/test_documentation_guardrails.py CONTRIBUTING.md
git commit -m "docs(contributing): route work to current owners and gates

Correct the line-length policy to the 120-character CI value, replace the
retired PreflightValidator recipe with an ownership routing table, describe
dry-run as the central KubeClient contract rather than a per-call-site recipe,
fix CLI examples that omitted the required --method argument, and name ansible
as the primary development branch.

Guarded by three new documentation tests.

Refs #246"
```

---

### Task 2: Testing guide guardrails and nine-surface taxonomy

**Files:**
- Modify: `tests/test_documentation_guardrails.py` (append tests)
- Modify: `docs/development/testing.md:7-12`, `:21`, `:190-194`, `:232-254`, `:382-406`, `:480`
- Test: `tests/test_documentation_guardrails.py`

**Interfaces:**
- Consumes: `_read`, `TESTING_DOC` (Task 1)
- Produces: none consumed by later tasks

- [ ] **Step 1: Write the failing testing-guide guardrails**

Append to `tests/test_documentation_guardrails.py`:

```python
COLLECTION_VERIFICATION_TOKENS = (
    "ansible_collections/tomazb/acm_switchover/tests/unit/",
    "ansible_collections/tomazb/acm_switchover/tests/integration/",
    "ansible_collections/tomazb/acm_switchover/tests/scenario/",
    "--syntax-check",
    "ansible-galaxy collection build",
    "tests/e2e",
    "tests/release",
    "certification",
)


def test_testing_guide_covers_every_collection_verification_surface():
    """The gate inventory must name every maintained verification surface separately."""
    content = _read(TESTING_DOC)

    for token in COLLECTION_VERIFICATION_TOKENS:
        assert token in content, f"testing.md must document the verification surface using {token}"


def test_testing_guide_states_run_tests_is_not_complete():
    """The runner must not be presented as the complete verification surface."""
    content = _read(TESTING_DOC)

    assert "./run_tests.sh" in content
    assert (
        "is not a complete verification surface" in content
    ), "testing.md must state that ./run_tests.sh is not a complete verification surface"


def test_testing_guide_links_compatibility_authority():
    """Compatibility facts must be linked to their authority, never restated."""
    content = _read(TESTING_DOC)

    assert (
        "ansible_collections/tomazb/acm_switchover/docs/compatibility.md" in content
    ), "testing.md must link the compatibility authority"
    assert not re.search(
        r"ansible-core\s*==", content
    ), "testing.md must not pin ansible-core versions; link the compatibility authority instead"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "collection_verification_surface or run_tests_is_not_complete or links_compatibility" -v`

Expected: 3 FAILED. The first fails on `ansible_collections/tomazb/acm_switchover/tests/integration/`; the second on the missing sentence; the third on the missing compatibility link.

- [ ] **Step 3: Replace the surface overview with the nine-surface taxonomy**

In `docs/development/testing.md`, replace lines 7-12 (the "four distinct verification surfaces" list) with:

````markdown
This document is the gate inventory for the repository. It defines every maintained
verification surface, the exact command that runs it, and — as importantly — what each surface
does not prove.

`AGENTS.md` owns the policy for which gates a change must run; see its
[Verification Matrix by Changed Surface](../../AGENTS.md#verification-matrix-by-changed-surface).
This document owns the commands.

### The nine verification surfaces

| # | Surface | Nature | What it does not prove |
| --- | --- | --- | --- |
| 1 | Root Python and Bash tests | Local, fake-backed | Nothing about the collection, and nothing about live clusters |
| 2 | Release-framework helpers (non-live) | Local, fake-backed | Not certification evidence |
| 3 | Collection unit tests | Local | Nothing about playbook wiring or cross-role behaviour |
| 4 | Collection integration tests | Local, fake-backed | Nothing about real cluster responses |
| 5 | Collection scenario tests | Local, fake-backed | Nothing about live timing or partial failure |
| 6 | Playbook syntax check | Local | Only that playbooks parse and resolve — no behaviour at all |
| 7 | Collection archive build | Local | Only that the archive builds — not that it works |
| 8 | On-demand E2E | Live, real hubs | Not certification evidence unless run under a release profile |
| 9 | Controller-gated live release evidence | Live, certification-eligible | Bounded by the profile and controller decisions |

Surfaces 1 through 7 are entirely local and fake-backed or static. None of them is live
evidence. Fake, dry-run, static-fixture, and local-harness results never substitute for live
certification evidence — see the
[Release-Validation and Lab-Controller Authority Boundary](../../AGENTS.md#release-validation-and-lab-controller-authority-boundary).

### Commands by surface

Surfaces 3 through 7 take their commands from
`.github/workflows/ansible-collection-foundation.yml`, which is ground truth. `PYTHONPATH=.` is
part of each collection pytest command — without it, collection imports fail before any test
runs.

```bash
# 1. Root Python and Bash tests
python -m pytest tests/ --ignore=tests/release -v -m "not e2e"

# 2. Release-framework helper tests (non-live)
python -m pytest tests/release -q

# 3. Collection unit tests
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q

# 4. Collection integration tests
# CI exports ANSIBLE_COLLECTIONS_PATH before this step; `$(pwd)` is the local equivalent of
# ${GITHUB_WORKSPACE}. Surface 3 above deliberately has no export, matching CI.
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q

# 5. Collection scenario tests
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q

# 6. Playbook syntax check
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
for playbook in ansible_collections/tomazb/acm_switchover/playbooks/*.yml; do
  ansible-playbook "${playbook}" --syntax-check
done

# 7. Collection archive build
ansible-galaxy collection build --output-path /tmp/dist \
  ansible_collections/tomazb/acm_switchover
```

Surfaces 8 and 9 are covered under [E2E Tests](#e2e-tests-on-demand) and
[Release Validation Framework](#release-validation-framework) below.

CI runs surfaces 3 through 7 across two `ansible-core` lanes: the declared floor and the newest
tested series. The supported versions are defined by
[the compatibility authority](../../ansible_collections/tomazb/acm_switchover/docs/compatibility.md)
and are deliberately not restated here.

### Three levels of confidence

1. **Targeted development loop** — the single test or module you are changing. Fast, and proves
   only what it covers.
2. **Complete relevant gate set** — every surface your change invalidates, per the `AGENTS.md`
   verification matrix. Complete this before terminal validation, so the frozen head is
   validated once.
3. **Exact-head hosted CI** — mandatory for merge readiness regardless of local results.
````

- [ ] **Step 4: Correct the test-structure tree**

In `docs/development/testing.md`, replace line 21:

```
├── test_preflight.py         # Tests for modules/preflight.py
```

with:

```
├── test_preflight.py         # Tests for the modules/preflight/ package
```

- [ ] **Step 5: Add the run_tests.sh non-completeness statement**

In `docs/development/testing.md`, immediately after the sentence ending "...excludes long-running E2E tests (marked `@pytest.mark.e2e`)." (line 44), insert:

```markdown

`./run_tests.sh` covers surfaces 1 and 2 only. It never runs collection unit, integration,
scenario, syntax, or build gates, so it is not a complete verification surface for any change
that touches `ansible_collections/`.
```

- [ ] **Step 6: Label the historical lab observations**

In `docs/development/testing.md`, replace lines 182-194 (the `### Real-Cluster Validation (Example)` block) with:

````markdown
### Historical observations

The following is a recorded observation from a specific lab on a specific date. It is
**not** current support evidence, not a compatibility claim, and not a guarantee about any
other environment. Current supported versions are defined by
[the compatibility authority](../../ansible_collections/tomazb/acm_switchover/docs/compatibility.md).

Example real-cluster validation using the discovery and preflight scripts:

```bash
./scripts/discover-hub.sh --auto --run
```

Observed on 2026-01-28:
- Hubs detected: `mgmt1` (primary) and `mgmt2` (secondary)
- ACM: 2.14.1 on both hubs
- OCP: 4.19.21 on both hubs
- Preflight: **38 checks passed, 0 warnings**
````

- [ ] **Step 7: Run the three tests to verify they pass**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "collection_verification_surface or run_tests_is_not_complete or links_compatibility" -v`

Expected: 3 PASSED.

- [ ] **Step 8: Replace the formatter commands with CI's exact invocation**

In `docs/development/testing.md`, replace the `### Black (Formatting)` and `### isort (Import Sorting)` sections (lines 232-254) with:

````markdown
### Black (Formatting)

Reproduce CI exactly. The path list below is copied from the `lint` job in
`.github/workflows/ci-cd.yml` — do not substitute `.`, which walks `.venv/` and generated
trees, and do not rely on an editor auto-format hook, which only touches files edited in your
session.

Check formatting:
```bash
black --check --line-length 120 --diff acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
```

Auto-format (same paths, without `--check --diff`):
```bash
black --line-length 120 acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
```

### isort (Import Sorting)

Check imports:
```bash
isort --check-only --profile black --line-length 120 acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
```

Auto-sort (same paths, without `--check-only`):
```bash
isort --profile black --line-length 120 acm_switchover.py lib modules \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests tests
```

CI does not currently format `check_rbac.py` or `show_state.py`. This documents what CI does,
not an idealised superset: a scoped command that merely looks plausible fails differently from
CI, which is worse than no command at all.
````

- [ ] **Step 9: Correct the manual integration testing examples**

In `docs/development/testing.md`, replace the `### Dry-Run Testing` and `### Validate-Only Mode` command blocks (lines 388-406) with:

````markdown
### Dry-Run Testing

Test against real clusters without making changes. The CLI is flag-only — there is no
`switchover` subcommand:

```bash
python acm_switchover.py \
  --primary-context prod-hub \
  --secondary-context dr-hub \
  --method passive \
  --old-hub-action secondary \
  --dry-run
```

### Validate-Only Mode

Run pre-flight checks only:

```bash
python acm_switchover.py \
  --primary-context prod-hub \
  --secondary-context dr-hub \
  --method passive \
  --old-hub-action secondary \
  --validate-only
```

Both modes prove that inputs validate and that the planned actions resolve. Neither is live
evidence, and neither is certification evidence.
````

- [ ] **Step 10: Refresh the footer date**

In `docs/development/testing.md`, replace line 480:

````markdown
**Last Updated**: November 18, 2025
```

with:

```markdown
**Last Updated**: 2026-08-12
````

- [ ] **Step 11: Run the full guardrail suite and check whitespace**

Run:

```bash
python -m pytest tests/test_documentation_guardrails.py -q
git diff --check
```

Expected: all tests PASS; `git diff --check` produces no output.

- [ ] **Step 12: Commit**

```bash
git add tests/test_documentation_guardrails.py docs/development/testing.md
git commit -m "docs(testing): define the nine verification surfaces

Replace the four-surface framing with the nine maintained surfaces, each with
its exact command, nature, and what it does not prove. Take collection commands
from the foundation workflow, including the load-bearing PYTHONPATH prefix.

Replace repo-wide black/isort invocations with CI's exact path list, correct
two CLI examples that used a subcommand and a method value that do not exist,
label the 2026-01-28 lab observations as historical, and link the compatibility
authority instead of restating versions.

Refs #246"
```

---

### Task 3: Architecture guide guardrails and extraction refresh

**Files:**
- Modify: `tests/test_documentation_guardrails.py` (append tests)
- Modify: `docs/development/architecture.md:3`, `:171-183`, `:185-196`, `:378`, and append a new section before `## Known Constraints`
- Test: `tests/test_documentation_guardrails.py`

**Interfaces:**
- Consumes: `_read`, `ARCHITECTURE_DOC` (Task 1)
- Produces: none consumed by later tasks

- [ ] **Step 1: Write the failing architecture guardrails**

Append to `tests/test_documentation_guardrails.py`:

```python
def test_architecture_names_workflow_and_runner_extraction():
    """Architecture prose must describe the extracted flow, runner, and run-record layers."""
    content = _read(ARCHITECTURE_DOC)

    for token in (
        "run_phase_flow",
        "handle_completed_state",
        "execute_operation",
        "OperationDispatchHooks",
    ):
        assert token in content, f"architecture.md must describe {token} in prose"

    for path in ("lib/workflow.py", "lib/operation_runners.py", "lib/run_record.py"):
        assert content.count(path) >= 2, f"architecture.md mentions {path} only in the file inventory, not in prose"


def test_architecture_uses_run_record_vocabulary():
    """Architecture must use RunRecord vocabulary, not the config wording CONTEXT.md forbids."""
    content = _read(ARCHITECTURE_DOC)

    assert "RunRecord" in content, "architecture.md must name the RunRecord facade"
    assert (
        "config discovered during execution" not in content
    ), "architecture.md uses state-config wording that CONTEXT.md lists under Avoid"


def test_architecture_links_authorities_without_restating_status():
    """Architecture must link authority documents and must not carry volatile status or a version."""
    content = _read(ARCHITECTURE_DOC)

    for token in ("release-validation-framework.md", "lab-role-controller-spec.md"):
        assert token in content, f"architecture.md must link {token}"

    assert not re.search(
        r"^\*\*Version\*\*:", content, re.MULTILINE
    ), "architecture.md must not carry a document version that reads as a product release"
    assert not re.search(
        r"Phase 9[A-Z]?\s+(is|remains|has|was)\b", content
    ), "architecture.md must not restate Phase 9 status; the issue tracker owns it"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "architecture_names_workflow or architecture_uses_run_record or architecture_links_authorities" -v`

Expected: 3 FAILED. The first fails on `run_phase_flow`; the second on the `config discovered during execution` string at line 192; the third on the `**Version**: 1.6.3` line at line 3.

- [ ] **Step 3: Remove the misleading document version**

In `docs/development/architecture.md`, replace lines 3-4:

````markdown
**Version**: 1.6.3  
**Last Updated**: 2026-04-10
```

with:

```markdown
**Last Updated**: 2026-08-12
````

- [ ] **Step 4: Correct the entrypoint description and add the extracted layers**

In `docs/development/architecture.md`, replace the `### acm_switchover.py` section (lines 171-183) with:

```markdown
### `acm_switchover.py`

The entrypoint owns:

- CLI argument parsing
- logger setup
- runtime bootstrap (client, state-file, and state-directory resolution)
- dispatch into the operation runners

It is deliberately thin. Cross-mode branching and phase orchestration were extracted into
`lib/operation_runners.py` and `lib/workflow.py`; phase modules own resource-specific behaviour.

### `lib/operation_runners.py`

Owns operation dispatch and the two runner implementations:

- `execute_operation` — the shared dispatch path
- `run_switchover_impl` — the standard switchover operation
- `run_restore_only_impl` — the single-hub restore-only operation

The seam between dispatch and each operation is a set of hook dataclasses —
`OperationDispatchHooks`, `SwitchoverRunnerHooks`, and `RestoreOnlyRunnerHooks` — so the runners
can be exercised without a live client.

### `lib/workflow.py`

Owns phase-flow execution and state-driven entry decisions:

- `run_phase_flow` — drives the ordered phase handlers
- `handle_completed_state` — handles reruns against a recently completed state
- `handle_failed_state` — prepares a failed state for retry, or exits when the retry phase is unknown
- `run_validate_only_preflight` — the validate-only path

`CompletedStateConfig`, `FailedStateConfig`, and `CompletionLogConfig` carry the parameters for
these decisions, keeping the banners and exit behaviour consistent across operations.
```

- [ ] **Step 5: Correct the StateManager description to RunRecord vocabulary**

In `docs/development/architecture.md`, within the `### lib/utils.py` section, replace the `StateManager` persistence bullet list (lines 190-196, beginning "`StateManager` is the backbone for resumability. It persists:") with:

```markdown
`StateManager` is the backbone for resumability. It owns the durable file and persists:

- current phase
- completed steps
- cross-phase run facts, reached only through the `RunRecord` facade (see below)
- Argo CD pause metadata
- error history

### `lib/run_record.py`

`RunRecord` is the facade for cross-phase run facts — what preflight discovered, and what each
phase recorded for later phases or reports. It exposes only named, typed operations
(`HubFacts`, `ManagedClusterExpectation`, `StepRecord`, `ErrorRecord`, `RunSummary`).

The split matters: the durable file behind the run belongs to `StateManager`, but the key
vocabulary belongs to `RunRecord` alone. Reading or writing the underlying persisted key
literals outside the facade is a contract violation — see the Run record entry in
[`CONTEXT.md`](../../CONTEXT.md).
```

- [ ] **Step 6: Name RunRecord as the access path in the State Model**

In `docs/development/architecture.md`, replace the State Model bullet at line 378:

````markdown
- detected config such as ACM version and observability presence
```

with:

```markdown
- detected run facts such as ACM version and observability presence, read and written through
  the `RunRecord` facade (`lib/run_record.py`) rather than as raw persisted keys
````

- [ ] **Step 7: Add the authority boundary section**

In `docs/development/architecture.md`, insert immediately before the `## Known Constraints` heading (line 446):

```markdown
## Release Validation and Lab-Controller Boundary

Release validation lives under `tests/release/`. The live lab controller is a separate
authority with its own safety invariants. This document does not restate either — it points at
the owners, because copied invariants and copied status both go stale silently.

- Policy and durable invariants:
  [Release-Validation and Lab-Controller Authority Boundary](../../AGENTS.md#release-validation-and-lab-controller-authority-boundary)
- Framework contract: [Release validation framework](release-validation-framework.md)
- Controller design: [Lab role controller spec](lab-role-controller-spec.md)
- Non-live orchestration guidance: [Lab role controller agent instructions](lab-role-controller-agent-instructions.md)

Current phase status is owned by the GitHub issue tracker, not by this document.

```

- [ ] **Step 8: Run the three tests to verify they pass**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "architecture_names_workflow or architecture_uses_run_record or architecture_links_authorities" -v`

Expected: 3 PASSED.

- [ ] **Step 9: Verify the described symbols actually exist**

Run:

```bash
grep -n "def run_phase_flow\|def handle_completed_state\|def handle_failed_state\|def run_validate_only_preflight\|class CompletedStateConfig\|class FailedStateConfig\|class CompletionLogConfig" lib/workflow.py
grep -n "def execute_operation\|def run_switchover_impl\|def run_restore_only_impl\|class OperationDispatchHooks\|class SwitchoverRunnerHooks\|class RestoreOnlyRunnerHooks" lib/operation_runners.py
grep -n "class HubFacts\|class ManagedClusterExpectation\|class StepRecord\|class ErrorRecord\|class RunSummary\|class RunRecord" lib/run_record.py
```

Expected: every symbol named in the new prose appears. If any is missing, correct the prose to match source — source wins.

- [ ] **Step 10: Run the full guardrail suite**

Run: `python -m pytest tests/test_documentation_guardrails.py -q`

Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add tests/test_documentation_guardrails.py docs/development/architecture.md
git commit -m "docs(architecture): reflect workflow, runner, and run-record ownership

The file inventory was refreshed after the flow and runner extraction but the
prose was not: the entrypoint was still credited with cross-mode branching and
phase orchestration, and StateManager was described as persisting config, which
CONTEXT.md lists under Avoid.

Describe lib/operation_runners.py and lib/workflow.py, give the RunRecord facade
prose and name it as the access path for run facts, add a links-only release
validation and lab-controller boundary section, and drop the document version
that read as a product release.

Refs #246"
```

---

### Task 4: Repoint the lab-controller spec attribution

**Files:**
- Modify: `tests/test_documentation_guardrails.py` (append one test)
- Modify: `docs/development/lab-role-controller-spec.md:242-243`
- Test: `tests/test_documentation_guardrails.py`

**Interfaces:**
- Consumes: `_read`, `LAB_CONTROLLER_SPEC_DOC` (Task 1)
- Produces: none

- [ ] **Step 1: Write the failing attribution guardrail**

Append to `tests/test_documentation_guardrails.py`:

```python
def test_lab_role_controller_spec_attributes_uid_binding_to_owning_authority():
    """Cluster-UID binding must be attributed to its owning authorities, not to AGENTS.md."""
    content = _read(LAB_CONTROLLER_SPEC_DOC)

    assert (
        "records hub identities by" in content
    ), "lab-role-controller-spec.md must still describe cluster-UID identity recording"
    assert not re.search(
        r"records hub identities by[^.]*`AGENTS\.md`", content
    ), "cluster-UID binding must cite docs/operations/usage.md and architecture.md, not AGENTS.md"
    assert "docs/operations/usage.md" in content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "attributes_uid_binding" -v`

Expected: FAILED on the `AGENTS.md` citation.

- [ ] **Step 3: Remove the AGENTS.md half of the dual citation**

In `docs/development/lab-role-controller-spec.md`, replace:

````markdown
This should reuse existing identity ideas where possible. The Python CLI already records hub identities by
cluster UID in state, as described in `AGENTS.md` and `docs/operations/usage.md`. The release framework
```

with:

```markdown
This should reuse existing identity ideas where possible. The Python CLI already records hub identities by
cluster UID in state, as described in `docs/operations/usage.md` ("Hub identity binding on resume") and the
State Model in `docs/development/architecture.md`. The release framework
````

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "attributes_uid_binding" -v`

Expected: PASSED.

- [ ] **Step 5: Verify the referenced target still exists**

Run: `grep -n "Hub identity binding on resume" docs/operations/usage.md`

Expected: one match. If absent, find the current heading and cite that instead.

- [ ] **Step 6: Commit**

```bash
git add tests/test_documentation_guardrails.py docs/development/lab-role-controller-spec.md
git commit -m "docs(lab-controller): attribute UID binding to its owning authorities

The #245 refresh moved cluster-UID binding detail out of AGENTS.md into the
usage and architecture documents, leaving a stale half of a dual citation here.

Refs #246"
```

---

### Task 5: Cross-document regression locks

These assertions span all three documents, so they cannot pass until Tasks 1 through 3 are complete. They are regression locks rather than drivers: their job is to make the next drift a CI failure.

**Files:**
- Modify: `tests/test_documentation_guardrails.py` (append tests)
- Test: `tests/test_documentation_guardrails.py`

**Interfaces:**
- Consumes: `_read`, `CONTRIBUTOR_DOCS`, `CONTRIBUTING_DOC`, `TESTING_DOC` (Task 1)
- Produces: none

- [ ] **Step 1: Write the cross-document guardrails**

Append to `tests/test_documentation_guardrails.py`:

```python
OBSOLETE_CLI_PATTERNS = (
    (re.compile(r"acm_switchover\.py\s+switchover"), "the obsolete `switchover` subcommand"),
    (re.compile(r"passive-sync"), "the obsolete `passive-sync` method value"),
)

BARE_DOT_FORMATTER = re.compile(r"^\s*(?:\$\s*)?(?:black|isort)\b[^\n]*\s\.\s*$", re.MULTILINE)


def test_active_docs_avoid_obsolete_cli_shapes():
    """Contributor-facing docs must not show CLI shapes the parser rejects."""
    for doc in CONTRIBUTOR_DOCS:
        content = _read(doc)
        for pattern, label in OBSOLETE_CLI_PATTERNS:
            match = pattern.search(content)
            assert match is None, f"{doc} still documents {label}: {match.group(0)!r}"


def test_formatter_guidance_avoids_repo_wide_traversal():
    """Documented formatter commands must not target the repository root."""
    for doc in (CONTRIBUTING_DOC, TESTING_DOC):
        content = _read(doc)
        match = BARE_DOT_FORMATTER.search(content)
        assert match is None, f"{doc} documents repo-wide formatting that can walk .venv/: {match.group(0).strip()!r}"
```

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "obsolete_cli_shapes or formatter_guidance" -v`

Expected: 2 PASSED, because Tasks 1 through 3 already removed every offending shape. If either fails, a stale shape survives — fix the document, not the test.

- [ ] **Step 3: Prove the locks actually bite**

Temporarily append a violating line to the end of `CONTRIBUTING.md`:

```bash
printf '\nblack --line-length 120 .\n' >> CONTRIBUTING.md
python -m pytest tests/test_documentation_guardrails.py -k "formatter_guidance" -v
```

Expected: FAILED, naming `CONTRIBUTING.md`. Then revert:

```bash
git checkout -- CONTRIBUTING.md
python -m pytest tests/test_documentation_guardrails.py -k "formatter_guidance" -v
```

Expected: PASSED. A guardrail that cannot fail is not a guardrail.

- [ ] **Step 4: Confirm the multi-line CI commands do not false-fire**

Run: `python -m pytest tests/test_documentation_guardrails.py -k "formatter_guidance" -v`

Expected: PASSED. The CI commands added in Task 2 end lines with a backslash continuation or a path, never a bare `.`, so `BARE_DOT_FORMATTER` does not match them.

- [ ] **Step 5: Commit**

```bash
git add tests/test_documentation_guardrails.py
git commit -m "test(docs): lock out obsolete CLI shapes and repo-wide formatting

Regression locks spanning CONTRIBUTING.md, the testing guide, and the
architecture guide. Both shapes were live in the tree before this issue: a
switchover subcommand that never existed, and formatter commands that walk
.venv/ contrary to AGENTS.md.

Refs #246"
```

---

### Task 6: Changelog, full verification, and pull request

**Files:**
- Modify: `CHANGELOG.md` (under `[Unreleased]`, `Changed` group)

**Interfaces:**
- Consumes: everything from Tasks 1 through 5
- Produces: the documentation pull request

- [ ] **Step 1: Add the changelog entry**

Inspect the existing `[Unreleased]` groups first:

```bash
sed -n '1,40p' CHANGELOG.md
```

Add one line under the existing `### Changed` group (create it only if absent, keeping the group order the existing `test_changelog_unreleased_keeps_standard_groups` guardrail expects):

```markdown
- Realigned contributor, testing, and architecture documentation with current source, CI, and
  `AGENTS.md` policy, and locked the corrected contracts with documentation guardrail tests (#246).
```

- [ ] **Step 2: Run the complete relevant gate set**

Run each and confirm before moving on:

```bash
python -m pytest tests/test_documentation_guardrails.py -q
python -m pytest tests/test_ci_guardrails.py -q
python -m pytest tests/ --ignore=tests/release -q -m "not e2e"
black --check --line-length 120 --diff tests/test_documentation_guardrails.py
isort --check-only --profile black --line-length 120 tests/test_documentation_guardrails.py
git diff --check
```

Expected: all PASS, no formatting diff, no whitespace errors.

The changed surface is documentation and process plus one Python test file, so the root lane
and formatter gates apply. Collection gates are deliberately **not** run here — no file under
`ansible_collections/` is touched on this branch.

- [ ] **Step 3: Verify every link added by this work resolves**

Run:

```bash
python - <<'PY'
import re, pathlib
root = pathlib.Path(".")
docs = ["CONTRIBUTING.md", "docs/development/testing.md",
        "docs/development/architecture.md", "docs/development/lab-role-controller-spec.md"]
bad = []
for d in docs:
    text = (root / d).read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)#][^)]*?)(?:#[^)]*)?\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (root / d).parent.joinpath(target).resolve().exists():
            bad.append(f"{d} -> {target}")
print("\n".join(bad) if bad else "all relative links resolve")
PY
```

Expected: `all relative links resolve`. Fix any listed path before continuing.

- [ ] **Step 4: Commit the changelog**

```bash
git add CHANGELOG.md
git commit -m "docs: record the documentation realignment in the changelog

Refs #246"
```

- [ ] **Step 5: Push and open the draft pull request**

```bash
git push -u origin HEAD
gh pr create --draft --base ansible \
  --title "docs: realign contributor, testing, and architecture guidance (#246)" \
  --body "$(cat <<'BODY'
Closes #246 (documentation portion).

## What changed

- `CONTRIBUTING.md` — 120-character policy, ownership routing table replacing the retired
  `PreflightValidator` recipe, dry-run described as the central `KubeClient` contract, CLI
  examples that actually pass argument validation, `ansible` named as the primary branch.
- `docs/development/testing.md` — nine verification surfaces with exact commands, natures, and
  what each does not prove; CI's exact formatter path list replacing repo-wide `black .`;
  compatibility linked rather than restated; lab observations labelled historical.
- `docs/development/architecture.md` — prose now reflects the flow/runner extraction and the
  `RunRecord` facade, adds a links-only authority boundary section, drops the misleading
  document version.
- `docs/development/lab-role-controller-spec.md` — one stale `AGENTS.md` citation removed.
- `tests/test_documentation_guardrails.py` — 12 tests locking these contracts.

## Notable findings

- The testing guide documented `python acm_switchover.py switchover …`. That subcommand has
  never existed; the CLI is flag-only. `--method passive-sync` was likewise invalid.
- `CONTRIBUTING.md` dry-run and validate-only examples omitted the required `--method`, so
  they failed argument validation as written.
- `architecture.md` described `StateManager` as persisting "config discovered during
  execution", which `CONTEXT.md` lists under **_Avoid_**.

## Out of scope, raised separately

- `AGENTS.md:341-344` names `docs/development/ci.md` as an authoritative gate inventory, but
  `ci.md` is a registry-secrets and container-build guide with zero test gates. This PR makes
  `testing.md` the gate inventory and raises the stale pointer as a follow-up rather than
  editing an out-of-scope file.
- Two collection files still attribute content to `AGENTS.md`. They ship in a separate PR so
  the full collection gate set is paid once, in isolation.

## Verification

```
python -m pytest tests/test_documentation_guardrails.py -q
python -m pytest tests/test_ci_guardrails.py -q
python -m pytest tests/ --ignore=tests/release -q -m "not e2e"
black --check --line-length 120 --diff tests/test_documentation_guardrails.py
isort --check-only --profile black --line-length 120 tests/test_documentation_guardrails.py
git diff --check
```

Collection gates intentionally not run: no file under `ansible_collections/` is touched.
BODY
)"
```

---

### Task 7: Collection attribution pull request (separate branch)

This task must **not** be performed on the documentation branch. It exists as a separate branch and pull request so the full collection gate set is paid once, in isolation, where a failure is unambiguous.

**Files:**
- Modify: `ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/check_auto_import_orphan.yml:16`
- Modify: `ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_auto_import_orphan.py:47`

**Interfaces:**
- Consumes: nothing
- Produces: nothing

- [ ] **Step 1: Create a branch from `origin/ansible`**

Do this in a **separate** worktree or in the main checkout. Running `git checkout -b` inside the
documentation worktree would switch that worktree off the documentation branch and strand the
work from Tasks 1 through 6.

```bash
git fetch origin ansible
git worktree add ../acm-collection-attribution -b docs/issue-246-collection-attribution origin/ansible
cd ../acm-collection-attribution
```

- [ ] **Step 2: Inspect both current claims**

```bash
sed -n '12,20p' ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/check_auto_import_orphan.yml
sed -n '43,51p' ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_auto_import_orphan.py
```

Expected: both state that `restore_only` is the documented secondary-only flow, citing `AGENTS.md`.

- [ ] **Step 3: Repoint both citations**

In each file, replace the `AGENTS.md` attribution with the owning authorities, preserving the surrounding wording and the file's comment or docstring style:

- `AGENTS.md` becomes `docs/operations/usage.md and docs/development/architecture.md`

Do not reword the surrounding claim; only the attribution changes.

- [ ] **Step 4: Run the full collection gate set**

```bash
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
for playbook in ansible_collections/tomazb/acm_switchover/playbooks/*.yml; do
  ansible-playbook "${playbook}" --syntax-check
done
ansible-galaxy collection build --output-path /tmp/dist ansible_collections/tomazb/acm_switchover
```

Expected: all pass. These gates are required by `AGENTS.md:350` for any collection change, including a comment-only one.

- [ ] **Step 5: Commit and open the pull request**

```bash
git add ansible_collections/tomazb/acm_switchover/roles/preflight/tasks/check_auto_import_orphan.yml \
        ansible_collections/tomazb/acm_switchover/tests/unit/test_preflight_auto_import_orphan.py
git commit -m "docs(collection): attribute restore_only flow to its owning authorities

The #245 refresh moved this detail out of AGENTS.md into the usage and
architecture documents. Comment-only change, carried separately so the full
collection gate set is paid once in isolation.

Refs #246"
git push -u origin HEAD
gh pr create --draft --base ansible \
  --title "docs(collection): attribute restore_only flow to its owning authorities (#246)" \
  --body "Comment-only attribution fix deferred from #245. Split from the #246 documentation PR so the full collection gate set (unit, integration, scenario, syntax, build) runs in isolation.

Refs #246"
```

---

### Task 8: Follow-up issue for the ci.md pointer

**Files:** none

**Interfaces:**
- Consumes: nothing
- Produces: a GitHub issue referenced from the Task 6 pull request

- [ ] **Step 1: Confirm the conflict still holds**

```bash
sed -n '341,345p' AGENTS.md
grep -c pytest docs/development/ci.md
```

Expected: `AGENTS.md` names `docs/development/ci.md` as an authoritative gate inventory, and the `pytest` count is `0`.

- [ ] **Step 2: File the issue**

```bash
gh issue create \
  --title "AGENTS.md cites docs/development/ci.md as a gate inventory, but ci.md contains no gates" \
  --body "$(cat <<'BODY'
## Problem

`AGENTS.md:341-344` states that the authoritative gate inventory and exact commands live in
`docs/development/ci.md` and `docs/development/testing.md`.

`docs/development/ci.md` is a Quay/GHCR registry-secrets and container-build guide. It contains
zero `pytest` references and no gate inventory. A contributor following the pointer finds
nothing about verification surfaces.

Surfaced while implementing #246. Neither `AGENTS.md` nor `ci.md` was in that issue's declared
scope, so #246 made `docs/development/testing.md` the sole gate inventory and left this pointer
for a dedicated change.

## Options

1. Narrow the `AGENTS.md` sentence to cite `testing.md` only, and let `ci.md` keep its
   container-publishing scope. Smallest change.
2. Rename `ci.md` to something matching its content (for example `container-publishing.md`) and
   update the required-documentation list in the `documentation` job of
   `.github/workflows/ci-cd.yml`, plus any inbound links.
3. Make `ci.md` an actual CI gate inventory. Largest change, and it would duplicate
   `testing.md`.

Option 1 is the smallest correct fix.

## Note

`AGENTS.md` is a protected policy document with its own guardrail tests. Any edit must keep
`tests/test_documentation_guardrails.py` green, including the link-resolution and
section-ordering tests.
BODY
)"
```

- [ ] **Step 3: Cross-reference from the documentation pull request**

Comment the new issue number on the Task 6 pull request so the deferral is traceable:

```bash
gh pr comment <PR-number> --body "Follow-up for the AGENTS.md -> ci.md gate-inventory pointer: #<issue-number>"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
| --- | --- |
| Contributor: branch, start gate, worktree | 1 (Step 4) |
| Contributor: 120-character policy | 1 (Step 5) |
| Contributor: ownership routing table | 1 (Step 6) |
| Contributor: dry-run as public contract | 1 (Step 8) |
| Contributor: current CLI examples | 1 (Step 9), verified Step 11 |
| Contributor: remove feature ideas | 1 (Step 10) |
| Testing: nine-surface taxonomy with five fields | 2 (Step 3) |
| Testing: CI-exact formatter commands | 2 (Step 8) |
| Testing: `modules/preflight/` package | 2 (Step 4) |
| Testing: run_tests.sh non-completeness | 2 (Step 5) |
| Testing: historical observations labelled | 2 (Step 6) |
| Testing: compatibility linked, not restated | 2 (Step 3), guarded Step 1 |
| Testing: obsolete CLI examples corrected | 2 (Step 9) |
| Testing: three confidence tiers | 2 (Step 3) |
| Architecture 3a: metadata | 3 (Step 3) |
| Architecture 3b: extraction prose | 3 (Step 4) |
| Architecture 3c: RunRecord vocabulary | 3 (Steps 5, 6) |
| Architecture 3d: authority boundary links | 3 (Step 7) |
| Architecture 3e: lab-controller attribution | 4 |
| Guardrails: 9 spec tests + retarget | 1, 2, 3, 5 (12 tests total) |
| Verification block | 6 (Step 2) |
| Deliverable 1: docs PR | 6 |
| Deliverable 2: collection PR | 7 |
| Deliverable 3: follow-up issue | 8 |
| `CHANGELOG.md` entry | 6 (Step 1) |

No spec requirement is unassigned.

**Guardrail count reconciliation:** the spec's table lists nine tests. This plan implements
twelve: the nine, plus `test_contributing_names_primary_branch_and_start_gate` and
`test_architecture_uses_run_record_vocabulary` (split out because they assert distinct
contracts and would otherwise hide two failures behind one), plus
`test_lab_role_controller_spec_attributes_uid_binding_to_owning_authority` covering spec item
3e, which the spec's table omitted. The existing
`test_contributing_matches_current_dev_workflow` is not rewritten but is explicitly
re-verified in Task 1 Step 12, since its four tokens must survive the rewrite.

**Placeholder scan:** no `TBD`, `TODO`, "implement later", "add appropriate error handling", or
"similar to Task N" appears. Every code step contains the literal content to write. The one
placeholder-shaped item — `<PR-number>` and `<issue-number>` in Task 8 Step 3 — is a runtime
value the implementer reads from the preceding command's output.

**Type and name consistency:** `CONTRIBUTING_DOC`, `TESTING_DOC`, `ARCHITECTURE_DOC`,
`LAB_CONTROLLER_SPEC_DOC`, and `CONTRIBUTOR_DOCS` are defined once in Task 1 Step 1 and used
under those exact names in Tasks 2, 3, 4, and 5. `_read` matches the existing helper signature
at `tests/test_documentation_guardrails.py:33`. `re` is already imported at line 4; no new
import is required. Every source symbol named in architecture prose is verified against source
in Task 3 Step 9.

**Ordering constraint:** Task 5 must run after Tasks 1 through 3. Tasks 7 and 8 are independent
of the documentation branch and of each other.
