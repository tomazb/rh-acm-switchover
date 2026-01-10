# Migration Guide: Bash Scripts → Python E2E Orchestrator

This guide helps you migrate from the deprecated bash scripts to the Python-based E2E testing framework.

## Why Migrate?

The Python E2E orchestrator provides:

- **Soak testing controls**: Time limits (`--e2e-run-hours`), max failures (`--e2e-max-failures`), resume capability (`--e2e-resume`)
- **Real-time monitoring**: Native Python monitoring with JSONL metrics emission
- **Better error handling**: Transient error detection, structured logging
- **Native integration**: Direct access to Kubernetes/ACM APIs without shell wrappers
- **Resume capability**: Continue failed test runs from last successful cycle
- **CI/CD friendly**: pytest integration with standard exit codes and reports

## Migration Table

| Bash Command | Python Equivalent | Notes |
|--------------|-------------------|-------|
| `./quick_start_e2e.sh --cycles 5` | `pytest -m e2e tests/e2e/ --e2e-cycles 5` | Single switchover cycles |
| `./e2e_test_orchestrator.sh --cycles 10` | `pytest -m e2e tests/e2e/ --e2e-cycles 10` | Multiple cycles |
| `./phase_monitor.sh --primary mgmt1 --secondary mgmt2` | Built into pytest (automatic) | Monitoring runs automatically |
| Manual cycle with bash | `pytest -m e2e tests/e2e/test_e2e_switchover.py::TestE2ESwitchover::test_single_switchover_cycle` | More control |

## Quick Examples

### Basic Single Cycle Test

**Before (bash):**
```bash
./quick_start_e2e.sh --cycles 1 --primary mgmt1 --secondary mgmt2
```

**After (pytest):**
```bash
pytest -m e2e tests/e2e/ \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-cycles 1 \
  --e2e-output-dir ./e2e-results
```

### Multi-Cycle Soak Test

**Before (bash):**
```bash
CYCLES=20 ./e2e_test_orchestrator.sh
```

**After (pytest with soak controls):**
```bash
pytest -m e2e tests/e2e/ \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-cycles 20 \
  --e2e-run-hours 4 \
  --e2e-max-failures 3 \
  --e2e-output-dir ./e2e-soak-test
```

### Dry-Run Test (CI)

**Before (bash):**
```bash
./quick_start_e2e.sh --dry-run --cycles 1
```

**After (pytest):**
```bash
pytest -m e2e tests/e2e/ --e2e-dry-run
```

### Resume Failed Test

**Before (bash):**
Not supported - had to start from scratch

**After (pytest):**
```bash
# Original run that failed at cycle 15:
pytest -m e2e tests/e2e/ --e2e-cycles 20 --e2e-output-dir ./test-run

# Resume from cycle 15:
pytest -m e2e tests/e2e/ --e2e-resume --e2e-output-dir ./test-run
```

### Monitoring Only

**Before (bash):**
```bash
./phase_monitor.sh --primary mgmt1 --secondary mgmt2 --phase monitoring
```

**After (Python):**
```python
from tests.e2e.monitoring import ResourceMonitor, MetricsLogger

monitor = ResourceMonitor(
    primary_context="mgmt1",
    secondary_context="mgmt2",
    poll_interval=30
)

with monitor:
    # Your test code here
    pass
```

## Environment Variables

Most bash environment variables map directly to pytest CLI options:

| Bash Variable | Pytest Option | Example |
|---------------|---------------|---------|
| `PRIMARY_CONTEXT` | `--primary-context` | `--primary-context mgmt1` |
| `SECONDARY_CONTEXT` | `--secondary-context` | `--secondary-context mgmt2` |
| `CYCLES` | `--e2e-cycles` | `--e2e-cycles 5` |
| `OUTPUT_DIR` | `--e2e-output-dir` | `--e2e-output-dir ./results` |
| N/A (new) | `--e2e-run-hours` | `--e2e-run-hours 4` |
| N/A (new) | `--e2e-max-failures` | `--e2e-max-failures 3` |
| N/A (new) | `--e2e-resume` | `--e2e-resume` |
| N/A (new) | `--e2e-dry-run` | `--e2e-dry-run` |

## Programmatic Usage

If you were calling bash scripts from other scripts, use the Python API:

**Before (bash):**
```bash
#!/bin/bash
source tests/e2e/e2e_test_orchestrator.sh
run_cycles 5
```

**After (Python):**
```python
from tests.e2e.orchestrator import E2EOrchestrator, RunConfig, PhaseConfig

config = RunConfig(
    primary_context="mgmt1",
    secondary_context="mgmt2",
    cycles=5,
    run_hours=2.0,
    max_failures=2
)

orchestrator = E2EOrchestrator(config)
results = orchestrator.run_all_cycles()

print(f"Success rate: {orchestrator.calculate_success_rate():.1f}%")
```

## CI/CD Integration

### GitHub Actions

**Before (bash in workflow):**
```yaml
- name: Run E2E Tests
  run: |
    cd tests/e2e
    ./quick_start_e2e.sh --cycles 3
```

**After (pytest in workflow):**
```yaml
- name: Run E2E Tests
  run: |
    pytest -m e2e tests/e2e/ \
      --e2e-cycles 3 \
      --e2e-dry-run \
      --junitxml=junit.xml
      
- name: Upload test results
  uses: actions/upload-artifact@v4
  with:
    name: e2e-test-results
    path: junit.xml
```

### Jenkins

**Before:**
```groovy
sh './tests/e2e/quick_start_e2e.sh --cycles 5'
```

**After:**
```groovy
sh '''
  pytest -m e2e tests/e2e/ \
    --e2e-cycles 5 \
    --junitxml=results.xml
'''
junit 'results.xml'
```

## Metrics and Artifacts

### Bash Script Output

Bash scripts created:
- `monitoring-TIMESTAMP/metrics.csv`
- `e2e-results-TIMESTAMP/e2e-orchestrator.log`
- Manual analysis required

### Python Orchestrator Output

Python orchestrator creates per cycle:
```
e2e-output/
├── .resume_state.json           # Resume state (if enabled)
├── run_TIMESTAMP_<id>/
│   ├── cycle_001/
│   │   ├── manifest.json        # Full cycle metadata
│   │   ├── primary_state.json   # Primary hub state
│   │   ├── secondary_state.json # Secondary hub state
│   │   └── timing.json          # Phase timings
│   ├── cycle_002/
│   │   └── ...
│   ├── metrics/
│   │   └── metrics.jsonl        # Streaming metrics (JSONL)
│   └── summary.csv              # Overall summary
```

JSONL metrics can be analyzed with:
```bash
# Count events by type
jq -r .event_type metrics.jsonl | sort | uniq -c

# Extract cycle durations
jq -r 'select(.event_type=="cycle_end") | .duration_seconds' metrics.jsonl

# Find failures
jq 'select(.success==false)' metrics.jsonl
```

## Monitoring Differences

### Bash Monitoring

- Polling in background process
- CSV metrics output
- Manual alert detection
- No integration with test orchestrator

### Python Monitoring

- Thread-based background polling
- JSONL metrics with structured events
- Automatic alert detection and logging
- Integrated with orchestrator lifecycle
- ResourceMonitor class for programmatic use

Example:
```python
from tests.e2e.monitoring import ResourceMonitor, Alert

monitor = ResourceMonitor(
    primary_context="mgmt1",
    secondary_context="mgmt2",
    alert_callback=lambda alert: print(f"ALERT: {alert.message}")
)

with monitor:
    # Monitoring runs in background
    # Alerts automatically detected
    pass
```

## Troubleshooting

### "Can't find bash script"

The bash scripts are still present but deprecated. Use pytest instead.

### "Resume not working"

Bash scripts don't support resume. This is a pytest-only feature:

```bash
# Enable resume
pytest -m e2e tests/e2e/ --e2e-resume --e2e-output-dir ./my-test
```

### "Missing metrics.jsonl"

JSONL metrics are only created by the Python orchestrator, not bash scripts. Bash scripts create CSV format.

### "Monitoring not running"

Python orchestrator includes monitoring automatically. No separate script needed.

## Timeline

| Version | Status |
|---------|--------|
| 1.x | Bash scripts deprecated but functional |
| 2.0 | Bash scripts removed, pytest only |

**Current recommendation**: Migrate to pytest now to avoid disruption in version 2.0.

## Getting Help

- **Documentation**: See `tests/e2e/README.md`
- **Examples**: Run `pytest -m e2e tests/e2e/ --help`
- **API Reference**: See `tests/e2e/orchestrator.py` docstrings
- **Issues**: Check existing test files in `tests/e2e/test_*.py`

## Summary

✅ **DO**: Use `pytest -m e2e tests/e2e/` for all E2E testing  
✅ **DO**: Use Python API for programmatic access  
✅ **DO**: Leverage soak controls and resume capability  
❌ **DON'T**: Rely on bash scripts - they will be removed  
❌ **DON'T**: Use `phase_monitor.sh` - monitoring is automatic  
