# ACM Switchover Automation - Testing Guide

## Overview

This document describes the testing strategy, test structure, and how to run tests for the ACM Switchover Automation project.

This document is the gate inventory for the repository. It defines every maintained
verification surface, the exact command that runs it, and — as importantly — what each surface
does not prove.

`AGENTS.md` owns the policy for which gates a change must run; see its
[Verification Matrix by Changed Surface](../../AGENTS.md#verification-matrix-by-changed-surface).
This document owns the commands.

### The nine verification surfaces

| # | Surface | Nature | What it does not prove |
| --- | --- | --- | --- |
| 1 | Root Python and Bash tests | Local, fake-backed | Nothing about live clusters. Of the collection it proves only the four parity contracts — shared constants (`tests/test_constants_parity.py`), Argo CD `ACM_KINDS`/`ACM_NAMESPACES` (`tests/test_argocd_constants_parity.py`), cross-phase state key names (`tests/test_checkpoint_state_parity.py`), and RBAC expansion (`tests/test_rbac_collection_parity.py`). Those compare declared values across the two runtimes; no collection role, playbook, or module is executed |
| 2 | Release-framework helpers | Local and fake-backed **only when no release profile is supplied** | Not certification evidence. Non-live only while neither `--release-profile` (including one injected through `PYTEST_ADDOPTS`) nor `ACM_RELEASE_PROFILE` resolves a profile |
| 3 | Collection unit tests | Local, static and fake-backed | Nothing about live cluster behaviour. Playbook and cross-role wiring is checked statically against the YAML, not by executing it |
| 4 | Collection integration tests | Local, fake-backed | Nothing about real cluster responses |
| 5 | Collection scenario tests | Local, fixture-backed | Nothing about live timing or live partial failure. Interruption and resume are exercised, but only against checked-in fixtures |
| 6 | Playbook syntax check | Local | Only that playbooks parse and resolve — no behaviour at all |
| 7 | Collection archive build | Local | Only that the archive builds — not that it works |
| 8 | On-demand E2E | Live, real hubs | Not certification evidence. Running it under a release profile does not promote its results to certification evidence |
| 9 | Profile-driven live release certification | Live, certification-eligible | Bounded by the supplied profile. Driven by the profile-based release orchestrator, not gated by the lab controller |

Surfaces 1 and 3 through 7 are entirely local and fake-backed, fixture-backed, or static. None
of them is live evidence. Fake, dry-run, static-fixture, and local-harness results never
substitute for live certification evidence — see the
[Release-Validation and Lab-Controller Authority Boundary](../../AGENTS.md#release-validation-and-lab-controller-authority-boundary).

Surface 2 is local **conditionally** for direct pytest invocations. `tests/release/conftest.py`
resolves the release profile from `--release-profile` *or* the `ACM_RELEASE_PROFILE` environment
variable, and skips release-marked items only when neither supplies one. Pytest can populate the
`--release-profile` option from inherited `PYTEST_ADDOPTS` before the conftest reads it. A direct
`python -m pytest tests/release -q` therefore stops being a helper-only lane whenever the option
or `ACM_RELEASE_PROFILE` resolves a profile, and then runs
`tests/release/test_release_certification.py` against real infrastructure through live discovery
and the stream adapters. `./run_tests.sh` is deliberately different: it overrides inherited
`ACM_RELEASE_PROFILE` and `PYTEST_ADDOPTS` with empty values for its release-helper subprocess,
so the default convenience runner remains non-live. Invoke the profile-driven pytest entrypoint
directly for live certification.

Surface 9 is the profile-based release orchestrator invoked directly from pytest. It is not
gated by the lab role controller. The controller-owned read-only live-discovery path is a
separate authority whose artifacts are explicitly stamped `certification_eligible=false` and
`live_certification_evidence=false`, so it cannot establish certification either — see
[Release validation framework](release-validation-framework.md) and
[Lab role controller spec](lab-role-controller-spec.md).

### Commands by surface

Surfaces 3 through 7 take their commands from
`.github/workflows/ansible-collection-foundation.yml`, which is ground truth. Each collection
pytest command keeps the `PYTHONPATH=.` prefix to match that CI invocation exactly. Running from
the repository root also works without it, because `setup.cfg` sets `pythonpath = .` for pytest,
but the documented form is CI's.

```bash
# 1. Root Python and Bash tests
# CI (.github/workflows/ci-cd.yml, "Run root tests with coverage") runs this same selection with
# coverage and JUnit reporting added: --cov=. --cov-report=xml --cov-report=html
# --cov-report=term --junitxml=pytest-results-<python-version>.xml. The selection of tests is
# identical; only the reporting artifacts differ. Use the coverage form below when you need
# the report.
python -m pytest tests/ --ignore=tests/release -v -m "not e2e"

# 2. Release-framework helper tests
# Non-live only when no profile is resolved. Inspect ACM_RELEASE_PROFILE and PYTEST_ADDOPTS first.
python -m pytest tests/release -q

# 3. Collection unit tests
# CI precedes this with a separate compatibility-contract step under an exported
# ANSIBLE_COLLECTIONS_PATH — see "Folded into surface 3" below.
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
# Two things here are load-bearing and are NOT optional polish. A bare loop exits with the
# status of the *last* playbook, so an early failure followed by a later success returns 0 —
# a silent false pass. And a resolved collection that does not support this lane's
# ansible-core must fail the lane, not warn inside a passing run. `set -o pipefail` is what
# makes `|| status=1` observe ansible-playbook rather than tee.
set -o pipefail
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
log="$(mktemp)"
status=0
for playbook in ansible_collections/tomazb/acm_switchover/playbooks/*.yml; do
  echo "== ${playbook}"
  ansible-playbook "${playbook}" --syntax-check 2>&1 | tee -a "${log}" || status=1
done
if [ "${status}" -ne 0 ]; then
  echo "playbook syntax check failed"
  exit 1
fi
if grep -qE "does not support Ansible version" "${log}"; then
  echo "a collection reported an unsupported ansible-core version for this lane"
  grep -nE "does not support Ansible version" "${log}"
  exit 1
fi

# 7. Collection archive build
ansible-galaxy collection build --output-path /tmp/dist \
  ansible_collections/tomazb/acm_switchover
```

#### Folded into surface 3: the resolved-dependency compatibility check

`ansible-collection-foundation.yml` runs one further collection-pytest step, "Verify resolved
dependency compatibility", *before* the unit sweep. It is not a tenth surface: it re-runs a
single file that surface 3 already covers,
`ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py`. What
makes it worth naming is the environment. It runs immediately after
`ansible-galaxy collection install` and **with `ANSIBLE_COLLECTIONS_PATH` exported**, so the
contract is checked against the dependencies the lane actually resolved. Surface 3's own
invocation deliberately carries no export, which is why the same file is run twice:

```bash
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest \
  ansible_collections/tomazb/acm_switchover/tests/unit/test_compatibility_contract.py -q
```

If you reproduce surface 3 without first running this step, a lane-specific dependency
resolution failure is exactly what you will miss.

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

## Test Structure

```
tests/
├── __init__.py
├── test_utils.py             # Tests for lib/utils.py
├── test_kube_client.py       # Tests for lib/kube_client.py
├── test_preflight.py         # Tests for the modules/preflight/ package
├── test_backup_schedule.py   # Tests for modules/backup_schedule.py
├── test_primary_prep.py      # Tests for modules/primary_prep.py
├── test_decommission.py      # Tests for modules/decommission.py
├── test_post_activation.py   # Tests for modules/post_activation.py
├── test_finalization.py      # Tests for modules/finalization.py
├── test_activation.py        # Tests for modules/activation.py
├── test_validation.py        # Tests for lib/validation.py
├── test_rbac_validator.py    # Tests for lib/rbac_validator.py
├── test_waiter.py            # Tests for lib/waiter.py
├── test_main.py              # Tests for acm_switchover.py (args)
├── test_scripts.py           # Unit tests for bash scripts
└── test_scripts_integration.py # Integration tests for bash scripts
```

## Running Tests

### Quick Test Run

```bash
./run_tests.sh
```

By default, this runs the root test lane, then the release-framework helper tests under
`tests/release/`, and excludes long-running E2E tests (marked `@pytest.mark.e2e`). For the release
helper subprocess the runner overrides inherited `ACM_RELEASE_PROFILE` and `PYTEST_ADDOPTS` with
empty values, so neither a shell profile nor pytest option injection can silently promote the
default convenience run into live certification. `./run_tests.sh` is not a live-certification
entrypoint; invoke certification directly with an explicit release profile instead.

`./run_tests.sh` covers surfaces 1 and 2, and adds surface 8 only when you export `RUN_E2E=1`.
No invocation of it runs the collection unit, integration, scenario, syntax, or build gates —
surfaces 3 through 7 have no code path in the runner at all. In particular, `./run_tests.sh`
is not a complete verification surface for any change that touches `ansible_collections/`.

CI-equivalent quality gates (`black`, `isort`, `mypy`, and `bandit`) fail by default.
For a local advisory-only quality pass, run `STRICT_QUALITY=0 ./run_tests.sh`.
To include E2E tests on demand:

```bash
RUN_E2E=1 ./run_tests.sh
```

#### Virtual Environment Usage

- Prefer activating an existing virtual environment before running tooling.
- The test runner (`run_tests.sh`) detects an active `$VIRTUAL_ENV`; otherwise it will try `.venv/` first, then `venv/`, and create `.venv/` if none exist.
- Recommended setup:
    - Create `.venv` once: `python3 -m venv .venv`
    - Activate: `source .venv/bin/activate`
    - Then run: `./run_tests.sh`

This script will:
1. Set up a virtual environment (if needed)
2. Install dependencies
3. Run unit tests with coverage (excluding E2E by default)
4. Run code quality checks (flake8, pylint, black, isort, mypy)
5. Run security scans (bandit, pip-audit)
6. Validate Python syntax

### Manual Test Execution

#### Install Test Dependencies

```bash
pip install -r requirements-dev.txt
```

#### Run Root Tests (matches the default local runner)

```bash
python -m pytest tests/ --ignore=tests/release -v -m "not e2e"
```

#### Run Specific Test File

```bash
python -m pytest tests/test_utils.py -v
```

#### Run Specific Test Case

```bash
python -m pytest tests/test_utils.py::TestStateManager::test_initial_state -v
```

#### Run with Coverage

```bash
python -m pytest tests/ -v -m "not e2e" --cov=. --cov-report=html --cov-report=term
```

View HTML coverage report:
```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### E2E Tests (On Demand)

E2E tests are marked with `@pytest.mark.e2e` and are intended to run only on demand.
They may require real cluster contexts and take significantly longer.

```bash
python -m pytest tests/e2e/ -v -m e2e
```

See `tests/e2e/README.md` for details.

### Release Validation Framework

Framework helper tests are part of the default `./run_tests.sh` flow and should
also be runnable directly when you need a tighter release-framework loop:

```bash
python -m pytest tests/release -q
```

That direct command is a helper-only lane only while no profile is resolved.
`tests/release/conftest.py` takes the profile from `--release-profile` *or*
`ACM_RELEASE_PROFILE`; inherited `PYTEST_ADDOPTS` can also populate the `--release-profile`
option. `./run_tests.sh` deliberately overrides both environment variables with empty values for
its internal release-helper subprocess. Use the direct profile-driven entrypoint below when live
certification is intended.

Live certification requires an explicit profile, supplied by flag or by environment:

```bash
python -m pytest tests/release/test_release_certification.py \
  --release-profile /path/to/release-profile.yaml \
  --release-mode certification
```

Focused reruns are filter-based and use `--release-scenario` / `--release-stream`.
The current harness does not support resuming or rerunning from a previous
artifact directory. Dirty checkouts fail certification-mode runs unless
`--allow-dirty` is supplied; even then, the run remains not certification
eligible.

When a profile defines `release.metadata_files`, the harness validates those
files against `release.expected_version` and records the metadata status/hash in
`manifest.json`. See `docs/development/release-validation-framework.md` for the
full contract.

### Full Real E2E (On Demand)

These commands run real switchover cycles against actual hubs. Ensure the
output directory exists before running (it is not created automatically):

```bash
mkdir -p ./e2e-results
```

Single-cycle real switchover (passive -> secondary):

```bash
python -m pytest tests/e2e/test_e2e_switchover.py -v -m e2e \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-method passive \
  --e2e-old-hub-action secondary \
  --e2e-cycles 1 \
  --e2e-output-dir ./e2e-results
```

Multi-cycle soak with limits:

```bash
python -m pytest tests/e2e/test_e2e_switchover.py -v -m e2e \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-method passive \
  --e2e-old-hub-action secondary \
  --e2e-cycles 5 \
  --e2e-run-hours 2 \
  --e2e-max-failures 2 \
  --e2e-output-dir ./e2e-results
```

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

## Test Coverage

### Current Coverage

- **lib/utils.py**: StateManager, Phase enum, helper functions
- **lib/kube_client.py**: KubeClient initialization, CRUD operations, dry-run mode
- **modules/preflight/**: the validator package — `base_validator.py`, `backup_validators.py`,
  `cluster_validators.py`, `namespace_validators.py`, `version_validators.py`, and `reporter.py`.
  There is no `modules/preflight.py`; the coordinator that drives the package is
  `modules/preflight_coordinator.py`

### Coverage Goals

- **Target**: 80%+ line coverage
- **Critical paths**: 100% coverage for data protection logic
- **Current status**: See coverage report for details

Mutation testing is documented as a deferred concept, not an active local or CI
gate. See [Mutation Testing Notes](mutation-testing-plan.md) for the future
Superpowers design/spec handoff.

## Code Quality Tools

Every command in this section is copied from the invocation that actually runs it — the `lint`
and `security` jobs in `.github/workflows/ci-cd.yml`, or `run_tests.sh` where the tool is
local-only. A scoped command that merely looks plausible fails differently from CI, which is
worse than no command at all.

### Flake8 (Style)

CI runs flake8 **twice**, and only the first invocation can fail the build:

```bash
# Blocking: syntax errors and undefined names only.
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
# Advisory: --exit-zero means this pass reports style findings and always returns 0.
flake8 . --count --exit-zero --max-complexity=15 --max-line-length=120 --statistics
```

So flake8 proves only that the tree has no syntax errors and no undefined names. The
120-character maximum and the complexity ceiling of 15 are reported, never enforced.

`setup.cfg` also declares `max-line-length = 120` and `max-complexity = 15`, but it is **not**
the governing authority for those numbers and must not be cited as one. CI passes both on the
command line, to the `--exit-zero` invocation, so neither can fail a build via flake8. The value
that can break a build over line length is the one handed to `black --line-length` and
`isort --line-length` below — the same authority [`CONTRIBUTING.md`](../../CONTRIBUTING.md)
names.

That is a statement about those two numbers, not about the file. CI does read `setup.cfg`:
flake8 runs from the repository root and discovers it there, so the `[flake8]` section's
`ignore = E203,E501,W503` and its `exclude` list are both in force. An explicit flag overrides
the one value it names for that invocation; it does not disable config discovery.

Because `setup.cfg:13-29` already excludes `.git`, `__pycache__`, `.venv`, `venv`,
`.worktrees`, `.claude/worktrees`, the `*_cache` directories, `build`, `dist`, `completions`,
`.eggs`, `graphify-out`, `htmlcov`, and `review`, the repository-root `flake8 .` above does not
walk your virtualenv or the other generated trees. That exclusion is flake8's alone: black and
isort get no such list, which is why the commands below name paths explicitly instead of
targeting the root — and why repo-wide formatting stays prohibited on the authority of
[`AGENTS.md`](../../AGENTS.md), independently of what any exclude list happens to cover.

### Pylint (Analysis)

```bash
pylint acm_switchover.py lib/ modules/ --exit-zero --max-line-length=120 \
  --disable=C0103,C0114,C0115,C0116
```

`--exit-zero` again: pylint cannot fail CI. `run_tests.sh` runs the identical command through
its advisory helper, so it cannot fail the local runner either.

### Black (Formatting)

Reproduce CI exactly. The path list below is copied from the `lint` job in
`.github/workflows/ci-cd.yml`. Do not substitute `.`, and do not rely on an editor auto-format
hook, which only touches files edited in your session.

Substituting `.` is prohibited by [`AGENTS.md`](../../AGENTS.md), which is the authority here.
The mechanical reason is narrower than it is sometimes stated: black's built-in default excludes
already cover `.venv/`, `venv/`, `build/`, `dist/`, and the `*_cache` directories, and
`setup.cfg`'s `[isort] skip` covers a similar set for isort. What neither excludes is this
repository's other generated and vendored trees — `completions/` (a protected path),
`.claude/worktrees/` (entire nested checkouts), `graphify-out/`, `htmlcov/`, and `review/`.
Those are what a repo-root run reformats, and `completions/` alone is reason enough.

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

### MyPy (Type Checking)

Copied from the `lint` job. The collection plugins and collection tests are part of the checked
set, and `--explicit-package-bases` and `--no-strict-optional` are both load-bearing — dropping
either changes what mypy reports:

```bash
mypy --explicit-package-bases acm_switchover.py lib/ modules/ \
  ansible_collections/tomazb/acm_switchover/plugins \
  ansible_collections/tomazb/acm_switchover/tests \
  --ignore-missing-imports --no-strict-optional
```

Root `tests/` is deliberately absent: CI does not type-check it.

## Security Testing

### Bandit (Static Security Analysis)

```bash
bandit --ini .bandit -f json -o bandit-report.json || true
bandit --ini .bandit -f txt
```

The second invocation is the gate; the first only produces the uploaded JSON report and is
neutralised with `|| true`.

Do **not** substitute `bandit --ini .bandit -ll`, which this guide previously documented. `-ll`
filters the report to medium-and-above severity, so it is *weaker* than the CI gate: a
low-severity finding passes locally and then fails CI. Run the unfiltered `-f txt` form above.

### Safety

Safety is not a maintained repository verification gate. The GitHub workflows no longer invoke
`safety scan`, and `safety` remains absent from the repository dependency files. Dependency
vulnerability reporting uses the declared `pip-audit` dependency instead. Reintroducing Safety
would require an explicit CI design, including its installation and authentication contract,
rather than relying on an undeclared executable whose failure can be masked as success.

### Pip-Audit (Supply Chain)

```bash
pip-audit
```

`pip-audit` is declared in `requirements-dev.txt`, so it installs with the maintained development
dependencies. It runs in two places, and neither can fail the overall verification flow:

- **Locally**, `run_tests.sh` invokes bare `pip-audit` through `run_advisory_check`, which prints
  findings and returns 0 regardless.
- **In CI**, the `dependency-check` job of `.github/workflows/security.yml` runs it twice:
  `pip-audit --desc --format json --output pip-audit-report.json || true` produces the uploaded
  artifact, then a bare `pip-audit --desc` produces the log. The second invocation can exit
  non-zero, but the step carries `continue-on-error: true`, so findings remain advisory.

So pip-audit is reported in CI and enforced nowhere. Read the job log or the
`pip-audit-report.json` artifact; a passing job proves nothing about findings.

## CI/CD Integration

### GitHub Actions Workflows

#### CI/CD Pipeline (`.github/workflows/ci-cd.yml`)

Runs on every push and pull request:
- ✅ Unit tests (Python 3.10-3.12)
- ✅ Code quality checks
- ✅ Security scanning
- ✅ Syntax validation
- ✅ Documentation checks
- ✅ `integration-test` job — smoke checks only, despite the job's "Integration Tests (Dry-Run)"
  display name. It captures the flag-only top-level CLI `--help` output and asserts representative
  supported flags are present, prints the version imported from `lib`, and asserts that a freshly
  saved state file contains its expected top-level keys. No dry-run switchover is executed and no
  cluster is contacted, so it proves only that the CLI help surface starts with the expected flags
  and that the state file has the right shape — nothing about switchover behaviour
- ✅ Container build test

#### Security Workflow (`.github/workflows/security.yml`)

Runs daily and on security-related changes:
- 🔒 Dependency vulnerability scanning
- 🔒 Static code security analysis
- 🔒 Secrets detection
- 🔒 Container image scanning
- 🔒 SBOM generation
- 🔒 License compliance

### Viewing CI/CD Results

1. Go to repository on GitHub
2. Click "Actions" tab
3. Select workflow run
4. View job results and artifacts

### Downloading Artifacts

- Coverage reports
- Security scan results
- SBOM files
- License reports

## Test Development Guidelines

### Writing New Tests

1. **Create test file**: `tests/test_<module>.py`
2. **Import dependencies**:
   ```python
   import unittest
   from unittest.mock import MagicMock, patch
   ```
3. **Create test class**:
   ```python
   class TestMyModule(unittest.TestCase):
       def setUp(self):
           # Set up fixtures
       
       def test_feature(self):
           # Test implementation
   ```

### Test Naming Conventions

- Test files: `test_<module>.py`
- Test classes: `Test<ClassName>`
- Test methods: `test_<feature>_<scenario>`

Examples:
- `test_state_manager_initial_state`
- `test_kube_client_dry_run_mode`
- `test_preflight_namespace_missing`

### Mocking Guidelines

Use mocks for external dependencies:
- Kubernetes API calls
- File system operations
- Network requests

Example:
```python
@patch('lib.kube_client.client.CoreV1Api')
def test_namespace_exists(self, mock_api):
    mock_instance = mock_api.return_value
    mock_instance.read_namespace.return_value = MagicMock()
    
    client = KubeClient()
    result = client.namespace_exists("test-ns")
    
    self.assertTrue(result)
```

### Test Data

Use fixtures for test data:
```python
def setUp(self):
    self.mock_mch = {
        "status": {"currentVersion": "2.12.0"}
    }
```

## Manual Integration Testing

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

Both modes prove that inputs validate. Both also do real work when you give them real contexts:
`lib/runtime_bootstrap.initialize_clients` builds actual `KubeClient` instances, hub identities
are read from the live clusters, and the preflight coordinator performs live discovery —
namespace probes, RBAC permission checks, and ACM/OADP version detection. So neither mode is
purely offline.

What they do not prove differs:

- `--validate-only` returns as soon as preflight finishes
  (`lib/operation_runners.py` dispatches to `run_validate_only_preflight` in `lib/workflow.py`).
  It therefore proves nothing about whether the later planned actions resolve — those phases are
  never entered.
- `--dry-run` walks the phase flow but routes every mutation through `KubeClient`'s synthetic
  returns, and still logs a simulated completion message on success.

Neither mode is certification evidence, and neither substitutes for surface 8 or 9.

### Test Clusters

Use non-production clusters for testing:
- Development clusters
- Lab environments
- Kind/Minikube clusters

## Troubleshooting Tests

### Import Errors

Ensure parent directory in path:
```python
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
```

### Mock Issues

Verify mock paths match actual imports:
```python
# If code imports: from lib.kube_client import KubeClient
# Mock as: @patch('lib.kube_client.config')
```

### Coverage Gaps

Identify uncovered code:
```bash
coverage report -m
coverage html
open htmlcov/index.html
```

## Future Testing Enhancements

### Planned Improvements

- [ ] Release artifact resume/rerun support with compatibility validation
- [ ] Broader performance benchmarks for release and E2E paths
- [ ] Additional failure-injection and chaos-style scenarios
- [ ] Mutation testing

### Test Environment Setup

For full integration testing:
1. Set up two test ACM hubs
2. Configure OADP on both
3. Set up managed clusters
4. Configure passive sync
5. Run full switchover test

## Contributing Tests

When contributing code:
1. Write tests for new features
2. Maintain 80%+ coverage
3. Run full test suite before PR
4. Update this guide if needed

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for details.

## Questions and Support

- Check existing tests for examples
- Review test output carefully
- Use verbose mode: `pytest -vv`
- Check CI/CD logs for failures

---

**Last Updated**: 2026-08-13
