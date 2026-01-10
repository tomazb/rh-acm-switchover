# Plan: Python E2E Orchestrator Migration

**Decision: MIGRATE TO PYTHON** - Replace bash E2E orchestration with a Python-based orchestrator that directly invokes existing phase modules. Bash scripts remain for manual runs but are deprecated long-term.

## Progress Tracking

### Phase 1 Status: ✅ COMPLETED

| Step | Description | Status | Commit |
|------|-------------|--------|--------|
| 1 | Create branch and add dependencies | ✅ Done | `9945081` |
| 2 | Fix bash script stability bugs | ✅ Done | `9de58fd` |
| 3 | Create Python E2EOrchestrator class | ✅ Done | `9de58fd` |
| 4 | Create phase handlers with timing | ✅ Done | `9de58fd` |
| 5 | Add pytest fixtures and test cases | ✅ Done | `979c41d` |
| 6 | Extend analyzer with percentiles | ✅ Done | `f524f97` |
| 7 | Add deprecation warnings to bash | ✅ Done | `8169469` |
| 8 | Update plan with progress tracking | ✅ Done | — |

**Branch**: `e2e-development`  
**Started**: 2026-01-03  
**Completed**: 2026-01-03

### Phase 2 Status: ✅ COMPLETED

| Step | Description | Status |
|------|-------------|--------|
| E2E-200 | Add soak controls (--run-hours, --max-failures, --resume) | ✅ Done |
| E2E-201 | Port phase_monitor.sh to Python monitoring.py | ✅ Done |
| E2E-202 | Add JSONL metrics emission | ✅ Done |

**Completed**: 2026-01-03

### Phase 2 Files Created/Modified

- `tests/e2e/orchestrator.py` - Added soak controls (run_hours, max_failures, resume), JSONL integration
- `tests/e2e/conftest.py` - Added CLI options: --e2e-run-hours, --e2e-max-failures, --e2e-resume
- `tests/e2e/monitoring.py` - NEW: ResourceMonitor, MetricsLogger, Alert, MonitoringContext
- `tests/e2e/__init__.py` - Exported monitoring classes
- `tests/e2e/test_e2e_dry_run.py` - Added tests for soak controls and metrics (54 total E2E tests)
- `tests/e2e/test_e2e_monitoring.py` - NEW: Tests for monitoring module
- `lib/constants.py` - Added LOCAL_CLUSTER_NAME constant

### Phase 1 Files Created/Modified

- `tests/e2e/__init__.py` - Package init
- `tests/e2e/orchestrator.py` - E2EOrchestrator class with RunConfig
- `tests/e2e/phase_handlers.py` - Phase wrappers with timing instrumentation
- `tests/e2e/conftest.py` - pytest fixtures and CLI options
- `tests/e2e/test_e2e_switchover.py` - Real cluster E2E tests
- `tests/e2e/test_e2e_dry_run.py` - CI-friendly dry-run tests (21 tests)
- `tests/e2e/e2e_analyzer.py` - Extended with percentiles, compare mode
- `tests/e2e/*.sh` - Deprecated bash scripts with warnings
- `setup.cfg` - Added `e2e` marker
- `requirements-dev.txt` - Added pandas, matplotlib, seaborn

---

## Goals

- Run complete switchover cycles repeatedly with automated context swapping
- Produce structured logs, metrics, and statistics per run/cycle/phase
- Integrate with pytest for CI (`pytest -m e2e`)
- Support both real-cluster runs and dry-run validation

## Success Criteria

- >=95% success rate over 20+ cycles without manual intervention
- Phase durations tracked with P50/P90/P95 and regressions flagged
- Every cycle emits artifacts: logs, metrics, state, environment snapshot
- Analyzer can compare runs and highlight deltas

## Architecture

### Python Orchestrator (New)

```text
tests/e2e/
├── __init__.py
├── conftest.py              # pytest fixtures for E2E
├── orchestrator.py          # Core E2EOrchestrator class
├── phase_handlers.py        # Phase execution wrappers with timing
├── test_e2e_switchover.py   # pytest test cases
└── test_e2e_dry_run.py      # CI-friendly dry-run tests
```

### Reused from Main Codebase

- `KubeClient`, `StateManager`, `Phase` from `lib/`
- `PreflightValidator`, `PrimaryPreparation`, `SecondaryActivation`, `PostActivationVerification`, `Finalization` from `modules/`
- Constants from `lib/constants.py`

### Deprecated (Keep for Manual Runs)

- `tests/e2e/quick_start_e2e.sh`
- `tests/e2e/e2e_test_orchestrator.sh`
- `tests/e2e/phase_monitor.sh`

## Phase 1: Core Migration (~29 hours)

### Step 1: Fix Bash Stability Bugs (~4 hours)

Keep scripts runnable during migration.

| File | Fix |
|------|-----|
| `tests/e2e/phase_monitor.sh` | Source `scripts/constants.sh`, fix subshell variable mutation (use process substitution) |
| `tests/e2e/quick_start_e2e.sh` | Source `scripts/constants.sh`, remove hardcoded namespaces |
| `tests/e2e/e2e_test_orchestrator.sh` | Add `log_error` fallback, fix metrics filename (`metrics_*.json` not `cycle*_*.json`) |

### Step 2: Create Python Orchestrator (~8 hours)

Create `tests/e2e/orchestrator.py`:

```python
@dataclass
class RunConfig:
    primary_context: str
    secondary_context: str
    method: str = "passive"
    old_hub_action: str = "secondary"
    cycles: int = 5
    dry_run: bool = False
    output_dir: Path = Path("./e2e-results")
    stop_on_failure: bool = False
    cooldown_seconds: int = 30

class E2EOrchestrator:
    def __init__(self, config: RunConfig): ...
    def run_all_cycles(self) -> bool: ...
    def _run_cycle(self, cycle_id, primary_ctx, secondary_ctx) -> CycleResult: ...
```

Key behaviors:
- Generates `run_id` and `cycle_id` for all artifacts
- Writes `manifest.json` with inputs, git SHA, tool versions
- Swaps contexts after each cycle
- Creates fresh `StateManager` per cycle

### Step 3: Create Phase Handlers (~4 hours)

Create `tests/e2e/phase_handlers.py`:

Wrap existing modules with timing and E2E logging:
- `run_preflight()` → `PreflightValidator.validate_all()`
- `run_primary_prep()` → `PrimaryPreparation.prepare()`
- `run_activation()` → `SecondaryActivation.activate()`
- `run_post_activation()` → `PostActivationVerification.verify()`
- `run_finalization()` → `Finalization.finalize()`

### Step 4: Create Pytest Fixtures (~4 hours)

Create `tests/e2e/conftest.py`:

```python
def pytest_addoption(parser):
    parser.addoption("--primary-context", ...)
    parser.addoption("--secondary-context", ...)
    parser.addoption("--e2e-cycles", default="1", ...)
    parser.addoption("--e2e-dry-run", action="store_true", ...)

@pytest.fixture(scope="session")
def primary_client(e2e_config): ...

@pytest.fixture(scope="session")
def secondary_client(e2e_config): ...

@pytest.fixture(scope="session")
def validate_cluster_access(primary_client, secondary_client): ...
```

Add to `setup.cfg`:
```ini
[tool:pytest]
markers =
    e2e: End-to-end tests requiring real clusters
```

### Step 5: Create E2E Test Cases (~3 hours)

Create `tests/e2e/test_e2e_switchover.py`:

```python
@pytest.mark.e2e
class TestE2ESwitchover:
    def test_single_switchover_cycle(self, e2e_config, tmp_path): ...
    
    @pytest.mark.slow
    def test_multi_cycle_switchover(self, e2e_config, tmp_path): ...
```

### Step 6: Extend Analyzer (~6 hours)

Update `tests/e2e/e2e_analyzer.py`:

- Add percentile calculations (P50/P90/P95) for phase durations
- Implement `--compare` mode (currently stubbed)
- Accept `CycleResult` objects directly from Python orchestrator
- Make pandas/matplotlib optional with graceful fallback

## Phase 2: Soak Testing & Monitoring (~12-16 hours)

After Phase 1 validation on real clusters.

### Soak Controls

Add to `RunConfig` and CLI:
- `--run-hours`: Time-boxed execution
- `--max-failures`: Stop after N failures
- `--cooldown`: Seconds between cycles
- `--resume`: Continue from last completed cycle

### Monitoring Migration

Port `tests/e2e/phase_monitor.sh` to `tests/e2e/monitoring.py`:
- Real-time resource polling (ManagedClusters, Restore, Backup, Observability)
- Structured alert emission
- Metrics time-series to JSONL

## Phase 3: Resilience Testing (~8 hours)

### Failure Injection

Add `--inject-failure` flag with scenarios:
- Pause backup mid-cycle
- Delay restore completion
- Kill observability pod

### Rollback Validation

Verify rollback completion and data continuity after injected failures.

## Deprecation Timeline

| Component | Phase 1 | Phase 2 | Phase 3 |
|-----------|---------|---------|---------|
| `quick_start_e2e.sh` | Bug-fixed, keep | Keep for manual | Deprecate |
| `e2e_test_orchestrator.sh` | Bug-fixed, keep | Keep for manual | Deprecate |
| `phase_monitor.sh` | Bug-fixed | Replace with Python | Remove |
| `pytest -m e2e` | Primary for CI | Primary for all | Only option |

## Issue Breakdown

### Phase 1 Issues (COMPLETED)

| ID | Title | Effort | Status |
|----|-------|--------|--------|
| E2E-001 | Fix namespace mismatch in bash scripts | 1h | ✅ Done |
| E2E-002 | Fix subshell variable mutation in phase_monitor.sh | 2h | ✅ Done |
| E2E-003 | Add log_error fallback and fix metrics filename | 1h | ✅ Done |
| E2E-100 | Create Python E2EOrchestrator class | 8h | ✅ Done |
| E2E-101 | Create phase handlers with timing instrumentation | 4h | ✅ Done |
| E2E-102 | Add pytest fixtures and CLI options | 4h | ✅ Done |
| E2E-103 | Create E2E test cases with markers | 3h | ✅ Done |
| E2E-104 | Extend analyzer with percentiles and compare mode | 6h | ✅ Done |

### Phase 2 Issues (COMPLETED)

| ID | Title | Effort | Status |
|----|-------|--------|--------|
| E2E-200 | Add soak controls (--run-hours, --max-failures, --resume) | 6h | ✅ Done |
| E2E-201 | Port phase_monitor.sh to Python | 6h | ✅ Done |
| E2E-202 | Add JSONL metrics emission | 4h | ✅ Done |

### Phase 3 Status: ✅ COMPLETED (Resilience Testing)

| Step | Description | Status |
|------|-------------|--------|
| E2E-300 | Implement failure injection scenarios | ✅ Done |

**Completed**: 2026-01-04

### Phase 3 Files Created/Modified

- `tests/e2e/failure_injection.py` - NEW: FailureInjector class with 3 scenarios (pause-backup, delay-restore, kill-observability-pod)
- `tests/e2e/test_e2e_resilience.py` - NEW: 22 resilience tests covering injection scenarios
- `tests/e2e/conftest.py` - Added CLI options: --e2e-inject-failure, --e2e-inject-at-phase
- `tests/e2e/orchestrator.py` - Extended RunConfig with injection fields, added injection hooks
- `tests/e2e/phase_handlers.py` - Added phase_callback parameter for injection timing
- `tests/e2e/__init__.py` - Exported FailureInjector class
- `lib/kube_client.py` - Added get_deployment() and delete_pod() methods

### Phase 3 Features

**Failure Injection Scenarios:**
- `pause-backup`: Pauses BackupSchedule during cycle
- `delay-restore`: Scales down Velero to delay restore operations
- `kill-observability-pod`: Deletes MCO observability-observatorium-api pod
- `random`: Randomly selects one of the above scenarios

**CLI Options:**
- `--e2e-inject-failure`: Select failure scenario to inject
- `--e2e-inject-at-phase`: Choose phase at which to inject (default: activation)

## Running E2E Tests

### CI (Dry-Run)
```bash
pytest -m e2e --e2e-dry-run tests/e2e/
```

### Real Clusters (Single Cycle)
```bash
pytest -m e2e \
  --primary-context=mgmt1 \
  --secondary-context=mgmt2 \
  tests/e2e/test_e2e_switchover.py::TestE2ESwitchover::test_single_switchover_cycle
```

### Soak Test (Multiple Cycles)
```bash
pytest -m e2e \
  --primary-context=mgmt1 \
  --secondary-context=mgmt2 \
  --e2e-cycles=20 \
  tests/e2e/test_e2e_switchover.py::TestE2ESwitchover::test_multi_cycle_switchover
```

### Time-Limited Soak (Phase 2)
```bash
pytest -m e2e \
  --primary-context=mgmt1 \
  --secondary-context=mgmt2 \
  --e2e-cycles=100 \
  --e2e-run-hours=8 \
  --e2e-max-failures=5 \
  tests/e2e/test_e2e_switchover.py::TestE2ESwitchover::test_multi_cycle_switchover
```

### Resume Interrupted Run (Phase 2)
```bash
pytest -m e2e \
  --primary-context=mgmt1 \
  --secondary-context=mgmt2 \
  --e2e-cycles=100 \
  --e2e-run-hours=8 \
  --e2e-resume \
  tests/e2e/test_e2e_switchover.py::TestE2ESwitchover::test_multi_cycle_switchover
```

### Resilience Testing with Failure Injection (Phase 3)
```bash
# Inject pause-backup failure during activation phase
pytest -m e2e \
  --primary-context=mgmt1 \
  --secondary-context=mgmt2 \
  --e2e-inject-failure=pause-backup \
  --e2e-inject-at-phase=activation \
  tests/e2e/test_e2e_resilience.py

# Inject random failure scenarios
pytest -m e2e \
  --primary-context=mgmt1 \
  --secondary-context=mgmt2 \
  --e2e-cycles=10 \
  --e2e-inject-failure=random \
  tests/e2e/test_e2e_switchover.py
```

### Manual (Bash, Deprecated)
```bash
cd tests/e2e
./quick_start_e2e.sh --mode full
```
