# ACM Switchover Test Suite

Comprehensive test suite for the Red Hat Advanced Cluster Management (ACM) Switchover Tool.

## Overview

The test suite uses **pytest** with modern fixtures, markers, and parameterization to provide:
- Fast unit tests for individual components
- Integration tests with mocked Kubernetes/bash environments
- High code coverage with minimal maintenance overhead

**Test Statistics:**
- **Total Tests**: 320
- **Unit Tests**: ~290 (fast, no external dependencies)
- **Integration Tests**: ~30 (mocked external commands)
- **Execution Time**: ~90-95 seconds (all tests)
- **Overall Coverage**: 67%

## Test Organization

### Python Unit Tests (pytest style)
Located in `tests/test_*.py`:

- `test_kube_client.py` - KubeClient operations (28 tests)
- `test_utils.py` - StateManager, Phase enum, utilities (28 tests)
- `test_preflight.py` - Validation reporters and validators (18 tests)
- `test_backup_schedule.py` - BackupSchedule management (13 tests)
- `test_primary_prep.py` - Primary hub preparation (16 tests)
- `test_activation.py` - Secondary hub activation (11 tests)
- `test_decommission.py` - Old hub decommissioning (15 tests)
- `test_post_activation.py` - Post-activation verification (25 tests)
- `test_finalization.py` - Finalization workflow (11 tests)
- `test_waiter.py` - Wait/poll utilities (4 tests)
- `test_main.py` - Argument parsing (8 tests)
- `test_validation.py` - Input validation (28 tests)
- `test_version.py` - Version sync and CLI (10 tests)
- `test_rbac_validator.py` - RBAC validator (13 tests)
- `test_rbac_integration.py` - RBAC manifest consistency (17 tests)
- `test_reliability.py` - Retry logic (5 tests)
- `test_auto_import.py` - Auto-import strategy (4 tests)
- `test_cli_auto_import.py` - CLI flag for auto-import (1 test)
- `test_state_dir_env_var.py` - State directory env var (4 tests)

### Bash Script Tests
- `test_scripts.py` - Argument validation, error handling (20 tests)
- `test_scripts_integration.py` - End-to-end with mocked `oc`/`jq` (8 tests)

See `README-scripts-tests.md` for detailed bash test documentation.

## Running Tests

### All Tests
```bash
pytest tests/
# or
./run_tests.sh
```

### Specific Test Files
```bash
pytest tests/test_kube_client.py -v
pytest tests/test_utils.py -v
```

### By Test Marker
```bash
# Unit tests only (fast)
pytest -m unit -v

# Integration tests only
pytest -m integration -v

# Exclude slow tests
pytest -m "not slow" -v
```

### With Coverage
```bash
# Coverage disabled by default for speed
# Enable explicitly:
pytest --cov=. --cov-branch --cov-report=html tests/

# View report
open htmlcov/index.html
```

### Single Test
```bash
pytest tests/test_utils.py::TestStateManager::test_persistence -v
```

## Test Markers

Configured in `setup.cfg`:
- `@pytest.mark.unit` - Fast unit tests
- `@pytest.mark.integration` - Integration tests with mocking
- `@pytest.mark.slow` - Slower running tests

## Test Fixtures

### Common Fixtures
- `tmp_path` - Temporary directory (pytest built-in)
- `mock_kube_client` - Mocked KubeClient
- `mock_state_manager` - Mocked StateManager
- `mock_k8s_apis` - Mocked Kubernetes API clients

### Module-Specific Fixtures
Each test file defines fixtures for its module's classes with common configurations.

## Writing Tests

### Unit Test Example
```python
import pytest
from unittest.mock import Mock

@pytest.fixture
def my_fixture():
    return Mock()

@pytest.mark.unit
class TestMyClass:
    def test_my_method(self, my_fixture):
        result = my_fixture.do_something()
        assert result is True
```

### Parameterized Test Example
```python
@pytest.mark.parametrize("version,expected", [
    ("2.12.0", True),
    ("2.11.0", False),
])
def test_version_check(version, expected):
    result = is_acm_version_ge(version, "2.12.0")
    assert result == expected
```

## Configuration

### setup.cfg
- Test discovery: `python_files = test_*.py`
- Test markers: `unit`, `integration`, `slow`
- Coverage: Disabled by default (use `--cov=.` to enable)
- Excluded from coverage: `*/tests/*`, `*/venv/*`

### pytest.ini (via setup.cfg)
```ini
[tool:pytest]
testpaths = tests
markers =
    unit: Unit tests for individual components
    integration: Integration tests with mocked binaries
    slow: Slower running tests
```

## Test Coverage Goals

### Current Coverage (December 2025)
- `lib/__init__.py`: 100%
- `lib/constants.py`: 100%
- `lib/exceptions.py`: 100%
- `lib/validation.py`: 98%
- `lib/rbac_validator.py`: 90%
- `lib/waiter.py`: 90%
- `modules/backup_schedule.py`: 90%
- `lib/utils.py`: 84%
- `modules/primary_prep.py`: 81%
- `modules/activation.py`: 76%
- `modules/decommission.py`: 76%
- `modules/finalization.py`: 69%
- `lib/kube_client.py`: 60%
- `modules/post_activation.py`: 54%
- `modules/preflight_validators.py`: 42%
- `modules/preflight.py`: 17%

### Priority Areas for Improvement
- [ ] `modules/preflight.py` - Test `PreflightValidator.validate_all()` orchestration
- [ ] `modules/preflight_validators.py` - Add tests for VersionValidator, HubComponentValidator, BackupValidator, PassiveSyncValidator
- [ ] `modules/post_activation.py` - Test klusterlet reconnection logic
- [ ] `lib/kube_client.py` - Test configmap operations (get, create, patch, delete)
- [ ] `modules/finalization.py` - Test old hub handling and backup collision fix

### Future Enhancements
- [ ] Integration tests for multi-step workflows
- [ ] Performance benchmarking tests
- [ ] Additional edge case coverage

## Continuous Integration

Tests are designed for CI/CD integration:
- Execution time (~90-95 seconds for full suite)
- No external dependencies (all mocked)
- Clear pass/fail signals
- Detailed error reporting

### CI Example
```yaml
- name: Run tests
  run: |
    python -m pytest tests/ -v --junitxml=test-results.xml
    
- name: Run tests with coverage
  run: |
    python -m pytest tests/ --cov=. --cov-report=xml
```

## Troubleshooting

### Tests Hanging
- **Cause**: Coverage tracing adds significant overhead
- **Solution**: Disable coverage with `--no-cov` flag
- **Note**: Coverage is disabled by default in setup.cfg

### Import Errors
- **Cause**: Module import issues or missing dependencies
- **Solution**: Ensure venv is activated and requirements installed:
  ```bash
  source .venv/bin/activate
  pip install -r requirements-dev.txt
  ```

### Mock Issues
- **Symptom**: `'Mock' object has no attribute X`
- **Solution**: Configure mock return values properly:
  ```python
  mock_client.method.return_value = expected_value
  ```

## Best Practices

1. **Use pytest fixtures** instead of unittest setUp/tearDown
2. **Use assert statements** instead of self.assertEqual()
3. **Parameterize tests** to reduce code duplication
4. **Mock external dependencies** (Kubernetes API, shell commands)
5. **Test both success and failure paths**
6. **Use descriptive test names** that explain what is being tested
7. **Group related tests** in classes
8. **Mark tests** with appropriate markers (unit/integration/slow)

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest Fixtures](https://docs.pytest.org/en/stable/fixture.html)
- [pytest Parametrization](https://docs.pytest.org/en/stable/parametrize.html)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)
