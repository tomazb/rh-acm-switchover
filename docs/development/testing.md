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
`.github/workflows/ansible-collection-foundation.yml`, which is ground truth. Each collection
pytest command keeps the `PYTHONPATH=.` prefix to match that CI invocation exactly. Running from
the repository root also works without it, because `setup.cfg` sets `pythonpath = .` for pytest,
but the documented form is CI's.

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

By default, this runs the root test lane, then the non-live release-framework helper tests under `tests/release/`, and excludes long-running E2E tests (marked `@pytest.mark.e2e`).

`./run_tests.sh` covers surfaces 1 and 2 only. It never runs collection unit, integration,
scenario, syntax, or build gates, so it is not a complete verification surface for any change
that touches `ansible_collections/`.

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

Live certification requires an explicit profile:

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
- **modules/preflight.py**: All validation checks

### Coverage Goals

- **Target**: 80%+ line coverage
- **Critical paths**: 100% coverage for data protection logic
- **Current status**: See coverage report for details

Mutation testing is documented as a deferred concept, not an active local or CI
gate. See [Mutation Testing Notes](mutation-testing-plan.md) for the future
Superpowers design/spec handoff.

## Code Quality Tools

### Flake8 (Style)

```bash
flake8 acm_switchover.py lib/ modules/
```

Configuration in `setup.cfg`:
- Max line length: 120
- Complexity: 15

### Pylint (Analysis)

```bash
pylint acm_switchover.py lib/ modules/
```

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

### MyPy (Type Checking)

```bash
mypy acm_switchover.py lib/ modules/ --ignore-missing-imports
```

## Security Testing

### Bandit (Static Security Analysis)

```bash
bandit --ini .bandit -ll
```

### Safety (Dependency Vulnerabilities)

```bash
safety check
```

### Pip-Audit (Supply Chain)

```bash
pip-audit --desc
```

## CI/CD Integration

### GitHub Actions Workflows

#### CI/CD Pipeline (`.github/workflows/ci-cd.yml`)

Runs on every push and pull request:
- ✅ Unit tests (Python 3.10-3.12)
- ✅ Code quality checks
- ✅ Security scanning
- ✅ Syntax validation
- ✅ Documentation checks
- ✅ Integration tests (dry-run)
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

Both modes prove that inputs validate and that the planned actions resolve. Neither is live
evidence, and neither is certification evidence.

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

**Last Updated**: 2026-08-12
