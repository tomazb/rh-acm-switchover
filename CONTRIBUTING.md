# Contributing to ACM Switchover Automation

Thank you for considering contributing to the ACM Switchover Automation project!

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/rh-acm-switchover.git`
3. Create a feature branch: `git checkout -b feature/your-feature-name`
4. Set up development environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

## Development Guidelines

### Code Style

- Follow PEP 8 Python style guide
- Use meaningful variable and function names
- Add docstrings to all functions and classes
- Keep functions focused and single-purpose
- Maximum line length: 100 characters

**Example:**
```python
def check_backup_status(self):
    """
    Check backup status on primary hub.
    
    Verifies that the latest backup has completed successfully
    and no backups are currently in progress.
    
    Raises:
        ValidationError: If backup validation fails
    """
    # Implementation here
```

### Maintaining Idempotency

**Critical rule:** Every operation must be idempotent and resumable.

**Pattern to follow:**
```python
def prepare(self):
    if not self.state.is_step_completed("step_name"):
        self._execute_step()
        self.state.mark_step_completed("step_name")
    else:
        logger.info("Step already completed: step_name")
```

**Do:**
- ✅ Check current state before modifying
- ✅ Use conditional patches/creates
- ✅ Mark steps as completed after success
- ✅ Handle already-completed gracefully

**Don't:**
- ❌ Assume resources don't exist
- ❌ Execute without checking state
- ❌ Mark steps complete before execution
- ❌ Fail if resource already in desired state

### Adding New Validation Checks

1. Add method to `PreflightValidator` class
2. Follow naming convention: `_check_<what>_<where>()`
3. Use `self.add_result()` to record results
4. Set `critical=True` for blocking validations

**Example:**
```python
def _check_custom_resource(self):
    """Check custom resource exists."""
    try:
        resource = self.primary.get_custom_resource(
            group="example.io",
            version="v1",
            plural="customresources",
            name="required-resource"
        )
        
        if resource:
            self.add_result(
                "Custom resource check",
                True,
                "resource exists",
                critical=True
            )
        else:
            self.add_result(
                "Custom resource check",
                False,
                "resource not found",
                critical=True
            )
    except Exception as e:
        self.add_result(
            "Custom resource check",
            False,
            f"error checking resource: {e}",
            critical=True
        )
```

### Adding New Switchover Steps

1. Identify which module owns the step (prep, activation, etc.)
2. Add private method: `_execute_new_step()`
3. Add step to main workflow method
4. Use state tracking

**Example:**
```python
# In modules/primary_prep.py

def prepare(self):
    # Existing steps...
    
    # New step
    if not self.state.is_step_completed("new_preparation_step"):
        self._execute_new_preparation_step()
        self.state.mark_step_completed("new_preparation_step")
    else:
        logger.info("Step already completed: new_preparation_step")

def _execute_new_preparation_step(self):
    """Execute new preparation step."""
    logger.info("Executing new preparation step...")
    
    # Your logic here
    
    logger.info("New preparation step completed")
```

### Error Handling

**Always:**
- Catch specific exceptions when possible
- Provide context in error messages
- Distinguish expected errors (404) from failures
- Log errors before raising

**Example:**
```python
try:
    result = self.client.delete_custom_resource(...)
except ApiException as e:
    if e.status == 404:
        logger.debug("Resource already deleted (expected)")
        return False
    else:
        logger.error(f"Failed to delete resource: {e}")
        raise
except Exception as e:
    logger.error(f"Unexpected error deleting resource: {e}")
    raise
```

### Dry-Run Support

All Kubernetes operations should respect dry-run mode.

**Use KubeClient methods** - they handle dry-run automatically:
```python
# Good - dry-run handled automatically
self.client.patch_custom_resource(...)

# Bad - bypasses dry-run
self.custom_api.patch_namespaced_custom_object(...)
```

If adding new KubeClient methods:
```python
def new_operation(self, ...):
    if self.dry_run:
        logger.info(f"[DRY-RUN] Would execute operation")
        return {}  # Return safe mock result
    
    # Actual operation
    return self.api.execute_operation(...)
```

### Logging

**Use appropriate log levels:**
- `logger.debug()` - Detailed diagnostic info
- `logger.info()` - Progress and success messages
- `logger.warning()` - Non-critical issues
- `logger.error()` - Errors that need attention

**Be descriptive:**
```python
# Good
logger.info(f"Scaled {deployment_name} to {replicas} replicas")

# Bad
logger.info("Scaled deployment")
```

### Testing

Before submitting a PR:

1. **Validate syntax:**
   ```bash
   python -m py_compile acm_switchover.py lib/*.py modules/*.py
   ```

2. **Test dry-run mode:**
   ```bash
   python acm_switchover.py --dry-run \
     --primary-context test-primary \
     --secondary-context test-secondary
   ```

3. **Test validate-only:**
   ```bash
   python acm_switchover.py --validate-only \
     --primary-context test-primary \
     --secondary-context test-secondary
   ```

4. **Test in non-production environment** (if possible)

5. **Verify idempotency:**
   - Run twice
   - Second run should skip all completed steps
   - Verify state file updated correctly

### Documentation

When adding features:

1. **Update README.md** - Add to feature list if significant
2. **Update USAGE.md** - Add usage examples
3. **Update QUICKREF.md** - Add commands if new flags
4. **Update ARCHITECTURE.md** - Explain design decisions
5. **Add inline comments** - Explain complex logic
6. **Update docstrings** - Document function behavior

### Commit Messages

Follow conventional commits format:

```
<type>: <description>

[optional body]

[optional footer]
```

**Types:**
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `refactor:` Code refactoring
- `test:` Adding tests
- `chore:` Maintenance tasks

**Examples:**
```
feat: add support for ACM 2.13 backup API changes

fix: handle missing Observability namespace gracefully

docs: add troubleshooting guide for import failures

refactor: extract common validation logic to helper
```

## Pull Request Process

1. **Update documentation** as described above
2. **Test thoroughly** in non-production environment
3. **Update CHANGELOG** (if we add one)
4. **Create PR** with clear description:
   - What does this PR do?
   - Why is this change needed?
   - How was it tested?
   - Any breaking changes?

5. **Address review feedback**
6. **Squash commits** if requested

## Code Review Checklist

Before submitting PR, verify:

- [ ] Code follows PEP 8 style
- [ ] All functions have docstrings
- [ ] Idempotency maintained
- [ ] Error handling implemented
- [ ] Dry-run mode supported
- [ ] Logging at appropriate levels
- [ ] State tracking for new steps
- [ ] Documentation updated
- [ ] Tested in non-production
- [ ] No hardcoded values
- [ ] Commit messages follow convention

## Feature Ideas

Looking for contribution ideas? Consider:

- **Parallel validation checks** - Speed up pre-flight validation
- **Progress bars** - Visual feedback using rich library
- **Notification support** - Email/Slack alerts on completion
- **Metrics collection** - Track switchover duration and success rate
- **Automated testing** - Post-switchover functionality tests
- **Web UI** - Browser-based monitoring interface
- **Multi-hub support** - Batch switchover for multiple hubs
- **Enhanced logging** - Structured logging with JSON output
- **Helm chart** - Deploy as Kubernetes Job

## Questions?

- Open an issue for discussion
- Review existing issues and PRs
- Check ARCHITECTURE.md for design context

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
