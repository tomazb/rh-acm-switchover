# ACM Switchover E2E Testing Framework

This directory contains the end-to-end (E2E) testing tools for the ACM Hub Switchover process.

## Overview

The E2E testing framework provides:
- **Pytest-based orchestration** for single or repeated switchover cycles
- **Metrics and artifacts** written per cycle (manifest, states, metrics JSON, CSV summary)
- **Real-time monitoring hooks** via the Python orchestrator (preferred)
- **Alert detection** for common issues during switchover
- **Bash helpers** kept for backward compatibility (deprecated)

## Files

### Core Components

- **`pytest` suite** — primary entrypoint; see `tests/e2e/test_e2e_switchover.py`
- **`orchestrator.py`** — Python orchestrator invoked by the tests
- **`phase_monitor.sh`** — legacy bash monitor (deprecated; use pytest instead)
- **`e2e_analyzer.py`** — analyze existing result folders and generate HTML
- **`quick_start_e2e.sh` / `e2e_test_orchestrator.sh`** — legacy bash wrappers (deprecated)

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
- `--e2e-method` (passive only) and `--e2e-old-hub-action` (secondary|decommission)
- `--e2e-stop-on-failure` to halt after the first failed cycle
- `--e2e-cooldown` (default 30) seconds between cycles

**Soak Testing Options (Phase 2):**

- `--e2e-run-hours` — Time limit in hours; orchestrator stops when exceeded
- `--e2e-max-failures` — Stop after N cycle failures
- `--e2e-resume` — Resume from last completed cycle (reads `.resume_state.json`)

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

### GitHub Actions Example

```yaml
- name: Run E2E Tests
  run: |
    ./tests/e2e/quick_start_e2e.sh --dry-run --cycles 1
    
- name: Analyze Results
  if: always()
  run: |
    python3 tests/e2e/e2e_analyzer.py \
      --results-dir ./e2e-results-* \
      --output ./e2e-report.html
```

### Jenkins Pipeline Example

```groovy
stage('E2E Testing') {
    steps {
        sh './tests/e2e/quick_start_e2e.sh --cycles 3'
        sh 'python3 tests/e2e/e2e_analyzer.py --results-dir ./e2e-results-* --output ./e2e-report.html'
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
