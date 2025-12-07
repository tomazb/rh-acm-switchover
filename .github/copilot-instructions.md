# ACM Switchover - AI Agent Instructions

## Project Overview

This is a Python CLI tool for automating Red Hat Advanced Cluster Management (ACM) hub switchover. It orchestrates a phased workflow to migrate from a primary ACM hub to a secondary hub with idempotent execution and comprehensive validation.

## Architecture

**Entry Point**: `acm_switchover.py` - Main orchestrator using `Phase` enum and `StateManager`

**Core Libraries** (`lib/`):
- `kube_client.py` - Kubernetes API wrapper with `@retry_api_call` decorator, validation, dry-run support
- `utils.py` - `StateManager` for idempotent state tracking, `Phase` enum, `@dry_run_skip` decorator
- `constants.py` - Centralized namespaces, timeouts, ACM spec field names
- `exceptions.py` - Hierarchy: `SwitchoverError` → `TransientError`/`FatalError` → `ValidationError`/`ConfigurationError`
- `validation.py` - Input validation with `InputValidator` class and `SecurityValidationError`

**Workflow Modules** (`modules/`):
- `preflight.py` / `preflight_validators.py` - Pre-flight validation checks
- `primary_prep.py` - Pause backups, disable auto-import, scale down Thanos
- `activation.py` - Patch restore resource to activate managed clusters
- `post_activation.py` - Verify cluster connections, fix klusterlet agents
- `finalization.py` - Set up old hub as secondary or prepare for decommission
- `decommission.py` - Remove ACM from old hub

## Phase Flow

The switchover executes phases sequentially, with state tracking for resume capability:

```
INIT → PREFLIGHT → PRIMARY_PREP → ACTIVATION → POST_ACTIVATION → FINALIZATION → COMPLETED
```

| Phase | Module | Key Actions |
|-------|--------|-------------|
| `PREFLIGHT` | `preflight.py` | Validate both hubs, check ACM versions, verify backups |
| `PRIMARY_PREP` | `primary_prep.py` | Pause BackupSchedule, add disable-auto-import annotations, scale Thanos |
| `ACTIVATION` | `activation.py` | Patch restore with `veleroManagedClustersBackupName: latest` |
| `POST_ACTIVATION` | `post_activation.py` | Wait for ManagedClusters to connect, verify klusterlet agents |
| `FINALIZATION` | `finalization.py` | Configure old hub as secondary or prepare for decommission |

Each phase handler checks `state.get_current_phase()` before executing. Failed phases set `Phase.FAILED`.

## Key Patterns

### Idempotent Step Execution
```python
if not self.state.is_step_completed("step_name"):
    self._do_step()
    self.state.mark_step_completed("step_name")
```

### Dry-Run Decorator
```python
@dry_run_skip(message="Would scale deployment", return_value={})
def scale_deployment(self, name, namespace, replicas):
    # Only executes when self.dry_run is False
```

### Exception Hierarchy
- Use `SwitchoverError` for domain-specific workflow errors
- Use `FatalError` for non-recoverable errors (e.g., missing resources)
- Wrapper methods (e.g., `secret_exists`) should NOT have `@retry_api_call` if they call methods that already have it

### Constants Usage
Import from `lib/constants.py` - never hard-code namespaces (`BACKUP_NAMESPACE`, `OBSERVABILITY_NAMESPACE`) or resource names

### KubeClient Pattern
- Methods return `Optional[Dict]` for get operations (None = not found)
- Use `e.status == 404` to check ApiException, not string matching
- Per-instance TLS configuration to avoid global side effects

## Testing

```bash
# Run all tests with coverage
./run_tests.sh

# Quick pytest run
pytest tests/ -v

# Run specific test file
pytest tests/test_kube_client.py -v

# Run with markers
pytest -m unit tests/      # Unit tests only
pytest -m integration tests/  # Integration tests
```

Tests use mocked `KubeClient` - fixture pattern in `tests/conftest.py`. Mock responses should include `resourceVersion` in metadata for patch verification tests.

## Common Tasks

**Adding a new constant**: Add to `lib/constants.py`, import where needed
**Adding a KubeClient method**: Add `@retry_api_call` for API calls, return `Optional[Dict]` for gets, handle 404→None
**New workflow step**: Follow idempotent pattern with `state.is_step_completed()` / `mark_step_completed()`
**New validation**: Add to `lib/validation.py` with `InputValidator` static method

## Files to Know

- `docs/CHANGELOG.md` - Update `[Unreleased]` section for changes
- `docs/ARCHITECTURE.md` - Design decisions and module descriptions
- `lib/constants.py` - All magic strings centralized here
- `setup.cfg` - pytest, flake8, mypy configuration