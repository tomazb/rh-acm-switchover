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

# Source constants and common library
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/constants.sh" ]]; then
    source "${SCRIPT_DIR}/constants.sh"
else
    echo "Error: constants.sh not found in ${SCRIPT_DIR}"
    exit 1
fi

if [[ -f "${SCRIPT_DIR}/lib-common.sh" ]]; then
    source "${SCRIPT_DIR}/lib-common.sh"
else
    echo "Error: lib-common.sh not found in ${SCRIPT_DIR}"
    exit 1
fi

# Parse arguments
PRIMARY_CONTEXT=""
SECONDARY_CONTEXT=""
METHOD=""
SKIP_GITOPS_CHECK=0
ARGOCD_CHECK=0

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
        --skip-gitops-check)
            SKIP_GITOPS_CHECK=1
            shift
            ;;
        --argocd-check)
            ARGOCD_CHECK=1
            shift
            ;;
        --help|-h)
            echo "Usage: $0 --primary-context <primary> --secondary-context <secondary> --method <passive|full> [--skip-gitops-check] [--argocd-check]"
            echo ""
            echo "Options:"
            echo "  --primary-context     Kubernetes context for primary hub (required)"
            echo "  --secondary-context   Kubernetes context for secondary hub (required)"
            echo "  --method              Switchover method: passive or full (required)"
            echo "  --skip-gitops-check   Disable GitOps marker detection (ArgoCD, Flux)"
            echo "  --argocd-check        Detect ArgoCD instances and ACM resources managed by GitOps"
            echo "  --help, -h            Show this help message"
            exit "$EXIT_SUCCESS"
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit "$EXIT_INVALID_ARGS"
            ;;
    esac
done

# Configure GitOps detection
if [[ $SKIP_GITOPS_CHECK -eq 1 ]]; then
    disable_gitops_detection
fi

# Validate required arguments
if [[ -z "$PRIMARY_CONTEXT" ]] || [[ -z "$SECONDARY_CONTEXT" ]]; then
    echo -e "${RED}Error: Both --primary-context and --secondary-context are required${NC}"
    echo "Use --help for usage information"
    exit "$EXIT_INVALID_ARGS"
fi

if [[ -z "$METHOD" ]]; then
    echo -e "${RED}Error: --method is required (passive or full)${NC}"
    echo "Use --help for usage information"
    exit "$EXIT_INVALID_ARGS"
fi

if [[ "$METHOD" != "passive" ]] && [[ "$METHOD" != "full" ]]; then
    echo -e "${RED}Error: --method must be 'passive' or 'full', got '$METHOD'${NC}"
    echo "Use --help for usage information"
    exit "$EXIT_INVALID_ARGS"
fi

# Main validation
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   ACM Switchover Pre-flight Validation                    ║"
echo "╚════════════════════════════════════════════════════════════╝"
print_script_version "preflight-check.sh"
echo ""
echo "Primary Hub:    $PRIMARY_CONTEXT"
echo "Secondary Hub:  $SECONDARY_CONTEXT"
echo "Method:         $METHOD"
echo ""

# Check 1: Verify CLI tools
section_header "1. Checking CLI Tools"
detect_cluster_cli

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

ACM_PRIMARY_VERSION=$(oc --context="$PRIMARY_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")
ACM_SECONDARY_VERSION=$(oc --context="$SECONDARY_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o jsonpath='{.items[0].status.currentVersion}' 2>/dev/null || echo "unknown")

# Detect GitOps markers on MultiClusterHub (primary)
PRIMARY_MCH_JSON=$(oc --context="$PRIMARY_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o json 2>/dev/null | jq '.items[0]' 2>/dev/null || echo "{}")
if [[ -n "$PRIMARY_MCH_JSON" && "$PRIMARY_MCH_JSON" != "{}" ]]; then
    PRIMARY_MCH_NAME=$(echo "$PRIMARY_MCH_JSON" | jq -r '.metadata.name // "multiclusterhub"')
    GITOPS_MARKERS=$(detect_gitops_markers "$PRIMARY_MCH_JSON")
    if [[ -n "$GITOPS_MARKERS" ]]; then
        collect_gitops_markers "primary" "$ACM_NAMESPACE" "MultiClusterHub" "$PRIMARY_MCH_NAME" "$GITOPS_MARKERS"
    fi
fi

# Detect GitOps markers on MultiClusterHub (secondary)
SECONDARY_MCH_JSON=$(oc --context="$SECONDARY_CONTEXT" get $RES_MCH -n "$ACM_NAMESPACE" -o json 2>/dev/null | jq '.items[0]' 2>/dev/null || echo "{}")
if [[ -n "$SECONDARY_MCH_JSON" && "$SECONDARY_MCH_JSON" != "{}" ]]; then
    SECONDARY_MCH_NAME=$(echo "$SECONDARY_MCH_JSON" | jq -r '.metadata.name // "multiclusterhub"')
    GITOPS_MARKERS=$(detect_gitops_markers "$SECONDARY_MCH_JSON")
    if [[ -n "$GITOPS_MARKERS" ]]; then
        collect_gitops_markers "secondary" "$ACM_NAMESPACE" "MultiClusterHub" "$SECONDARY_MCH_NAME" "$GITOPS_MARKERS"
    fi
fi

if [[ "$ACM_PRIMARY_VERSION" != "unknown" ]]; then
    check_pass "Primary hub ACM version: $ACM_PRIMARY_VERSION"
else
    check_fail "Primary hub: Could not detect ACM version"
fi

if [[ "$ACM_SECONDARY_VERSION" != "unknown" ]]; then
    check_pass "Secondary hub ACM version: $ACM_SECONDARY_VERSION"
else
    check_fail "Secondary hub: Could not detect ACM version"
fi

if [[ "$ACM_PRIMARY_VERSION" == "$ACM_SECONDARY_VERSION" ]] && [[ "$ACM_PRIMARY_VERSION" != "unknown" ]]; then
    check_pass "ACM versions match between hubs"
else
    check_fail "ACM version mismatch: Primary=$ACM_PRIMARY_VERSION, Secondary=$ACM_SECONDARY_VERSION"
fi

# Gather managed cluster counts for both hubs (used in summary and later checks)
PRIMARY_MC_TOTAL=$(get_total_mc_count "$PRIMARY_CONTEXT")
PRIMARY_MC_AVAILABLE=$(get_available_mc_count "$PRIMARY_CONTEXT")
SECONDARY_MC_TOTAL=$(get_total_mc_count "$SECONDARY_CONTEXT")
SECONDARY_MC_AVAILABLE=$(get_available_mc_count "$SECONDARY_CONTEXT")

# Get backup schedule states
PRIMARY_BACKUP_STATE=$(get_backup_schedule_state "$PRIMARY_CONTEXT")
SECONDARY_BACKUP_STATE=$(get_backup_schedule_state "$SECONDARY_CONTEXT")

# Determine hub states for summary
PRIMARY_STATE_DESC="Active primary hub"
if [[ "$PRIMARY_BACKUP_STATE" == "active" ]]; then
    PRIMARY_STATE_DESC="Active primary hub (BackupSchedule active)"
elif [[ "$PRIMARY_BACKUP_STATE" == "paused" ]]; then
    PRIMARY_STATE_DESC="Primary hub with paused backups"
fi

SECONDARY_STATE_DESC="Secondary hub"
if [[ "$SECONDARY_BACKUP_STATE" == "active" ]]; then
    SECONDARY_STATE_DESC="Secondary hub (BackupSchedule active - unexpected)"
elif [[ "$SECONDARY_MC_TOTAL" -eq 0 ]]; then
    SECONDARY_STATE_DESC="Secondary hub (clean, ready for restore)"
elif [[ "$SECONDARY_MC_AVAILABLE" -eq 0 ]] && [[ "$SECONDARY_MC_TOTAL" -gt 0 ]]; then
    SECONDARY_STATE_DESC="Secondary hub (clusters in Unknown state)"
else
    SECONDARY_STATE_DESC="Secondary hub (has existing clusters)"
fi

# Print Hub Summary
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Hub Summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
print_hub_summary "$PRIMARY_CONTEXT" "$ACM_PRIMARY_VERSION" "primary" "$PRIMARY_MC_AVAILABLE" "$PRIMARY_MC_TOTAL" "$PRIMARY_STATE_DESC"
print_hub_summary "$SECONDARY_CONTEXT" "$ACM_SECONDARY_VERSION" "secondary" "$SECONDARY_MC_AVAILABLE" "$SECONDARY_MC_TOTAL" "$SECONDARY_STATE_DESC"
echo ""

# Check 5: Verify OADP operator
section_header "5. Checking OADP Operator"

check_velero_pods "$PRIMARY_CONTEXT" "Primary hub"
check_velero_pods "$SECONDARY_CONTEXT" "Secondary hub"

# Check 6: Checking DataProtectionApplication
section_header "6. Checking DataProtectionApplication"

check_dpa_status "$PRIMARY_CONTEXT" "Primary hub"
check_dpa_status "$SECONDARY_CONTEXT" "Secondary hub"

# Check 7: Verify BackupStorageLocation status
section_header "7. Checking BackupStorageLocation Status"

check_bsl_status "$PRIMARY_CONTEXT" "Primary hub"
check_bsl_status "$SECONDARY_CONTEXT" "Secondary hub"

# Check 8: Verify Cluster Health (Nodes and ClusterOperators)
section_header "8. Checking Cluster Health"

check_nodes "$PRIMARY_CONTEXT" "Primary hub"
check_nodes "$SECONDARY_CONTEXT" "Secondary hub"

# Check ClusterOperators health (uses helper from lib-common.sh)
check_cluster_operators "$PRIMARY_CONTEXT" "Primary hub"
check_cluster_operators "$SECONDARY_CONTEXT" "Secondary hub"

# Check cluster upgrade status (uses helper from lib-common.sh)
check_cluster_upgrade_status "$PRIMARY_CONTEXT" "Primary hub"
check_cluster_upgrade_status "$SECONDARY_CONTEXT" "Secondary hub"

# Check 9: Verify backup status
section_header "9. Checking Backup Status"

BACKUPS=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP -n "$BACKUP_NAMESPACE" --no-headers 2>/dev/null | wc -l)
if [[ $BACKUPS -gt 0 ]]; then
    check_pass "Primary hub: Found $BACKUPS backup(s)"
    
    # Check for in-progress backups
    IN_PROGRESS=""
    IN_PROGRESS_ERROR=""
    IN_PROGRESS_ERR_FILE=$(mktemp)
    if IN_PROGRESS=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[?(@.status.phase=="InProgress")].metadata.name}' 2>"$IN_PROGRESS_ERR_FILE"); then
        if [[ -z "$IN_PROGRESS" ]]; then
            check_pass "Primary hub: No backups in progress"
        else
            WAIT_SECONDS=${BACKUP_IN_PROGRESS_WAIT_SECONDS:-600}
            POLL_SECONDS=${BACKUP_IN_PROGRESS_POLL_SECONDS:-30}
            ELAPSED=0
            echo -e "${YELLOW}⚠${NC} Primary hub: Backup(s) in progress; waiting up to ${WAIT_SECONDS}s for completion..."

            while [[ -n "$IN_PROGRESS" && $ELAPSED -lt $WAIT_SECONDS ]]; do
                sleep "$POLL_SECONDS"
                ELAPSED=$((ELAPSED + POLL_SECONDS))
                if ! IN_PROGRESS=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP -n "$BACKUP_NAMESPACE" -o jsonpath='{.items[?(@.status.phase=="InProgress")].metadata.name}' 2>"$IN_PROGRESS_ERR_FILE"); then
                    IN_PROGRESS_ERROR=$(<"$IN_PROGRESS_ERR_FILE")
                    check_fail "Primary hub: Failed to query backup progress during wait"
                    if [[ -n "$IN_PROGRESS_ERROR" ]]; then
                        echo -e "${RED}       Error: $IN_PROGRESS_ERROR${NC}"
                    fi
                    IN_PROGRESS="error"
                    break
                fi
            done

            if [[ -z "$IN_PROGRESS" ]]; then
                check_pass "Primary hub: Backups completed within ${ELAPSED}s"
            elif [[ "$IN_PROGRESS" == "error" ]]; then
                : # error already reported
            else
                check_fail "Primary hub: Backup(s) in progress after waiting ${WAIT_SECONDS}s: $IN_PROGRESS"
            fi
        fi
    else
        IN_PROGRESS_ERROR=$(<"$IN_PROGRESS_ERR_FILE")
        check_fail "Primary hub: Failed to query backups for in-progress status"
        if [[ -n "$IN_PROGRESS_ERROR" ]]; then
            echo -e "${RED}       Error: $IN_PROGRESS_ERROR${NC}"
        fi
    fi
    rm -f "$IN_PROGRESS_ERR_FILE"
    
    # Check latest backup
    LATEST_BACKUP=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP -n "$BACKUP_NAMESPACE" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null)
    LATEST_PHASE=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP "$LATEST_BACKUP" -n "$BACKUP_NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null)
    if [[ "$LATEST_PHASE" == "Finished" ]] || [[ "$LATEST_PHASE" == "Completed" ]]; then
        check_pass "Primary hub: Latest backup '$LATEST_BACKUP' completed successfully"
        
        # Show backup age/freshness
        BACKUP_COMPLETION=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP "$LATEST_BACKUP" -n "$BACKUP_NAMESPACE" \
            -o jsonpath='{.status.completionTimestamp}' 2>/dev/null || echo "")
        
        if [[ -n "$BACKUP_COMPLETION" ]]; then
            # Convert timestamps to epoch seconds for age calculation
            BACKUP_EPOCH=$(date -d "$BACKUP_COMPLETION" +%s 2>/dev/null || echo "0")
            CURRENT_EPOCH=$(date +%s)
            AGE_SECONDS=$((CURRENT_EPOCH - BACKUP_EPOCH))
            AGE_DISPLAY=$(format_age_display "$AGE_SECONDS")

            # If we can derive schedule cadence, show age relative to it
            EFFECTIVE_BACKUP_AGE_MAX_SECONDS="$BACKUP_AGE_MAX_SECONDS"
            # Fetch BackupSchedule JSON locally for cadence-aware messaging (keep Check 10 logic unchanged)
            PRIMARY_BACKUP_SCHEDULE=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" -o json 2>/dev/null || echo "")
            SCHEDULE_EXPR=""
            INTERVAL_SECONDS=""
            if [[ -n "$PRIMARY_BACKUP_SCHEDULE" ]] && echo "$PRIMARY_BACKUP_SCHEDULE" | jq -e '.items[0]' &>/dev/null; then
                SCHEDULE_EXPR=$(echo "$PRIMARY_BACKUP_SCHEDULE" | jq -r '.items[0].spec.veleroSchedule // ""' 2>/dev/null || echo "")
            fi
            if [[ -n "$SCHEDULE_EXPR" ]]; then
                INTERVAL_SECONDS=$(_derive_backup_interval_seconds "$SCHEDULE_EXPR")
                if [[ -n "$INTERVAL_SECONDS" && "$INTERVAL_SECONDS" -gt "$BACKUP_AGE_MAX_SECONDS" ]]; then
                    EFFECTIVE_BACKUP_AGE_MAX_SECONDS=$((INTERVAL_SECONDS + BACKUP_AGE_MAX_SECONDS))
                fi
            fi

            if [[ -n "$INTERVAL_SECONDS" ]]; then
                INTERVAL_DISPLAY=$(format_age_display "$INTERVAL_SECONDS")
                THRESHOLD_DISPLAY=$(format_age_display "$EFFECTIVE_BACKUP_AGE_MAX_SECONDS")

                if [[ $AGE_SECONDS -le $INTERVAL_SECONDS ]]; then
                    echo -e "${GREEN}       Backup age: $AGE_DISPLAY (completed: $BACKUP_COMPLETION, schedule: $SCHEDULE_EXPR, interval: $INTERVAL_DISPLAY, threshold: $THRESHOLD_DISPLAY) - within one schedule interval${NC}"
                elif [[ $AGE_SECONDS -le $EFFECTIVE_BACKUP_AGE_MAX_SECONDS ]]; then
                    echo -e "${YELLOW}       Backup age: $AGE_DISPLAY (completed: $BACKUP_COMPLETION, schedule: $SCHEDULE_EXPR, interval: $INTERVAL_DISPLAY, threshold: $THRESHOLD_DISPLAY) - within allowed threshold${NC}"
                else
                    echo -e "${RED}       Backup age: $AGE_DISPLAY (completed: $BACKUP_COMPLETION, schedule: $SCHEDULE_EXPR, interval: $INTERVAL_DISPLAY, threshold: $THRESHOLD_DISPLAY) - OLDER than allowed threshold, run a fresh backup before switchover${NC}"
                fi
            else
                # Fallback: absolute buckets when schedule cadence is unknown
                if [[ $AGE_SECONDS -lt 3600 ]]; then
                    echo -e "${GREEN}       Backup age: $AGE_DISPLAY (completed: $BACKUP_COMPLETION) - FRESH${NC}"
                elif [[ $AGE_SECONDS -lt 86400 ]]; then
                    echo -e "${YELLOW}       Backup age: $AGE_DISPLAY (completed: $BACKUP_COMPLETION) - acceptable${NC}"
                else
                    echo -e "${YELLOW}       Backup age: $AGE_DISPLAY (completed: $BACKUP_COMPLETION) - consider running a fresh backup${NC}"
                fi
            fi
        else
            check_warn "Primary hub: Could not determine backup age (completion timestamp unavailable)"
        fi
        
        # Check if all joined ManagedClusters existed before the latest managed clusters backup
        # This prevents data loss when clusters were imported after the last backup
        JOINED_CLUSTERS=$(oc --context="$PRIMARY_CONTEXT" get $RES_MANAGED_CLUSTER -o json 2>/dev/null | \
            jq -r '.items[] | select(.metadata.name != "local-cluster") | select(.status.conditions[]? | select(.type=="ManagedClusterJoined" and .status=="True")) | .metadata.name' | sort)
        
        # Find the latest managed clusters backup (not validation or resources backup)
        MC_BACKUP_NAME=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP -n "$BACKUP_NAMESPACE" \
            -l "cluster.open-cluster-management.io/backup-schedule-type=managedClusters" \
            --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null || echo "")
        
        if [[ -n "$MC_BACKUP_NAME" ]] && [[ -n "$JOINED_CLUSTERS" ]]; then
            # Get the backup completion timestamp
            BACKUP_TIME=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP "$MC_BACKUP_NAME" -n "$BACKUP_NAMESPACE" \
                -o jsonpath='{.status.completionTimestamp}' 2>/dev/null || echo "")
            
            if [[ -n "$BACKUP_TIME" ]]; then
                BACKUP_EPOCH=$(date -d "$BACKUP_TIME" +%s 2>/dev/null || echo "0")
                
                # Find clusters that were created after the backup completed
                MISSING_FROM_BACKUP=""
                for cluster in $JOINED_CLUSTERS; do
                    CLUSTER_TIME=$(oc --context="$PRIMARY_CONTEXT" get $RES_MANAGED_CLUSTER "$cluster" \
                        -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null || echo "")
                    if [[ -n "$CLUSTER_TIME" ]]; then
                        CLUSTER_EPOCH=$(date -d "$CLUSTER_TIME" +%s 2>/dev/null || echo "0")
                        # If cluster was created after backup completed, it's not in the backup
                        if [[ $CLUSTER_EPOCH -gt $BACKUP_EPOCH ]]; then
                            MISSING_FROM_BACKUP="$MISSING_FROM_BACKUP $cluster"
                        fi
                    fi
                done
                
                if [[ -z "$MISSING_FROM_BACKUP" ]]; then
                    check_pass "Primary hub: All joined ManagedClusters existed before latest backup ($MC_BACKUP_NAME)"
                else
                    check_warn "Primary hub: Clusters imported after latest backup:$MISSING_FROM_BACKUP"
                    echo -e "${YELLOW}       Consider running a new backup before switchover to include recently imported clusters${NC}"
                fi
            else
                check_warn "Primary hub: Could not verify ManagedCluster backup coverage (backup timestamp unavailable)"
            fi
        elif [[ -z "$MC_BACKUP_NAME" ]]; then
            check_warn "Primary hub: No managed clusters backup found (cannot verify cluster coverage)"
        else
            check_pass "Primary hub: No joined ManagedClusters to verify"
        fi
    else
        check_fail "Primary hub: Latest backup '$LATEST_BACKUP' in unexpected state: $LATEST_PHASE"
    fi
else
    check_fail "Primary hub: No backups found"
fi

# Check 10: Verify BackupSchedule useManagedServiceAccount (CRITICAL for auto-reconnect)
section_header "10. Checking BackupSchedule useManagedServiceAccount (CRITICAL)"

BACKUP_SCHEDULE=$(oc --context="$PRIMARY_CONTEXT" get $RES_BACKUP_SCHEDULE -n "$BACKUP_NAMESPACE" -o json 2>/dev/null)
if [[ -n "$BACKUP_SCHEDULE" ]] && echo "$BACKUP_SCHEDULE" | jq -e '.items[0]' &>/dev/null; then
    SCHEDULE_COUNT=$(echo "$BACKUP_SCHEDULE" | jq -r '.items | length' 2>/dev/null || echo "0")
    
    # Note: SCHEDULE_COUNT >= 1 is guaranteed by the jq -e '.items[0]' check above
    if [[ $SCHEDULE_COUNT -gt 1 ]]; then
        check_warn "Found $SCHEDULE_COUNT BackupSchedules - will check first one only"
    fi
    
    SCHEDULE_NAME=$(echo "$BACKUP_SCHEDULE" | jq -r '.items[0].metadata.name')
    USE_MSA=$(echo "$BACKUP_SCHEDULE" | jq -r '.items[0].spec.useManagedServiceAccount // false')

    # Detect GitOps markers on BackupSchedule
    SCHEDULE_JSON=$(echo "$BACKUP_SCHEDULE" | jq '.items[0]')
    GITOPS_MARKERS=$(detect_gitops_markers "$SCHEDULE_JSON")
    if [[ -n "$GITOPS_MARKERS" ]]; then
        collect_gitops_markers "primary" "$BACKUP_NAMESPACE" "BackupSchedule" "$SCHEDULE_NAME" "$GITOPS_MARKERS"
    fi

    if [[ "$USE_MSA" == "true" ]]; then
        check_pass "Primary hub: BackupSchedule '$SCHEDULE_NAME' has useManagedServiceAccount=true"
        echo -e "${GREEN}       Managed clusters will auto-reconnect to new hub after switchover${NC}"
    else
        check_fail "Primary hub: BackupSchedule '$SCHEDULE_NAME' does NOT have useManagedServiceAccount=true"
        echo -e "${RED}       WITHOUT THIS SETTING, managed clusters will NOT auto-reconnect after switchover!${NC}"
        echo -e "${RED}       Fix: oc --context=$PRIMARY_CONTEXT patch $RES_BACKUP_SCHEDULE/$SCHEDULE_NAME -n $BACKUP_NAMESPACE --type=merge -p '{\"spec\":{\"useManagedServiceAccount\":true}}'${NC}"
        echo -e "${RED}       Then wait for a new backup to be created before proceeding with switchover.${NC}"
    fi
else
    check_fail "Primary hub: No BackupSchedule found in $BACKUP_NAMESPACE namespace"
fi

# Check 11: Verify ClusterDeployment preserveOnDelete (CRITICAL)
section_header "11. Checking ClusterDeployment preserveOnDelete (CRITICAL)"

CDS_JSON=$(oc --context="$PRIMARY_CONTEXT" get $RES_CLUSTER_DEPLOYMENT --all-namespaces -o json 2>/dev/null || echo '{"items":[]}')
CDS=$(echo "$CDS_JSON" | jq '.items | length')
if [[ $CDS -eq 0 ]]; then
    check_pass "No ClusterDeployments found (no Hive-managed clusters)"
else
    # Detect GitOps markers on each ClusterDeployment
    echo "$CDS_JSON" | jq -c '.items[]' 2>/dev/null | while read -r CD_JSON; do
        CD_NAMESPACE=$(echo "$CD_JSON" | jq -r '.metadata.namespace')
        CD_NAME=$(echo "$CD_JSON" | jq -r '.metadata.name')
        GITOPS_MARKERS=$(detect_gitops_markers "$CD_JSON")
        if [[ -n "$GITOPS_MARKERS" ]]; then
            collect_gitops_markers "primary" "$CD_NAMESPACE" "ClusterDeployment" "$CD_NAME" "$GITOPS_MARKERS"
        fi
    done

    MISSING_PRESERVE=$(echo "$CDS_JSON" | \
        jq -r '.items[] | select(.spec.preserveOnDelete != true) | "\(.metadata.namespace)/\(.metadata.name)"' | wc -l)
    
    if [[ $MISSING_PRESERVE -eq 0 ]]; then
        check_pass "All $CDS ClusterDeployment(s) have preserveOnDelete=true"
    else
        MISSING_LIST=$(echo "$CDS_JSON" | \
            jq -r '.items[] | select(.spec.preserveOnDelete != true) | "\(.metadata.namespace)/\(.metadata.name)"')
        check_fail "ClusterDeployments missing preserveOnDelete=true: $MISSING_LIST"
        echo -e "${RED}       THIS IS CRITICAL! Without preserveOnDelete=true, deleting ManagedClusters will DESTROY infrastructure!${NC}"
    fi
fi

# Check 12: Method-specific checks
if [[ "$METHOD" == "passive" ]]; then
    section_header "12. Checking Passive Sync (Method 1)"
    
    # Find passive sync restore by looking for syncRestoreWithNewBackups=true
    # This matches the Python discovery logic in modules/activation.py
    PASSIVE_RESTORE_NAME=$(oc --context="$SECONDARY_CONTEXT" get $RES_RESTORE -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | \
        jq -r '.items[] | select(.spec.syncRestoreWithNewBackups == true) | .metadata.name' | head -1 || true)
    
    # Fallback: if not found by spec, try the well-known name for backward compatibility
    if [[ -z "$PASSIVE_RESTORE_NAME" ]]; then
        if oc --context="$SECONDARY_CONTEXT" get $RES_RESTORE "$RESTORE_PASSIVE_SYNC_NAME" -n "$BACKUP_NAMESPACE" &> /dev/null; then
            PASSIVE_RESTORE_NAME="$RESTORE_PASSIVE_SYNC_NAME"
        fi
    fi
    
    if [[ -n "$PASSIVE_RESTORE_NAME" ]]; then
        if ! RESTORE_JSON=$(oc --context="$SECONDARY_CONTEXT" get $RES_RESTORE "$PASSIVE_RESTORE_NAME" -n "$BACKUP_NAMESPACE" -o json 2>/dev/null); then
            check_fail "Secondary hub: Failed to fetch restore '$PASSIVE_RESTORE_NAME' details"
            RESTORE_JSON="{}"
        fi
        PHASE=$(echo "$RESTORE_JSON" | jq -r '.status.phase // "unknown"')
        LAST_MESSAGE=$(echo "$RESTORE_JSON" | jq -r '.status.lastMessage // ""')

        # Detect GitOps markers on Restore
        GITOPS_MARKERS=$(detect_gitops_markers "$RESTORE_JSON")
        if [[ -n "$GITOPS_MARKERS" ]]; then
            collect_gitops_markers "secondary" "$BACKUP_NAMESPACE" "Restore" "$PASSIVE_RESTORE_NAME" "$GITOPS_MARKERS"
        fi

        if [[ "$PHASE" == "Enabled" ]] || [[ "$PHASE" == "Completed" ]] || [[ "$PHASE" == "Finished" ]]; then
            check_pass "Secondary hub: Found passive sync restore '$PASSIVE_RESTORE_NAME' in state: $PHASE"
        else
            check_fail "Secondary hub: Passive sync restore '$PASSIVE_RESTORE_NAME' exists but phase is: $PHASE (expected: Enabled, Completed, or Finished)"
            if [[ -n "$LAST_MESSAGE" ]]; then
                echo -e "${RED}       Restore message: $LAST_MESSAGE${NC}"
            fi
            BSL_CONDITIONS=$(oc --context="$SECONDARY_CONTEXT" get $RES_BSL -n "$BACKUP_NAMESPACE" -o json 2>/dev/null | \
                jq -r '.items[0].status.conditions // [] | map("\(.type)=\(.status) reason=\(.reason // "n/a") msg=\(.message // "n/a")") | join("; ")' || true)
            if [[ -n "$BSL_CONDITIONS" ]]; then
                echo -e "${RED}       BSL conditions: $BSL_CONDITIONS${NC}"
                # Check if BSL is explicitly unavailable
                if echo "$BSL_CONDITIONS" | grep -qE "Available=False|Ready=False|Unavailable=True"; then
                    echo -e "${RED}       Unavailable BSL means restores cannot proceed${NC}"
                else
                    echo -e "${RED}       Restore '$PASSIVE_RESTORE_NAME' failed - check restore status and BSL conditions above${NC}"
                fi
            else
                echo -e "${RED}       Restore '$PASSIVE_RESTORE_NAME' failed - unable to retrieve BSL conditions${NC}"
            fi
        fi
    else
        check_fail "Secondary hub: No passive sync restore found (required for Method 1). Expected a Restore with spec.syncRestoreWithNewBackups=true or named '$RESTORE_PASSIVE_SYNC_NAME'"
    fi
else
    section_header "12. Method 2 (Full Restore) - No passive sync check needed"
    check_pass "Method 2 selected - passive sync not required"
fi

# Check 13: Verify Observability (optional)
section_header "13. Checking ACM Observability (Optional)"

if oc --context="$PRIMARY_CONTEXT" get namespace "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
    check_pass "Primary hub: Observability namespace exists"
    
    # Check MCO CR on primary
    if oc --context="$PRIMARY_CONTEXT" get $RES_MCO observability &> /dev/null; then
         check_pass "Primary hub: MultiClusterObservability CR found"
         # Detect GitOps markers on MCO (cluster-scoped, no namespace)
         MCO_JSON=$(oc --context="$PRIMARY_CONTEXT" get $RES_MCO observability -o json 2>/dev/null || echo "{}")
         if [[ -n "$MCO_JSON" && "$MCO_JSON" != "{}" ]]; then
             MCO_NAME=$(echo "$MCO_JSON" | jq -r '.metadata.name // "observability"')
             GITOPS_MARKERS=$(detect_gitops_markers "$MCO_JSON")
             if [[ -n "$GITOPS_MARKERS" ]]; then
                 collect_gitops_markers "primary" "" "MultiClusterObservability" "$MCO_NAME" "$GITOPS_MARKERS"
             fi
         fi
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

        # Secondary hub observability safety:
        # - If MCO is present, it must NOT be active on the secondary hub during switchover.
        #   It's OK for MCO to exist if both Thanos compactor and observatorium-api are scaled to 0.
        # - If MCO is absent but observability pods still exist, warn (likely incomplete decommission).
        if oc --context="$SECONDARY_CONTEXT" get $RES_MCO observability -n "$OBSERVABILITY_NAMESPACE" &> /dev/null; then
            SECONDARY_COMPACTOR_PODS=$(get_pod_count "$SECONDARY_CONTEXT" "$OBSERVABILITY_NAMESPACE" "app.kubernetes.io/name=thanos-compact" "$OBS_THANOS_COMPACT_POD")
            SECONDARY_OBSERVATORIUM_API_PODS=$(get_pod_count "$SECONDARY_CONTEXT" "$OBSERVABILITY_NAMESPACE" "app.kubernetes.io/name=observatorium-api" "$OBS_API_POD")

            if [[ $SECONDARY_COMPACTOR_PODS -gt 0 ]] || [[ $SECONDARY_OBSERVATORIUM_API_PODS -gt 0 ]]; then
                check_fail "Secondary hub: MultiClusterObservability is active (thanos-compact=$SECONDARY_COMPACTOR_PODS, observatorium-api=$SECONDARY_OBSERVATORIUM_API_PODS). Scale both to 0 before switchover."
            else
                check_pass "Secondary hub: MultiClusterObservability present but compactor/observatorium-api are scaled to 0 (OK)"
            fi
        else
            SECONDARY_OBS_PODS_TOTAL=$(oc --context="$SECONDARY_CONTEXT" get pods -n "$OBSERVABILITY_NAMESPACE" --no-headers 2>/dev/null | wc -l || true)
            if [[ $SECONDARY_OBS_PODS_TOTAL -gt 0 ]]; then
                check_warn "Secondary hub: Observability pods exist ($SECONDARY_OBS_PODS_TOTAL) but MultiClusterObservability CR not found (hub may not be properly decommissioned)"
            else
                check_pass "Secondary hub: MultiClusterObservability CR not found"
            fi
        fi
    else
        check_warn "Secondary hub: Observability namespace not found (may need manual setup)"
    fi
else
    check_pass "Observability not detected (optional component)"
fi

# Check 14: Verify Secondary Hub Pre-existing Managed Clusters
section_header "14. Checking Secondary Hub Managed Clusters"

# Use already-gathered managed cluster counts from hub summary
if [[ "$SECONDARY_MC_TOTAL" -gt 0 ]]; then
    if [[ "$SECONDARY_MC_AVAILABLE" -eq 0 ]]; then
        check_warn "Secondary hub: Has $SECONDARY_MC_TOTAL existing managed cluster(s) - all in Unknown state (0/${SECONDARY_MC_TOTAL} available)"
        echo -e "${YELLOW}       These clusters may be remnants from a previous restore or test.${NC}"
        echo -e "${YELLOW}       They will likely reconnect after switchover, or may need cleanup.${NC}"
    elif [[ "$SECONDARY_MC_AVAILABLE" -lt "$SECONDARY_MC_TOTAL" ]]; then
        check_warn "Secondary hub: Has $SECONDARY_MC_TOTAL existing managed cluster(s) (${SECONDARY_MC_AVAILABLE}/${SECONDARY_MC_TOTAL} available)"
        echo -e "${YELLOW}       Some clusters are not available - review before restore.${NC}"
        echo -e "${YELLOW}       They may conflict with clusters being restored from the primary hub.${NC}"
    else
        check_warn "Secondary hub: Has $SECONDARY_MC_TOTAL existing managed cluster(s) (${SECONDARY_MC_AVAILABLE}/${SECONDARY_MC_TOTAL} available)"
        echo -e "${YELLOW}       Review these clusters before restore - they may conflict with${NC}"
        echo -e "${YELLOW}       clusters being restored from the primary hub.${NC}"
    fi
else
    check_pass "Secondary hub: No pre-existing managed clusters (clean restore target)"
fi

# Check 15: Verify Auto-Import Strategy (ACM 2.14+ only)
section_header "15. Checking Auto-Import Strategy (ACM 2.14+ only)"

# Check primary hub
if is_acm_214_or_higher "$ACM_PRIMARY_VERSION"; then
    PRIMARY_STRATEGY=$(get_auto_import_strategy "$PRIMARY_CONTEXT")
    if [[ "$PRIMARY_STRATEGY" == "error" ]]; then
        check_fail "Primary hub: Could not retrieve autoImportStrategy (connection or API error)"
    elif [[ "$PRIMARY_STRATEGY" == "default" ]]; then
        check_pass "Primary hub: Using default autoImportStrategy ($AUTO_IMPORT_STRATEGY_DEFAULT)"
    elif [[ "$PRIMARY_STRATEGY" == "$AUTO_IMPORT_STRATEGY_DEFAULT" ]]; then
        check_pass "Primary hub: autoImportStrategy explicitly set to $AUTO_IMPORT_STRATEGY_DEFAULT"
    else
        check_warn "Primary hub: autoImportStrategy is set to '$PRIMARY_STRATEGY' (non-default)"
        echo -e "${YELLOW}       This should only be temporary for specific scenarios.${NC}"
        echo -e "${YELLOW}       See: $AUTO_IMPORT_STRATEGY_DOC_URL${NC}"
    fi
else
    check_pass "Primary hub: ACM $ACM_PRIMARY_VERSION (autoImportStrategy not applicable, requires 2.14+)"
fi

# Check secondary hub
if is_acm_214_or_higher "$ACM_SECONDARY_VERSION"; then
    SECONDARY_STRATEGY=$(get_auto_import_strategy "$SECONDARY_CONTEXT")
    if [[ "$SECONDARY_STRATEGY" == "error" ]]; then
        check_fail "Secondary hub: Could not retrieve autoImportStrategy (connection or API error)"
    elif [[ "$SECONDARY_STRATEGY" == "default" ]]; then
        check_pass "Secondary hub: Using default autoImportStrategy ($AUTO_IMPORT_STRATEGY_DEFAULT)"
    elif [[ "$SECONDARY_STRATEGY" == "$AUTO_IMPORT_STRATEGY_DEFAULT" ]]; then
        check_pass "Secondary hub: autoImportStrategy explicitly set to $AUTO_IMPORT_STRATEGY_DEFAULT"
    else
        check_warn "Secondary hub: autoImportStrategy is set to '$SECONDARY_STRATEGY' (non-default)"
        echo -e "${YELLOW}       This should only be temporary for specific scenarios.${NC}"
        echo -e "${YELLOW}       See: $AUTO_IMPORT_STRATEGY_DOC_URL${NC}"
    fi
    
    # If secondary hub already has managed clusters, provide ImportOnly guidance
    if [[ $SECONDARY_MC_TOTAL -gt 0 ]] && [[ "$SECONDARY_STRATEGY" == "default" || "$SECONDARY_STRATEGY" == "$AUTO_IMPORT_STRATEGY_DEFAULT" ]]; then
        check_warn "Secondary hub: ImportOnly strategy with existing clusters"
        echo -e "${YELLOW}       ACM 2.14+ default ImportOnly requires one of the following:${NC}"
        echo -e "${YELLOW}       Option A (preferred): Add immediate-import annotation (empty value) on secondary hub${NC}"
        echo -e "${YELLOW}         oc --context=$SECONDARY_CONTEXT get managedcluster -o name | grep -v $LOCAL_CLUSTER_NAME | \\${NC}"
        echo -e "${YELLOW}           xargs -I{} oc --context=$SECONDARY_CONTEXT annotate {} import.open-cluster-management.io/immediate-import='' --overwrite${NC}"
        echo -e "${YELLOW}         Note: empty value ('') triggers auto-import; controller sets it to 'Completed' afterward${NC}"
        echo -e "${YELLOW}         It is safe to leave the annotation in place (no removal needed)${NC}"
        echo -e "${YELLOW}       Option B (switchback scenarios): Temporarily set autoImportStrategy=${AUTO_IMPORT_STRATEGY_SYNC}${NC}"
        echo -e "${YELLOW}         oc -n $MCE_NAMESPACE create configmap $IMPORT_CONTROLLER_CONFIGMAP \\${NC}"
        echo -e "${YELLOW}           --from-literal=$AUTO_IMPORT_STRATEGY_KEY=$AUTO_IMPORT_STRATEGY_SYNC --dry-run=client -o yaml | oc apply -f -${NC}"
        echo -e "${YELLOW}         Then remove the configmap after activation to restore default ImportOnly${NC}"
        echo -e "${YELLOW}         oc -n $MCE_NAMESPACE delete configmap $IMPORT_CONTROLLER_CONFIGMAP${NC}"
        echo -e "${YELLOW}       See: $AUTO_IMPORT_STRATEGY_DOC_URL${NC}"
    fi
else
    check_pass "Secondary hub: ACM $ACM_SECONDARY_VERSION (autoImportStrategy not applicable, requires 2.14+)"
fi

if [[ $ARGOCD_CHECK -eq 1 ]]; then
    section_header "16. Checking ArgoCD GitOps Management (Optional)"
    check_argocd_acm_resources "$PRIMARY_CONTEXT" "Primary hub"
    check_argocd_acm_resources "$SECONDARY_CONTEXT" "Secondary hub"
fi

# Print GitOps detection report if any markers were found
print_gitops_report

# Summary and exit
if print_summary "preflight"; then
    exit "$EXIT_SUCCESS"
else
    exit "$EXIT_FAILURE"
fi
