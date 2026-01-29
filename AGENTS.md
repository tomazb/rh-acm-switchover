# ACM Switchover - AI Agent Instructions

## Project Overview

This is a Python CLI tool for automating Red Hat Advanced Cluster Management (ACM) hub switchover. It orchestrates a phased workflow to migrate from a primary ACM hub to a secondary hub with idempotent execution and comprehensive validation.

## Engineering Principles

- **DRY**: Avoid duplication in code, tests, and documentation; prefer shared helpers or utilities where it makes sense.
- **KISS**: Prefer simple, straightforward solutions over clever or overly abstract designs.
- **YAGNI**: Do not add speculative features or abstractions until they are clearly needed.
- **Fail fast with clear errors**: Detect problems early and surface explicit, actionable error messages.
- **Prefer explicit over implicit**: Make control flow, side effects, and configuration obvious at call sites.
- **Keep changes minimal and localized**: Touch as few files and code paths as possible to implement a change.
- **Respect existing patterns and abstractions**: Align with current architecture and style unless there is a strong reason to refactor.
- **Keep AGENTS.md current**: Update this file when making significant architectural, workflow, module, or CLI changes.

## Architecture

**Entry Point**: `acm_switchover.py` - Main orchestrator using `Phase` enum and `StateManager`

**Core Libraries** (`lib/`):
- `kube_client.py` - Kubernetes API wrapper with `@retry_api_call` decorator, validation, dry-run support
- `utils.py` - `StateManager` for idempotent state tracking, `Phase` enum, `@dry_run_skip` decorator
- `constants.py` - Centralized namespaces, timeouts, ACM spec field names
- `exceptions.py` - Hierarchy: `SwitchoverError` → `TransientError`/`FatalError` → `ValidationError`/`ConfigurationError`
- `validation.py` - Input validation with `InputValidator` class and `SecurityValidationError`
- `rbac_validator.py` - RBAC permission checks for operator/validator roles
- `waiter.py` - Generic polling/wait utilities for async conditions

**Workflow Modules** (`modules/`):
- `preflight/` - Modular pre-flight validators
- `preflight_coordinator.py` - Coordinates pre-flight validation across modules
- `preflight_validators.py` - Deprecated compatibility shim (prefer `modules.preflight`)
- `backup_schedule.py` - Shared helpers for BackupSchedule management
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
Defined phases in `Phase` enum: `INIT`, `PREFLIGHT`, `PRIMARY_PREP`, `SECONDARY_VERIFY`, `ACTIVATION`, `POST_ACTIVATION`, `FINALIZATION`, `COMPLETED`, `FAILED`. The main switchover flow uses the diagram above; `FAILED` is set on errors.
|
| Phase | Runbook Steps | Module | Key Actions |
| --- | --- | --- | --- |
| `PREFLIGHT` | Step 0 | `preflight_coordinator.py` + `preflight/` | Validate both hubs, check ACM versions, verify backups |
| `PRIMARY_PREP` | Steps 1-3 / F1-F3 | `primary_prep.py` | Pause BackupSchedule, add disable-auto-import annotations, scale Thanos |
| `ACTIVATION` | Steps 4-5 / F4-F5 | `activation.py` | Verify passive sync or create full restore, activate clusters |
| `POST_ACTIVATION` | Steps 6-10 / F6 | `post_activation.py` | Wait for ManagedClusters to connect, verify klusterlet agents |
| `FINALIZATION` | Steps 11-12 | `finalization.py` | Enable backups, verify integrity, handle old hub state |
| (manual) | Step 13 | — | Inform stakeholders (out-of-band) |
| (separate) | Step 14 | `decommission.py` | Decommission old hub |
| (separate) | Rollback 1-5 | (manual/partial) | Rollback procedures |
|
Each phase handler checks `state.get_current_phase()` before executing. Failed phases set `Phase.FAILED`.

## Key Patterns

### Idempotent Step Execution
```python
if not self.state.is_step_completed("step_name"):
    self._do_step()
    self.state.mark_step_completed("step_name")
    # Note: mark_step_completed() persists via save_state() for durability
```

### State Persistence Pattern
The StateManager uses optimized write batching:
- **`save_state()`**: Writes only if state is dirty (has pending changes). Used for step/config updates.
- **`flush_state()`**: Forces immediate write. Use for critical checkpoints (phase transitions, errors, resets).

Critical operations automatically call `flush_state()`:
- `set_phase()` - Phase transitions
- `add_error()` - Error recording
- `reset()` - State resets
- `ensure_contexts()` - Context changes

Non-critical operations persist state via `save_state()`:
- `mark_step_completed()` - Step completion tracking (immediate durability via `save_state()`)
- `set_config()` - Configuration storage (immediate durability via `save_state()`)

State is automatically flushed on program termination (SIGTERM/SIGINT/atexit) to prevent data loss.

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

Keep tests current with any behavior changes. The default test run excludes E2E; run E2E on demand.

```bash
# Core tests with coverage (default, excludes E2E)
./run_tests.sh

# On-demand E2E
RUN_E2E=1 ./run_tests.sh

# Quick pytest run
pytest tests/ -v

# Run specific test file
pytest tests/test_kube_client.py -v

# Run with markers
pytest -m unit tests/      # Unit tests only
pytest -m integration tests/  # Integration tests
```

Tests use mocked `KubeClient` - fixture pattern in `tests/conftest.py`. Mock responses should include `resourceVersion` in metadata for patch verification tests.

### Test Quality Guidelines
- **DO NOT create meaningless or superficial tests** - tests should verify real logic and functionality
- Focus on testing actual business logic, error handling, and edge cases
- Avoid tests that only verify implementation details or trivial functionality
- Each test should provide meaningful value by catching real potential bugs

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
**Hub discovery and preflight**: Use `./scripts/discover-hub.sh --auto --run` to discover hub contexts and run smart preflight checks

### CLI Validation Guidance (Contributor note)
- CLI validation is implemented in `lib/validation.py` (class `InputValidator`). When changing existing arguments or adding new ones, update the validator accordingly and add tests in `tests/test_validation.py`.
- Current important cross-argument rules (enforced by `InputValidator.validate_all_cli_args`):
    - `--secondary-context` is required for switchover operations unless `--decommission` or `--setup` is set.
    - `--non-interactive` can only be used together with `--decommission` (it's disallowed for normal switchovers).
    - `--setup` requires `--admin-kubeconfig` and validates `--token-duration` format (e.g., `48h`, `30m`, `3600s`).
- If you change these rules, update `docs/reference/validation-rules.md`, `docs/operations/usage.md`, and `docs/operations/quickref.md` to match.

## Code Review Checklist for Future Refactoring

When doing similar refactoring work:
- Compare line-by-line old vs new implementation
- Verify all conditional branches are preserved
- Check that error handling matches original behavior
- Validate critical vs non-critical failure classifications
- Ensure all imports are present in new modules
- Test with real-world scenarios, not just unit tests

## Files to Know

- `CHANGELOG.md` - Update `[Unreleased]` section for changes
- `docs/development/architecture.md` - Design decisions and module descriptions
- `docs/development/findings-report.md` - Issue backlog with identified bugs and improvements (check Status field before fixing)
- `lib/constants.py` - All magic strings centralized here (Python)
- `scripts/constants.sh` - All magic strings centralized here (Bash)
- `setup.cfg` - pytest, flake8, mypy configuration
- `.claude/skills/` - Claude SKILLS for switchover procedures (keep in sync with runbook)

## Version Management

**IMPORTANT**: Python and Bash versions MUST always be in sync. When making changes to either Python or Bash code, update BOTH version files to the same version.

Container image and Helm chart metadata follow the same version: the Containerfile `version` label and the Helm chart `appVersion` should match the Python/Bash tool version; bump the Helm chart `version` alongside releases.

### Version Locations
|
| Component | File | Variables |
| --- | --- | --- |
| **Bash Scripts** | `scripts/constants.sh` | `SCRIPT_VERSION`, `SCRIPT_VERSION_DATE` |
| **Python Tool** | `lib/__init__.py` | `__version__`, `__version_date__` |
| **README** | `README.md` | Version badge at top of file |
| **Container Image** | `container-bootstrap/Containerfile` | `LABEL version` |
| **Helm Chart** | `deploy/helm/Chart.yaml` | `version`, `appVersion` (appVersion = tool version) |

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

When making script changes:
1. [ ] Update `SCRIPT_VERSION` in `scripts/constants.sh`
2. [ ] Update `SCRIPT_VERSION_DATE` to current date
3. [ ] Update container image label version in [container-bootstrap/Containerfile](container-bootstrap/Containerfile)
4. [ ] Update Helm chart `version` and `appVersion` (appVersion = tool version) in [deploy/helm/Chart.yaml](deploy/helm/Chart.yaml)
5. [ ] Update version in `README.md` (top of file)
6. [ ] Add changelog entry in `CHANGELOG.md`
7. [ ] Update `scripts/README.md` if new features/checks added
8. [ ] Create and push a git tag for the new version (e.g., `git tag vX.Y.Z && git push origin vX.Y.Z`)

When making Python code changes:
1. [ ] Update `__version__` in `lib/__init__.py`
2. [ ] Update `__version_date__` to current date
3. [ ] Update container image label version in [container-bootstrap/Containerfile](container-bootstrap/Containerfile)
4. [ ] Update Helm chart `version` and `appVersion` (appVersion = tool version) in [deploy/helm/Chart.yaml](deploy/helm/Chart.yaml)
5. [ ] Update version in `README.md` (top of file)
6. [ ] Add changelog entry in `CHANGELOG.md`
7. [ ] Keep Python and Bash versions in sync if changes affect both
8. [ ] Create and push a git tag for the new version (e.g., `git tag vX.Y.Z && git push origin vX.Y.Z`)

## Claude SKILLS

The `.claude/skills/` directory contains conversational guides for Claude to help operators through ACM switchover procedures. Each SKILL provides decision trees, commands, and troubleshooting paths.

> **Maintenance Rule**: When updating [docs/ACM_SWITCHOVER_RUNBOOK.md](docs/ACM_SWITCHOVER_RUNBOOK.md), also update the corresponding SKILLS in `.claude/skills/` to keep procedures synchronized.

### Operations SKILLS
|
| SKILL | Purpose | Runbook Reference |
| --- | --- | --- |
| [preflight-validation.skill.md](.claude/skills/operations/preflight-validation.skill.md) | Interactive pre-flight checklist with go/no-go decisions | Step 0 |
| [pause-backups.skill.md](.claude/skills/operations/pause-backups.skill.md) | Pause BackupSchedule (ACM 2.11 vs 2.12+ variants) | Step 1 |
| [activate-passive-restore.skill.md](.claude/skills/operations/activate-passive-restore.skill.md) | Method 1: Passive restore activation flow | Steps 2-5 |
| [activate-full-restore.skill.md](.claude/skills/operations/activate-full-restore.skill.md) | Method 2: One-time full restore flow | Steps F1-F5 |
| [verify-switchover.skill.md](.claude/skills/operations/verify-switchover.skill.md) | Post-activation verification (clusters, observability) | Steps 6-10 |
| [enable-backups.skill.md](.claude/skills/operations/enable-backups.skill.md) | Enable BackupSchedule on new hub | Steps 11-12 |
| [rollback.skill.md](.claude/skills/operations/rollback.skill.md) | Rollback procedure with decision tree by failure point | Rollback 1-5 |
| [decommission.skill.md](.claude/skills/operations/decommission.skill.md) | Safe decommissioning with safety checks | Step 14 |

### Troubleshooting SKILLS
|
| SKILL | Symptoms | Resolution |
| --- | --- | --- |
| [pending-import.skill.md](.claude/skills/troubleshooting/pending-import.skill.md) | Clusters stuck in "Pending Import" | Klusterlet diagnostics, reimport |
| [grafana-no-data.skill.md](.claude/skills/troubleshooting/grafana-no-data.skill.md) | No metrics in Grafana dashboards | Observatorium restart, collector checks |
| [restore-stuck.skill.md](.claude/skills/troubleshooting/restore-stuck.skill.md) | Restore stuck in "Running" state | Velero diagnostics, storage checks |

### Using SKILLS

SKILLS are designed for conversational guidance. When helping with switchover:
1. Start with `preflight-validation` to assess readiness
2. Follow the appropriate method (passive or full restore)
3. Use troubleshooting SKILLS when issues arise
4. Reference the runbook for detailed command explanations
