#!/bin/bash
#
# ACM Switchover Pre-flight Validation Script
# 
# This script automates the prerequisite checks before starting an ACM switchover.
# It validates both primary and secondary hubs to catch issues early.
#
# IDEMPOTENT: This script is read-only and can be run multiple times without
# side effects. It performs only GET operations and does not modify cluster state.
#
# Usage:
#   ./scripts/preflight-check.sh --primary-context <primary> --secondary-context <secondary> [--method passive|full]
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
PRIMARY_CONTEXT=""
SECONDARY_CONTEXT=""
METHOD="passive"

while [[ $# -gt 0 ]]; do
    case $1 in
        --primary-context)
            PRIMARY_CONTEXT="$2"
            shift 2
            ;;
        --secondary-context)
            SECONDARY_CONTEXT="$2"
            shift 2
            ;;
        --method)
            METHOD="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 --primary-context <primary> --secondary-context <secondary> [--method passive|full]"
            echo ""
            echo "Options:"
            echo "  --primary-context     Kubernetes context for primary hub (required)"
            echo "  --secondary-context   Kubernetes context for secondary hub (required)"
            echo "  --method              Switchover method: passive (default) or full"
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
if [[ -z "$PRIMARY_CONTEXT" ]] || [[ -z "$SECONDARY_CONTEXT" ]]; then
    echo -e "${RED}Error: Both --primary-context and --secondary-context are required${NC}"
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
echo "║   ACM Switchover Pre-flight Validation                    ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Primary Hub:    $PRIMARY_CONTEXT"
echo "Secondary Hub:  $SECONDARY_CONTEXT"
echo "Method:         $METHOD"
echo ""

# Check 1: Verify CLI tools
section_header "1. Checking CLI Tools"

if command -v oc &> /dev/null; then
    check_pass "OpenShift CLI (oc) is installed"
elif command -v kubectl &> /dev/null; then
    check_pass "Kubernetes CLI (kubectl) is installed"
else
    check_fail "Neither oc nor kubectl CLI found"
fi

if command -v jq &> /dev/null; then
    check_pass "jq is installed"
else
    check_warn "jq not found (optional, but recommended for some commands)"
fi

# Check 2: Verify contexts exist
section_header "2. Verifying Kubernetes Contexts"

if oc config get-contexts "$PRIMARY_CONTEXT" &> /dev/null; then
    check_pass "Primary context '$PRIMARY_CONTEXT' exists"
else
    check_fail "Primary context '$PRIMARY_CONTEXT' not found"
fi

if oc config get-contexts "$SECONDARY_CONTEXT" &> /dev/null; then
    check_pass "Secondary context '$SECONDARY_CONTEXT' exists"
else
    check_fail "Secondary context '$SECONDARY_CONTEXT' not found"
fi

# Check 3: Verify namespace access
section_header "3. Verifying Namespace Access"

if oc --context="$PRIMARY_CONTEXT" get namespace open-cluster-management &> /dev/null; then
    check_pass "Primary hub: open-cluster-management namespace exists"
else
    check_fail "Primary hub: open-cluster-management namespace not found"
fi

if oc --context="$PRIMARY_CONTEXT" get namespace open-cluster-management-backup &> /dev/null; then
    check_pass "Primary hub: open-cluster-management-backup namespace exists"
else
    check_fail "Primary hub: open-cluster-management-backup namespace not found"
fi

if oc --context="$SECONDARY_CONTEXT" get namespace open-cluster-management &> /dev/null; then
    check_pass "Secondary hub: open-cluster-management namespace exists"
else
    check_fail "Secondary hub: open-cluster-management namespace not found"
fi

if oc --context="$SECONDARY_CONTEXT" get namespace open-cluster-management-backup &> /dev/null; then
    check_pass "Secondary hub: open-cluster-management-backup namespace exists"
else
    check_fail "Secondary hub: open-cluster-management-backup namespace not found"
fi

# Check 4: Verify ACM versions
section_header "4. Checking ACM Versions"

PRIMARY_VERSION=$(oc --context="$PRIMARY_CONTEXT" get mch -n open-cluster-management -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")
SECONDARY_VERSION=$(oc --context="$SECONDARY_CONTEXT" get mch -n open-cluster-management -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")

if [[ "$PRIMARY_VERSION" != "unknown" ]]; then
    check_pass "Primary hub ACM version: $PRIMARY_VERSION"
else
    check_fail "Primary hub: Could not detect ACM version"
fi

if [[ "$SECONDARY_VERSION" != "unknown" ]]; then
    check_pass "Secondary hub ACM version: $SECONDARY_VERSION"
else
    check_fail "Secondary hub: Could not detect ACM version"
fi

if [[ "$PRIMARY_VERSION" == "$SECONDARY_VERSION" ]] && [[ "$PRIMARY_VERSION" != "unknown" ]]; then
    check_pass "ACM versions match between hubs"
else
    check_fail "ACM version mismatch: Primary=$PRIMARY_VERSION, Secondary=$SECONDARY_VERSION"
fi

# Check 5: Verify OADP operator
section_header "5. Checking OADP Operator"

if oc --context="$PRIMARY_CONTEXT" get namespace openshift-adp &> /dev/null; then
    VELERO_PODS=$(oc --context="$PRIMARY_CONTEXT" get pods -n openshift-adp -l app.kubernetes.io/name=velero --no-headers 2>/dev/null | wc -l)
    if [[ $VELERO_PODS -gt 0 ]]; then
        check_pass "Primary hub: OADP operator installed ($VELERO_PODS Velero pod(s))"
    else
        check_fail "Primary hub: OADP namespace exists but no Velero pods found"
    fi
else
    check_fail "Primary hub: OADP operator not installed (openshift-adp namespace missing)"
fi

if oc --context="$SECONDARY_CONTEXT" get namespace openshift-adp &> /dev/null; then
    VELERO_PODS=$(oc --context="$SECONDARY_CONTEXT" get pods -n openshift-adp -l app.kubernetes.io/name=velero --no-headers 2>/dev/null | wc -l)
    if [[ $VELERO_PODS -gt 0 ]]; then
        check_pass "Secondary hub: OADP operator installed ($VELERO_PODS Velero pod(s))"
    else
        check_fail "Secondary hub: OADP namespace exists but no Velero pods found"
    fi
else
    check_fail "Secondary hub: OADP operator not installed (openshift-adp namespace missing)"
fi

# Check 6: Verify DataProtectionApplication
section_header "6. Checking DataProtectionApplication"

PRIMARY_DPA=$(oc --context="$PRIMARY_CONTEXT" get dpa -n openshift-adp --no-headers 2>/dev/null | wc -l)
if [[ $PRIMARY_DPA -gt 0 ]]; then
    DPA_NAME=$(oc --context="$PRIMARY_CONTEXT" get dpa -n openshift-adp -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    DPA_RECONCILED=$(oc --context="$PRIMARY_CONTEXT" get dpa "$DPA_NAME" -n openshift-adp -o jsonpath='{.status.conditions[?(@.type=="Reconciled")].status}' 2>/dev/null)
    if [[ "$DPA_RECONCILED" == "True" ]]; then
        check_pass "Primary hub: DataProtectionApplication '$DPA_NAME' is reconciled"
    else
        check_fail "Primary hub: DataProtectionApplication '$DPA_NAME' exists but not reconciled"
    fi
else
    check_fail "Primary hub: No DataProtectionApplication found"
fi

SECONDARY_DPA=$(oc --context="$SECONDARY_CONTEXT" get dpa -n openshift-adp --no-headers 2>/dev/null | wc -l)
if [[ $SECONDARY_DPA -gt 0 ]]; then
    DPA_NAME=$(oc --context="$SECONDARY_CONTEXT" get dpa -n openshift-adp -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    DPA_RECONCILED=$(oc --context="$SECONDARY_CONTEXT" get dpa "$DPA_NAME" -n openshift-adp -o jsonpath='{.status.conditions[?(@.type=="Reconciled")].status}' 2>/dev/null)
    if [[ "$DPA_RECONCILED" == "True" ]]; then
        check_pass "Secondary hub: DataProtectionApplication '$DPA_NAME' is reconciled"
    else
        check_fail "Secondary hub: DataProtectionApplication '$DPA_NAME' exists but not reconciled"
    fi
else
    check_fail "Secondary hub: No DataProtectionApplication found"
fi

# Check 7: Verify backup status
section_header "7. Checking Backup Status"

BACKUPS=$(oc --context="$PRIMARY_CONTEXT" get backup -n open-cluster-management-backup --no-headers 2>/dev/null | wc -l)
if [[ $BACKUPS -gt 0 ]]; then
    check_pass "Primary hub: Found $BACKUPS backup(s)"
    
    # Check for in-progress backups
    IN_PROGRESS=$(oc --context="$PRIMARY_CONTEXT" get backup -n open-cluster-management-backup -o jsonpath='{.items[?(@.status.phase=="InProgress")].metadata.name}' 2>/dev/null)
    if [[ -z "$IN_PROGRESS" ]]; then
        check_pass "Primary hub: No backups in progress"
    else
        check_fail "Primary hub: Backup(s) in progress: $IN_PROGRESS"
    fi
    
    # Check latest backup
    LATEST_BACKUP=$(oc --context="$PRIMARY_CONTEXT" get backup -n open-cluster-management-backup --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null)
    LATEST_PHASE=$(oc --context="$PRIMARY_CONTEXT" get backup "$LATEST_BACKUP" -n open-cluster-management-backup -o jsonpath='{.status.phase}' 2>/dev/null)
    if [[ "$LATEST_PHASE" == "Finished" ]]; then
        check_pass "Primary hub: Latest backup '$LATEST_BACKUP' completed successfully"
    else
        check_fail "Primary hub: Latest backup '$LATEST_BACKUP' in unexpected state: $LATEST_PHASE"
    fi
else
    check_fail "Primary hub: No backups found"
fi

# Check 8: Verify ClusterDeployment preserveOnDelete (CRITICAL)
section_header "8. Checking ClusterDeployment preserveOnDelete (CRITICAL)"

CDS=$(oc --context="$PRIMARY_CONTEXT" get clusterdeployment --all-namespaces --no-headers 2>/dev/null | wc -l)
if [[ $CDS -eq 0 ]]; then
    check_pass "No ClusterDeployments found (no Hive-managed clusters)"
else
    MISSING_PRESERVE=$(oc --context="$PRIMARY_CONTEXT" get clusterdeployment --all-namespaces -o json 2>/dev/null | \
        jq -r '.items[] | select(.spec.preserveOnDelete != true) | "\(.metadata.namespace)/\(.metadata.name)"' | wc -l)
    
    if [[ $MISSING_PRESERVE -eq 0 ]]; then
        check_pass "All $CDS ClusterDeployment(s) have preserveOnDelete=true"
    else
        MISSING_LIST=$(oc --context="$PRIMARY_CONTEXT" get clusterdeployment --all-namespaces -o json 2>/dev/null | \
            jq -r '.items[] | select(.spec.preserveOnDelete != true) | "\(.metadata.namespace)/\(.metadata.name)"')
        check_fail "ClusterDeployments missing preserveOnDelete=true: $MISSING_LIST"
        echo -e "${RED}       THIS IS CRITICAL! Without preserveOnDelete=true, deleting ManagedClusters will DESTROY infrastructure!${NC}"
    fi
fi

# Check 9: Method-specific checks
if [[ "$METHOD" == "passive" ]]; then
    section_header "9. Checking Passive Sync (Method 1)"
    
    PASSIVE_RESTORE=$(oc --context="$SECONDARY_CONTEXT" get restore restore-acm-passive-sync -n open-cluster-management-backup --no-headers 2>/dev/null | wc -l)
    if [[ $PASSIVE_RESTORE -eq 1 ]]; then
        PHASE=$(oc --context="$SECONDARY_CONTEXT" get restore restore-acm-passive-sync -n open-cluster-management-backup -o jsonpath='{.status.phase}' 2>/dev/null)
        if [[ "$PHASE" == "Enabled" ]]; then
            check_pass "Secondary hub: Passive sync restore exists and is Enabled"
        else
            check_fail "Secondary hub: Passive sync restore exists but phase is: $PHASE (expected: Enabled)"
        fi
    else
        check_fail "Secondary hub: restore-acm-passive-sync not found (required for Method 1)"
    fi
else
    section_header "9. Method 2 (Full Restore) - No passive sync check needed"
    check_pass "Method 2 selected - passive sync not required"
fi

# Check 10: Verify Observability (optional)
section_header "10. Checking ACM Observability (Optional)"

if oc --context="$PRIMARY_CONTEXT" get namespace open-cluster-management-observability &> /dev/null; then
    check_pass "Primary hub: Observability namespace exists"
    
    if oc --context="$SECONDARY_CONTEXT" get namespace open-cluster-management-observability &> /dev/null; then
        check_pass "Secondary hub: Observability namespace exists"
    else
        check_warn "Secondary hub: Observability namespace not found (may need manual setup)"
    fi
else
    check_pass "Observability not detected (optional component)"
fi

# Summary
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   Validation Summary                                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo -e "Total Checks:    $TOTAL_CHECKS"
echo -e "${GREEN}Passed:          $PASSED_CHECKS${NC}"
echo -e "${RED}Failed:          $FAILED_CHECKS${NC}"
echo -e "${YELLOW}Warnings:        $WARNING_CHECKS${NC}"
echo ""

if [[ $FAILED_CHECKS -eq 0 ]]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓ ALL CRITICAL CHECKS PASSED${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "You are ready to proceed with the switchover."
    echo ""
    if [[ $WARNING_CHECKS -gt 0 ]]; then
        echo -e "${YELLOW}Note: $WARNING_CHECKS warning(s) detected. Review them before proceeding.${NC}"
        echo ""
    fi
    exit 0
else
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}✗ VALIDATION FAILED${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Please fix the failed checks before proceeding with switchover."
    echo "Review the output above for specific issues."
    echo ""
    exit 1
fi
