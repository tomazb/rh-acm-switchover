# Tests Directory

This directory contains the unit test suite for the ACM Switchover Automation project.

## Test Files

- **`test_utils.py`** - Tests for `lib/utils.py`
  - StateManager class (load, save, reset, step tracking)
  - Phase enum
  - Version comparison utilities
  - Logging setup

- **`test_kube_client.py`** - Tests for `lib/kube_client.py`
  - KubeClient initialization
  - Custom resource operations (get, list, patch, create, delete)
  - Deployment and StatefulSet scaling
  - Pod management and waiting
  - Dry-run mode functionality

- **`test_preflight.py`** - Tests for `modules/preflight.py`
  - Namespace validation
  - ACM version detection
  - OADP operator checks
  - Backup status validation
  - ClusterDeployment preserveOnDelete validation (critical)
  - Passive sync restore verification
  - Observability detection

## Running Tests

### Quick Start

```bash
# From project root
./run_tests.sh
```

### Individual Test Files

```bash
# Run specific test file
python -m pytest tests/test_utils.py -v

# Run specific test class
python -m pytest tests/test_utils.py::TestStateManager -v

# Run specific test
python -m pytest tests/test_utils.py::TestStateManager::test_initial_state -v
```

### With Coverage

```bash
python -m pytest tests/ -v --cov=. --cov-report=html
open htmlcov/index.html
```

## Test Structure

All tests follow this pattern:

```python
import unittest
from unittest.mock import MagicMock, patch

class TestMyClass(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        # Initialize mocks and test data
    
    def tearDown(self):
        """Clean up after tests."""
        # Remove temp files, etc.
    
    def test_feature_success(self):
        """Test successful scenario."""
        # Arrange
        # Act
        # Assert
    
    def test_feature_failure(self):
        """Test failure scenario."""
        # Test error handling
```

## Mocking Strategy

- **Kubernetes API**: Always mocked to avoid real cluster dependencies
- **File system**: Temporary directories used for state files
- **External calls**: All network and API calls are mocked

Example:
```python
@patch('lib.kube_client.client.CoreV1Api')
def test_namespace_exists(self, mock_api):
    mock_instance = mock_api.return_value
    # Configure mock behavior
    # Test the function
```

## Coverage Goals

- **Overall**: 80%+ line coverage
- **Critical paths**: 100% coverage (data protection, validation)
- **State management**: 100% coverage
- **Error handling**: All error paths tested

## Adding New Tests

1. Create test file: `test_<module>.py`
2. Import module under test
3. Create test class inheriting from `unittest.TestCase`
4. Add setUp/tearDown methods if needed
5. Write test methods (must start with `test_`)
6. Run tests to verify
7. Check coverage

## Test Dependencies

Install with:
```bash
pip install -r requirements-dev.txt
```

Includes:
- pytest
- pytest-cov
- pytest-mock
- coverage

## CI/CD Integration

Tests run automatically on:
- Every push to main/develop
- Every pull request
- Python versions: 3.8, 3.9, 3.10, 3.11, 3.12

See `.github/workflows/ci-cd.yml` for details.

## Troubleshooting

### Import Errors

Ensure Python path includes parent directory:
```python
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
```

### Kubernetes Client Errors

Mock the kubernetes client:
```python
with patch('lib.kube_client.config'):
    client = KubeClient()
```

### State File Conflicts

Use temporary directories:
```python
import tempfile
self.temp_dir = tempfile.mkdtemp()
self.state_file = os.path.join(self.temp_dir, "test-state.json")
```

## Future Tests

Planned additions:
- [ ] tests/test_primary_prep.py
- [ ] tests/test_activation.py
- [ ] tests/test_post_activation.py
- [ ] tests/test_finalization.py
- [ ] tests/test_decommission.py
- [ ] Integration tests with real clusters
- [ ] End-to-end tests

## Documentation

See [CONTRIBUTING.md](../docs/CONTRIBUTING.md) for details.

---

**Note**: Tests use mocks and do not require access to real Kubernetes clusters.
