# Contributing to ACM Switchover Automation

Thank you for considering contributing to the ACM Switchover Automation project!

## Getting Started

Before writing anything, read [`AGENTS.md`](AGENTS.md) and the governing issue or spec for
the work. `AGENTS.md` owns the mandatory start gate, the authority hierarchy, the protected-file
policy, and the verification matrix; this guide only covers contributor mechanics.

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/rh-acm-switchover.git`
3. Branch from `ansible`, which is the primary development branch — not `main`:
   ```bash
   git fetch origin ansible
   git checkout -b feature/your-feature-name origin/ansible
   ```
4. Use an isolated branch or git worktree for implementation, so independent validation runs
   against a stable tree.
5. Set up the development environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```

The repository defaults to `.venv`, and `./run_tests.sh` will reuse an active virtualenv when possible.

## Development Guidelines

### Code Style

- Follow PEP 8 Python style guide
- Use meaningful variable and function names
- Add docstrings to all functions and classes
- Keep functions focused and single-purpose
- Maximum line length: 120 characters — this matches CI. See `setup.cfg` and the `black`
  invocation in `.github/workflows/ci-cd.yml`.

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

### Routing a Change to Its Owner

Find the owner before writing code. Editing the wrong layer is the most common source of
review churn.

| Change | Owner |
| --- | --- |
| CLI, input, and path validation | `lib/validation.py` |
| Python preflight checks | `modules/preflight/` plus `modules/preflight_coordinator.py` and `modules/preflight/reporter.py` |
| Python phase behaviour | The owning phase module under `modules/` |
| Python flow, dispatch, and completed/failed-state behaviour | `lib/workflow.py` and `lib/operation_runners.py` |
| Cross-phase run facts | `lib/run_record.py` (the `RunRecord` facade) — never raw state config keys |
| Ansible behaviour | The owning role, module, `module_utils`, or action plugin |
| Release checks | `tests/release/checks/` and the framework contracts |
| Lab-controller safety | `tests/release/lab_controller/` |
| Parity behaviour | Parity fixtures, parity tests, and the parity authority documents |

Preflight checks live in the modular `modules/preflight/` package — `backup_validators.py`,
`cluster_validators.py`, `namespace_validators.py`, and `version_validators.py`, each building on
`base_validator.py`. Add a check to the module matching its subject, and let
`modules/preflight_coordinator.py` orchestrate it and `modules/preflight/reporter.py` render it.

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

### Dry-Run and Check-Mode Behaviour

Dry-run is a property of the client layer, not something each call site re-implements.
`lib/kube_client.py` honours dry-run centrally, dry-run orchestration captures and restores a
full `StateManager` snapshot after the run, and paths that cannot prove safety fail closed.

Route mutations through `KubeClient` so this behaviour applies:

```python
# Good - dry-run, retry, and state-snapshot behaviour all apply
self.client.patch_custom_resource(...)

# Bad - bypasses the client contract entirely
self.custom_api.patch_namespaced_custom_object(...)
```

Do not add local `if self.dry_run: return {}` guards to new call sites. A hand-rolled guard
returns a fabricated result that later phases may treat as a real observation, which is exactly
the failure the central contract prevents. If a genuinely new operation needs dry-run support,
add it to `KubeClient` alongside the existing operations so every caller inherits it.

A dry-run or check-mode pass proves that the planned actions parse and that validation accepts
the inputs. It is not evidence of live behaviour and never substitutes for certification
evidence.

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

1. **Run the default verification path:**
   ```bash
   ./run_tests.sh
   ```

2. **Validate syntax when narrowing failures:**
   ```bash
   python -m py_compile acm_switchover.py lib/*.py modules/*.py
   ```

3. **Test dry-run mode** (`--method` is required unless using `--setup`, `--restore-only`, or
   `--argocd-resume-only`):
   ```bash
   python acm_switchover.py --dry-run \
     --primary-context test-primary \
     --secondary-context test-secondary \
     --method passive
   ```

4. **Test validate-only:**
   ```bash
   python acm_switchover.py --validate-only \
     --primary-context test-primary \
     --secondary-context test-secondary \
     --method passive
   ```

5. **Run collection tests when touching the collection.** `PYTHONPATH=.` is part of the
   command — without it the collection imports fail before any test runs:
   ```bash
   PYTHONPATH=. python -m pytest ansible_collections/tomazb/acm_switchover/tests/unit/ -q
   ```

   Collection unit tests are one surface of several. See
   [the testing guide](docs/development/testing.md) for the full gate inventory and for which
   surfaces your change requires.

6. **Test in non-production environment** (if possible)

7. **Verify idempotency:**
   - Run twice
   - Second run should skip all completed steps
   - Verify state file updated correctly

### Documentation

When adding features:

1. **Update README.md** - Add to feature list if significant
2. **Update docs/operations/usage.md** - Add usage examples
3. **Update docs/operations/quickref.md** - Add commands if new flags
4. **Update docs/development/architecture.md** - Explain design decisions
5. **Update collection docs** - Refresh `docs/ansible-collection/` or `ansible_collections/.../docs/` when collection behavior changes
6. **Update CHANGELOG.md** - Add entries under `[Unreleased]`
7. **Add inline comments** - Explain complex logic
8. **Update docstrings** - Document function behavior

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
3. **Update CHANGELOG.md**
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

## Finding Work

Work starts from a governing issue or spec, so browse the
[issue tracker](https://github.com/tomazb/rh-acm-switchover/issues) rather than an inline
wishlist. Open an issue first if what you want to build does not have one.

## Questions?

- Open an issue for discussion
- Review existing issues and PRs
- Check docs/development/architecture.md for design context

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
