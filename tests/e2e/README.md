# ACM Switchover E2E Testing Framework

This directory contains comprehensive end-to-end testing tools for the ACM Hub Switchover process.

## Overview

The E2E testing framework provides:
- **Automated test orchestration** for multiple consecutive switchover cycles
- **Real-time monitoring** of all critical resources during testing
- **Comprehensive analysis and reporting** with performance metrics
- **Alert detection** for common issues during switchover
- **Easy-to-use interfaces** for different testing scenarios

## Files

### Core Components

- **`e2e_test_orchestrator.sh`** - Main test orchestrator that runs multiple switchover cycles
- **`phase_monitor.sh`** - Real-time monitoring of ACM resources during switchover phases
- **`e2e_analyzer.py`** - Python tool for analyzing test results and generating HTML reports
- **`quick_start_e2e.sh`** - Simple interface for running E2E tests with common scenarios

## Quick Start

### 1. Environment Validation (No Changes)
```bash
./tests/e2e/quick_start_e2e.sh --dry-run --cycles 1
```

### 2. Run 5 Complete Switchover Cycles
```bash
./tests/e2e/quick_start_e2e.sh --cycles 5
```

### 3. Monitor Only (No Execution)
```bash
./tests/e2e/quick_start_e2e.sh --monitoring-only
```

### 4. Analyze Existing Results
```bash
./tests/e2e/quick_start_e2e.sh --analyze-only --results-dir ./e2e-results-20240101-120000
```

## Detailed Usage

### E2E Test Orchestrator

The main orchestrator runs complete switchover cycles with comprehensive monitoring:

```bash
# Basic usage
./tests/e2e/e2e_test_orchestrator.sh

# Custom configuration
PRIMARY_CONTEXT=mgmt1 \
SECONDARY_CONTEXT=mgmt2 \
CYCLES=5 \
./tests/e2e/e2e_test_orchestrator.sh
```

**Features:**
- Runs N consecutive switchover cycles
- Automatically swaps contexts between cycles for testing
- Monitors all critical resources in real-time
- Generates detailed logs and metrics
- Provides comprehensive success/failure reporting
- Supports resume capability if interrupted

**Output Structure:**
```
e2e-results-YYYYMMDD-HHMMSS/
├── logs/              # Detailed logs for each phase and cycle
├── states/            # State files for analysis
├── metrics/           # Resource metrics collected during testing
├── alerts/            # Alert notifications generated
├── cycle_results.csv # Summary results in CSV format
└── summary_report.txt # Text summary of all cycles
```

### Phase Monitor

Real-time monitoring of ACM resources during switchover:

```bash
# Monitor during switchover
./tests/e2e/phase_monitor.sh \
  --primary mgmt1 \
  --secondary mgmt2 \
  --phase switchover \
  --output-dir ./monitoring-results
```

**Monitored Resources:**
- Managed cluster status and availability
- Backup schedule status (primary hub)
- Restore progress (secondary hub)
- Observability deployments
- Klusterlet agent status

**Alert Detection:**
- Cluster unavailable for >5 minutes
- Backup failures
- Restore operations stalled >15 minutes
- Unexpected observability scale-up

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

## Testing Scenarios

### Scenario 1: Basic Validation
```bash
# Validate environment without making changes
./tests/e2e/quick_start_e2e.sh --dry-run --cycles 1
```

### Scenario 2: Single Switchover
```bash
# Execute one complete switchover cycle
./tests/e2e/quick_start_e2e.sh --cycles 1
```

### Scenario 3: Stress Testing (5 Cycles)
```bash
# Run 5 consecutive switchover cycles
./tests/e2e/quick_start_e2e.sh --cycles 5
```

### Scenario 4: Custom Configuration
```bash
# Test with different contexts and cycle count
./tests/e2e/quick_start_e2e.sh --primary hub1 --secondary hub2 --cycles 3
```

### Scenario 5: Monitoring-Only
```bash
# Monitor resources without executing switchover
./tests/e2e/quick_start_e2e.sh --monitoring-only
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

- **CLUSTER_UNAVAILABLE** - Cluster unavailable >5 minutes
- **BACKUP_FAILURE** - Backup operation failed
- **RESTORE_STALLED** - Restore operation stalled >15 minutes
- **OBSERVABILITY_SCALE_UP** - Unexpected observability component scale-up

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

Enable verbose logging for detailed troubleshooting:

```bash
# Verbose orchestrator
PRIMARY_CONTEXT=mgmt1 SECONDARY_CONTEXT=mgmt2 CYCLES=1 \
./tests/e2e/e2e_test_orchestrator.sh 2>&1 | tee debug.log

# Verbose switchover
python acm_switchover.py --verbose --primary-context mgmt1 --secondary-context mgmt2 --method passive --dry-run
```

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
