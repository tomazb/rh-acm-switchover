# Plan: Add `@api_call` Decorator

Introduce a new decorator to reduce repetitive `except ApiException as e:` handling patterns found in ~20+ locations across the codebase, addressing the external review's recommendation.

## Design Decisions

| Consideration | Decision |
|---------------|----------|
| Scope | Simple cases only; keep ~3-4 custom handlers as-is |
| Decorator composition | Combine `retry_api_call` + `handle_api_exception` into single `@api_call` |
| Resource descriptions | Static strings with method-name fallback |

## Implementation Steps

### Step 1: Add `@api_call` decorator to `lib/kube_client.py`

Add near the existing `retry_api_call` and `is_retryable_error` functions (~line 50-80).

```python
def api_call(
    not_found_value: Any = None,
    log_on_error: bool = True,
    resource_desc: Optional[str] = None,
) -> Callable:
    """
    Combined decorator for Kubernetes API calls with retry and exception handling.
    
    Combines retry logic (5xx/429 → exponential backoff) with standard exception handling:
    - 404 → return not_found_value
    - Retryable errors → re-raise for tenacity
    - Other errors → log and re-raise
    
    Args:
        not_found_value: Value to return when resource not found (404)
        log_on_error: Whether to log non-retryable errors before re-raising
        resource_desc: Description for error messages (defaults to method name)
    
    Usage:
        @api_call(not_found_value=None)
        def get_namespace(self, name: str) -> Optional[Dict]:
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        desc = resource_desc or func.__name__.replace("_", " ")
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except ApiException as e:
                if e.status == 404:
                    return not_found_value
                if is_retryable_error(e):
                    raise
                if log_on_error:
                    logger.error("Failed to %s: %s", desc, e)
                raise
        
        # Apply retry decorator
        return retry_api_call(wrapper)
    return decorator
```

### Step 2: Refactor `KubeClient` getter methods

Apply decorator to methods that follow the "return None on 404" pattern:

- `get_namespace()` 
- `read_configmap()`
- `get_secret()`
- `get_custom_resource()`
- `list_custom_resources()`

Example refactor:
```python
# Before
@retry_api_call
def get_namespace(self, name: str) -> Optional[Dict]:
    try:
        result = self.core_api.read_namespace(name=name)
        return result.to_dict()
    except ApiException as e:
        if e.status == 404:
            return None
        if is_retryable_error(e):
            raise
        logger.error("Failed to get namespace %s: %s", name, e)
        raise

# After
@api_call(not_found_value=None)
def get_namespace(self, name: str) -> Optional[Dict]:
    result = self.core_api.read_namespace(name=name)
    return result.to_dict()
```

### Step 3: Refactor `KubeClient` deletion methods

Apply decorator with `not_found_value=False` to methods that return boolean on 404:

- `delete_custom_resource()`

### Step 4: Keep custom handlers as-is

Do NOT refactor these locations that have fallback logic:

- `modules/post_activation.py`: Bootstrap secret fallback (tries `bootstrap-hub-kubeconfig` on 404)
- `modules/primary_prep.py`: Thanos compactor optional handling (logs warning on 404)

### Step 5: Add unit tests

Add tests in `tests/test_kube_client.py` for the new decorator:

```python
class TestApiCallDecorator:
    """Tests for the @api_call decorator."""

    def test_returns_not_found_value_on_404(self):
        """Decorator returns not_found_value when ApiException with 404."""
        ...

    def test_reraises_retryable_errors(self):
        """Decorator re-raises 5xx errors for tenacity to handle."""
        ...

    def test_logs_and_reraises_non_retryable_errors(self):
        """Decorator logs non-retryable errors before re-raising."""
        ...

    def test_uses_method_name_as_default_resource_desc(self):
        """Decorator derives resource_desc from method name if not provided."""
        ...
```

## Expected Impact

- Reduces ~60+ lines of repetitive try/except blocks
- Consolidates two decorators (`@retry_api_call` + exception handling) into one
- Improves consistency of error messages
- Makes `KubeClient` methods more concise and readable

## Files Changed

- `lib/kube_client.py` - Add decorator, refactor ~10 methods
- `tests/test_kube_client.py` - Add decorator tests
