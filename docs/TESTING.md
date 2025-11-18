# ACM Switchover Automation - Testing Guide

## Overview

This document describes the testing strategy, test structure, and how to run tests for the ACM Switchover Automation project.

## Test Structure

```
tests/
├── __init__.py
├── test_utils.py          # Tests for lib/utils.py
├── test_kube_client.py    # Tests for lib/kube_client.py
└── test_preflight.py      # Tests for modules/preflight.py
```

## Running Tests

### Quick Test Run

```bash
./run_tests.sh
```

This script will:
1. Set up a virtual environment (if needed)
2. Install dependencies
3. Run unit tests with coverage
4. Run code quality checks (flake8, pylint, black, isort, mypy)
5. Run security scans (bandit, safety)
6. Validate Python syntax

### Manual Test Execution

#### Install Test Dependencies

```bash
pip install -r requirements-dev.txt
```

#### Run All Tests

```bash
python -m pytest tests/ -v
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
python -m pytest tests/ -v --cov=. --cov-report=html --cov-report=term
```

View HTML coverage report:
```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

## Test Coverage

### Current Coverage

- **lib/utils.py**: StateManager, Phase enum, helper functions
- **lib/kube_client.py**: KubeClient initialization, CRUD operations, dry-run mode
- **modules/preflight.py**: All validation checks

### Coverage Goals

- **Target**: 80%+ line coverage
- **Critical paths**: 100% coverage for data protection logic
- **Current status**: See coverage report for details

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

Check formatting:
```bash
black --check --line-length 120 .
```

Auto-format:
```bash
black --line-length 120 .
```

### isort (Import Sorting)

Check imports:
```bash
isort --check-only --profile black --line-length 120 .
```

Auto-sort:
```bash
isort --profile black --line-length 120 .
```

### MyPy (Type Checking)

```bash
mypy acm_switchover.py lib/ modules/ --ignore-missing-imports
```

## Security Testing

### Bandit (Static Security Analysis)

```bash
bandit -r . -ll
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
- ✅ Unit tests (Python 3.8-3.12)
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

Test against real clusters without making changes:

```bash
python acm_switchover.py switchover \
  --primary-context prod-hub \
  --secondary-context dr-hub \
  --method passive-sync \
  --dry-run
```

### Validate-Only Mode

Run pre-flight checks only:

```bash
python acm_switchover.py switchover \
  --primary-context prod-hub \
  --secondary-context dr-hub \
  --method passive-sync \
  --validate-only
```

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

- [ ] Integration tests with real Kubernetes clusters
- [ ] End-to-end tests with test fixtures
- [ ] Performance benchmarks
- [ ] Chaos engineering tests
- [ ] Additional module coverage (activation, finalization)
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Questions and Support

- Check existing tests for examples
- Review test output carefully
- Use verbose mode: `pytest -vv`
- Check CI/CD logs for failures

---

**Last Updated**: November 18, 2025
