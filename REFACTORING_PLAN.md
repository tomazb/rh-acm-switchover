# Refactoring Plan for ACM Switchover

## Overview

This refactoring plan addresses the code quality improvements identified in the deep code smell analysis. The plan focuses on maintaining the excellent architecture while improving maintainability and reducing code duplication.

## Branch: `swe-refactoring`

## Priority Items

### 1. High Priority - Module Decomposition

**Target**: `modules/preflight_validators.py` (1,281 lines)

**Problem**: The module is too large and handles multiple responsibilities

**Solution**: Split into focused modules:

```
modules/preflight/
├── __init__.py
├── base_validator.py          # Base validator classes
├── cluster_validators.py      # Cluster-related validations
├── backup_validators.py       # Backup and restore validations
├── namespace_validators.py    # Namespace and resource validations
├── rbac_validators.py         # RBAC permission validations
├── version_validators.py     # Version and compatibility validations
└── reporter.py               # Validation reporting logic
```

**Benefits**:
- Easier to maintain and test
- Clear separation of concerns
- Better code organization

### 2. High Priority - Exception Handling Decorator

**Target**: Repetitive API exception handling patterns (20+ instances)

**Problem**: Code duplication in exception handling across `KubeClient` methods

**Current Pattern**:
```python
except ApiException as e:
    if e.status == 404:
        return None
    if is_retryable_error(e):
        raise
    logger.error("Failed to %s: %s", operation, e)
    raise
```

**Solution**: Create `@handle_api_exceptions` decorator:

```python
def handle_api_exception(
    return_none_on_404: bool = True,
    log_operation: str = None,
    reraise_retryable: bool = True
):
    """Decorator for consistent API exception handling."""
```

**Benefits**:
- Eliminates code duplication
- Consistent error handling behavior
- Easier to modify exception handling logic

## Implementation Plan

### Phase 1: Exception Handling Decorator

1. **Create decorator in `lib/decorators.py`**
   - Implement `@handle_api_exception`
   - Add comprehensive tests
   - Update `KubeClient` methods to use decorator

2. **Files to modify**:
   - `lib/kube_client.py` (20+ method updates)
   - `tests/test_kube_client.py` (add decorator tests)

### Phase 2: Module Decomposition

1. **Create new module structure**
   ```bash
   mkdir -p modules/preflight
   touch modules/preflight/__init__.py
   ```

2. **Split validators by domain**:
   - Move cluster validators → `cluster_validators.py`
   - Move backup validators → `backup_validators.py`
   - Move namespace validators → `namespace_validators.py`
   - Move RBAC validators → `rbac_validators.py`
   - Move version validators → `version_validators.py`
   - Move reporter logic → `reporter.py`

3. **Update imports**:
   - `modules/preflight.py` (update imports)
   - `tests/test_preflight.py` (update test imports)

4. **Maintain backward compatibility**:
   - Keep `modules/preflight_validators.py` as compatibility layer
   - Add deprecation warnings

### Phase 3: Testing and Validation

1. **Ensure all tests pass**
   - Run full test suite
   - Verify no functionality changes

2. **Add new tests**
   - Test exception handling decorator
   - Test individual validator modules

3. **Integration testing**
   - Verify end-to-end functionality
   - Test with different ACM versions

## Detailed Implementation Steps

### Step 1: Exception Handling Decorator

#### 1.1 Create `lib/decorators.py`
```python
"""
Common decorators for ACM switchover.
"""

import logging
from functools import wraps
from typing import Any, Callable, Optional

from kubernetes.client.rest import ApiException

from .exceptions import TransientError

logger = logging.getLogger("acm_switchover")

def handle_api_exception(
    return_none_on_404: bool = True,
    log_operation: str = None,
    reraise_retryable: bool = True
) -> Callable:
    """
    Decorator for consistent API exception handling.
    
    Args:
        return_none_on_404: Return None for 404 errors
        log_operation: Operation description for logging
        reraise_retryable: Re-raise retryable errors for tenacity
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except ApiException as e:
                if e.status == 404 and return_none_on_404:
                    return None
                
                # Check if retryable (let tenacity handle)
                if reraise_retryable and _is_retryable_error(e):
                    raise
                
                # Log and re-raise non-retryable errors
                operation = log_operation or func.__name__
                logger.error("Failed to %s: %s", operation, e)
                raise
        return wrapper
    return decorator

def _is_retryable_error(exception: ApiException) -> bool:
    """Check if API exception is retryable."""
    return 500 <= exception.status < 600 or exception.status == 429
```

#### 1.2 Update KubeClient methods
Replace repetitive exception handling with decorator:

```python
@handle_api_exception(log_operation="get namespace")
def get_namespace(self, name: str) -> Optional[Dict]:
    # Validate namespace name before making API call
    InputValidator.validate_kubernetes_namespace(name)
    ns = self.core_v1.read_namespace(name)
    return ns.to_dict()
```

### Step 2: Module Decomposition

#### 2.1 Create base validator
```python
# modules/preflight/base_validator.py
"""Base validator classes."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

class BaseValidator(ABC):
    """Base class for all validators."""
    
    def __init__(self, reporter: 'ValidationReporter'):
        self.reporter = reporter
    
    @abstractmethod
    def validate(self) -> bool:
        """Perform validation checks."""
        pass
    
    def add_result(self, name: str, passed: bool, message: str, critical: bool = False):
        """Add validation result to reporter."""
        self.reporter.add_result(name, passed, message, critical)
```

#### 2.2 Split validators by domain
Example for cluster validators:

```python
# modules/preflight/cluster_validators.py
"""Cluster-related validation checks."""

from typing import Dict, Tuple
from lib.kube_client import KubeClient
from .base_validator import BaseValidator

class ClusterDeploymentValidator(BaseValidator):
    """Validates cluster deployment resources."""
    
    def __init__(self, reporter: 'ValidationReporter', primary: KubeClient, secondary: KubeClient):
        super().__init__(reporter)
        self.primary = primary
        self.secondary = secondary
    
    def validate(self) -> bool:
        """Validate cluster deployments."""
        # Implementation moved from original file
        pass
```

#### 2.3 Update main preflight module
```python
# modules/preflight.py
"""Pre-flight validation module for ACM switchover."""

from .preflight_validators import (
    # Keep existing imports for backward compatibility
    # Will be deprecated in future version
)

# New modular imports
from .preflight.cluster_validators import ClusterDeploymentValidator
from .preflight.backup_validators import BackupValidator
# ... other new imports

class PreflightValidator:
    """Coordinates modular pre-flight validation checks."""
    
    def __init__(self, ...):
        # Use new modular validators
        self.cluster_validator = ClusterDeploymentValidator(...)
        # ... other validators
```

## Testing Strategy

### New Test Files
- `tests/test_decorators.py` - Test exception handling decorator
- `tests/test_preflight_modular.py` - Test new modular structure
- Update existing tests to work with new structure

### Test Coverage Goals
- Maintain current 328 tests
- Add tests for new decorator (5-10 tests)
- Add tests for individual validator modules

## Risk Assessment

### Low Risk
- Exception handling decorator (pure refactoring)
- Module decomposition (maintains interfaces)

### Mitigation Strategies
- Comprehensive test suite
- Backward compatibility layer
- Incremental deployment

## Success Criteria

1. **All tests pass** - No functionality regression
2. **Code reduction** - Eliminate repetitive exception handling
3. **Improved maintainability** - Smaller, focused modules
4. **Clean architecture** - Better separation of concerns

## Timeline Estimate

- **Phase 1** (Exception decorator): 2-3 days
- **Phase 2** (Module decomposition): 3-4 days  
- **Phase 3** (Testing/validation): 1-2 days
- **Total**: 6-9 days

## Rollback Plan

If issues arise:
1. Keep original `preflight_validators.py` as backup
2. Gradual rollback possible (per phase)
3. Git branch provides clean rollback point

## Next Steps

1. Create `lib/decorators.py` with exception handling decorator
2. Update `KubeClient` methods to use decorator
3. Create new `modules/preflight/` structure
4. Split validators by domain
5. Update tests and verify functionality
6. Create PR for review

This refactoring maintains the excellent architecture while improving code maintainability and reducing duplication.
