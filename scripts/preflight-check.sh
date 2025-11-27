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

# Source constants
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/constants.sh" ]]; then
    source "${SCRIPT_DIR}/constants.sh"
else
    echo "Error: constants.sh not found in ${SCRIPT_DIR}"
    exit 1
fi

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

# Arrays to store messages
FAILED_MESSAGES=()
WARNING_MESSAGES=()

# Parse arguments
PRIMARY_CONTEXT=""
SECONDARY_CONTEXT=""
METHOD=""

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
            echo "Usage: $0 --primary-context <primary> --secondary-context <secondary> --method <passive|full>"
            echo ""
            echo "Options:"
            echo "  --primary-context     Kubernetes context for primary hub (required)"
            echo "  --secondary-context   Kubernetes context for secondary hub (required)"
            echo "  --method              Switchover method: passive or full (required)"
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

if [[ -z "$METHOD" ]]; then
    echo -e "${RED}Error: --method is required (passive or full)${NC}"
    echo "Use --help for usage information"
    exit 2
fi

if [[ "$METHOD" != "passive" ]] && [[ "$METHOD" != "full" ]]; then
    echo -e "${RED}Error: --method must be 'passive' or 'full', got '$METHOD'${NC}"
    echo "Use --help for usage information"
    exit 2
fi

# Helper functions
check_pass() {
    ((TOTAL_CHECKS++)) || true
    ((PASSED_CHECKS++)) || true
    echo -e "${GREEN}✓${NC} $1"
}

check_fail() {
    ((TOTAL_CHECKS++)) || true
    ((FAILED_CHECKS++)) || true
    FAILED_MESSAGES+=("$1")
    echo -e "${RED}✗${NC} $1"
}

check_warn() {
    ((TOTAL_CHECKS++)) || true
    ((WARNING_CHECKS++)) || true
    WARNING_MESSAGES+=("$1")
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

CLUSTER_CLI_BIN=""
CLUSTER_CLI_NAME=""

if command -v oc &> /dev/null; then
    CLUSTER_CLI_BIN="oc"
    CLUSTER_CLI_NAME="OpenShift CLI (oc)"
    check_pass "$CLUSTER_CLI_NAME is installed"
elif command -v kubectl &> /dev/null; then
    CLUSTER_CLI_BIN="kubectl"
    CLUSTER_CLI_NAME="Kubernetes CLI (kubectl)"
    # Provide oc alias so the rest of the script can keep using oc invocations
    oc() {
        kubectl "$@"
    }
    check_pass "$CLUSTER_CLI_NAME is installed"
else
    check_fail "Neither oc nor kubectl CLI found"
fi

if command -v jq &> /dev/null; then
    check_pass "jq is installed"
else
    check_warn "jq not found (optional, but recommended for some commands)"
fi

if [[ -n "$CLUSTER_CLI_BIN" ]]; then
    echo "Using CLI: $CLUSTER_CLI_NAME ($(command -v "$CLUSTER_CLI_BIN"))"
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

if oc --context="$PRIMARY_CONTEXT" get namespace "$ACM_NAMESPACE" &> /dev/null; then
    check_pass "Primary hub: $ACM_NAMESPACE namespace exists"
else
    check_fail "Primary hub: $ACM_NAMESPACE namespace not found"
fi

if oc --context="$PRIMARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &> /dev/null; then
    check_pass "Primary hub: $BACKUP_NAMESPACE namespace exists"
else
    check_fail "Primary hub: $BACKUP_NAMESPACE namespace not found"
fi

if oc --context="$SECONDARY_CONTEXT" get namespace "$ACM_NAMESPACE" &> /dev/null; then
    check_pass "Secondary hub: $ACM_NAMESPACE namespace exists"
else
    check_fail "Secondary hub: $ACM_NAMESPACE namespace not found"
fi

if oc --context="$SECONDARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &> /dev/null; then
    check_pass "Secondary hub: $BACKUP_NAMESPACE namespace exists"
else
    check_fail "Secondary hub: $BACKUP_NAMESPACE namespace not found"
fi

# Check 4: Verify ACM versions
section_header "4. Checking ACM Versions"

PRIMARY_VERSION=$(oc --context="$PRIMARY_CONTEXT" get mch -n "$ACM_NAMESPACE" -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")
SECONDARY_VERSION=$(oc --context="$SECONDARY_CONTEXT" get mch -n "$ACM_NAMESPACE" -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")

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

if oc --context="$PRIMARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &> /dev/null; then
    VELERO_PODS=$(oc --context="$PRIMARY_CONTEXT" get pods -n "$BACKUP_NAMESPACE" -l app.kubernetes.io/name=velero --no-headers 2>/dev/null | wc -l)
    if [[ $VELERO_PODS -gt 0 ]]; then
        check_pass "Primary hub: OADP operator installed ($VELERO_PODS Velero pod(s))"
    else
        check_fail "Primary hub: OADP namespace exists but no Velero pods found"
    fi
else
    check_fail "Primary hub: OADP operator not installed ($BACKUP_NAMESPACE namespace missing)"
fi

if oc --context="$SECONDARY_CONTEXT" get namespace "$BACKUP_NAMESPACE" &> /dev/null; then
    VELERO_PODS=$(oc --context="$SECONDARY_CONTEXT" get pods -n "$BACKUP_NAMESPACE" -l app.kubernetes.io/name=velero --no-headers 2>/dev/null | wc -l)
    if [[ $VELERO_PODS -gt 0 ]]; then
        check_pass "Secondary hub: OADP operator installed ($VELERO_PODS Velero pod(s))"
    else
        check_fail "Secondary hub: OADP namespace exists but no Velero pods found"
    fi
else
    check_fail "Secondary hub: OADP operator not installed ($BACKUP_NAMESPACE namespace missing)"
fi

# Check 6: Verify DataProtectionApplication
section_header "6. Checking DataProtectionApplication"

PRIMARY_DPA=$(oc --context="$PRIMARY_CONTEXT" get dpa -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l)
if [[ $PRIMARY_DPA -gt 0 ]]; then
    DPA_NAME=$(oc --context="$PRIMARY_CONTEXT" get dpa -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    DPA_RECONCILED=$(oc --context="$PRIMARY_CONTEXT" get dpa "$DPA_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Reconciled")].status}' 2>/dev/null)
    if [[ "$DPA_RECONCILED" == "True" ]]; then
        check_pass "Primary hub: DataProtectionApplication '$DPA_NAME' is reconciled"
    else
        check_fail "Primary hub: DataProtectionApplication '$DPA_NAME' exists but not reconciled"
    fi
else
    check_fail "Primary hub: No DataProtectionApplication found"
fi

SECONDARY_DPA=$(oc --context="$SECONDARY_CONTEXT" get dpa -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l)
if [[ $SECONDARY_DPA -gt 0 ]]; then
    DPA_NAME=$(oc --context="$SECONDARY_CONTEXT" get dpa -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    DPA_RECONCILED=$(oc --context="$SECONDARY_CONTEXT" get dpa "$DPA_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Reconciled")].status}' 2>/dev/null)
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

BACKUPS=$(oc --context="$PRIMARY_CONTEXT" get backup -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l)
if [[ $BACKUPS -gt 0 ]]; then
    check_pass "Primary hub: Found $BACKUPS backup(s)"
    
    # Check for in-progress backups
    IN_PROGRESS=$(oc --context="$PRIMARY_CONTEXT" get backup -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[?(@.status.phase=="InProgress")].metadata.name}' 2>/dev/null)
    if [[ -z "$IN_PROGRESS" ]]; then
        check_pass "Primary hub: No backups in progress"
    else
        check_fail "Primary hub: Backup(s) in progress: $IN_PROGRESS"
    fi
    
    # Check latest backup
    LATEST_BACKUP=$(oc --context="$PRIMARY_CONTEXT" get backup -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null)
    LATEST_PHASE=$(oc --context="$PRIMARY_CONTEXT" get backup "$LATEST_BACKUP" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null)
    if [[ "$LATEST_PHASE" == "Finished" ]] || [[ "$LATEST_PHASE" == "Completed" ]]; then
        check_pass "Primary hub: Latest backup '$LATEST_BACKUP' completed successfully"
        
        # Check if all joined ManagedClusters are included in the backup
        # This prevents data loss when clusters were imported after the last backup
        JOINED_CLUSTERS=$(oc --context="$PRIMARY_CONTEXT" get managedclusters -o json 2>/dev/null | \
            jq -r '.items[] | select(.metadata.name != "local-cluster") | select(.status.conditions[]? | select(.type=="ManagedClusterJoined" and .status=="True")) | .metadata.name' | sort)
        
        # Get the managed clusters backup name from the latest backup
        CLUSTERS_BACKUP_NAME=$(oc --context="$PRIMARY_CONTEXT" get backup "$LATEST_BACKUP" -n "$BACKUP_NAMESPACE" -o jsonpath='{.metadata.labels.cluster\.open-cluster-management\.io/backup-cluster}' 2>/dev/null || echo "")
        
        if [[ -n "$CLUSTERS_BACKUP_NAME" ]] && [[ -n "$JOINED_CLUSTERS" ]]; then
            # Get clusters from the managed clusters backup
            BACKED_UP_CLUSTERS=$(oc --context="$PRIMARY_CONTEXT" get backup "$CLUSTERS_BACKUP_NAME" -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | \
                jq -r '.status.progress.itemsBackedUp // [] | .[]' 2>/dev/null | grep -E '^managedclusters/' | sed 's|managedclusters/||' | sort || echo "")
            
            # Find clusters that are joined but not in backup
            MISSING_FROM_BACKUP=""
            for cluster in $JOINED_CLUSTERS; do
                if ! echo "$BACKED_UP_CLUSTERS" | grep -q "^${cluster}$"; then
                    MISSING_FROM_BACKUP="$MISSING_FROM_BACKUP $cluster"
                fi
            done
            
            if [[ -z "$MISSING_FROM_BACKUP" ]]; then
                check_pass "Primary hub: All joined ManagedClusters are included in the latest backup"
            else
                check_warn "Primary hub: Some joined clusters may not be in latest backup:$MISSING_FROM_BACKUP"
                echo -e "${YELLOW}       Consider running a new backup before switchover to include recently imported clusters${NC}"
            fi
        else
            check_warn "Primary hub: Could not verify ManagedCluster backup coverage (backup metadata unavailable)"
        fi
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
    
    # Find the latest restore in the namespace
    PASSIVE_RESTORE_NAME=$(oc --context="$SECONDARY_CONTEXT" get restore -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || true)
    
    if [[ -n "$PASSIVE_RESTORE_NAME" ]]; then
        PHASE=$(oc --context="$SECONDARY_CONTEXT" get restore "$PASSIVE_RESTORE_NAME" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null)
        if [[ "$PHASE" == "Enabled" ]] || [[ "$PHASE" == "Completed" ]] || [[ "$PHASE" == "Finished" ]]; then
            check_pass "Secondary hub: Found restore '$PASSIVE_RESTORE_NAME' in state: $PHASE"
        else
            check_fail "Secondary hub: Restore '$PASSIVE_RESTORE_NAME' exists but phase is: $PHASE (expected: Enabled, Completed, or Finished)"
        fi
    else
        check_fail "Secondary hub: No restore resources found (required for Method 1)"
    fi
else
    section_header "9. Method 2 (Full Restore) - No passive sync check needed"
    check_pass "Method 2 selected - passive sync not required"
fi

# Check 10: Verify Observability (optional)
section_header "10. Checking ACM Observability (Optional)"

if oc --context="$PRIMARY_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
    check_pass "Primary hub: Observability namespace exists"
    
    # Check MCO CR on primary
    if oc --context="$PRIMARY_CONTEXT" get mco observability -n "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
         check_pass "Primary hub: MultiClusterObservability CR found"
    else
         check_warn "Primary hub: MultiClusterObservability CR not found (but namespace exists)"
    fi
    
    if oc --context="$SECONDARY_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
        check_pass "Secondary hub: Observability namespace exists"

        # Check for object storage secret on secondary (CRITICAL for switchover)
        if oc --context="$SECONDARY_CONTEXT" get secret "$THANOS_OBJECT_STORAGE_SECRET" -n "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
            check_pass "Secondary hub: '$THANOS_OBJECT_STORAGE_SECRET' secret exists"
        else
            check_fail "Secondary hub: '$THANOS_OBJECT_STORAGE_SECRET' secret missing! (Required for Observability)"
        fi
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

if [[ $FAILED_CHECKS -gt 0 ]]; then
    echo -e "${RED}Failed Checks:${NC}"
    for msg in "${FAILED_MESSAGES[@]}"; do
        echo -e "${RED}  - $msg${NC}"
    done
    echo ""
fi

if [[ $WARNING_CHECKS -gt 0 ]]; then
    echo -e "${YELLOW}Warnings:${NC}"
    for msg in "${WARNING_MESSAGES[@]}"; do
        echo -e "${YELLOW}  - $msg${NC}"
    done
    echo ""
fi

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
    echo ""
    exit 1
fi
