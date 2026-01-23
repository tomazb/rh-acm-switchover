# ACM Switchover E2E Testing Framework

This directory contains the end-to-end (E2E) testing tools for the ACM Hub Switchover process.

## ⚠️ Important: Bash Scripts Deprecated

**The bash scripts in this directory are deprecated and will be removed in version 2.0.**

- ❌ `quick_start_e2e.sh` → ✅ Use `pytest -m e2e tests/e2e/`
- ❌ `e2e_test_orchestrator.sh` → ✅ Use `pytest -m e2e tests/e2e/`
- ❌ `phase_monitor.sh` → ✅ Use Python `ResourceMonitor` (automatic in pytest)

**Migration Guide**: See [`MIGRATION.md`](./MIGRATION.md) for detailed instructions on migrating from bash to pytest.

## Overview

The E2E testing framework provides:
- **Pytest-based orchestration** — Primary testing interface (Python-native)
- **Soak testing controls** — Time limits, max failures, resume capability (Phase 2)
- **Real-time monitoring** — JSONL metrics, alert detection via `ResourceMonitor` (Phase 2)
- **Failure injection testing** — Resilience testing with chaos scenarios (Phase 3)
- **Metrics and artifacts** — Per-cycle manifests, states, timing, JSONL metrics
- **Resume capability** — Continue from last successful cycle after failures
- **Bash helpers** — Legacy scripts (deprecated, will be removed in v2.0)

## Files

### Core Components (Use These)

- **`pytest` suite** — Primary entrypoint (`tests/e2e/test_e2e_switchover.py`, `test_e2e_dry_run.py`, `test_e2e_resilience.py`)
- **`orchestrator.py`** — Python E2E orchestrator with soak controls and failure injection
- **`monitoring.py`** — Python monitoring module (ResourceMonitor, MetricsLogger, Alert)
- **`failure_injection.py`** — FailureInjector class for chaos testing scenarios (Phase 3)
- **`e2e_analyzer.py`** — Analyze result folders and generate HTML reports
- **`conftest.py`** — Pytest fixtures and CLI options

### Deprecated Components (Don't Use)

- **`phase_monitor.sh`** — ⚠️ DEPRECATED: Legacy bash monitor (use `monitoring.py` instead)
- **`quick_start_e2e.sh`** — ⚠️ DEPRECATED: Legacy bash wrapper (use pytest instead)
- **`e2e_test_orchestrator.sh`** — ⚠️ DEPRECATED: Legacy bash orchestrator (use pytest instead)

## Quick Start (Recommended)

1) Configure environments (optional, for convenience):

```bash
cp .env.example .env
# adjust contexts, cycles, output dir if needed
```

2) Run a dry-run smoke (no cluster changes):

```bash
pytest tests/e2e -m e2e --e2e-dry-run
```

3) Run a single real switchover (passive + secondary):

```bash
pytest tests/e2e -m e2e \
   --primary-context mgmt1 \
   --secondary-context mgmt2 \
   --e2e-method passive \
   --e2e-old-hub-action secondary \
   --e2e-cycles 1 \
   --e2e-output-dir ./e2e-artifacts
```

4) Run a multi-cycle soak:

```bash
pytest tests/e2e -m "e2e and slow" \
   --primary-context mgmt1 \
   --secondary-context mgmt2 \
   --e2e-method passive \
   --e2e-old-hub-action secondary \
   --e2e-cycles 5 \
   --e2e-output-dir ./e2e-artifacts-soak
```

Artifacts are written under the provided `--e2e-output-dir` (manifest, metrics, states, CSV summary).

## Detailed Usage

### Pytest Orchestrator

The pytest suite drives the Python orchestrator. Key options (CLI or env vars):

- `--primary-context` / `--secondary-context` (or `E2E_PRIMARY_CONTEXT`, `E2E_SECONDARY_CONTEXT`)
- `--e2e-cycles` (default 1 via CLI, default 5 inside orchestrator)
- `--e2e-output-dir` (default: pytest temp dir)
- `--e2e-method` (passive|full) and `--e2e-old-hub-action` (secondary|decommission|none)
- `--e2e-stop-on-failure` to halt after the first failed cycle
- `--e2e-cooldown` (default 30) seconds between cycles

**Soak Testing Options (Phase 2):**

- `--e2e-run-hours` — Time limit in hours; orchestrator stops when exceeded
- `--e2e-max-failures` — Stop after N cycle failures
- `--e2e-resume` — Resume from last completed cycle (reads `.resume_state.json`)

**Failure Injection Options (Phase 3):**

- `--e2e-inject-failure` — Failure scenario to inject: `pause-backup`, `delay-restore`, `kill-observability-pod`, or `random`
- `--e2e-inject-at-phase` — Phase at which to inject failure (default: `activation`); choices: `preflight`, `primary_prep`, `activation`, `post_activation`, `finalization`

Output structure for a run:

```
<output-dir>/<run-id>/
├── logs/
├── states/
├── metrics/
│   └── metrics.jsonl       # JSONL time-series metrics
├── manifests/
│   └── manifest.json       # Run configuration and environment
├── alerts/                  # Alert files (if any triggered)
├── summary.json
└── cycle_results.csv
```

### JSONL Metrics Format (Phase 2)

The `metrics/metrics.jsonl` file contains one JSON object per line with real-time metrics:

```jsonl
{"timestamp": "2026-01-03T10:00:00+00:00", "metric_type": "cycle_start", "cycle_id": "cycle_001", "cycle_num": 1, ...}
{"timestamp": "2026-01-03T10:01:00+00:00", "metric_type": "phase_result", "phase_name": "preflight", "success": true, ...}
{"timestamp": "2026-01-03T10:05:00+00:00", "metric_type": "cycle_end", "cycle_id": "cycle_001", "success": true, ...}
```

Metric types include:
- `cycle_start` / `cycle_end` — Cycle lifecycle events
- `phase_result` — Individual phase completion with timing
- `resource_snapshot` — Periodic resource state (managed clusters, backups, restores)
- `alert` — Alert events (cluster unavailable, backup failure, etc.)

### Resource Monitoring (Phase 2)

The Python monitoring module (`monitoring.py`) provides:

- **ResourceMonitor** — Background thread polling clusters for resource status
- **MetricsLogger** — Thread-safe JSONL writer for metrics time-series
- **MonitoringContext** — Context manager for start/stop monitoring during cycles

Use `MonitoringContext` for integrated monitoring:

```python
from tests.e2e.monitoring import MonitoringContext

with MonitoringContext(primary_client, secondary_client, output_dir) as monitor:
    if monitor:
        monitor.set_phase("activation")
    # ... run cycles ...
```

### Phase Monitor (Legacy)

`tests/e2e/phase_monitor.sh` remains for manual/legacy monitoring but is deprecated. Use pytest for automated runs. Alert types now include `OBSERVABILITY_NOT_READY` (replacing the previous scale-up wording).

### E2E Analyzer

Python tool for comprehensive analysis of test results:

```bash
# Analyze test results
python3 tests/e2e/e2e_analyzer.py \
  --results-dir ./e2e-results-20240101-120000 \
  --output ./analysis_report.html
```

**Analysis Features:**
- Performance metrics across all cycles
- Phase-by-phase success rates and timing
- Alert pattern analysis
- Resource trend analysis
- Automated recommendations
- Interactive HTML reports

## Testing Scenarios (Pytest)

- **Smoke (dry-run):** `pytest tests/e2e -m e2e --e2e-dry-run`
- **Single cycle:** `pytest tests/e2e -m e2e --primary-context mgmt1 --secondary-context mgmt2 --e2e-cycles 1`
- **Multi-cycle soak:** add `--e2e-cycles N` and `-m "e2e and slow"`
- **Stop on first failure:** add `--e2e-stop-on-failure`
- **Time-limited soak:** add `--e2e-run-hours 4` to stop after 4 hours
- **Max failures limit:** add `--e2e-max-failures 3` to stop after 3 failures
- **Resume interrupted run:** add `--e2e-resume` to continue from last completed cycle

### Soak Testing Examples

**Overnight soak test (8 hours, max 5 failures):**

```bash
pytest tests/e2e -m "e2e and slow" \
   --primary-context mgmt1 \
   --secondary-context mgmt2 \
   --e2e-cycles 100 \
   --e2e-run-hours 8 \
   --e2e-max-failures 5 \
   --e2e-output-dir ./e2e-overnight-soak
```

**Resume after interruption:**

```bash
# If the above run is interrupted (Ctrl+C, pod restart, etc.):
pytest tests/e2e -m "e2e and slow" \
   --primary-context mgmt1 \
   --secondary-context mgmt2 \
   --e2e-cycles 100 \
   --e2e-run-hours 8 \
   --e2e-max-failures 5 \
   --e2e-output-dir ./e2e-overnight-soak \
   --e2e-resume
```

### Resilience Testing with Failure Injection (Phase 3)

**Pause backup mid-cycle:**

```bash
pytest -m e2e tests/e2e/test_e2e_resilience.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-inject-failure=pause-backup \
  --e2e-inject-at-phase=activation
```

**Kill observability pod during activation:**

```bash
pytest -m e2e tests/e2e/test_e2e_resilience.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-inject-failure=kill-observability-pod \
  --e2e-inject-at-phase=activation
```

**Delay restore with random scenario:**

```bash
pytest -m e2e tests/e2e/test_e2e_resilience.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-cycles 5 \
  --e2e-inject-failure=random
```

**Chaos test with multiple random failures:**

```bash
pytest -m "e2e and slow" tests/e2e/ \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --e2e-cycles 20 \
  --e2e-inject-failure=random \
  --e2e-max-failures 5
```

## Monitoring and Alerting

### Key Metrics Tracked

1. **Managed Cluster Status**
   - Total clusters per hub
   - Available clusters
   - Connection state changes

2. **Backup/Restore Operations**
   - Backup schedule phase and status
   - Restore progress and completion
   - Operation timing and success rates

3. **Observability Components**
   - Deployment replica counts
   - Unexpected scale-up detection
   - Component health status

4. **Klusterlet Agents**
   - Agent status per managed cluster
   - Connection health
   - Reconnection events

### Alert Types

- **CLUSTER_UNAVAILABLE** — Cluster unavailable >5 minutes
- **BACKUP_FAILURE** — Backup failing for >10 minutes
- **RESTORE_STALLED** — Restore operation stalled >15 minutes
- **OBSERVABILITY_NOT_READY** — Observability pods not ready at desired replicas

## Performance Benchmarks

### Expected Performance Targets

| Phase | Target Duration | Description |
|-------|----------------|-------------|
| Pre-flight Validation | < 3 minutes | Environment checks and validation |
| Primary Preparation | < 2 minutes | Backup pause and preparation |
| Activation | < 15 minutes | Restore and cluster activation |
| Post-activation | < 15 minutes | Verification and cleanup |
| Finalization | < 10 minutes | Old hub configuration |
| **Total** | **< 45 minutes** | **Complete switchover** |

### Success Criteria

- **Overall Success Rate**: ≥95%
- **Zero Data Loss**: All clusters successfully transition
- **Service Continuity**: No interruption to managed clusters
- **Rollback Capability**: State management preserved

## Troubleshooting

### Common Issues

1. **Context Not Found**
   ```bash
   # Check available contexts
   kubectl config get-contexts
   ```

2. **Permission Errors**
   ```bash
   # Validate RBAC permissions
   python check_rbac.py --context mgmt1 --role operator
   python check_rbac.py --context mgmt2 --role operator
   ```

3. **Network Connectivity**
   ```bash
   # Test connectivity to hubs
   kubectl --context mgmt1 cluster-info
   kubectl --context mgmt2 cluster-info
   ```

4. **Missing Dependencies**
   ```bash
   # Install required tools
   # kubectl, python3, jq
   ```

### Debug Mode

Enable verbose logging with pytest’s `-v` and review per-cycle logs/metrics under the run directory. Legacy bash orchestrators still accept `set -x` for debugging if needed.

## Integration with CI/CD

### GitHub Actions Example (Recommended)

```yaml
- name: Run E2E Tests
  run: |
    pytest -m e2e tests/e2e/ \
      --e2e-dry-run \
      --e2e-cycles 1 \
      --junitxml=junit.xml
    
- name: Upload test results
  uses: actions/upload-artifact@v4
  if: always()
  with:
    name: e2e-test-results
    path: junit.xml
    
- name: Analyze Results
  if: always()
  run: |
    python3 tests/e2e/e2e_analyzer.py \
      --results-dir ./e2e-results-* \
      --output ./e2e-report.html
```

### Jenkins Pipeline Example (Recommended)

```groovy
stage('E2E Testing') {
    steps {
        sh '''
            pytest -m e2e tests/e2e/ \
              --e2e-cycles 3 \
              --junitxml=results.xml
        '''
        sh 'python3 tests/e2e/e2e_analyzer.py --results-dir ./e2e-results-* --output ./e2e-report.html'
        junit 'results.xml'
        publishHTML([
            allowMissing: false,
            alwaysLinkToLastBuild: true,
            keepAll: true,
            reportDir: '.',
            reportFiles: 'e2e-report.html',
            reportName: 'E2E Test Report'
        ])
    }
}
```

### Legacy CI/CD Examples (Deprecated)

<details>
<summary>⚠️ Click to view deprecated bash script examples (use pytest instead)</summary>

**GitHub Actions (deprecated):**
```yaml
- name: Run E2E Tests
  run: |
    ./tests/e2e/quick_start_e2e.sh --dry-run --cycles 1
```

**Jenkins (deprecated):**
```groovy
sh './tests/e2e/quick_start_e2e.sh --cycles 3'
```

**Migrate to pytest**: See [MIGRATION.md](./MIGRATION.md) for updated examples.

</details>

## Best Practices

1. **Always run dry-run first** before executing real switchovers
2. **Monitor resource usage** during testing to identify bottlenecks
3. **Review alert patterns** to identify recurring issues
4. **Document findings** and update procedures based on test results
5. **Use consistent naming** for test environments and contexts
6. **Validate RBAC permissions** before starting tests
7. **Check network connectivity** between all clusters
8. **Monitor disk space** for logs and metrics during long tests

## Contributing

When adding new features to the E2E framework:

1. **Update documentation** for new monitoring capabilities
2. **Add error handling** for new failure scenarios
3. **Include tests** for new functionality
4. **Update alert thresholds** based on operational experience
5. **Enhance reporting** with new metrics and insights

## Support

For issues with the E2E testing framework:

1. Check the troubleshooting section above
2. Review generated logs and reports
3. Validate environment prerequisites
4. Check for known issues in the project repository
5. Create detailed bug reports with logs and configuration

## Deprecation Notice & Migration

### Timeline

| Version | Bash Scripts Status | Recommended Action |
|---------|--------------------|--------------------|
| 1.x | Deprecated but functional | Migrate to pytest now |
| 2.0 | **REMOVED** | pytest only |

### Why Migrate?

The Python E2E orchestrator provides significant advantages over bash scripts:

| Feature | Bash Scripts | Python Orchestrator |
|---------|--------------|---------------------|
| Time-limited soak tests | ❌ No | ✅ `--e2e-run-hours` |
| Resume failed tests | ❌ No | ✅ `--e2e-resume` |
| Max failures limit | ❌ No | ✅ `--e2e-max-failures` |
| Failure injection/resilience | ❌ No | ✅ Phase 3: `--e2e-inject-failure` |
| Real-time monitoring | ⚠️ Separate script | ✅ Integrated |
| Metrics format | CSV | JSONL (streaming) |
| Alert detection | Manual | Automatic |
| CI/CD integration | Custom | pytest native |
| Error handling | Basic | Transient error detection |
| Test debugging | set -x | pytest -v, pdb |
| API access | kubectl wrappers | Native K8s/ACM APIs |

### Quick Migration Examples

**Run 5 cycles:**
```bash
# Before (bash - deprecated)
./tests/e2e/quick_start_e2e.sh --cycles 5

# After (pytest - recommended)
pytest -m e2e tests/e2e/ --e2e-cycles 5
```

**Run with monitoring:**
```bash
# Before (bash - deprecated)
./tests/e2e/phase_monitor.sh --primary mgmt1 --secondary mgmt2 &
./tests/e2e/e2e_test_orchestrator.sh --cycles 5

# After (pytest - monitoring automatic)
pytest -m e2e tests/e2e/ --e2e-cycles 5 --primary-context mgmt1 --secondary-context mgmt2
```

**Soak test with time limit:**
```bash
# Before (bash - not possible)
# Had to manually kill after 4 hours

# After (pytest - built-in)
pytest -m e2e tests/e2e/ --e2e-cycles 100 --e2e-run-hours 4 --e2e-max-failures 5
```

**Resume failed test:**
```bash
# Before (bash - not possible)
# Had to start over from cycle 1

# After (pytest - built-in)
pytest -m e2e tests/e2e/ --e2e-resume --e2e-output-dir ./previous-test
```

### Full Migration Guide

See **[MIGRATION.md](./MIGRATION.md)** for:
- Complete command mapping (bash → pytest)
- Environment variable equivalents
- Programmatic API usage
- CI/CD integration examples
- Troubleshooting common migration issues

### Getting Help

- **Migration questions**: See [MIGRATION.md](./MIGRATION.md)
- **pytest options**: Run `pytest -m e2e tests/e2e/ --help`
- **API documentation**: See docstrings in `orchestrator.py`, `monitoring.py`
- **Examples**: Review test files in `tests/e2e/test_*.py`
