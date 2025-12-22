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

### Virtual Environment Usage
- Prefer activating an existing virtual environment before running tooling.
- The test runner (`run_tests.sh`) will detect an active `$VIRTUAL_ENV`, otherwise it will try `.venv/` first, then `venv/`, and create `.venv/` if none exist.
- Recommended setup:
    - Create `.venv` once: `python3 -m venv .venv`
    - Activate: `source .venv/bin/activate`
    - Then run: `./run_tests.sh`

## Common Tasks

**Adding a new constant**: Add to `lib/constants.py`, import where needed
**Adding a KubeClient method**: Add `@retry_api_call` for API calls, return `Optional[Dict]` for gets, handle 404→None
**New workflow step**: Follow idempotent pattern with `state.is_step_completed()` / `mark_step_completed()`
**New validation**: Add to `lib/validation.py` with `InputValidator` static method

### CLI Validation Guidance (Contributor note)
- CLI validation is implemented in `lib/validation.py` (class `InputValidator`). When changing existing arguments or adding new ones, update the validator accordingly and add tests in `tests/test_validation.py`.
- Current important cross-argument rules (enforced by `InputValidator.validate_all_cli_args`):
    - `--secondary-context` is required for switchover operations unless `--decommission` is set.
    - `--non-interactive` can only be used together with `--decommission` (it's disallowed for normal switchovers).
- If you change these rules, update `docs/reference/validation-rules.md`, `docs/operations/usage.md`, and `docs/operations/quickref.md` to match.

## Files to Know

- `CHANGELOG.md` - Update `[Unreleased]` section for changes
- `docs/development/architecture.md` - Design decisions and module descriptions
- `lib/constants.py` - All magic strings centralized here (Python)
- `scripts/constants.sh` - All magic strings centralized here (Bash)
- `setup.cfg` - pytest, flake8, mypy configuration
- `pyproject.toml` - Python packaging, dependencies, console scripts
- `packaging/README.md` - Packaging formats, build process, state dir defaults
- `packaging/common/VERSION` - Canonical version source

## Version Management

**IMPORTANT**: Python and Bash versions MUST always be in sync. When making changes to either Python or Bash code, update BOTH version files to the same version.

### Version Locations

| Component | File | Variables |
|-----------|------|-----------|
| **Canonical Source** | `packaging/common/VERSION`, `VERSION_DATE` | Single source of truth |
| **Bash Scripts** | `scripts/constants.sh` | `SCRIPT_VERSION`, `SCRIPT_VERSION_DATE` |
| **Python Tool** | `lib/__init__.py` | `__version__`, `__version_date__` |
| **README** | `README.md` | Version at top of file |
| **setup.cfg** | `setup.cfg` | `version` in `[metadata]` |
| **Containerfile** | `container-bootstrap/Containerfile` | `version` label |
| **Helm Charts** | `packaging/helm/*/Chart.yaml`, `deploy/helm/*/Chart.yaml` | `version`, `appVersion` |

**Version sync tooling**:
- `./packaging/common/version-bump.sh <version> [date]` - Update all version sources
- `./packaging/common/validate-versions.sh` - Verify all sources are in sync (used by CI)

### Bash Scripts Version

Location: `scripts/constants.sh`

```bash
export SCRIPT_VERSION="X.Y.Z"
export SCRIPT_VERSION_DATE="YYYY-MM-DD"
```

**When to bump**:
- **PATCH (X.Y.Z → X.Y.Z+1)**: Bug fixes, minor improvements, documentation updates
- **MINOR (X.Y.Z → X.Y+1.0)**: New checks, new features, significant improvements
- **MAJOR (X.Y.Z → X+1.0.0)**: Breaking changes to script behavior or output format

**Files that use this version**:
- `scripts/preflight-check.sh` - displays via `print_script_version`
- `scripts/postflight-check.sh` - displays via `print_script_version`
- `scripts/discover-hub.sh` - displays via `print_script_version`

### Python Tool Version

Location: `lib/__init__.py`

```python
__version__ = "X.Y.Z"
__version_date__ = "YYYY-MM-DD"
```

**When to bump**: Same rules as bash scripts (PATCH/MINOR/MAJOR).

**Files that use this version**:
- `acm_switchover.py` - displays at startup: `ACM Hub Switchover Automation vX.Y.Z (YYYY-MM-DD)`
- `lib/utils.py` - stores `tool_version` in state files for troubleshooting
- `check_rbac.py` - can import and display version
- `show_state.py` - can import and display version

### Changelog Updates

Location: `CHANGELOG.md`

1. For new releases, create a new section: `## [X.Y.Z] - YYYY-MM-DD`
2. For ongoing work, add entries under `## [Unreleased]`
3. Group changes by: `### Added`, `### Changed`, `### Fixed`, `### Removed`

### Version Update Checklist

**Preferred method** (uses version-bump tooling):
```bash
./packaging/common/version-bump.sh 1.6.0 2025-01-15
./packaging/common/validate-versions.sh
# Add changelog entry
git tag v1.6.0 && git push origin v1.6.0
```

When making script changes:
1. [ ] Update `SCRIPT_VERSION` in `scripts/constants.sh`
2. [ ] Update `SCRIPT_VERSION_DATE` to current date
3. [ ] Update version in `README.md` (top of file)
4. [ ] Add changelog entry in `CHANGELOG.md`
5. [ ] Update `scripts/README.md` if new features/checks added
6. [ ] Create and push a git tag for the new version (e.g., `git tag vX.Y.Z && git push origin vX.Y.Z`)

When making Python code changes:
1. [ ] Run `./packaging/common/version-bump.sh <new-version>` to update all sources
2. [ ] Verify with `./packaging/common/validate-versions.sh`
3. [ ] Add changelog entry in `CHANGELOG.md`
4. [ ] Create and push a git tag for the new version (e.g., `git tag vX.Y.Z && git push origin vX.Y.Z`)

**Alternative (manual updates)**:
1. [ ] Update `packaging/common/VERSION` and `VERSION_DATE`
2. [ ] Update `__version__` in `lib/__init__.py`
3. [ ] Update `SCRIPT_VERSION` in `scripts/constants.sh`
4. [ ] Update version in `README.md`, `setup.cfg`, Containerfile, Helm charts
5. [ ] Run `./packaging/common/validate-versions.sh` to verify
