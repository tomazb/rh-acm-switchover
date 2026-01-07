# ACM Hub Switchover - End-to-End Testing Guide

## Overview

This document provides a comprehensive plan for end-to-end validation of the ACM Hub Switchover tool using real OpenShift clusters. The testing strategy covers complete switchover workflows, failure scenarios, and performance validation.

## Test Environment

### Current Environment Discovery

Based on the `discover-hub.sh` output, your test environment includes:

**ACM Hubs:**
- **mgmt1**: Primary hub (ACM 2.12.7, OCP 4.16.54) - 3/3 clusters connected
  - API Server: `https://api.mgmt1.htz1.all-it.tech:6443`
  - Also accessible via context: `open-cluster-management/api-mgmt1-htz1-all-it-tech:6443/system:admin`
- **mgmt2**: Secondary hub (ACM 2.12.7, OCP 4.16.54) - Ready for switchover
  - API Server: `https://api.mgmt2.htz1.all-it.tech:6443`

**Note:** The `discover-hub.sh` script automatically detects that `mgmt1` and the long-form context point to the same cluster (by API server URL) and groups them together, using the shortest name (`mgmt1`) in proposed commands.

**Managed Clusters:**
- **prod1**: Connected to mgmt1 (OCP 4.16.54)
- **prod2**: Connected to mgmt1 (OCP 4.16.54)  
- **prod3**: Connected to mgmt1 (OCP 4.16.54)

### Environment Prerequisites

**Cluster Requirements:**
- ✅ ACM 2.12.7 on both hubs (version compatible)
- ✅ OCP 4.16.54 on all clusters
- ✅ Network connectivity between all clusters
- ✅ 3 managed clusters currently connected to primary hub

**Setup Validation Commands:**
```bash
# Verify ACM installation on hubs
kubectl --context mgmt1 get mch -n open-cluster-management
kubectl --context mgmt2 get mch -n open-cluster-management

# Check managed cluster status
kubectl --context mgmt1 get managedclusters
kubectl --context mgmt1 get klusterlets -n open-cluster-management-agent

# Verify OADP installation
kubectl --context mgmt1 get dpa -n openshift-adp
kubectl --context mgmt2 get dpa -n openshift-adp
```

## Comprehensive Test Scenarios

### Scenario 1: Complete Switchover Workflow

**Objective**: Validate full primary→secondary switchover with all 3 managed clusters

**Test Matrix:**

| Method | Old Hub Action | Primary Context | Secondary Context | Expected Outcome |
|--------|---------------|----------------|------------------|------------------|
| passive-sync | secondary | mgmt1 | mgmt2 | Full failback capability |
| passive-sync | decommission | mgmt1 | mgmt2 | Clean removal |
| full-restore | secondary | mgmt1 | mgmt2 | One-time restore |
| full-restore | decommission | mgmt1 | mgmt2 | Clean migration |

**Execution Steps:**

1. **Pre-flight Validation**
```bash
# Run comprehensive pre-flight checks
./scripts/preflight-check.sh \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --method passive
```

2. **Dry-Run Testing**
```bash
# Preview switchover actions
python acm_switchover.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --method passive \
  --old-hub-action secondary \
  --dry-run \
  --verbose
```

3. **Execute Switchover**
```bash
# Full execution with monitoring
python acm_switchover.py \
  --primary-context mgmt1 \
  --secondary-context mgmt2 \
  --method passive \
  --old-hub-action secondary \
  --verbose
```

4. **Post-flight Validation**
```bash
# Verify successful switchover
./scripts/postflight-check.sh \
  --old-hub-context mgmt1 \
  --new-hub-context mgmt2
```

### Scenario 2: Failure Recovery Testing

**Objective**: Test resilience and recovery mechanisms

**Failure Injection Points:**

1. **Network Partition During Activation**
   - Simulate network connectivity loss between hubs
   - Validate state management and resume capability
   - Test rollback procedures

2. **OADP Backup Issues**
   - Test with corrupted/incomplete backups
   - Validate error handling and reporting
   - Test manual backup restoration

3. **Managed Cluster Connection Failures**
   - Simulate klusterlet agent failures
   - Test retry mechanisms and timeouts
   - Validate partial switchover scenarios

4. **RBAC Permission Issues**
   - Test with insufficient permissions
   - Validate RBAC validation checks
   - Test permission recovery procedures

**Recovery Validation Commands:**
```bash
# Check state management
python show_state.py --primary-context mgmt1 --secondary-context mgmt2

# Validate RBAC permissions
python check_rbac.py --context mgmt1 --role operator
python check_rbac.py --context mgmt2 --role operator

# Manual cluster status check
kubectl --context mgmt2 get managedclusters
kubectl --context mgmt2 get klusterlets -n open-cluster-management-agent
```

### Scenario 3: Edge Cases and Boundary Conditions

**Test Cases:**

1. **Version Compatibility**
   - Test with different ACM versions (if available)
   - Validate version checking logic
   - Test upgrade/downgrade scenarios

2. **Cluster Scale Testing**
   - Test with varying numbers of managed clusters
   - Validate performance with 1, 3, 10+ clusters
   - Monitor resource usage and API call efficiency

3. **Network Latency Scenarios**
   - Test cross-region/long-distance hub pairs
   - Validate timeout configurations
   - Test retry logic under high latency

## Success Criteria and Validation Checkpoints

### Critical Success Metrics

#### Phase 1: Pre-flight Validation (100% Required)
- ✅ All 15+ validation checks pass
- ✅ No critical security warnings
- ✅ All ClusterDeployments have `preserveOnDelete=true`
- ✅ Backup completed within last 24 hours
- ✅ Network connectivity between all clusters

#### Phase 2: Execution Validation (Zero Data Loss)
- ✅ All 3 ManagedClusters successfully transition to mgmt2
- ✅ Zero ClusterDeployment interruptions
- ✅ State management integrity maintained
- ✅ Rollback capability preserved throughout

#### Phase 3: Post-activation Validation (Service Continuity)
- ✅ All ManagedClusters report `Available: true` on mgmt2
- ✅ Backup schedule active on mgmt2 within 5 minutes
- ✅ Observability metrics flowing (if enabled)
- ✅ Old hub (mgmt1) properly configured as secondary or decommissioned

### Performance Benchmarks

**Execution Time Targets:**
- Pre-flight validation: < 3 minutes
- Primary preparation: < 2 minutes
- Activation phase: < 15 minutes
- Post-activation verification: < 15 minutes
- Finalization: < 10 minutes
- **Total execution time: < 45 minutes**

**Resource Usage:**
- State file size: < 1MB
- API call count: < 500 total
- Memory usage: < 512MB peak
- Network bandwidth: < 100MB total

### Automated Validation Script

```bash
#!/bin/bash
# E2E Validation Script
# Usage: ./e2e-validate.sh --primary mgmt1 --secondary mgmt2

PRIMARY_CONTEXT=${1:-mgmt1}
SECONDARY_CONTEXT=${2:-mgmt2}
LOG_FILE="e2e-validation-$(date +%Y%m%d-%H%M%S).log"

echo "Starting E2E Validation at $(date)" | tee $LOG_FILE

# Phase 1: Environment Validation
echo "=== Phase 1: Environment Validation ===" | tee -a $LOG_FILE
./scripts/preflight-check.sh \
  --primary-context $PRIMARY_CONTEXT \
  --secondary-context $SECONDARY_CONTEXT \
  --method passive | tee -a $LOG_FILE

# Phase 2: Dry-Run Testing
echo "=== Phase 2: Dry-Run Testing ===" | tee -a $LOG_FILE
python acm_switchover.py \
  --primary-context $PRIMARY_CONTEXT \
  --secondary-context $SECONDARY_CONTEXT \
  --method passive \
  --old-hub-action secondary \
  --dry-run \
  --verbose | tee -a $LOG_FILE

# Phase 3: Execute Switchover (uncomment when ready)
# echo "=== Phase 3: Execute Switchover ===" | tee -a $LOG_FILE
# python acm_switchover.py \
#   --primary-context $PRIMARY_CONTEXT \
#   --secondary-context $SECONDARY_CONTEXT \
#   --method passive \
#   --old-hub-action secondary \
#   --verbose | tee -a $LOG_FILE

# Phase 4: Post-flight Validation
echo "=== Phase 4: Post-flight Validation ===" | tee -a $LOG_FILE
./scripts/postflight-check.sh \
  --old-hub-context $PRIMARY_CONTEXT \
  --new-hub-context $SECONDARY_CONTEXT | tee -a $LOG_FILE

echo "E2E Validation completed at $(date)" | tee -a $LOG_FILE
```

## Automated Test Execution Framework

### Master Test Orchestrator

Create `tests/e2e/run_e2e_tests.sh`:
```bash
#!/bin/bash
# Master E2E Test Orchestrator

set -e

PRIMARY_CONTEXT=${1:-mgmt1}
SECONDARY_CONTEXT=${2:-mgmt2}
MANAGED_CLUSTERS=${3:-prod1,prod2,prod3}
TEST_SCENARIOS=${4:-all}
REPORT_DIR=${5:-./e2e-results}

# Create report directory
mkdir -p $REPORT_DIR

echo "======================================"
echo "ACM Switchover E2E Test Suite"
echo "======================================"
echo "Primary Hub: $PRIMARY_CONTEXT"
echo "Secondary Hub: $SECONDARY_CONTEXT"
echo "Managed Clusters: $MANAGED_CLUSTERS"
echo "Test Scenarios: $TEST_SCENARIOS"
echo "Report Directory: $REPORT_DIR"
echo ""

# Run environment validation
echo "=== Environment Validation ==="
./tests/e2e/environment-validator.sh \
  --primary $PRIMARY_CONTEXT \
  --secondary $SECONDARY_CONTEXT \
  --clusters $MANAGED_CLUSTERS \
  --report-dir $REPORT_DIR

# Run test scenarios based on selection
case $TEST_SCENARIOS in
  "all")
    echo "=== Running All Test Scenarios ==="
    ./tests/e2e/scenario-complete-switchover.sh $PRIMARY_CONTEXT $SECONDARY_CONTEXT $REPORT_DIR
    ./tests/e2e/scenario-failure-recovery.sh $PRIMARY_CONTEXT $SECONDARY_CONTEXT $REPORT_DIR
    ./tests/e2e/scenario-edge-cases.sh $PRIMARY_CONTEXT $SECONDARY_CONTEXT $REPORT_DIR
    ;;
  "switchover")
    echo "=== Running Complete Switchover Scenario ==="
    ./tests/e2e/scenario-complete-switchover.sh $PRIMARY_CONTEXT $SECONDARY_CONTEXT $REPORT_DIR
    ;;
  "recovery")
    echo "=== Running Failure Recovery Scenario ==="
    ./tests/e2e/scenario-failure-recovery.sh $PRIMARY_CONTEXT $SECONDARY_CONTEXT $REPORT_DIR
    ;;
  "edge-cases")
    echo "=== Running Edge Cases Scenario ==="
    ./tests/e2e/scenario-edge-cases.sh $PRIMARY_CONTEXT $SECONDARY_CONTEXT $REPORT_DIR
    ;;
esac

# Generate final report
echo "=== Generating Test Report ==="
./tests/e2e/generate-report.sh $REPORT_DIR

echo ""
echo "E2E Test Suite completed successfully!"
echo "Report available at: $REPORT_DIR/final-report.html"
```

### Test Components Structure

```text
tests/e2e/
├── run_e2e_tests.sh              # Master orchestrator
├── environment-validator.sh       # Environment readiness checks
├── scenario-complete-switchover.sh  # Full switchover tests
├── scenario-failure-recovery.sh     # Failure injection tests
├── scenario-edge-cases.sh           # Edge case tests
├── generate-report.sh               # Report generation
├── lib/
│   ├── test-utils.sh             # Common test utilities
│   ├── state-monitor.sh          # State monitoring
│   └── cleanup-manager.sh        # Environment cleanup
└── templates/
    ├── test-report.html          # Report template
    └── validation-checklist.md   # Validation checklist
```

## Troubleshooting Guide

### Common Failure Points

1. **RBAC Permission Issues**
   ```bash
   # Validate RBAC permissions
   python check_rbac.py --context mgmt1 --role operator
   python check_rbac.py --context mgmt2 --role operator
   
   # Deploy missing RBAC
   kubectl --context mgmt1 apply -f deploy/rbac/
   kubectl --context mgmt2 apply -f deploy/rbac/
   ```

2. **Network Connectivity Problems**
   ```bash
   # Test connectivity between hubs
   oc --context mgmt1 get pods -n open-cluster-management-hub
   oc --context mgmt2 get pods -n open-cluster-management-hub
   
   # Check network policies
   oc --context mgmt1 get networkpolicy -n open-cluster-management
   oc --context mgmt2 get networkpolicy -n open-cluster-management
   ```

3. **Backup/Restore Issues**
   ```bash
   # Check OADP status
   oc --context mgmt1 get dpa -n openshift-adp -o yaml
   oc --context mgmt2 get dpa -n openshift-adp -o yaml
   
   # Verify backup storage
   oc --context mgmt1 get backup -n openshift-adp
   oc --context mgmt2 get restore -n openshift-adp
   ```

4. **State File Corruption**
   ```bash
   # Inspect state file
   python show_state.py --primary-context mgmt1 --secondary-context mgmt2
   
   # Clear corrupted state (if needed)
   rm -f .state/switchover-mgmt1__mgmt2.json
   ```

### Debugging Tools

**Enhanced Logging:**
```bash
# Enable verbose logging
python acm_switchover.py --verbose --primary-context mgmt1 --secondary-context mgmt2 --method passive --dry-run

# Monitor real-time logs
tail -f .state/switchover-mgmt1__mgmt2.log
```

**Manual Validation Commands:**
```bash
# Check managed cluster connections
kubectl --context mgmt1 get managedclusters -o wide
kubectl --context mgmt2 get managedclusters -o wide

# Verify klusterlet status
kubectl --context prod1 get klusterlet -n open-cluster-management-agent
kubectl --context prod2 get klusterlet -n open-cluster-management-agent
kubectl --context prod3 get klusterlet -n open-cluster-management-agent

# Check backup status
kubectl --context mgmt1 get backupschedules -n openshift-adp
kubectl --context mgmt2 get backupschedules -n openshift-adp
   ./scripts/preflight-check.sh --primary-context mgmt1 --secondary-context mgmt2 --method passive
   ```

2. **Create Test Framework**
   ```bash
   # Create E2E test directory structure
   mkdir -p tests/e2e/{lib,templates}
   
   # Implement test scripts (refer to framework structure above)
   ```

3. **Baseline Validation**
   ```bash
   # Run initial validation to establish baseline
   ./tests/e2e/environment-validator.sh --primary mgmt1 --secondary mgmt2 --clusters prod1,prod2,prod3
   ```

### Recommended Validation Sequence

1. **Start with validate-only mode** to ensure environment readiness
2. **Progress to dry-run mode** for execution validation
3. **Execute full switchover** with comprehensive monitoring
4. **Test rollback and recovery** scenarios
5. **Document all findings** and update procedures

## Success Metrics

**Test Coverage:**
- ✅ All switchover phases tested
- ✅ All failure scenarios covered
- ✅ Performance benchmarks established
- ✅ Recovery procedures validated

**Quality Gates:**
- ✅ Zero data loss during switchover
- ✅ All managed clusters successfully transition
- ✅ Service continuity maintained
- ✅ Rollback capability preserved

**Documentation:**
- ✅ Test procedures documented
- ✅ Troubleshooting guide created
- ✅ Performance baselines established
- ✅ Lessons learned captured

---

**Last Updated**: December 30, 2025
**Version**: 1.0
**Environment**: mgmt1 → mgmt2 with prod1, prod2, prod3
