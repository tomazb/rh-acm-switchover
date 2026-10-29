# ACM Switchover Test Suite

Comprehensive test suite for the Red Hat Advanced Cluster Management (ACM) Switchover Tool.

## Overview

The repository now ships several test surfaces:
- Root Python and shell-adjacent tests under `tests/`
- Property-based tests under `tests/properties/`, included in the root selection
- Collection tests under `ansible_collections/tomazb/acm_switchover/tests/`
- Release validation framework tests under `tests/release/`
- On-demand E2E coverage under `tests/e2e/`

Avoid relying on hard-coded totals in this file; the suite is still expanding.

## Test Organization

### Root Tests (`tests/`)

- `tests/test_*.py` covers the Python CLI, shared libraries, workflow modules, shell completions, and script/tool regression checks.
- Script-adjacent coverage includes `test_scripts.py`, `test_scripts_integration.py`, `test_check_rbac.py`, and `test_generate_merged_kubeconfig_script.py`.
- The seven property suites cover validation, paths, checkpoints, reports, BackupSchedules, Argo CD, and RBAC. See the [PBT guide](../docs/testing/property-based-testing.md) for selection and profiles.
- See `README-scripts-tests.md` for the script/tool-focused subset.

### Collection Tests

- `ansible_collections/tomazb/acm_switchover/tests/unit/` covers collection plugins, module_utils, and role contracts.
- `ansible_collections/tomazb/acm_switchover/tests/integration/` covers mocked role/playbook flows.
- `ansible_collections/tomazb/acm_switchover/tests/scenario/` covers checkpoint/resume-style scenarios.

### E2E Tests

- `tests/e2e/` contains the pytest-based real-cluster and dry-run orchestration harness.
- See `tests/e2e/README.md` for environment, markers, and execution guidance.

### Release Validation Framework

- `tests/release/` contains the profile-driven release certification framework.
- `./run_tests.sh` now runs the non-live `tests/release/` helper suite explicitly after the root test lane.
- Live certification requires a real lab profile and is not certification-eligible when fake discovery clients or fake stream adapters are injected.
- See `tests/release/README.md` and `docs/development/release-validation-framework.md`.

## Running Tests

### Default Local Run

```bash
./run_tests.sh
```

This is the preferred entry point for local verification. It respects the existing virtualenv if one is active, otherwise it uses `.venv` first and falls back to `venv`.
The default runner executes root tests first, then runs `tests/release/` as an explicit non-live helper lane. Live certification still requires a profile-driven invocation.
The runner treats CI-equivalent `black`, `isort`, `mypy`, and `bandit` checks as hard failures by default. Use `STRICT_QUALITY=0 ./run_tests.sh` only when you need a local advisory pass.
It does not run the Collection unit, integration, scenario, syntax, or build gates. The [testing guide](../docs/development/testing.md#the-nine-verification-surfaces) owns the complete inventory and evidence limits.

### Root Tests Only

```bash
python -m pytest tests/ --ignore=tests/release -q -m "not e2e" -n auto --dist worksteal
```

This is the default root selection, including property tests and unmarked E2E-directory helpers. The [root dependency/import gap](../docs/development/testing.md#root-dependency-and-import-gap) remains tracked separately; passing with the declared development dependencies does not prove no-Ansible import safety.

### Release Framework Helpers (Non-Live)

```bash
ACM_RELEASE_PROFILE='' PYTEST_ADDOPTS='' python -m pytest tests/release -q
```

Keep this lane serial. Clearing both variables gives the same non-live isolation as `./run_tests.sh`; a bare direct pytest invocation can instead resolve an inherited release profile. Live certification requires an operator-provided profile:

```bash
python -m pytest tests/release/test_release_certification.py \
  --release-profile /path/to/release-profile.yaml \
  --release-mode certification
```

### Collection Pytest Lanes

Install the Collection's dependencies and use the environments defined by the [compatibility authority](../ansible_collections/tomazb/acm_switchover/docs/compatibility.md).

```bash
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q -n auto --dist worksteal
export ANSIBLE_COLLECTIONS_PATH="$(pwd):${HOME}/.ansible/collections"
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q -n auto --dist worksteal
PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/scenario/ -q
```

### Complete Relevant Verification

Root plus Collection unit tests alone are not the complete Collection gate set. Follow the [commands by surface](../docs/development/testing.md#commands-by-surface), including the resolved-dependency check, playbook syntax checks, and archive build on both supported lanes when the changed surface requires them. Release helpers and live E2E/certification stay separate and serial.

### E2E Runs

```bash
RUN_E2E=1 ./run_tests.sh
python -m pytest tests/e2e/ -v -m e2e
```

## Guidance

- Use `requirements-dev.txt` for local tooling and test dependencies.
- Prefer `.venv` for local work to match the repository guidance elsewhere.
- Keep new test docs qualitative unless the source of truth is automated.
- When adding new scripts, CLIs, or collection plugins, update the relevant README in the same change.

## Troubleshooting

- If collection test discovery fails, verify collection dependencies are installed and the active interpreter matches the repo virtualenv.
- If E2E tests fail during setup, start with `tests/e2e/README.md` and the real-cluster prerequisites documented there.
- If the default runner behavior is unclear, use `./run_tests.sh` first and only fall back to direct `pytest` commands when you need a narrower slice.
