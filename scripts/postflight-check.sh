#!/bin/bash
#
# ACM Switchover Post-flight Verification Script
# 
# This script validates that the ACM switchover completed successfully by validating
# all critical components on the new hub and optionally comparing with the old hub.
#
# IDEMPOTENT: This script is read-only and can be run multiple times without
# side effects. It performs only GET operations and does not modify cluster state.
#
# Usage:
#   ./scripts/postflight-check.sh --new-hub-context <context> [--old-hub-context <context>]
#
# Exit codes:
#   0 - All checks passed
#   1 - One or more critical checks failed
#   2 - Invalid arguments

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

# Parse arguments
NEW_HUB_CONTEXT=""
OLD_HUB_CONTEXT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --new-hub-context)
            NEW_HUB_CONTEXT="$2"
            shift 2
            ;;
        --old-hub-context)
            OLD_HUB_CONTEXT="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 --new-hub-context <context> [--old-hub-context <context>]"
            echo ""
            echo "Options:"
            echo "  --new-hub-context     Kubernetes context for new active hub (required)"
            echo "  --old-hub-context     Kubernetes context for old primary hub (optional)"
            echo "  --help, -h            Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 2
            ;;
    esac
done

# Validate required arguments
if [[ -z "$NEW_HUB_CONTEXT" ]]; then
    echo -e "${RED}Error: --new-hub-context is required${NC}"
    echo "Use --help for usage information"
    exit 2
fi

# Helper functions
check_pass() {
    ((TOTAL_CHECKS++))
    ((PASSED_CHECKS++))
    echo -e "${GREEN}✓${NC} $1"
}

check_fail() {
    ((TOTAL_CHECKS++))
    ((FAILED_CHECKS++))
    echo -e "${RED}✗${NC} $1"
}

check_warn() {
    ((TOTAL_CHECKS++))
    ((WARNING_CHECKS++))
    echo -e "${YELLOW}⚠${NC} $1"
}

section_header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Main validation
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   ACM Switchover Post-flight Verification                 ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "New Hub:        $NEW_HUB_CONTEXT"
if [[ -n "$OLD_HUB_CONTEXT" ]]; then
    echo "Old Hub:        $OLD_HUB_CONTEXT (for comparison)"
fi
echo ""

# Check 1: Verify restore completed
section_header "1. Checking Restore Status"

RESTORES=$(oc --context="$NEW_HUB_CONTEXT" get restore -n open-cluster-management-backup --no-headers 2>/dev/null | wc -l)
if [[ $RESTORES -gt 0 ]]; then
    # Check for any restore (passive-sync, activate, or full)
    RESTORE_NAME=$(oc --context="$NEW_HUB_CONTEXT" get restore -n open-cluster-management-backup -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    RESTORE_PHASE=$(oc --context="$NEW_HUB_CONTEXT" get restore "$RESTORE_NAME" -n open-cluster-management-backup -o jsonpath='{.status.phase}' 2>/dev/null)
    
    if [[ "$RESTORE_PHASE" == "Finished" ]]; then
        check_pass "Restore '$RESTORE_NAME' completed successfully (Phase: Finished)"
    elif [[ "$RESTORE_PHASE" == "Enabled" ]]; then
        check_warn "Restore '$RESTORE_NAME' in Enabled state (passive sync may still be running)"
    else
        check_fail "Restore '$RESTORE_NAME' in unexpected state: $RESTORE_PHASE"
    fi
else
    check_fail "No restore resources found on new hub"
fi

# Check 2: Verify ManagedClusters are connected
section_header "2. Checking ManagedCluster Status"

TOTAL_CLUSTERS=$(oc --context="$NEW_HUB_CONTEXT" get managedclusters --no-headers 2>/dev/null | grep -c -v local-cluster)
if [[ $TOTAL_CLUSTERS -gt 0 ]]; then
    check_pass "Found $TOTAL_CLUSTERS managed cluster(s) (excluding local-cluster)"
    
    # Check Available status
    AVAILABLE_CLUSTERS=$(oc --context="$NEW_HUB_CONTEXT" get managedclusters -o json 2>/dev/null | \
        jq -r '.items[] | select(.metadata.name != "local-cluster") | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status=="True")) | .metadata.name' | wc -l)
    
    if [[ $AVAILABLE_CLUSTERS -eq $TOTAL_CLUSTERS ]]; then
        check_pass "All $TOTAL_CLUSTERS cluster(s) show Available=True"
    else
        check_fail "Only $AVAILABLE_CLUSTERS of $TOTAL_CLUSTERS cluster(s) are Available"
        
        # List unavailable clusters
        UNAVAILABLE=$(oc --context="$NEW_HUB_CONTEXT" get managedclusters -o json 2>/dev/null | \
            jq -r '.items[] | select(.metadata.name != "local-cluster") | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status!="True")) | .metadata.name')
        if [[ -n "$UNAVAILABLE" ]]; then
            echo -e "${RED}       Unavailable clusters: $UNAVAILABLE${NC}"
        fi
    fi
    
    # Check Joined status
    JOINED_CLUSTERS=$(oc --context="$NEW_HUB_CONTEXT" get managedclusters -o json 2>/dev/null | \
        jq -r '.items[] | select(.metadata.name != "local-cluster") | select(.status.conditions[]? | select(.type=="ManagedClusterJoined" and .status=="True")) | .metadata.name' | wc -l)
    
    if [[ $JOINED_CLUSTERS -eq $TOTAL_CLUSTERS ]]; then
        check_pass "All $TOTAL_CLUSTERS cluster(s) show Joined=True"
    else
        check_warn "$JOINED_CLUSTERS of $TOTAL_CLUSTERS cluster(s) are Joined (some may still be connecting)"
    fi
    
    # Check for Pending Import
    PENDING_IMPORT=$(oc --context="$NEW_HUB_CONTEXT" get managedclusters 2>/dev/null | grep -c "Pending Import")
    if [[ $PENDING_IMPORT -eq 0 ]]; then
        check_pass "No clusters stuck in Pending Import"
    else
        check_warn "$PENDING_IMPORT cluster(s) in Pending Import state (may need time to auto-import)"
    fi
else
    check_fail "No managed clusters found on new hub"
fi

# Check 3: Verify Observability pods
section_header "3. Checking Observability Components"

if oc --context="$NEW_HUB_CONTEXT" get namespace open-cluster-management-observability &> /dev/null; then
    check_pass "Observability namespace exists"

    # Check critical pods
    CRITICAL_PODS=("observability-grafana" "observability-observatorium-api" "observability-thanos-query")

    for pod_prefix in "${CRITICAL_PODS[@]}"; do
        POD_COUNT=$(oc --context="$NEW_HUB_CONTEXT" get pods -n open-cluster-management-observability -l "app=${pod_prefix}" --no-headers 2>/dev/null | grep -c "Running" || echo "0")
        if [[ $POD_COUNT -gt 0 ]]; then
            check_pass "${pod_prefix}: $POD_COUNT pod(s) running"
        else
            check_fail "${pod_prefix}: No running pods found"
        fi
    done

    # Check for any pods in error state
    ERROR_PODS=$(oc --context="$NEW_HUB_CONTEXT" get pods -n open-cluster-management-observability --no-headers 2>/dev/null | \
        grep -E -c "Error|CrashLoopBackOff|ImagePullBackOff")
    if [[ $ERROR_PODS -eq 0 ]]; then
        check_pass "No pods in error state"
    else
        check_fail "$ERROR_PODS pod(s) in error state"
    fi

    # Check observatorium-api specifically (critical for metrics)
    OBSERVATORIUM_API_PODS=$(oc --context="$NEW_HUB_CONTEXT" get pods -n open-cluster-management-observability -l "app.kubernetes.io/name=observatorium-api" --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    if [[ $OBSERVATORIUM_API_PODS -gt 0 ]]; then
        # Check if pods were recently restarted (should be after switchover)
        RESTART_TIME=$(oc --context="$NEW_HUB_CONTEXT" get pods -n open-cluster-management-observability -l "app.kubernetes.io/name=observatorium-api" -o jsonpath='{.items[0].status.startTime}' 2>/dev/null)
        check_pass "observatorium-api pods running (started: $RESTART_TIME)"
    else
        check_fail "observatorium-api pods not running (critical for metrics)"
    fi
else
    check_pass "Observability not installed (optional component)"
fi

# Check 4: Verify Grafana metrics (if observability exists)
section_header "4. Checking Metrics Collection"

if oc --context="$NEW_HUB_CONTEXT" get namespace open-cluster-management-observability &> /dev/null; then
    # Get Grafana route
    GRAFANA_ROUTE=$(oc --context="$NEW_HUB_CONTEXT" get route grafana -n open-cluster-management-observability -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
    
    if [[ -n "$GRAFANA_ROUTE" ]]; then
        check_pass "Grafana route accessible: https://$GRAFANA_ROUTE"
    else
        check_warn "Grafana route not found"
    fi
    
    # Check metrics-collector on managed clusters (sample check)
    if [[ $TOTAL_CLUSTERS -gt 0 ]]; then
        # This is informational - we can't easily check all managed clusters
        check_warn "Verify metrics-collector pods on managed clusters manually (oc get pods -n open-cluster-management-addon-observability)"
        echo -e "${YELLOW}       Wait 5-10 minutes after switchover for metrics to appear in Grafana${NC}"
    fi
fi

# Check 5: Verify BackupSchedule is enabled
section_header "5. Checking Backup Configuration"

BACKUP_SCHEDULE=$(oc --context="$NEW_HUB_CONTEXT" get backupschedule -n open-cluster-management-backup --no-headers 2>/dev/null | wc -l)
if [[ $BACKUP_SCHEDULE -gt 0 ]]; then
    SCHEDULE_NAME=$(oc --context="$NEW_HUB_CONTEXT" get backupschedule -n open-cluster-management-backup -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    PAUSED=$(oc --context="$NEW_HUB_CONTEXT" get backupschedule "$SCHEDULE_NAME" -n open-cluster-management-backup -o jsonpath='{.spec.paused}' 2>/dev/null)
    
    if [[ "$PAUSED" == "false" ]] || [[ -z "$PAUSED" ]]; then
        check_pass "BackupSchedule '$SCHEDULE_NAME' is enabled (not paused)"
    else
        check_fail "BackupSchedule '$SCHEDULE_NAME' is paused (should be enabled on new hub)"
    fi
    
    # Check for recent backups
    RECENT_BACKUPS=$(oc --context="$NEW_HUB_CONTEXT" get backup -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp --no-headers 2>/dev/null | tail -3 | wc -l)
    if [[ $RECENT_BACKUPS -gt 0 ]]; then
        LATEST_BACKUP=$(oc --context="$NEW_HUB_CONTEXT" get backup -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null)
        LATEST_PHASE=$(oc --context="$NEW_HUB_CONTEXT" get backup "$LATEST_BACKUP" -n open-cluster-management-backup -o jsonpath='{.status.phase}' 2>/dev/null)
        LATEST_TIME=$(oc --context="$NEW_HUB_CONTEXT" get backup "$LATEST_BACKUP" -n open-cluster-management-backup -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null)
        
        check_pass "Latest backup: '$LATEST_BACKUP' (Phase: $LATEST_PHASE, Created: $LATEST_TIME)"
    else
        check_warn "No recent backups found (may take time for first backup to run)"
    fi
else
    check_fail "No BackupSchedule found on new hub"
fi

# Check 6: Verify ACM hub components
section_header "6. Checking ACM Hub Components"

MCH_COUNT=$(oc --context="$NEW_HUB_CONTEXT" get mch -n open-cluster-management --no-headers 2>/dev/null | wc -l)
if [[ $MCH_COUNT -eq 1 ]]; then
    MCH_NAME=$(oc --context="$NEW_HUB_CONTEXT" get mch -n open-cluster-management -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    MCH_PHASE=$(oc --context="$NEW_HUB_CONTEXT" get mch "$MCH_NAME" -n open-cluster-management -o jsonpath='{.status.phase}' 2>/dev/null)
    
    if [[ "$MCH_PHASE" == "Running" ]]; then
        check_pass "MultiClusterHub '$MCH_NAME' is Running"
    else
        check_fail "MultiClusterHub '$MCH_NAME' in unexpected phase: $MCH_PHASE"
    fi
else
    check_fail "Expected 1 MultiClusterHub, found $MCH_COUNT"
fi

# Check ACM pods
ACM_PODS_RUNNING=$(oc --context="$NEW_HUB_CONTEXT" get pods -n open-cluster-management --no-headers 2>/dev/null | grep -c "Running" || echo "0")
ACM_PODS_TOTAL=$(oc --context="$NEW_HUB_CONTEXT" get pods -n open-cluster-management --no-headers 2>/dev/null | wc -l)

if [[ $ACM_PODS_RUNNING -eq $ACM_PODS_TOTAL ]] && [[ $ACM_PODS_TOTAL -gt 0 ]]; then
    check_pass "All $ACM_PODS_TOTAL ACM pods are Running"
else
    check_warn "$ACM_PODS_RUNNING of $ACM_PODS_TOTAL ACM pods are Running"
fi

# Check 7: Compare with old hub (if provided)
if [[ -n "$OLD_HUB_CONTEXT" ]]; then
    section_header "7. Comparing with Old Hub"
    
    # Check old hub cluster status
    OLD_CLUSTERS=$(oc --context="$OLD_HUB_CONTEXT" get managedclusters --no-headers 2>/dev/null | grep -c -v local-cluster)
    if [[ $OLD_CLUSTERS -gt 0 ]]; then
        OLD_UNKNOWN=$(oc --context="$OLD_HUB_CONTEXT" get managedclusters -o json 2>/dev/null | \
            jq -r '.items[] | select(.metadata.name != "local-cluster") | select(.status.conditions[]? | select(.type=="ManagedClusterConditionAvailable" and .status!="True")) | .metadata.name' | wc -l)
        
        if [[ $OLD_UNKNOWN -eq $OLD_CLUSTERS ]]; then
            check_pass "All $OLD_CLUSTERS cluster(s) on old hub show as Unknown/Unavailable (expected)"
        else
            check_warn "Some clusters on old hub still show as Available (may need time to disconnect)"
        fi
    fi
    
    # Check if old hub BackupSchedule is paused
    OLD_SCHEDULE=$(oc --context="$OLD_HUB_CONTEXT" get backupschedule -n open-cluster-management-backup --no-headers 2>/dev/null | wc -l)
    if [[ $OLD_SCHEDULE -gt 0 ]]; then
        OLD_SCHEDULE_NAME=$(oc --context="$OLD_HUB_CONTEXT" get backupschedule -n open-cluster-management-backup -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        OLD_PAUSED=$(oc --context="$OLD_HUB_CONTEXT" get backupschedule "$OLD_SCHEDULE_NAME" -n open-cluster-management-backup -o jsonpath='{.spec.paused}' 2>/dev/null)
        
        if [[ "$OLD_PAUSED" == "true" ]]; then
            check_pass "Old hub BackupSchedule is paused (expected)"
        else
            check_warn "Old hub BackupSchedule is not paused (should be paused after switchover)"
        fi
    fi
    
    # Check Thanos compactor on old hub
    OLD_COMPACTOR=$(oc --context="$OLD_HUB_CONTEXT" get pods -n open-cluster-management-observability -l "app.kubernetes.io/name=thanos-compact" --no-headers 2>/dev/null | wc -l)
    if [[ $OLD_COMPACTOR -eq 0 ]]; then
        check_pass "Old hub Thanos compactor is stopped (expected)"
    else
        check_warn "Old hub Thanos compactor is still running (should be scaled to 0)"
    fi
fi

# Check 8: Verify no auto-import disabled annotations
section_header "8. Checking Auto-Import Status"

DISABLED_AUTO_IMPORT=$(oc --context="$NEW_HUB_CONTEXT" get managedclusters -o json 2>/dev/null | \
    jq -r '.items[] | select(.metadata.name != "local-cluster") | select(.metadata.annotations["import.open-cluster-management.io/disable-auto-import"] != null) | .metadata.name' | wc -l)

if [[ $DISABLED_AUTO_IMPORT -eq 0 ]]; then
    check_pass "No clusters have disable-auto-import annotation on new hub (expected)"
else
    check_warn "$DISABLED_AUTO_IMPORT cluster(s) have disable-auto-import annotation (may be intentional)"
fi

# Summary
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   Verification Summary                                     ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo -e "Total Checks:    $TOTAL_CHECKS"
echo -e "${GREEN}Passed:          $PASSED_CHECKS${NC}"
echo -e "${RED}Failed:          $FAILED_CHECKS${NC}"
echo -e "${YELLOW}Warnings:        $WARNING_CHECKS${NC}"
echo ""

if [[ $FAILED_CHECKS -eq 0 ]]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ SWITCHOVER VERIFICATION PASSED${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "The ACM switchover appears to have completed successfully."
    echo ""
    if [[ $WARNING_CHECKS -gt 0 ]]; then
        echo -e "${YELLOW}Note: $WARNING_CHECKS warning(s) detected. Review them above.${NC}"
        echo -e "${YELLOW}Some items may need time to stabilize (e.g., metrics collection).${NC}"
        echo ""
    fi
    echo "Recommended next steps:"
    echo "  1. Verify Grafana dashboards show recent metrics (wait 5-10 minutes)"
    echo "  2. Test cluster management operations (create/update policies, etc.)"
    echo "  3. Monitor for 24 hours before decommissioning old hub"
    echo "  4. Inform stakeholders that switchover is complete"
    echo ""
    exit 0
else
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}✗ VERIFICATION FAILED${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Critical issues detected. Review the failed checks above."
    echo ""
    echo "Common issues and solutions:"
    echo "  - Clusters not Available: Wait 5-10 minutes for reconnection"
    echo "  - Restore not Finished: Check restore status with 'oc describe restore'"
    echo "  - Observability pods failing: Verify observatorium-api was restarted"
    echo "  - BackupSchedule paused: Unpause with 'oc patch backupschedule ...'"
    echo ""
    echo "If issues persist, consider rollback procedure in the runbook."
    echo ""
    exit 1
fi
