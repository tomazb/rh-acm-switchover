# ACM Switchover Test Suite

Comprehensive test suite for the Red Hat Advanced Cluster Management (ACM) Switchover Tool.

## Overview

The repository now ships several test surfaces:
- Root Python and shell-adjacent tests under `tests/`
- Collection tests under `ansible_collections/tomazb/acm_switchover/tests/`
- On-demand E2E coverage under `tests/e2e/`

Avoid relying on hard-coded totals in this file; the suite is still expanding.

## Test Organization

### Root Tests (`tests/`)

- `tests/test_*.py` covers the Python CLI, shared libraries, workflow modules, shell completions, and script/tool regression checks.
- Script-adjacent coverage includes `test_scripts.py`, `test_scripts_integration.py`, `test_check_rbac.py`, `test_generate_merged_kubeconfig_script.py`, and `test_argocd_manage_script.py`.
- See `README-scripts-tests.md` for the script/tool-focused subset.

### Collection Tests

- `ansible_collections/tomazb/acm_switchover/tests/unit/` covers collection plugins, module_utils, and role contracts.
- `ansible_collections/tomazb/acm_switchover/tests/integration/` covers mocked role/playbook flows.
- `ansible_collections/tomazb/acm_switchover/tests/scenario/` covers checkpoint/resume-style scenarios.

### E2E Tests

- `tests/e2e/` contains the pytest-based real-cluster and dry-run orchestration harness.
- See `tests/e2e/README.md` for environment, markers, and execution guidance.

## Running Tests

### Default Local Run

```bash
./run_tests.sh
```

This is the preferred entry point for local verification. It respects the existing virtualenv if one is active, otherwise it uses `.venv` first and falls back to `venv`.

### Root Tests Only

```bash
python -m pytest tests/ -q
```

### Collection Tests Only

```bash
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
python -m pytest ansible_collections/tomazb/acm_switchover/tests/integration/ -q
```

### Combined Root + Collection Run

```bash
source .venv/bin/activate
python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ tests/ -q
```

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
