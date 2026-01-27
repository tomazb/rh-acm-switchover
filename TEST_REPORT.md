# ACM Switchover - Comprehensive Test Report
**Generated:** January 27, 2026  
**Test Environment:** Linux, Python 3.14.2, pytest 9.0.2

---

## Executive Summary

✅ **Unit Tests:** 512 passed, 92 deselected (669.59s)  
✅ **Code Coverage:** 69% (3,760 statements analyzed)  
⚠️ **Code Quality:** Issues found by flake8, pylint, black, isort, and mypy  
✅ **Security Checks:** Bandit and pip-audit completed with no known vulnerabilities

---

## Test Suite Overview

### Total Test Coverage
- **Total Tests Collected:** 604
- **Tests Executed:** 512 passed
- **Tests Deselected:** 92
- **Execution Time:** 669.59s (0:11:09)

### Test Breakdown by Category
- **Python Unit & Integration Tests:** 512 passed
- **Bash Script Tests:** Included in the unit test run
- **E2E/Soak Tests:** Not executed (deselected by default)

---

## Code Coverage Analysis

### Coverage Summary
```
Total Coverage: 69% (2,579 covered / 3,760 total statements)
```

### Library Coverage (lib/)
| Module | Statements | Miss | Coverage |
|--------|-----------|------|----------|
| __init__.py | 7 | 0 | 100% |
| constants.py | 81 | 14 | 83% |
| exceptions.py | 6 | 0 | 100% |
| kube_client.py | 334 | 114 | 66% |
| rbac_validator.py | 213 | 23 | 89% |
| utils.py | 293 | 39 | 87% |
| validation.py | 139 | 14 | 90% |
| waiter.py | 31 | 3 | 90% |

### Modules Coverage (modules/)
| Module | Statements | Miss | Coverage |
|--------|-----------|------|----------|
| __init__.py | 8 | 0 | 100% |
| activation.py | 241 | 53 | 78% |
| backup_schedule.py | 62 | 6 | 90% |
| decommission.py | 133 | 31 | 77% |
| finalization.py | 335 | 89 | 73% |
| post_activation.py | 548 | 242 | 56% |
| preflight_coordinator.py | 66 | 55 | 17% |
| primary_prep.py | 108 | 22 | 80% |
| preflight/__init__.py | 7 | 0 | 100% |
| preflight/backup_validators.py | 264 | 53 | 80% |
| preflight/base_validator.py | 6 | 0 | 100% |
| preflight/cluster_validators.py | 26 | 6 | 77% |
| preflight/namespace_validators.py | 64 | 8 | 88% |
| preflight/reporter.py | 28 | 0 | 100% |
| preflight/version_validators.py | 191 | 98 | 49% |
| preflight_validators.py | 6 | 0 | 100% |

### Top-level Script Coverage
| Module | Statements | Miss | Coverage |
|--------|-----------|------|----------|
| acm_switchover.py | 277 | 130 | 53% |
| check_rbac.py | 89 | 89 | 0% |
| show_state.py | 197 | 92 | 53% |

---

## Code Quality Analysis

### Flake8
Issues found:
- F401 unused imports: acm_switchover.py (typing.Any, typing.Dict)
- E306 formatting: lib/utils.py
- C901 complexity: lib/validation.py, modules/post_activation.py, modules/preflight/backup_validators.py
- W293 whitespace: modules/post_activation.py

### Pylint
Selected issues:
- line-too-long in acm_switchover.py and lib/constants.py
- broad-exception-caught in multiple modules
- too-many-statements/branches/returns in validation and post_activation
- import-outside-toplevel in several modules

Pylint score: **9.58/10**

### Black
Formatting changes required in 9 files (see run output). Black reported: “Format issues found. Run: black --line-length 120 .”

### isort
Import sorting issues reported in:
- lib/kube_client.py
- modules/post_activation.py
- modules/preflight_validators.py
- modules/primary_prep.py
- modules/preflight/__init__.py
- modules/preflight/backup_validators.py

### MyPy
Type-check error:
- lib/utils.py:477 – invalid return type for __exit__ (expected Literal[False] or None)

---

## Security Checks

- **Bandit:** No issues identified
- **pip-audit:** No known vulnerabilities found

---

## Test Execution Environment

### Python Environment
```
Python Version: 3.14.2
Pytest Version: 9.0.2
Platform: Linux
pytest-mock: 3.15.1
pytest-cov: 7.0.0
```

### Virtual Environment
```
Location: .venv/
Type: Python venv
Status: Active
```

---

## Test Execution Timeline

| Phase | Duration | Status |
|-------|----------|--------|
| Unit Tests + Coverage | 669.59s | ✅ PASSED (512/512) |
| Code Quality Checks | Not recorded | ⚠️ Issues found |
| Security Checks | Not recorded | ✅ Completed |
| **Total** | **~11m** | **⚠️ Issues present in quality checks** |

---

## Known Limitations

1. **E2E/Soak Coverage:** Not executed in the default run (92 deselected).
2. **Cluster Dependency:** Full end-to-end validation requires real ACM clusters.
3. **Code Quality Gaps:** Formatting and lint issues remain in several modules.

---

## Suggestions

Address lint and formatting issues reported by flake8, pylint, black, isort, and mypy to improve code quality and reduce CI noise. Once these are resolved, re-run the test suite to confirm a clean quality report alongside passing unit tests.

Run on-demand E2E/soak tests when cluster access is available and capture environment details and timing to make results reproducible and comparable across releases.

---

## Conclusion

Unit and integration tests completed successfully with 512 passing tests and 69% overall coverage. Code quality checks surfaced multiple lint, formatting, and type-check issues that should be addressed before considering the report clean. E2E/soak tests were not executed.

---

## Running Tests Locally

### Core Tests (default, excludes E2E)
```bash
./run_tests.sh
```

### On-demand E2E
```bash
RUN_E2E=1 ./run_tests.sh
```

### Quick pytest run
```bash
pytest tests/ -v
```

### Run specific test files
```bash
pytest tests/test_kube_client.py -v
pytest tests/test_scripts.py tests/test_scripts_integration.py -v
```
